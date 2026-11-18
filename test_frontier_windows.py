"""Frontier (instance) event windows — oracle-backed regression.

Client: scene/instanceScene.instanceScene.lua 33-46/96-112 (button gates),
modules/_instanceMod.lua 1035-1076 (11801), 802-828 (isShowInMain).
Oracle: recon/v7-capture/v7_actions.jsonl 156/210/222.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers
from gos.defaults import default_player
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


ORACLE_SYS = 1794821839
ORACLE_SINGLE = [1794819600, 1794826800]   # 12:00-14:00 GMT+3
ORACLE_GLOBAL = [1794848400, 1794852000]   # 20:00-21:00 GMT+3


def _player() -> Player:
    data = default_player("frontiertest")
    data["BossSingleUTC"] = 0
    data["BossGlobalUTC"] = [0, 0]
    return Player(uid="frontiertest", data=data,
                  path=Path(tempfile.mkdtemp()) / "p.json")


def main() -> None:
    check("BossSingleUTC reproduces v7 oracle",
          handlers._boss_window("BossSingleUTC", ORACLE_SYS) == ORACLE_SINGLE,
          handlers._boss_window("BossSingleUTC", ORACLE_SYS))
    check("BossGlobalUTC reproduces v7 oracle",
          handlers._boss_window("BossGlobalUTC", ORACLE_SYS) == ORACLE_GLOBAL,
          handlers._boss_window("BossGlobalUTC", ORACLE_SYS))
    check("durations 2h / 1h",
          ORACLE_SINGLE[1] - ORACLE_SINGLE[0] == 7200
          and ORACLE_GLOBAL[1] - ORACLE_GLOBAL[0] == 3600)
    # Same wall clock every game day (client mod-86400 wrap math).
    check("window wall clock stable across days",
          all(handlers._boss_window("BossSingleUTC", ORACLE_SYS + 86400 * d)[0] % 86400 == 32400
              and handlers._boss_window("BossGlobalUTC", ORACLE_SYS + 86400 * d)[0] % 86400 == 61200
              for d in (0, 1, 7, 30)))

    p = _player()
    handlers._refresh_boss_windows(p)
    for key, duration in (("BossSingleUTC", 7200), ("BossGlobalUTC", 3600)):
        v = p.data[key]
        check(f"{key} healed to 2-int array",
              isinstance(v, list) and len(v) == 2
              and all(isinstance(x, int) for x in v)
              and v[1] - v[0] == duration, v)

    store = SimpleNamespace(save=lambda p: None)
    session = SimpleNamespace(jwt="j", user_id="frontiertest")
    d = handlers.action_instance_info({}, p, session, store)
    check("11801 returns fresh non-[0,0] windows",
          d["BossSingleUTC"] == p.data["BossSingleUTC"]
          and d["BossSingleUTC"] != [0, 0] and d["BossGlobalUTC"] != [0, 0], d)
    check("11801 keeps the other four callback slices",
          d["BossSingleComplete"] == 0
          and d["DamageRankAward"] == {"RemainList": [], "RoleRank": 0}
          and isinstance(d["BossGlobalDead"], dict), d)
    check("11801 response is json round-trippable", json.loads(json.dumps(d)) == d)
    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
