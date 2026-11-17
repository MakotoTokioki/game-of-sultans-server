"""Regression tests for the daily sign-in / Divination guard (11501/11502).

Client evidence:
  modules/_signMod.lua  init 12-37, getSignDay 41-55, refreshSignTime 57-91,
                        isRedPoint 103-161, action11501/11502 163-275
  scene/signScene.signScene.lua  CheckInRecords 316, record keys 366/411/449
  defaults.py TimeZone: 3 -> UTC+3 game-day boundary

Run: python3 test_signin_daily.py
"""
from __future__ import annotations

import datetime
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from gos import handlers
from gos.defaults import default_player
from gos.store import Player

ok: list[bool] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="signtest", data=default_player("signtest"),
                  path=Path("/tmp/opencode/gos_test_players/signtest.json"))


def _stub():
    return SimpleNamespace(save=lambda p: None), SimpleNamespace(jwt="jwt", user_id="signtest")


def _set_refresh(player: Player, when: datetime.datetime) -> None:
    player.data["CheckInRefreshTime"] = when.strftime(handlers._GAME_DATE_FMT)


def test_first_checkin_succeeds() -> None:
    player = _player()
    store, session = _stub()
    info = handlers.action_sign_info({}, player, session, store)
    refresh = info["CheckInfo"]["CheckInReFreshTime"]
    check("11501 fresh account gets a valid refresh string",
          isinstance(refresh, str) and handlers._game_day_number(refresh) > 0, refresh)
    check("11501 fresh account is collectable (yesterday < today)",
          handlers._game_day_number(refresh) < handlers._game_day_number(
              handlers._game_date_string()), refresh)
    check("11501 CheckInfo.Award is a table (signView pairs() over it)",
          isinstance(info["CheckInfo"].get("Award"), list),
          info["CheckInfo"].get("Award"))

    before_day = int(player.data.get("CheckInDay") or 0)
    before_gold = int(player.data.get("RoleData", {}).get("Gold") or 0)
    d = handlers.action_sign_checkin({}, player, session, store)
    check("11502 grants the v7 daily award (3x60 Gold)",
          int(player.data["RoleData"]["Gold"]) - before_gold == 180,
          (before_gold, player.data["RoleData"]["Gold"]))
    check("11502 Award matches v7 capture",
          d.get("Award") == [{"ItemId": 1, "Num": 60}] * 3, d.get("Award"))
    check("11502 first check-in succeeds",
          "_fail" not in d and d["CheckInDay"] == before_day + 1, d.get("_fail", d.get("CheckInDay")))
    check("11502 stores the refresh with a real UTC+3 day",
          player.data.get("CheckInRefreshTime") == d["CheckInReFreshTime"]
          and handlers._game_day_number(d["CheckInReFreshTime"])
          == handlers._game_day_number(handlers._game_date_string()), d["CheckInReFreshTime"])
    rec = d["CheckInRecord"][0]
    check("record has all scene-read keys with Lua-safe types",
          set(rec) == {"CheckInRefreshTime", "CheckTypeId", "TodayAward", "Day"}
          and isinstance(rec["TodayAward"], list)
          and isinstance(rec["Day"], int), rec)


def test_same_day_repeat_fails_without_mutation() -> None:
    player = _player()
    store, session = _stub()
    first = handlers.action_sign_checkin({}, player, session, store)
    snapshot = {
        "day": player.data.get("CheckInDay"),
        "records": list(player.data.get("CheckInRecord") or []),
        "flags": list(player.data.get("CumulativeList") or []),
        "refresh": player.data.get("CheckInRefreshTime"),
    }
    d = handlers.action_sign_checkin({}, player, session, store)
    check("same-day 11502 returns _fail", "_fail" in d, d)
    check("failure message is clear", "today" in str(d.get("_fail", "")).lower(), d)
    check("CheckInDay unchanged",
          player.data.get("CheckInDay") == snapshot["day"], snapshot["day"])
    check("records unchanged",
          player.data.get("CheckInRecord") == snapshot["records"], snapshot["records"])
    check("cumulative flags unchanged",
          player.data.get("CumulativeList") == snapshot["flags"], snapshot["flags"])
    check("stored refresh unchanged",
          player.data.get("CheckInRefreshTime") == snapshot["refresh"]
          == first["CheckInReFreshTime"], snapshot["refresh"])
    check("failure grants no reward", "RemainList" not in d, d)


