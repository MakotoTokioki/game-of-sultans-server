"""Tribute extras (12002-12004) and universal/rotating activities (12202-12213).

Kept in a separate module so ``gos/handlers.py`` and ``gos/defaults.py`` stay
untouched; the coordinator merges ``ACTIVITIES_HANDLERS`` into ``HANDLERS``.

Everything here is single-player.  Response shapes were recovered by walking
backwards from each ``actionNNNNN = Lxx`` assignment to its request function in
the decompiled modules and reading the nested response callback (and, where the
module callback only forwards to a listener, the scene listener as well):

* ``_tributeMod.lua``            -> 12002 NowMaid; 12003 Charm (scene listener);
                                     12004 HasMaidNum + TotalCharm
* ``_universalActivitiesMod.lua`` -> field lists per action, see each handler.

Reward policy (per HANDLER_SPEC): NO number is invented.  The only config with
an ``AwardItem`` column reachable by these actions is ``TcCountry`` (tribute),
so only 12003 grants items, via the shared ``_grant_award_pairs`` +
``attach_remain_list`` helpers.  The rotating-activity configs
(TcCrossServiceActivityCfg / TcIndianaActivityCfg / TcLimitedActivity /
TcRankUpActivityCfg / TcActivityAward) contain NO AwardItem/Rewards column and
the client's StoreCfg/ExchangeCfg/AwardCfg come from bundled client assets
(``universalCfg.lua``), not from the server configs.  Activity grants,
schedules and leaderboards are therefore server-authoritative: those responses
use Lua-safe empty/zero defaults and are listed in the report as research
items.  No persistent state is seeded in defaults.py -- all new state is lazy.
"""
from __future__ import annotations

from gos import v2configs
from gos.handlers import (
    _REMAIN_LIST_MAP,
    _as_int,
    _grant_award_pairs,
    attach_remain_list,
)


# ---------------------------------------------------------------------------
# config / lazy-state helpers
# ---------------------------------------------------------------------------
def _cfg_rows(name: str) -> list[dict]:
    rows = v2configs.load(name)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, (list, tuple)):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _country_row(country_id: int) -> dict | None:
    for row in _cfg_rows("TcCountry"):
        if _as_int(row.get("Id")) == country_id:
            return row
    return None


def _beauty_init_charm(beauty_id: int) -> int:
    """Config-sourced base charm for a wife with no RoleBeauty row yet."""
    for row in _cfg_rows("TcBeauty"):
        if _as_int(row.get("BeautyId")) == beauty_id:
            return _as_int(row.get("InitCharm"))
    return 0


def _tribute_state(player) -> dict:
    return player.data.setdefault("TributeExtra", {})


def _act_state(player, activity_id: int) -> dict:
    return player.data.setdefault("UniversalActivityExtra", {}).setdefault(str(activity_id), {})


def _activity_id(payload: dict) -> int:
    return _as_int(payload.get("ActivityId") or payload.get("activityId") or 0)


# ---------------------------------------------------------------------------
# tribute extras
# ---------------------------------------------------------------------------
def action_12002(payload: dict, player, session, store) -> dict:
    """_tributeMod.action12002: query the current maid count for a country.

    The callback reads only ``A0_3.NowMaid`` and derives
    HasMaidNum = (NowMaid ~= 0) and TotalCharm = NowMaid locally.  NowMaid is
    player state (live tribute progress), not a config value, so it is read
    from the lazy TributeExtra slice seeded by 12004 (default 0, Lua-safe).
    """
    country = _as_int(payload.get("CountryId") or payload.get("countryId") or 0)
    maids = _tribute_state(player).setdefault("Maids", {})
    return {"NowMaid": _as_int(maids.get(str(country)) or 0)}


def action_12003(payload: dict, player, session, store) -> dict:
    """_tributeMod.action12003: tribute one beauty to a country.

    Module callback forwards the whole response to the tributeScene listener,
    which reads ``A0_4.Charm`` and writes it to the wife's Charm.  Charm is
    derived only from config (``TcCountry.MaidCharm`` added to the wife's
    current Charm, or ``TcBeauty.InitCharm`` when she has no row yet).  The
    country's ``TcCountry.AwardItem`` is granted; no other item is invented.
    """
    country = _as_int(payload.get("CountryId") or payload.get("countryId") or 0)
    beauty = _as_int(payload.get("BeautyId") or payload.get("beautyId") or 0)
    row = _country_row(country)

    pairs: list[list[int]] = []
    bag_ids: list[int] = []
    charm = 0
    if row is not None:
        pairs, bag_ids = _grant_award_pairs(player, row.get("AwardItem"))
        bonus = _as_int(row.get("MaidCharm"))
        wives = player.data.setdefault("RoleBeauty", {})
        key = str(beauty)
        wife = wives.get(key)
        if isinstance(wife, dict):
            charm = _as_int(wife.get("Charm")) + bonus
            wife["Charm"] = charm
        else:
            charm = _beauty_init_charm(beauty) + bonus
            wives[key] = {"BeautyId": beauty, "Charm": charm}
        store.save(player)

    return attach_remain_list(
        {"Charm": charm},
        player,
        items=bag_ids,
        resources=[int(p[0]) for p in pairs if int(p[0]) in _REMAIN_LIST_MAP],
    )


