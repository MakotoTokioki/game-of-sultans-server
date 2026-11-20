"""Court / counsellor challenge (Yamen) cluster (actions 10801-10816).

Standalone handler module — the coordinator merges ``COURT_HANDLERS`` into
``gos.handlers.HANDLERS`` (see ``_merge_cluster_handlers``).  Do not edit
handlers.py / defaults.py here; all new state is created lazily with
``setdefault`` under ``player.data["CourtYamen"]``.

Response shapes were recovered by walking BACKWARD in
``modules/_courtMod.lua`` from each ``netProtocol.action(NNNNN, ...)`` call to
the enclosing ``function Lxx(...)`` and reading the nested response callback
(the shape harness reports NO_REQ_FN for this module because the decompiler
never assigns 10801..10814 to named ``actionNNNNN`` fields).  The main callback
``L5_1(resp, revenge_status)`` (lines ~176-330) parses ``resp.YamenRecord`` for
10801/10802/10803/10804/10805/10806/10812/10815, so every field it reads is
always present here.

Fields read by the callbacks:

    10801 netCourtData  -> YamenRecord{...}, IsFenshenChanllenge, PopUpState,
                           EnemyScore, EnemyNickname, EnemyLv, EnemyAvatar,
                           EnemyDressList, EnemyFigure, AnnouncementMsg,
                           NotReadMsgCount
    10802 netArrestState -> YamenRecord{...}
    10803/10804 netBattle -> YamenRecord{...}, RandomNum, AllKill, EnemyAttr,
                           EnemyYamenScore
    10805 netUseSortie  -> YamenRecord{...}, EnemyScore, EnemyNickname,
                           EnemyLv, EnemyDressList
    10806 netUseChallenge -> YamenRecord{...}, EnemyScore, EnemyNickname
    10807 netMultiAward -> RemainList (callback reads RemainList[1])
    10810 netAnnouncement -> whole response forwarded; no fields read
    10811 netSeachHuman -> Status (optional), RoleInfo
    10812 netInterimAttr -> YamenRecord{...}
    10813 netRankData -> YamenScoreRank[{YamenScore, Nickname, DressList,
                           RankRid, TitleId}], Place, Nickname, Score
    10814 netDefendAndEnemy -> DefendMsg, EnemyMsg
    10815 action10815 -> YamenRecord{...}, EnemyScore, RemainList
                           (callback reads ItemId 1007/1006/510 entries)
    10816 action10816 -> no response fields (callback uses the REQUEST value)

Config-derived numbers (original v2 tables only):
  * ConfigEnvSet.YamenChallengeFreeTimes -> seeded RemainAttackTimes
  * TcYamenEffectCfg (PriceType==1 rows) -> CommonBuyCfg / RevengeBuyCfg
    as [[EffectId, Price], ...] (the client's own fallback in
    courtInterimAttr.lua lines 19-39 is exactly these three rows).
  * TcYamenMultiDefeatPrizeCfg.Prize -> the granted multi-defeat award for
    10807 (only positive amounts, weights are ignored server-side here).

UNSOURCED / server-authoritative values (documented, NOT fabricated as
balance data): the opponent roster (CommonEnemy*Counsellors, RandomNum),
battle outcome / score deltas / multi-kill counters, ranking table
(YamenScoreRank / Place), announcements and defend/enemy messages, RoleInfo
and the multi-defeat prize *roll*.  These are returned as Lua-safe neutral
defaults (0 / "" / {} / []) and should be simulated by the real game server.
"""
from __future__ import annotations

from gos import v2configs
from gos.handlers import (
    _as_int,
    _grant_award_pairs,
    _now,
    _role_item_count,
    attach_remain_list,
)


# ---------------------------------------------------------------------------
# config helpers (no invented numbers)
# ---------------------------------------------------------------------------
def _rows(name: str) -> list[dict]:
    """All dict rows of a v2 config as a fresh list."""
    table = v2configs.load(name)
    if isinstance(table, dict):
        table = list(table.values())
    if isinstance(table, list):
        return [r for r in table if isinstance(r, dict)]
    return []


def _env_int(key: str, default: int = 0) -> int:
    cfg = v2configs.load("ConfigEnvSet")
    if isinstance(cfg, dict) and key in cfg:
        return _as_int(cfg.get(key))
    return default


