"""Regression tests for the wife-competition / diplomacy clusters.

Actions 18104-18111 (modules/_wifeCompeteMod.lua) and 18112-18127
(modules/_diplomacyMod.lua), excluding the four already implemented in
gos/handlers.py (18100, 18120, 18121, 18129).

Response shapes were discovered with
``python3 tools/lua_shape_harness/extract_fields.py ACTION`` and cross-checked
against the decompiled callbacks plus their scene callers (wifeCompeteScene /
diplomacyScene), which read ``Status`` on 18108/18109/18110/18111 even though
the static extractor only sees the module callbacks.

These are multi-player systems: all other-player data must be Lua-safe empty
lists/dicts and no player, room, ranking, message or reward is fabricated.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Import gos.handlers FIRST: its module-level _merge_cluster_handlers() imports
# gos.handlers_competition and registers COMPETITION_HANDLERS into HANDLERS.
from gos import handlers, handlers_competition  # noqa: E402
from gos.defaults import default_player  # noqa: E402
from gos.store import Player  # noqa: E402

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="competitiontest", data=default_player("competitiontest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def _stub():
    return SimpleNamespace(save=lambda p: None), None


EXPECTED_IDS = {
    # wife competition
    "18104", "18105", "18106", "18107", "18108", "18109", "18110", "18111",
    # diplomacy / cross-server relations
    "18112", "18113", "18114", "18115", "18116", "18117", "18118", "18119",
    "18122", "18123", "18124", "18125", "18126", "18127",
}

ALREADY_IMPLEMENTED = {"18100", "18120", "18121", "18129"}


def main() -> None:
    store, session = _stub()
    hc = handlers_competition

    # ---- registration -----------------------------------------------------
    check("COMPETITION_HANDLERS has exactly the 22 new actions",
          set(hc.COMPETITION_HANDLERS) == EXPECTED_IDS
          and all(callable(f) for f in hc.COMPETITION_HANDLERS.values()),
          sorted(hc.COMPETITION_HANDLERS))
    check("already-implemented 18100/18120/18121/18129 are not duplicated",
          not (set(hc.COMPETITION_HANDLERS) & ALREADY_IMPLEMENTED)
          and all(a in handlers.HANDLERS for a in ALREADY_IMPLEMENTED))
    check("cluster handlers are merged into gos.handlers.HANDLERS",
          all(handlers.HANDLERS.get(a) is hc.COMPETITION_HANDLERS[a]
              for a in EXPECTED_IDS),
          [a for a in EXPECTED_IDS if handlers.HANDLERS.get(a) is not hc.COMPETITION_HANDLERS[a]])

    # every action must be callable with an empty payload without throwing
    p = _player()
    responses = {}
    for aid in sorted(EXPECTED_IDS):
        responses[aid] = hc.COMPETITION_HANDLERS[aid]({}, p, session, store)
    check("all 22 handlers return dicts on an empty payload",
          all(isinstance(d, dict) for d in responses.values()))
    check("no handler grants a fabricated reward (no RemainList)",
          all("RemainList" not in d for d in responses.values()))

    # ---- 18104: room list / log / daily count -----------------------------
    d = responses["18104"]
    check("18104 has exact callback fields DayBtyVsTimes/RoomList/LogList",
          set(d) == {"DayBtyVsTimes", "RoomList", "LogList"}, d)
    check("18104 DayBtyVsTimes is an int Lua can subtract from DayBtyVsCount",
          isinstance(d["DayBtyVsTimes"], int) and d["DayBtyVsTimes"] == 0, d)
    check("18104 room/log data is empty other-player data",
          d["RoomList"] == [] and d["LogList"] == []
          and isinstance(d["RoomList"], list) and isinstance(d["LogList"], list), d)
    p.data.setdefault("WifeCompete", {})["DayBtyVsTimes"] = 3
    d2 = hc.action_wife_room_list({}, p, session, store)
    check("18104 echoes lazily-seeded player state (no defaults.py edit)",
          d2["DayBtyVsTimes"] == 3 and "WifeCompete" in p.data, d2)

    # ---- 18105 / 18106 / 18107 --------------------------------------------
    for aid, key in (("18105", "RoomList"), ("18106", "RoomList"),
                     ("18107", "LogList")):
        d = responses[aid]
        check(f"{aid} has exactly {key} as an empty list",
              set(d) == {key} and isinstance(d[key], list) and not d[key], d)

    # ---- 18108-18111: scene callbacks read Status -------------------------
    for aid in ("18108", "18109", "18110", "18111"):
        d = responses[aid]
        check(f"{aid} returns Lua-safe Status int (scene callback field)",
              set(d) == {"Status"} and isinstance(d["Status"], int)
              and d["Status"] == 0, d)

    # ---- 18112-18119, 18123, 18124, 18127: Status / lists / empty ---------
    for aid in ("18112", "18114", "18117"):
        d = responses[aid]
        check(f"{aid} Status is a neutral int 0 (no simulated result)",
              set(d) == {"Status"} and isinstance(d["Status"], int)
              and d["Status"] == 0, d)
    for aid in ("18115", "18116", "18118", "18123", "18124", "18127"):
        check(f"{aid} has empty callback shape", responses[aid] == {},
              responses[aid])
    for aid, key in (("18113", "ReqList"), ("18119", "BlackList")):
        d = responses[aid]
        check(f"{aid} has exactly {key} as an empty list",
              set(d) == {key} and isinstance(d[key], list) and not d[key], d)

    # ---- 18122: private chat history --------------------------------------
    d = responses["18122"]
    check("18122 has the three callback-read fields",
          set(d) == {"ChatList", "ChatRoleData", "SelfChatRoleData"}, d)
    check("18122 ChatList is an empty list", d["ChatList"] == [], d)
    role = d["ChatRoleData"]
    check("18122 ChatRoleData is a Lua-safe dict (Nickname/TitleId/DressList/"
          "RoleId/Unread)",
          isinstance(role, dict)
          and all(k in role for k in ("Nickname", "TitleId", "DressList",
                                      "RoleId", "Unread"))
          and isinstance(role["DressList"], list)
          and isinstance(role["Nickname"], str), role)
    self_role = d["SelfChatRoleData"]
    check("18122 SelfChatRoleData has DressList list for next() and own stats",
          isinstance(self_role, dict) and isinstance(self_role["DressList"], list)
          and isinstance(self_role["AvatarList"], list)
          and isinstance(self_role["Nickname"], str)
          and all(isinstance(self_role[k], int)
                  for k in ("Lv", "VipLv", "TitleId", "AvatarBox")), self_role)

    # ---- 18125: recent chat tabs ------------------------------------------
    d = responses["18125"]
    extractor_18125 = {"BlackList", "ChatList", "ChatRoleData", "FriendList",
                       "ReadMsg", "RecentChatList", "ReqList",
                       "SelfChatRoleData", "Status", "TeamChat"}
    check("18125 contains every field extract_fields.py lists",
          extractor_18125 <= set(d), sorted(set(d)))
    check("18125 TeamChat/RecentChatList are empty lists; RecentChatList is "
          "never nil (unguarded pairs)",
          isinstance(d["TeamChat"], list) and not d["TeamChat"]
          and isinstance(d["RecentChatList"], list) and not d["RecentChatList"], d)
    check("18125 over-reported extras are Lua-safe no-ops",
          d["Status"] == 0 and d["ReadMsg"] == 0
          and d["FriendList"] == [] and d["ReqList"] == []
          and d["BlackList"] == [] and d["ChatList"] == [], d)

    # ---- 18126: send chat --------------------------------------------------
    d = responses["18126"]
    check("18126 has Status / ext / Text with Lua-safe ext.Type",
          set(d) == {"Status", "ext", "Text"}
          and isinstance(d["Status"], int) and d["Status"] == 0
          and isinstance(d["ext"], dict) and isinstance(d["ext"].get("Type"), int)
          and isinstance(d["Text"], str), d)

    # ---- dispatch integration (no server restart / ADB) --------------------
    d = handlers.dispatch("18104", {}, _player(), session, store)
    check("gos.handlers.dispatch routes 18104 to the new handler",
          set(d) == {"DayBtyVsTimes", "RoomList", "LogList"}, d)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
