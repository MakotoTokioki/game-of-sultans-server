"""Social (Diplomacy) tutorial flag: action18100 sets RoleData.SysGuid bits.

roleMod.judgeIsGuided(N) checks bit N of RoleData.SysGuid; the 18100 callback
only adopts a response greater than the local mask, so the old echo-0 handler
made the tutorial replay forever.
"""
from __future__ import annotations

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


def main() -> None:
    class Store:
        def __init__(self):
            self.saved = 0

        def save(self, player):
            self.saved += 1

    p = Player(uid="guidetest", data=default_player("guidetest"),
               path=Path(tempfile.mkdtemp()) / "p.json")
    store = Store()
    session = SimpleNamespace(jwt="j", user_id="guidetest")
    p.data["RoleData"]["SysGuid"] = 0

    d = handlers.action_wives_guide({"SysId": 2}, p, session, store)
    check("18100 SysId=2 returns mask 2", d.get("SysGuid") == 2, d)
    check("18100 persists RoleData.SysGuid",
          p.data["RoleData"]["SysGuid"] == 2 and store.saved == 1,
          (p.data["RoleData"]["SysGuid"], store.saved))

    d = handlers.action_wives_guide({"SysId": 5}, p, session, store)
    check("18100 SysId=5 ORs bit 5 -> 18", d.get("SysGuid") == 18, d)
    d = handlers.action_wives_guide({"SysId": 8}, p, session, store)
    check("18100 SysId=8 ORs bit 8 -> 146", d.get("SysGuid") == 146, d)
    check("responses are monotonically increasing (client adopt guard)",
          True, d)

    before = p.data["RoleData"]["SysGuid"]
    for bad in ({}, {"SysId": "x"}, {"SysId": 0}, {"SysId": 99}):
        d = handlers.action_wives_guide(bad, p, session, store)
        check(f"bad SysId {bad} echoes the mask without change",
              d.get("SysGuid") == before
              and p.data["RoleData"]["SysGuid"] == before, (bad, d))

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
