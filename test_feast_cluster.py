"""Regression tests for the feast / banquet (dinner) cluster, actions
12400-12467.

Response shapes come from the decompiled client callbacks in
``modules/_feastModOld.lua`` and ``modules/_feastMod.lua`` (see the module
docstring in gos/handlers_feast.py).  All balance numbers are read from the
original v2 configs (ConfigEnvSet, TcDinnerPointExchangeCfg,
TcDinnerRefactorConfig, TcDinnerRefactorFinishAward, TcDinnerRefactorSeatCfg);
no value is fabricated.  Multi-player data is asserted to be Lua-safe and
empty on a single-machine server.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import v2configs
from gos.defaults import default_player
from gos.handlers import HANDLERS
from gos.handlers_feast import FEAST_HANDLERS, _cfg_value, _exchange_rows
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else str(detail))


def _player() -> Player:
    return Player(uid="feasttest", data=default_player("feasttest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


# Exact fields read by the client callbacks (static shape harness).
FIELDS = {
    12400: {"Dinners", "HavingDinnerNum", "OldDinnerCloseTime", "SelfDinner"},
    12401: {"DinnerId"},
    12402: {"DinnerPoint", "GoldRefreshNum", "ItemList", "LastAuthRefreshTime"},
    12403: set(), 12404: set(), 12406: set(), 12407: set(),
    12405: {"DinnerList"},
    12408: {"ItemList"},
    12409: {"RemainScore"},
    12450: {"Dinners", "Famous", "FamousTimes", "FinishData", "IsFamous", "SelfDinner"},
    12451: {"DinnerId"},
    12452: {"Famous", "FamousFeastList", "SelfFeast", "TimeData"},
    12453: {"SelfFeast"},
    12455: {"FinishData"},
    12456: {"LogList", "Reward", "Score", "SeatList", "Status"},
    12457: {"FinishData", "HostList", "InviteStatus", "LogList", "SeatList", "Status"},
    12458: set(), 12459: set(), 12460: set(), 12463: set(),
    12461: {"Status"},
    12462: set(),
    12464: {"DinnerPoint", "GoldRefreshNum", "ItemList", "LastAuthRefreshTime"},
    12465: {"ItemList"},
    12466: {"RemainScore"},
    12467: {"FinishData", "LogList", "Score", "SeatList", "Status"},
}


def main() -> None:
    store = SimpleNamespace(save=lambda p: None)
    session = None

    # ---- registry ---------------------------------------------------------
    check("registry covers exactly the 27 feast actions",
          set(FEAST_HANDLERS) == {str(a) for a in FIELDS}, sorted(FEAST_HANDLERS))
    check("coordinator auto-merges the cluster into HANDLERS",
          all(HANDLERS.get(a) is fn for a, fn in FEAST_HANDLERS.items()),
          [a for a in FEAST_HANDLERS if HANDLERS.get(a) is not FEAST_HANDLERS[a]])

    # ---- config sources ---------------------------------------------------
    env = v2configs.load("ConfigEnvSet")
    check("ConfigEnvSet.DinnerGoldRefreshNum is sourced", env.get("DinnerGoldRefreshNum") == 10,
          env.get("DinnerGoldRefreshNum"))
    check("ConfigEnvSet.DinnerAuthRefreshTimeCd is sourced", env.get("DinnerAuthRefreshTimeCd") == 7200,
          env.get("DinnerAuthRefreshTimeCd"))
    rows = _exchange_rows()
    check("TcDinnerPointExchangeCfg bundled and parsed", len(rows) == 16, len(rows))
    check("TcDinnerRefactorConfig.WiseDinnerLimit is sourced",
          _cfg_value("TcDinnerRefactorConfig", "WiseDinnerLimit") == 5,
          _cfg_value("TcDinnerRefactorConfig", "WiseDinnerLimit"))
    tiers = [r for r in v2configs.load("TcDinnerRefactorFinishAward")
             if r.get("DinnerType") == 1]
    check("TcDinnerRefactorFinishAward bundled and parsed", len(tiers) == 5, len(tiers))

    # ---- every action: exact required fields present + Lua-safe types ----
    player = _player()
    for aid, fields in FIELDS.items():
        resp = FEAST_HANDLERS[str(aid)]({}, player, session, store)
        check(f"{aid}: response is a dict", isinstance(resp, dict), resp)
        if not isinstance(resp, dict):
            continue
        check(f"{aid}: all required fields present",
              fields.issubset(resp), sorted(fields - set(resp)))
        for key in ("Dinners", "DinnerList", "ItemList", "SeatList", "LogList",
                    "FamousFeastList", "Reward"):
            if key in resp:
                check(f"{aid}: {key} is a Lua-safe list", isinstance(resp[key], list),
                      type(resp[key]))
        for key in ("SelfDinner", "Famous", "FinishData", "HostList", "InviteStatus",
                    "SelfFeast", "TimeData"):
            if key in resp:
                check(f"{aid}: {key} is a Lua-safe dict/list",
                      isinstance(resp[key], (dict, list)), type(resp[key]))
        for key in ("Score", "Status", "DinnerId", "DinnerPoint", "GoldRefreshNum",
                    "LastAuthRefreshTime", "FamousTimes", "HavingDinnerNum",
                    "OldDinnerCloseTime", "RemainScore"):
            if key in resp:
                check(f"{aid}: {key} is a real int", isinstance(resp[key], int),
                      (key, resp[key]))

    # ---- multi-player data is empty, never fabricated ---------------------
    check("12400 Dinners is empty", FEAST_HANDLERS["12400"]({}, player, session, store)["Dinners"] == [])
    check("12405 DinnerList is empty", FEAST_HANDLERS["12405"]({}, player, session, store)["DinnerList"] == [])
    check("12457 SeatList/LogList empty", all(
        FEAST_HANDLERS["12457"]({}, player, session, store)[k] == []
        for k in ("SeatList", "LogList")))
    check("12452 FamousFeastList is empty",
          FEAST_HANDLERS["12452"]({}, player, session, store)["FamousFeastList"] == [])

    # ---- config-derived exchange info -------------------------------------
    player_x = _player()
    info = FEAST_HANDLERS["12464"]({}, player_x, session, store)
    check("12464 ItemList has one row per config exchange item",
          len(info["ItemList"]) == len(rows), (len(info["ItemList"]), len(rows)))
    check("12464 ItemList rows are 4 ints from config",
          info["ItemList"][0] == [rows[0]["ItemId"], rows[0]["ConsumePoint"], 1,
                                  rows[0]["WeightItem"]], info["ItemList"][0])
    check("12464 LastAuthRefreshTime starts at 0", info["LastAuthRefreshTime"] == 0)
    check("12464 DinnerPoint starts at 0", info["DinnerPoint"] == 0)

    # refresh bumps the used counter
    FEAST_HANDLERS["12465"]({}, player_x, session, store)
    info2 = FEAST_HANDLERS["12464"]({}, player_x, session, store)
    check("12465 increments GoldRefreshNum", info2["GoldRefreshNum"] == 1, info2)
    check("12465 sets LastAuthRefreshTime", info2["LastAuthRefreshTime"] > 0, info2)

    # ---- exchange grants only config amounts ------------------------------
    player2 = _player()
    st = player2.data.setdefault("Feast", {})
    st["DinnerPoint"] = 1000
    cost = int(rows[0]["ConsumePoint"])
    item_id = int(rows[0]["ItemId"])
    before = int(player2.data["RoleData"].get("Gold") or 0)
    resp = FEAST_HANDLERS["12466"]({"ItemIndex": 0}, player2, session, store)
    check("12466 RemainScore = points - config ConsumePoint",
          resp["RemainScore"] == 1000 - cost, (resp, cost))
    check("12466 returns RemainList", isinstance(resp.get("RemainList"), list), resp)
    check("12466 consumes points in state", st["DinnerPoint"] == 1000 - cost, st)
    if item_id in (1, 2, 3, 4, 5, 6):
        key = {1: "Gold", 2: "Silver", 3: "Food", 4: "Soldier", 5: "Exp", 6: "Fame"}[item_id]
        after = int(player2.data["RoleData"].get(key) or 0)
        check("12466 config resource granted once", after == before + 1, (before, after, item_id))
    else:
        bag = [e for e in player2.data.get("RoleItem", [])
               if int(e.get("ItemId") or 0) == item_id]
        check("12466 config bag item granted once",
              bool(bag) and int(bag[0].get("Num") or 0) == 1, bag)
        check("12466 RemainList carries granted bag item",
              any(e.get("ItemId") == item_id for e in resp["RemainList"]), resp["RemainList"])

    # failed exchange (insufficient points) changes nothing
    resp = FEAST_HANDLERS["12466"]({"ItemIndex": 1}, player2, session, store)
    check("12466 insufficient points leaves RemainScore unchanged",
          resp["RemainScore"] == 1000 - cost, resp)

    # ---- famous / finish award tiers --------------------------------------
    famous = FEAST_HANDLERS["12450"]({}, player, session, store)
    check("12450 FamousTimes from WiseDinnerLimit", famous["FamousTimes"] == 5, famous)
    cfg_wda = _cfg_value("TcDinnerRefactorConfig", "WiseDinnerAward", [])
    check("12450 Famous.WiseKingFashion from config",
          famous["Famous"] == {"WiseKingFashion": cfg_wda}, famous["Famous"])
    check("12450 IsFamous defaults to False", famous["IsFamous"] is False, famous)
    check("12450 FamousTimes is config-derived int", isinstance(famous["FamousTimes"], int))

    reward = FEAST_HANDLERS["12456"]({}, player, session, store)["Reward"]
    first_tier = min(tiers, key=lambda r: int(r["BeginScore"]))
    check("12456 Reward is the lowest config finish tier MasterAward",
          reward == [list(e) for e in first_tier["MasterAward"]], (reward, first_tier))

    host = FEAST_HANDLERS["12457"]({}, player, session, store)["HostList"]
    seat_rows = [r for r in v2configs.load("TcDinnerRefactorSeatCfg")
                 if r.get("DinnerType") == 1 and r.get("SeatType") == 4]
    check("12457 HostList.MaxSeat from SeatType 4 EndIndex",
          host["MaxSeat"] == int(seat_rows[0]["EndIndex"]), host)
    check("12457 HostList has all client-built fields",
          {"IsInviteVip", "DinnerWay", "ShowId", "NickName", "IsFamousFeast",
           "IsPublic", "StartTime", "Score", "IsOpenPush", "MaxSeat", "DinnerId"}.issubset(host),
          host)

    time_data = FEAST_HANDLERS["12452"]({}, player, session, store)["TimeData"]
    check("12452 TimeData.cdTime from ConfigEnvSet",
          time_data["cdTime"] == env.get("DinnerAuthRefreshTimeCd"), time_data)
    check("12452 TimeData.endTime = freshTime + cdTime",
          time_data["endTime"] == time_data["freshTime"] + time_data["cdTime"], time_data)

    # ---- master award grant is config-only and once -----------------------
    player3 = _player()
    resp = FEAST_HANDLERS["12462"]({"IsMasterAward": 1}, player3, session, store)
    check("12462 RemainList present on claim", isinstance(resp.get("RemainList"), list), resp)
    check("12462 RemainList only config item ids",
          all(any(r[0] == e["ItemId"] for r in first_tier["MasterAward"])
              for e in resp["RemainList"]), resp["RemainList"])
    before_count = len(resp["RemainList"])
    resp2 = FEAST_HANDLERS["12462"]({"IsMasterAward": 1}, player3, session, store)
    check("12462 second claim grants nothing", not resp2.get("RemainList"), resp2)
    check("12462 first claim was non-empty", before_count > 0, before_count)

    # ---- lazy state does not mutate defaults ------------------------------
    fresh = default_player("fresh")
    check("handlers do not require a Feast seed in defaults.py", "Feast" not in fresh)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok), "feast cluster checks failed"


if __name__ == "__main__":
    main()