def action_12004(payload: dict, player, session, store) -> dict:
    """_tributeMod.action12004: consume ``UseNum`` maids for a country.

    Callback reads ``A0_3.HasMaidNum`` and ``A0_3.TotalCharm`` directly and
    stores them via setMaidData.  Both are live tribute state: HasMaidNum is
    1 while the resulting NowMaid is non-zero (same rule the 12002 callback
    derives), and TotalCharm is the accumulated charm, increased by
    ``UseNum * TcCountry.MaidCharm`` (config-sourced).  No item grant is
    attached: TcCountryAwards/TcCountryLv have no AwardItem column and the
    chapter/threshold mapping is server-authoritative.
    """
    country = _as_int(payload.get("CountryId") or payload.get("countryId") or 0)
    use_num = max(0, _as_int(payload.get("UseNum") or payload.get("useNum") or 0))
    row = _country_row(country)

    st = _tribute_state(player)
    maids = st.setdefault("Maids", {})
    charms = st.setdefault("CountryCharm", {})
    key = str(country)
    now_maid = _as_int(maids.get(key) or 0) + use_num
    maids[key] = now_maid
    total_charm = _as_int(charms.get(key) or 0)
    if row is not None:
        total_charm += use_num * _as_int(row.get("MaidCharm"))
    charms[key] = total_charm
    store.save(player)

    return {"HasMaidNum": 1 if now_maid else 0, "TotalCharm": total_charm}


# ---------------------------------------------------------------------------
# universal / rotating activities
# ---------------------------------------------------------------------------
def _rank_list() -> dict:
    """SingleRankList/AllianceRankList shape: scenes read RankList and
    SelfRankData.Ranking directly, so both keys are always present."""
    return {"RankList": [], "SelfRankData": {"Ranking": 0}}


def _boss_status() -> dict:
    """BossCurrentStatus.CurrentStage is indexed directly by 12202/12203."""
    return {"CurrentStage": 0}


def action_12202(payload: dict, player, session, store) -> dict:
    """_universalActivitiesMod.action12202: activity overview query.

    Callback stores SingleRankList, AllianceRankList, AwardList, AttackItems,
    BossCurrentStatus (reads .CurrentStage), SelfGetAwardStage, IsReceiveBox,
    HistoryAwardList and passes WareHouseScore to setPlayerInfo.  All values
    are live/scheduled activity state -> Lua-safe defaults, no config numbers.
    """
    return {
        "SingleRankList": _rank_list(),
        "AllianceRankList": _rank_list(),
        "AwardList": [],
        "AttackItems": [],
        "BossCurrentStatus": _boss_status(),
        "SelfGetAwardStage": 0,
        "IsReceiveBox": 0,
        "WareHouseScore": 0,
        "HistoryAwardList": [],
    }


def action_12203(payload: dict, player, session, store) -> dict:
    """_universalActivitiesMod.action12203: boss attack / stage claim.

    Callback reads AwardList (list), BossCurrentStatus.CurrentStage, RankData,
    SingleRankList, AllianceRankList, Awards (-> addRewardInActivity) and
    AttackItem.ItemId/.Num.  Awards/leaderboards are live state -> defaults.
    """
    return {
        "AwardList": [],
        "BossCurrentStatus": _boss_status(),
        "RankData": {},
        "SingleRankList": _rank_list(),
        "AllianceRankList": _rank_list(),
        "Awards": [],
        "AttackItem": {"ItemId": 0, "Num": 0},
    }


def action_12204(payload: dict, player, session, store) -> dict:
    """_universalActivitiesMod.action12204: activity shop query.

    Callback pairs() ``ShopData`` rows and reads each ``ItemId``/``Num``
    (purchased counts) to subtract from the client's StoreCfg.  Purchase counts
    are player state -> empty list (Lua-safe for pairs).
    """
    return {"ShopData": []}


