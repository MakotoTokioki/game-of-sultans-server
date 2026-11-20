"""Hunting / instance cluster extras: actions 11804-11816 (excluding the five
already implemented in ``gos.handlers``: 11801/11802/11803/11808/11810).

Kept in a separate module so ``gos/handlers.py`` and ``gos/defaults.py`` stay
untouched; ``gos.handlers._merge_cluster_handlers`` imports
``INSTANCE_MORE_HANDLERS`` into ``HANDLERS`` at server startup.

Response shapes were recovered by walking *backwards* from every
``actionNNNNN = Lxx`` / ``netXxx = Lxx`` assignment in ``_instanceMod.lua`` to
the most recent ``function Lxx(`` before it (the decompiler reuses Lxx names),
then reading that request function's nested response callback.  Where the
module callback only forwards to a scene listener, the scene was checked too:
``instanceCreditsDialog.lua`` (11811), ``chatDialog.lua`` /
``chatScene.huntBoxDialog.lua`` (11812/11814) and
``instanceGlobalBattleDialog.lua`` (11805/11806/11815/11816).

Field-to-action mapping determined from those callbacks
--------------------------------------------------------
* 11804 netSingleUseItem  -> CounsellorAttackData
* 11805 netBossInfomation(global) -> BossId/BossHP/BossHPMax (via set_cur_enemy),
  CounsellorAttackData, DamageRank, KillerName, RoleDamage, RoleRank,
  DamageRankAward, BossGlobalDead, KillRank, BossSingleComplete
* 11806 netAttack(global) -> BossStasus, BossId/BossHP/BossHPMax,
  CounsellorAttackData, RemainList, DamageRank, RoleDamage, RoleRank,
  DamageRankAward, BossGlobalDead, KillRank
* 11807 netGlobalUseItem -> CounsellorAttackData
* 11809 netDamgeData -> DamageRank, RoleRank, Damage, RId
* 11811 netExchange -> RemainList[1].Num (NEW BossCredits), RequireScore,
  ExchangeTimes  (scene callback, not the forwarding module callback)
* 11812 action11812 (hunt box) -> IsFinish, HasReceivedByOthers, RemainTimes,
  RemainList, InReceiverList, ReceiverList
* 11813 shareBox -> forward-only, no field reads -> {}
* 11814 action11814 (receive shared box) -> IsFinish + huntBoxDialog fields
* 11815 action11815 (global boss attack) -> BossStasus, BossId, BossHP, BossHPMax
* 11816 action11816 -> DamageRank

Reward policy (HANDLER_SPEC): no number is invented.  Awards come ONLY from
original config award columns (TcBossSingleAwardBox.SelfAwardItem /
.GlobalAwardItem, TcBossExchange.ItemId).  Costs/thresholds come from
TcBossExchange + ConfigEnvSet.  All new state is lazy (no defaults.py change).

Server-authoritative items (documented, not fabricated): the shared global-boss
leaderboard (DamageRank/KillRank/ScoreRank), its live HP, kill attribution and
RNG drop rolls.  They are returned as Lua-safe empty/zero values; the HP seed
is the lowest-level TcAllianceInstance.Blood and per-attack damage is
TcKingBossAttack.Score so those fields are still config-sourced and never zero.
"""
from __future__ import annotations

import math

from gos import v2configs
from gos.handlers import (
    _REMAIN_LIST_MAP,
    _as_int,
    _boss_single_state,
    _grant_award_pairs,
    _iter_award_pairs,
    _now,
    _role_item_add,
    attach_remain_list,
)


# ---------------------------------------------------------------------------
# config / lazy state helpers
# ---------------------------------------------------------------------------
def _cfg_rows(name: str) -> list[dict]:
    rows = v2configs.load(name)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, (list, tuple)):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _env(name: str, default: int) -> int:
    try:
        return int(v2configs.env_value(name, default) or default)
    except (TypeError, ValueError):
        return default


# --- lazy global-boss (Fortress Siege / King Boss) state --------------------
_GLOBAL_KEY = "InstanceMoreGlobal"


