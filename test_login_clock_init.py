"""Targeted test: login snapshot must carry every field a __login listener
reads via getTbData(), so no listener aborts the __login dispatch chain.

Root cause (recon/v2-phone-20261116-210819.log, at EVERY login):
  bad argument #1 to 'pairs' (table expected, got nil)
    at pairs -> dispatchEvent -> callBackFunc
  ?:0: attempt to index upvalue '?' (a nil value)
    in function 'init' -> _listener

Failure chain:
  _rankingMod.init (modules/_rankingMod.lua 21-56) reads
  tbData.RankWorshipTime and isRedPoint (214-246) pairs() over L1_1.  Missing
  table -> nil -> the __login listener aborts.  eventManager.dispatchEvent
  (event/manager.lua 72-113) has NO per-listener pcall, so the remaining
  __login listeners never run -- including tools.timerListener.init
  (tools/timerListener.lua final lines), which creates the 1-second timer
  whose runListener refreshes homeScene.manageAssetsDialog's countdown text.

Run: python3 test_login_clock_init.py
"""
from __future__ import annotations

import datetime
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


def _login(data=None):
    p = Player("clocktest", data if data is not None else default_player("clocktest"),
               Path("/tmp/opencode/gos_test_players/clocktest.json"))
    store = SimpleNamespace(save=lambda x: None)
    return handlers.action_login({}, p, SimpleNamespace(jwt="jwt"), store), p


def _date_number(s: str) -> int:
    """Client dateToNumber(str, 'day') equivalent: YYYYMMDD, or 0 if unparseable."""
    try:
        d = datetime.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        return d.year * 10000 + d.month * 100 + d.day
    except (TypeError, ValueError):
        return 0


def test_fresh_account_rank_worship_time() -> None:
    login, _ = _login()
    rwt = login.get("RankWorshipTime")
    check("login carries RankWorshipTime (guards _rankingMod __login init)",
          isinstance(rwt, dict), rwt)
    check("RankWorshipTime has all three ranking keys",
          isinstance(rwt, dict) and set(rwt) == {"PowerRank", "SectionRank", "IntimacyRank"},
          rwt)
    check("all three values are non-empty date strings (pairs/dateToNumber safe)",
          all(isinstance(v, str) and _date_number(v) > 0 for v in rwt.values()), rwt)
    today = _date_number(handlers._game_date_string())
    check("stored day <= today (client 'new day > stored' lights up once)",
          all(_date_number(v) <= today for v in rwt.values()), (rwt, today))
    check("stored day is a real previous/current day, not epoch 0",
          all(_date_number(v) >= 20200101 for v in rwt.values()), rwt)


def test_legacy_account_healed() -> None:
    data = default_player("clocktest")
    data.pop("RankWorshipTime", None)
    login, p = _login(data)
    rwt = login.get("RankWorshipTime")
    check("legacy account without RankWorshipTime is healed at login",
          isinstance(rwt, dict) and set(rwt) == {"PowerRank", "SectionRank", "IntimacyRank"}
          and all(_date_number(v) > 0 for v in rwt.values()), rwt)
    check("healed value persisted on the player", p.data.get("RankWorshipTime") == rwt,
          p.data.get("RankWorshipTime"))
    # Wrong shapes that would break pairs()/dateToNumber are also repaired.
    for bad in (0, [], "x", {"PowerRank": ""}):
        data = default_player("clocktest")
        data["RankWorshipTime"] = bad
        login, _ = _login(data)
        check("malformed RankWorshipTime %r repaired" % (bad,),
              isinstance(login.get("RankWorshipTime"), dict)
              and all(_date_number(v) > 0 for v in login["RankWorshipTime"].values()),
              login.get("RankWorshipTime"))


def test_all_login_init_fields_present() -> None:
    login, _ = _login()
    # Fields read via getTbData() by __login init listeners.
    for key in ("RankWorshipTime", "CheckInReFreshTime", "CheckInDay",
                "CumulativeList", "SysTime", "TimeZone", "RoleData"):
        check("login carries %s (non-nil for __login listeners)" % key,
              key in login and login[key] is not None, login.get(key))
    check("RoleData.GovEvenId present for _governmentMod.init",
          isinstance(login.get("RoleData"), dict)
          and login["RoleData"].get("GovEvenId") is not None,
          login.get("RoleData", {}).get("GovEvenId"))
    check("SysTime is concrete (clock resync anchor)",
          isinstance(login.get("SysTime"), int) and login["SysTime"] > 0,
          login.get("SysTime"))


def main() -> None:
    test_fresh_account_rank_worship_time()
    test_legacy_account_healed()
    test_all_login_init_fields_present()
    print("\n%d/%d checks passed" % (sum(ok), len(ok)))
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
