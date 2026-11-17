"""Appearance / avatar (13301-13305, 13310) regressions.

The v7 capture's 13310 response was the only source of the full cosmetic
field set; ids 400 (fashion), 1400 (avatar box), 1500 (skin) are verified in
the bundled TcItem config.  Caption-only cosmetic ids with no v2 config source
(ChatBox/AvatarBoxEffect/RankBottomId/RoleInfoBackGround) come from the v7
capture and are documented in handlers.action_appearance_info.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers, v2configs
from gos.defaults import default_player
from gos.store import Player

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def make_player(uid: str = "avatar_test") -> Player:
    data = default_player(uid, "AvatarTest")
    tmp = Path(tempfile.mkdtemp(prefix="gos_avatar_")) / f"{uid}.json"
    return Player(uid=uid, data=data, path=tmp)


class MemStore:
    def save(self, player: Player) -> None:
        pass


def test_login_cosmetics_seeded_from_config() -> None:
    p = make_player()
    items = {e["ItemId"]: e for e in p.data["RoleItem"]}
    for item_id in (400, 1400, 1500):
        check(f"starter item {item_id} exists in TcItem",
              bool(v2configs.item_by_id(item_id)), item_id)
        check(f"login seeds RoleItem {item_id} Num=1 with future ExpireTime",
              items.get(item_id, {}).get("Num") == 1
              and items[item_id]["ExpireTime"] > handlers._now(), items.get(item_id))
    check("login seeds top-level DressList with fashion 400",
          p.data["DressList"] == [{
              "ItemId": 400, "Num": 1,
              "Timestamp": p.data["DressList"][0]["Timestamp"],
          }], p.data["DressList"])
    check("login seeds AvatarList with avatar box 1400",
          [e["ItemId"] for e in p.data["AvatarList"]] == [1400], p.data["AvatarList"])
    check("RoleBase/RoleData use config-derived AvatarBox 1400 and DressTitle 0",
          all(p.data[k]["AvatarBox"] == 1400 and p.data[k]["DressTitle"] == 0
              for k in ("RoleBase", "RoleData")),
          (p.data["RoleBase"], p.data["RoleData"]))
    check("RoleBase carries the seeded DressList",
          [e["ItemId"] for e in p.data["RoleBase"]["DressList"]] == [400],
          p.data["RoleBase"]["DressList"])


def test_login_heals_legacy_empty_cosmetics() -> None:
    p = make_player("legacy_avatar")
    p.data["RoleItem"] = []
    p.data["DressList"] = []
    p.data["AvatarList"] = []
    for key in ("RoleBase", "RoleData"):
        p.data[key]["AvatarBox"] = 0
        p.data[key]["ChatAvatar"] = "B1"
    d = handlers.action_login({}, p, None, MemStore())
    check("login heals legacy RoleItem from TcItem",
          {e["ItemId"] for e in d["RoleItem"]} == {400, 1400, 1500}, d.get("RoleItem"))
    check("login heals legacy DressList/AvatarList",
          [e["ItemId"] for e in d["DressList"]] == [400]
          and [e["ItemId"] for e in d["AvatarList"]] == [1400], d)
    check("login heals legacy AvatarBox/ChatAvatar",
          d["RoleBase"]["AvatarBox"] == 1400
          and d["RoleBase"]["ChatAvatar"] == "F400", d["RoleBase"])


def test_13303_returns_new_chat_avatar() -> None:
    p = make_player()
    d = handlers.action_appearance_avatar(
        {"AvatarId": 400, "AvatarType": 1}, p, None, MemStore())
    check("13303 returns NewChatAvatar", d.get("NewChatAvatar") == "F400", d)
    check("13303 persists ChatAvatar in RoleBase and RoleData",
          p.data["RoleBase"]["ChatAvatar"] == "F400"
          and p.data["RoleData"]["ChatAvatar"] == "F400", p.data)
    d2 = handlers.action_appearance_avatar(
        {"AvatarId": 7, "AvatarType": 0}, p, None, MemStore())
    check("13303 type 0 (starter) uses F prefix", d2.get("NewChatAvatar") == "F7", d2)


def test_13310_full_v7_field_set() -> None:
    p = make_player()
    d = handlers.action_appearance_info({}, p, None, MemStore())
    need = {"ImageId", "InviteJoinAllianceNum", "Identifier", "AliasBg",
            "HonorList", "AvatarBoxData", "RankBottomId", "FashionEffectInfo",
            "RoleInfoBackGround", "RoleItems", "ChatAvatar", "CustomizeTitleList",
            "ServerVersion", "CustomizeTitleId", "ChatFontColorId",
            "ChatAvatarExpireTime", "ChatBox", "AvatarBoxEffect", "DressList",
            "AvatarBox", "ItemTitleId", "AvatarBoxEffectExpireTime",
            "DressTitle", "RedPoint", "SysTime"}
    check("13310 carries the full v7 field set", need <= set(d), sorted(need - set(d)))
    check("13310 AvatarBox / DressList / RoleItems come from the seed",
          d["AvatarBox"] == 1400
          and [e["ItemId"] for e in d["DressList"]] == [400]
          and {e["ItemId"] for e in d["RoleItems"]} == {400, 1400, 1500}, d)
    check("13310 ChatAvatar mirrors RoleBase",
          d["ChatAvatar"] == p.data["RoleBase"]["ChatAvatar"], d)
    check("13310 capture-only cosmetic ids are the documented v7 values",
          d["ChatBox"] == 4000 and d["AvatarBoxEffect"] == 1900
          and d["RankBottomId"] == 2700 and d["RoleInfoBackGround"] == 5900, d)


def test_13301_13302_13304_13305_persist() -> None:
    p = make_player()
    handlers.action_appearance_fashion({"ItemId": 500}, p, None, MemStore())
    handlers.action_appearance_title({"ItemId": 9}, p, None, MemStore())
    handlers.action_appearance_avatar_box({"AvatarBox": 1401}, p, None, MemStore())
    handlers.action_appearance_scene_skin({"SceneSkinId": 1501}, p, None, MemStore())
    check("13301 adds the dressed fashion id",
          500 in [e["ItemId"] for e in p.data["DressList"]], p.data["DressList"])
    check("13302 sets DressTitle", p.data["RoleBase"]["DressTitle"] == 9)
    check("13304 sets AvatarBox", p.data["RoleBase"]["AvatarBox"] == 1401)
    check("13305 sets DressSceneId in both role slices",
          all(p.data[k]["DressSceneId"] == 1501 for k in ("RoleBase", "RoleData")))


def test_appearance_actions_are_routed() -> None:
    for action in ("13301", "13302", "13303", "13304", "13305", "13310"):
        check(f"{action} is mapped in HANDLERS", action in handlers.HANDLERS)


def main() -> None:
    test_login_cosmetics_seeded_from_config()
    test_login_heals_legacy_empty_cosmetics()
    test_13303_returns_new_chat_avatar()
    test_13310_full_v7_field_set()
    test_13301_13302_13304_13305_persist()
    test_appearance_actions_are_routed()
    print(f"\n{sum(OK)}/{len(OK)} checks passed")
    sys.exit(0 if all(OK) else 1)


if __name__ == "__main__":
    main()
