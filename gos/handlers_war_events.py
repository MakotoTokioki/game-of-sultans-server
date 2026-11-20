"""Late-game war/event action cluster handlers (181xx-188xx).

Kept in a separate module so ``gos/handlers.py`` and ``gos/defaults.py`` stay
untouched.  ``gos.handlers._merge_cluster_handlers()`` already imports
``WAR_EVENTS_HANDLERS`` and merges it into ``HANDLERS``.

Source modules (``/tmp/opencode/gos/decomp_all/modules/``):
  _vedioMod.lua, _exploreMysteryMod.lua, _infiniteWarMod.lua,
  _crossWarMod.lua, _kingBossMod.lua, _ramadanMod.lua, _recallMod.lua.

Response shapes were recovered by walking BACKWARD from every
``actionNNNNN = Lxx`` assignment to its request function and reading the
nested response callback, cross-checked with
``tools/lua_shape_harness/extract_fields.py``.  The harness misses callbacks
that use the ``pairs({"FieldA", "FieldB", ...})`` field-name list idiom (the
recall module does this for 18801/18804/18806/18807), so those fields were
added by reading the module directly.

Policy (per HANDLER_SPEC):
  * These are MULTI-PLAYER / server-authoritative clusters (opponents, war
    reports, leaderboards, schedules, other-player armies).  Fields that can
    only come from the live server are returned as Lua-safe empty lists/maps
    and numeric 0 -- NO opponent, rank, report or reward is fabricated.
  * Player-owned scalar state (video counters, cheer counts, dart counts,
    claim flags) is stored lazily via ``setdefault`` (no defaults.py change)
    and persisted with ``store.save`` on mutating actions.
  * Config-backed Cfg/award-tier fields are filled from ORIGINAL v2 config
    tables (TcRelicGrid, TcRamadanMenuCfg, TcPlayerComebackTask, ...), copied
    verbatim into ``gos/v2configs_data/``.  The only item-granting config in
    this cluster is TcVedio/TcPlayerComeback*/TcInfiniteBattle*/TcCrossWar*;
    those grants are server-authoritative claim flows and are intentionally
    NOT executed here (see report) -- award/record fields are Lua-safe empty.
"""
from __future__ import annotations

from gos import v2configs
from gos.handlers import _as_int


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _rows(name: str) -> list[dict]:
    """Original config rows as a Lua-safe list of plain dicts."""
    rows = v2configs.load(name)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, (list, tuple)):
        return []
    return [dict(r) for r in rows if isinstance(r, dict)]


def _rd(player) -> dict:
    return player.data.setdefault("RoleData", {})


def _st(player, section: str) -> dict:
    return player.data.setdefault(section, {})


def _lazy(section: str, field: str, default=0):
    """Decorator helper factory kept simple: handlers read/write via _st."""
    return (section, field, default)


# ===========================================================================
# video rewards (_vedioMod)  18101-18103
# ===========================================================================
def action_18101(payload: dict, player, session, store) -> dict:
    """Video overview refresh; callback reads no response field."""
    return {}


def action_18102(payload: dict, player, session, store) -> dict:
    """_vedioMod 18102 -> A0_3.VedioRewardStatus (live RoleData state)."""
    return {"VedioRewardStatus": _as_int(_rd(player).get("VedioRewardStatus"))}


def action_18103(payload: dict, player, session, store) -> dict:
    """_vedioMod 18103 -> DayVedioTimes + VedioRewardStatus (RoleData)."""
    role = _rd(player)
    return {
        "DayVedioTimes": _as_int(role.get("DayVedioTimes")),
        "VedioRewardStatus": _as_int(role.get("VedioRewardStatus")),
    }


# ===========================================================================
# explore mystery / relic (_exploreMysteryMod)  18201-18242
# ===========================================================================
def action_18201(payload: dict, player, session, store) -> dict:
    """Enroll overview. ActivityCfg/HintData are server schedules; rank/score
    are live server-authoritative values -> zero/empty. EnrollRankChange=0
    keeps the client's `if EnrollRankChange ~= 0 then` branch safe."""
    return {
        "ActivityCfg": {},
        "EnrollRankChange": 0,
        "EnrollStatus": 0,
        "HintData": {},
        "MyEnrollRank": 0,
        "MyRank": 0,
        "MyScore": 0,
    }


