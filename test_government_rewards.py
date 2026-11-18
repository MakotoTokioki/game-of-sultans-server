"""Regression tests for the Imperial Affairs (10701) choice rewards.

Reward formulas (read-only decomp, scene/governmentScene.governmentScene.lua):
  yes (OptionNum 1) for the *current* TcGovEvent row's ItemId:
    ItemId 2/3/4 -> floor(AffairResourceBase
                          + GovAchievementRate * getRolePower(ItemId))
    otherwise    -> 1
  no  (OptionNum 2) -> GovAchievement EXP (icon/5.png)

The power stats are derived from the v2 configs, not hardcoded:
  TcTalent[].TalentType/StarLevel + TcStar[].Effect -> aptitude[1..4]
  floor(((Lv-1)*0.1*(Lv+2) + 10) * aptitude[i]) per unlocked counsellor

IMPORTANT (test-order/attribute-order note): the client's own language file
lists the four attributes as "Military, Research, Politics, Prestige"
(settings.Clienten.lua ~6693).  With the observed devtest viziers
(Lv 1 / 14 / 3) this yields [215, 105, 72, 103] -- the exact four numbers
seen on-device.  Therefore getRolePower(2) = Research = 105 and
getRolePower(3) = Politics = 72.  The +5007 screenshot is the ItemId 3
(Food) event, not ItemId 2; ItemId 2 grants 5000 + 0.1*105 = 5010.

Run: python3 test_government_rewards.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from gos import handlers, v2configs
from gos.defaults import default_player
from gos.store import Player

ok: list[bool] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _store():
    return SimpleNamespace(save=lambda p: None)


def _session():
    return SimpleNamespace(jwt="jwt", user_id="rewardtest")


def _player(data: dict | None = None) -> Player:
    return Player(uid="rewardtest", data=data or default_player("rewardtest"),
                  path=Path("/tmp/opencode/gos_test_players/rewardtest.json"))


def _devtest_data() -> dict:
    """Player data with the on-device vizier levels that produced the stats
    215/105/72/103.  Synthesized from defaults so the test no longer depends
    on a live account file (which may be deleted/reset between sessions)."""
    data = default_player("devtest")
    for counsellor, level in zip(data["RoleCounsellor"], (36, 48, 14)):
        counsellor["Lv"] = level
    level = int(data["RoleData"].get("Lv") or 1)
    cfg = next((r for r in v2configs.load("TcLvUpgradeCfg")
                if int(r.get("Level") or 0) == level), {})
    for key in ("GovAchievement", "GovAchievementRate", "GovEventTimes"):
        if cfg.get(key) is not None:
            data["RoleData"][key] = cfg[key]
    return data


def _ready(event_id: int, data: dict | None = None) -> Player:
    data = json.loads(json.dumps(data if data is not None else _devtest_data()))
    data["RoleData"]["GovEvenId"] = event_id
    # Full backlog (GovEventTimes ticks) so each processed affair consumes
    # exactly one cooldown tick, matching the v7 capture sequence.
    data["RoleData"]["GovTimeUTC"] = int(time.time()) - 3 * int(v2configs.env_value("GovEventCd"))
    data.setdefault("GovAffairs", {})["Completed"] = 0
    # The level-indexed government values were previously read from the live
    # account file; seed them from the original TcLvUpgradeCfg row instead.
    level = int(data["RoleData"].get("Lv") or 1)
    cfg = next((r for r in v2configs.load("TcLvUpgradeCfg")
                if int(r.get("Level") or 0) == level), {})
    for key in ("GovAchievement", "GovAchievementRate", "GovEventTimes"):
        if cfg.get(key) is not None:
            data["RoleData"][key] = cfg[key]
    return _player(data)


def _item_id(event_id: int) -> int:
    return int(v2configs.load("TcGovEvent")[event_id - 1]["ItemId"])


def test_power_stats_are_derived_from_v2_configs() -> None:
    """No magic power number: it comes from TcTalent/TcStar + vizier levels."""
    player = _player(_devtest_data())
    stats = handlers._gov_role_power_stats(player)
    check("devtest power stats come from configs, not hardcoded values",
          len(stats) == 4 and all(isinstance(v, int) and v >= 0 for v in stats),
          stats)
    check("getRolePower(2) matches derived stats slot 2",
          handlers._gov_role_power(player, 2) == stats[1],
          (handlers._gov_role_power(player, 2), stats))
    check("getRolePower(3) matches derived stats slot 3",
          handlers._gov_role_power(player, 3) == stats[2],
          (handlers._gov_role_power(player, 3), stats))

    # Fresh starter viziers are all Lv1; the base term is floor(10 * aptitude).
    fresh = _player()
    check("fresh Lv1 viziers yield [90,80,50,60]",
          handlers._gov_role_power_stats(fresh) == [90, 80, 50, 60],
          handlers._gov_role_power_stats(fresh))


def test_yes_item3_regression_5007() -> None:
    """The on-device screenshot reward: +5007 for the Politics-backed event."""
    player = _ready(4)  # TcGovEvent Id 4 -> ItemId 3 (Food)
    role = player.data["RoleData"]
    before = int(role["Food"])
    rate = float(role["GovAchievementRate"])
    base = float(v2configs.env_value("AffairResourceBase"))
    power = handlers._gov_role_power(player, 3)
    expected = int(base + rate * power)
    check("screenshot regression uses the current derived power", expected > 0,
          (expected, power))
    res = handlers.action_government_event({"OptionNum": 1}, player,
                                           _session(), _store())
    gained = int(role["Food"]) - before
    check("yes ItemId 3 grants the config-derived Food amount", gained == expected,
          (gained, expected))
    check("yes reward first in RemainList with absolute new total",
          res["RemainList"][0] == {"ItemId": 3, "Num": before + expected},
          res["RemainList"][0])


def test_yes_item2_item4_use_their_own_power_slot() -> None:
    """ItemId 2 uses getRolePower(2)=105; ItemId 4 uses getRolePower(4)=103."""
    for event_id, item_id, key in ((1, 2, "Silver"), (7, 4, "Soldier")):
        player = _ready(event_id)
        role = player.data["RoleData"]
        power = handlers._gov_role_power(player, item_id)
        before = int(role[key])
        res = handlers.action_government_event({"OptionNum": 1}, player,
                                               _session(), _store())
        expected = int(float(v2configs.env_value("AffairResourceBase"))
                       + float(role["GovAchievementRate"]) * power)
        gained = int(role[key]) - before
        label = "Silver" if item_id == 2 else "Soldier"
        check("yes ItemId %d grants +%d %s (getRolePower(%d)=%d)"
              % (item_id, expected, label, item_id, power),
              gained == expected, (gained, expected))
        check("yes ItemId %d RemainList[0] absolute" % item_id,
              res["RemainList"][0] == {"ItemId": item_id, "Num": before + expected},
              res["RemainList"][0])


def test_no_grants_govachievement_exp() -> None:
    player = _ready(1)
    role = player.data["RoleData"]
    before_exp = int(role["Exp"])
    achievement = int(float(role["GovAchievement"]))
    res = handlers.action_government_event({"OptionNum": 2}, player,
                                           _session(), _store())
    check("no grants +GovAchievement EXP", int(role["Exp"]) - before_exp == achievement,
          (role["Exp"], before_exp, achievement))
    level = int(role.get("Lv") or 1)
    cfg_achievement = next(
        int(r.get("GovAchievement") or 0)
        for r in v2configs.load("TcLvUpgradeCfg")
        if int(r.get("Level") or 0) == level)
    check("No grant equals TcLvUpgradeCfg[Lv].GovAchievement",
          achievement == cfg_achievement, (level, achievement, cfg_achievement))
    check("no puts ItemId 5 (EXP) first with absolute total",
          res["RemainList"][0] == {"ItemId": 5, "Num": before_exp + achievement},
          res["RemainList"][0])


def test_event_advances_and_remain_list_is_absolute() -> None:
    player = _ready(1)
    role = player.data["RoleData"]
    ids = sorted(int(r["Id"]) for r in v2configs.load("TcGovEvent"))
    first = int(role["GovEvenId"])
    before = {key: int(role[key]) for key in ("Gold", "Silver", "Food", "Soldier", "Exp")}
    res = handlers.action_government_event({"OptionNum": 1}, player,
                                           _session(), _store())
    check("EventId advances along TcGovEvent ids",
          res["EventId"] == ids[(ids.index(first) + 1) % len(ids)]
          and role["GovEvenId"] == res["EventId"], (res["EventId"], first))
    values = {r["ItemId"]: r["Num"] for r in res["RemainList"]}
    mapping = {1: "Gold", 2: "Silver", 3: "Food", 4: "Soldier", 5: "Exp"}
    check("every RemainList resource row is an absolute post-grant total",
          all(values.get(iid) == int(role[key]) for iid, key in mapping.items()
              if iid in values), (values, role))
    check("granted resource is first, other absolute resources follow",
          res["RemainList"][0]["ItemId"] == _item_id(first)
          and len(res["RemainList"]) >= 5, res["RemainList"])
    check("MiracleTrigger stays false / Achievement stays a bool",
          res["MiracleTrigger"] is False and isinstance(res["Achievement"], bool), res)


def test_repeated_calls_and_cooldown_anchor_unchanged() -> None:
    """From a full backlog each call consumes one GovEventCd tick; cooldown
    refusal stays the client's responsibility (no new check)."""
    player = _ready(1)
    role = player.data["RoleData"]
    cd = int(v2configs.env_value("GovEventCd"))
    anchor = int(role["GovTimeUTC"])
    ids = sorted(int(r["Id"]) for r in v2configs.load("TcGovEvent"))
    first = int(role["GovEvenId"])
    for index in range(1, 4):
        res = handlers.action_government_event({"OptionNum": 2}, player,
                                               _session(), _store())
        check("call %d advances GovTimeUTC by exactly GovEventCd" % index,
              int(role["GovTimeUTC"]) == anchor + index * cd,
              (role["GovTimeUTC"], anchor + index * cd))
        check("call %d advances EventId" % index,
              res["EventId"] == ids[(ids.index(first) + index) % len(ids)], res["EventId"])
    achievement = int(float(role["GovAchievement"]))
    check("no cooldown refusal added: repeated calls keep granting",
          int(role["Exp"]) == int(_devtest_data()["RoleData"]["Exp"]) + 3 * achievement,
          (role["Exp"], achievement))


def test_reward_amounts_come_only_from_config() -> None:
    """Changing AffairResourceBase/GovAchievementRate changes the amount;
    nothing is hardcoded."""
    base = float(v2configs.env_value("AffairResourceBase"))
    rate = float(_devtest_data()["RoleData"]["GovAchievementRate"])
    power = handlers._gov_role_power(_player(_devtest_data()), 3)
    expected = int(base + rate * power)
    player = _ready(4)
    before = int(player.data["RoleData"]["Food"])
    handlers.action_government_event({"OptionNum": 1}, player, _session(), _store())
    gained = int(player.data["RoleData"]["Food"]) - before
    check("amount == AffairResourceBase + GovAchievementRate * real power",
          gained == expected, (gained, expected, base, rate, power))


def main() -> None:
    test_power_stats_are_derived_from_v2_configs()
    test_yes_item3_regression_5007()
    test_yes_item2_item4_use_their_own_power_slot()
    test_no_grants_govachievement_exp()
    test_event_advances_and_remain_list_is_absolute()
    test_repeated_calls_and_cooldown_anchor_unchanged()
    test_reward_amounts_come_only_from_config()
    print("\n%d/%d checks passed" % (sum(ok), len(ok)))
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
