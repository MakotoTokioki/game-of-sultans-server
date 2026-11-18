"""Time-limited events (11901 AwardCfg, 11902 flags, 11903 rank, 11904 claim).

v2 client wiring: modules/_limitTimeMod.lua 687-726 (sortData walks
`#AwardCfg` as a LIST; flags of 0 are truthy and drop rows unless claimed==1),
scene/limitTimeScene.limitTimeSubDialog.lua 603-604 (# processed rows drives
the reward list).  Oracle: recon/v7-capture/v7_actions.jsonl.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers
from gos.defaults import default_player
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player(uid="timelimittest") -> Player:
    return Player(uid=uid, data=default_player(uid),
                  path=Path(tempfile.mkdtemp()) / (uid + ".json"))


def _stub():
    return SimpleNamespace(save=lambda p: None, _players={}), \
        SimpleNamespace(jwt="j", user_id="timelimittest")


def main() -> None:
    check("11902/11903/11904 registered",
          all(a in handlers.HANDLERS for a in ("11902", "11903", "11904")))
    store, session = _stub()

    # 11901: AwardCfg must be a LIST of rows with explicit index.
    cfg = handlers.action_11901({"TimeLimitActivity": [1, 2, 9]}, None, None, None)["TimeLimitCfg"]
    a1 = cfg["1"]["AwardCfg"]
    check("activity 1 AwardCfg is a list with the captured stage-1 row",
          isinstance(a1, list) and len(a1) == 1
          and a1[0]["Award"] == [{"ItemId": 509, "Num": 1}, {"ItemId": 210, "Num": 1},
                                 {"ItemId": 505, "Num": 1}, {"ItemId": 507, "Num": 1}]
          and a1[0]["IsShow"] == 1 and a1[0]["RequireNum"] == 0 and a1[0]["index"] == 1,
          a1)
    a9 = cfg["9"]["AwardCfg"]
    check("activity 9 AwardCfg is a 5-row list with indexes 1..5",
          isinstance(a9, list) and [r["index"] for r in a9] == [1, 2, 3, 4, 5]
          and all(r["Award"] == [{"ItemId": 2, "Num": 50000},
                                 {"ItemId": 201, "Num": 1},
                                 {"ItemId": 203, "Num": 1}] for r in a9), a9)
    check("activities without original data keep an empty AwardCfg list",
          cfg["2"]["AwardCfg"] == [], cfg["2"]["AwardCfg"])

    # 11902: unclaimed flags must be ABSENT (0-padded arrays hide rows).
    p = _player()
    d = handlers.action_11902({}, p, session, store)
    awards = d.get("TimeLimitAwardGet") or {}
    check("11902 has all nine string-keyed ids",
          set(awards) == {str(i) for i in range(1, 10)}, awards)
    check("unclaimed flags are empty lists",
          all(awards[str(i)] == [] for i in range(1, 10)), awards)
    check("11902 progress map has numeric values",
          all(isinstance(v, int) for v in (d.get("TimeLimitComplete") or {}).values()))
    check("11902 json round-trippable", json.loads(json.dumps(d)) == d)

    p.data["TimeLimitAwardGet"] = {"9": [1, 1, 0, 0, 0]}
    check("claimed prefix served as [1,1]",
          handlers.action_11902({}, p, session, store)["TimeLimitAwardGet"]["9"] == [1, 1])
    p.data["TimeLimitAwardGet"] = {"9": [0, 1, 0, 0, 0]}  # legacy hole
    check("legacy hole self-heals: only dense prefix served",
          handlers.action_11902({}, p, session, store)["TimeLimitAwardGet"]["9"] == [])

    # 11903 rank shape.
    rank = handlers.action_11903({"ActivityId": "9"}, p, session, store)
    rows = rank.get("RankList")
    check("11903 RankList is an array of real-player rows",
          isinstance(rows, list)
          and all(set(r) == {"NickName", "Score", "RId"} for r in rows), rows)
    check("11903 RankSelf is a 2-element array",
          isinstance(rank.get("RankSelf"), list) and len(rank["RankSelf"]) == 2,
          rank.get("RankSelf"))
    check("11903 with junk ActivityId is safe",
          isinstance(handlers.action_11903({"ActivityId": "zzz"}, p, session, store)
                     ["RankList"], list))

    # 11904: sequential claims, original awards.
    p.data["TimeLimitAwardGet"] = {"9": [0, 0, 0, 0, 0]}
    check("out-of-order stage refused",
          "_fail" in handlers.action_11904({"ActivityId": "9", "Stage": 2},
                                            p, session, store))
    before = int(p.data["RoleData"]["Silver"])
    a = handlers.action_11904({"ActivityId": "9", "Stage": 1}, p, session, store)
    check("stage 1 grants the original 50k silver",
          int(p.data["RoleData"]["Silver"]) == before + 50000, a)
    check("FinalShowAwardCfg is the captured award",
          a.get("FinalShowAwardCfg") == [{"ItemId": 2, "Num": 50000},
                                         {"ItemId": 201, "Num": 1},
                                         {"ItemId": 203, "Num": 1}], a)
    check("stage 1 persisted, served prefix becomes [1]",
          p.data["TimeLimitAwardGet"]["9"] == [1, 0, 0, 0, 0]
          and handlers.action_11902({}, p, session, store)["TimeLimitAwardGet"]["9"] == [1])
    check("stage 2 claimable after stage 1",
          "_fail" not in handlers.action_11904({"ActivityId": "9", "Stage": 2},
                                                p, session, store))
    check("double claim refused",
          "_fail" in handlers.action_11904({"ActivityId": "9", "Stage": 1},
                                            p, session, store))
    check("out-of-range stage refused",
          "_fail" in handlers.action_11904({"ActivityId": "9", "Stage": 99},
                                            p, session, store))
    check("activity without original awards refused",
          "_fail" in handlers.action_11904({"ActivityId": "2", "Stage": 1},
                                            p, session, store))

    # Activity 1's captured row grants the four v2-present items.
    p1 = _player("timelimit1")
    bag_before = {int(e["ItemId"]): int(e.get("Num") or 0)
                  for e in p1.data.get("RoleItem") or []}
    a = handlers.action_11904({"ActivityId": "1", "Stage": 1}, p1, session, store)
    bag_after = {int(e["ItemId"]): int(e.get("Num") or 0)
                 for e in p1.data.get("RoleItem") or []}
    check("activity 1 grants the four captured v2 items",
          "_fail" not in a
          and all(bag_after.get(i, 0) == bag_before.get(i, 0) + 1
                  for i in (509, 210, 505, 507)), (a.get("FinalShowAwardCfg"), bag_after))
    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