def action_18202(payload: dict, player, session, store) -> dict:
    return {"MyEnrollRank": 0, "Status": 0}


def action_18203(payload: dict, player, session, store) -> dict:
    """Callback does RankData[1].Ranking -- list with one safe row."""
    return {"RankData": [{"Ranking": 0}]}


def action_18210(payload: dict, player, session, store) -> dict:
    """Explore board. GridCfg from original TcRelicGrid; RoleInfoData must be
    a map with Score/BindScore (client adds them). Other data is live state."""
    return {
        "BossProgress": 0,
        "CounsellorData": {},
        "GridCfg": _rows("TcRelicGrid"),
        "GridData": {},
        "RoleInfoData": {"Score": 0, "BindScore": 0},
    }


def action_18211(payload: dict, player, session, store) -> dict:
    """Callback reads no response field (request gridId/ministerId only)."""
    return {}


def action_18212(payload: dict, player, session, store) -> dict:
    return {"CounsellorInfo": {}, "FightCounsellorHp": 0, "FightCounsellorId": 0}


def action_18213(payload: dict, player, session, store) -> dict:
    return {
        "AnnouncementList": [], "BeKillData": {}, "BossProgress": 0,
        "CounsellorData": {}, "EnemyData": {}, "EventId": 0,
        "Hp": 0, "IsComplete": 0, "LastResumeTime": 0,
        "RoleInfoData": {}, "Status": 0, "Vitality": 0,
    }


def action_18214(payload: dict, player, session, store) -> dict:
    return {
        "AnnouncementList": [], "BeKillData": {}, "BossCount": 0,
        "BossProgress": 0, "CounsellorData": {}, "CurrentMembers": {},
        "EventData": {}, "IsComplete": 0, "RemainBox": 0,
        "RoleInfoData": {},
    }


def action_18215(payload: dict, player, session, store) -> dict:
    return {}


def action_18216(payload: dict, player, session, store) -> dict:
    return {
        "BeKillData": {}, "LastResumeTime": 0, "RoleInfoData": {},
        "Status": 0, "Vitality": 0,
    }


def action_18217(payload: dict, player, session, store) -> dict:
    return {"BindScore": 0, "Score": 0}


def action_18218(payload: dict, player, session, store) -> dict:
    return {
        "AnnouncementList": [], "BeKillData": {}, "BossProgress": 0,
        "CounsellorData": {}, "Hp": 0, "IsComplete": 0,
        "RoleInfoData": {}, "Status": 0, "awardList": [],
    }


def action_18219(payload: dict, player, session, store) -> dict:
    return {
        "AnnouncementList": [], "BeKillData": {}, "BossProgress": 0,
        "CounsellorData": {}, "EnemyData": {}, "EnemyHp": 0,
        "EventId": 0, "Hp": 0, "IsComplete": 0, "RoleInfoData": {},
        "Score": 0, "Status": 0, "addNum": 0, "awardList": [],
    }


def action_18220(payload: dict, player, session, store) -> dict:
    return {
        "Boss": {}, "BossList": [], "BossProgress": 0,
        "GridBattle": {}, "GridData": {},
    }


def action_18240(payload: dict, player, session, store) -> dict:
    return {"AnnouncementCount": 0, "AnnouncementList": [], "StartIndex": 0}


def action_18241(payload: dict, player, session, store) -> dict:
    return {"AnnouncementCount": 0, "AnnouncementList": [], "StartIndex": 0}


def action_18242(payload: dict, player, session, store) -> dict:
    return {"AnnouncementList": [], "TimeInterval": 0}


# ===========================================================================
# infinite war (_infiniteWarMod)  18301-18318
# ===========================================================================
def action_18301(payload: dict, player, session, store) -> dict:
    """StrongholdInfo needs an index-2 table (client calls next() on it);
    two Lua-safe numeric rows avoid a nil-table crash. Rest is live state."""
    return {
        "AnnouncementList": [],
        "EndTime": 0,
        "IsUnlockOneKey": 0,
        "StrongholdInfo": [[0, 0, 0, 0], [0, 0, 0, 0]],
        "TopRole": {},
    }


