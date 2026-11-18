"""In-memory verification of the CompetitionRank / rank-up activity data path.

v2 client 2.0.05, verified against the decomp (no server needed):
  modules/loginMod.lua            __login -> _settingCatchMod.start(Activity)
  modules/_settingCatchMod.lua    start/updateSetting/saveRouteSetting (11901)
  modules/_rankUpActivityMod.lua  updataRankUpActivity / check_activity_state
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from gos import handlers
from gos.defaults import default_player
from gos.store import Player

ok: list[bool] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _fresh_player(data: dict | None = None) -> Player:
    path = Path("/tmp/opencode/gos_test_players/competition_rank_test.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return Player(
        uid="7770001",
        data=data if data is not None else default_player("7770001"),
        path=path,
    )


def _stubs() -> tuple[SimpleNamespace, SimpleNamespace]:
    store = SimpleNamespace(save=lambda player: None)
    session = SimpleNamespace(jwt="jwt")
    return session, store


def test_login_advertises_competition_rank() -> None:
    session, store = _stubs()
    data = handlers.action_login({}, _fresh_player(), session, store)

    activity = data["Activity"]
    expected = [{"Id": 1, "Version": 0}, {"Id": 2, "Version": 0}, {"Id": 3, "Version": 0}]
    check("login Activity.CompetitionRank entries 1-3",
          activity.get("CompetitionRank") == expected, activity.get("CompetitionRank"))
    check("no other activity keys advertised", set(activity.keys()) == {"CompetitionRank"}, activity.keys())
    check("login Activity json-serializable (object, not array)",
          isinstance(json.loads(json.dumps(activity)), dict)
          and json.loads(json.dumps(activity)) == activity)

    # exactly what _settingCatchMod does: entries with Version ~= -1 are queued
    ids = [e["Id"] for e in activity["CompetitionRank"] if e.get("Version") != -1]
    check("client would request ids 1-3", ids == [1, 2, 3], ids)


def test_login_heals_legacy_empty_activity() -> None:
    session, store = _stubs()

    legacy = default_player("7770001")
    legacy["Activity"] = []
    data = handlers.action_login({}, _fresh_player(legacy), session, store)
    check("legacy Activity [] healed to CompetitionRank",
          [e["Id"] for e in data["Activity"]["CompetitionRank"]] == [1, 2, 3],
          data["Activity"])

    # other activity keys survive the heal untouched
    custom = default_player("7770001")
    custom["Activity"] = {"TimeLimitActivity": [{"Id": 1, "Version": 0}]}
    data = handlers.action_login({}, _fresh_player(custom), session, store)
    check("heal keeps existing activity keys",
          data["Activity"].get("TimeLimitActivity") == [{"Id": 1, "Version": 0}]
          and [e["Id"] for e in data["Activity"]["CompetitionRank"]] == [1, 2, 3],
          data["Activity"])


def test_11901_config_shape() -> None:
    session, store = _stubs()
    player = _fresh_player()

    d = handlers.action_11901({"CompetitionRank": [1, 2, 3], "ActId": 11901},
                              player, session, store)
    cfg = d.get("CompetitionRankCfg")
    check("11901 CompetitionRankCfg string-keyed 1/2/3",
          isinstance(cfg, dict) and sorted(cfg.keys()) == ["1", "2", "3"], d)

    expected = {
        "1": {"Id": 1, "ActivityType": 1, "StartTime": "2026-11-16 00:00:00", "EndTime": "2026-11-18 21:59:59"},
        "2": {"Id": 2, "ActivityType": 2, "StartTime": "2026-11-16 00:00:00", "EndTime": "2026-11-18 21:59:59"},
        "3": {"Id": 3, "ActivityType": 13, "StartTime": "2026-11-16 00:00:00", "EndTime": "2026-11-21 21:59:59"},
    }
    check("11901 entries = official schedule (Id/ActivityType/StartTime/EndTime)",
          cfg == expected, cfg)
    check("StartTime/EndTime are date strings (dateToSec requires strings)",
          all(isinstance(e["StartTime"], str) and isinstance(e["EndTime"], str)
              and " " in e["StartTime"] and " " in e["EndTime"] for e in cfg.values()), cfg)
    check("unix twins omitted (no v2 CompetitionRank consumer reads them)",
          all(set(e) == {"Id", "ActivityType", "StartTime", "EndTime"} for e in cfg.values()), cfg)
    check("json round-trip keeps string ids",
          json.loads(json.dumps(cfg)) == cfg)

    # the full client flow: login entries -> ids -> 11901 -> response covers
    # every requested id (saveRouteSetting indexes response[name][str(Id)])
    login = handlers.action_login({}, _fresh_player(), session, store)
    ids = [e["Id"] for e in login["Activity"]["CompetitionRank"] if e.get("Version") != -1]
    d = handlers.action_11901({"CompetitionRank": ids}, player, session, store)
    check("11901 answers every login-advertised id",
          d.get("CompetitionRankCfg") is not None
          and all(str(i) in d["CompetitionRankCfg"] for i in ids), d)


def test_11901_bad_requests() -> None:
    session, store = _stubs()
    player = _fresh_player()

    def call(payload: dict) -> dict:
        return handlers.action_11901(payload, player, session, store)

    names = set(handlers._SETTING_CATCH_CFG_NAMES)
    check("empty payload still returns the full config envelope",
          set(call({})) >= names | {"CrossChat"}, call({}))
    check("empty id list still returns the full config envelope",
          set(call({"CompetitionRank": []})) >= names, call({"CompetitionRank": []}))
    check("non-list tolerated", set(call({"CompetitionRank": "CompetitionRank"})) >= names)
    check("empty dict payload tolerated (no crash)", set(call({})) >= names)

    junk = call({"CompetitionRank": [None, "a", 1]})
    check("junk entries filtered, valid id answered",
          list((junk.get("CompetitionRankCfg") or {}).keys()) == ["1"], junk)

    unknown = call({"CompetitionRank": [9]})
    check("unknown ids keep the envelope (client indexes response[name][str(Id)])",
          unknown.get("CompetitionRankCfg") == {}
          and set(unknown) >= set(handlers._SETTING_CATCH_CFG_NAMES), unknown)

    check("unknown activity keys still get the full envelope",
          set(call({"Promotions": [1], "PackageActivity": [1]}))
          >= set(handlers._SETTING_CATCH_CFG_NAMES))
    mixed = call({"CompetitionRank": [1, 2, 3], "Promotions": [1]})
    check("CompetitionRank answered alongside unknown keys",
          sorted((mixed.get("CompetitionRankCfg") or {}).keys()) == ["1", "2", "3"], mixed)


def main() -> None:
    check("11901 registered in HANDLERS", "11901" in handlers.HANDLERS, handlers.HANDLERS.get("11901"))
    test_login_advertises_competition_rank()
    test_login_heals_legacy_empty_activity()
    test_11901_config_shape()
    test_11901_bad_requests()
    print("\n%d/%d checks passed" % (sum(ok), len(ok)))
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
