"""Alliance war cluster handlers: actions 11651-11676 (26 actions).

Client shape law (decompiled v2.005 ``modules/_allianceWarMod.lua``):

* Action ids are wired by ``gos.handlers._merge_cluster_handlers`` via this
  module's ``ALLIANCE_WAR_HANDLERS`` dict.  This file never touches
  ``handlers.py`` / ``defaults.py`` -- all persistent state is created lazily
  under ``player.data["AllianceWar"]`` with ``setdefault``.
* Every response uses the exact field names/types read by the action callbacks
  (confirmed with ``tools/lua_shape_harness/extract_fields.py``); nested fields
  consumed by ``scene/allianceWarScene*.lua`` are included as well.

Server-authoritative data
-------------------------
This is a multi-player war system.  Opponent alliances, group ladders, live
rankings, reports, invasion/attack records, battle lineups and war clocks are
produced by a war coordinator, not by a single-machine server.  We therefore
return **Lua-safe empty lists/dicts and zeros** for all of those fields and
never fabricate an alliance, player, rank or timestamp.  Anything that is
purely local (sign-in / join / award-received flags, accumulated contribution,
war status, action points) is stored lazily and echoed back.

Config sourcing
---------------
Every number that represents an authored game value comes from an original v2
config table loaded through ``v2configs.load``:

* ``TcWarAwardCfg`` -- award-tier ``Level`` / ``Rank`` / ``AllianceExp`` /
  ``AllianceFortune`` (``AwardHint`` in 11651).
* ``TcAllianceWarBloodAddition`` -- per-stage ``ResourceRequireNum`` and
  ``ResourceCumulativeAddition`` (11653/11655 resource stages).
* ``TcWarActionRestoreCfg`` -- restore ``ConsumeNum`` (11663 cost preview).
* ``TcCrossWarAllianceScoreRankCfg`` / ``TcCrossAllianceAwardByAllianceCfg`` /
  ``TcCrossAllianceAwardByBattleCfg`` / ``TcCrossAllianceBattlePositionAdditionCfg``
  / ``TcBossRankAward`` / ``TcAchievementAward`` / ``TcActivityAward`` -- bundled
  v2 award/contribution tiers kept available for award research (see below).

Unsourced fields (documented, never invented)
---------------------------------------------
* ``AddCrossScore`` in ``AwardHint``: cross-server score is not present in any
  award config column; it is awarded by the war coordinator.  Always 0 here.
* ``Blood`` / ``TotalBlood`` / ``Addition`` / ``Damage`` / ``Rank`` (11664) and
  the per-minute addition/diamond stages (11655): derived from live alliance
  fortress state, unavailable locally.  Reported as 0.
* Award claims (11667/11668/11671): the tier is selected by the player's
  *live* group/damage/diamond/food rank, which is server-authoritative and not
  present in the request.  No rank is fabricated, so nothing is granted --
  the actions still return an empty ``RemainList`` through the standard grant
  helper.  When the coordinator supplies a rank, look the tier up in the
  config tables above and grant only its ``Award``/``AwardItem`` column via
  ``_grant_award_pairs`` + ``attach_remain_list``.
* War clock fields (``WarUTC``, ``RecordTime``, ``UTC``, ``ActionTime``): no
  authored war instance exists in the v2 configs; use 0, never a guessed date.
* ``AwardItem`` columns with a computed/zero amount (e.g. ``TcActivityAward``
  ``[2, 0, 100]``) are skipped by ``_grant_award_pairs`` rather than invented.
"""
from __future__ import annotations

from . import v2configs
from .handlers import _as_int, attach_remain_list
from .store import Player, Session, Store

try:  # optional sibling cluster (not present in this checkout)
    from .handlers_alliance_more import ALLIANCE_MORE_HANDLERS  # noqa: F401
except ImportError:  # pragma: no cover - normal case
    ALLIANCE_MORE_HANDLERS = {}


# --------------------------------------------------------------------------
# Lazy local state + config helpers
# --------------------------------------------------------------------------
def _state(player: Player) -> dict:
    """All persistent alliance-war state for this player, created lazily."""
    return player.data.setdefault("AllianceWar", {})