def action_18302(payload: dict, player, session, store) -> dict:
    return {"IsUnlockOneKey": 0, "RemainEnemy": 0}


def action_18303(payload: dict, player, session, store) -> dict:
    """Client writes tSpoils itself from config + request; server value is
    overwritten, so an empty list is the neutral safe shape."""
    return {"tSpoils": []}


def action_18304(payload: dict, player, session, store) -> dict:
    return {}


def action_18305(payload: dict, player, session, store) -> dict:
    return {}


def action_18316(payload: dict, player, session, store) -> dict:
    return {"GetAwardTimes": 0}


def action_18317(payload: dict, player, session, store) -> dict:
    return {"ClaimRecord": {}, "GetAwardTimes": 0}


def action_18318(payload: dict, player, session, store) -> dict:
    return {"ClaimRecord": {}, "Status": 0}


# ===========================================================================
# cross war (_crossWarMod)  18401-18439
# ===========================================================================
def action_18401(payload: dict, player, session, store) -> dict:
    return {"ReportList": [], "RoleInfo": {}, "TeamList": []}


def action_18402(payload: dict, player, session, store) -> dict:
    return {
        "BagId": 0, "BloodAfter": 0, "BloodBefore": 0, "Defend": 0,
        "HpPercent": 0, "Hurt": 0, "MinisterId": 0, "SkinId": 0,
        "Status": 0, "UpdateVitalityTime": 0, "Vitality": 0,
    }


def action_18403(payload: dict, player, session, store) -> dict:
    return {"Vitality": 0}


def action_18404(payload: dict, player, session, store) -> dict:
    return {"HasReachAward": 0, "ReportList": [], "RoleInfo": {}}


def action_18405(payload: dict, player, session, store) -> dict:
    return {"RoleInfo": {}, "Status": 0, "TeamInfo": {}}


def action_18406(payload: dict, player, session, store) -> dict:
    return {"RoleInfo": {}, "Status": 0}


def action_18407(payload: dict, player, session, store) -> dict:
    return {"CheerTimeCd": 0, "RemainCheerTimes": 0, "ServerList": []}


def action_18408(payload: dict, player, session, store) -> dict:
    return {"AllianceGuid": "", "Status": 0, "TroopId": 0, "XpxInfoList": []}


def action_18409(payload: dict, player, session, store) -> dict:
    return {"CheerTimeCd": 0, "RemainCheerTimes": 0, "ServerList": []}


def action_18410(payload: dict, player, session, store) -> dict:
    return {
        "GetGateStatus": 0, "IsGetPersonalDamageAward": 0,
        "Order": 0, "PersonalRank": 0, "sortId": 0, "TroopRank": 0,
    }


def action_18411(payload: dict, player, session, store) -> dict:
    return {"RewardCount": 0}


def action_18412(payload: dict, player, session, store) -> dict:
    """War overview. Stage/times/flags are the live server schedule;
    per-alliance win info and results are other-player data."""
    return {
        "AllAllianceNoOne": 0, "AllianceNoOne": 0, "AllianceWinInfo": {},
        "CanJoin": 0, "EndTime": 0, "FeastEndTime": 0, "HasFeast": 0,
        "IsJoin": 0, "KingWin": 0, "ResultInfo": {},
        "ServerListStatus": {}, "Stage": 0, "StartTime": 0,
    }


def action_18413(payload: dict, player, session, store) -> dict:
    return {"AttackName": ""}


def action_18414(payload: dict, player, session, store) -> dict:
    return {
        "AttackRank": 0, "AttackReport": [], "DefendList": [],
        "DefendReport": [], "MyRank": 0, "TroopInfo": {},
    }


def action_18416(payload: dict, player, session, store) -> dict:
    return {"FastAttackInfo": {}, "Status": 0}


def action_18417(payload: dict, player, session, store) -> dict:
    return {}


def action_18418(payload: dict, player, session, store) -> dict:
    return {"Rank": 0, "ReachRecordInfo": {}}


def action_18420(payload: dict, player, session, store) -> dict:
    return {"WinnerList": []}


def action_18421(payload: dict, player, session, store) -> dict:
    return {"FeastEndTime": 0}


def action_18422(payload: dict, player, session, store) -> dict:
    return {"Status": 0}


