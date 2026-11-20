"""Game of Sultans v2 wife-competition / diplomacy clusters (18104-18127).

Isolated module for the parallel-handler work: the coordinator merges this file
into ``gos.handlers.HANDLERS`` through ``COMPETITION_HANDLERS`` (see the
``_merge_cluster_handlers`` block at the bottom of ``gos/handlers.py``).
``gos/handlers.py`` and ``gos/defaults.py`` are intentionally untouched.

Ground rules (tools/HANDLER_SPEC.md)
------------------------------------
* Every response field is one the v2.005 client actually reads. Shapes were
  taken from the real response callbacks in

      modules/_wifeCompeteMod.lua      (18104-18111)
      modules/_diplomacyMod.lua        (18112-18127)

  walking BACKWARD from each ``actionNNNNN = Lxx`` assignment, and cross-
  checked with ``tools/lua_shape_harness/extract_fields.py``.  Two extra
  sources were inspected because the static extractor only sees the module
  callback, not the scene callback that receives the same table:

      scene/wifeCompeteScene.roomDialog.lua:2693 / :1291 / :2481 / :698
        -> 18108 / 18109 / 18110 / 18111 read ``Status``;
      scene/diplomacyScene.privateChatDialog.lua
        -> 18122 / 18125 use the module-callback shapes below.

* These are MULTI-PLAYER systems (competition rooms, opponents, friend /
  black / chat / cross-server relations).  The dev server owns no other
  player's data, so every other-player collection is a Lua-safe empty list or
  dict and every server-decided status is the neutral int 0.  No player,
  room, ranking, chat message or reward is fabricated.

* No award tier is read by any of these callbacks, so nothing is granted and
  ``attach_remain_list`` is not used.  The only config-backed numbers these
  systems observe are client-side gates (ConfigEnvSet ``DayBtyVsCount`` = 5,
  ``BlackListLimit`` = 500, ``AddChatUnlock`` = 3); they are read by the
  client from its own config, never echoed by these responses.  Nothing here
  invents a config value.

* Persistent state is seeded lazily (``dict.setdefault``) under a new
  ``WifeCompete`` slice so no change to ``defaults.py`` is needed.

Already implemented elsewhere and deliberately NOT registered here:
18100 (guide), 18120 (blacklist), 18121 (friends), 18129 (diplomacy info).
18101-18103 belong to ``_vedioMod.lua`` (handled by another cluster); 18128
does not exist in the decompiled v2.005 tree.
"""
from __future__ import annotations

try:
    from gos.handlers import _as_int
except (ImportError, AttributeError):  # pragma: no cover - merge-time only
    def _as_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


# ---------------------------------------------------------------------------
# lazy state helpers
# ---------------------------------------------------------------------------
def _wife_state(player) -> dict:
    """Per-player wife-competition counters (seeded lazily, no defaults.py edit)."""
    state = player.data.get("WifeCompete")
    if not isinstance(state, dict):
        state = {}
        player.data["WifeCompete"] = state
    return state


def _daily_bty_vs_times(player) -> int:
    """Today's beauty-vs attempts.

    The client subtracts this from ConfigEnvSet.DayBtyVsCount (5) in
    wifeCompeteScene.mainHallScene.lua:201-208 and roomDialog.lua:2563-2571,
    so it MUST be an int.  The dev server does not simulate battles, so the
    stored value stays at its Lua-safe zero unless a future handler advances
    it; incrementing/date-rollover logic needs research/simulation.
    """
    return _as_int(_wife_state(player).get("DayBtyVsTimes"), 0)


def _empty_chat_role() -> dict:
    """Other-player chat profile: no data on the dev server, Lua-safe fields.

    _diplomacyMod.lua:1359-1380 (18122) indexes Nickname, TitleId and DressList,
    and :1527-1532 reads name / RoleId / Unread.  ``next(DressList)`` at
    :1367 requires a table, so DressList is an empty list, never nil.
    """
    return {
        "Nickname": "",
        "TitleId": 0,
        "DressList": [],
        "RoleId": "",
        "Unread": 0,
    }


