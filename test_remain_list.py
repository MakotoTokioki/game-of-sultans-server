"""RemainList regression tests (top resource bar refresh, client 2.0.05).

The v2 client refreshes the top gold/silver/food/soldier bar ONLY through
dataNetMod.serverUpToData -> _itemMod.setCurrRewardData -> roleMod.setRoleData,
driven by the response's RemainList ({ItemId, Num} = ABSOLUTE RoleData value).
The 10001/10002/10101 callbacks themselves never touch those keys
(_manageAssetsMod.lua lines 296-341, _ministerMod.lua lines 3095-3115).

These tests call the handlers directly with in-memory state: no network, no
server process, no files written outside the OS temp dir.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers
from gos.defaults import default_player
from gos.store import Player, Session

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def make_player(uid: str = "remain_test") -> Player:
    data = default_player(uid, "RemainTest")
    tmp = Path(tempfile.mkdtemp(prefix="gos_test_")) / f"{uid}.json"
    return Player(uid=uid, data=data, path=tmp)


def make_session(player: Player) -> Session:
    return Session(jwt="test-jwt", user_id=player.uid, sk="",
                   keypair=None, player_id=player.uid, server_id=1, app_id=1020)


class MemStore:
    """Store stand-in: handlers only call store.save(player)."""

    def __init__(self) -> None:
        self.saved: list[str] = []

    def save(self, player: Player) -> None:
        self.saved.append(player.uid)


def remain_map(data: dict) -> dict:
    return {entry["ItemId"]: entry["Num"] for entry in data.get("RemainList", [])}


def test_counsellor_upgrade_deducts_silver_into_remain_list() -> None:
    player = make_player("remain_counsellor")
    player.data["RoleCounsellor"] = [{"CounsellorId": 1, "Lv": 1, "NowSilver": 0}]
    player.data["RoleData"]["Silver"] = 500
    before = remain_map({})
    before.clear()  # no RemainList expected before the action
    store = MemStore()
    d = handlers.action_counsellor_upgrade(
        {"CounsellorId": 1, "IsUpTen": False}, player, make_session(player), store)
    # Lv 1 -> 2 costs COUNSELLOR_LEVEL_SILVER[2-1+... : cost to reach 2] = 100
    spent = 500 - player.data["RoleData"]["Silver"]
    check("10101 deducts silver", spent == 100, f"spent={spent} d={d}")
    rem = remain_map(d)
    check("10101 RemainList Silver == post-spend value",
          rem.get(2) == player.data["RoleData"]["Silver"], f"rem={rem} d={d}")
    check("10101 RemainList Silver < starting 500", rem.get(2, 500) < 500, f"rem={rem}")
    check("10101 keeps CounsellorLv/NowSilver/MiracleTrigger",
          d["CounsellorLv"] == 2 and d["CounsellorSilver"] == 0
          and d["MiracleTrigger"] == 0, d)
    check("10101 RemainList has all present keys 1-5",
          set(rem) == {1, 2, 3, 4, 5}, f"rem={rem}")
    check("10101 saved", "remain_counsellor" in store.saved)


def test_levy_and_recover_carry_remain_list() -> None:
    player = make_player("remain_levy")
    store = MemStore()
    session = make_session(player)

    d = handlers.action_manage_assets(
        {"CollectType": "login_manageAssets"}, player, session, store)
    rem = remain_map(d)
    rd = player.data["RoleData"]
    check("10001 RemainList matches RoleData",
          rem.get(2) == rd["Silver"] and rem.get(3) == rd["Food"]
          and rem.get(4) == rd["Soldier"] and rem.get(1) == rd["Gold"], f"rem={rem} rd={rd}")
    check("10001 keeps LevyTimeUTC + MiracleTrigger",
          len(d["LevyTimeUTC"]) == 3 and d["MiracleTrigger"] == 0, d)
    check("10001 keeps Extra.mainTask", isinstance(d.get("Extra"), dict), d)

    d = handlers.action_recover({"RecoverType": 1, "RecoverTimes": 1},
                                player, session, store)
    rem = remain_map(d)
    check("10002 RemainList matches RoleData",
          rem.get(2) == rd["Silver"] and rem.get(3) == rd["Food"]
          and rem.get(4) == rd["Soldier"], f"rem={rem} rd={rd}")
    check("10002 keeps LevyTimeUTC", len(d["LevyTimeUTC"]) == 3, d)


def test_remain_list_absolute_and_coerced() -> None:
    """Num must be the absolute current value, ints only, missing -> 0."""
    player = make_player("remain_shape")
    player.data["RoleData"]["Silver"] = "12345"  # string is coerced to int
    player.data["RoleData"]["Exp"] = None        # missing value -> 0
    player.data["RoleData"]["fame"] = 7          # lowercase: ItemId 6 absent
    d = handlers.attach_remain_list({}, player)
    rem = remain_map(d)
    check("coerces string to int", rem.get(2) == 12345, f"rem={rem}")
    check("None -> 0", rem.get(5) == 0, f"rem={rem}")
    check("lowercase fame not emitted as ItemId 6", 6 not in rem, f"rem={rem}")
    player.data["RoleData"]["Fame"] = 9          # exact key -> ItemId 6 present
    d = handlers.attach_remain_list({"keep": 1}, player)
    rem = remain_map(d)
    check("Fame present as ItemId 6 when key exists", rem.get(6) == 9, f"rem={rem}")
    check("existing response keys preserved", d.get("keep") == 1, d)


def main() -> None:
    test_counsellor_upgrade_deducts_silver_into_remain_list()
    test_levy_and_recover_carry_remain_list()
    test_remain_list_absolute_and_coerced()
    if all(OK):
        print(f"ALL PASS ({len(OK)} checks)")
    else:
        print(f"FAILURES: {OK.count(False)}/{len(OK)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
