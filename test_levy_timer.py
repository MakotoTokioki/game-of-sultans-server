"""Regression tests for the levy (Manage Assets) countdown fix.

Client evidence:
  modules/_manageAssetsMod.lua       get_stage_cd (18-37), get_max_GainTimes
                                     (39-58), collect/recover (293-355)
  scene/homeScene.manageAssetsDialog.lua  sceneTimeDataHandler (422-610)
  recon/v7-capture/v7_actions.jsonl  10001 responses (LevyTimeUTC/OverTimes)

Run: python3 test_levy_timer.py
"""
from __future__ import annotations

import math
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


def _player(power: int = 1000, level: int = 1) -> Player:
    data = default_player("levytest")
    data["RoleData"]["Power"] = power
    data["RoleData"]["Lv"] = level
    return Player(uid="levytest", data=data,
                  path=Path("/tmp/opencode/gos_test_players/levytest.json"))


def _stub():
    return SimpleNamespace(save=lambda p: None), SimpleNamespace(jwt="jwt", user_id="levytest")


def _v7_shape(d: dict) -> bool:
    """Official 10001/10002 wire fields mirrored with neutral values."""
    return (
        isinstance(d.get("OverTimes"), list) and len(d["OverTimes"]) == 1
        and isinstance(d.get("EstimatedRevenue"), dict)
        and isinstance(d.get("LevyList"), dict)
        and all(isinstance(d.get(k), int) for k in
                ("LevyNum", "LevyTriggerNum", "LevySoldierNum"))
        and isinstance(d.get("MiracleTrigger"), bool)
    )


def test_stage_cd_formula() -> None:
    # Mirror of _manageAssetsMod.get_stage_cd; verified against the Lua source.
    def client_cd(power: int) -> int:
        return min(math.ceil(power / 10000) * 60, 1800)
    for power in (1, 1000, 10000, 10001, 20000, 300000, 10**9):
        p = _player(power)
        check("stage_cd matches client formula at Power=%d" % power,
              handlers._levy_stage_cd(p) == client_cd(power),
              (handlers._levy_stage_cd(p), client_cd(power)))
    check("stage_cd never zero/negative",
          all(handlers._levy_stage_cd(_player(p)) >= 1 for p in (0, -100, 1)), "")
    check("max gain from TcLvUpgradeCfg Lv1 = 3",
          handlers._levy_max_gain(_player()) == 3,
          handlers._levy_max_gain(_player()))


def test_single_resource_collect_moves_one_index() -> None:
    player = _player()
    store, session = _stub()
    cd = handlers._levy_stage_cd(player)
    base = int(time.time())
    # Two queued ticks, inside the GainTimes=3 window: clamp is a no-op here.
    player.data["RoleData"]["LevyTimeUTC"] = [base - 2 * cd] * 3

    for ctype, idx in ((1, 0), (2, 1), (3, 2)):
        before = list(player.data["RoleData"]["LevyTimeUTC"])
        called = int(time.time())
        d = handlers.action_manage_assets({"CollectType": ctype}, player, session, store)
        after = d["LevyTimeUTC"]
        check("10001 CollectType=%d advances index %d by exactly one cd" % (ctype, idx),
              after[idx] == before[idx] + cd
              and all(after[j] == before[j] for j in range(3) if j != idx),
              (before, after, cd))
        check("10001 CollectType=%d OverTimes = now - new_ts" % ctype,
              abs(d["OverTimes"][0] - (called - after[idx])) <= 2,
              (d["OverTimes"], called, after[idx]))
        check("10001 CollectType=%d keeps v7 wire shape" % ctype, _v7_shape(d), d)


def test_v7_capture_tick_sequence() -> None:
    """Reproduce the three rapid collects from the official capture.

    v7: LevyTimeUTC 1794821561 -> 1794821621 -> 1794821681 (+cd each),
    OverTimes [120] -> [61] -> [2].  A collect must NOT anchor at `now`.
    """
    player = _player()
    store, session = _stub()
    cd = handlers._levy_stage_cd(player)
    now0 = int(time.time())
    player.data["RoleData"]["LevyTimeUTC"] = [now0 - 86400] * 3  # very stale
    stamps = []
    overs = []
    for _ in range(3):
        called = int(time.time())
        d = handlers.action_manage_assets({"CollectType": 1}, player, session, store)
        stamps.append(d["LevyTimeUTC"][0])
        overs.append(d["OverTimes"][0])
        check("v7 collect anchor <= now", stamps[-1] <= called + 1, (stamps[-1], called))
        check("v7 OverTimes matches now - anchor",
              abs(overs[-1] - (called - stamps[-1])) <= 2, (overs[-1], called, stamps[-1]))
    check("three collects advance the anchor by one cd each",
          stamps[1] - stamps[0] == cd and stamps[2] - stamps[1] == cd,
          (stamps, cd))
    check("v7 stale anchor is capped at (GainTimes-1)*cd before first collect",
          abs((stamps[0] - now0) + (handlers._levy_max_gain(player) - 1) * cd) <= 3,
          (stamps[0], now0, cd))
    check("v7 collect leaves the other two resources untouched",
          d["LevyTimeUTC"][1] == now0 - 86400 and d["LevyTimeUTC"][2] == now0 - 86400,
          d["LevyTimeUTC"])