def action_18423(payload: dict, player, session, store) -> dict:
    return {"FeastEndTime": 0, "SubDinnerId": 0}


def action_18424(payload: dict, player, session, store) -> dict:
    return {"OpenConditionAdd": 0, "OpenStatus": 0}


def action_18425(payload: dict, player, session, store) -> dict:
    return {"AwardClaimedRecord": {}}


def action_18426(payload: dict, player, session, store) -> dict:
    return {"OpenConditionAdd": 0, "OpenStatus": 0, "SubDinnerId": 0}


def action_18427(payload: dict, player, session, store) -> dict:
    return {"IsClaimed": 0}


def action_18431(payload: dict, player, session, store) -> dict:
    return {
        "Addition": 0, "Attack": 0, "Blood": 0, "Defend": 0,
        "GateId": 0, "MemberList": [], "TroopIndex": 0,
    }


def action_18432(payload: dict, player, session, store) -> dict:
    return {}


def action_18433(payload: dict, player, session, store) -> dict:
    return {}


def action_18434(payload: dict, player, session, store) -> dict:
    return {"IsSelect": 0, "TroopNum": 0}


def action_18435(payload: dict, player, session, store) -> dict:
    return {"MyAllianceInfo": {}, "ServerList": []}


def action_18436(payload: dict, player, session, store) -> dict:
    return {"AllianceList": []}


def action_18437(payload: dict, player, session, store) -> dict:
    return {"AllianceAchievement": {}, "PersonalAchievement": {}}


def action_18438(payload: dict, player, session, store) -> dict:
    return {"AllianceAchievement": {}, "PersonalAchievement": {}}


def action_18439(payload: dict, player, session, store) -> dict:
    return {"ReportList": []}


# ===========================================================================
# king boss (_kingBossMod)  18501-18506
# ===========================================================================
def action_18501(payload: dict, player, session, store) -> dict:
    """ArmyList/Rank/Score/AnnouncementList are live boss-battle state."""
    return {
        "ActivityCfg": {}, "AnnouncementList": [], "ArmyList": [],
        "Rank": 0, "Score": 0,
    }


def action_18502(payload: dict, player, session, store) -> dict:
    return {
        "AnnouncementList": [], "BuffEndTime": 0,
        "DefaultPreAwardList": [], "PreAwardList": [], "Rank": 0,
        "RemainPauseTime": 0, "Score": 0,
    }


def action_18503(payload: dict, player, session, store) -> dict:
    return {
        "BuffEndTime": 0, "BuffType": 0,
        "PreAwardList": [], "Rank": 0,
    }


def action_18504(payload: dict, player, session, store) -> dict:
    return {"RemainPauseTime": 0}


def action_18505(payload: dict, player, session, store) -> dict:
    return {"BuffEndTime": 0}


def action_18506(payload: dict, player, session, store) -> dict:
    return {"CurBlood": 0, "KillNickname": ""}


# ===========================================================================
# ramadan (_ramadanMod)  18701-18729
# ===========================================================================
def action_18701(payload: dict, player, session, store) -> dict:
    """MenuTyp defaults to 0 in the client, HasFreeTickets/crit flags are live
    state; IngredientsList/ActivityCfg are server schedule data."""
    return {
        "ActivityCfg": {}, "HasFreeTickets": 0, "IngredientsList": [],
        "IsFarmCrit": 0, "IsMorningHallCrit": 0, "MenuTyp": 0,
    }


def action_18702(payload: dict, player, session, store) -> dict:
    return {
        "HasPlayCommonRound": 0, "IngredientsList": [], "RaiseRank": 0,
        "RaiseScore": 0, "RoundVegetable": {}, "Type": 0,
    }


def action_18703(payload: dict, player, session, store) -> dict:
    return {"RaiseRank": 0, "RemainList": []}


def action_18704(payload: dict, player, session, store) -> dict:
    return {"RoundVegetable": {}, "Type": 0, "VegetableCfg": {}}


def action_18705(payload: dict, player, session, store) -> dict:
    return {
        "HasPlayCommonRound": 0, "IngredientsList": [], "RaiseRank": 0,
        "RaiseScore": 0, "RoundAnimal": {}, "Type": 0,
    }


