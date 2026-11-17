"""Chat (12301 send, 12302 history, cash-pack icon) regressions.

v7 sends HaveCashPack as boolean false; a Python 0 is truthy in Lua and made
the pack icon permanently visible.  The 12301 request {Text, Channel, Time}
is echoed into a v7-shaped MsgData and persisted as a small sent-message ring
that 12302 replays.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers
from gos.defaults import default_player
from gos.store import Player

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def make_player(uid: str = "chat_test") -> Player:
    data = default_player(uid, "ChatTest")
    tmp = Path(tempfile.mkdtemp(prefix="gos_chat_")) / f"{uid}.json"
    return Player(uid=uid, data=data, path=tmp)


class MemStore:
    def __init__(self) -> None:
        self.saved = 0

    def save(self, player: Player) -> None:
        self.saved += 1


def test_chat_pack_icon_is_real_boolean_false() -> None:
    d = handlers.action_chat({}, make_player(), None, MemStore())
    raw = json.dumps(d, separators=(",", ":"))  # server wire format (compact)
    check("HaveCashPack is a real bool False",
          d.get("HaveCashPack") is False and isinstance(d["HaveCashPack"], bool), d)
    check("JSON encodes HaveCashPack as false, not 0",
          '"HaveCashPack":false' in raw, raw)
    check("IsExistNationsManeuverRedBag is bool False",
          d.get("IsExistNationsManeuverRedBag") is False
          and '"IsExistNationsManeuverRedBag":false' in raw, d)
    check("12302 keeps empty MsgList/NewestMsg safe when no messages",
          d["MsgList"] == [] and d["NewestMsg"] == {}, d)


def test_12301_response_shape() -> None:
    p = make_player()
    store = MemStore()
    payload = {"Text": "hello sultan", "Channel": "c", "Time": "1794821999000.0"}
    d = handlers.action_chat_send(payload, p, None, store)
    check("12301 is mapped in HANDLERS", handlers.HANDLERS.get("12301") is handlers.action_chat_send)
    check("12301 returns a one-element MsgList", len(d.get("MsgList", [])) == 1, d)
    msg = d["MsgList"][0]
    check("12301 echoes Text/Channel/Time",
          (msg["Text"], msg["Channel"], msg["Time"])
          == ("hello sultan", "c", "1794821999000.0"), msg)
    check("MsgData has Id and RoleBase/RoleData identity fields",
          msg.get("Id") == 1
          and all(k in msg for k in ("Rid", "Nickname", "Lv", "VipLv",
                                     "Avatar", "ChatAvatar", "Dress",
                                     "AvatarBox")), msg)
    check("zero avatar-frame ids are omitted (Lua 0 is truthy)",
          all(int(msg.get(k) or 0) > 0 for k in
              ("AvatarBox", "Figure", "RankBottomId", "TitleId")
              if k in msg), msg)
    check("MsgData.ext has the v7 shape",
          msg.get("ext") == {"Type": 1, "pre_text": "hello sultan",
                             "MentionMsg": [], "ReplyMsg": {}}, msg.get("ext"))
    check("NewestMsg wraps the same MsgData and channel",
          d["NewestMsg"] == {"MsgData": msg, "Channel": "c"}, d["NewestMsg"])
    check("12301 carries the false pack flags and SystemInfo",
          d["SystemInfo"] == {} and d["HaveCashPack"] is False
          and d["ShareRemainSeconds"] == 0, d)
    check("12301 persists the sent message", store.saved == 1, store.saved)


def test_12302_replays_sent_history() -> None:
    p = make_player()
    handlers.action_chat_send({"Text": "one", "Channel": "c", "Time": "1.0"},
                              p, None, MemStore())
    handlers.action_chat_send({"Text": "two", "Channel": "a", "Time": "2.0"},
                              p, None, MemStore())
    d = handlers.action_chat({}, p, None, MemStore())
    check("12302 replays both sent messages in order",
          [m["Text"] for m in d["MsgList"]] == ["one", "two"], d)
    check("12302 NewestMsg is the last sent message",
          d["NewestMsg"]["MsgData"]["Text"] == "two"
          and d["NewestMsg"]["Channel"] == "a", d["NewestMsg"])
    check("12302 history is json-serializable", json.loads(json.dumps(d)) == d)
    # Ring stays small.
    for i in range(25):
        handlers.action_chat_send({"Text": f"m{i}", "Channel": "c", "Time": str(i)},
                                  p, None, MemStore())
    check("sent-message ring is capped at 20",
          len(handlers.action_chat({}, p, None, MemStore())["MsgList"]) == 20,
          len(p.data["ChatSent"]))


def test_chat_history_uses_current_avatar() -> None:
    p = make_player("chat_avatar_test")
    p.data["ChatSent"] = [{"Id": 1, "Text": "old", "Channel": "c",
                           "ChatAvatar": "B1", "AvatarBox": 0}]
    handlers.action_appearance_avatar({"AvatarId": 2, "AvatarType": 3}, p,
                                      None, MemStore())
    d = handlers.action_chat({}, p, None, MemStore())
    msg = d["MsgList"][0]
    check("12302 history uses the selected avatar head",
          msg.get("ChatAvatar") == "C2", msg)
    check("12302 history uses a valid avatar box",
          int(msg.get("AvatarBox") or 0) > 0, msg)


def main() -> None:
    test_chat_pack_icon_is_real_boolean_false()
    test_12301_response_shape()
    test_12302_replays_sent_history()
    test_chat_history_uses_current_avatar()
    print(f"\n{sum(OK)}/{len(OK)} checks passed")
    sys.exit(0 if all(OK) else 1)


if __name__ == "__main__":
    main()
