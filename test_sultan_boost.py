"""Sultan Boost (MiracleTrigger) regressions.

Odds and multipliers are original data:
  ConfigEnvSet.MiracleProbability = [0.15]*7  (15%)
  Clienten.lua: Trade Earnings 10x, Gold/Grain/Soldier 10x,
                Player XP 3x, Attribute Book/Tome 3x,
                ranking worship +110 instead of +10 (v7 oracle).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers, v2configs
from gos.defaults import default_player
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def make(uid):
    return Player(uid=uid, data=default_player(uid, "Sultan"),
                  path=Path(tempfile.mkdtemp()) / f"{uid}.json")


def main():
    cfg = v2configs.load("ConfigEnvSet")
    check("original MiracleProbability is 15%",
          cfg.get("MiracleProbability") == [0.15] * 7,
          cfg.get("MiracleProbability"))
    original = handlers._miracle_roll

    # Ranking worship: normal +10, boosted +110 with MiracleTrigger true.
    p = make("boost_rank")
    handlers._miracle_roll = lambda: False
    d = handlers.action_rank_worship({}, p, None, SimpleNamespace(save=lambda p: None))
    check("normal worship grants +10 gold",
          d["IncrRemainList"][0]["Num"] == 10 and d["MiracleTrigger"] is False, d)
    p2 = make("boost_rank2")
    handlers._miracle_roll = lambda: True
    d = handlers.action_rank_worship({}, p2, None, SimpleNamespace(save=lambda p: None))
    check("boosted worship grants +110 gold and sets MiracleTrigger",
          d["IncrRemainList"][0]["Num"] == 110 and d["MiracleTrigger"] is True, d)
    check("boosted gold persisted",
          int(p2.data["RoleData"]["Gold"]) == 5110, p2.data["RoleData"]["Gold"])

    # Imperial Affairs: Yes resource 10x, No EXP 3x.
    p3 = make("boost_gov")
    handlers._miracle_roll = lambda: True
    store = SimpleNamespace(save=lambda p: None)
    handlers._gov_state(p3)  # seed GovEvenId / level-indexed values
    rd = p3.data["RoleData"]
    # Event 1 -> ItemId 2 (Silver); compute the base from the same config the
    # handler uses instead of hardcoding a number.
    event = next(r for r in handlers._gov_rows() if int(r.get("Id") or 0) == int(rd["GovEvenId"]))
    item_id = int(event.get("ItemId") or 0)
    key = handlers._REMAIN_LIST_MAP[item_id]
    base = float(v2configs.env_value("AffairResourceBase", 5000) or 5000)
    rate = float(rd.get("GovAchievementRate") or 0)
    normal = int(__import__('math').floor(base + rate * handlers._gov_role_power(p3, item_id)))
    before = int(rd[key])
    d = handlers.action_government_event({"OptionNum": 1}, p3, None, store)
    check("boosted affair sets MiracleTrigger", d.get("MiracleTrigger") is True, d)
    check("boosted affair resource is 10x",
          int(rd[key]) - before == 10 * normal, (before, rd[key], normal))
    before_exp = int(rd["Exp"])
    handlers.action_government_event({"OptionNum": 2}, p3, None, store)
    check("boosted No-button EXP is 3x",
          int(rd["Exp"]) - before_exp == 3, (before_exp, rd["Exp"]))

    # Levy: 10x earnings with MiracleTrigger true.
    import time
    p4 = make("boost_levy")
    handlers._miracle_roll = lambda: False
    now = int(time.time())
    p4.data["RoleData"]["LevyTimeUTC"] = [now - 120] * 3
    before = int(p4.data["RoleData"]["Silver"])
    d = handlers.action_manage_assets({"CollectType": 1}, p4, None,
                                      SimpleNamespace(save=lambda p: None))
    normal_gain = int(p4.data["RoleData"]["Silver"]) - before
    check("normal levy mirrors the config revenue", normal_gain > 0 and d["MiracleTrigger"] is False,
          (normal_gain, d.get("MiracleTrigger")))

    p5 = make("boost_levy2")
    handlers._miracle_roll = lambda: True
    p5.data["RoleData"]["LevyTimeUTC"] = [now - 120] * 3
    before = int(p5.data["RoleData"]["Silver"])
    d = handlers.action_manage_assets({"CollectType": 1}, p5, None,
                                      SimpleNamespace(save=lambda p: None))
    check("boosted levy grants 10x and sets MiracleTrigger",
          int(p5.data["RoleData"]["Silver"]) - before == normal_gain * 10
          and d["MiracleTrigger"] is True,
          (before, p5.data["RoleData"]["Silver"], normal_gain, d.get("MiracleTrigger")))

    handlers._miracle_roll = original
    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