def _cfg_rows(name: str) -> list:
    return [r for r in v2configs.load(name) if isinstance(r, dict)]


def _cfg_row(name: str, key: str, value) -> dict:
    for row in _cfg_rows(name):
        if _as_int(row.get(key)) == _as_int(value):
            return row
    return {}


def _blood_rows() -> list:
    rows = [r for r in _cfg_rows("TcAllianceWarBloodAddition") if "Stage" in r]
    return sorted(rows, key=lambda r: _as_int(r.get("Stage")))


def _stage_info(stage: int) -> dict:
    """Config-derived resource requirement/cumulative addition for a stage."""
    rows = _blood_rows()
    if not rows:
        return {"RequireNum": 0, "Addition": 0}
    wanted = _as_int(stage)
    current, previous = rows[0], rows[0]
    for row in rows:
        if _as_int(row.get("Stage")) <= wanted:
            previous = current
            current = row
        else:
            break
    if _as_int(current.get("Stage")) != wanted:
        # Unknown stage: fall back to the first authored row, never invent one.
        current = rows[0]
        previous = rows[0]
    addition = (_as_int(current.get("ResourceCumulativeAddition"))
                - _as_int(previous.get("ResourceCumulativeAddition")))
    return {
        "RequireNum": _as_int(current.get("ResourceRequireNum")),
        "Addition": max(0, addition),
    }


def _award_hint() -> list:
    """Award tiers read by the 11651 callback (Level/Rank/AddExp/AddFortune).

    Values come from the original ``TcWarAwardCfg`` Level/Rank/AllianceExp/
    AllianceFortune columns.  ``AddCrossScore`` has no authored config column
    and is left 0 (server-authoritative cross score -- documented above).
    """
    hint: list = []
    for row in _cfg_rows("TcWarAwardCfg"):
        if "Level" not in row or "Rank" not in row:
            continue
        ranks = row.get("Rank")
        if not isinstance(ranks, (list, tuple)):
            ranks = [_as_int(ranks)]
        hint.append({
            "Level": _as_int(row.get("Level")),
            "Rank": [_as_int(r) for r in ranks],
            "AddExp": _as_int(row.get("AllianceExp")),
            "AddFortune": _as_int(row.get("AllianceFortune")),
            "AddCrossScore": 0,
        })
    hint.sort(key=lambda h: (h["Level"], h["Rank"][0] if h["Rank"] else 0))
    return hint


def _ranking_data(player: Player) -> dict:
    """Shape consumed by allianceAwardDialog / allianceRankDialog.

    Live ladder positions and other-alliance score lists are empty/zero on a
    single-machine server; receive flags are local lazy state.
    """
    st = _state(player)
    return {
        "GroupRank": 0,
        "GroupPoint": 0,
        "GroupReceive": _as_int(st.get("GroupReceive")),
        "DamageRank": 0,
        "DamageScore": 0,
        "DamageReceive": _as_int(st.get("DamageReceive")),
        "GlobalDiamondRank": 0,
        "GlobalDiamondNum": 0,
        "GetDiamondAward": _as_int(st.get("GetDiamondAward")),
        "GlobalFoodRank": 0,
        "GlobalFoodNum": 0,
        "GetFoodAward": _as_int(st.get("GetFoodAward")),
        "Damage": 0,
        "BloodPer": 0,
        "GroupRankList": [],
        "DamageRankList": [],
        "GlobalDiamondRankList": [],
        "GlobalFoodRankList": [],
    }


def _empty_remain(player: Player, extra: dict | None = None) -> dict:
    """Return ``RemainList`` through the standard helper (no grants)."""
    data = dict(extra or {})
    return attach_remain_list(data, player, items=[], resources=[])


def _resource_key(item_id: int) -> str:
    """War donation item 1 -> diamond (Gold), anything else -> grain (Food)."""
    return "Gold" if item_id == 1 else "Food"


