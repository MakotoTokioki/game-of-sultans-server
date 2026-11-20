"""Regression tests for the alliance war cluster (actions 11651-11676).

* Response shapes are checked against the field lists recovered by
  ``tools/lua_shape_harness/extract_fields.py`` plus nested fields read by
  ``scene/allianceWarScene*.lua`` -- see ``gos/handlers_alliance_war.py``.
* All authored numbers come from bundled v2 configs (``TcWarAwardCfg``,
  ``TcAllianceWarBloodAddition``, ...).  Multi-player / server-authoritative
  data (opponent alliances, ranks, reports, war clock) must stay Lua-safe and
  empty/zero -- no alliance, player or rank is fabricated.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import v2configs
from gos.defaults import default_player
from gos.handlers_alliance_war import (
    ALLIANCE_WAR_HANDLERS,
    _award_hint,
    _stage_info,
)
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="alliancewartest",
                  data=default_player("alliancewartest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


# Fields extracted from the client response callbacks (plus nested scene
# fields for the actions whose callback only forwards the response).
EXPECTED = {
    "11651": {"AllianceJoinNum", "AttackInfo", "AwardHint", "DefendInfo",
              "Group", "Id", "IsJoin", "IsSignIn", "IsSuspend", "Stage",
              "WarUTC"},
    "11652": {"RemainList"},
    "11653": {"ContributionReports", "NowResourceAddition", "NowResourceNum",
              "NowStage", "TotalAddition"},
    "11654": {"RemainList"},
    "11655": {"BattleArrayList", "ContributionReports", "DiamondAddition",
              "DiamondStage", "ResourceAddition", "ResourceStage",
              "TotalDiamond", "TotalFood", "WarStatus"},
    "11656": {"BetInformation", "GuessStage", "LeftGuessInfo"},
    "11657": {"GuessStage"},
    "11658": {"Score", "JinnangStore", "PersonBetStore", "ExchangeLimit",
              "ExchangeTime", "TimeLimit"},
    "11659": {"Score"},
    "11660": {"ExchangeTime", "JinnangStore"},
    "11661": {"AttackRecord", "CounsellorData", "GroupData", "InvasionRecord"},
    "11662": {"GroupRank", "GroupPoint", "GroupReceive", "DamageRank",
              "DamageScore", "DamageReceive", "GlobalDiamondRank",
              "GlobalDiamondNum", "GetDiamondAward", "GlobalFoodRank",
              "GlobalFoodNum", "GetFoodAward", "Damage", "BloodPer",
              "GroupRankList", "DamageRankList", "GlobalDiamondRankList",
              "GlobalFoodRankList"},
    "11663": {"ActionNum"},
    "11664": {"Addition", "AttackData", "Blood", "Damage", "DefendData",
              "DefendList", "Rank", "TotalBlood"},
    "11665": {"BeginBloodPer", "Damage", "DefendBufId", "DefendCounsellorId",
              "DefendRole", "EndBloodPer", "IsBreak", "ShouldRefresh",
              "SkinId", "UseBuf"},
    "11666": {"RecordList"},
    "11667": {"RemainList"},
    "11668": {"RemainList"},
    "11669": {"GroupRank", "GroupPoint", "GroupReceive", "DamageRank",
              "DamageScore", "DamageReceive", "GlobalDiamondRank",
              "GlobalDiamondNum", "GetDiamondAward", "GlobalFoodRank",
              "GlobalFoodNum", "GetFoodAward", "Damage", "BloodPer",
              "GroupRankList", "DamageRankList", "GlobalDiamondRankList",
              "GlobalFoodRankList"},
    "11670": {"DiamondRankList", "FoodRankList", "DiamondRank", "DiamondNum",
              "FoodRank", "FoodNum"},
    "11671": {"RemainList"},
    "11672": {"GroupList"},
    "11673": {"BattleArrayList", "WarStatus"},
    "11674": {"AttackInfo", "ShouldRefresh"},
    "11675": {"HistoryData"},
    "11676": {"WarStatus"},
}


def main() -> None:
    saved = []
    store = SimpleNamespace(save=lambda p: saved.append(p.uid))
    session = None

    # ---- registry --------------------------------------------------------
    expected_ids = {str(a) for a in range(11651, 11677)}
    check("ALLIANCE_WAR_HANDLERS covers exactly 11651-11676 (26)",
          set(ALLIANCE_WAR_HANDLERS) == expected_ids,
          sorted(ALLIANCE_WAR_HANDLERS))

    # ---- bundled configs parse and drive numbers -------------------------
    for name in ("TcWarAwardCfg", "TcAllianceWarBloodAddition",
                 "TcWarActionRestoreCfg", "TcCrossWarAllianceScoreRankCfg",
                 "TcCrossAllianceAwardByAllianceCfg",
                 "TcCrossAllianceAwardByBattleCfg",
                 "TcCrossAllianceBattlePositionAdditionCfg",
                 "TcBossRankAward", "TcAchievementAward", "TcActivityAward"):
        check(f"config {name} parses", len(v2configs.load(name)) > 0)

    award_rows = [r for r in v2configs.load("TcWarAwardCfg")
                  if isinstance(r, dict) and "Level" in r]
    hint = _award_hint()
    check("award hint row count matches TcWarAwardCfg",
          len(hint) == len(award_rows), (len(hint), len(award_rows)))
    first = award_rows[0]
    check("award hint values sourced from config",
          any(h["AddExp"] == int(first["AllianceExp"])
              and h["AddFortune"] == int(first["AllianceFortune"])
              and h["Level"] == int(first["Level"]) for h in hint), hint[:2])
    check("award hint cross-score unsourced -> 0",
          all(h["AddCrossScore"] == 0 for h in hint))

    stage_rows = sorted((r for r in v2configs.load("TcAllianceWarBloodAddition")
                         if isinstance(r, dict) and "Stage" in r),
                        key=lambda r: int(r["Stage"]))
    stage2 = int(stage_rows[1]["Stage"])
    info2 = _stage_info(stage2)
    expected_add2 = (int(stage_rows[1]["ResourceCumulativeAddition"])
                     - int(stage_rows[0]["ResourceCumulativeAddition"]))
    check("stage-2 addition = config cumulative difference",
          info2["Addition"] == max(0, expected_add2),
          (info2, expected_add2))

    # ---- every response has the exact client-read fields ------------------
    p = _player()
    responses = {}
    for aid in sorted(expected_ids):
        responses[aid] = ALLIANCE_WAR_HANDLERS[aid]({}, p, session, store)
        d = responses[aid]
        check(f"{aid} response is a dict", isinstance(d, dict), type(d))
        check(f"{aid} field set matches client callback",
              set(d) == EXPECTED[aid],
              (sorted(set(d) - EXPECTED[aid]), sorted(EXPECTED[aid] - set(d))))

    # ---- Lua-safe shapes / no fabricated multiplayer data -----------------
    d = responses["11651"]
    check("11651 WarUTC is a 2-int list", isinstance(d["WarUTC"], list)
          and len(d["WarUTC"]) == 2 and all(isinstance(x, int) for x in d["WarUTC"]))
    check("11651 opponent infos are empty dicts",
          d["AttackInfo"] == {} and d["DefendInfo"] == {})
    check("11651 AwardHint entries have config-shaped keys",
          all(set(h) == {"Level", "Rank", "AddExp", "AddFortune",
                         "AddCrossScore"} and isinstance(h["Rank"], list)
              for h in d["AwardHint"]))

    check("11652/11654/11667/11668/11671 RemainList empty",
          all(responses[a]["RemainList"] == []
              for a in ("11652", "11654", "11667", "11668", "11671")))

    check("11653 contribution reports empty (no other players)",
          responses["11653"]["ContributionReports"] == [])

    d = responses["11655"]
    check("11655 BattleArrayList is an empty list",
          d["BattleArrayList"] == [] and d["ContributionReports"] == [])
    check("11655 totals are ints", all(isinstance(d[k], int) for k in
          ("DiamondAddition", "DiamondStage", "ResourceAddition",
           "ResourceStage", "TotalDiamond", "TotalFood", "WarStatus")))

    d = responses["11656"]
    check("11656 guess data Lua-safe",
          d["BetInformation"] == {} and d["LeftGuessInfo"] == {}
          and isinstance(d["GuessStage"], int))

    d = responses["11661"]
    check("11661 all record slices are empty lists",
          all(d[k] == [] for k in ("AttackRecord", "CounsellorData",
                                   "GroupData", "InvasionRecord")))

    d = responses["11662"]
    check("11662 rank lists empty, ranks zero, receive flags ints",
          all(d[k] == [] for k in ("GroupRankList", "DamageRankList",
                                   "GlobalDiamondRankList",
                                   "GlobalFoodRankList"))
          and all(d[k] == 0 for k in ("GroupRank", "DamageRank",
                                      "GlobalDiamondRank", "GlobalFoodRank",
                                      "Damage", "BloodPer"))
          and all(isinstance(d[k], int) for k in ("GroupReceive",
                                                  "DamageReceive",
                                                  "GetDiamondAward",
                                                  "GetFoodAward")))

    check("11663 ActionNum is an int", isinstance(responses["11663"]["ActionNum"], int))

    d = responses["11664"]
    check("11664 scalars zero + report lists empty",
          all(d[k] == 0 for k in ("Blood", "TotalBlood", "Addition",
                                  "Damage", "Rank"))
          and all(d[k] == [] for k in ("DefendList", "DefendData", "AttackData")))

    d = responses["11665"]
    check("11665 battle result Lua-safe defaults",
          d["DefendRole"] == "" and d["ShouldRefresh"] == 0
          and all(isinstance(d[k], int) for k in
                  ("BeginBloodPer", "EndBloodPer", "Damage", "DefendBufId",
                   "DefendCounsellorId", "SkinId", "UseBuf", "IsBreak")))

    check("11666/11672/11675 lists empty",
          responses["11666"]["RecordList"] == []
          and responses["11672"]["GroupList"] == []
          and responses["11675"]["HistoryData"] == [])

    d = responses["11670"]
    check("11670 contribution ranking empty/zero",
          d["DiamondRankList"] == [] and d["FoodRankList"] == []
          and all(d[k] == 0 for k in ("DiamondRank", "DiamondNum",
                                      "FoodRank", "FoodNum")))

    d = responses["11673"]
    check("11673 lineup dict has nested Lua-safe tables",
          isinstance(d["BattleArrayList"], dict)
          and all(d["BattleArrayList"][k] == {}
                  for k in ("BattleArry", "FightList", "ItemList", "SkinList")))
    check("11674 opponent attack info empty",
          responses["11674"]["AttackInfo"] == {}
          and responses["11674"]["ShouldRefresh"] == 0)
    check("11676 war status is 2", responses["11676"]["WarStatus"] == 2)

    # ---- request echo / lazy state ---------------------------------------
    p = _player()
    d = ALLIANCE_WAR_HANDLERS["11651"](
        {"Id": 7, "Stage": 3, "Group": 2}, p, session, store)
    check("11651 echoes war id/stage/group",
          (d["Id"], d["Stage"], d["Group"]) == (7, 3, 2), d)
    check("11651 state stored lazily under AllianceWar",
          p.data["AllianceWar"]["WarId"] == 7)
    check("11676 persists war status",
          ALLIANCE_WAR_HANDLERS["11676"]({"WarStatus": 2}, p, session, store)
          == {"WarStatus": 2} and p.data["AllianceWar"]["WarStatus"] == 2)
    d = ALLIANCE_WAR_HANDLERS["11657"]({"Stage": 5}, p, session, store)
    check("11657 guess stage echoed", d == {"GuessStage": 3}, d)

    # donation consumes the real resource and adds config-derived addition
    p = _player()
    before = int(p.data["RoleData"]["Food"])
    d = ALLIANCE_WAR_HANDLERS["11653"]({"ItemId": 2, "Num": 100,
                                        "Stage": stage2}, p, session, store)
    check("11653 consumes food", int(p.data["RoleData"]["Food"]) == before - 100,
          (before, p.data["RoleData"]["Food"]))
    check("11653 NowResourceNum reflects new total",
          d["NowResourceNum"] == int(p.data["RoleData"]["Food"]), d)
    check("11653 NowStage/NowResourceAddition config-derived",
          d["NowStage"] == stage2 and d["NowResourceAddition"] == info2["Addition"], d)
    check("11653 TotalAddition accumulates config addition",
          d["TotalAddition"] == info2["Addition"], d)
    check("handler saves player on mutation", saved)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok), "alliance war cluster checks failed"


if __name__ == "__main__":
    main()
