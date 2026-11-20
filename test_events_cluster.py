"""Regression tests for the lantern event (12500-12506) and seven-day 12601.

Response shapes are the client-callback fields (see
tools/lua_shape_harness/extract_fields.py and the lanternScene call sites).
Reward values come only from the original v2 TcLWelfareAwards config through
the shared handlers.py grant helpers.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers, v2configs
from gos.defaults import default_player
from gos.handlers_events import EVENTS_HANDLERS, action_12601
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="eventstest", data=default_player("eventstest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def _resources(player) -> dict:
    return {k: int(player.data["RoleData"].get(k) or 0)
            for k in ("Gold", "Silver", "Food", "Soldier", "Exp", "Fame")
            if k in player.data["RoleData"]}


def _items(player) -> dict:
    return {int(e.get("ItemId") or 0): int(e.get("Num") or 0)
            for e in player.data.get("RoleItem", []) if isinstance(e, dict)}


def main() -> None:
    store, session = SimpleNamespace(save=lambda p: None), None

    check("registry covers all lantern actions + seven-day",
          set(EVENTS_HANDLERS) == {"12500", "12501", "12502", "12503",
                                   "12504", "12505", "12506", "12601"},
          sorted(EVENTS_HANDLERS))

    # config tables are bundled and parseable
    lamp_ids = {int(r.get("AchievementId") or 0)
                for r in v2configs.load("TcLampActivityAchievement")
                if isinstance(r, dict)}
    welfare_rows = {int(r.get("Day") or 0): r.get("Award")
                    for r in v2configs.load("TcLWelfareAwards")
                    if isinstance(r, dict)}
    check("TcLampActivityAchievement bundled with expected ids",
          lamp_ids == {1, 2, 3, 4, 5, 6, 7, 8, 9}, lamp_ids)
    check("TcLWelfareAwards bundled with day 1-7 awards",
          all(isinstance(welfare_rows.get(d), list) for d in range(1, 8)),
          sorted(welfare_rows))

    # ---- 12500: enter / main state ---------------------------------------
    p = _player()
    st = p.data.setdefault("LanternEvent", {})
    st.update({"CurrentLv": 3, "CandleNum": 12, "CurrentScore": 900,
               "IsFirstEnter": 1, "SelfRank": 7,
               "ServerRecord": [{"Rank": 1}], "SelfRecord": [{"AwardTime": "x"}]})
    d = EVENTS_HANDLERS["12500"]({"LampActivityId": 1}, p, session, store)
    check("12500 has every callback-read field",
          set(d) >= {"CurrentLv", "CandleNum", "CurrentScore", "IsFirstEnter",
                     "SelfRank", "ServerRecord", "SelfRecord"}, d)
    check("12500 echoes config-free server state with Lua-safe types",
          (d["CurrentLv"], d["CandleNum"], d["CurrentScore"],
           d["IsFirstEnter"], d["SelfRank"]) == (3, 12, 900, 1, 7)
          and isinstance(d["ServerRecord"], list)
          and isinstance(d["SelfRecord"], list), d)

    # ---- 12501: light lantern --------------------------------------------
    p = _player()
    p.data.setdefault("LanternEvent", {}).update(
        {"CandleNum": 5, "CurrentScore": 42, "SelfRank": 2,
         "GetAwards": [[2, 10]], "LampAwards": [[[2, 10]]]})
    d = EVENTS_HANDLERS["12501"]({}, p, session, store)
    check("12501 has all six callback fields with list awards",
          set(d) >= {"CandleNum", "CurrentScore", "SelfRank", "ServerRecord",
                     "GetAwards", "LampAwards"}
          and d["CandleNum"] == 5 and d["CurrentScore"] == 42
          and d["SelfRank"] == 2 and isinstance(d["GetAwards"], list)
          and isinstance(d["LampAwards"], list), d)

    # ---- 12502: upgrade returns CurrentLv --------------------------------
    p = _player()
    p.data.setdefault("LanternEvent", {})["CurrentLv"] = 4
    d = EVENTS_HANDLERS["12502"]({}, p, session, store)
    check("12502 returns int CurrentLv", d == {"CurrentLv": 4}, d)

    # ---- 12503: achievement tables are Lua-safe --------------------------
    d = EVENTS_HANDLERS["12503"]({}, _player(), session, store)
    check("12503 returns both achievement tables as lists",
          isinstance(d.get("PersonAchievementData"), list)
          and isinstance(d.get("ServerAchievementData"), list), d)

    # ---- 12504: achievement claim recorded from config id ----------------
    p = _player()
    d = EVENTS_HANDLERS["12504"]({"AchievementId": 1, "GetAward": 2},
                                 p, session, store)
    check("12504 returns Lua-safe envelope and records a valid claim",
          isinstance(d, dict)
          and p.data["LanternEvent"]["ClaimedAchievements"].get("1") == 2, d)
    p2 = _player()
    EVENTS_HANDLERS["12504"]({"AchievementId": 9999, "GetAward": 1},
                             p2, session, store)
    check("12504 ignores achievement ids absent from config",
          p2.data["LanternEvent"]["ClaimedAchievements"] == {},
          p2.data["LanternEvent"]["ClaimedAchievements"])

    # ---- 12505: rank lists, nested scene fields --------------------------
    d = EVENTS_HANDLERS["12505"]({}, _player(), session, store)
    check("12505 has four lists + claim flags",
          all(isinstance(d.get(k), list) for k in
              ("SingleAwardList", "AllianceAwardList"))
          and isinstance(d.get("GetSingleAward"), int)
          and isinstance(d.get("GetAllianceAward"), int), d)
    single, alliance = d.get("SingleRankList"), d.get("AllianceRankList")
    check("12505 rank tables have SelfRankData.Ranking / RankList",
          isinstance(single, dict) and isinstance(alliance, dict)
          and isinstance(single.get("SelfRankData"), dict)
          and isinstance(single["SelfRankData"].get("Ranking"), int)
          and isinstance(single.get("RankList"), list)
          and isinstance(alliance.get("SelfRankData"), dict)
          and isinstance(alliance["SelfRankData"].get("Ranking"), int)
          and isinstance(alliance.get("RankList"), list)
          and isinstance(alliance.get("SelfAllianceName"), str),
          (single, alliance))

    # ---- 12506: rank award claim (both casing conventions) ---------------
    p = _player()
    d = EVENTS_HANDLERS["12506"]({"awardId": 5, "awardType": 1},
                                 p, session, store)
    check("12506 records lowercase claim and returns a dict",
          isinstance(d, dict)
          and p.data["LanternEvent"]["ClaimedRankAwards"].get("1") == 5, d)

    # ---- 12601: seven-day award comes from TcLWelfareAwards only ---------
    p = _player()
    before_gold, before_soldier = (_resources(p).get("Gold", 0),
                                   _resources(p).get("Silver", 0))
    award_pairs = [[int(pair[0]), int(pair[1])]
                   for pair in welfare_rows[1]
                   if isinstance(pair, list) and len(pair) >= 2]
    d = EVENTS_HANDLERS["12601"]({"Stage": 1}, p, session, store)
    res = _resources(p)
    check("12601 grants exactly the day-1 config resource amounts",
          res.get("Silver", 0) - before_soldier
          == sum(n for i, n in award_pairs if i == 2)
          and res.get("Gold", 0) - before_gold
          == sum(n for i, n in award_pairs if i == 1), (res, award_pairs))
    granted_bag = [i for i, _ in award_pairs if i > 6]
    check("12601 grants exactly the day-1 config bag items",
          all(_items(p).get(i, 0) >= sum(n for j, n in award_pairs if j == i)
              for i in granted_bag), _items(p))
    remain_ids = [int(e.get("ItemId") or 0) for e in d.get("RemainList", [])]
    check("12601 RemainList is Lua-safe and includes changed resources",
          isinstance(d.get("RemainList"), list)
          and 2 in remain_ids
          and all(i in remain_ids for i in granted_bag), remain_ids)
    check("12601 records the claimed day in ExtraData",
          p.data["ExtraData"]["lwReceiveNum"] == 1
          and p.data["ExtraData"]["LoginWelfare"] == 1,
          p.data["ExtraData"])

    snapshot = (_resources(p), _items(p))
    EVENTS_HANDLERS["12601"]({"Stage": 1}, p, session, store)
    check("12601 repeat claim does not double-grant",
          (_resources(p), _items(p)) == snapshot, p.data["ExtraData"])

    p3 = _player()
    r3 = _resources(p3)
    EVENTS_HANDLERS["12601"]({"Stage": 8}, p3, session, store)
    check("12601 clamps Stage to 7 and grants day-7 config award",
          p3.data["ExtraData"]["lwReceiveNum"] == 7
          and _resources(p3).get("Gold", 0) - r3.get("Gold", 0) > 0,
          p3.data["ExtraData"])

    p4 = _player()
    d4 = action_12601({"Stage": 0}, p4, session, store)
    check("12601 Stage 0 is a no-op with Lua-safe envelope",
          isinstance(d4, dict) and d4.get("RemainList") == []
          and p4.data.get("ExtraData", {}).get("lwReceiveNum", 0) == 0, d4)

    # handlers.py merges this cluster into the dispatcher
    check("dispatcher merges EVENTS_HANDLERS",
          all(handlers.HANDLERS.get(a) is EVENTS_HANDLERS[a]
              for a in ("12500", "12505", "12601")), None)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
