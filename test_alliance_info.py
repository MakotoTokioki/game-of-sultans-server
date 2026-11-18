"""Alliance page (11601/11603) regressions.

_allianceMod.lua's callbacks read A0_3.AllianceData.QuitTime / .AllianceId
unguarded (4347-4348, 3989-3990).  Our old missing handler fell through to
action_full_state, which has no AllianceData, so entering the Alliance page
crashed with "attempt to index field 'AllianceData' (a nil value)".
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


def main() -> None:
    p = Player(uid="alliancetest", data=default_player("alliancetest"),
               path=Path(tempfile.mkdtemp()) / "p.json")
    store = SimpleNamespace(save=lambda p: None)
    session = SimpleNamespace(jwt="j", user_id="alliancetest")
    for action in ("11601", "11603"):
        check(f"{action} has a dedicated handler",
              action in handlers.HANDLERS, handlers.HANDLERS.get(action))
        d = handlers.dispatch(action, {}, p, session, store)
        data = d.get("AllianceData")
        check(f"{action} carries an AllianceData table",
              isinstance(data, dict), d)
        check(f"{action} is a neutral no-alliance state",
              data.get("AllianceId") == 0 and data.get("AllianceName") == ""
              and data.get("AllianceGuid") == "", data)
        check(f"{action} omits date-like fields (nil is client-safe)",
              "QuitTime" not in data and "LastExchangeTime" not in data, data)
        check(f"{action} includes war timestamp 0 (unguarded arithmetic)",
              d.get("AllianceWarUTC") == 0, d)
        check(f"{action} carries safe empty lists",
              d.get("RefusedList") == [] and d.get("TradeInfo") == [], d)
        check(f"{action} response is json round-trippable",
              json.loads(json.dumps(d)) == d)
    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