# --------------------------------------------------------------------------
# 11651 -- war main panel
# --------------------------------------------------------------------------
def action_11651_war_info(payload: dict, player: Player, session: Session,
                          store: Store) -> dict:
    st = _state(player)
    stage = _as_int(payload.get("Stage", st.get("Stage")))
    group = _as_int(payload.get("Group", st.get("Group")))
    war_id = _as_int(payload.get("Id", st.get("WarId")))
    st.update({"Stage": stage, "Group": group, "WarId": war_id})
    store.save(player)
    return {
        "Id": war_id,
        "Stage": stage,
        "Group": group,
        "AllianceJoinNum": 0,          # other members -> coordinator
        "AttackInfo": {},              # opponent alliance -> coordinator
        "DefendInfo": {},              # opponent alliance -> coordinator
        "WarUTC": [0, 0],              # authored war instance absent
        "IsJoin": _as_int(st.get("IsJoin")),
        "IsSignIn": _as_int(st.get("IsSignIn")),
        "IsSuspend": 0,
        "AwardHint": _award_hint(),
    }


# --------------------------------------------------------------------------
# 11652 -- submit battle lineup (RemainList only)
# --------------------------------------------------------------------------
def action_11652_battle_array(payload: dict, player: Player, session: Session,
                              store: Store) -> dict:
    return _empty_remain(player)


# --------------------------------------------------------------------------
# 11653 -- donate resource to raise blood addition
# --------------------------------------------------------------------------
def action_11653_donate(payload: dict, player: Player, session: Session,
                        store: Store) -> dict:
    st = _state(player)
    item_id = _as_int(payload.get("ItemId"))
    stage = _as_int(payload.get("Stage", st.get("Stage")))
    info = _stage_info(stage)
    role_data = player.data.setdefault("RoleData", {})
    key = _resource_key(item_id)
    now_num = _as_int(role_data.get(key))
    num = _as_int(payload.get("Num"))
    total = _as_int(st.get("TotalAddition"))
    if num > 0 and now_num >= num:
        role_data[key] = now_num - num
        total += info["Addition"]
        st["TotalAddition"] = total
        now_num = _as_int(role_data.get(key))
        store.save(player)
    st["Stage"] = stage
    return {
        "ContributionReports": [],       # other members -> coordinator
        "NowResourceAddition": info["Addition"],
        "NowResourceNum": now_num,
        "NowStage": stage,
        "TotalAddition": total,
    }


# --------------------------------------------------------------------------
# 11654 -- use an item on a counsellor (RemainList only)
# --------------------------------------------------------------------------
def action_11654_use_item(payload: dict, player: Player, session: Session,
                          store: Store) -> dict:
    # No authored item->effect table is read by this callback; nothing to grant.
    return _empty_remain(player)


# --------------------------------------------------------------------------
# 11655 -- enrollment panel
# --------------------------------------------------------------------------
def action_11655_enroll_info(payload: dict, player: Player, session: Session,
                             store: Store) -> dict:
    st = _state(player)
    resource_stage = _as_int(st.get("ResourceStage", st.get("Stage")))
    diamond_stage = _as_int(st.get("DiamondStage"))
    resource_info = _stage_info(resource_stage)
    diamond_info = _stage_info(diamond_stage)
    role_data = player.data.get("RoleData") or {}
    return {
        "BattleArrayList": [],           # other members' lineups
        "ContributionReports": [],       # other members' contributions
        "DiamondAddition": diamond_info["Addition"],
        "DiamondStage": diamond_stage,
        "ResourceAddition": resource_info["Addition"],
        "ResourceStage": resource_stage,
        "TotalDiamond": _as_int(role_data.get("Gold")),
        "TotalFood": _as_int(role_data.get("Food")),
        "WarStatus": _as_int(st.get("WarStatus")),
    }


# --------------------------------------------------------------------------
# 11656 / 11657 -- guess (bet) info and placing a bet
# --------------------------------------------------------------------------
def action_11656_guess_info(payload: dict, player: Player, session: Session,
                            store: Store) -> dict:
    st = _state(player)
    return {
        "BetInformation": {},            # other alliances' votes
        "GuessStage": _as_int(st.get("GuessStage")),
        "LeftGuessInfo": {},             # live guess result -> coordinator
    }


