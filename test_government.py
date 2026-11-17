"""Regression tests for the Imperial Affairs module (10701/10702).

Evidence:
  modules/_governmentMod.lua            10701/10702 callbacks, getGovNum
  scene/governmentScene.governmentScene.lua  updateEvent (RemainList[1],
                                          MiracleTrigger), option buttons
  configs/settings.TcGovEvent.lua        event ids 1..12
  configs/settings.ConfigEnvSet.lua      GovEventCd = 1800
  configs/settings.TcLvUpgradeCfg.lua    GovEventTimes/GovAchievement
  recon/v7-capture: 10701 responses + login RoleData GovEvenId/GovTimeUTC

Run: python3 test_government.py
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


def _player() -> Player:
    data = default_player("govtest")
    return Player(uid="govtest", data=data,
                  path=Path("/tmp/opencode/gos_test_players/govtest.json"))


def _stub():
    store = SimpleNamespace(save=lambda p: None)
    session = SimpleNamespace(jwt="jwt", user_id="govtest")
    return store, session


def test_login_seeds_government_state() -> None:
    player = _player()
    store, session = _stub()
    data = handlers.action_login({}, player, session, store)
    role = data["RoleData"]
    first = v2configs.load("TcGovEvent")[0]["Id"]
    check("login seeds GovEvenId from the config",
          role.get("GovEvenId") == first, role.get("GovEvenId"))
    check("login seeds GovTimeUTC / GovEventTime",
          isinstance(role.get("GovTimeUTC"), int)
          and isinstance(role.get("GovEventTime"), str), role)
    lv1 = v2configs.load("TcLvUpgradeCfg")[0]
    check("login seeds config-backed Gov achievement fields",
          all(role.get(k) == lv1[k] for k in
              ("GovEventTimes", "GovAchievement", "GovAchievementRate")), role)


def test_10702_initial_shape() -> None:
    player = _player()
    store, session = _stub()
    d = handlers.action_government_info({}, player, session, store)
    for key in ("EventId", "GovTimeUTC", "OverTime", "Achievement", "MiracleTrigger"):
        check("10702 has %s" % key, key in d, d)
    check("10702 EventId is the first TcGovEvent id",
          d["EventId"] == v2configs.load("TcGovEvent")[0]["Id"], d)
    check("10702 GovTimeUTC is a unix int", isinstance(d["GovTimeUTC"], int), d)
    check("10702 OverTime is a non-negative int", isinstance(d["OverTime"], int)
          and d["OverTime"] >= 0, d)
    check("10702 Achievement/MiracleTrigger are booleans",
          isinstance(d["Achievement"], bool) and d["MiracleTrigger"] is False, d)


def test_10701_advance_and_persistence() -> None:
    player = _player()
    store, session = _stub()
    role = player.data["RoleData"]
    role["GovTimeUTC"] = int(time.time()) - 3600  # two config cooldowns elapsed
    anchor = role["GovTimeUTC"]
    first_id = role.get("GovEvenId") or v2configs.load("TcGovEvent")[0]["Id"]
    cd = int(v2configs.env_value("GovEventCd"))
    ids = sorted(r["Id"] for r in v2configs.load("TcGovEvent"))

    called_at = int(time.time())
    d = handlers.action_government_event({"OptionNum": 1}, player, session, store)
    check("10701 EventId advances along TcGovEvent ids",
          d["EventId"] == ids[(ids.index(first_id) + 1) % len(ids)]
          and role["GovEvenId"] == d["EventId"], (d["EventId"], first_id))
    check("10701 GovTimeUTC advances by exactly GovEventCd",
          role["GovTimeUTC"] == anchor + cd, (anchor, role["GovTimeUTC"], cd))
    expected_over = max(0, called_at - role["GovTimeUTC"])
    check("10701 OverTime = now - new anchor (+/-1s)",
          abs(d["OverTime"] - expected_over) <= 1, (d["OverTime"], expected_over))
    check("10701 GovEventTime is a parseable date string",
          isinstance(role.get("GovEventTime"), str)
          and len(role["GovEventTime"]) == 19 and role["GovEventTime"][4] == "-",
          role.get("GovEventTime"))
    check("10701 completed count persisted", player.data["GovAffairs"]["Completed"] == 1,
          player.data["GovAffairs"])
    # reload: the next response must come from persisted state, not locals
    d2 = handlers.action_government_info({}, player, session, store)
    check("reload keeps advanced EventId/GovTimeUTC",
          d2["EventId"] == d["EventId"] and d2["GovTimeUTC"] == role["GovTimeUTC"],
          (d2["EventId"], d2["GovTimeUTC"]))
    # wrap around at the last config id
    role["GovEvenId"] = ids[-1]
    d3 = handlers.action_government_event({"OptionNum": 2}, player, session, store)
    check("10701 wraps to first config event id", d3["EventId"] == ids[0], d3)


def test_rewards_are_config_derived_not_fabricated() -> None:
    player = _player()
    store, session = _stub()
    role = player.data["RoleData"]
    before = {k: int(role[k]) for k in ("Gold", "Silver", "Food", "Soldier", "Exp")}
    d = handlers.action_government_event({"OptionNum": 2}, player, session, store)
    remain = d["RemainList"]
    check("RemainList is a non-empty array (updateEvent indexes [1])",
          isinstance(remain, list) and len(remain) > 0, remain)
    check("all RemainList rows are {ItemId:int, Num:int}",
          all(isinstance(r, dict) and isinstance(r.get("ItemId"), int)
              and isinstance(r.get("Num"), int) for r in remain), remain)
    values = {r["ItemId"]: r["Num"] for r in remain}
    mapping = {1: "Gold", 2: "Silver", 3: "Food", 4: "Soldier", 5: "Exp"}
    # No button grants exactly TcLvUpgradeCfg[Lv].GovAchievement EXP.
    grant = int(float(role["GovAchievement"]))
    expected = dict(before)
    expected["Exp"] = before["Exp"] + grant
    check("RemainList reports absolute post-grant totals",
          all(values.get(iid) == expected[key] for iid, key in mapping.items()),
          (values, expected))
    check("no button grants exactly GovAchievement EXP (config-sourced)",
          int(role["Exp"]) == before["Exp"] + grant,
          (role["Exp"], before["Exp"], grant))
    check("granted EXP is first in RemainList", remain[0]["ItemId"] == 5, remain[0])
    check("no item 509 was created", handlers._role_item_count(player, 509) == 0,
          player.data["RoleItem"])
    before_gold = int(role["Gold"])
    info = handlers.action_government_info({"RestoreNum": 2}, player, session, store)
    check("10702 without item 509 does not mutate resources",
          int(role["Gold"]) == before_gold and info["RemainList"][0]["Num"] == before_gold,
          info)


def test_restore_and_lua_safe_types() -> None:
    player = _player()
    store, session = _stub()
    role = player.data["RoleData"]
    cd = int(v2configs.env_value("GovEventCd"))
    anchor = int(time.time()) - 10 * cd
    role["GovTimeUTC"] = anchor
    handlers._role_item_add(player, 509, 3)
    d = handlers.action_government_info({"RestoreNum": 2}, player, session, store)
    check("10702 consumes the real restore item 509",
          handlers._role_item_count(player, 509) == 1, player.data["RoleItem"])
    check("10702 moves the cooldown anchor back by RestoreNum * GovEventCd",
          role["GovTimeUTC"] == anchor - 2 * cd, (anchor, role["GovTimeUTC"]))
    check("10702 response is json round-trippable (Lua-safe types)",
          json.loads(json.dumps(d, ensure_ascii=False)) == d, d)
    check("no table field is numeric 0 (Lua treats 0 as truthy)",
          all(type(d[k]) not in (int, float) for k in
              ("RemainList", "Achievement", "MiracleTrigger")), d)


def main() -> None:
    test_login_seeds_government_state()
    test_10702_initial_shape()
    test_10701_advance_and_persistence()
    test_rewards_are_config_derived_not_fabricated()
    test_restore_and_lua_safe_types()
    print("\n%d/%d checks passed" % (sum(ok), len(ok)))
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
