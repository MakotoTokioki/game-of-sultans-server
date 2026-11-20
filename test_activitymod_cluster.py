"""Regression tests for the generic activityMod.lua cluster.

Actions: 12814/12815, 12901, 17101/17102, 17201-17204, 17301-17305,
17403/17404, 17501-17503, 17601, 18601/18602.

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

from gos import handlers, v2configs
from gos.defaults import default_player
from gos.handlers_activitymod import ACTIVITYMOD_HANDLERS
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="activitymodtest",
                  data=default_player("activitymodtest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


EXPECTED = {
    "12814", "12815", "12901", "17101", "17102",
    "17201", "17202", "17203", "17204",
    "17301", "17302", "17303", "17304", "17305",
    "17403", "17404", "17501", "17502", "17503",
    "17601", "18601", "18602",
}
PAYLOAD = {"ActivityId": 1, "ActivityType": 90000, "RankType": 1}


def main() -> None:
    store, session = SimpleNamespace(save=lambda p: None), None
    fn = ACTIVITYMOD_HANDLERS

    # ---- registry / isolation ---------------------------------------------
    check("registry covers exactly the 22 actions",
          set(fn) == EXPECTED, sorted(set(fn) ^ EXPECTED))
    check("all registry entries are callable", all(callable(v) for v in fn.values()))
    check("coordinator merge wired all actions into handlers.HANDLERS",
          all(handlers.HANDLERS.get(a) is f for a, f in fn.items()),
          [a for a, f in fn.items() if handlers.HANDLERS.get(a) is not f])
    check("no persistent state seeded in defaults.py",
          "ActivityModExtra" not in default_player("x"))

    # ---- config bundles (parsed originals only) ---------------------------
    for name in ("TcCrossServiceActivityCfg", "TcIndianaActivityCfg",
                 "TcLimitedActivity", "TcRankUpActivityCfg",
                 "TcLimitActivityText", "TcValentineAchievementCfg",
                 "TcBoardActivityAchievementCfg", "TcRankUpActivityItem",
                 "TcActivityAward", "TcLampActivityAchievement"):
        rows = v2configs.load(name)
        check(f"config {name} loads non-empty rows",
              isinstance(rows, (list, dict)) and len(rows) > 0,
              type(rows).__name__)

    # ---- 12814 rank overview ---------------------------------------------
    d = fn["12814"](PAYLOAD, _player(), session, store)
    check("12814 HintData/RankData are Lua-safe maps",
          isinstance(d.get("HintData"), dict)
          and isinstance(d.get("RankData"), dict), d)
    check("12814 RankData has all listener-read int fields",
          all(isinstance(d["RankData"].get(k), int)
              for k in ("GetRoleAward", "RoleRank", "RoleScore")), d.get("RankData"))
    check("12814 both rank lists present",
          all(isinstance(d.get(k), dict) and "RankList" in d[k]
              for k in ("SingleRankList", "AllianceRankList")), d)

    # ---- 12815 rank award claim ------------------------------------------
    p = _player()
    before = dict(p.data["RoleData"])
    d = fn["12815"](PAYLOAD, p, session, store)
    check("12815 attaches RemainList with no fabricated grant",
          isinstance(d.get("RemainList"), list) and not d["RemainList"]
          and p.data["RoleData"] == before, (d, before))
    check("12815 lazy state stored under ActivityModExtra",
          "ActivityModExtra" in p.data, p.data.get("ActivityModExtra"))

    # ---- 12901 valentine --------------------------------------------------
    p = _player()
    st = p.data.setdefault("ActivityModExtra", {}).setdefault("activities", {})
    # find the state key chosen by the handler for this payload
    d0 = fn["12901"](PAYLOAD, p, session, store)
    key = next(iter(st))
    st[key].update({"AwardNum": 3, "Likability": 7, "Rank": 2,
                    "FlowerNum": {"1": 4},
                    "WreathInfo": {"FlowerMap": {"5": 2}, "Score": 9,
                                   "HighScoreArea": {"3": 1}, "IsMaster": 1}})
    d = fn["12901"](PAYLOAD, p, session, store)
    check("12901 all extract_fields fields (ActivityCfg intentionally omitted)",
          all(k in d for k in ("AwardNum", "FlowerNum", "Likability",
                               "Rank", "WreathInfo")), d)
    check("12901 ActivityCfg omitted (config absent, guarded client read)",
          "ActivityCfg" not in d, d)
    check("12901 state values read back",
          (d["AwardNum"], d["Likability"], d["Rank"]) == (3, 7, 2), d)
    check("12901 FlowerNum/WreathInfo are Lua-safe maps",
          isinstance(d["FlowerNum"], dict)
          and isinstance(d["WreathInfo"], dict)
          and isinstance(d["WreathInfo"]["FlowerMap"], dict), d)
    check("12901 WreathInfo subfields preserved",
          d["WreathInfo"]["Score"] == 9 and d["WreathInfo"]["IsMaster"] == 1, d)
    check("12901 first call had zero-safe defaults",
          d0["AwardNum"] == 0 and d0["WreathInfo"]["FlowerMap"] == {}, d0)

    # ---- 17101 achievements (config-derived ids) --------------------------
    d = fn["17101"](PAYLOAD, _player(), session, store)
    check("17101 has both achievement buckets",
          isinstance(d.get("PersonalAchievement"), dict)
          and isinstance(d.get("ServerAchievement"), dict), d)
    cfg_ids = {str(r["AchievementId"])
               for r in v2configs.load("TcValentineAchievementCfg")
               if isinstance(r, dict) and r.get("AchievementId")}
    seeded = set(d["PersonalAchievement"]) | set(d["ServerAchievement"])
    check("17101 seeded from TcValentineAchievementCfg ids",
          cfg_ids.issubset(seeded), (cfg_ids, seeded))
    one = next(iter(d["PersonalAchievement"].values()))
    check("17101 rows carry Count/AwardsRecord with zero-safe defaults",
          one["Count"] == 0 and one["AwardsRecord"] == [], one)

    # ---- 17102 claim ------------------------------------------------------
    p = _player()
    d = fn["17102"]({"AchievementId": 4, "StageId": 2, **PAYLOAD},
                    p, session, store)
    check("17102 empty Lua-safe envelope", d == {}, d)
    st = p.data["ActivityModExtra"]["activities"]
    check("17102 records claimed stage in lazy state",
          any(isinstance(a.get("AchievementAwards"), dict)
              and a["AchievementAwards"].get("4") == 2
              for a in st.values() if isinstance(a, dict)), st)

    # ---- 17201-17203 rank queries ----------------------------------------
    for a in ("17201", "17202", "17203"):
        d = fn[a](PAYLOAD, _player(), session, store)
        check(f"{a} rank lists + award status present",
              all(k in d for k in ("SingleRankList", "AllianceRankList",
                                   "SingleAwardStatus", "AllianceAwardStatus")), d)

    # ---- 17204 / 18602 rank award claims ---------------------------------
    for a in ("17204", "18602"):
        d = fn[a](PAYLOAD, _player(), session, store)
        check(f"{a} reward envelope is Lua-safe",
              isinstance(d.get("RemainList"), list), d)

    # ---- 17301 history / 17302-05 ----------------------------------------
    d = fn["17301"](PAYLOAD, _player(), session, store)
    check("17301 ItemList is a list", isinstance(d.get("ItemList"), list), d)
    for a in ("17302", "17303", "17304", "17305"):
        d = fn[a](PAYLOAD, _player(), session, store)
        check(f"{a} absent client action -> safe envelope",
              isinstance(d, dict) and isinstance(d.get("RemainList"), list), d)

    # ---- 17403 / 17404 exchange ------------------------------------------
    d = fn["17403"](PAYLOAD, _player(), session, store)
    check("17403 shop fields present",
          all(k in d for k in ("ItemList", "ItemNum", "HasBuyItem",
                               "Score", "ExchangeCount")), d)
    p = _player()
    d = fn["17404"]({"itemId": 1101, "itemCount": 3, **PAYLOAD},
                    p, session, store)
    check("17404 returns Score/ExchangeCount ints",
          isinstance(d.get("Score"), int) and d.get("ExchangeCount") == 3, d)

    # ---- 17501/17502/17503 shop ------------------------------------------
    d = fn["17501"](PAYLOAD, _player(), session, store)
    check("17501 shop/wreath fields present",
          all(k in d for k in ("ItemList", "ItemNum", "HasBuyItem", "FlowerMap",
                               "Score", "HighScoreArea", "Grade", "IsMaster")), d)
    p = _player()
    d = fn["17502"]({"ItemId": 1101, "ItemCount": 2, **PAYLOAD},
                    p, session, store)
    check("17502 ItemNum is non-negative int",
          isinstance(d.get("ItemNum"), int) and d["ItemNum"] >= 0, d)
    check("17502 attaches bag item count to RemainList",
          any(e.get("ItemId") == 1101 for e in d.get("RemainList", [])), d)
    d = fn["17503"](PAYLOAD, p, session, store)
    check("17503 shares the 17501 shop shape",
          isinstance(d.get("ItemList"), list) and "ItemNum" in d, d)

    # ---- 17601 / 18601 ----------------------------------------------------
    check("17601 empty callback envelope", fn["17601"]({}, _player(), session, store) == {})
    d = fn["18601"](PAYLOAD, _player(), session, store)
    check("18601 SingleRankList present",
          isinstance(d.get("SingleRankList"), dict), d)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
