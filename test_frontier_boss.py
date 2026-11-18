"""Hunting Grounds (single boss) fight — 11802/11803 oracle-backed tests.

Client: modules/_instanceMod.lua 1081-1139 (11802), 1163-1220 (11803).
Oracle: recon/v7-capture/v7_actions.jsonl 214 (11802), 220 (11803).
"""
from __future__ import annotations

import datetime
import json
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


ORACLE_11802 = {"CounsellorAttackData": {}, "RecoverCountList": [],
                "BoxRewardList": [], "BossId": 1, "BossHPMax": 20010,
                "BossHP": 20010}
ORACLE_11803 = {"BossId": 2, "BossHP": 450310, "BossHPMax": 450310,
                "CounsellorAttackData": {"2": 1},
                "RecoverCountList": [{"MinisterId": 2, "RecoverCount": 0}]}


def _player() -> Player:
    return Player(uid="frontierbosstest", data=default_player("frontierbosstest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def main() -> None:
    by_stage = {int(r.get("Stage") or 0): r
                for r in v2configs.load("TcBossSingle") if isinstance(r, dict)}
    check("TcBossSingle bundled with 20 waves", len(by_stage) == 20, len(by_stage))
    check("stage 1/2 HP match oracle",
          int(by_stage[1]["HP"]) == 20010 and int(by_stage[2]["HP"]) == 450310)
    check("11802/11803 registered",
          "11802" in handlers.HANDLERS and "11803" in handlers.HANDLERS)

    store = SimpleNamespace(save=lambda p: None)
    session = SimpleNamespace(jwt="j", user_id="frontierbosstest")
    p = _player()

    d = handlers.dispatch("11802", {}, p, session, store)
    check("11802 dispatch returns boss state (no full-state fallthrough)",
          d.get("BossId") == 1 and d.get("BossHPMax") == 20010, d)
    check("11802 first entry matches oracle",
          all(d.get(k) == v for k, v in ORACLE_11802.items()) and d["BossSingleComplete"] is False,
          d)
    check("11802 booleans are JSON booleans (Lua 0 is truthy)",
          d["BossSingleComplete"] is False
          and json.loads(json.dumps(d))["BossSingleComplete"] is False, d)
    check("11802 json round-trippable", json.loads(json.dumps(d)) == d)

    a = handlers.action_instance_boss_attack({"CounsellorId": 2, "IsHeadShot": 0},
                                             p, session, store)
    check("11803 kills wave 1 -> oracle wave 2",
          all(a.get(k) == v for k, v in ORACLE_11803.items())
          and a["IsBoxAward"] is False and a["IsComplete"] is False, a)
    check("11803 RemainList has stage items 9/10",
          {e["ItemId"] for e in a["RemainList"]} >= {9, 10}, a.get("RemainList"))
    check("attack persisted",
          p.data["BossSingleFight"]["BossId"] == 2
          and p.data["BossSingleFight"]["Attacks"] == {"2": 1})
    check("repeat attack refused",
          handlers.action_instance_boss_attack({"CounsellorId": 2}, p, session, store)
          == {"BossStasus": 1})
    check("unknown counsellor refused",
          handlers.action_instance_boss_attack({"CounsellorId": 99}, p, session, store)
          == {"BossStasus": 1})

    state = p.data["BossSingleFight"]
    last = handlers._boss_single_max_stage()
    state["BossId"], state["Attacks"] = last, {}
    state["BossHP"] = int(by_stage[last]["HP"])
    z = handlers.action_instance_boss_attack({"CounsellorId": 3, "IsHeadShot": 1},
                                             p, session, store)
    check("final wave kill -> zero triple + IsComplete",
          z["BossId"] == 0 and z["BossHP"] == 0 and z["BossHPMax"] == 0
          and z["IsComplete"] == 1, z)
    d2 = handlers.action_instance_boss_info({}, p, session, store)
    check("11802 after completion shows complete",
          d2["BossId"] == 0 and d2["BossSingleComplete"] is True, d2)
    check("11801 mirrors completion",
          handlers.action_instance_info({}, p, session, store)["BossSingleComplete"] is True)

    p2 = _player()
    p2.data["BossSingleFight"] = {
        "Day": (handlers._game_time() - datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
        "BossId": 7, "BossHP": 123, "Attacks": {"1": 1},
    }
    d3 = handlers.action_instance_boss_info({}, p2, session, store)
    check("stale day reseeds to fresh wave 1",
          d3["BossId"] == 1 and d3["BossHP"] == 20010
          and d3["CounsellorAttackData"] == {}, d3)
    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