def test_next_day_checkin_succeeds() -> None:
    player = _player()
    store, session = _stub()
    handlers.action_sign_checkin({}, player, session, store)
    day_before = int(player.data["CheckInDay"])
    yesterday = handlers._game_time() - datetime.timedelta(days=1)
    _set_refresh(player, yesterday)

    d = handlers.action_sign_checkin({}, player, session, store)
    check("next-day 11502 succeeds", "_fail" not in d, d.get("_fail"))
    check("next-day increments CheckInDay",
          d.get("CheckInDay") == day_before + 1, (day_before, d.get("CheckInDay")))
    check("next-day stores a new refresh",
          handlers._game_day_number(player.data["CheckInRefreshTime"])
          == handlers._game_day_number(handlers._game_date_string()), player.data["CheckInRefreshTime"])


def test_utc3_boundary() -> None:
    # 22:30 UTC == 01:30 next day in UTC+3: a UTC comparison would be wrong.
    ts = int(datetime.datetime(2026, 11, 17, 22, 30, tzinfo=datetime.timezone.utc).timestamp())
    game = handlers._game_date_string(ts)
    check("UTC+3 game day rolls over at 21:00 UTC",
          game.startswith("2026-11-18"), game)
    check("game day number matches the stored string",
          handlers._game_day_number(game) == 20261118, game)


def test_cycle_reset_and_11501_state() -> None:
    player = _player()
    store, session = _stub()
    player.data["CheckInDay"] = 21
    player.data["CumulativeList"] = [1, 1, 1, 1]
    _set_refresh(player, handlers._game_time() - datetime.timedelta(days=1))
    d = handlers.action_sign_checkin({}, player, session, store)
    check("21-day cycle resets cumulative flags",
          d.get("CumulativeList") == [0, 0, 0, 0], d.get("CumulativeList"))
    check("21-day cycle wraps CheckInDay to 22 (cycle day 1)",
          d.get("CheckInDay") == 22 and handlers._sign_day(22) == 1, d.get("CheckInDay"))

    info = handlers.action_sign_info({}, player, session, store)
    ci = info["CheckInfo"]
    check("11501 CheckInfo carries the persisted refresh",
          ci["CheckInReFreshTime"] == player.data["CheckInRefreshTime"], ci)
    check("11501 CheckInfo.CheckInRecords is the persisted list",
          ci["CheckInRecords"] == player.data["CheckInRecord"]
          and len(ci["CheckInRecords"]) >= 1, ci["CheckInRecords"])
    check("11501 records keep all scene-read keys",
          all(set(r) == {"CheckInRefreshTime", "CheckTypeId", "TodayAward", "Day"}
              and isinstance(r["TodayAward"], list) for r in ci["CheckInRecords"]),
          ci["CheckInRecords"])


def test_login_seeds_neutral_refresh() -> None:
    player = _player()
    store, session = _stub()
    data = handlers.action_login({}, player, session, store)
    seeded = datetime.datetime.strptime(data.get("CheckInReFreshTime", "")[:19],
                                        handlers._GAME_DATE_FMT).date()
    expected = (handlers._game_time() - datetime.timedelta(days=1)).date()
    check("login seeds a collectable CheckInReFreshTime", seeded == expected,
          (data.get("CheckInReFreshTime"), expected))


def test_legacy_records_are_normalized() -> None:
    player = _player()
    player.data["CheckInRecord"] = [
        {"Day": 2, "Time": 1794767340},          # pre-guard legacy row
        {"Day": 1, "CheckInRefreshTime": "2026-11-16 12:00:00",
         "CheckTypeId": 8, "TodayAward": [[1, 60]]},
    ]
    store, session = _stub()
    info = handlers.action_sign_info({}, player, session, store)
    records = info["CheckInfo"]["CheckInRecords"]
    check("legacy sign-in rows gain all scene-read keys",
          all({"CheckInRefreshTime", "CheckTypeId", "TodayAward", "Day"} <= set(r)
              for r in records), records)
    check("legacy Time converted to a date string",
          isinstance(records[0]["CheckInRefreshTime"], str)
          and records[0]["CheckTypeId"] == 10
          and records[0]["TodayAward"] == [], records[0])


def main() -> None:
    test_first_checkin_succeeds()
    test_same_day_repeat_fails_without_mutation()
    test_next_day_checkin_succeeds()
    test_utc3_boundary()
    test_cycle_reset_and_11501_state()
    test_legacy_records_are_normalized()
    test_login_seeds_neutral_refresh()
    print("\n%d/%d checks passed" % (sum(ok), len(ok)))
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
