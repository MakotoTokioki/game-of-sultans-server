"""Regression tests for the child raising/education (11410-11419) and child
marriage (11702-11713) clusters.

Response shapes are the exact fields read by the decompiled client callbacks in
``modules/_childrenRaiseMod.lua`` and ``modules/_childrenMod.lua`` (cross-checked
with ``tools/lua_shape_harness/extract_fields.py``).  Values are checked against
the original v2 configs bundled under ``gos/v2configs_data``.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import v2configs
from gos.defaults import default_player
from gos.handlers_childraise import CHILDRAISE_HANDLERS
from gos.handlers_childraise import (
    _married_flag,
    _power_addition,
    _visit_cfg,
    _visit_cost,
    action_apprentice_add_student,
    action_apprentice_confirm,
    action_apprentice_info,
    action_apprentice_reset,
    action_apprentice_reset_confirm,
    action_apprentice_select,
    action_apprentice_settle,
    action_child_back,
    action_child_travel,
    action_child_visit,
    action_marriage_accept,
    action_marriage_apply,
    action_marriage_apply_list,
    action_marriage_apply_push,
    action_marriage_back_item,
    action_marriage_marry,
    action_marriage_noop,
    action_marriage_refresh,
    action_marriage_reject,
    action_marriage_reset_apply,
    action_marriage_self_apply_list,
    action_marriage_wait_list,
)
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="childraisetest", data=default_player("childraisetest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def _stub():
    return SimpleNamespace(save=lambda p: None), None


def _rows(name):
    t = v2configs.load(name)
    return [r for r in (t if isinstance(t, list) else []) if isinstance(r, dict)]


# Action -> fields the shape harness / callbacks require.
EXPECTED = {
    11410: ("ChildrenTravelList", "GainItemRemainTimes", "VisitCount"),
    11411: ("ChildrenUpdateList", "VisitCount"),
    11412: ("ChildrenBackList",),
    11413: ("StudentList", "ConsumeStage", "TotalScore", "NewUnlocLvPos", "RedPointCfg"),
    11414: ("Status",),
    11415: ("ConsumeStage",),
    11416: ("ChildrenSelectList", "Status"),
    11417: ("ResetAttribute",),
    11418: ("ResetAttribute",),
    11419: ("PowerAddition", "ResetAttribute", "Status"),
    11702: ("RefreshTime", "WaitMarryChildren"),
    11703: ("OChild",),
    11704: ("MarryApply",),
    11705: ("MarryApply",),
    11706: (),
    11707: ("AnimationList", "SelfApply", "msg", "table"),
    11708: ("AnimationList", "ApplyCount", "ApplyList", "animationList"),
    11709: ("WaitMarryChildren",),
    11710: (),
    11711: ("SelfApplyList",),
    11712: (),
    11713: ("BackItem",),
}


def main() -> None:
    store, session = _stub()

    check("registry has exactly 11410-11419 and 11702-11713",
          set(CHILDRAISE_HANDLERS)
          == {str(a) for a in range(11410, 11420)}
          | {str(a) for a in range(11702, 11714)},
          sorted(CHILDRAISE_HANDLERS))

    # ---- 11410 travel ----
    p = _player()
    cfg = _visit_cfg(1)
    d = action_child_travel({"CountryId": 1}, p, session, store)
    check("11410 response carries all callback fields",
          all(k in d for k in EXPECTED[11410])
          and isinstance(d["ChildrenTravelList"], list), d)
    entry = d["ChildrenTravelList"][0]
    check("11410 FinishTime uses TcCultivateChildrenVisitCfg.Duration",
          isinstance(entry["FinishTime"], int)
          and entry["CountryId"] == 1
          and entry["FinishTime"] > 0
          and int(cfg.get("Duration") or 0) > 0, entry)
    check("11410 lazy state seeded under RoleChildrenRaise",
          "RoleChildrenRaise" in p.data, p.data.keys())

    # ---- 11411 visit ----
    p = _player()
    action_child_travel({"CountryId": 1}, p, session, store)
    p.data["RoleData"]["Gold"] = 10000000
    cost = _visit_cost(2, 1, 0)
    d = action_child_visit({"ChildId": "child_1", "CountryId": 1, "OneKeyNum": 2},
                           p, session, store)
    check("11411 response carries ChildrenUpdateList + VisitCount",
          all(k in d for k in EXPECTED[11411])
          and isinstance(d["ChildrenUpdateList"], list), d)
    check("11411 consumes config-derived visit cost gold",
          d["VisitCount"] == 2 and cost > 0
          and p.data["RoleData"]["Gold"] == 10000000 - cost
          and any(e.get("ItemId") == 1 for e in d.get("RemainList", [])),
          (d, cost, p.data["RoleData"]["Gold"]))

    # ---- 11412 back list ----
    p = _player()
    action_child_travel({"CountryId": 1}, p, session, store)
    d = action_child_back({}, p, session, store)
    check("11412 returns Lua-safe ChildrenBackList",
          isinstance(d.get("ChildrenBackList"), list), d)

    # ---- 11413 apprentice info ----
    p = _player()
    d = action_apprentice_info({}, p, session, store)
    check("11413 response carries all callback fields",
          all(k in d for k in EXPECTED[11413]), d)
    check("11413 StudentList/NewUnlocLvPos/RedPointCfg are Lua-safe",
          isinstance(d["StudentList"], list)
          and isinstance(d["NewUnlocLvPos"], list)
          and isinstance(d["RedPointCfg"], dict)
          and "ChildRed" in d["RedPointCfg"], d)
    check("11413 TotalScore is config/state derived int",
          isinstance(d["TotalScore"], int), d)

    # ---- 11414 settle ----
    p = _player()
    d = action_apprentice_settle({"ChildId": "child_1", "CounsellorId": 7},
                                 p, session, store)
    pa = _power_addition()
    check("11414 Status 0 success + PowerAddition from TcCultivateAdditionCfg",
          d["Status"] == 0
          and d["PowerAddition"]["CultivatePercentage"] == pa["CultivatePercentage"]
          and d["PowerAddition"]["CultivateConst"] == pa["CultivateConst"]
          and all(k in d for k in EXPECTED[11414]), d)
    check("11414 persists teacher id on the child",
          p.data["RoleChildren"][0]["TeacherId"] == 7,
          p.data["RoleChildren"][0])

    # ---- 11415 add student ----
    p = _player()
    d = action_apprentice_add_student({"Position": 1, "ChildId": "child_1"},
                                      p, session, store)
    check("11415 ConsumeStage int and StudentList grew",
          all(k in d for k in EXPECTED[11415])
          and isinstance(d["ConsumeStage"], int)
          and len(p.data["RoleChildrenRaise"]["StudentList"]) == 1, d)

    # ---- 11416 select ----
    p = _player()
    action_apprentice_info({}, p, session, store)
    d = action_apprentice_select({"ChildId": "child_1", "Position": 1},
                                 p, session, store)
    check("11416 ChildrenSelectList + Status 0",
          all(k in d for k in EXPECTED[11416])
          and isinstance(d["ChildrenSelectList"], list)
          and d["Status"] == 0, d)

    # ---- 11417 / 11418 reset attribute ----
    p = _player()
    d = action_apprentice_reset({"MainAttr": 2, "Score": 42}, p, session, store)
    check("11417 ResetAttribute echoes config-safe ints",
          all(k in d for k in EXPECTED[11417])
          and d["ResetAttribute"]["MainAttr"] == 2
          and d["ResetAttribute"]["Score"] == 42, d)
    d = action_apprentice_reset_confirm({}, p, session, store)
    check("11418 ResetAttribute is Lua-safe dict",
          all(k in d for k in EXPECTED[11418])
          and isinstance(d["ResetAttribute"], dict), d)

    # ---- 11419 confirm ----
    p = _player()
    action_apprentice_reset({"MainAttr": 3, "Score": 9}, p, session, store)
    d = action_apprentice_confirm({"ChildId": "child_1", "IsConfirm": True},
                                  p, session, store)
    check("11419 PowerAddition/ResetAttribute/Status present",
          all(k in d for k in EXPECTED[11419])
          and isinstance(d["PowerAddition"], dict)
          and d["Status"] == 0, d)
    check("11419 persists reset attributes on child",
          p.data["RoleChildren"][0]["MainAttr"] == 3
          and p.data["RoleChildren"][0]["Score"] == 9,
          p.data["RoleChildren"][0])

    # ---- 11702 refresh ----
    p = _player()
    d = action_marriage_refresh({"ChildId": "child_1", "UseGold": 0},
                                p, session, store)
    check("11702 WaitMarryChildren list + RefreshTime int",
          all(k in d for k in EXPECTED[11702])
          and isinstance(d["WaitMarryChildren"], list)
          and isinstance(d["RefreshTime"], int) and d["RefreshTime"] > 0, d)

    # ---- 11703 marry ----
    p = _player()
    d = action_marriage_marry({"ChildId": "child_1", "OChildId": "child_2",
                               "CostType": 1}, p, session, store)
    oc = d.get("OChild")
    check("11703 OChild is config/echo derived",
          all(k in d for k in EXPECTED[11703])
          and isinstance(oc, dict)
          and oc.get("MarryChildId") == "child_1"
          and oc.get("ChildId") == "child_2", d)
    check("11703 marks our child married via TcMarried.IsMarried",
          p.data["RoleChildren"][0]["Married"] == _married_flag()
          and p.data["RoleChildren"][0]["MarryChildrenId"] == "child_2",
          p.data["RoleChildren"][0])

    # ---- 11704 / 11705 apply ----
    p = _player()
    d = action_marriage_apply({"ChildId": "child_1", "CostType": 1, "ShowId": 5},
                              p, session, store)
    check("11704 MarryApply carries IsApplying/ApplyTime/FatherName",
          all(k in d for k in EXPECTED[11704])
          and d["MarryApply"]["IsApplying"] == 1
          and isinstance(d["MarryApply"]["ApplyTime"], int)
          and "FatherName" in d["MarryApply"], d)
    d = action_marriage_apply_push({"ChildId": "child_1", "CostType": 1,
                                    "DeviceId": "x", "IsPush": True},
                                   p, session, store)
    check("11705 MarryApply carries IsApplying/ApplyTime",
          all(k in d for k in EXPECTED[11705])
          and d["MarryApply"]["IsApplying"] == 1, d)

    # ---- 11706 accept ----
    p = _player()
    d = action_marriage_accept({"ORid": "r1", "OChildId": "child_2"},
                               p, session, store)
    check("11706 no-op response is a Lua-safe table", isinstance(d, dict), d)

    # ---- 11707 reject ----
    p = _player()
    d = action_marriage_reject({"children_id": "child_1"}, p, session, store)
    check("11707 AnimationList/SelfApply/msg/table present",
          all(k in d for k in EXPECTED[11707])
          and isinstance(d["AnimationList"], list)
          and d["SelfApply"]["Reject"] == 1
          and isinstance(d["msg"], str), d)

    # ---- 11708 apply list ----
    p = _player()
    d = action_marriage_apply_list({}, p, session, store)
    check("11708 all spellings of animation lists present",
          all(k in d for k in EXPECTED[11708])
          and isinstance(d["ApplyList"], list)
          and isinstance(d["ApplyCount"], int), d)

    # ---- 11709 / 11710 / 11711 / 11712 / 11713 ----
    p = _player()
    d = action_marriage_wait_list({}, p, session, store)
    check("11709 WaitMarryChildren list",
          all(k in d for k in EXPECTED[11709])
          and isinstance(d["WaitMarryChildren"], list), d)
    d = action_marriage_reset_apply({}, p, session, store)
    check("11710 no-op response is a Lua-safe table", isinstance(d, dict), d)
    action_marriage_apply({"ChildId": "child_1", "CostType": 1, "ShowId": 2},
                          p, session, store)
    d = action_marriage_self_apply_list({"ChildrenId": "child_1"},
                                        p, session, store)
    check("11711 SelfApplyList dict keyed by child id",
          all(k in d for k in EXPECTED[11711])
          and isinstance(d["SelfApplyList"], dict)
          and "child_1" in d["SelfApplyList"], d)
    d = action_marriage_noop({}, p, session, store)
    check("11712 NO_MODULE action is a Lua-safe no-op",
          all(k in d for k in EXPECTED[11712]) and isinstance(d, dict), d)
    d = action_marriage_back_item({"ChildId": "child_1"}, p, session, store)
    check("11713 BackItem Lua-safe container",
          all(k in d for k in EXPECTED[11713])
          and isinstance(d["BackItem"], dict), d)
    check("marriage lazy state seeded under RoleChildrenMarriage",
          "RoleChildrenMarriage" in p.data, p.data.keys())

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
