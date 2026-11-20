"""Regression tests for the limited-time / cross-service / valentine cluster.

Actions: 10012, 10104, 10105, 11469, 11908, 11909, 11911-11915, 11917, 11918,
12305, 12308, 12309, 12820, 12821, 12902-12909, 17103, 17104, 14301.

Shapes come from the decompiled response callbacks and their scene listeners
(extract_fields.py + manual listener tracing); values come from original v2
configs or from lazy player state.  No numbers are fabricated.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Import handlers FIRST so the coordinator's cluster merge has run by the time
# handlers_limited is imported directly below.
from gos import handlers, v2configs
from gos.defaults import default_player
from gos.handlers_limited import LIMITED_HANDLERS
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="limitedtest",
                  data=default_player("limitedtest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


EXPECTED = {
    "10012", "10104", "10105", "11469",
    "11908", "11909", "11911", "11912", "11913", "11914",
    "11915", "11917", "11918",
    "12305", "12308", "12309",
    "12820", "12821",
    "12902", "12903", "12904", "12905", "12906", "12907", "12908", "12909",
    "17103", "17104", "14301",
}
PAYLOAD = {"ActivityId": 1, "ActivityType": 1, "CounsellorId": 1}


def main() -> None:
    store, session = SimpleNamespace(save=lambda p: None), None
    fn = LIMITED_HANDLERS

    # ---- registry / isolation ---------------------------------------------
    check(f"registry covers exactly the {len(EXPECTED)} actions",
          set(fn) == EXPECTED, sorted(set(fn) ^ EXPECTED))
    check("all registry entries are callable", all(callable(v) for v in fn.values()))
    check("coordinator merge wired all actions into handlers.HANDLERS",
          all(handlers.HANDLERS.get(a) is f for a, f in fn.items()),
          [a for a, f in fn.items() if handlers.HANDLERS.get(a) is not f])
    check("no persistent state seeded in defaults.py",
          "LimitedExtra" not in default_player("x"))

    # ---- config bundles (parsed originals only) ---------------------------
    for name in ("TcCrossServiceActivityCfg", "TcLimitedActivity",
                 "TcIndianaActivityCfg", "TcRankUpActivityCfg", "TcCounsellor",
                 "TcValentineAchievementCfg"):
        rows = v2configs.load(name)
        check(f"config {name} loads non-empty rows",
              isinstance(rows, (list, dict)) and len(rows) > 0,
              type(rows).__name__)

    # ---- 10012 rename -----------------------------------------------------
    p = _player()
    d = fn["10012"]({"Name": "NewName"}, p, session, store)
    check("10012 omits guarded SensitiveWord and persists nickname",
          d == {} and p.data["RoleBase"]["Nickname"] == "NewName", d)

    # ---- 10104 / 10105 ----------------------------------------------------
    d = fn["10104"]({"CounsellorId": 2}, _player(), session, store)
    check("10104 Figure is config-derived int",
          isinstance(d.get("Figure"), int) and d["Figure"] > 0, d)
    d = fn["10105"]({"CounsellorId": 2}, _player(), session, store)
    check("10105 halo fields int + RemainList",
          all(isinstance(d.get(k), int) for k in ("HaloBreakLv", "HaloLv"))
          and isinstance(d.get("RemainList"), list), d)

    # ---- 11469 / 11908 / 11909 -------------------------------------------
    check("11469 empty callback", fn["11469"](PAYLOAD, _player(), session, store) == {})
    d = fn["11908"](PAYLOAD, _player(), session, store)
    check("11908 RemainList envelope", isinstance(d.get("RemainList"), list), d)
    check("11909 empty ping", fn["11909"](PAYLOAD, _player(), session, store) == {})

    # ---- cross-service cluster -------------------------------------------
    for aid in ("11911", "11912", "11918"):
        d = fn[aid](PAYLOAD, _player(), session, store)
        check(f"{aid} award lists / rank rows Lua-safe",
              isinstance(d.get("SelfAwardList"), list)
              and isinstance(d.get("ServerAwardList"), dict)
              and isinstance(d.get("FirstAward"), list)
              and isinstance(d.get("RankList"), list)
              and isinstance(d.get("ReturnList"), list)
              and isinstance(d.get("RemainList"), list), d)
    d = fn["11913"](PAYLOAD, _player(), session, store)
    check("11913 setRankData shape",
          all(k in d for k in ("ReturnList", "ServerRank", "SelfPlace",
                               "SelfServerName", "SelfScore")), d)
    d = fn["11914"](PAYLOAD, _player(), session, store)
    check("11914 RemainList envelope", isinstance(d.get("RemainList"), list), d)
    d = fn["11915"](PAYLOAD, _player(), session, store)
    check("11915 ticket claim envelope",
          isinstance(d.get("RemainList"), list), d)
    check("11917 empty ping", fn["11917"](PAYLOAD, _player(), session, store) == {})

    # ---- chat -------------------------------------------------------------
    check("12305 empty send callback", fn["12305"](PAYLOAD, _player(), session, store) == {})
    d = fn["12308"](PAYLOAD, _player(), session, store)
    check("12308 ShieldList is list", isinstance(d.get("ShieldList"), list), d)
    p = _player()
    p.data.setdefault("LimitedExtra", {}).setdefault("chat", {})["ShieldList"] = [
        {"Rid": "a"}, {"Rid": "b"}]
    d = fn["12309"]({"Rid": "a"}, p, session, store)
    check("12309 removes rid from ShieldList",
          d == {} and [r["Rid"] for r in p.data["LimitedExtra"]["chat"]["ShieldList"]] == ["b"],
          p.data["LimitedExtra"]["chat"])

    # ---- 12820 / 12821 / 17103 / 17104 -----------------------------------
    d = fn["12820"](PAYLOAD, _player(), session, store)
    check("12820 FriendList is list", isinstance(d.get("FriendList"), list), d)
    for aid in ("12821", "17103", "17104"):
        d = fn[aid](PAYLOAD, _player(), session, store)
        check(f"{aid} RemainList envelope", isinstance(d.get("RemainList"), list), d)

    # ---- valentine --------------------------------------------------------
    d = fn["12902"](PAYLOAD, _player(), session, store)
    check("12902 wreath fields Lua-safe",
          all(k in d for k in ("WreathInfo", "NextWreathInfo", "FlowerNum"))
          and isinstance(d["WreathInfo"], dict)
          and all(k in d["WreathInfo"] for k in (
              "FlowerMap", "FlowerNum", "Score", "HighScoreArea", "IsMaster", "Grade")), d)
    d = fn["12903"](PAYLOAD, _player(), session, store)
    check("12903 award/likability/rank ints",
          all(isinstance(d.get(k), int) for k in ("AwardNum", "Likability", "Rank")), d)
    d = fn["12904"](PAYLOAD, _player(), session, store)
    check("12904 FlowerNum map", isinstance(d.get("FlowerNum"), dict), d)
    d = fn["12905"](PAYLOAD, _player(), session, store)
    check("12905 rank lists + received flags",
          all(k in d for k in ("SingleRankList", "AllianceRankList",
                               "ReceivedSAward", "ReceivedAAward"))
          and "RankList" in d["SingleRankList"]
          and "Ranking" in d["SingleRankList"]["SelfRankData"], d)
    d = fn["12906"](PAYLOAD, _player(), session, store)
    check("12906 RoleScore + ItemList",
          isinstance(d.get("RoleScore"), int) and isinstance(d.get("ItemList"), list), d)
    d = fn["12907"]({"ItemCount": 3, **PAYLOAD}, _player(), session, store)
    check("12907 exchange echoes requested count",
          d.get("ExchangeTimes") == 3
          and isinstance(d.get("ExchangeScore"), int)
          and isinstance(d.get("RemainList"), list), d)
    d = fn["12908"](PAYLOAD, _player(), session, store)
    check("12908 RoleScore int", isinstance(d.get("RoleScore"), int), d)
    d = fn["12909"](PAYLOAD, _player(), session, store)
    check("12909 RemainList envelope", isinstance(d.get("RemainList"), list), d)
    p = _player()
    fn["12902"](PAYLOAD, p, session, store)
    check("valentine reuses the ActivityModExtra state slice (shared with 12901)",
          isinstance(p.data.get("ActivityModExtra"), dict), p.data.get("ActivityModExtra"))

    # ---- 14301 ------------------------------------------------------------
    check("14301 empty callback", fn["14301"](PAYLOAD, _player(), session, store) == {})

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
