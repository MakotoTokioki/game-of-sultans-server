"""Chat-screen player profile (11003 / 11006) regressions.

Client: chatScene.chatDialog.lua 2283-2350 (roleServerId comes from the
string MsgData.ServerId, so the cross-server branch fires); gameUi.lua
9100-10453 (showRoleInformation draws the dialog from the response and
reads every field below unguarded).  The old 11006 alliance stub returned
{} -> empty profile.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from gos import config, handlers
from gos.defaults import default_player
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


NUM_FIELDS = ("Lv", "Sex", "Avatar", "Figure", "VipLv", "ShowId", "AllianceId",
              "Intimacy", "Section", "Force", "Intelligence", "Politics",
              "Charm", "IsBlack", "IsFriend")
STR_FIELDS = ("RoleId", "Nickname", "AllianceName")
LIST_FIELDS = ("DressList", "TitleList")


def _player(uid, nick, show_id) -> Player:
    data = default_player(uid, nick)
    data["RoleBase"]["Rid"] = uid
    data["RoleBase"]["Nickname"] = nick
    data["RoleBase"]["ShowId"] = show_id
    data["RoleSection"].update({"ChapterId": 1, "SectionId": 2, "PhaseId": 5})
    return Player(uid=uid, data=data,
                  path=Path("/tmp/opencode/gos_test_players") / f"profile_{uid}.json")


def _fixture():
    self_p = _player("devtest", "Taha", 927665)
    rival = _player("profile_rival", "RivalSultan", 2990000250)
    rival.data["RoleCounsellor"] = [
        {"CounsellorId": 1, "IsLock": 0, "AttributeValue": [10, 20, 30, 40]},
        {"CounsellorId": 2, "IsLock": 1, "AttributeValue": [99, 99, 99, 99]},
    ]
    rival.data["Children"] = [{"Force": 1, "Intelligence": 2, "Politics": 3, "Charm": 4}]
    rival.data["OChildren"] = [{"oChild": {"Force": 5, "Intelligence": 6,
                                           "Politics": 7, "Charm": 8}}]
    rival.data["RoleBeauty"] = {"1": {"Intimacy": 700}}
    store = SimpleNamespace(_players={p.uid: p for p in (self_p, rival)},
                            save=lambda p: None)
    session = SimpleNamespace(jwt="jwt", user_id=self_p.uid)
    return self_p, rival, store, session


def _contract(name, row):
    for key in NUM_FIELDS:
        check(f"{name} {key} is numeric", isinstance(row.get(key), int), row.get(key))
    for key in STR_FIELDS:
        check(f"{name} {key} is a string", isinstance(row.get(key), str), row.get(key))
    for key in LIST_FIELDS:
        check(f"{name} {key} is a table", isinstance(row.get(key), list), row.get(key))
    check(f"{name} Section decodes as chapter.section",
          row["Section"] // 1000 == 1 and (row["Section"] % 1000) // 100 == 2,
          row["Section"])
    check(f"{name} DressList entries have ItemId",
          all(isinstance(e, dict) and "ItemId" in e for e in row["DressList"]),
          row["DressList"])
    check(f"{name} json round-trips", json.loads(json.dumps(row)) == row)


def main() -> None:
    self_p, rival, store, session = _fixture()
    check("11006 wired to the player-detail handler",
          handlers.HANDLERS.get("11006") is handlers.action_rank_cross_player_detail)
    row = handlers.action_rank_cross_player_detail(
        {"RoleId": "devtest", "QueryServerId": "1"}, self_p, session, store)
    check("11006 returns the clicked player's profile",
          row.get("RoleId") == "devtest" and row.get("Nickname") == "Taha", row)
    check("11006 ShowId mirrors RoleBase", row.get("ShowId") == 927665)
    _contract("11006", row)
    check("11006 empty/unknown RoleId -> {}",
          handlers.action_rank_cross_player_detail({}, self_p, session, store) == {}
          and handlers.action_rank_cross_player_detail(
              {"RoleId": "nobody"}, self_p, session, store) == {})

    detail = handlers.action_rank_player_detail({"QueryId": "profile_rival"},
                                                self_p, session, store)
    check("11003 known player detail is non-empty",
          detail.get("RoleId") == "profile_rival", detail)
    _contract("11003", detail)
    check("11003 unknown player -> {}",
          handlers.action_rank_player_detail({"QueryId": "nobody"},
                                             self_p, session, store) == {})

    check("four attributes = counsellor + children, locked excluded",
          [detail["Force"], detail["Intelligence"], detail["Politics"], detail["Charm"]]
          == [16, 28, 40, 52], detail)
    check("Intimacy = real wife total", detail["Intimacy"] == 700)
    ident = handlers._chat_identity(self_p)
    check("chat ServerId stays the original string shape",
          isinstance(ident["ServerId"], str)
          and ident["ServerId"] == str(config.SERVER_ID), ident["ServerId"])
    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