def _buy_cfg() -> list[list[int]]:
    """[[EffectId, Price], ...] from the gold-priced (PriceType==1) Yamen
    effects.  The client builds the very same list from TcYamenEffectCfg in
    courtInterimAttr.lua; no price is hardcoded here."""
    rows = [r for r in _rows("TcYamenEffectCfg")
            if _as_int(r.get("PriceType")) == 1]
    rows.sort(key=lambda r: _as_int(r.get("EffectId")))
    return [[_as_int(r.get("EffectId")), _as_int(r.get("Price"))] for r in rows]


def _prize_pair() -> list[int]:
    """One deterministic, config-derived multi-defeat award: the first
    positive TcYamenMultiDefeatPrizeCfg.Prize entry as [ItemId, Num].

    The real server picks an entry by the config's weight column (35/30/...);
    that roll is server-authoritative and is intentionally not simulated.  The
    amounts themselves are copied verbatim from the original v2 config."""
    for row in _rows("TcYamenMultiDefeatPrizeCfg"):
        prize = row.get("Prize")
        if not isinstance(prize, (list, tuple)):
            continue
        for entry in prize:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                item_id, num = _as_int(entry[0]), _as_int(entry[1])
                if item_id > 0 and num > 0:
                    return [item_id, num]
    return []


# ---------------------------------------------------------------------------
# lazy state (no defaults.py edit)
# ---------------------------------------------------------------------------
_STATE_KEY = "CourtYamen"
_PEARL_ITEM_ID = 510  # client reads item 510 in the 10815 RemainList callback


def _record_defaults(player) -> dict:
    """Neutral, Lua-safe YamenRecord seed.  Only numeric seeds that come from
    config are non-zero: RemainAttackTimes (ConfigEnvSet) and the buy-cfg list
    (TcYamenEffectCfg).  RefreshUnix is the server clock, not a balance value."""
    buy_cfg = _buy_cfg()
    return {
        "YamenScore": 0,
        "RemainAttackTimes": _env_int("YamenChallengeFreeTimes"),
        "InterimAttrPearl": _role_item_count(player, _PEARL_ITEM_ID),
        "CommonMultiDefeat": 0,
        "RefreshUnix": _now(),
        "CommonAttackStatus": 0,
        "CommonCounsellorId": 0,
        "CommonAttackedCounsellors": [],
        "CommonBuyCfg": buy_cfg,
        "CommonBuyInterimAttrStatus": 0,
        "CommonCounsellorAttr": {},
        "RevengeAttackedCounsellors": [],
        "RevengeMultiDefeat": 0,
        "RevengeAttackStatus": 0,
        "RevengeCounsellorId": 0,
        "RevengeCounsellorAttr": {},
        "RevengeBuyInterimAttrStatus": 0,
        "RevengeBuyCfg": [list(p) for p in buy_cfg],
        "CommonEnemyRandomCounsellors": {},
        "CommonEnemyAllCounsellors": [],
        "RevengeEnemyRandomCounsellors": {},
        "RevengeEnemyAllCounsellors": [],
    }


def _court(player) -> dict:
    """Return the persistent court state, seeding every key the client reads."""
    state = player.data.setdefault(_STATE_KEY, {})
    if not isinstance(state, dict):
        state = {}
        player.data[_STATE_KEY] = state
    record = state.setdefault("YamenRecord", {})
    if not isinstance(record, dict):
        record = {}
        state["YamenRecord"] = record
    for key, value in _record_defaults(player).items():
        if key not in record:
            record[key] = value
    # Root-level fields consumed by the L5_1 parser and the 10801/10813/10814
    # callbacks.  All are present even when empty so Lua never indexes nil.
    state.setdefault("IsFenshenChanllenge", 0)
    state.setdefault("PopUpState", 0)
    state.setdefault("EnemyScore", 0)
    state.setdefault("EnemyNickname", "")
    state.setdefault("EnemyLv", 0)
    state.setdefault("EnemyAvatar", 0)
    state.setdefault("EnemyDressList", [])
    state.setdefault("EnemyFigure", 0)
    state.setdefault("AnnouncementMsg", "")
    state.setdefault("NotReadMsgCount", 0)
    state.setdefault("RandomNum", 0)
    state.setdefault("AllKill", 0)
    state.setdefault("EnemyAttr", {})
    state.setdefault("EnemyYamenScore", 0)
    state.setdefault("YamenScoreRank", [])
    state.setdefault("Place", 0)
    state.setdefault("Nickname", "")
    state.setdefault("Score", 0)
    state.setdefault("DefendMsg", "")
    state.setdefault("EnemyMsg", "")
    state.setdefault("RoleInfo", {})
    return state


