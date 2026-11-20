"""Isolated cluster handlers: treasure hunt (14200-14209) and the
binding-info extras (16002; 16003/16004 are re-exported from gos.handlers).

Shapes are taken from the decompiled v2.005 client callbacks (the response
callback is the function passed as the third argument of
``netProtocol.action(NNNNN, ...)`` inside each ``actionNNNNN`` request sender):

  * 14200 -> _treasureMod.lua request sender ending at line 1700,
             callback L3_2 (lines 1571-1690)
  * 14201 -> sender ending at line 1563, callback L5_2 (lines 1347-1551)
  * 14202 -> sender ending at line 1338, callback L3_2 (lines 1305-1328)
  * 14203 -> sender ending at line 1297, callback L4_2 (lines 1200-1286)
  * 14204 -> sender ending at line 1191, callback L4_2 (lines 1166-1180)
  * 14205 -> sender ending at line 1154, callback L2_2() takes no response
  * 14206 -> sender ending at line 1135, callback L2_2() takes no response
  * 14207 -> sender ending at line 1116, callback L2_2 -> helper L5_1
             (lines 739-..., reads SingleAchievements/SrvAchievements)
  * 14208 -> sender ending at line 1091, callback L3_2 (lines 1012-1081)
  * 14209 -> sender ending at line 1004, callback L2_2 reads no response field
  * 16002 -> roleMod.lua request sender at lines 1073-1102, callback L1_2
             reads A0_3.Status

The action-cluster map lists NO original v2 config table for treasure hunt
(cluster 40, "Config tables: none identified") and none for binding.  The
client config is injected at runtime via ``getEnvCfg``/``setCfgData`` and is
not present in gos/v2configs_data/.  Therefore every value here is neutral
lazy state seeded with setdefault (0 for counters, [] for lists, {} for maps);
NO reward/cost/threshold number is invented.  Fields that would normally be
config-derived are documented in the handler docstrings as unsourced.
"""
from __future__ import annotations

from . import v2configs  # noqa: F401 - imported for parity with sibling clusters
from .handlers import (
    _as_int,
    action_binding_accounts as _existing_action_binding_accounts_16004,
    action_binding_info as _existing_action_binding_info_16003,
)


def _treasure(player) -> dict:
    """Lazy treasure state slice.  All shapes are Lua-safe: lists stay lists,
    maps stay maps, counters are real ints.  No config table exists, so the
    neutral defaults below are the complete initial state."""
    t = player.data.setdefault("Treasure", {})
    # Main info (14200) / award (14201) counters.
    t.setdefault("IsNeedDraw", 0)
    t.setdefault("NeedDrawTimes", 0)
    t.setdefault("SelfSingleRank", 0)
    t.setdefault("DiceNum", 0)
    t.setdefault("AwardDiceNum", 0)
    t.setdefault("TreasureStatus", 0)
    t.setdefault("AwardScore", 0)
    t.setdefault("AwardType", 0)
    t.setdefault("Point", 0)
    t.setdefault("IsNeedTip", 0)
    # Nested tables the client indexes directly (never nil).
    t.setdefault("Info", {})
    t.setdefault("BoardCurrentStatus", {"Lv": 0, "Step": 0})
    t.setdefault("Award", {"ItemId": 0, "Num": 0})
    # List-shaped fields (the client pairs()/table.insert()s them).
    t.setdefault("AwardList", [])
    t.setdefault("HistoryAwardList", [])
    # 14202 pairs() over Awards and indexes [1]/[2], so seed two empty groups.
    t.setdefault("Awards", [[], []])
    t.setdefault("SingleRankList", [])
    t.setdefault("AllianceRankList", [])
    t.setdefault("SingleAchievements", [])
    t.setdefault("SrvAchievements", [])
    return t


def _board(t: dict) -> dict:
    b = t.get("BoardCurrentStatus")
    return dict(b) if isinstance(b, dict) else {"Lv": 0, "Step": 0}


def _list(t: dict, key: str) -> list:
    v = t.get(key)
    return list(v) if isinstance(v, list) else []


def _award(t: dict) -> dict:
    a = t.get("Award")
    return dict(a) if isinstance(a, dict) else {"ItemId": 0, "Num": 0}


# ---------------------------------------------------------------------------
# treasure hunt 14200-14209
# ---------------------------------------------------------------------------
def action_treasure_info(payload, player, session, store) -> dict:
    """14200 main info.  Callback reads IsNeedDraw, NeedDrawTimes,
    SelfSingleRank, DiceNum, AwardDiceNum, BoardCurrentStatus, TreasureStatus,
    HistoryAwardList, AwardList.  All values come from lazy state (no v2
    config table); BoardCurrentStatus.{Lv,Step} are unsourced and default 0."""
    t = _treasure(player)
    store.save(player)
    return {
        "IsNeedDraw": int(t["IsNeedDraw"]),
        "NeedDrawTimes": int(t["NeedDrawTimes"]),
        "SelfSingleRank": int(t["SelfSingleRank"]),
        "DiceNum": int(t["DiceNum"]),
        "AwardDiceNum": int(t["AwardDiceNum"]),
        "BoardCurrentStatus": _board(t),
        "TreasureStatus": int(t["TreasureStatus"]),
        "HistoryAwardList": _list(t, "HistoryAwardList"),
        "AwardList": _list(t, "AwardList"),
    }


