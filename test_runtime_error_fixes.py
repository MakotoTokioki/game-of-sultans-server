"""Regression tests for the 2026-11-17 device runtime errors.

Error 1 -- pairs(nil) at dispatchEvent -> callBackFunc
  _diplomacyMod.action18121 response callback does an unguarded
  `pairs(A0_3.RecentChatList)`.  The dev handler used to send `RecentList`,
  so pairs() got nil.  The wire response must carry `RecentChatList` as a
  Lua-safe table (a Python list becomes a Lua table).

Error 2 -- compare number with nil at selectIndex -> showOverlay -> callBackFunc
  ui/myWidget.lua's tab selectIndex/showRedPoint path compares each red-point
  count with `0 < count`.  Every official v7 action response carries
  Data.RedPoint (8 numeric keys); the dev server attached it only for 11901.
  Screen actions (11001 Rankings, 12311 Chat, 18121 Friends, 10015 Task claim)
  must therefore also carry RedPoint with non-nil, numeric values.

Run: python3 test_runtime_error_fixes.py
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from gos import handlers
from gos.defaults import default_player
from gos.store import Player

ok: list[bool] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    data = default_player("runtime_fix", "FixTester")
    return Player(uid="runtime_fix", data=data,
                  path=__import__("pathlib").Path(
                      "/tmp/opencode/gos_test_players/runtime_fix.json"))


def _session() -> SimpleNamespace:
    return SimpleNamespace(jwt="jwt", user_id="runtime_fix", sk="sk",
                           keypair=None, player_id="runtime_fix",
                           server_id=1, app_id=1020)


# The 8 red-point keys/values every v7 action response carries; the same dict
# the dev server has always returned for action 11901.
RED_POINT_KEYS = {
    "HaloCounsellorRankChange", "SpiritPetRankChange", "SpiritPetStage",
    "FbFansFeedback", "SpecialDiscount", "boss", "dailyTask", "achievement",
}


def test_18121_has_recent_chat_list_table() -> None:
    d = handlers.action_diplomacy_friends({}, _player(), _session(),
                                          SimpleNamespace(save=lambda p: None))
    for key in ("RecentChatList", "FriendList"):
        check("18121 %s present and a Lua-safe list" % key,
              isinstance(d.get(key), list), (key, d.get(key)))
    check("18121 RecentChatList carries the original empty list data",
          d.get("RecentChatList") == [], d.get("RecentChatList"))


def test_every_action_response_carries_red_point() -> None:
    """Emulate the handle_forward path: handler response -> attach_task_progress."""
    player = _player()
    session = _session()
    store = SimpleNamespace(save=lambda p: None, _players={}, get_or_create=lambda u: player)
    for action in ("11001", "12311", "18121", "10015"):
        raw = handlers.dispatch(action, {"actionid": int(action)}, player,
                                session, store)
        wire = handlers.attach_task_progress(dict(raw), player)
        rp = wire.get("RedPoint")
        check("%s action response has RedPoint dict" % action,
              isinstance(rp, dict), (action, rp))
        check("%s RedPoint retains the 8 captured v7 keys" % action,
              isinstance(rp, dict) and RED_POINT_KEYS <= set(rp),
              (action, None if rp is None else sorted(rp)))
        check("%s RedPoint values are Lua-safe numbers (no nil)" % action,
              isinstance(rp, dict)
              and all(isinstance(v, int) for v in rp.values()),
              (action, rp))


def test_red_point_survives_early_return_path() -> None:
    """attach_task_progress returns early if task counters are malformed;
    RedPoint must already be attached so even those responses are safe."""
    player = _player()
    player.data["Extra"] = "not-a-dict"  # forces the defensive path
    wire = handlers.attach_task_progress({}, player)
    rp = wire.get("RedPoint")
    check("early-return path still carries RedPoint with numeric values",
          isinstance(rp, dict) and RED_POINT_KEYS <= set(rp)
          and all(isinstance(v, int) for v in rp.values()), rp)


def test_chat_cross_config_and_avatar_box_config() -> None:
    from gos import v2configs
    player = _player()
    cfg = handlers.action_11901({"CompetitionRank": [1]}, player,
                                _session(), SimpleNamespace(save=lambda p: None))
    check("11901 supplies CrossChat table for chat listener",
          isinstance(cfg.get("CrossChat"), list), cfg.get("CrossChat"))
    boxes = v2configs.load("TcAvatarBoxCfg")
    check("TcAvatarBoxCfg bundled for chat avatar backgrounds",
          any(int(r.get("ItemId") or 0) == 1400 for r in boxes), boxes)


def main() -> None:
    test_18121_has_recent_chat_list_table()
    test_every_action_response_carries_red_point()
    test_red_point_survives_early_return_path()
    test_chat_cross_config_and_avatar_box_config()
    print("\n%d/%d checks passed" % (sum(ok), len(ok)))
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