def _yamen_view(player) -> dict:
    """Copy YamenRecord into a response-safe dict with concrete int/list/map
    types (the client stores these values and later does arithmetic on them)."""
    record = _court(player)["YamenRecord"]
    int_keys = (
        "YamenScore", "RemainAttackTimes", "InterimAttrPearl", "CommonMultiDefeat",
        "RefreshUnix", "CommonAttackStatus", "CommonCounsellorId",
        "CommonBuyInterimAttrStatus", "RevengeMultiDefeat", "RevengeAttackStatus",
        "RevengeCounsellorId", "RevengeBuyInterimAttrStatus",
    )
    list_keys = (
        "CommonAttackedCounsellors", "CommonEnemyAllCounsellors",
        "RevengeAttackedCounsellors", "RevengeEnemyAllCounsellors",
    )
    map_keys = (
        "CommonCounsellorAttr", "CommonEnemyRandomCounsellors",
        "RevengeCounsellorAttr", "RevengeEnemyRandomCounsellors",
    )
    view = {key: _as_int(record.get(key)) for key in int_keys}
    for key in list_keys:
        value = record.get(key)
        view[key] = [v for v in value] if isinstance(value, (list, tuple)) else []
    for key in map_keys:
        value = record.get(key)
        view[key] = dict(value) if isinstance(value, dict) else {}
    for key in ("CommonBuyCfg", "RevengeBuyCfg"):
        value = record.get(key)
        view[key] = [[_as_int(p[0]), _as_int(p[1])]
                     for p in value
                     if isinstance(p, (list, tuple)) and len(p) >= 2] \
            if isinstance(value, (list, tuple)) else []
    return view


def _enemy_view(player) -> dict:
    state = _court(player)
    dress = state.get("EnemyDressList")
    return {
        "EnemyScore": _as_int(state.get("EnemyScore")),
        "EnemyNickname": str(state.get("EnemyNickname") or ""),
        "EnemyLv": _as_int(state.get("EnemyLv")),
        "EnemyAvatar": _as_int(state.get("EnemyAvatar")),
        "EnemyDressList": [v for v in dress] if isinstance(dress, (list, tuple)) else [],
        "EnemyFigure": _as_int(state.get("EnemyFigure")),
    }


def _record_envelope(player) -> dict:
    """Payload every YamenRecord-driven callback (L5_1) consumes."""
    state = _court(player)
    return {
        "YamenRecord": _yamen_view(player),
        "IsFenshenChanllenge": _as_int(state.get("IsFenshenChanllenge")),
        "PopUpState": _as_int(state.get("PopUpState")),
    }


# ---------------------------------------------------------------------------
# action handlers
# ---------------------------------------------------------------------------
def action_10801(payload, player, session, store) -> dict:
    """netCourtData: full Yamen entry state."""
    state = _court(player)
    data = _record_envelope(player)
    data.update(_enemy_view(player))
    data["AnnouncementMsg"] = str(state.get("AnnouncementMsg") or "")
    data["NotReadMsgCount"] = _as_int(state.get("NotReadMsgCount"))
    store.save(player)
    return data


def action_10802(payload, player, session, store) -> dict:
    """netArrestState: the callback only parses YamenRecord."""
    store.save(player)
    return _record_envelope(player)


def action_10803(payload, player, session, store) -> dict:
    """netBattle (normal attack).  Opponent / RNG values stay neutral."""
    state = _court(player)
    data = _record_envelope(player)
    data["RandomNum"] = _as_int(state.get("RandomNum"))
    data["AllKill"] = _as_int(state.get("AllKill"))
    attr = state.get("EnemyAttr")
    data["EnemyAttr"] = dict(attr) if isinstance(attr, dict) else {}
    data["EnemyYamenScore"] = _as_int(state.get("EnemyYamenScore"))
    store.save(player)
    return data


def action_10804(payload, player, session, store) -> dict:
    """netBattle (revenge attack) — same fields as 10803."""
    return action_10803(payload, player, session, store)


def action_10805(payload, player, session, store) -> dict:
    """netUseSortie: YamenRecord plus a subset of the enemy summary."""
    data = _record_envelope(player)
    enemy = _enemy_view(player)
    for key in ("EnemyScore", "EnemyNickname", "EnemyLv", "EnemyDressList"):
        data[key] = enemy[key]
    store.save(player)
    return data


