"""Endgame / payment cluster 19001-19308 (isolated handlers_endgame module).

Checks exact response keys/types, config-derived values, and the
server-authoritative policy: payment/activation actions are no-op stubs that
never grant items or currency.  Run with: python3 test_endgame_cluster.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers, v2configs
from gos.defaults import default_player
from gos.handlers_endgame import (
    ENDGAME_HANDLERS,
    action_19001,
    action_19002,
    action_19201,
    action_19202,
    action_19203,
    action_19204,
    action_19205,
    action_19206,
    action_19301,
    action_19302,
    action_19303,
    action_19304,
    action_19305,
    action_19306,
    action_19307,
    action_19308,
)
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="endgametest", data=default_player("endgametest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def _stub():
    return SimpleNamespace(save=lambda p: None), None


def _keys(d, names):
    return all(k in d for k in names)


def main() -> None:
    store, session = _stub()

    # registry completeness
    expected_ids = {str(i) for i in (
        19001, 19002,
        19201, 19202, 19203, 19204, 19205, 19206,
        19301, 19302, 19303, 19304, 19305, 19306, 19307, 19308,
    )}
    check("ENDGAME_HANDLERS registers all 16 ids",
          set(ENDGAME_HANDLERS) == expected_ids, sorted(ENDGAME_HANDLERS))
    check("all registered handlers are callable and merged into HANDLERS",
          all(callable(f) for f in ENDGAME_HANDLERS.values())
          and all(handlers.HANDLERS.get(k) is v for k, v in ENDGAME_HANDLERS.items()))

    # ---- 19001 RechargeCfg (original config only) ----
    d = action_19001({}, _player(), session, store)
    cfg_rows = [dict(r) for r in v2configs.load("TcRechargeConfig")]
    check("19001 has RechargeCfg list", isinstance(d.get("RechargeCfg"), list), d)
    check("19001 RechargeCfg is verbatim original TcRechargeConfig rows",
          d.get("RechargeCfg") == cfg_rows and cfg_rows, d.get("RechargeCfg"))
    check("19001 rows carry ProductId/Money/Name from config",
          all(_keys(r, ("ProductId", "Money", "Name")) for r in d["RechargeCfg"]),
          d.get("RechargeCfg"))

    # ---- 19002 report (no purchase, no grant) ----
    p = _player()
    before_items = list(p.data.get("RoleItem") or [])
    before_gold = (p.data.get("RoleData") or {}).get("Gold")
    d = action_19002({"ActivityId": 2, "Stage": 3}, p, session, store)
    check("19002 is Lua-safe empty stub", d == {}, d)
    check("19002 records reported stage in lazy state only",
          p.data["EndgameExtra"]["RechargeActivity"]["GetAwardStage"]["2"] == [3],
          p.data.get("EndgameExtra"))
    check("19002 does not grant currency/items",
          p.data.get("RoleItem") == before_items
          and (p.data.get("RoleData") or {}).get("Gold") == before_gold)
    action_19002({"ActivityId": 2, "Stage": 3}, p, session, store)
    check("19002 stage report is idempotent",
          p.data["EndgameExtra"]["RechargeActivity"]["GetAwardStage"]["2"] == [3])

    # ---- 19201 full race state ----
    d = action_19201({"ActivityId": 1}, _player(), session, store)
    need_19201 = ("IsCreateTeam", "SingleScore", "TeamScore", "CurrentPlace",
                  "StageTotalDistance", "Stage", "ItemStream", "HintData")
    check("19201 has all callback-read fields", _keys(d, need_19201), d)
    check("19201 scalars are real ints/bool, tables are Lua-safe containers",
          isinstance(d["IsCreateTeam"], bool)
          and all(isinstance(d[k], int) for k in ("SingleScore", "TeamScore",
                                                  "CurrentPlace", "StageTotalDistance", "Stage"))
          and isinstance(d["ItemStream"], dict) and isinstance(d["HintData"], dict), d)
    check("19201 omits MyTeam (presence would falsely mean 'in team')",
          "MyTeam" not in d, d)

    # ---- 19202 tick ----
    d = action_19202({"ActivityId": 1, "TeamId": 0, "IsRecover": 0}, _player(), session, store)
    check("19202 has callback fields", _keys(d, ("HintData", "LastRecoveryTime",
                                                 "CurrentPlace", "NpcConf")), d)
    check("19202 NpcConf is a table with NpcLane=0 (no AI opponent)",
          isinstance(d["NpcConf"], dict) and d["NpcConf"].get("NpcLane") == 0
          and _keys(d["NpcConf"], ("NpcPlace", "NpcItemConf", "IsNpcBeginRun", "IsNew")),
          d.get("NpcConf"))

    # ---- 19203 / 19204 ----
    for aid, fn in ((19203, action_19203), (19204, action_19204)):
        d = fn({"ActivityId": 1}, _player(), session, store)
        check(f"{aid} HintData-only Lua-safe response",
              list(d) == ["HintData"] and isinstance(d["HintData"], dict), d)

    # ---- 19205 ----
    d = action_19205({"ActivityId": 1}, _player(), session, store)
    check("19205 has HintData/GainItems/GetNpc",
          _keys(d, ("HintData", "GainItems", "GetNpc"))
          and isinstance(d["GainItems"], list) and isinstance(d["GetNpc"], dict)
          and not d["GainItems"] and not d["GetNpc"], d)

    # ---- 19206 ----
    d = action_19206({"ActivityId": 1}, _player(), session, store)
    check("19206 has HintData/ItemStream",
          _keys(d, ("HintData", "ItemStream"))
          and isinstance(d["ItemStream"], dict) and not d["ItemStream"], d)

    # ---- 19301 medal bootstrap ----
    d = action_19301({}, _player(), session, store)
    check("19301 has callback fields", _keys(d, ("BuyTimes", "EliteList",
                                                 "LegendList", "Medal")), d)
    check("19301 Medal/BuyTimes are ints 0 and lists are 16-slot arrays",
          d["Medal"] == 0 and d["BuyTimes"] == 0
          and isinstance(d["EliteList"], list) and len(d["EliteList"]) == 16
          and isinstance(d["LegendList"], list) and len(d["LegendList"]) == 16, d)
    check("19301 includes time window + IsOn for client timer setup",
          _keys(d, ("StartTime", "EndTime", "IsOn")) and d["IsOn"] == 1, d)

    # ---- 19302 / 19303 payment stubs (no grants, no state change) ----
    p = _player()
    snapshot = dict(p.data["EndgameExtra"]) if "EndgameExtra" in p.data else {}
    before_items = list(p.data.get("RoleItem") or [])
    d = action_19302({}, p, session, store)
    check("19302 returns Lua-safe time echo only",
          _keys(d, ("IsOn", "StartTime", "EndTime")), d)
    d3 = action_19303({"Lv": 2}, p, session, store)
    check("19303 returns Lua-safe stub", isinstance(d3, dict) and d3.get("IsOn") == 1, d3)
    check("19302/19303 do NOT activate medal or grant items/currency",
          p.data.get("EndgameExtra", {}).get("OsmanMedal", {}).get("Medal", 0) == 0
          and p.data.get("RoleItem") == before_items, p.data.get("EndgameExtra"))
    check("19302/19303 state unaffected apart from lazy containers",
          all(k not in p.data.get("EndgameExtra", {}).get("OsmanMedal", {})
              for k in ("Medal", "LegendAward", "EliteAward")),
          p.data.get("EndgameExtra"))

    # ---- 19304 task map (config-derived ids) ----
    d = action_19304({"Stage": 1, "needSubmitList": 0}, _player(), session, store)
    check("19304 has TaskMap/SubmitList/FinishTaskList",
          _keys(d, ("TaskMap", "SubmitList", "FinishTaskList"))
          and isinstance(d["TaskMap"], dict), d)
    task_ids = {int(r.get("Id") or 0) for r in v2configs.load("TcGrowthFundTaskCfg")}
    check("19304 TaskMap keys come from TcGrowthFundTaskCfg",
          {int(k) for k in d["TaskMap"]} == task_ids and task_ids, d["TaskMap"])
    check("19304 TaskMap values are Lua-safe task objects",
          all(_keys(v, ("Id", "IsSubmit", "isReach", "Progress"))
              for v in d["TaskMap"].values()), list(d["TaskMap"].values())[:1])

    # ---- 19305 / 19306 / 19307 / 19308 ----
    d = action_19305({"Stage": 1, "Index": 1}, _player(), session, store)
    check("19305 echoes Progress/IsSubmit safely",
          _keys(d, ("Progress", "IsSubmit")) and d["Progress"] == 0 and d["IsSubmit"] == 0, d)
    d = action_19306({"TaskId": 1}, _player(), session, store)
    check("19306 has int Lv/Exp (no invented level-up)",
          _keys(d, ("Lv", "Exp")) and isinstance(d["Lv"], int) and isinstance(d["Exp"], int)
          and d["Lv"] == 1 and d["Exp"] == 0, d)
    d = action_19307({}, _player(), session, store)
    check("19307 HasLegendMedal is 0 (false)", d.get("HasLegendMedal") == 0, d)
    d = action_19308({}, _player(), session, store)
    check("19308 is empty Lua-safe stub", d == {}, d)

    # --- dispatch integration (no key collisions with existing handlers) ---
    p = _player()
    d = handlers.dispatch("19301", {}, p, session, store)
    check("dispatch() reaches the endgame 19301 handler", "Medal" in d, d)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
