"""Regression tests for the misc-event clusters.

* Actions: 13001, 13051-13064, 13101-13105, 13200-13209, 14301
  (13301-13305 are already covered by test_appearance.py / gos/handlers.py).
* Every response field is checked against the list recovered by
  ``tools/lua_shape_harness/extract_fields.py`` from the decompiled callbacks.
* The two config-backed grants (13102 invitation stage award, 13201 holiday
  login check-in) are checked against ``TcFBInvitationAward`` /
  ``TcCheckInAward``; no other reward amount is fabricated.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import v2configs
from gos.defaults import default_player
from gos.handlers_misc_events import MISC_EVENTS_HANDLERS
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="misceventstest", data=default_player("misceventstest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def _stub():
    return SimpleNamespace(save=lambda p: None), None


# Fields extracted from the decompiled response callbacks.  Extra keys are
# allowed (e.g. 13102 echoes the stage) but every listed field must exist with
# a Lua-safe type.
FIELDS = {
    "13001": {"Stat", "RemainList"},
    "13051": set(),
    "13052": {"FoodNum"},
    "13053": {"ActivityCfg", "CurrentAreaId", "HintData", "IsHaveCreateTeam",
              "KillBossNum", "KillPirateNum", "MyScore", "MyTeam", "Overlord",
              "QuitTime", "UnOpenBoxNum"},
    "13054": {"CurrentAreaId", "CurrentEvent", "FoodNum", "HintData",
              "IsObservationFull", "LastRecoveryTime", "MyScore", "ObservationNum"},
    "13055": {"CurrentEvent", "FoodNum", "HintData", "LastRecoveryTime",
              "ObservationNum", "UnlockAreaAward"},
    "13056": {"FoodNum"},
    "13057": {"HintData", "MyScore", "ObservationList"},
    "13058": {"CurrentBlood", "HintData", "LastAttackTime", "MyScore", "Status"},
    "13059": {"HintData"},
    "13060": {"CurrentEvent", "HintData", "MyScore"},
    "13061": {"HintData"},
    "13063": {"HintData", "LogList"},
    "13064": {"HintData"},
    "13101": {"InvitedFriends", "InvitedStage", "IsFirst", "IsInvited",
              "Point", "RewardData"},
    "13102": set(),
    "13103": {"InvitedFriends", "InvitedStage"},
    "13104": set(),
    "13105": {"IsFirst"},
    "13200": {"LoginActivity"},
    "13201": {"HistoricalReward", "IsAllComplete", "NextCheckInTime"},
    "13202": {"TurntableLottery"},
    "13203": {"FreeTimes", "LogList", "LotteryList", "NextFreeTime",
              "NowTimes", "RemainTimes"},
    "13204": {"NowGrade", "NowTimes", "UpgradeTimes"},
    "13206": {"MarketingGroupBuyActivityCfg", "IsChangeCfg", "RemainTimes",
              "NowBuyNum", "NextStageNum", "IsStageReward"},
    "13207": set(),
    "13208": {"GiftPackage"},
    "13209": {"NextStageNum", "NowBuyNum"},
    "14301": set(),
}


def main() -> None:
    store, session = _stub()

    # ---- registry ---------------------------------------------------------
    expected = (
        {"13001"} | {str(a) for a in range(13051, 13065) if a != 13062}
        | {str(a) for a in range(13101, 13106)}
        | {str(a) for a in range(13200, 13205)}
        | {str(a) for a in range(13206, 13210)} | {"14301"}
    )
    check("registry covers exactly the 29 new actions",
          set(MISC_EVENTS_HANDLERS) == expected,
          (sorted(set(MISC_EVENTS_HANDLERS) ^ expected)))
    check("13301-13305 not duplicated here (already in handlers.py)",
          not ({str(a) for a in range(13301, 13306)} & set(MISC_EVENTS_HANDLERS)), None)

    # ---- every response has the extracted fields, all Lua-safe ------------
    for aid in sorted(expected):
        p = _player()
        try:
            d = MISC_EVENTS_HANDLERS[aid]({}, p, session, store)
        except Exception as exc:  # noqa: BLE001 - test report needs the detail
            check(f"{aid} handler runs", False, repr(exc))
            continue
        missing = FIELDS[aid] - set(d)
        check(f"{aid} has all callback fields", not missing, missing)
        check(f"{aid} response is a Lua-safe map", isinstance(d, dict), type(d))

    # ---- 13001 redeem code ------------------------------------------------
    d = MISC_EVENTS_HANDLERS["13001"]({"RedeemCode": "anything"}, _player(), session, store)
    check("13001 returns invalid Stat and empty RemainList",
          d["Stat"] == 0 and isinstance(d["RemainList"], list) and not d["RemainList"], d)

    # ---- 13053 config-derived Sea King state ------------------------------
    area_rows = [r for r in v2configs.load("TcSeaKingAreaInfoCfg") if isinstance(r, dict)]
    p = _player()
    d = MISC_EVENTS_HANDLERS["13053"]({}, p, session, store)
    unlock = [int(r["RequireNum"]) for r in area_rows]
    check("13053 UnlockRequireNum sourced from TcSeaKingAreaInfoCfg",
          d["ActivityCfg"]["UnlockRequireNum"] == unlock, (d["ActivityCfg"], unlock))
    check("13053 KillBossNum is a 5-entry per-area list",
          isinstance(d["KillBossNum"], list) and len(d["KillBossNum"]) == len(area_rows)
          and isinstance(d["MyTeam"], dict) and isinstance(d["Overlord"], dict), d)

    # ---- 13101-13105 invitation (state + config award) --------------------
    p = _player()
    d = MISC_EVENTS_HANDLERS["13104"]({"InvitationCode": "ABC"}, p, session, store)
    d = MISC_EVENTS_HANDLERS["13101"]({}, p, session, store)
    check("13104 sets IsInvited and 13101 reflects it", d["IsInvited"] == 1, d)
    check("13101 RewardData/InvitedFriends are Lua-safe containers",
          isinstance(d["RewardData"], dict) and isinstance(d["InvitedFriends"], list), d)
    d = MISC_EVENTS_HANDLERS["13105"]({}, p, session, store)
    check("13105 IsFirst is an int", isinstance(d["IsFirst"], int), d)

    inv_rows = [r for r in v2configs.load("TcFBInvitationAward") if isinstance(r, dict)]
    stage = 2
    row = next(r for r in inv_rows
               if int(r["AwardType"]) == 2 and int(r["Stage"]) == stage)
    gold_gain = sum(int(e[1]) for e in row["AwardItem"] if int(e[0]) == 1)
    gold_before = int(p.data["RoleData"].get("Gold") or 0)
    d = MISC_EVENTS_HANDLERS["13102"]({"AwardStage": stage}, p, session, store)
    gold_after = int(p.data["RoleData"].get("Gold") or 0)
    check("13102 grants only TcFBInvitationAward AwardItem (Gold delta)",
          gold_after - gold_before == gold_gain,
          (gold_after - gold_before, gold_gain, row["AwardItem"]))
    check("13102 attaches RemainList and marks stage claimed",
          "RemainList" in d and p.data["InvitationExtra"]["RewardData"][str(stage)] == 1, d)

    # ---- 13200-13209 holiday marketing ------------------------------------
    p = _player()
    d = MISC_EVENTS_HANDLERS["13200"]({}, p, session, store)
    check("13200 LoginActivity is a Lua-safe map", isinstance(d["LoginActivity"], dict), d)

    rows = [r for r in v2configs.load("TcCheckInAward") if isinstance(r, dict)]
    day1 = next(r for r in rows if int(r["AwardId"]) == 1)
    gold_gain = sum(int(e[1]) for e in day1["AwardList"] if int(e[0]) == 1)
    gold_before = int(p.data["RoleData"].get("Gold") or 0)
    d = MISC_EVENTS_HANDLERS["13201"]({}, p, session, store)
    gold_after = int(p.data["RoleData"].get("Gold") or 0)
    check("13201 grants TcCheckInAward award for day 1",
          gold_after - gold_before == gold_gain,
          (gold_after - gold_before, gold_gain, day1["AwardList"]))
    check("13201 records day and historical reward",
          p.data["HolidayMarketing"]["LoginActivity"]["CheckInDay"] == 1
          and 1 in d["HistoricalReward"], d)

    d = MISC_EVENTS_HANDLERS["13202"]({}, p, session, store)
    check("13202 TurntableLottery has scalar counters and empty config maps",
          all(k in d["TurntableLottery"] for k in
              ("FreeTimes", "RemainTimes", "NextFreeTime", "NowTimes",
               "AwardCfg", "TurntableGradeCfg")), d)

    p.data["HolidayMarketing"]["TurntableLottery"]["FreeTimes"] = 1
    p.data["HolidayMarketing"]["TurntableLottery"]["RemainTimes"] = 3
    d = MISC_EVENTS_HANDLERS["13203"]({}, p, session, store)
    check("13203 decrements free/remain and increments NowTimes",
          d["FreeTimes"] == 0 and d["RemainTimes"] == 2 and d["NowTimes"] == 1, d)
    check("13203 logs are empty Lua-safe lists",
          isinstance(d["LogList"], list) and isinstance(d["LotteryList"], list), d)
    d = MISC_EVENTS_HANDLERS["13204"]({}, p, session, store)
    check("13204 bumps NowGrade/UpgradeTimes",
          d["NowGrade"] == 1 and d["UpgradeTimes"] == 1, d)

    d = MISC_EVENTS_HANDLERS["13206"]({"PackageId": 7, "CurRemainNum": 2}, p, session, store)
    check("13206 returns buy fields and empty group-buy config",
          d["NowBuyNum"] == 1 and d["RemainTimes"] == 1
          and isinstance(d["MarketingGroupBuyActivityCfg"], list), d)
    d = MISC_EVENTS_HANDLERS["13209"]({"PackageId": 7}, p, session, store)
    check("13209 echoes NowBuyNum from 13206 state", d["NowBuyNum"] == 1, d)
    d = MISC_EVENTS_HANDLERS["13208"]({}, p, session, store)
    check("13208 GiftPackage is an empty list", d["GiftPackage"] == [], d)

    # ---- 14301 ------------------------------------------------------------
    check("14301 is a Lua-safe empty map",
          MISC_EVENTS_HANDLERS["14301"]({"UId": "1"}, p, session, store) == {}, None)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
