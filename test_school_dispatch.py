"""Academy "Dispatch All" (action10907) regression.

schoolScene's BtnOneKey sends ONLY 10907 with an empty request; the lineup is
the StudyArray persisted by 10906.  The 10907 callback appends every response
DataList entry without dedup, so only newly-seated counsellors may be listed.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import defaults, handlers, v2configs
from gos.store import Player

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def make_player(uid):
    return Player(uid=uid, data=defaults.default_player(uid, "DispatchTest"),
                  path=Path(tempfile.mkdtemp()) / f"{uid}.json")


class MemStore:
    def __init__(self):
        self.saves = 0

    def save(self, player):
        self.saves += 1


def main():
    check("10907 registered to study-list handler",
          handlers.HANDLERS.get("10907") is handlers.action_school_study_list)
    check("StudyCd is 10800", int(v2configs.env_value("StudyCd", -1)) == 10800)

    p = make_player("lineup")
    res = handlers.action_school_study({"StudyArray": [2, 3]}, p, None, MemStore())
    check("10906 persists the lineup", p.data["RoleData"]["StudyArray"] == [2, 3], res)

    store = MemStore()
    res = handlers.action_school_study_list({}, p, None, store)
    dl = res.get("DataList")
    check("dispatch seats the lineup", len(dl) == 2, dl)
    check("entries carry CounsellorId/StudyPosition/STUnix",
          {(e["CounsellorId"], e["StudyPosition"]) for e in dl} == {(2, 1), (3, 2)}
          and all(e.get("STUnix", 0) > 0 for e in dl), dl)
    check("seating persisted", store.saves == 1)
    listing = handlers.action_school_list({}, p, None, MemStore())
    check("10905 restores the seats after re-login",
          {(e["CounsellorId"], e["StudyPosition"]) for e in listing["DataList"]}
          == {(2, 1), (3, 2)}, listing)

    store2 = MemStore()
    res2 = handlers.action_school_study_list({}, p, None, store2)
    check("second press is an empty no-op (client appends without dedup)",
          res2.get("DataList") == [] and store2.saves == 0, res2)

    p2 = make_player("mixed")
    handlers.action_school_assign(
        {"PositionAndMinisterId": [{"MinisterId": 2, "Position": 3}]},
        p2, None, MemStore())
    handlers.action_school_study({"StudyArray": [1, 2, 3]}, p2, None, MemStore())
    res = handlers.action_school_study_list({}, p2, None, MemStore())
    check("already-seated counsellor is not re-seated",
          {(e["CounsellorId"], e["StudyPosition"]) for e in res["DataList"]}
          == {(1, 1), (3, 2)}, res)

    p3 = make_player("cap")
    p3.data.setdefault("School", {})["Capacity"] = 2
    handlers.action_school_study({"StudyArray": [1, 2, 3]}, p3, None, MemStore())
    res = handlers.action_school_study_list({}, p3, None, MemStore())
    check("capacity is respected",
          {(e["CounsellorId"], e["StudyPosition"]) for e in res["DataList"]}
          == {(1, 1), (2, 2)} and res["Capacity"] == 2, res)

    p4 = make_player("unknown")
    handlers.action_school_study({"StudyArray": [99]}, p4, None, MemStore())
    check("unknown counsellor is skipped",
          handlers.action_school_study_list({}, p4, None, MemStore())["DataList"] == [])

    print(f"\n{sum(OK)}/{len(OK)} checks passed")
    sys.exit(0 if all(OK) else 1)


if __name__ == "__main__":
    main()