def _self_chat_role(player) -> dict:
    """Own chat profile for 18122 (guarded by ``if A0_3.SelfChatRoleData``).

    Filled from the player's own persisted data where available; cosmetic
    collections are empty lists because the client calls ``next(DressList)``
    at _diplomacyMod.lua:1454-1464 and would throw on nil.
    """
    role_base = player.data.get("RoleBase") or {}
    role_data = player.data.get("RoleData") or {}
    return {
        "ChatAvatar": role_base.get("ChatAvatar") or "",
        "Nickname": role_base.get("Nickname") or "",
        "VipLv": _as_int(role_data.get("VipLv") or role_base.get("VipLv")),
        "Lv": _as_int(role_data.get("Lv") or role_base.get("Lv")),
        "DressList": role_base.get("DressList") or [],
        "AvatarList": role_base.get("AvatarList") or [],
        "Manifesto": role_base.get("Manifesto") or "",
        "AvatarBox": _as_int(role_base.get("AvatarBox")),
        "TitleId": _as_int(role_base.get("DressTitle")),
    }


# ===========================================================================
# _wifeCompeteMod.lua  (18104-18111)
# ===========================================================================
def action_wife_room_list(payload: dict, player, session, store) -> dict:
    """18104: room list / battle log / daily attempt count.

    Callback (_wifeCompeteMod.lua:814-832) reads DayBtyVsTimes, RoomList and
    LogList.  RoomList and LogList are other players' rooms/logs -> empty.
    """
    return {
        "DayBtyVsTimes": _daily_bty_vs_times(player),
        "RoomList": [],
        "LogList": [],
    }


def action_wife_room_create(payload: dict, player, session, store) -> dict:
    """18105: create room (RoomType / BeautyId request).

    Callback (:875-890) reads RoomList.  The dev server has no room registry,
    so it cannot return a fabricated room; the updateRoomList helper
    (:434-442) returns early for an empty list.
    """
    return {"RoomList": []}


def action_wife_room_page(payload: dict, player, session, store) -> dict:
    """18106: paginate rooms (StartIndex / RoomCount request)."""
    return {"RoomList": []}


def action_wife_room_log(payload: dict, player, session, store) -> dict:
    """18107: room combat log.  Client sorts LogList by Id (:358-376)."""
    return {"LogList": []}


def action_wife_room_set(payload: dict, player, session, store) -> dict:
    """18108: set beauty / position / method in a room.

    The module callback (:940-970) reads no field, but the scene callbacks
    (wifeCompeteScene.roomDialog.lua:2693 and :3591) read ``Status``.
    A zero status means "no simulated result" and skips every status branch.
    No BeautyRoom object is returned (other-player room data).
    """
    return {"Status": 0}


def action_wife_room_protect(payload: dict, player, session, store) -> dict:
    """18109: buy protection (ProCount request); scene reads Status (:1291)."""
    return {"Status": 0}


def action_wife_room_leave(payload: dict, player, session, store) -> dict:
    """18110: leave/change seat (Pos request); scene reads Status (:2481)."""
    return {"Status": 0}


def action_wife_room_show(payload: dict, player, session, store) -> dict:
    """18111: search by ShowId; scene reads Status and, on 1, BeautyRoom
    (searchRoomDialog.lua:698).  Status 0 avoids fabricating a room."""
    return {"Status": 0}


# ===========================================================================
# _diplomacyMod.lua  (18112-18127)
# ===========================================================================
def action_diplomacy_add_friend(payload: dict, player, session, store) -> dict:
    """18112: add friend.  Callback (:2131-2211) reads Status only.

    Status 0 is the neutral server value: the client maps 1/2/8/9/10/11/3/5/7
    to localised prompts and passes it on; with no friend service there is no
    result to report.  Real status codes need cross-server research.
    """
    return {"Status": 0}


def action_diplomacy_req_list(payload: dict, player, session, store) -> dict:
    """18113: friend-request list; callback (:2063-2118) pairs() over ReqList."""
    return {"ReqList": []}


def action_diplomacy_friend_result(payload: dict, player, session, store) -> dict:
    """18114: accept/reject friend request result; callback reads Status."""
    return {"Status": 0}


def action_diplomacy_friend_cancel(payload: dict, player, session, store) -> dict:
    """18115: cancel friend request; callback (:1969-1985) reads no field."""
    return {}