def action_12205(payload: dict, player, session, store) -> dict:
    """_universalActivitiesMod.action12205: activity exchange query.

    Callback passes WareHouseScore to setPlayerInfo and pairs() ``ExchangeData``
    rows reading ``ItemId``/``Num``.  Exchange counts are player state -> empty.
    """
    return {"WareHouseScore": 0, "ExchangeData": []}


def action_12206(payload: dict, player, session, store) -> dict:
    """_universalActivitiesMod.action12206: set attack item for an activity.

    Callback passes ``AttackItem`` to setAttackItem, which iterates pairs and
    reads ``ItemId``/``Num`` on each row.  Echo the requested item/count
    (request fields are echoed back, never invented) in a Lua-safe list.
    """
    item_id = _as_int(payload.get("ItemId") or payload.get("item_id") or 0)
    count = _as_int(payload.get("ItemCount") or payload.get("itemCount") or 0)
    return {"AttackItem": [{"ItemId": item_id, "Num": count}]}


def action_12207(payload: dict, player, session, store) -> dict:
    """_universalActivitiesMod.action12207: exchange items in an activity.

    Callback only passes ``WareHouseScore`` to setPlayerInfo.  The real score
    delta is server-authoritative; keep the lazy score (default 0).
    """
    aid = _activity_id(payload)
    state = _act_state(player, aid)
    state["WareHouseScore"] = _as_int(state.get("WareHouseScore") or 0)
    return {"WareHouseScore": state["WareHouseScore"]}


def action_12208(payload: dict, player, session, store) -> dict:
    """_universalActivitiesMod.action12208: claim a stage award.

    Callback writes ``A0_3.SelfGetAwardStage`` into the module.  Request
    carries ``AwardStage``; echo/store it.  Which items a stage grants lives
    in client assets (AwardCfg.ChiefItemCfg), so no server grant is invented.
    """
    aid = _activity_id(payload)
    state = _act_state(player, aid)
    stage = _as_int(payload.get("AwardStage") or payload.get("awardStage")
                    or state.get("SelfGetAwardStage") or 0)
    state["SelfGetAwardStage"] = stage
    store.save(player)
    return {"SelfGetAwardStage": stage}


def action_12209(payload: dict, player, session, store) -> dict:
    """_universalActivitiesMod.action12209: draw a reward box.

    Callback forwards ``A0_3.AwardData`` to the reward-prompt listener; an
    empty list is Lua-safe.  Actual box contents are server-authoritative.
    """
    return {"AwardData": []}


def action_12210(payload: dict, player, session, store) -> dict:
    """_universalActivitiesMod.action12210: rank award status query.

    Callback pairs() over SingleRankList/AllianceRankList (reads
    .SelfRankData.Ranking on each) and reads ReceivedSAward/ReceivedAAward.
    Rankings and claim flags are server state -> default 0/false.
    """
    return {
        "SingleRankList": _rank_list(),
        "AllianceRankList": _rank_list(),
        "ReceivedSAward": 0,
        "ReceivedAAward": 0,
    }


def action_12211(payload: dict, player, session, store) -> dict:
    """_universalActivitiesMod.action12211: activity messages query.

    Callback only assigns ``A0_3.AwardList`` to AwardInfo (a list that scenes
    iterate).  Messages/award records are live state -> empty list.
    """
    return {"AwardList": []}


def action_12212(payload: dict, player, session, store) -> dict:
    """_universalActivitiesMod.action12212: no-op refresh.

    Callback reads no response field (just calls the listener), so a Lua-safe
    empty envelope is correct.
    """
    return {}


def action_12213(payload: dict, player, session, store) -> dict:
    """_universalActivitiesMod.action12213: claim a rank award.

    Request carries awardType/ranking/AwardId; the callback reads no response
    field (marks isReceived locally, forwards the envelope).  Award contents
    come from client AwardCfg, not from any server config, so no item grant is
    fabricated -- return a Lua-safe empty envelope.
    """
    return {}


ACTIVITIES_HANDLERS = {
    "12002": action_12002,
    "12003": action_12003,
    "12004": action_12004,
    "12202": action_12202,
    "12203": action_12203,
    "12204": action_12204,
    "12205": action_12205,
    "12206": action_12206,
    "12207": action_12207,
    "12208": action_12208,
    "12209": action_12209,
    "12210": action_12210,
    "12211": action_12211,
    "12212": action_12212,
    "12213": action_12213,
}
