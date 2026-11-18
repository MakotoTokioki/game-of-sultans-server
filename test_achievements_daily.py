"""Regression tests for Achievements (11201/11202) and Quests (11301-11303).

Evidence:
  modules/_achieveMod.lua      11201/11202 callbacks, 27 config rows
  modules/_dailyTaskMod.lua    11301/11302/11303 callbacks
  configs/TcAchievementName.lua (27), TcAchievementAward.lua (1639)
  configs/TcDailyTask.lua (17), TcActivityAward.lua (5)

Run: python3 test_achievements_daily.py
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path
from types import SimpleNamespace

from gos import handlers, v2configs
from gos.defaults import default_player
from gos.store import Player

ok: list[bool] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="achievetest", data=default_player("achievetest"),
                  path=Path("/tmp/opencode/gos_test_players/achievetest.json"))


def _stub():
    return SimpleNamespace(save=lambda p: None), SimpleNamespace(jwt="jwt", user_id="achievetest")


def test_11201_all_ids_and_real_metrics() -> None:
    player = _player()
    store, session = _stub()
    rd = player.data["RoleData"]
    player.data["CheckInDay"] = 5
    rd["Lv"] = 3
    rd["Power"] = 12345
    player.data["RoleCounsellor"] = player.data["RoleCounsellor"][:2] + [{}, {}]
    d = handlers.action_achieve({}, player, session, store)
    entries = d["AchievementData"]
    expected_ids = [int(r["AchievementId"]) for r in v2configs.load("TcAchievementName")]
    check("11201 has all 27 config ids, once each",
          sorted(e["AchievementId"] for e in entries) == sorted(expected_ids)
          and len(entries) == 27, (len(entries), expected_ids[:3]))
    check("11201 entries have exactly the three copied keys",
          all(set(e) == {"AchievementId", "CurrentNum", "GetAwardStage"} for e in entries),
          entries[0])
    by_id = {e["AchievementId"]: e for e in entries}
    check("11201 metrics are real (sign-ins, Lv, Power, viziers)",
          by_id[1]["CurrentNum"] == 5 and by_id[2]["CurrentNum"] == 3
          and by_id[3]["CurrentNum"] == 12345 and by_id[4]["CurrentNum"] == 4,
          [by_id[i] for i in (1, 2, 3, 4)])
    check("11201 all stages default to 0",
          all(e["GetAwardStage"] == 0 for e in entries), entries[:2])
    check("11201 unknown metrics are 0, never invented",
          all(by_id[i]["CurrentNum"] == 0 for i in (11, 12, 13, 16, 18, 19, 20, 21, 22, 23, 25, 26, 27)),
          {i: by_id[i]["CurrentNum"] for i in (11, 12, 13)})


def test_11202_claim_rules() -> None:
    player = _player()
    store, session = _stub()
    # Achievement 1 (sign-ins) stage 1 needs 1; fresh account has CheckInDay 1.
    d = handlers.action_achieve_claim({"achievementId": 1, "getAwardStage": 1},
                                      player, session, store)
    check("11202 reachable stage succeeds", "_fail" not in d, d.get("_fail"))
    check("11202 stage persisted", player.data["AchievementStages"]["1"] == 1,
          player.data["AchievementStages"])
    check("11202 grants only config AwardItem ([102,1] [104,1])",
          all(any(r["ItemId"] == iid and r["Num"] == num for r in d["RemainList"])
              for iid, num in ((102, 1), (104, 1))), d["RemainList"])
    d2 = handlers.action_achieve_claim({"achievementId": 1, "getAwardStage": 1},
                                       player, session, store)
    check("11202 already-claimed stage refused", "_fail" in d2, d2)
    # Achievement 2 stage 1 needs Lv 2; fresh Lv is 1.
    bad = handlers.action_achieve_claim({"achievementId": 2, "getAwardStage": 1},
                                        player, session, store)
    check("11202 unreachable stage refused", "_fail" in bad, bad)
    check("rejected claim grants nothing", "RemainList" not in bad and bad.get("_fail"), bad)
    bad_stage = handlers.action_achieve_claim({"achievementId": 1, "getAwardStage": 9},
                                              player, session, store)
    check("11202 skips-ahead stage refused", "_fail" in bad_stage, bad_stage)


def test_11301_shape() -> None:
    player = _player()
    store, session = _stub()
    d = handlers.action_daily_task({}, player, session, store)
    check("11301 has all 17 tasks", len(d["DailyTaskData"]) == 17, len(d["DailyTaskData"]))
    check("11301 has all 5 activity awards", len(d["ActivityAwardData"]) == 5,
          len(d["ActivityAwardData"]))
    check("11301 task entries are Lua-safe dicts",
          all(set(t) == {"TaskId", "CurrentNum", "RequireNum", "IsGetAward"}
              and all(isinstance(t[k], int) for k in t) for t in d["DailyTaskData"]),
          d["DailyTaskData"][:2])
    check("11301 award entries are Lua-safe dicts",
          all(set(a) == {"ActivityAwardId", "IsGetAward", "RequireActivity"}
              and all(isinstance(a[k], int) for k in a) for a in d["ActivityAwardData"]),
          d["ActivityAwardData"][:2])
    check("11301 RoleActivity is a number", isinstance(d["RoleActivity"], int), d)


def test_11302_claim_and_same_day_refusal() -> None:
    player = _player()
    store, session = _stub()
    state = handlers._daily_state(player)
    state["counters"]["LevySilver"] = 12  # task 2 RequireNum == 12
    d = handlers.action_daily_task_claim({"TaskId": 2}, player, session, store)
    check("11302 reachable task succeeds", "_fail" not in d, d.get("_fail"))
    check("11302 adds config AwardActivity to RoleActivity",
          d["RoleActivity"] == 10, d)
    check("11302 gain items are config pairs [5,10]",
          d["GainActivityItem"] == [[5, 10]], d["GainActivityItem"])
    check("11302 grants item 5 x10 in RemainList",
          any(r["ItemId"] == 5 and r["Num"] == 10 for r in d["RemainList"]), d["RemainList"])
    check("11302 marks the task claimed",
          player.data["DailyTaskFlags"]["2"] == 1, player.data["DailyTaskFlags"])
    again = handlers.action_daily_task_claim({"TaskId": 2}, player, session, store)
    check("11302 same-day second claim refused", "_fail" in again, again)


def test_11303_claim_and_next_day_reset() -> None:
    player = _player()
    store, session = _stub()
    state = handlers._daily_state(player)
    player.data["RoleActivity"] = 10  # activity award 1 RequireActivity == 10
    before_gold = int(player.data["RoleData"]["Gold"])
    d = handlers.action_activity_award_claim({"ActivityAwardId": 1}, player, session, store)
    check("11303 reachable milestone succeeds", "_fail" not in d, d.get("_fail"))
    silver = int(player.data["RoleData"]["Silver"])
    check("11303 skips Num=0 power-derived entries, grants config item 7 x10",
          any(r["ItemId"] == 7 and r["Num"] == 10 for r in d["RemainList"])
          and all(r["Num"] == silver for r in d["RemainList"] if r["ItemId"] == 2),
          d["RemainList"])
    check("11303 no gold fabricated", int(player.data["RoleData"]["Gold"]) == before_gold,
          player.data["RoleData"]["Gold"])
    again = handlers.action_activity_award_claim({"ActivityAwardId": 1}, player, session, store)
    check("11303 same-day second claim refused", "_fail" in again, again)

    # Next UTC+3 game day: reset counters, flags and activity, not achievements.
    player.data["AchievementStages"] = {"1": 1}
    player.data["AchievementCounters"] = {"LevySilver": 7}
    player.data["DailyTaskDate"] = (
        handlers._game_time() - datetime.timedelta(days=1)
    ).strftime(handlers._GAME_DATE_FMT)
    info = handlers.action_daily_task({}, player, session, store)
    check("11301 next day resets all 17 task flags",
          all(t["IsGetAward"] == 0 for t in info["DailyTaskData"]), info["DailyTaskData"][:2])
    check("11301 next day resets all 5 activity flags",
          all(a["IsGetAward"] == 0 for a in info["ActivityAwardData"]), info["ActivityAwardData"])
    check("11301 next day resets counters and RoleActivity",
          info["RoleActivity"] == 0 and all(t["CurrentNum"] == 0 for t in info["DailyTaskData"]),
          info)
    check("daily reset leaves lifetime achievement data untouched",
          player.data["AchievementStages"] == {"1": 1}
          and player.data["AchievementCounters"]["LevySilver"] == 7,
          (player.data["AchievementStages"], player.data["AchievementCounters"]))


def test_counters_from_existing_handlers() -> None:
    player = _player()
    store, session = _stub()
    handlers.action_manage_assets({"CollectType": 1}, player, session, store)
    check("levy bumps lifetime + daily Silver counters",
          player.data["AchievementCounters"]["LevySilver"] == 1
          and player.data["DailyCounters"]["LevySilver"] == 1,
          (player.data["AchievementCounters"], player.data["DailyCounters"]))
    handlers.action_government_event({"OptionNum": 1}, player, session, store)
    check("government affair bumps lifetime + daily counters",
          player.data["AchievementCounters"]["GovernmentAffairs"] == 1
          and player.data["DailyCounters"]["Government"] == 1,
          (player.data["AchievementCounters"], player.data["DailyCounters"]))
    handlers.action_stage_fight({}, player, session, store)
    check("phase clear bumps lifetime + daily campaign counters",
          player.data["AchievementCounters"]["CampaignVictories"] == 1
          and player.data["DailyCounters"]["Campaign"] == 1,
          (player.data["AchievementCounters"], player.data["DailyCounters"]))
    handlers.action_wives_call({}, player, session, store)
    check("consort visit bumps HaremVisits",
          player.data["AchievementCounters"]["HaremVisits"] == 1,
          player.data["AchievementCounters"])
    metrics = handlers._achievement_current(player)
    check("achievement 5/6/9/15 read the real counters",
          metrics[5] == 1 and metrics[6] == 1 and metrics[9] == 1 and metrics[15] == 1,
          metrics)


def test_main_page_dots_track_claimable_state() -> None:
    """Main-page dots are RedPoint keys; presence lights them
    (mainUi.bottomActiveDialog.lua 11/35, redPointMod.lua 324)."""
    player = _player()
    store, session = _stub()
    rp = handlers._client_red_point(player)
    # Fresh account: daily counters are zero, but achievement stage 1 of
    # sign-in (CheckInDay=1) and power (Power>=1000) is claimable.
    check("fresh account has no Quests dot", "dailyTask" not in rp, rp)
    check("fresh account has an Achievements dot when a stage is claimable",
          "achievement" in handlers._client_red_point(player), rp)
    state = handlers._daily_state(player)
    state["counters"]["LevySilver"] = 12  # task 2 RequireNum == 12
    check("completed unclaimed daily task lights the Quests dot",
          handlers._client_red_point(player).get("dailyTask") == 1,
          handlers._client_red_point(player))
    d = handlers.action_daily_task_claim({"TaskId": 2}, player, session, store)
    check("task claim succeeds", "_fail" not in d, d.get("_fail"))
    # The activity milestone (RoleActivity >= 10) may still be claimable;
    # draining everything must turn the dot off.
    handlers.action_activity_award_claim({"ActivityAwardId": 1},
                                         player, session, store)
    check("claiming the activity milestone turns the Quests dot off",
          "dailyTask" not in handlers._client_red_point(player),
          handlers._client_red_point(player))


def main() -> None:
    test_main_page_dots_track_claimable_state()
    test_11201_all_ids_and_real_metrics()
    test_11202_claim_rules()
    test_11301_shape()
    test_11302_claim_and_same_day_refusal()
    test_11303_claim_and_next_day_reset()
    test_counters_from_existing_handlers()
    print("\n%d/%d checks passed" % (sum(ok), len(ok)))
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