def _alliance_row() -> dict:
    rows = _cfg_rows("TcAllianceInstance")
    if not rows:
        return {}
    # Lowest configured alliance level is the deterministic default seed.
    return min(rows, key=lambda r: _as_int(r.get("Lv")) or 0)


def _global_state(player) -> dict:
    """Lazy per-player mirror of global-boss state; seeded only from config."""
    st = player.data.get(_GLOBAL_KEY)
    if not isinstance(st, dict):
        row = _alliance_row()
        hp = _as_int(row.get("Blood"))
        st = {
            "BossId": _as_int(row.get("Lv")) or 1,
            "BossHP": hp,
            "BossHPMax": hp,
            "Attacks": {},
            "Damage": 0,
            "Kills": 0,
            "KillTime": 0,
        }
        player.data[_GLOBAL_KEY] = st
    return st


def _global_attack_damage() -> int:
    rows = _cfg_rows("TcKingBossAttack")
    if not rows:
        return 0
    # First config row is the deterministic default attack score.
    first = min(rows, key=lambda r: _as_int(r.get("Id")) or 0)
    return max(0, _as_int(first.get("Score") or first.get("Attack")))


def _hit_global(player, state: dict, damage: int) -> None:
    """Apply config-sourced damage; on death rotate to a fresh full-HP boss."""
    state["BossHP"] = max(0, _as_int(state.get("BossHP")) - max(0, int(damage)))
    state["Damage"] = _as_int(state.get("Damage")) + max(0, int(damage))
    if _as_int(state.get("BossHP")) <= 0:
        row = _alliance_row()
        hp = _as_int(row.get("Blood"))
        state["BossId"] = _as_int(row.get("Lv")) or 1
        state["BossHP"] = hp
        state["BossHPMax"] = hp
        state["Attacks"] = {}
        state["Kills"] = _as_int(state.get("Kills")) + 1
        state["KillTime"] = _now()
        rb = player.data.get("RoleBase") or {}
        state["KillerName"] = str(rb.get("Nickname") or "")


def _killer_name(player, state: dict) -> str:
    if _as_int(state.get("Kills")) > 0:
        return str(state.get("KillerName") or "")
    return ""


def _boss_global_dead(state: dict) -> dict:
    """Table or empty table, never 0 (client does `if it then it.KillerName`)."""
    if _as_int(state.get("Kills")) > 0 and _as_int(state.get("KillTime")) > 0:
        return {"KillerName": str(state.get("KillerName") or ""),
                "KillTime": _as_int(state.get("KillTime"))}
    return {}


def _damage_rank_award(rank: int) -> dict:
    """Preview struct from TcBossRankAward; rank 0 naturally finds no row."""
    award = []
    for row in _cfg_rows("TcBossRankAward"):
        if _as_int(row.get("Rank")) == int(rank):
            for item_id, num in _iter_award_pairs(row.get("AwardItem")):
                award.append({"ItemId": int(item_id), "Num": int(num)})
            break
    return {"RemainList": award, "RoleRank": int(rank)}


def _global_info(player, state: dict) -> dict:
    """Fields shared by 11805 (info) and 11806 (attack) callbacks."""
    return {
        "BossId": _as_int(state.get("BossId")),
        "BossHP": _as_int(state.get("BossHP")),
        "BossHPMax": _as_int(state.get("BossHPMax")),
        "CounsellorAttackData": dict(state.get("Attacks") or {}),
        "DamageRank": [],                 # shared leaderboard: server-authoritative
        "KillerName": _killer_name(player, state),
        "RoleDamage": _as_int(state.get("Damage")),
        "RoleRank": 0,
        "DamageRankAward": _damage_rank_award(0),
        "BossGlobalDead": _boss_global_dead(state),
        "KillRank": [],                   # shared leaderboard: server-authoritative
        "BossSingleComplete": False,      # Lua-truthiness: real bool (oracle sends false)
    }


# --- lazy hunt-box / exchange state ----------------------------------------
_BOX_KEY = "InstanceMoreBoxes"
_EXCHANGE_KEY = "InstanceMoreExchange"