def action_18706(payload: dict, player, session, store) -> dict:
    return {
        "AnimalType": 0, "RaiseRank": 0, "RemainList": [], "RoundAnimal": {},
    }


def action_18707(payload: dict, player, session, store) -> dict:
    return {"AnimalType": 0, "RoundAnimal": {}, "Type": 0}


def action_18708(payload: dict, player, session, store) -> dict:
    return {
        "ExpiredExchange": [], "IngredientsList": [],
        "MarketList": [], "SuccessExchange": [],
    }


def action_18709(payload: dict, player, session, store) -> dict:
    return {"InfoList": []}


def action_18710(payload: dict, player, session, store) -> dict:
    return {}


def action_18711(payload: dict, player, session, store) -> dict:
    return {}


def action_18712(payload: dict, player, session, store) -> dict:
    return {"Num": 0, "Status": 0}


def action_18713(payload: dict, player, session, store) -> dict:
    return {}


def action_18714(payload: dict, player, session, store) -> dict:
    return {
        "IngredientsList": [], "PopRank": 0, "PopScore": 0,
        "ProgressNum": 0, "RemainEatTimes": 0,
    }


def action_18715(payload: dict, player, session, store) -> dict:
    return {"ProgressNum": 0}


def action_18716(payload: dict, player, session, store) -> dict:
    return {"PopScore": 0, "ProgressNum": 0, "RemainEatTimes": 0}


def action_18717(payload: dict, player, session, store) -> dict:
    return {}


def action_18718(payload: dict, player, session, store) -> dict:
    return {}


def action_18719(payload: dict, player, session, store) -> dict:
    return {"IngredientsList": []}


def action_18720(payload: dict, player, session, store) -> dict:
    return {
        "ExpiredExchange": [], "IngredientsList": [],
        "MenuCfg": _rows("TcRamadanMenuCfg"), "MenuTyp": 0,
        "SuccessExchange": [],
    }


def action_18721(payload: dict, player, session, store) -> dict:
    return {"IngredientsList": []}


def action_18722(payload: dict, player, session, store) -> dict:
    return {
        "IngredientsList": [], "MenuCfg": _rows("TcRamadanMenuCfg"),
        "Status": 0,
    }


def action_18723(payload: dict, player, session, store) -> dict:
    return {
        "DishList": [], "EatenData": {}, "IsHaveCat": 0,
        "PopRank": 0, "PopScore": 0, "RemainBuyEatTimes": 0,
        "RemainTentEatTimes": 0,
    }


def action_18724(payload: dict, player, session, store) -> dict:
    return {
        "IsHaveCat": 0, "PopRank": 0, "PopScore": 0,
        "RemainTimes": 0, "Status": 0,
    }


def action_18725(payload: dict, player, session, store) -> dict:
    return {"PopRank": 0, "PopScore": 0, "Status": 0}


def action_18726(payload: dict, player, session, store) -> dict:
    return {}


def action_18727(payload: dict, player, session, store) -> dict:
    return {"MyDishList": []}


def action_18728(payload: dict, player, session, store) -> dict:
    return {}


def action_18729(payload: dict, player, session, store) -> dict:
    return {"RemainTentEatTimes": 0}


# ===========================================================================
# recall / player comeback (_recallMod)  18801-18809
# ===========================================================================
def action_18801(payload: dict, player, session, store) -> dict:
    """Callback extracts 5 fields via a pairs() name list (harness shows []).
    RecallCode/RecallPlayer are other-player/server data -> empty string/list;
    RewardCfg uses original TcPlayerComebackTask award tiers."""
    return {
        "RecallCode": "",
        "RecallPlayer": {},
        "CanClaimRecalledReward": 0,
        "CanClaimAcceptedReward": 0,
        "RewardCfg": _rows("TcPlayerComebackTask"),
    }


def action_18802(payload: dict, player, session, store) -> dict:
    return {"RecallPlayer": {}, "CanClaimRecalledReward": 0}


def action_18803(payload: dict, player, session, store) -> dict:
    return {"CanClaimAcceptedReward": 0}


def action_18804(payload: dict, player, session, store) -> dict:
    """Callback extracts 5 fields via pairs() name list (harness shows [])."""
    return {
        "StartTime": 0, "EndTime": 0, "LightData": [],
        "ActivityDay": 0, "DisappearTime": 0,
    }


