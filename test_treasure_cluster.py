"""Regression tests for treasure (14200-14209) and binding-info extras
(16002-16004), implemented in gos/handlers_treasure.py.

Shapes asserted here are the exact fields the decompiled v2.005 callbacks
read; values are neutral lazy state (the cluster map lists no original v2
config table for treasure or binding). No invented numbers.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos.defaults import default_player
from gos.handlers_treasure import (
    TREASURE_HANDLERS,
    action_binding_status,
    action_treasure_achievements,
    action_treasure_ack,
    action_treasure_award,
    action_treasure_board_award,
    action_treasure_dig,
    action_treasure_history_awards,
    action_treasure_info,
    action_treasure_noop,
    action_treasure_rank_list,
)
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="treasuretest", data=default_player("treasuretest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def _stub():
    return SimpleNamespace(save=lambda p: None), None


def main() -> None:
    store, session = _stub()

    # ---- registration ----------------------------------------------------
    expected_ids = {str(a) for a in range(14200, 14210)} | {"16002", "16003", "16004"}
    check("TREASURE_HANDLERS registers every target id",
          expected_ids <= set(TREASURE_HANDLERS), sorted(TREASURE_HANDLERS))
    check("all registered handlers are callable",
          all(callable(f) for f in TREASURE_HANDLERS.values()))

    # ---- 14200 main info -------------------------------------------------
    p = _player()
    d = action_treasure_info({"ActivityId": 1}, p, session, store)
    info_fields = {"IsNeedDraw", "NeedDrawTimes", "SelfSingleRank", "DiceNum",
                   "AwardDiceNum", "BoardCurrentStatus", "TreasureStatus",
                   "HistoryAwardList", "AwardList"}
    check("14200 exact callback-read fields present", set(d) == info_fields, set(d))
    check("14200 counters are real ints (zero initial state)",
          all(isinstance(d[k], int) for k in ("IsNeedDraw", "NeedDrawTimes",
              "SelfSingleRank", "DiceNum", "AwardDiceNum", "TreasureStatus")), d)
    check("14200 BoardCurrentStatus is a map with Lv/Step",
          isinstance(d["BoardCurrentStatus"], dict)
          and {"Lv", "Step"} <= set(d["BoardCurrentStatus"]), d["BoardCurrentStatus"])
    check("14200 list fields are Lua-safe lists",
          isinstance(d["HistoryAwardList"], list) and isinstance(d["AwardList"], list))
    check("14200 lazy state seeded under Treasure", "Treasure" in p.data)

    # seeded values round-trip
    p.data["Treasure"]["DiceNum"] = 7
    d = action_treasure_info({}, p, session, store)
    check("14200 echoes lazy DiceNum", d["DiceNum"] == 7, d["DiceNum"])

    # ---- 14201 award result ---------------------------------------------
    d = action_treasure_award({"ActivityId": 1, "Tmp": 0}, _player(), session, store)
    award_fields = {"BoardCurrentStatus", "AwardScore", "Info", "SelfSingleRank",
                    "DiceNum", "AwardType", "AwardList", "Awards", "Point", "IsNeedTip"}
    check("14201 exact callback-read fields present", set(d) == award_fields, set(d))
    check("14201 numeric fields are ints",
          all(isinstance(d[k], int) for k in ("AwardScore", "SelfSingleRank",
              "DiceNum", "AwardType", "Point", "IsNeedTip")), d)
    check("14201 nested tables are Lua-safe",
          isinstance(d["BoardCurrentStatus"], dict) and isinstance(d["Info"], dict)
          and isinstance(d["AwardList"], list) and isinstance(d["Awards"], list))

    # ---- 14202 board award ----------------------------------------------
    d = action_treasure_board_award({"ActivityId": 1}, _player(), session, store)
    check("14202 exact callback-read fields present",
          set(d) == {"SelfSingleRank", "BoardCurrentStatus"}, set(d))
    check("14202 BoardCurrentStatus is a map",
          isinstance(d["BoardCurrentStatus"], dict)
          and {"Lv", "Step"} <= set(d["BoardCurrentStatus"]))

    # ---- 14203 dig -------------------------------------------------------
    d = action_treasure_dig({"ActivityId": 1, "TreasureIndex": 3}, _player(), session, store)
    check("14203 exact callback-read fields present",
          set(d) == {"Award", "RemainList"}, set(d))
    check("14203 Award is a map with ItemId/Num",
          isinstance(d["Award"], dict)
          and {"ItemId", "Num"} <= set(d["Award"])
          and d["Award"]["ItemId"] == 0 and d["Award"]["Num"] == 0, d["Award"])
    check("14203 RemainList is an empty list (no fabricated reward)",
          d["RemainList"] == [])

    # ---- 14204 rank lists ------------------------------------------------
    d = action_treasure_rank_list({"ActivityId": 1, "RankType": 2}, _player(), session, store)
    check("14204 exact callback-read fields present",
          set(d) == {"SingleRankList", "AllianceRankList"}, set(d))
    check("14204 rank lists are Lua-safe lists",
          isinstance(d["SingleRankList"], list) and isinstance(d["AllianceRankList"], list))

    # ---- 14205 / 14206 ack ----------------------------------------------
    for aid, fn in ((14205, action_treasure_ack), (14206, action_treasure_ack)):
        d = fn({}, _player(), session, store)
        check(f"{aid} empty Lua table response", d == {}, d)

    # ---- 14207 achievements ---------------------------------------------
    d = action_treasure_achievements({}, _player(), session, store)
    check("14207 exact helper-read fields present",
          set(d) == {"SingleAchievements", "SrvAchievements"}, set(d))
    check("14207 achievement lists are Lua-safe lists",
          isinstance(d["SingleAchievements"], list)
          and isinstance(d["SrvAchievements"], list))

    # ---- 14208 history awards -------------------------------------------
    d = action_treasure_history_awards({"ActivityId": 1}, _player(), session, store)
    check("14208 exact callback-read fields present",
          set(d) == {"Awards", "RemainList"}, set(d))
    check("14208 Awards is a list (indexable [1]/[2]) and RemainList empty",
          isinstance(d["Awards"], list) and len(d["Awards"]) >= 2
          and d["RemainList"] == [], d)

    # ---- 14209 noop ------------------------------------------------------
    d = action_treasure_noop({"ActivityId": 1}, _player(), session, store)
    check("14209 empty Lua table response", d == {}, d)

    # ---- 16002 binding status -------------------------------------------
    p = _player()
    d = action_binding_status({"Uid": "treasuretest"}, p, session, store)
    check("16002 exact callback-read field present", set(d) == {"Status"}, set(d))
    check("16002 Status is a numeric int (default 0)", isinstance(d["Status"], int)
          and d["Status"] == 0, d)
    p.data["RoleBase"]["BindingStatus"] = 2
    d = action_binding_status({}, p, session, store)
    check("16002 echoes stored Status", d["Status"] == 2, d)

    # ---- 16003 / 16004 re-exports (not duplicated) ----------------------
    d = TREASURE_HANDLERS["16003"]({}, _player(), session, store)
    check("16003 re-export returns IsBinding int",
          set(d) == {"IsBinding"} and isinstance(d["IsBinding"], int), d)
    d = TREASURE_HANDLERS["16004"]({}, _player(), session, store)
    check("16004 re-export returns AccountList list",
          set(d) == {"AccountList"} and isinstance(d["AccountList"], list), d)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
