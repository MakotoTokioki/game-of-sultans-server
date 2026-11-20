"""Lantern event (12500-12506) + seven-day login (12601) handlers.

This module is isolated from ``gos/handlers.py`` / ``gos/defaults.py``:
``_merge_cluster_handlers()`` imports ``EVENTS_HANDLERS`` from here.

Response shapes come from the decompiled v2.005 client callbacks (cross-checked
with ``tools/lua_shape_harness/extract_fields.py`` and the lantern scene):

  12500 _lanternMod.enter      -> CurrentLv, CandleNum, CurrentScore,
                                  IsFirstEnter, SelfRank, ServerRecord,
                                  SelfRecord
  12501 _lanternMod.light      -> CandleNum, CurrentScore, SelfRank,
                                  ServerRecord, GetAwards, LampAwards
  12502 _lanternMod.upgrade    -> CurrentLv
  12503 _lanternMod.achievements -> PersonAchievementData,
                                  ServerAchievementData (fed to getAwardList)
  12504 _lanternMod.claim achievement -> response ignored by client
  12505 _lanternMod.rank lists -> SingleAwardList, AllianceAwardList,
                                  SingleRankList, AllianceRankList,
                                  GetSingleAward, GetAllianceAward
                                  (the last two are read by lanternScene)
  12506 _lanternMod.claim rank award  -> response only forwarded
  12601 _sevenDaysMod.claim    -> response ignored; day award is granted here

Rewards use only original v2 config AwardItem-like columns through the shared
handlers.py helpers.  Event windows, rank data, lamp progress and achievement
thresholds/awards are server-authoritative and have no v2 config source: they
are returned as Lua-safe zero/empty defaults (see the report's research list).
"""
from __future__ import annotations

from gos import v2configs
from gos.handlers import (
    _REMAIN_LIST_MAP,
    _as_int,
    _grant_award_pairs,
    attach_remain_list,
)

_LAMP_CFG = "TcLampActivityAchievement"
_WELFARE_CFG = "TcLWelfareAwards"


# ---------------------------------------------------------------------------
# config / state helpers
# ---------------------------------------------------------------------------
def _rows(name: str) -> list[dict]:
    rows = v2configs.load(name)
    if isinstance(rows, dict):
        rows = list(rows.values())
    return [r for r in rows if isinstance(r, dict)]


def _lamp_achievement_ids() -> set[int]:
    """AchievementIds that exist in the original TcLampActivityAchievement.

    That table only carries Lang/AchievementId/AchievementType/Desc; its
    thresholds and awards are server-authoritative and are NOT invented here.
    """
    return {
        _as_int(r.get("AchievementId"))
        for r in _rows(_LAMP_CFG)
        if r.get("AchievementId") is not None
    }


def _welfare_award(stage: int) -> list | None:
    for row in _rows(_WELFARE_CFG):
        if _as_int(row.get("Day")) == stage:
            award = row.get("Award")
            return award if isinstance(award, list) else None
    return None


def _lantern_state(player) -> dict:
    """All lantern state is created lazily; no defaults.py edit is needed."""
    st = player.data.setdefault("LanternEvent", {})
    st.setdefault("CurrentLv", 0)
    st.setdefault("CandleNum", 0)
    st.setdefault("CurrentScore", 0)
    st.setdefault("IsFirstEnter", 0)
    st.setdefault("SelfRank", 0)
    st.setdefault("ServerRecord", [])
    st.setdefault("SelfRecord", [])
    st.setdefault("GetAwards", [])
    st.setdefault("LampAwards", [])
    # achievement rows are server-authoritative; empty lists keep the client's
    # getAwardList() pairs() loops safe without fabricating RequireNum/Awards.
    st.setdefault("PersonAchievementData", [])
    st.setdefault("ServerAchievementData", [])
    st.setdefault("SingleAwardList", [])
    st.setdefault("AllianceAwardList", [])
    st.setdefault("SingleRankList", {})
    st.setdefault("AllianceRankList", {})
    st.setdefault("GetSingleAward", 0)
    st.setdefault("GetAllianceAward", 0)
    st.setdefault("ClaimedAchievements", {})
    st.setdefault("ClaimedRankAwards", {})
    return st


def _list(value) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def _rank_list(st: dict, key: str, alliance: bool = False) -> dict:
    """Return a Lua-safe rank table; the scene indexes
    ``<List>.SelfRankData.Ranking``, ``<List>.RankList`` and (alliance only)
    ``AllianceRankList.SelfAllianceName`` unconditionally."""
    value = st.get(key)
    if not isinstance(value, dict):
        value = {}
        st[key] = value
    value.setdefault("RankList", [])
    value.setdefault("SelfRankData", {})
    self_rank = value["SelfRankData"]
    if not isinstance(self_rank, dict):
        self_rank = {}
        value["SelfRankData"] = self_rank
    self_rank.setdefault("Ranking", 0)
    if alliance:
        value.setdefault("SelfAllianceName", "")
    return value


# ---------------------------------------------------------------------------
# lantern event handlers
# ---------------------------------------------------------------------------
def action_12500(payload: dict, player, session, store) -> dict:
    """Enter / refresh the lantern event (main state callback)."""
    st = _lantern_state(player)
    return {
        "CurrentLv": _as_int(st["CurrentLv"]),
        "CandleNum": _as_int(st["CandleNum"]),
        "CurrentScore": _as_int(st["CurrentScore"]),
        "IsFirstEnter": _as_int(st["IsFirstEnter"]),
        "SelfRank": _as_int(st["SelfRank"]),
        "ServerRecord": _list(st["ServerRecord"]),
        "SelfRecord": _list(st["SelfRecord"]),
    }


