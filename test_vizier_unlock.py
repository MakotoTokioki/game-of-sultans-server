"""Regression tests for level-up vizier unlock persistence.

The v2 client unlocks viziers entirely client-side in the action-10010
callback (character.upOfficerScene.lua reads TcLvUpgradeCfg.UnlockCounsellor
and calls _ministerMod.setMinisterInfo(id, "IsLock", 0)); it sends no action.
The server must mirror that into RoleCounsellor, otherwise the next login
snapshot restores the starter three and the new vizier vanishes.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import defaults, handlers, v2configs
from gos.store import Player, Session

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def make_player(uid: str = "unlock_test") -> Player:
    data = defaults.default_player(uid, "UnlockTest")
    tmp = Path(tempfile.mkdtemp(prefix="gos_unlock_")) / f"{uid}.json"
    return Player(uid=uid, data=data, path=tmp)


class MemStore:
    def __init__(self):
        self.saves = 0

    def save(self, player: Player) -> None:
        self.saves += 1


def ids(player: Player) -> set[int]:
    return {int(c.get("CounsellorId") or c.get("Id") or 0)
            for c in player.data.get("RoleCounsellor", [])}


def test_unlock_schedule_from_config() -> None:
    rows = {int(r.get("Level") or 0): int(r.get("UnlockCounsellor") or 0)
            for r in v2configs.load("TcLvUpgradeCfg") if isinstance(r, dict)}
    check("config grants vizier 6 at level 2", rows.get(2) == 6, rows.get(2))
    check("config grants vizier 7 at level 3", rows.get(3) == 7, rows.get(3))
    check("config grants vizier 8 at level 4", rows.get(4) == 8, rows.get(4))
    check("level 1 grants nobody", defaults.level_unlocked_counsellors(1) == [],
          defaults.level_unlocked_counsellors(1))
    check("level 4 grants 6/7/8",
          defaults.level_unlocked_counsellors(4) == [6, 7, 8],
          defaults.level_unlocked_counsellors(4))


def test_new_counsellor_uses_config_talents() -> None:
    c = defaults.default_counsellor(6)
    cfg = next(r for r in v2configs.load("TcCounsellor")
               if int(r.get("CounsellorId") or 0) == 6)
    check("vizier 6 talents come from TcCounsellor",
          [int(t) for t, _ in c["Talent"]] == [int(t) for t in cfg["Talent"]],
          (c["Talent"], cfg["Talent"]))
    check("vizier 6 is unlocked", int(c.get("IsLock") or 0) == 0, c)
    check("starter vizier 1 talents unchanged",
          [t for t, _ in defaults.default_counsellor(1)["Talent"]]
          == [2, 39, 55, 75])


def test_level_up_persists_unlock() -> None:
    p = make_player()
    p.data["RoleData"]["Lv"] = 1
    p.data["RoleData"]["Exp"] = 200  # TcLvUpgradeCfg level 2 requirement
    p.data["RoleBase"]["Lv"] = 1
    check("starts with only the three starters", ids(p) == {1, 2, 3}, ids(p))
    res = handlers.action_level_up({}, p, None, MemStore())
    check("level up succeeds", res.get("NewLevel") == 2, res)
    check("vizier 6 added on level up", ids(p) == {1, 2, 3, 6}, ids(p))
    check("added vizier is unlocked",
          all(int(c.get("IsLock") or 0) == 0 for c in p.data["RoleCounsellor"]))
    # A second level up must add 7, not re-add 6.
    p.data["RoleData"]["Exp"] = 400
    handlers.action_level_up({}, p, None, MemStore())
    check("level 3 adds vizier 7 exactly once",
          ids(p) == {1, 2, 3, 6, 7}, ids(p))


def test_login_heals_existing_accounts() -> None:
    """Regressions created before this fix: level 4 but only 3 viziers."""
    p = make_player("legacy_unlock")
    p.data["RoleData"]["Lv"] = 4
    p.data["RoleBase"]["Lv"] = 4
    p.data["RoleCounsellor"] = [defaults.default_counsellor(i) for i in (1, 2, 3)]
    handlers.action_login({}, p, None, MemStore())
    check("login restores all level-unlocked viziers",
          ids(p) == {1, 2, 3, 6, 7, 8}, ids(p))
    # Re-login must not duplicate them.
    handlers.action_login({}, p, None, MemStore())
    check("re-login does not duplicate",
          len(p.data["RoleCounsellor"]) == 6,
          len(p.data["RoleCounsellor"]))


def main() -> None:
    test_unlock_schedule_from_config()
    test_new_counsellor_uses_config_talents()
    test_level_up_persists_unlock()
    test_login_heals_existing_accounts()
    print(f"\n{sum(OK)}/{len(OK)} checks passed")
    sys.exit(0 if all(OK) else 1)


if __name__ == "__main__":
    main()