def _box_state(player) -> dict:
    st = player.data.get(_BOX_KEY)
    if not isinstance(st, dict):
        st = {"claims": {}, "finished": {}}
        player.data[_BOX_KEY] = st
    for key in ("claims", "finished"):
        if not isinstance(st.get(key), dict):
            st[key] = {}
    return st


def _exchange_state(player) -> dict:
    st = player.data.get(_EXCHANGE_KEY)
    if not isinstance(st, dict):
        st = {}
        player.data[_EXCHANGE_KEY] = st
    return st


def _box_row(box_id: int) -> dict:
    for row in _cfg_rows("TcBossSingleAwardBox"):
        if _as_int(row.get("BoxId")) == int(box_id):
            return row
    return {}


def _guaranteed_groups(award) -> list:
    """Weighted award groups: only weight==100 (guaranteed) are grantable.

    TcBossSingleAwardBox.SelfAwardItem entries are [ItemId, Num, Weight];
    sub-100 groups are RNG rolls (server-authoritative, not sourced).
    """
    out = []
    if isinstance(award, (list, tuple)):
        for group in award:
            if (isinstance(group, (list, tuple)) and len(group) >= 3
                    and _as_int(group[2]) != 100):
                continue
            out.append(group)
    return out


def _grant_and_remain(player, award) -> dict:
    """Grant a config award column and return a dict carrying its RemainList."""
    pairs, bag_ids = _grant_award_pairs(player, award)
    resources = [int(p[0]) for p in pairs if int(p[0]) in _REMAIN_LIST_MAP]
    return attach_remain_list({}, player, items=bag_ids, resources=resources)


# ---------------------------------------------------------------------------
# 11804 / 11807 - vizier item use (single / global)
# ---------------------------------------------------------------------------
def action_instance_single_use_item(payload: dict, player, session, store) -> dict:
    """_instanceMod.netSingleUseItem (11804 {CounsellorId}).

    The callback reads only CounsellorAttackData.  Reusing the single-fight
    state from ``handlers.action_instance_boss_attack`` means the recovered
    vizier can attack again on the next 11803 request.
    """
    try:
        counsellor_id = int(payload.get("CounsellorId") or 0)
    except (TypeError, ValueError):
        counsellor_id = 0
    state = _boss_single_state(player)
    attacks = state.setdefault("Attacks", {})
    for key in (str(counsellor_id), counsellor_id):
        if key in attacks:
            del attacks[key]
    store.save(player)
    return {"CounsellorAttackData": dict(attacks)}


def action_instance_global_use_item(payload: dict, player, session, store) -> dict:
    """_instanceMod.netGlobalUseItem (11807 {CounsellorId}); callback reads only
    CounsellorAttackData, mirrored in the lazy global-boss slice."""
    try:
        counsellor_id = int(payload.get("CounsellorId") or 0)
    except (TypeError, ValueError):
        counsellor_id = 0
    state = _global_state(player)
    attacks = state.setdefault("Attacks", {})
    for key in (str(counsellor_id), counsellor_id):
        if key in attacks:
            del attacks[key]
    store.save(player)
    return {"CounsellorAttackData": dict(attacks)}


# ---------------------------------------------------------------------------
# 11805 / 11806 / 11815 / 11816 / 11809 - global boss
# ---------------------------------------------------------------------------
def action_instance_global_info(payload: dict, player, session, store) -> dict:
    """_instanceMod.netBossInfomation global branch (11805)."""
    state = _global_state(player)
    store.save(player)
    return _global_info(player, state)


