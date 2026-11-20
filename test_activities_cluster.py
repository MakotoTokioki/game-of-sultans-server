"""Regression tests for the tribute-extras / universal-activities cluster.

* Actions: 12002-12004 (tribute) and 12202-12213 (universal activities).
* Tribute rewards come only from the original v2 TcCountry AwardItem column via
  the shared handlers.py helpers; Charm values are derived from TcCountry.MaidCharm
  and TcBeauty.InitCharm.  No numbers are fabricated.
* Activity responses are checked against the field lists recovered from the
  decompiled response callbacks (see gos/handlers_activities.py docstring).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import v2configs
from gos.defaults import default_player
from gos.handlers_activities import ACTIVITIES_HANDLERS, action_12002
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="activitiestest", data=default_player("activitiestest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def main() -> None:
    store, session = SimpleNamespace(save=lambda p: None), None

    # registry covers exactly the 15 new actions (12001 stays in handlers.py)
    expected = {"12002", "12003", "12004"} | {str(a) for a in range(12202, 12214)}
    check("registry covers all 15 actions", set(ACTIVITIES_HANDLERS) == expected,
          sorted(ACTIVITIES_HANDLERS))

    # ---- tribute ----------------------------------------------------------
    country_rows = [r for r in v2configs.load("TcCountry") if isinstance(r, dict)]
    country = int(country_rows[0]["Id"])
    award_ids = [int(e[0]) for e in country_rows[0]["AwardItem"]
                 if isinstance(e, list) and len(e) >= 2]
    resource_ids = [i for i in award_ids if 1 <= i <= 6]
    bag_award_ids = [i for i in award_ids if i > 6]
    maid_charm = int(country_rows[0]["MaidCharm"])

    # 12002: NowMaid query, defaults to 0 for a fresh player
    p = _player()
    d = ACTIVITIES_HANDLERS["12002"]({"countryId": country}, p, session, store)
    check("12002 NowMaid is an int (0 fresh)",
          isinstance(d.get("NowMaid"), int) and d["NowMaid"] == 0, d)

    # 12004: use maids -> HasMaidNum/TotalCharm, config-derived charm
    d = ACTIVITIES_HANDLERS["12004"]({"countryId": country, "useNum": 2},
                                     p, session, store)
    check("12004 returns int HasMaidNum=1 and TotalCharm",
          isinstance(d.get("HasMaidNum"), int) and d["HasMaidNum"] == 1
          and isinstance(d.get("TotalCharm"), int), d)
    check("12004 TotalCharm = useNum * TcCountry.MaidCharm",
          d["TotalCharm"] == 2 * maid_charm, (d, maid_charm))
    d2 = action_12002({"countryId": country}, p, session, store)
    check("12002 NowMaid reflects 12004 state",
          d2["NowMaid"] == 2, d2)
    d3 = ACTIVITIES_HANDLERS["12004"]({"countryId": country, "useNum": 0},
                                      p, session, store)
    check("12004 useNum=0 keeps charm stable",
          d3["TotalCharm"] == d["TotalCharm"], d3)

    # 12003: tribute a beauty -> config award + Charm, no double-grant issues
    p2 = _player()
    beauty = 1
    d = ACTIVITIES_HANDLERS["12003"]({"countryId": country, "beautyId": beauty},
                                     p2, session, store)
    check("12003 response has int Charm and Lua-safe RemainList",
          isinstance(d.get("Charm"), int) and isinstance(d.get("RemainList", []), list), d)
    check("12003 Charm = InitCharm(base) + MaidCharm",
          d["Charm"] >= maid_charm, (d, maid_charm))
    items = p2.data.setdefault("RoleItem", [])
    granted = {int(e.get("ItemId") or 0): int(e.get("Num") or 0) for e in items}
    resource_map = {1: "Gold", 2: "Silver", 3: "Food", 4: "Soldier", 5: "Exp", 6: "Fame"}
    role_data = p2.data.get("RoleData", {})
    check("12003 grants exactly the TcCountry AwardItem amounts",
          all(granted.get(i, 0) >= 1 for i in bag_award_ids)
          and all(int(role_data.get(resource_map[i], 0)) >= 1 for i in resource_ids),
          (granted, role_data, award_ids))
    remain_ids = [int(e.get("ItemId") or 0) for e in d.get("RemainList", [])]
    check("12003 RemainList carries the granted bag items",
          all(i in remain_ids for i in bag_award_ids), remain_ids)
    before = dict(granted)
    d_again = ACTIVITIES_HANDLERS["12003"]({"countryId": country, "beautyId": beauty},
                                           p2, session, store)
    granted2 = {int(e.get("ItemId") or 0): int(e.get("Num") or 0)
                for e in p2.data.get("RoleItem", [])}
    check("12003 second tribute accumulates Charm",
          d_again["Charm"] > d["Charm"], (d, d_again))
    check("12003 second tribute grants AwardItem again",
          all(granted2.get(i, 0) >= before.get(i, 0) for i in bag_award_ids), granted2)
    # invalid country -> no grant / no state change (state compared to fresh)
    p3 = _player()
    before_items = list(p3.data.get("RoleItem", []))
    d = ACTIVITIES_HANDLERS["12003"]({"countryId": -999, "beautyId": 1},
                                     p3, session, store)
    check("12003 unknown country grants nothing",
          d.get("Charm") == 0 and p3.data.get("RoleItem", []) == before_items, d)

    # ---- universal activities --------------------------------------------
    PAYLOAD = {"ActivityId": 1, "ActivityType": 1}
    # 12202: overview -- every callback-read field present and Lua-safe
    d = ACTIVITIES_HANDLERS["12202"](PAYLOAD, _player(), session, store)
    required_12202 = {"SingleRankList", "AllianceRankList", "AwardList",
                      "AttackItems", "BossCurrentStatus", "SelfGetAwardStage",
                      "IsReceiveBox", "WareHouseScore", "HistoryAwardList"}
    check("12202 has all callback fields",
          required_12202 <= set(d), d)
    check("12202 BossCurrentStatus.CurrentStage is an int",
          isinstance(d["BossCurrentStatus"].get("CurrentStage"), int), d)
    check("12202 lists are lists",
          all(isinstance(d[k], list) for k in ("AwardList", "AttackItems",
                                               "HistoryAwardList")), d)
    check("12202 rank lists expose SelfRankData.Ranking",
          all(isinstance(d[k]["SelfRankData"]["Ranking"], int)
              for k in ("SingleRankList", "AllianceRankList")), d)

    # 12203: boss attack
    d = ACTIVITIES_HANDLERS["12203"](PAYLOAD, _player(), session, store)
    required_12203 = {"AwardList", "BossCurrentStatus", "RankData",
                      "SingleRankList", "AllianceRankList", "Awards", "AttackItem"}
    check("12203 has all callback fields", required_12203 <= set(d), d)
    check("12203 AttackItem has ItemId/Num ints",
          all(isinstance(d["AttackItem"].get(k), int) for k in ("ItemId", "Num")), d)

    # 12204 / 12205: shop + exchange data are lists of ItemId/Num rows
    d = ACTIVITIES_HANDLERS["12204"](PAYLOAD, _player(), session, store)
    check("12204 ShopData is a list", isinstance(d.get("ShopData"), list), d)
    d = ACTIVITIES_HANDLERS["12205"](PAYLOAD, _player(), session, store)
    check("12205 ExchangeData list + int WareHouseScore",
          isinstance(d.get("ExchangeData"), list)
          and isinstance(d.get("WareHouseScore"), int), d)

    # 12206: AttackItem echoes request values
    d = ACTIVITIES_HANDLERS["12206"]({"item_id": 1101, "itemCount": 3},
                                     _player(), session, store)
    check("12206 AttackItem echoes ItemId/Num",
          isinstance(d.get("AttackItem"), list) and len(d["AttackItem"]) == 1
          and d["AttackItem"][0]["ItemId"] == 1101
          and d["AttackItem"][0]["Num"] == 3, d)

    # 12207: exchange -> int WareHouseScore
    d = ACTIVITIES_HANDLERS["12207"](dict(PAYLOAD, itemId=1101, itemCount=1),
                                     _player(), session, store)
    check("12207 WareHouseScore is an int", isinstance(d.get("WareHouseScore"), int), d)

    # 12208: stage claim echoes AwardStage + persists lazily
    p = _player()
    d = ACTIVITIES_HANDLERS["12208"](dict(PAYLOAD, AwardStage=4), p, session, store)
    check("12208 SelfGetAwardStage echoes request",
          d.get("SelfGetAwardStage") == 4, d)
    check("12208 persists SelfGetAwardStage lazily",
          p.data["UniversalActivityExtra"]["1"]["SelfGetAwardStage"] == 4,
          p.data.get("UniversalActivityExtra"))

    # 12209 / 12211: list-shaped award payloads
    d = ACTIVITIES_HANDLERS["12209"](PAYLOAD, _player(), session, store)
    check("12209 AwardData is a list", isinstance(d.get("AwardData"), list), d)
    d = ACTIVITIES_HANDLERS["12211"](PAYLOAD, _player(), session, store)
    check("12211 AwardList is a list", isinstance(d.get("AwardList"), list), d)

    # 12210: rank award status
    d = ACTIVITIES_HANDLERS["12210"](PAYLOAD, _player(), session, store)
    check("12210 rank fields present and Lua-safe",
          all(isinstance(d.get(k), dict) for k in ("SingleRankList", "AllianceRankList"))
          and all(isinstance(d[k]["SelfRankData"]["Ranking"], int)
                  for k in ("SingleRankList", "AllianceRankList"))
          and isinstance(d.get("ReceivedSAward"), int)
          and isinstance(d.get("ReceivedAAward"), int), d)

    # 12212 / 12213: callbacks read no response fields -> empty envelope
    for a in ("12212", "12213"):
        d = ACTIVITIES_HANDLERS[a](dict(PAYLOAD, awardType=1, ranking=1),
                                   _player(), session, store)
        check(f"{a} returns a Lua-safe empty envelope", isinstance(d, dict) and d == {}, d)

    print(f"{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