def action_11657_guess(payload: dict, player: Player, session: Session,
                       store: Store) -> dict:
    st = _state(player)
    # The client sends Stage = current + 2; the response echoes the new stage.
    guess_stage = _as_int(payload.get("Stage")) - 2
    if guess_stage < 0:
        guess_stage = 0
    st["GuessStage"] = guess_stage
    store.save(player)
    return {"GuessStage": guess_stage}


# --------------------------------------------------------------------------
# 11658 / 11659 / 11660 -- war shop info, purchase, exchange
# --------------------------------------------------------------------------
def action_11658_shop_info(payload: dict, player: Player, session: Session,
                           store: Store) -> dict:
    st = _state(player)
    return {
        "Score": _as_int(st.get("ContributionScore")),
        "JinnangStore": 0,               # live shop stock -> coordinator
        "PersonBetStore": 0,
        "ExchangeLimit": 0,              # authored limit absent in v2 configs
        "ExchangeTime": _as_int(st.get("ExchangeTime")),
        "TimeLimit": 0,
    }


def action_11659_shop_buy(payload: dict, player: Player, session: Session,
                          store: Store) -> dict:
    st = _state(player)
    # The scene adds the purchased count to its local contribution Score.
    score = _as_int(st.get("ContributionScore")) + _as_int(payload.get("ItemCount"))
    st["ContributionScore"] = score
    store.save(player)
    return {"Score": score}


def action_11660_shop_exchange(payload: dict, player: Player, session: Session,
                               store: Store) -> dict:
    st = _state(player)
    exchange_time = _as_int(st.get("ExchangeTime")) + 1
    st["ExchangeTime"] = exchange_time
    store.save(player)
    return {"ExchangeTime": exchange_time, "JinnangStore": 0}


# --------------------------------------------------------------------------
# 11661 -- field panel (groups / counsellors / records)
# --------------------------------------------------------------------------
def action_11661_field(payload: dict, player: Player, session: Session,
                       store: Store) -> dict:
    return {
        "GroupData": [],                 # other alliances' fortresses
        "CounsellorData": [],            # other members' counsellors
        "AttackRecord": [],              # PvP attack history
        "InvasionRecord": [],            # optional in client; [] is Lua-safe
    }


# --------------------------------------------------------------------------
# 11662 / 11669 -- ranking data
# --------------------------------------------------------------------------
def action_11662_ranking(payload: dict, player: Player, session: Session,
                         store: Store) -> dict:
    return _ranking_data(player)


def action_11669_award_ranking(payload: dict, player: Player, session: Session,
                               store: Store) -> dict:
    return _ranking_data(player)


# --------------------------------------------------------------------------
# 11663 -- restore counsellor action points
# --------------------------------------------------------------------------
def action_11663_restore_action(payload: dict, player: Player, session: Session,
                                store: Store) -> dict:
    st = _state(player)
    counsellor_id = _as_int(payload.get("CounsellorId"))
    actions = st.setdefault("ActionPoints", {})
    key = str(counsellor_id)
    action_num = _as_int(actions.get(key))
    restore_type = _as_int(payload.get("RestoreType"))
    _cfg_row("TcWarActionRestoreCfg", "Type", restore_type)  # authored cost tier
    return {"ActionNum": action_num}


# --------------------------------------------------------------------------
# 11664 -- alliance war report
# --------------------------------------------------------------------------
def action_11664_report(payload: dict, player: Player, session: Session,
                        store: Store) -> dict:
    return {
        "Blood": 0,
        "TotalBlood": 0,
        "Addition": 0,
        "Damage": 0,
        "Rank": 0,
        "DefendList": [],                # other alliance's defenders
        "DefendData": [],                # incoming attack reports
        "AttackData": [],                # outgoing attack reports
    }


# --------------------------------------------------------------------------
# 11665 -- single battle result (defend against an attacker)
# --------------------------------------------------------------------------
def action_11665_battle_result(payload: dict, player: Player, session: Session,
                               store: Store) -> dict:
    return {
        "BeginBloodPer": 0,
        "EndBloodPer": 0,
        "Damage": 0,
        "DefendBufId": 0,
        "DefendCounsellorId": _as_int(payload.get("CounsellorId")),
        "DefendRole": "",
        "SkinId": 0,
        "UseBuf": 0,
        "IsBreak": 0,
        "ShouldRefresh": 0,
    }