def action_10806(payload, player, session, store) -> dict:
    """netUseChallenge: YamenRecord plus EnemyScore / EnemyNickname."""
    data = _record_envelope(player)
    enemy = _enemy_view(player)
    for key in ("EnemyScore", "EnemyNickname"):
        data[key] = enemy[key]
    store.save(player)
    return data


def action_10807(payload, player, session, store) -> dict:
    """netMultiAward: grant one original-config multi-defeat prize and ship
    it as RemainList[1] (the callback's only response read)."""
    pair = _prize_pair()
    pairs: list[list[int]] = []
    bag_ids: list[int] = []
    if pair:
        pairs, bag_ids = _grant_award_pairs(player, [pair])
        store.save(player)
    return attach_remain_list({}, player, items=bag_ids, resources=[])


def action_10810(payload, player, session, store) -> dict:
    """netAnnouncement: the response is forwarded unread to the caller."""
    return {}


def action_10811(payload, player, session, store) -> dict:
    """netSeachHuman: Status is optional; RoleInfo is server-authoritative."""
    state = _court(player)
    info = state.get("RoleInfo")
    return {
        "Status": _as_int(state.get("Status", 0)),
        "RoleInfo": dict(info) if isinstance(info, dict) else {},
    }


def action_10812(payload, player, session, store) -> dict:
    """netInterimAttr: the callback only parses YamenRecord."""
    store.save(player)
    return _record_envelope(player)


def action_10813(payload, player, session, store) -> dict:
    """netRankData: the ranking table is server-authoritative -> empty list."""
    state = _court(player)
    rows = state.get("YamenScoreRank")
    rank = []
    if isinstance(rows, (list, tuple)):
        for row in rows:
            if not isinstance(row, dict):
                continue
            dress = row.get("DressList")
            rank.append({
                "YamenScore": _as_int(row.get("YamenScore")),
                "Nickname": str(row.get("Nickname") or ""),
                "DressList": [v for v in dress] if isinstance(dress, (list, tuple)) else [],
                "RankRid": _as_int(row.get("RankRid")),
                "TitleId": _as_int(row.get("TitleId")),
            })
    return {
        "YamenScoreRank": rank,
        "Place": _as_int(state.get("Place")),
        "Nickname": str(state.get("Nickname") or ""),
        "Score": _as_int(state.get("Score")),
    }


def action_10814(payload, player, session, store) -> dict:
    """netDefendAndEnemy: the two message strings are server-authoritative."""
    state = _court(player)
    return {
        "DefendMsg": str(state.get("DefendMsg") or ""),
        "EnemyMsg": str(state.get("EnemyMsg") or ""),
    }


def action_10815(payload, player, session, store) -> dict:
    """action10815 (BuyEffect): battle result envelope.

    The client sends BuyEffect = 0/1/2 (primary/intermediate/advanced) and its
    callback reads EnemyScore, YamenRecord and RemainList (ItemId 1007/1006/
    510 deltas).  Cost settlement and the battle award are server-authoritative
    and the client uses hardcoded gold prices (1/2/20) that do not map cleanly
    onto TcYamenEffectCfg.PriceType, so no cost/award is fabricated here; the
    effect choice is persisted and an empty RemainList is returned."""
    state = _court(player)
    state["LastBuyEffect"] = _as_int(payload.get("BuyEffect"))
    data = _record_envelope(player)
    data["EnemyScore"] = _as_int(state.get("EnemyScore"))
    store.save(player)
    return attach_remain_list(data, player, items=[], resources=[])


def action_10816(payload, player, session, store) -> dict:
    """action10816: persist the checkbox value; callback reads no response
    field (it writes the REQUEST PopUpState into client state)."""
    state = _court(player)
    state["PopUpState"] = _as_int(payload.get("PopUpState"))
    store.save(player)
    return {}


COURT_HANDLERS = {
    "10801": action_10801,
    "10802": action_10802,
    "10803": action_10803,
    "10804": action_10804,
    "10805": action_10805,
    "10806": action_10806,
    "10807": action_10807,
    "10810": action_10810,
    "10811": action_10811,
    "10812": action_10812,
    "10813": action_10813,
    "10814": action_10814,
    "10815": action_10815,
    "10816": action_10816,
}
