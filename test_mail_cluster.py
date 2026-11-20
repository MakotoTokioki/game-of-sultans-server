"""Regression tests for the mail cluster (actions 12111-12113).

Shapes come from the decompiled v2.005 callbacks (``_mailMod.lua`` and
``placeMap.mailDialog.lua``) plus ``extract_fields.py``; the only display-text
values asserted are read from the original bundled TcMail* configs.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import v2configs
from gos.defaults import default_player
from gos.handlers_mail import MAIL_HANDLERS, action_mail_claim, action_mail_open
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="mailtest", data=default_player("mailtest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def _stub():
    saved = []

    class StoreStub:
        def save(self, p):
            saved.append(p)

    return StoreStub(), saved, None


def _seed(player, **fields):
    row = {"MailId": 7, "Title": "", "Content": "", "Attachment": [],
           "HasAttachment": 0, "IsRead": 0, "IsReceive": 0}
    row.update(fields)
    player.data.setdefault("RoleMail", []).append(row)
    return row


def main() -> None:
    # --- registration ----------------------------------------------------
    check("MAIL_HANDLERS registers 12111/12112/12113",
          set(MAIL_HANDLERS) == {"12111", "12112", "12113"}, list(MAIL_HANDLERS))

    # --- bundled configs parse and carry the values we use ---------------
    for name in ("TcMailCompetitionRank", "TcMailPosition", "TcMailReplace",
                 "TcMailSenderCfg", "TcMailTrade"):
        check(f"{name} bundles as a non-empty list",
              isinstance(v2configs.load(name), list) and v2configs.load(name),
              v2configs.load(name)[:1])
    sender = next(r for r in v2configs.load("TcMailSenderCfg")
                  if r.get("Id") == 1)
    check("TcMailSenderCfg row 1 has a sourced Name",
          sender.get("Name") == "Henrika", sender)
    check("TcMailReplace row 4 has a sourced Content",
          any(r.get("Id") == 4 and r.get("Content") for r in v2configs.load("TcMailReplace")),
          v2configs.load("TcMailReplace")[3])

    # --- 12112 response shape (harness: Attachment; scene: Title/Content) -
    store, saved, session = _stub()
    p = _player()
    _seed(p, MailId=7, SenderId=1, Attachment=[[2, 500]], HasAttachment=1)
    d = action_mail_open({"MailId": 7}, p, session, store)
    check("12112 has all client-read fields with Lua-safe types",
          all(k in d for k in ("MailId", "Title", "Content", "Attachment"))
          and isinstance(d["Attachment"], list)
          and isinstance(d["Title"], str) and isinstance(d["Content"], str), d)
    check("12112 Title resolved from TcMailSenderCfg (no invented text)",
          d["Title"] == sender["Name"], d)
    check("12112 echoes stored attachment as numeric arrays",
          d["Attachment"] == [[2, 500]], d["Attachment"])
    check("12112 marks the row read and persists",
          p.data["RoleMail"][0]["IsRead"] == 1 and saved == [p], p.data["RoleMail"])

    # every config-keyed title fallback, all from original configs
    for row_kw, cfg_name, cfg_key, cfg_value in (
        ({"Position": 1}, "TcMailPosition", "Position", 1),
        ({"RankType": 2}, "TcMailCompetitionRank", "RankType", 2),
        ({"Lv": 3}, "TcMailTrade", "Lv", 3),
    ):
        cfg_row = next(r for r in v2configs.load(cfg_name) if r.get(cfg_key) == cfg_value)
        pl = _player()
        _seed(pl, **row_kw)
        got = action_mail_open({"MailId": 7}, pl, session, store)
        check(f"12112 title from {cfg_name}",
              bool(got["Title"]) and got["Title"] in str(list(cfg_row.values())), got)
    pl = _player()
    _seed(pl, ReplaceId=4)
    got = action_mail_open({"MailId": 7}, pl, session, store)
    check("12112 content from TcMailReplace",
          bool(got["Content"]) and got["Content"] in
          [str(r.get("Content")) for r in v2configs.load("TcMailReplace")], got)

    # unknown id: still safe, never fabricates a row
    p = _player()
    store, saved, session = _stub()
    d = action_mail_open({"MailId": 999}, p, session, store)
    check("12112 unknown MailId returns empty safe content",
          d["Attachment"] == [] and d["Title"] == "" and d["Content"] == ""
          and not saved, d)
    check("12112 lazily creates RoleMail list", isinstance(p.data.get("RoleMail"), list),
          p.data.get("RoleMail"))

    # --- 12113 claim: real grant + RemainList -----------------------------
    bag_id = 20001  # test-fixture inventory item, never a server-authored reward
    p = _player()
    store, saved, session = _stub()
    silver_before = int(p.data["RoleData"].get("Silver") or 0)
    _seed(p, MailId=7, HasAttachment=1, Attachment=[[2, 500], [bag_id, 2]])
    d = action_mail_claim({"MailId": 7}, p, session, store)
    remain = d.get("RemainList")
    check("12113 returns a Lua-safe RemainList list",
          isinstance(remain, list) and all(
              isinstance(r, dict) and isinstance(r.get("ItemId"), int)
              and isinstance(r.get("Num"), int) for r in remain), d)
    check("12113 grants config-free attachment resources (Silver +500)",
          int(p.data["RoleData"].get("Silver") or 0) == silver_before + 500,
          p.data["RoleData"].get("Silver"))
    check("12113 RemainList includes changed resource and bag item",
          {r["ItemId"] for r in remain} == {2, bag_id}, remain)
    check("12113 marks the row received/read and persists",
          p.data["RoleMail"][0]["IsReceive"] == 1
          and p.data["RoleMail"][0]["IsRead"] == 1 and saved == [p], p.data["RoleMail"])

    # idempotence: second claim grants nothing, RemainList stays empty
    again = action_mail_claim({"MailId": 7}, p, session, store)
    check("12113 is single-claim (no double grant)",
          int(p.data["RoleData"].get("Silver") or 0) == silver_before + 500
          and again.get("RemainList") == [], (again, p.data["RoleData"].get("Silver")))

    # unknown id: safe empty response, no state mutation
    p = _player()
    store, saved, session = _stub()
    d = action_mail_claim({"MailId": 999}, p, session, store)
    check("12113 unknown MailId returns empty RemainList",
          d.get("RemainList") == [] and not saved and p.data.get("RoleMail") == [],
          (d, p.data.get("RoleMail")))

    # third-element attachment ids (client CounsellorId/BeautyId) are ignored
    # by the grant but preserved verbatim for 12112's Attachment/Awards build.
    p = _player()
    _seed(p, MailId=7, HasAttachment=1, Attachment=[[1000, 3, 12]])
    opened = action_mail_open({"MailId": 7}, p, session, store)
    check("12112 preserves third attachment element for client Awards",
          opened["Attachment"] == [[1000, 3, 12]], opened)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