def action_instance_global_attack(payload: dict, player, session, store) -> dict:
    """_instanceMod.netAttack global branch (11806 {CounsellorId})."""
    if not _alliance_row():
        return {"_fail": "TcAllianceInstance config missing"}
    try:
        counsellor_id = int(payload.get("CounsellorId") or 0)
    except (TypeError, ValueError):
        counsellor_id = 0
    unlocked = {int(c.get("CounsellorId") or 0)
                for c in (player.data.get("RoleCounsellor") or [])
                if isinstance(c, dict) and not int(c.get("IsLock") or 0)}
    state = _global_state(player)
    attacks = state.setdefault("Attacks", {})
    if (counsellor_id not in unlocked or str(counsellor_id) in attacks
            or _as_int(state.get("BossHP")) <= 0):
        return {"BossStasus": 1}
    attacks[str(counsellor_id)] = 1
    _hit_global(player, state, _global_attack_damage())
    store.save(player)
    data = {"BossStasus": 0}
    data.update(_global_info(player, state))
    # No AwardItem column exists for a global attack; RemainList stays empty.
    data["RemainList"] = []
    return data


def action_instance_global_boss_attack(payload: dict, player, session, store) -> dict:
    """_instanceMod.action11815: global-boss attack with the ratio callback.

    BossHPMax is seeded from TcAllianceInstance.Blood (non-zero) so the scene's
    ``cur_hp / max_hp`` at instanceGlobalBattleDialog.lua:1572 never divides by
    zero -- the shape-required-fields harness flagged that exact failure.
    """
    if not _alliance_row():
        return {"_fail": "TcAllianceInstance config missing"}
    state = _global_state(player)
    _hit_global(player, state, _global_attack_damage())
    store.save(player)
    return {
        "BossStasus": 0,
        "BossId": _as_int(state.get("BossId")),
        "BossHP": _as_int(state.get("BossHP")),
        "BossHPMax": _as_int(state.get("BossHPMax")),
    }


def action_instance_global_damage(payload: dict, player, session, store) -> dict:
    """_instanceMod.netDamgeData (11809).  The callback builds
    tRankData[1] = DamageRank and tMyRankData[1] = {Ranking, Score, Rid}."""
    state = _global_state(player)
    return {
        "DamageRank": [],
        "RoleRank": 0,
        "Damage": _as_int(state.get("Damage")),
        "RId": 0,                         # numeric role id; server-authoritative
    }


def action_instance_global_damage_rank(payload: dict, player, session, store) -> dict:
    """_instanceMod.action11816: callback forwards A0_3.DamageRank only."""
    return {"DamageRank": []}


# ---------------------------------------------------------------------------
# 11811 - credit exchange
# ---------------------------------------------------------------------------
def action_instance_exchange(payload: dict, player, session, store) -> dict:
    """_instanceMod.netExchange (11811 {ItemId, ItemCount}).

    The module callback only forwards, but instanceCreditsDialog.lua:68-106
    reads RemainList[1].Num as the NEW BossCredits, RequireScore (next cost)
    and ExchangeTimes (remaining exchanges).  Item id comes from
    TcBossExchange.ItemId, the count from the request, and the cost curve from
    TcBossExchange.InitNeed/Increment + ConfigEnvSet.BossCreditsExchangeTimes.
    RemainList[1] must be ItemId 9 (BossCredits), which attach_remain_list does
    not emit (it only handles ids 1-6), so it is built explicitly here -- the
    same convention used by handlers.action_instance_boss_attack.
    """
    try:
        item_id = int(payload.get("ItemId") or payload.get("itemId") or 0)
        count = int(payload.get("ItemCount") or payload.get("itemCount") or 0)
    except (TypeError, ValueError):
        item_id, count = 0, 0
    row = next((r for r in _cfg_rows("TcBossExchange")
                if _as_int(r.get("ItemId")) == item_id), {})
    limit = _env("BossCreditsExchangeTimes", 10)
    state = _exchange_state(player)
    used = max(0, min(limit, _as_int(state.get(str(item_id)))))
    initiative = row.get("InitNeed")
    increment = row.get("Increment")
    require = 0
    if row and initiative is not None:
        try:
            factor = float(increment) if increment is not None else 1.0
            require = int(math.floor(float(initiative) * (factor ** used)))
        except (TypeError, ValueError, OverflowError):
            require = max(0, _as_int(initiative))

    role_data = player.data.setdefault("RoleData", {})
    credits = max(0, _as_int(role_data.get("BossCredits")) - require)
    role_data["BossCredits"] = credits
    if row and count > 0:
        _role_item_add(player, item_id, count)
    new_used = min(limit, used + 1)
    state[str(item_id)] = new_used
    store.save(player)
    try:
        factor = float(increment) if increment is not None else 1.0
        next_require = int(math.floor(float(initiative) * (factor ** new_used))) \
            if initiative is not None else 0
    except (TypeError, ValueError, OverflowError):
        next_require = 0
    return {
        "RemainList": [{"ItemId": 9, "Num": credits}],
        "RequireScore": next_require,
        "ExchangeTimes": max(0, limit - new_used),
    }


