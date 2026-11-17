"""Regression: using v2 book items (205/210/215) from the backpack.

Official v7 10302 for ItemId 210/UseId 26 returns the consumed book followed
by an attribute pill item (1001..1004) with CounsellorId and Num=TcItem.AddNum.
The client then writes that absolute value into RoleCounsellor.PillAdd.
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


def _player():
    return Player(uid="booktest", data=default_player("booktest", "B"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def main():
    store = SimpleNamespace(save=lambda p: None)
    session = SimpleNamespace(jwt="j", user_id="booktest")

    for item_id in (205, 210, 215):
        p = _player()
        handlers._role_item_add(p, item_id, 2)
        cfg = v2configs.item_by_id(item_id)
        before = handlers._role_item_count(p, item_id)
        res = handlers.action_item_use(
            {"ItemId": item_id, "UseNum": 1, "UseId": 1}, p, session, store)
        check(f"{item_id}: no failure", "_fail" not in res, res.get("_fail"))
        remain = res.get("RemainList", [])
        check(f"{item_id}: consumed book is first", remain and remain[0]["ItemId"] == item_id,
              remain[:2])
        pills = [e for e in remain if 1001 <= int(e.get("ItemId") or 0) <= 1004]
        check(f"{item_id}: one attribute pill granted to counsellor 1",
              len(pills) == 1 and pills[0].get("CounsellorId") == 1, pills)
        check(f"{item_id}: pill Num == TcItem.AddNum",
              pills and int(pills[0]["Num"]) == int(cfg.get("AddNum") or 0), pills)
        check(f"{item_id}: book count dropped by one",
              handlers._role_item_count(p, item_id) == before - 1,
              (before, handlers._role_item_count(p, item_id)))
        target = next(c for c in p.data["RoleCounsellor"] if c["CounsellorId"] == 1)
        check(f"{item_id}: PillAdd persisted", any(v > 0 for v in target.get("PillAdd", [])),
              target.get("PillAdd"))

    p = _player()
    handlers._role_item_add(p, 205, 1)
    res = handlers.action_item_use({"ItemId": 205, "UseNum": 1}, p, session, store)
    check("book without UseId is refused", "_fail" in res, res)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