def action_12501(payload: dict, player, session, store) -> dict:
    """Light lantern(s): callback stores scores and appends GetAwards to the
    history list and each LampAwards[1] to the lamp-awards list."""
    st = _lantern_state(player)
    return {
        "CandleNum": _as_int(st["CandleNum"]),
        "CurrentScore": _as_int(st["CurrentScore"]),
        "SelfRank": _as_int(st["SelfRank"]),
        "ServerRecord": _list(st["ServerRecord"]),
        "GetAwards": _list(st["GetAwards"]),
        "LampAwards": _list(st["LampAwards"]),
    }


def action_12502(payload: dict, player, session, store) -> dict:
    """Upgrade lamp: callback only consumes CurrentLv."""
    st = _lantern_state(player)
    return {"CurrentLv": _as_int(st["CurrentLv"])}


def action_12503(payload: dict, player, session, store) -> dict:
    """Achievement list: the client feeds both tables into getAwardList()."""
    st = _lantern_state(player)
    return {
        "PersonAchievementData": _list(st["PersonAchievementData"]),
        "ServerAchievementData": _list(st["ServerAchievementData"]),
    }


def action_12504(payload: dict, player, session, store) -> dict:
    """Claim a personal achievement reward.

    Request carries AchievementId + GetAward; the client's callback ignores the
    response.  TcLampActivityAchievement has no award column, so no item is
    granted here -- only the server-side claim index is recorded.
    """
    st = _lantern_state(player)
    achievement_id = _as_int(
        payload.get("AchievementId") or payload.get("achievementId") or 0
    )
    get_award = _as_int(payload.get("GetAward") or payload.get("getAward") or 0)
    if achievement_id in _lamp_achievement_ids():
        claims = st["ClaimedAchievements"]
        if not isinstance(claims, dict):
            claims = {}
            st["ClaimedAchievements"] = claims
        claims[str(achievement_id)] = get_award
        store.save(player)
    return {}


def action_12505(payload: dict, player, session, store) -> dict:
    """Rank / rank-award lists.  The module callback stores the four lists;
    lanternScene also reads GetSingleAward / GetAllianceAward and indexes
    SingleRankList.SelfRankData.Ranking, *.RankList and SelfAllianceName."""
    st = _lantern_state(player)
    return {
        "SingleAwardList": _list(st["SingleAwardList"]),
        "AllianceAwardList": _list(st["AllianceAwardList"]),
        "SingleRankList": _rank_list(st, "SingleRankList"),
        "AllianceRankList": _rank_list(st, "AllianceRankList", alliance=True),
        "GetSingleAward": _as_int(st["GetSingleAward"]),
        "GetAllianceAward": _as_int(st["GetAllianceAward"]),
    }


def action_12506(payload: dict, player, session, store) -> dict:
    """Claim a single/alliance rank award.

    Request carries AwardId + AwardType (the client initially builds lowercase
    awardId/awardType).  No v2 config supplies a rank-award amount, so nothing
    is granted; the server just records the claim.
    """
    st = _lantern_state(player)
    award_id = _as_int(payload.get("AwardId") or payload.get("awardId") or 0)
    award_type = _as_int(payload.get("AwardType") or payload.get("awardType") or 0)
    if award_id or award_type:
        claims = st["ClaimedRankAwards"]
        if not isinstance(claims, dict):
            claims = {}
            st["ClaimedRankAwards"] = claims
        claims[str(award_type)] = award_id
        store.save(player)
    return {}


# ---------------------------------------------------------------------------
# seven-day login (12601)
# ---------------------------------------------------------------------------
def action_12601(payload: dict, player, session, store) -> dict:
    """Claim a seven-day login award.

    The client sends ``Stage`` (1..7; it caps higher values at 7) and its
    callback reads no response field -- it computes the displayed award from
    TcLWelfareAwards locally.  The server grants that same config award and
    tracks the claimed day in ExtraData so a claim cannot be repeated.
    """
    stage = _as_int(payload.get("Stage") or payload.get("stage") or 0)
    if stage > 7:  # the client's getAward() clamps Day to 7
        stage = 7
    award = _welfare_award(stage) if stage > 0 else None
    if not award:
        return attach_remain_list({}, player, resources=[])

    extra = player.data.setdefault("ExtraData", {})
    claimed = _as_int(extra.get("lwReceiveNum") or 0)
    if stage <= claimed:
        return attach_remain_list({}, player, resources=[])

    pairs, bag_ids = _grant_award_pairs(player, award)
    extra["lwReceiveNum"] = stage
    extra["LoginWelfare"] = stage
    store.save(player)
    return attach_remain_list(
        {}, player, items=bag_ids,
        resources=[int(p[0]) for p in pairs if int(p[0]) in _REMAIN_LIST_MAP],
    )


EVENTS_HANDLERS = {
    "12500": action_12500,
    "12501": action_12501,
    "12502": action_12502,
    "12503": action_12503,
    "12504": action_12504,
    "12505": action_12505,
    "12506": action_12506,
    "12601": action_12601,
}
