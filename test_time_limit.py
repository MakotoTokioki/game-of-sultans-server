"""Time-limited events (11902 schedule, 11903 rank, 11904 claim).

Client crash fixed here: limitTimeSubDialog.dataProcess pairs(A1_2[2]) with
no guard (modules/_limitTimeMod.lua 775-776), so 11902 must return a flags
LIST for every advertised id.  Oracle: recon/v7-capture/v7_actions.jsonl.
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
    p = _player()
    store, session = _stub()

    d = handlers.action_11902({}, p, session, store)
    awards = d.get("TimeLimitAwardGet") or {}
    check("11902 has all nine string-keyed ids",
          set(awards) == {str(i) for i in range(1, 10)}, awards)
    check("every award flag is a list (dataProcess pairs safety)",
          all(isinstance(v, list) and all(f in (0, 1) for f in v)
              for v in awards.values()), awards)
    check("activity 9 has five flag slots", len(awards["9"]) == 5, awards["9"])
    check("11902 progress map has numeric values",
          all(isinstance(v, int) for v in (d.get("TimeLimitComplete") or {}).values()),
          d.get("TimeLimitComplete"))
    check("11902 json round-trippable", json.loads(json.dumps(d)) == d)

    p.data["TimeLimitAwardGet"] = {"9": [1, 0, 0, 0, 0]}
    check("11902 reflects persisted claim flags",
          handlers.action_11902({}, p, session, store)["TimeLimitAwardGet"]["9"]
          == [1, 0, 0, 0, 0])

    rank = handlers.action_11903({"ActivityId": "9"}, p, session, store)
    rows = rank.get("RankList")
    check("11903 RankList is an array", isinstance(rows, list), rank)
    check("11903 rows have only client-read fields",
          all(set(r) == {"NickName", "Score", "RId"} for r in rows), rows)
    check("11903 RankSelf is a 2-element array",
          isinstance(rank.get("RankSelf"), list) and len(rank["RankSelf"]) == 2,
          rank.get("RankSelf"))
    check("11903 with junk ActivityId is safe",
          isinstance(handlers.action_11903({"ActivityId": "zzz"}, p, session, store)
                     ["RankList"], list))

    p.data["TimeLimitAwardGet"] = {"9": [0, 0, 0, 0, 0]}
    before_silver = int(p.data["RoleData"]["Silver"])
    a = handlers.action_11904({"ActivityId": "9", "Stage": 2}, p, session, store)
    check("11904 grants the original 50k silver award",
          int(p.data["RoleData"]["Silver"]) == before_silver + 50000,
          (before_silver, p.data["RoleData"]["Silver"]))
    check("11904 FinalShowAwardCfg is the captured award",
          a.get("FinalShowAwardCfg") == [{"ItemId": 2, "Num": 50000},
                                         {"ItemId": 201, "Num": 1},
                                         {"ItemId": 203, "Num": 1}], a)
    check("11904 RemainList has absolute post-claim totals",
          any(r.get("ItemId") == 2 and int(r.get("Num") or 0) == before_silver + 50000
              for r in a.get("RemainList", [])), a.get("RemainList"))
    check("11904 persists the claim flag",
          p.data["TimeLimitAwardGet"]["9"] == [0, 1, 0, 0, 0],
          p.data["TimeLimitAwardGet"]["9"])
    check("double claim refused",
          "_fail" in handlers.action_11904({"ActivityId": "9", "Stage": 2},
                                            p, session, store))
    check("out-of-range stage refused",
          "_fail" in handlers.action_11904({"ActivityId": "9", "Stage": 99},
                                            p, session, store))
    check("activity without original awards refused",
          "_fail" in handlers.action_11904({"ActivityId": "1", "Stage": 1},
                                            p, session, store))
    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