def action_treasure_award(payload, player, session, store) -> dict:
    """14201 award/dice result.  Callback reads BoardCurrentStatus,
    AwardScore, Info, SelfSingleRank, DiceNum, AwardType, AwardList, Awards,
    Point, IsNeedTip.  AwardScore/AwardType/Point/IsNeedTip would normally be
    server-authoritative roll results (no config); unsourced -> lazy zero."""
    t = _treasure(player)
    store.save(player)
    return {
        "BoardCurrentStatus": _board(t),
        "AwardScore": int(t["AwardScore"]),
        "Info": dict(t["Info"]) if isinstance(t.get("Info"), dict) else {},
        "SelfSingleRank": int(t["SelfSingleRank"]),
        "DiceNum": int(t["DiceNum"]),
        "AwardType": int(t["AwardType"]),
        "AwardList": _list(t, "AwardList"),
        "Awards": _list(t, "Awards"),
        "Point": int(t["Point"]),
        "IsNeedTip": int(t["IsNeedTip"]),
    }


def action_treasure_board_award(payload, player, session, store) -> dict:
    """14202 leaderboard reward status.  Callback reads SelfSingleRank and
    BoardCurrentStatus (a map with Lv/Step); both unsourced -> lazy state."""
    t = _treasure(player)
    store.save(player)
    return {
        "SelfSingleRank": int(t["SelfSingleRank"]),
        "BoardCurrentStatus": _board(t),
    }


def action_treasure_dig(payload, player, session, store) -> dict:
    """14203 dig a treasure index.  Callback reads Award (map with ItemId and
    Num) and RemainList (list of absolute resource snapshots).  No config and
    no seeded award, so Award is the neutral {ItemId:0, Num:0} and RemainList
    is empty; the request TreasureIndex is not echoed as a fabricated reward."""
    t = _treasure(player)
    store.save(player)
    return {"Award": _award(t), "RemainList": []}


def action_treasure_rank_list(payload, player, session, store) -> dict:
    """14204 rank boards.  Callback reads SingleRankList and AllianceRankList
    (both lists the client pairs() over).  Server-authoritative rankings are
    unsourced offline -> empty lists."""
    t = _treasure(player)
    store.save(player)
    return {
        "SingleRankList": _list(t, "SingleRankList"),
        "AllianceRankList": _list(t, "AllianceRankList"),
    }


def action_treasure_ack(payload, player, session, store) -> dict:
    """14205/14206 acknowledgement actions.  Their callbacks take no response
    parameters (just invoke the success callback), so an empty Lua table is
    the complete safe response."""
    _treasure(player)
    store.save(player)
    return {}


def action_treasure_achievements(payload, player, session, store) -> dict:
    """14207 achievement lists.  The response table is handed to helper L5_1,
    which reads SingleAchievements and SrvAchievements (lists of rows with
    AchievementId/CurrentNum/GetAwardStage).  Achievements are seeded from a
    runtime config that is not bundled -> empty lists."""
    t = _treasure(player)
    store.save(player)
    return {
        "SingleAchievements": _list(t, "SingleAchievements"),
        "SrvAchievements": _list(t, "SrvAchievements"),
    }


def action_treasure_history_awards(payload, player, session, store) -> dict:
    """14208 history award refresh.  Callback pairs() over Awards and reads
    RemainList.  Both come from lazy state (empty by default); unsourced."""
    t = _treasure(player)
    store.save(player)
    return {"Awards": _list(t, "Awards"), "RemainList": []}


def action_treasure_noop(payload, player, session, store) -> dict:
    """14209 callback reads no response field -> empty Lua table."""
    _treasure(player)
    store.save(player)
    return {}


# ---------------------------------------------------------------------------
# binding-info extras (16002-16004)
# ---------------------------------------------------------------------------
def action_binding_status(payload, player, session, store) -> dict:
    """roleMod action16002: callback does setRoleData("Status", A0_3.Status).
    Status is compared to 0 by the client (numeric); it is server/session
    authority and unsourced offline, so echo a lazy int defaulting to 0."""
    base = player.data.setdefault("RoleBase", {})
    base.setdefault("BindingStatus", 0)
    store.save(player)
    return {"Status": _as_int(base.get("BindingStatus") or 0)}


# 16003 and 16004 already exist in gos.handlers.HANDLERS; re-export them
# instead of duplicating their logic (kept under distinct local names to avoid
# clashing with handlers_misc.action_binding_info for 16001).
action_binding_info_16003 = _existing_action_binding_info_16003
action_binding_accounts_16004 = _existing_action_binding_accounts_16004


TREASURE_HANDLERS = {
    "14200": action_treasure_info,
    "14201": action_treasure_award,
    "14202": action_treasure_board_award,
    "14203": action_treasure_dig,
    "14204": action_treasure_rank_list,
    "14205": action_treasure_ack,
    "14206": action_treasure_ack,
    "14207": action_treasure_achievements,
    "14208": action_treasure_history_awards,
    "14209": action_treasure_noop,
    "16002": action_binding_status,
    "16003": action_binding_info_16003,
    "16004": action_binding_accounts_16004,
}