def action_18805(payload: dict, player, session, store) -> dict:
    return {"LightData": []}


def action_18806(payload: dict, player, session, store) -> dict:
    """Callback extracts 6 fields via pairs() name list (harness shows []).
    TaskCfg/award tiers from original TcPlayerComebackTask."""
    return {
        "TaskCfg": _rows("TcPlayerComebackTask"), "Countdown": 0,
        "TaskCompleteAward": [], "DartsNum": 0,
        "HasRefreshTask": 0, "CurTaskDay": 0,
    }


def action_18807(payload: dict, player, session, store) -> dict:
    return {
        "TaskCfg": _rows("TcPlayerComebackTask"), "DartsNum": 0,
        "Countdown": 0, "CurTaskDay": 0,
    }


def action_18808(payload: dict, player, session, store) -> dict:
    state = _st(player, "RecallExtra")
    return {
        "DartsNum": _as_int(state.get("DartsNum")),
        "UseDartNum": _as_int(payload.get("UseDartNum") or payload.get("useDartNum")),
    }


def action_18809(payload: dict, player, session, store) -> dict:
    state = _st(player, "RecallExtra")
    use = _as_int(payload.get("UseDartNum") or payload.get("useDartNum"))
    darts = max(0, _as_int(state.get("DartsNum")) - use)
    state["DartsNum"] = darts
    store.save(player)
    return {"DartsNum": darts, "UseDartNum": use}


# ---------------------------------------------------------------------------
# registry (imported by gos.handlers._merge_cluster_handlers)
# ---------------------------------------------------------------------------
WAR_EVENTS_HANDLERS = {
    "18101": action_18101, "18102": action_18102, "18103": action_18103,
    "18201": action_18201, "18202": action_18202, "18203": action_18203,
    "18210": action_18210, "18211": action_18211, "18212": action_18212,
    "18213": action_18213, "18214": action_18214, "18215": action_18215,
    "18216": action_18216, "18217": action_18217, "18218": action_18218,
    "18219": action_18219, "18220": action_18220, "18240": action_18240,
    "18241": action_18241, "18242": action_18242,
    "18301": action_18301, "18302": action_18302, "18303": action_18303,
    "18304": action_18304, "18305": action_18305, "18316": action_18316,
    "18317": action_18317, "18318": action_18318,
    "18401": action_18401, "18402": action_18402, "18403": action_18403,
    "18404": action_18404, "18405": action_18405, "18406": action_18406,
    "18407": action_18407, "18408": action_18408, "18409": action_18409,
    "18410": action_18410, "18411": action_18411, "18412": action_18412,
    "18413": action_18413, "18414": action_18414, "18416": action_18416,
    "18417": action_18417, "18418": action_18418, "18420": action_18420,
    "18421": action_18421, "18422": action_18422, "18423": action_18423,
    "18424": action_18424, "18425": action_18425, "18426": action_18426,
    "18427": action_18427, "18431": action_18431, "18432": action_18432,
    "18433": action_18433, "18434": action_18434, "18435": action_18435,
    "18436": action_18436, "18437": action_18437, "18438": action_18438,
    "18439": action_18439,
    "18501": action_18501, "18502": action_18502, "18503": action_18503,
    "18504": action_18504, "18505": action_18505, "18506": action_18506,
    "18701": action_18701, "18702": action_18702, "18703": action_18703,
    "18704": action_18704, "18705": action_18705, "18706": action_18706,
    "18707": action_18707, "18708": action_18708, "18709": action_18709,
    "18710": action_18710, "18711": action_18711, "18712": action_18712,
    "18713": action_18713, "18714": action_18714, "18715": action_18715,
    "18716": action_18716, "18717": action_18717, "18718": action_18718,
    "18719": action_18719, "18720": action_18720, "18721": action_18721,
    "18722": action_18722, "18723": action_18723, "18724": action_18724,
    "18725": action_18725, "18726": action_18726, "18727": action_18727,
    "18728": action_18728, "18729": action_18729,
    "18801": action_18801, "18802": action_18802, "18803": action_18803,
    "18804": action_18804, "18805": action_18805, "18806": action_18806,
    "18807": action_18807, "18808": action_18808, "18809": action_18809,
}
