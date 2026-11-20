"""Regression tests for the late-game war/event cluster handlers.

Shapes are the union of `extract_fields.py ACTION` output and the pairs()
field-name lists that the harness cannot see (recall 18801/18804/18806/18807).
Config-derived values come from the original v2 tables bundled under
gos/v2configs_data/.  No invented numbers.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers, v2configs
from gos.defaults import default_player
from gos.handlers_war_events import WAR_EVENTS_HANDLERS
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="wareventtest", data=default_player("wareventtest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def _stub():
    return SimpleNamespace(save=lambda p: None), None


# exact top-level fields each callback reads
EXPECTED = {
    "18101": [], "18102": ["VedioRewardStatus"],
    "18103": ["DayVedioTimes", "VedioRewardStatus"],
    "18201": ["ActivityCfg", "EnrollRankChange", "EnrollStatus", "HintData", "MyEnrollRank", "MyRank", "MyScore"],
    "18202": ["MyEnrollRank", "Status"], "18203": ["RankData"],
    "18210": ["BossProgress", "CounsellorData", "GridCfg", "GridData", "RoleInfoData"],
    "18211": [], "18212": ["CounsellorInfo", "FightCounsellorHp", "FightCounsellorId"],
    "18213": ["AnnouncementList", "BeKillData", "BossProgress", "CounsellorData", "EnemyData", "EventId", "Hp", "IsComplete", "LastResumeTime", "RoleInfoData", "Status", "Vitality"],
    "18214": ["AnnouncementList", "BeKillData", "BossCount", "BossProgress", "CounsellorData", "CurrentMembers", "EventData", "IsComplete", "RemainBox", "RoleInfoData"],
    "18215": [], "18216": ["BeKillData", "LastResumeTime", "RoleInfoData", "Status", "Vitality"],
    "18217": ["BindScore", "Score"],
    "18218": ["AnnouncementList", "BeKillData", "BossProgress", "CounsellorData", "Hp", "IsComplete", "RoleInfoData", "Status", "awardList"],
    "18219": ["AnnouncementList", "BeKillData", "BossProgress", "CounsellorData", "EnemyData", "EnemyHp", "EventId", "Hp", "IsComplete", "RoleInfoData", "Score", "Status", "addNum", "awardList"],
    "18220": ["Boss", "BossList", "BossProgress", "GridBattle", "GridData"],
    "18240": ["AnnouncementCount", "AnnouncementList", "StartIndex"],
    "18241": ["AnnouncementCount", "AnnouncementList", "StartIndex"],
    "18242": ["AnnouncementList", "TimeInterval"],
    "18301": ["AnnouncementList", "EndTime", "IsUnlockOneKey", "StrongholdInfo", "TopRole"],
    "18302": ["IsUnlockOneKey", "RemainEnemy"], "18303": ["tSpoils"],
    "18304": [], "18305": [], "18316": ["GetAwardTimes"],
    "18317": ["ClaimRecord", "GetAwardTimes"], "18318": ["ClaimRecord", "Status"],
    "18401": ["ReportList", "RoleInfo", "TeamList"],
    "18402": ["BagId", "BloodAfter", "BloodBefore", "Defend", "HpPercent", "Hurt", "MinisterId", "SkinId", "Status", "UpdateVitalityTime", "Vitality"],
    "18403": ["Vitality"], "18404": ["HasReachAward", "ReportList", "RoleInfo"],
    "18405": ["RoleInfo", "Status", "TeamInfo"], "18406": ["RoleInfo", "Status"],
    "18407": ["CheerTimeCd", "RemainCheerTimes", "ServerList"],
    "18408": ["AllianceGuid", "Status", "TroopId", "XpxInfoList"],
    "18409": ["CheerTimeCd", "RemainCheerTimes", "ServerList"],
    "18410": ["GetGateStatus", "IsGetPersonalDamageAward", "Order", "PersonalRank", "sortId", "TroopRank"],
    "18411": ["RewardCount"],
    "18412": ["AllAllianceNoOne", "AllianceNoOne", "AllianceWinInfo", "CanJoin", "EndTime", "FeastEndTime", "HasFeast", "IsJoin", "KingWin", "ResultInfo", "ServerListStatus", "Stage", "StartTime"],
    "18413": ["AttackName"],
    "18414": ["AttackRank", "AttackReport", "DefendList", "DefendReport", "MyRank", "TroopInfo"],
    "18416": ["FastAttackInfo", "Status"], "18417": [],
    "18418": ["Rank", "ReachRecordInfo"], "18420": ["WinnerList"],
    "18421": ["FeastEndTime"], "18422": ["Status"],
    "18423": ["FeastEndTime", "SubDinnerId"],
    "18424": ["OpenConditionAdd", "OpenStatus"], "18425": ["AwardClaimedRecord"],
    "18426": ["OpenConditionAdd", "OpenStatus", "SubDinnerId"],
    "18427": ["IsClaimed"],
    "18431": ["Addition", "Attack", "Blood", "Defend", "GateId", "MemberList", "TroopIndex"],
    "18432": [], "18433": [], "18434": ["IsSelect", "TroopNum"],
    "18435": ["MyAllianceInfo", "ServerList"], "18436": ["AllianceList"],
    "18437": ["AllianceAchievement", "PersonalAchievement"],
    "18438": ["AllianceAchievement", "PersonalAchievement"], "18439": ["ReportList"],
    "18501": ["ActivityCfg", "AnnouncementList", "ArmyList", "Rank", "Score"],
    "18502": ["AnnouncementList", "BuffEndTime", "DefaultPreAwardList", "PreAwardList", "Rank", "RemainPauseTime", "Score"],
    "18503": ["BuffEndTime", "BuffType", "PreAwardList", "Rank"],
    "18504": ["RemainPauseTime"], "18505": ["BuffEndTime"],
    "18506": ["CurBlood", "KillNickname"],
    "18701": ["ActivityCfg", "HasFreeTickets", "IngredientsList", "IsFarmCrit", "IsMorningHallCrit", "MenuTyp"],
    "18702": ["HasPlayCommonRound", "IngredientsList", "RaiseRank", "RaiseScore", "RoundVegetable", "Type"],
    "18703": ["RaiseRank", "RemainList"], "18704": ["RoundVegetable", "Type", "VegetableCfg"],
    "18705": ["HasPlayCommonRound", "IngredientsList", "RaiseRank", "RaiseScore", "RoundAnimal", "Type"],
    "18706": ["AnimalType", "RaiseRank", "RemainList", "RoundAnimal"],
    "18707": ["AnimalType", "RoundAnimal", "Type"],
    "18708": ["ExpiredExchange", "IngredientsList", "MarketList", "SuccessExchange"],
    "18709": ["InfoList"], "18710": [], "18711": [], "18712": ["Num", "Status"],
    "18713": [], "18714": ["IngredientsList", "PopRank", "PopScore", "ProgressNum", "RemainEatTimes"],
    "18715": ["ProgressNum"], "18716": ["PopScore", "ProgressNum", "RemainEatTimes"],
    "18717": [], "18718": [], "18719": ["IngredientsList"],
    "18720": ["ExpiredExchange", "IngredientsList", "MenuCfg", "MenuTyp", "SuccessExchange"],
    "18721": ["IngredientsList"],
    "18722": ["IngredientsList", "MenuCfg", "Status"],
    "18723": ["DishList", "EatenData", "IsHaveCat", "PopRank", "PopScore", "RemainBuyEatTimes", "RemainTentEatTimes"],
    "18724": ["IsHaveCat", "PopRank", "PopScore", "RemainTimes", "Status"],
    "18725": ["PopRank", "PopScore", "Status"], "18726": [],
    "18727": ["MyDishList"], "18728": [], "18729": ["RemainTentEatTimes"],
    "18801": ["RecallCode", "RecallPlayer", "CanClaimRecalledReward", "CanClaimAcceptedReward", "RewardCfg"],
    "18802": ["RecallPlayer", "CanClaimRecalledReward"],
    "18803": ["CanClaimAcceptedReward"],
    "18804": ["StartTime", "EndTime", "LightData", "ActivityDay", "DisappearTime"],
    "18805": ["LightData"],
    "18806": ["TaskCfg", "Countdown", "TaskCompleteAward", "DartsNum", "HasRefreshTask", "CurTaskDay"],
    "18807": ["TaskCfg", "DartsNum", "Countdown", "CurTaskDay"],
    "18808": ["DartsNum", "UseDartNum"], "18809": ["DartsNum", "UseDartNum"],
}


def main() -> None:
    store, session = _stub()

    check("registry count == expected action count",
          len(WAR_EVENTS_HANDLERS) == 106, len(WAR_EVENTS_HANDLERS))
    check("registry keys == EXPECTED keys",
          set(WAR_EVENTS_HANDLERS) == set(EXPECTED),
          (set(EXPECTED) - set(WAR_EVENTS_HANDLERS), set(WAR_EVENTS_HANDLERS) - set(EXPECTED)))

    # every handler callable, returns dict, and carries its full field set
    for aid, fields in EXPECTED.items():
        fn = WAR_EVENTS_HANDLERS[aid]
        try:
            d = fn({}, _player(), session, store)
        except Exception as exc:  # pragma: no cover
            check(f"{aid} callable", False, repr(exc))
            continue
        missing = [f for f in fields if f not in d]
        check(f"{aid} returns dict with all callback fields",
              isinstance(d, dict) and not missing,
              f"type={type(d).__name__} missing={missing}")

    # config-derived Cfg fields
    relic_ids = {r["Id"] for r in v2configs.load("TcRelicGrid")}
    got = WAR_EVENTS_HANDLERS["18210"]({}, _player(), session, store)["GridCfg"]
    check("18210 GridCfg from TcRelicGrid",
          len(got) == len(relic_ids) and all(r["Id"] in relic_ids for r in got),
          f"rows={len(got)} expected={len(relic_ids)}")

    menu_ids = {r["Id"] for r in v2configs.load("TcRamadanMenuCfg")}
    for aid in ("18720", "18722"):
        got = WAR_EVENTS_HANDLERS[aid]({}, _player(), session, store)["MenuCfg"]
        check(f"{aid} MenuCfg from TcRamadanMenuCfg",
              len(got) == len(menu_ids) and all(r["Id"] in menu_ids for r in got),
              f"rows={len(got)}")
    for aid, field in (("18801", "RewardCfg"), ("18806", "TaskCfg"), ("18807", "TaskCfg")):
        got = WAR_EVENTS_HANDLERS[aid]({}, _player(), session, store)[field]
        expected_rows = len(v2configs.load("TcPlayerComebackTask"))
        check(f"{aid} {field} from TcPlayerComebackTask",
              len(got) == expected_rows and all("AwardItem" in r for r in got),
              f"rows={len(got)} expected={expected_rows}")

    # video fields read from RoleData (live state), Lua-safe ints
    p = _player()
    p.data["RoleData"]["DayVedioTimes"] = 3
    p.data["RoleData"]["VedioRewardStatus"] = 1
    d = WAR_EVENTS_HANDLERS["18103"]({}, p, session, store)
    check("18103 echoes RoleData video state",
          d["DayVedioTimes"] == 3 and d["VedioRewardStatus"] == 1, d)
    p2 = _player()
    d2 = WAR_EVENTS_HANDLERS["18103"]({}, p2, session, store)
    check("18103 video defaults are ints",
          d2["DayVedioTimes"] == 0 and d2["VedioRewardStatus"] == 0, d2)

    # Lua-safe nested shapes that the client indexes directly
    d = WAR_EVENTS_HANDLERS["18203"]({}, _player(), session, store)
    check("18203 RankData[1].Ranking safe",
          isinstance(d["RankData"], list) and "Ranking" in d["RankData"][0], d)
    d = WAR_EVENTS_HANDLERS["18301"]({}, _player(), session, store)
    check("18301 StrongholdInfo has index-2 table",
          isinstance(d["StrongholdInfo"], list) and len(d["StrongholdInfo"]) >= 2
          and isinstance(d["StrongholdInfo"][1], list), d)
    d = WAR_EVENTS_HANDLERS["18210"]({}, _player(), session, store)
    check("18210 RoleInfoData has Score/BindScore",
          "Score" in d["RoleInfoData"] and "BindScore" in d["RoleInfoData"], d)

    # lazy state + persistence on recall dart consume (18809)
    p = _player()
    p.data.setdefault("RecallExtra", {})["DartsNum"] = 5
    d = WAR_EVENTS_HANDLERS["18809"]({"UseDartNum": 2}, p, session, store)
    check("18809 consumes darts via lazy state",
          d["DartsNum"] == 3 and d["UseDartNum"] == 2
          and p.data["RecallExtra"]["DartsNum"] == 3, (d, p.data["RecallExtra"]))
    check("18808 reads lazy dart state",
          WAR_EVENTS_HANDLERS["18808"]({}, p, session, store)["DartsNum"] == 3, p.data["RecallExtra"])

    # no fabricated server-authoritative data
    d = WAR_EVENTS_HANDLERS["18401"]({}, _player(), session, store)
    check("18401 report/team lists empty",
          d["ReportList"] == [] and d["TeamList"] == [] and d["RoleInfo"] == {}, d)

    # auto-registration through handlers.dispatch
    check("cluster auto-merged into gos.handlers.HANDLERS",
          all(handlers.HANDLERS.get(aid) is fn for aid, fn in WAR_EVENTS_HANDLERS.items()))

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