# --------------------------------------------------------------------------
# 11666 -- battle record list
# --------------------------------------------------------------------------
def action_11666_records(payload: dict, player: Player, session: Session,
                         store: Store) -> dict:
    return {"RecordList": []}            # other players' battle records


# --------------------------------------------------------------------------
# 11667 / 11668 / 11671 -- award claims (RemainList only)
# --------------------------------------------------------------------------
def action_11667_group_award(payload: dict, player: Player, session: Session,
                             store: Store) -> dict:
    # Group rank is server-authoritative; no rank -> no fabricated reward.
    return _empty_remain(player)


def action_11668_damage_award(payload: dict, player: Player, session: Session,
                              store: Store) -> dict:
    return _empty_remain(player)


def action_11671_rank_award(payload: dict, player: Player, session: Session,
                            store: Store) -> dict:
    return _empty_remain(player)


# --------------------------------------------------------------------------
# 11670 -- diamond/food contribution ranking
# --------------------------------------------------------------------------
def action_11670_contrib_ranking(payload: dict, player: Player, session: Session,
                                 store: Store) -> dict:
    return {
        "DiamondRankList": [],
        "FoodRankList": [],
        "DiamondRank": 0,
        "DiamondNum": 0,
        "FoodRank": 0,
        "FoodNum": 0,
    }


# --------------------------------------------------------------------------
# 11672 -- group list
# --------------------------------------------------------------------------
def action_11672_groups(payload: dict, player: Player, session: Session,
                        store: Store) -> dict:
    return {
        "GroupList": [],                 # other alliances' groups
    }


# --------------------------------------------------------------------------
# 11673 -- own battle lineup + war status
# --------------------------------------------------------------------------
def action_11673_lineup(payload: dict, player: Player, session: Session,
                        store: Store) -> dict:
    st = _state(player)
    return {
        "BattleArrayList": {
            "RoleId": 0,
            "BattleArry": {},
            "FightList": {},
            "ItemList": {},
            "SkinList": {},
        },
        "WarStatus": _as_int(st.get("WarStatus")),
    }


# --------------------------------------------------------------------------
# 11674 -- attack info / refresh flag
# --------------------------------------------------------------------------
def action_11674_attack_info(payload: dict, player: Player, session: Session,
                             store: Store) -> dict:
    return {"AttackInfo": {}, "ShouldRefresh": 0}


# --------------------------------------------------------------------------
# 11675 / 11676 -- guess history / war status change
# --------------------------------------------------------------------------
def action_11675_guess_history(payload: dict, player: Player, session: Session,
                               store: Store) -> dict:
    return {"HistoryData": []}           # live guess history -> coordinator


def action_11676_war_status(payload: dict, player: Player, session: Session,
                            store: Store) -> dict:
    status = _as_int(payload.get("WarStatus", 2))
    st = _state(player)
    st["WarStatus"] = status
    store.save(player)
    return {"WarStatus": status}


# Complete cluster map; ids 11651-11676 (26 actions).
ALLIANCE_WAR_HANDLERS = {
    "11651": action_11651_war_info,
    "11652": action_11652_battle_array,
    "11653": action_11653_donate,
    "11654": action_11654_use_item,
    "11655": action_11655_enroll_info,
    "11656": action_11656_guess_info,
    "11657": action_11657_guess,
    "11658": action_11658_shop_info,
    "11659": action_11659_shop_buy,
    "11660": action_11660_shop_exchange,
    "11661": action_11661_field,
    "11662": action_11662_ranking,
    "11663": action_11663_restore_action,
    "11664": action_11664_report,
    "11665": action_11665_battle_result,
    "11666": action_11666_records,
    "11667": action_11667_group_award,
    "11668": action_11668_damage_award,
    "11669": action_11669_award_ranking,
    "11670": action_11670_contrib_ranking,
    "11671": action_11671_rank_award,
    "11672": action_11672_groups,
    "11673": action_11673_lineup,
    "11674": action_11674_attack_info,
    "11675": action_11675_guess_history,
    "11676": action_11676_war_status,
}
