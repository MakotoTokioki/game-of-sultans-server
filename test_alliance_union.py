"""Union create/join/rank regressions (11602/11603/11607/11617/11629).

Device logcat 2026-11-18 13:23: the union page crashed because 11617 lacked
AllianceLvRank, 11603 answered a no-alliance envelope whose AllianceLv 0 made
allianceJoinedDialog index filterTcData(...).TopMemberLimit on nil, and
ContributionTime 0 crashed dateToNumber with "arithmetic on field 'year'".
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers
from gos.defaults import default_player
from gos.store import Player

ok = []
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def player_of(uid):
    return Player(uid=uid, data=default_player(uid),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def main():
    tmp = Path(tempfile.mkdtemp())
    handlers._alliance_reset(tmp / "alliances.json")
    store = SimpleNamespace(save=lambda p: None)
    session = SimpleNamespace(jwt="j", user_id="u")

    p1 = player_of("unionleader")
    d = handlers.dispatch("11602", {"Name": "Test", "FlagId": 1}, p1, session, store)
    check("11602 create succeeds", d.get("AlreadyJoin") == 0, d)
    a = d.get("AllianceData") or {}
    check("11602 AllianceData present with truthy AllianceId",
          isinstance(a, dict) and isinstance(a.get("AllianceId"), int)
          and a["AllianceId"] > 0, a)
    check("11602 creator is leader (Position 4)", a.get("Position") == 4, a)
    check("11602 AllianceLv >= 1 (TcAllianceLvCfg row exists)",
          a.get("AllianceLv") == 1, a)
    check("11602 ContributionTime is a dateToNumber-safe string",
          isinstance(a.get("ContributionTime"), str)
          and bool(DATE_RE.match(a["ContributionTime"])), a)
    check("11602 omits date landmines",
          "QuitTime" not in a and "LastExchangeTime" not in a, a)
    check("11602 json round-trips", json.loads(json.dumps(d)) == d)
    guid = a["AllianceGuid"]

    check("11602 second create returns AlreadyJoin 1",
          handlers.dispatch("11602", {"Name": "Again"}, p1, session, store)
          .get("AlreadyJoin") == 1)
    check("11602 empty name refuses",
          bool(handlers.dispatch("11602", {"Name": "  "},
                                 player_of("anon"), session, store).get("_fail")))

    d = handlers.dispatch("11601", {}, p1, session, store)
    check("11601 after create returns the union",
          (d.get("AllianceData") or {}).get("AllianceGuid") == guid, d)

    p2 = player_of("joiner")
    d = handlers.dispatch("11603", {}, p2, session, store)
    check("11603 random join joins the available union",
          d.get("IsJoin") == 1 and (d.get("AllianceData") or {}).get("AllianceGuid") == guid, d)
    a = d.get("AllianceData") or {}
    check("11603 member Position 1, count 2, valid date",
          a.get("Position") == 1 and a.get("AllianceMemberNum") == 2
          and isinstance(a.get("ContributionTime"), str)
          and bool(DATE_RE.match(a["ContributionTime"])), a)

    handlers._alliance_reset(tmp / "alliances2.json")
    p3 = player_of("lonely")
    d = handlers.dispatch("11603", {}, p3, session, store)
    check("11603 random join with no union refuses",
          isinstance(d.get("_fail"), str) and bool(d["_fail"]), d)
    check("11603 unknown guid refuses",
          bool(handlers.dispatch("11603", {"AllianceGuid": "nope"},
                                 p3, session, store).get("_fail")))

    handlers._alliance_reset(tmp / "alliances3.json")
    d = handlers.dispatch("11617", {}, player_of("r1"), session, store)
    check("11617 carries AllianceLvRank list (empty is safe)",
          d.get("AllianceLvRank") == [], d)
    lead = player_of("leader2")
    handlers.dispatch("11602", {"Name": "RankMe", "FlagId": 2}, lead, session, store)
    d = handlers.dispatch("11617", {}, lead, session, store)
    rows = d.get("AllianceLvRank") or []
    check("11617 lists the created union with renderer fields",
          len(rows) == 1 and rows[0]["AllianceName"] == "RankMe"
          and rows[0]["AllianceMenNum"] == 1 and rows[0].get("AllianceLv") == 1
          and rows[0].get("HadApply") == 0, d)
    d = handlers.dispatch("11629", {}, lead, session, store)
    check("11629 carries AllianceData rows",
          isinstance(d.get("AllianceData"), list) and len(d["AllianceData"]) == 1, d)

    aid = rows[0]["AllianceId"] if rows else 0
    d = handlers.dispatch("11607", {"AllianceId": aid}, player_of("q"), session, store)
    check("11607 inquires by numeric id",
          d.get("AllianceName") == "RankMe" and d.get("AllianceMenNum") == 1, d)
    check("11607 unknown id refuses",
          bool(handlers.dispatch("11607", {"AllianceId": 999},
                                 player_of("q"), session, store).get("_fail")))

    handlers._alliance_reset(tmp / "alliances4.json")
    d = handlers.dispatch("11601", {}, player_of("fresh"), session, store)
    a = d.get("AllianceData") or {}
    check("11601 no-alliance omits AllianceId (Lua 0 is truthy)",
          "AllianceId" not in a, a)
    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