def test_all_collect_and_timestamps_le_now() -> None:
    player = _player()
    store, session = _stub()
    d = handlers.action_manage_assets({"CollectType": "login_manageAssets"},
                                      player, session, store)
    called = int(time.time())
    check("legacy all-collect moves all three to now",
          all(called - 2 <= ts <= called + 1 for ts in d["LevyTimeUTC"]),
          d["LevyTimeUTC"])
    check("legacy all-collect OverTimes is [0]",
          d["OverTimes"] == [0] and _v7_shape(d), d)
    check("stored list equals response list",
          player.data["RoleData"]["LevyTimeUTC"] == d["LevyTimeUTC"],
          player.data["RoleData"]["LevyTimeUTC"])
    player.data["RoleData"]["LevyTimeUTC"] = [called + 5000] * 3
    d = handlers.action_manage_assets({"CollectType": 1}, player, session, store)
    check("future timestamps clamped to <= now",
          all(ts <= called + 2 for ts in d["LevyTimeUTC"]), d["LevyTimeUTC"])


def test_recover_moves_one_index_by_cd() -> None:
    player = _player(power=20000)  # cd = min(ceil(20000/10000)*60, 1800) = 120
    store, session = _stub()
    cd = handlers._levy_stage_cd(player)
    now = int(time.time())
    player.data["RoleData"]["LevyTimeUTC"] = [now - 10] * 3
    called = int(time.time())
    d = handlers.action_recover({"RecoverType": 2, "RecoverTimes": 2}, player, session, store)
    check("10002 moves only RecoverType index back by one cd per tick",
          d["LevyTimeUTC"][1] == now - 10 - 2 * cd
          and d["LevyTimeUTC"][0] == now - 10
          and d["LevyTimeUTC"][2] == now - 10, d["LevyTimeUTC"])
    check("10002 OverTimes matches the recovered anchor",
          abs(d["OverTimes"][0] - (called - d["LevyTimeUTC"][1])) <= 2
          and _v7_shape(d), d)
    player.data["RoleData"]["LevyTimeUTC"] = [now - 10] * 3
    d = handlers.action_recover({"RecoverType": 1, "RecoverTimes": 99}, player, session, store)
    floor = int(time.time()) - handlers._levy_max_gain(player) * cd
    check("10002 clamps to max GainTimes window",
          d["LevyTimeUTC"][0] >= floor, (d["LevyTimeUTC"][0], floor))


def test_lua_countdown_decreases() -> None:
    """Simulate sceneTimeDataHandler (lines 428-493) with a drained queue.

    remainCount = min(floor((now-ts)/cd), maxGainTimes)
    countdown text = formatTime(ts + cd - now)   (while remainCount == 0)
    """
    player = _player()
    cd = handlers._levy_stage_cd(player)
    max_gain = handlers._levy_max_gain(player)
    store, session = _stub()
    # Two queued ticks, then consume both one at a time -> anchor == now.
    player.data["RoleData"]["LevyTimeUTC"] = [int(time.time()) - 2 * cd] * 3
    first = handlers.action_manage_assets({"CollectType": 1}, player, session, store)
    check("10001 Data carries SysTime for the client clock resync",
          isinstance(first.get("SysTime"), int)
          and abs(first["SysTime"] - int(time.time())) <= 2,
          first.get("SysTime"))
    queued = min(math.floor((int(time.time()) - first["LevyTimeUTC"][0]) / cd), max_gain)
    check("first collect preserves queued levies (n-1)",
          queued >= 1, (queued, first["LevyTimeUTC"][0]))
    d = handlers.action_manage_assets({"CollectType": 1}, player, session, store)
    ts = d["LevyTimeUTC"][0]
    now = ts  # queue drained; anchor is the last tick

    def remain_count(t: int) -> int:
        return min(math.floor((t - ts) / cd), max_gain)

    def shown_remaining(t: int) -> int:
        return ts + cd - t if remain_count(t) <= 0 else None

    check("remainCount is 0 once the queue is drained", remain_count(now) <= 0,
          remain_count(now))
    r0, r1, r5 = shown_remaining(now), shown_remaining(now + 1), shown_remaining(now + 5)
    check("countdown text decreases by one each second",
          r0 == cd and r1 == cd - 1 and r5 == cd - 5, (r0, r1, r5))
    check("next tick occurs at ts + stage_cd",
          remain_count(ts + cd - 1) <= 0 and remain_count(ts + cd) >= 1,
          (remain_count(ts + cd - 1), remain_count(ts + cd)))
    check("remainCount saturates at GainTimes, no NaN/inf",
          remain_count(ts + cd * 999) == max_gain
          and all(math.isfinite(x) for x in (r0, r1, r5)), max_gain)


def main() -> None:
    test_stage_cd_formula()
    test_single_resource_collect_moves_one_index()
    test_v7_capture_tick_sequence()
    test_all_collect_and_timestamps_le_now()
    test_recover_moves_one_index_by_cd()
    test_lua_countdown_decreases()
    print("\n%d/%d checks passed" % (sum(ok), len(ok)))
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
