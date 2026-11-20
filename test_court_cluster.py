"""Regression tests for the court / counsellor challenge (Yamen) cluster,
actions 10801-10816.

Response shapes come from the decompiled client callbacks in
``modules/_courtMod.lua`` (see the module docstring in gos/handlers_court.py).
All balance numbers are read from the original v2 configs (ConfigEnvSet,
TcYamenEffectCfg, TcYamenMultiDefeatPrizeCfg); no value is fabricated.
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
from gos.handlers_court import COURT_HANDLERS, _buy_cfg, _prize_pair
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else str(detail))


def _player() -> Player:
    return Player(uid="courttest", data=default_player("courttest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


# Fields read by the shared L5_1(resp) YamenRecord parser in _courtMod.lua.
RECORD_FIELDS = (
    "YamenScore", "RemainAttackTimes", "InterimAttrPearl", "CommonMultiDefeat",
    "RefreshUnix", "CommonAttackStatus", "CommonCounsellorId",
    "CommonAttackedCounsellors", "CommonBuyCfg", "CommonBuyInterimAttrStatus",
    "CommonCounsellorAttr", "RevengeAttackedCounsellors", "RevengeMultiDefeat",
    "RevengeAttackStatus", "RevengeCounsellorId", "RevengeCounsellorAttr",
    "RevengeBuyInterimAttrStatus", "RevengeBuyCfg",
    "CommonEnemyRandomCounsellors", "CommonEnemyAllCounsellors",
    "RevengeEnemyRandomCounsellors", "RevengeEnemyAllCounsellors",
)


def _assert_record(data, label):
    rec = data.get("YamenRecord")
    check(f"{label}: YamenRecord is a dict", isinstance(rec, dict), data)
    if not isinstance(rec, dict):
        return
    check(f"{label}: every YamenRecord field is present",
          all(f in rec for f in RECORD_FIELDS),
          sorted(set(RECORD_FIELDS) - set(rec)))
    int_fields = (
        "YamenScore", "RemainAttackTimes", "InterimAttrPearl", "CommonMultiDefeat",
        "RefreshUnix", "CommonAttackStatus", "CommonCounsellorId",
        "CommonBuyInterimAttrStatus", "RevengeMultiDefeat", "RevengeAttackStatus",
        "RevengeCounsellorId", "RevengeBuyInterimAttrStatus",
    )
    check(f"{label}: record counters are real ints",
          all(isinstance(rec[f], int) for f in int_fields), rec)
    check(f"{label}: list fields are Lua-safe lists",
          all(isinstance(rec[f], list) for f in (
              "CommonAttackedCounsellors", "CommonEnemyAllCounsellors",
              "RevengeAttackedCounsellors", "RevengeEnemyAllCounsellors",
              "CommonBuyCfg", "RevengeBuyCfg")), rec)
    check(f"{label}: map fields are Lua-safe dicts",
          all(isinstance(rec[f], dict) for f in (
              "CommonCounsellorAttr", "CommonEnemyRandomCounsellors",
              "RevengeCounsellorAttr", "RevengeEnemyRandomCounsellors")), rec)
    check(f"{label}: IsFenshenChanllenge + PopUpState present",
          "IsFenshenChanllenge" in data and "PopUpState" in data, data)


def main() -> None:
    store, session = SimpleNamespace(save=lambda p: None), None

    # ---- registry ---------------------------------------------------------
    expected = {str(a) for a in range(10801, 10817)} - {"10808", "10809"}
    check("registry covers all 14 court actions",
          set(COURT_HANDLERS) == expected, sorted(COURT_HANDLERS))
    check("coordinator auto-merges the cluster into HANDLERS",
          all(HANDLERS.get(a) is fn for a, fn in COURT_HANDLERS.items()),
          [a for a in COURT_HANDLERS if HANDLERS.get(a) is not COURT_HANDLERS[a]])

    # ---- config-derived seeds --------------------------------------------
    env = v2configs.load("ConfigEnvSet")
    free_times = int(env["YamenChallengeFreeTimes"])
    buy_cfg = _buy_cfg()
    gold_effects = [r for r in v2configs.load("TcYamenEffectCfg")
                    if int(r.get("PriceType") or 0) == 1]
    check("buy_cfg comes from TcYamenEffectCfg PriceType==1 rows",
          len(buy_cfg) == len(gold_effects)
          and all([int(r["EffectId"]), int(r["Price"])] in buy_cfg
                  for r in gold_effects), buy_cfg)
    prize = _prize_pair()
    prize_rows = v2configs.load("TcYamenMultiDefeatPrizeCfg")[0]["Prize"]
    check("prize pair comes verbatim from TcYamenMultiDefeatPrizeCfg",
          prize and [prize[0], prize[1]] in [list(p[:2]) for p in prize_rows],
          prize)

    # ---- 10801: full Yamen entry state ------------------------------------
    p = _player()
    d = COURT_HANDLERS["10801"]({}, p, session, store)
    _assert_record(d, "10801")
    check("10801 RemainAttackTimes seeded from ConfigEnvSet",
          d["YamenRecord"]["RemainAttackTimes"] == free_times,
          d["YamenRecord"]["RemainAttackTimes"])
    check("10801 buy_cfg seeded from config",
          d["YamenRecord"]["CommonBuyCfg"] == buy_cfg,
          d["YamenRecord"]["CommonBuyCfg"])
    check("10801 reads every enemy / announcement field",
          all(f in d for f in ("EnemyScore", "EnemyNickname", "EnemyLv",
                               "EnemyAvatar", "EnemyDressList", "EnemyFigure",
                               "AnnouncementMsg", "NotReadMsgCount")), d)
    check("10801 enemy list fields are Lua-safe",
          isinstance(d["EnemyDressList"], list)
          and isinstance(d["EnemyNickname"], str), d)
    check("10801 persists state lazily",
          isinstance(p.data["CourtYamen"]["YamenRecord"], dict),
          p.data.get("CourtYamen"))

    # ---- 10802: only YamenRecord parse ------------------------------------
    d = COURT_HANDLERS["10802"]({}, _player(), session, store)
    _assert_record(d, "10802")

    # ---- 10803 / 10804: battle --------------------------------------------
    for action in ("10803", "10804"):
        d = COURT_HANDLERS[action]({"CounsellorId": 1}, _player(), session, store)
        _assert_record(d, action)
        check(f"{action} reads RandomNum/AllKill/EnemyAttr/EnemyYamenScore",
              all(f in d for f in ("RandomNum", "AllKill", "EnemyAttr",
                                   "EnemyYamenScore"))
              and isinstance(d["EnemyAttr"], dict), d)

    # ---- 10805 / 10806: enemy subsets -------------------------------------
    d = COURT_HANDLERS["10805"]({}, _player(), session, store)
    _assert_record(d, "10805")
    check("10805 reads enemy subset",
          all(f in d for f in ("EnemyScore", "EnemyNickname", "EnemyLv",
                               "EnemyDressList")), d)
    d = COURT_HANDLERS["10806"]({"EnemyRid": 1, "CounsellorId": 2,
                                 "ChallengeType": 1, "IsFenshen": False},
                                _player(), session, store)
    _assert_record(d, "10806")
    check("10806 reads EnemyScore / EnemyNickname",
          "EnemyScore" in d and "EnemyNickname" in d, d)

    # ---- 10807: config-derived multi-defeat award -------------------------
    p = _player()
    d = COURT_HANDLERS["10807"]({}, p, session, store)
    remain = d.get("RemainList")
    check("10807 RemainList[1] is a Lua-safe award entry",
          isinstance(remain, list) and len(remain) >= 1
          and isinstance(remain[0], dict)
          and "ItemId" in remain[0] and "Num" in remain[0], d)
    check("10807 grants exactly the config prize item/count",
          int(remain[0]["ItemId"]) == prize[0]
          and int(remain[0]["Num"]) >= prize[1], remain)
    bag = {int(e.get("ItemId") or 0): int(e.get("Num") or 0)
           for e in p.data.get("RoleItem", [])}
    check("10807 persists the granted bag item", bag.get(prize[0], 0) >= prize[1], bag)

    # ---- 10810: unread passthrough ----------------------------------------
    check("10810 returns an empty Lua-safe envelope",
          COURT_HANDLERS["10810"]({}, _player(), session, store) == {}, )

    # ---- 10811: Status / RoleInfo -----------------------------------------
    d = COURT_HANDLERS["10811"]({"ShowId": 1}, _player(), session, store)
    check("10811 RoleInfo is a dict and Status an int",
          isinstance(d.get("RoleInfo"), dict) and isinstance(d.get("Status"), int), d)

    # ---- 10812: only YamenRecord parse ------------------------------------
    d = COURT_HANDLERS["10812"]({}, _player(), session, store)
    _assert_record(d, "10812")

    # ---- 10813: ranking ----------------------------------------------------
    d = COURT_HANDLERS["10813"]({}, _player(), session, store)
    check("10813 YamenScoreRank is a list with all required keys",
          isinstance(d.get("YamenScoreRank"), list)
          and all(isinstance(x, dict) and {"YamenScore", "Nickname", "DressList",
                                           "RankRid", "TitleId"} <= set(x)
                  for x in d["YamenScoreRank"]), d)
    check("10813 reads Place / Nickname / Score",
          all(f in d for f in ("Place", "Nickname", "Score")), d)

    # ---- 10814: messages ---------------------------------------------------
    d = COURT_HANDLERS["10814"]({}, _player(), session, store)
    check("10814 reads DefendMsg / EnemyMsg as strings",
          isinstance(d.get("DefendMsg"), str) and isinstance(d.get("EnemyMsg"), str), d)

    # ---- 10815: buy-effect battle envelope ---------------------------------
    p = _player()
    d = COURT_HANDLERS["10815"]({"BuyEffect": 2}, p, session, store)
    _assert_record(d, "10815")
    check("10815 reads EnemyScore and has Lua-safe RemainList",
          "EnemyScore" in d and isinstance(d.get("RemainList"), list), d)
    check("10815 persists the requested effect choice",
          p.data["CourtYamen"].get("LastBuyEffect") == 2,
          p.data["CourtYamen"].get("LastBuyEffect"))
    check("10815 grants no fabricated award",
          d.get("RemainList") == [], d.get("RemainList"))

    # ---- 10816: popup state round-trip ------------------------------------
    p = _player()
    d = COURT_HANDLERS["10816"]({"PopUpState": 1}, p, session, store)
    check("10816 returns a Lua-safe empty envelope", isinstance(d, dict) and d == {}, d)
    check("10816 persists PopUpState",
          p.data["CourtYamen"]["PopUpState"] == 1,
          p.data["CourtYamen"].get("PopUpState"))
    d = COURT_HANDLERS["10801"]({}, p, session, store)
    check("10816 popup state is returned by 10801", d["PopUpState"] == 1,
          d["PopUpState"])

    print(f"{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
