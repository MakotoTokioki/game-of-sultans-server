"""Tests for misc clusters: prison 10501/10502, seven-day 12601,
synthesis 15100, binding 16001."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos.defaults import default_player
from gos.store import Player
from gos import handlers_misc as hm

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player():
    return Player(uid="misctest", data=default_player("misctest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def _stub():
    return SimpleNamespace(save=lambda p: None), None


def main():
    store, session = _stub()
    p = _player()

    d = hm.action_prison_info({}, p, session, store)
    check("10501 prison info has all callback fields",
          all(k in d for k in ("prisoners", "currentChapter", "soldiers", "blood"))
          and isinstance(d["prisoners"], dict), d)

    p.data["Prison"]["prisoners"]["5"] = {"num": 3}
    d = hm.action_prison_punish({"prisoner_id": 5, "many": 2}, p, session, store)
    check("10502 punish decrements and echoes fame/remainList",
          "fame" in d and "remainList" in d and d["prisoners"]["5"]["num"] == 1, d)

    check("12601 seven-day is Lua-safe map", isinstance(hm.action_seven_days({}, p, session, store), dict), None)

    d = hm.action_synthesis({"ActivityId": 1, "ItemId": 9, "Num": 4}, p, session, store)
    check("15100 echoes SynthesisNum", d.get("SynthesisNum") == 4, d)
    d = hm.action_synthesis({"ActivityId": 1, "ItemId": 9, "Num": 3}, p, session, store)
    check("15100 accumulates", d.get("SynthesisNum") == 7, d)

    check("16001 binding Status present", "Status" in hm.action_binding_info({}, p, session, store), None)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