# ---------------------------------------------------------------------------
# 11812 / 11813 / 11814 - hunt boxes
# ---------------------------------------------------------------------------
def _hunt_box_response(player, state: dict, box_id: int, award,
                       is_finish: int, received: int, grant: bool) -> dict:
    claims = _as_int(state["claims"].get(str(box_id)))
    limit = _env("OpenHuntBoxLimit", 10)
    data = {
        "IsFinish": int(is_finish),
        "HasReceivedByOthers": int(received),
        "RemainTimes": max(0, limit - claims),
        "InReceiverList": [],
        "ReceiverList": [],
    }
    if grant and award:
        data.update(_grant_and_remain(player, _guaranteed_groups(award)))
    else:
        data["RemainList"] = []
    return data


def action_instance_hunt_box(payload: dict, player, session, store) -> dict:
    """_instanceMod.action11812: open your own hunt box (BoxId).

    chatDialog.lua:3472 reads HasReceivedByOthers (then falls through to 11814)
    and IsFinish; huntBoxDialog.lua reads RemainTimes/RemainList/
    InReceiverList/ReceiverList.  The player's own reward is the guaranteed
    SelfAwardItem groups of TcBossSingleAwardBox.
    """
    try:
        box_id = int(payload.get("BoxId") or 0)
    except (TypeError, ValueError):
        box_id = 0
    row = _box_row(box_id)
    state = _box_state(player)
    claims = _as_int(state["claims"].get(str(box_id)))
    limit = _env("OpenHuntBoxLimit", 10)
    if claims < limit:
        state["claims"][str(box_id)] = claims + 1
        response = _hunt_box_response(player, state, box_id,
                                      row.get("SelfAwardItem"), 0, 0, True)
    else:
        response = _hunt_box_response(player, state, box_id, None, 0, 1, False)
    store.save(player)
    return response


def action_instance_share_box(payload: dict, player, session, store) -> dict:
    """_instanceMod.shareBox (11813).  Callback forwards to a listener with no
    field reads; sharing/receiver fan-out is server-authoritative social state."""
    return {}


def action_instance_receive_hunt_box(payload: dict, player, session, store) -> dict:
    """_instanceMod.action11814: receive a shared hunt box (BoxId).

    chatDialog.lua:3411 reads IsFinish; the shared-box reward is the
    TcBossSingleAwardBox.GlobalAwardItem column, granted once per box id.
    """
    try:
        box_id = int(payload.get("BoxId") or 0)
    except (TypeError, ValueError):
        box_id = 0
    row = _box_row(box_id)
    state = _box_state(player)
    key = str(box_id)
    already = _as_int(state["finished"].get(key))
    grant = already == 0
    if grant:
        state["finished"][key] = 1
    response = _hunt_box_response(player, state, box_id,
                                  row.get("GlobalAwardItem"), 1, 1, grant)
    store.save(player)
    return response


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------
INSTANCE_MORE_HANDLERS = {
    "11804": action_instance_single_use_item,
    "11805": action_instance_global_info,
    "11806": action_instance_global_attack,
    "11807": action_instance_global_use_item,
    "11809": action_instance_global_damage,
    "11811": action_instance_exchange,
    "11812": action_instance_hunt_box,
    "11813": action_instance_share_box,
    "11814": action_instance_receive_hunt_box,
    "11815": action_instance_global_boss_attack,
    "11816": action_instance_global_damage_rank,
}