def action_diplomacy_friend_remove(payload: dict, player, session, store) -> dict:
    """18116: remove friend; callback (:1989-2004) reads no field."""
    return {}


def action_diplomacy_black_result(payload: dict, player, session, store) -> dict:
    """18117: add/remove blacklist result; callback (:1908-1966) reads Status."""
    return {"Status": 0}


def action_diplomacy_chat_open(payload: dict, player, session, store) -> dict:
    """18118: open private chat; callback (:1889-1905) reads no field."""
    return {}


def action_diplomacy_black_list(payload: dict, player, session, store) -> dict:
    """18119: blacklist.  Callback (:1818-1872) pairs() over BlackList."""
    return {"BlackList": []}


def action_diplomacy_chat_history(payload: dict, player, session, store) -> dict:
    """18122: private-chat history with another player.

    Callback (:1322-1557) reads ChatList (list), ChatRoleData (other player)
    and, when present, SelfChatRoleData (own player).  All three are Lua-safe;
    the empty ChatList means no messages are invented.
    """
    return {
        "ChatList": [],
        "ChatRoleData": _empty_chat_role(),
        "SelfChatRoleData": _self_chat_role(player),
    }


def action_diplomacy_chat_read(payload: dict, player, session, store) -> dict:
    """18123: mark messages read (RidListStr request); callback reads no field."""
    return {}


def action_diplomacy_chat_clear(payload: dict, player, session, store) -> dict:
    """18124: clear recent chat; callback (:1263-1278) reads no field."""
    return {}


def action_diplomacy_recent_list(payload: dict, player, session, store) -> dict:
    """18125: recent chat tabs.

    The real callback (:1062-1259) guards TeamChat with ``if L4_3 then`` but
    calls ``pairs(A0_3.RecentChatList)`` unguarded (:1172), so RecentChatList
    MUST be a list.  extract_fields.py over-reports this action (its block
    walker runs into the following callbacks); the extra keys below are the
    same Lua-safe empties and are harmless to the client.
    """
    return {
        "TeamChat": [],
        "RecentChatList": [],
        # over-reported by the static extractor, kept as safe no-ops:
        "BlackList": [],
        "ChatList": [],
        "ChatRoleData": _empty_chat_role(),
        "FriendList": [],
        "ReadMsg": 0,
        "ReqList": [],
        "SelfChatRoleData": _self_chat_role(player),
        "Status": 0,
    }


def action_diplomacy_chat_send(payload: dict, player, session, store) -> dict:
    """18126: send chat message.

    Callback (:1002-1058) reads Status, then -- when Status <= 1 -- calls
    setMsg(A0_3).  setMsg (:606-731) does ``A0_2.ext.Type`` unguarded, so
    ``ext`` must be a table; Type 0 hits none of its branches and Text is
    unused.  Status 0 == no simulated delivery result.
    """
    return {"Status": 0, "ext": {"Type": 0}, "Text": ""}


def action_diplomacy_chat_forward(payload: dict, player, session, store) -> dict:
    """18127: forward chat payload; callback (:983-998) reads no field."""
    return {}


COMPETITION_HANDLERS = {
    # _wifeCompeteMod.lua
    "18104": action_wife_room_list,
    "18105": action_wife_room_create,
    "18106": action_wife_room_page,
    "18107": action_wife_room_log,
    "18108": action_wife_room_set,
    "18109": action_wife_room_protect,
    "18110": action_wife_room_leave,
    "18111": action_wife_room_show,
    # _diplomacyMod.lua
    "18112": action_diplomacy_add_friend,
    "18113": action_diplomacy_req_list,
    "18114": action_diplomacy_friend_result,
    "18115": action_diplomacy_friend_cancel,
    "18116": action_diplomacy_friend_remove,
    "18117": action_diplomacy_black_result,
    "18118": action_diplomacy_chat_open,
    "18119": action_diplomacy_black_list,
    "18122": action_diplomacy_chat_history,
    "18123": action_diplomacy_chat_read,
    "18124": action_diplomacy_chat_clear,
    "18125": action_diplomacy_recent_list,
    "18126": action_diplomacy_chat_send,
    "18127": action_diplomacy_chat_forward,
}
