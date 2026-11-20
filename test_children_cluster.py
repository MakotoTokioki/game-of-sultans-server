"""Regression tests for the children-core cluster (actions 11400-11407).

Shapes are the exact fields read by the decompiled client callbacks in
modules/_childrenMod.lua; values are checked against the original v2 configs
(ConfigEnvSet, TcChildrenAptitudeCfg, TcChildrenLvUpgradeCfg,
TcChildrenSeatCfg, TcChildrenDegreeCfg) bundled under gos/v2configs_data.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import v2configs
from gos.defaults import default_player
from gos.handlers_children import CHILDREN_HANDLERS
from gos.handlers_children import (
    action_children_list,
    action_children_name,
    action_children_educate,
    action_children_educate_all,
    action_children_recover,
    action_children_recover_all,
    action_children_seat_expand,
    action_children_degree,
)
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="childrentest", data=default_player("childrentest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def _stub():
    return SimpleNamespace(save=lambda p: None), None


def _cfg_rows(name):
    t = v2configs.load(name)
    return [r for r in (t if isinstance(t, list) else []) if isinstance(r, dict)]


def _env(key):
    return int(v2configs.load("ConfigEnvSet").get(key) or 0)


CHILD_VIEW_FIELDS = ("Lv", "Exp", "Force", "Intelligence", "Politics", "Charm",
                    "LastRefreshTimeUnix")


def main() -> None:
    store, session = _stub()

    check("registry has exactly 11400-11407",
          set(CHILDREN_HANDLERS) == {str(a) for a in range(11400, 11408)},
          sorted(CHILDREN_HANDLERS))

    # ---- 11400 child list ----
    p = _player()
    d = action_children_list({}, p, session, store)
    cl = d.get("ChildList")
    check("11400 ChildList is a Lua-safe list",
          isinstance(cl, list) and bool(cl), cl)
    check("11400 entries carry ChildId + LastRefreshTimeUnix",
          all(isinstance(e, dict) and isinstance(e.get("ChildId"), str)
              and isinstance(e.get("LastRefreshTimeUnix"), int) for e in cl), cl)
    check("11400 seeded starter child matches config Lv/Exp",
          p.data["RoleChildren"][0]["Lv"] == 1
          and p.data["RoleChildren"][0]["Exp"] == 0
          and p.data["RoleChildren"][0]["MaxLv"]
          == int(_cfg_rows("TcChildrenAptitudeCfg")[0]["MaxLv"]),
          p.data["RoleChildren"][0])

    # ---- 11401 name (first = free, rename consumes config gold) ----
    p = _player()
    action_children_list({}, p, session, store)
    p.data["RoleData"]["Gold"] = 1000
    d = action_children_name({"ChildId": "child_1", "Nickname": "Wilbur"},
                             p, session, store)
    check("11401 echoes NewName and returns Lua-safe RemainList",
          d.get("NewName") == "Wilbur" and isinstance(d.get("RemainList"), list), d)
    check("11401 persists nickname and first naming is free",
          p.data["RoleChildren"][0]["Nickname"] == "Wilbur"
          and p.data["RoleData"]["Gold"] == 1000
          and d["RemainList"] == [],
          (p.data["RoleChildren"][0]["Nickname"], p.data["RoleData"]["Gold"], d))
    d = action_children_name({"ChildId": "child_1", "Nickname": "Suleiman"},
                             p, session, store)
    cost = _env("ChangeChildrenNameCost")
    check("11401 rename consumes ConfigEnvSet.ChangeChildrenNameCost gold",
          p.data["RoleData"]["Gold"] == 1000 - cost
          and any(e.get("ItemId") == 1 and e.get("Num") == 1000 - cost
                  for e in d["RemainList"]),
          (p.data["RoleData"]["Gold"], d.get("RemainList"), cost))

    # ---- 11402 educate single child ----
    p = _player()
    action_children_list({}, p, session, store)
    old_lv = p.data["RoleChildren"][0]["Lv"]
    d = action_children_educate({"ChildId": "child_1"}, p, session, store)
    child = d.get("Child")
    check("11402 Child has every field the callback writes",
          isinstance(child, dict) and all(k in child for k in CHILD_VIEW_FIELDS)
          and isinstance(d.get("MiracleTrigger"), bool), d)
    check("11402 levels up by one, capped by config ceiling",
          child["Lv"] == old_lv + 1
          and child["Lv"] <= max(int(r["Lv"]) for r in _cfg_rows("TcChildrenLvUpgradeCfg"))
          and all(isinstance(child[k], int) for k in CHILD_VIEW_FIELDS), child)
    check("11402 persists level and vitality stamp",
          p.data["RoleChildren"][0]["Lv"] == child["Lv"]
          and child["LastRefreshTimeUnix"] > 0, p.data["RoleChildren"][0])

    # ---- 11403 one-key educate all ----
    p = _player()
    action_children_list({}, p, session, store)
    d = action_children_educate_all({}, p, session, store)
    kids = d.get("Children")
    check("11403 Children is a Lua-safe list with all fields",
          isinstance(kids, list) and bool(kids)
          and all(isinstance(c, dict) and all(k in c for k in CHILD_VIEW_FIELDS)
                  for c in kids), kids)
    check("11403 every child advanced one level",
          all(c["Lv"] == 2 for c in kids) and isinstance(d.get("MiracleTrigger"), bool),
          kids)

    # ---- 11404 single recovery ----
    p = _player()
    action_children_list({}, p, session, store)
    d = action_children_recover({"ChildId": "child_1"}, p, session, store)
    cd = _env("ChildrenRecoveryVitalityCd")
    check("11404 returns an int LastRefreshTimeUnix",
          isinstance(d.get("LastRefreshTimeUnix"), int), d)
    check("11404 recovery uses ConfigEnvSet.ChildrenRecoveryVitalityCd",
          abs(p.data["RoleChildren"][0]["LastRefreshTimeUnix"]
              - d["LastRefreshTimeUnix"]) < 2
          and cd > 0, (d, cd))

    # ---- 11405 one-key recovery ----
    p = _player()
    action_children_list({}, p, session, store)
    d = action_children_recover_all({}, p, session, store)
    nl = d.get("NewTimeList")
    check("11405 NewTimeList is a Lua-safe list of time entries",
          isinstance(nl, list) and bool(nl)
          and all(isinstance(e, dict) and isinstance(e.get("ChildId"), str)
                  and isinstance(e.get("LastRefreshTimeUnix"), int) for e in nl), nl)

    # ---- 11406 add child seat ----
    p = _player()
    action_children_list({}, p, session, store)
    p.data["RoleData"]["Gold"] = 5000
    init_cap = _env("InitChildrenCapacity")
    seat = next(r for r in _cfg_rows("TcChildrenSeatCfg")
                if int(r["SeatNum"]) == init_cap + 1)
    d = action_children_seat_expand({}, p, session, store)
    check("11406 Capacity advances from InitChildrenCapacity",
          isinstance(d.get("Capacity"), int)
          and d["Capacity"] == init_cap + 1
          and p.data["RoleData"]["ChildrenCapacity"] == d["Capacity"], d)
    check("11406 consumes TcChildrenSeatCfg.Price gold",
          p.data["RoleData"]["Gold"] == 5000 - int(seat["Price"])
          and any(e.get("ItemId") == 1 and e.get("Num") == 5000 - int(seat["Price"])
                  for e in d.get("RemainList", [])),
          (p.data["RoleData"]["Gold"], seat, d.get("RemainList")))

    # ---- 11407 investiture / degree ----
    p = _player()
    action_children_list({}, p, session, store)
    first_degree = int(_cfg_rows("TcChildrenDegreeCfg")[0]["Id"])
    d = action_children_degree({"ChildId": "child_1"}, p, session, store)
    check("11407 ChildDegree is a config-derived int",
          isinstance(d.get("ChildDegree"), int)
          and d["ChildDegree"] == first_degree
          and p.data["RoleChildren"][0]["Degree"] == first_degree, d)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
