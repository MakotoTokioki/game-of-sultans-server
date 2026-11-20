"""Mail cluster handlers: action 12112 (open mail) and 12113 (claim attachment).

Client shape law (decompiled v2.005):

* ``_mailMod.action12112`` sends ``{MailId=...}``; its response callback stores
  the *whole* response as the mail content and iterates ``A0_3.Attachment``
  (a list of ``[ItemId, Num, OptionalThird]`` arrays).  ``placeMap.mailDialog``
  additionally reads ``Title`` and ``Content`` from that stored content, and
  builds ``Awards`` client-side from ``Attachment``.
* ``_mailMod.action12113`` sends ``{MailId=...}``; the claim button callback in
  ``placeMap.mailDialog.lua`` reads ``A0_5.RemainList`` from the response, so a
  granted attachment must go through ``_grant_award_pairs`` +
  ``attach_remain_list``.

Mail rows live in ``player.data["RoleMail"]`` (a list of row dicts mirroring
the client's ``MailList`` rows) and are created lazily.  No rows are seeded:
the five original ``TcMail*`` tables bundled with the v2 client are
localization/metadata only -- they contain no authored mail instances,
timestamps, or attachment amounts, so seeding would fabricate numbers.  When
another server-side feature *does* create a row carrying a config key, the
display text is resolved from the original bundled config (never invented).
"""
from __future__ import annotations

from . import v2configs
from .handlers import (
    _REMAIN_LIST_MAP,
    _as_int,
    _grant_award_pairs,
    action_mail_list,
    attach_remain_list,
)
from .store import Player, Session, Store


# --------------------------------------------------------------------------
# lazy RoleMail state + config-derived display text
# --------------------------------------------------------------------------
def _mail_rows(player: Player) -> list:
    rows = player.data.get("RoleMail")
    if not isinstance(rows, list):
        rows = []
        player.data["RoleMail"] = rows
    return rows


def _find_row(player: Player, mail_id: int) -> dict | None:
    for row in _mail_rows(player):
        if isinstance(row, dict) and _as_int(row.get("MailId")) == mail_id:
            return row
    return None


def _cfg_row(name: str, key: str, value) -> dict:
    for row in v2configs.load(name):
        if not isinstance(row, dict):
            continue
        if isinstance(value, int):
            match = _as_int(row.get(key)) == value
        else:
            match = str(row.get(key) or "") == str(value)
        if match:
            return row
    return {}


def _row_title(row: dict | None) -> str:
    if not isinstance(row, dict):
        return ""
    if row.get("Title"):
        return str(row["Title"])
    sender = str(_cfg_row("TcMailSenderCfg", "Id", _as_int(row.get("SenderId"))).get("Name") or "")
    if sender:
        return sender
    position = str(_cfg_row("TcMailPosition", "Position", _as_int(row.get("Position"))).get("PositionName") or "")
    if position:
        return position
    rank = str(_cfg_row("TcMailCompetitionRank", "RankType", _as_int(row.get("RankType"))).get("RankName") or "")
    if rank:
        return rank
    destination = str(_cfg_row("TcMailTrade", "Lv", _as_int(row.get("Lv"))).get("Destination") or "")
    return destination


def _row_content(row: dict | None) -> str:
    if not isinstance(row, dict):
        return ""
    if row.get("Content"):
        return str(row["Content"])
    replace_id = _as_int(row.get("ReplaceId"))
    if replace_id:
        # TcMailReplace has one row per (Id, Lang); take the first authored
        # Content for the requested Id, never synthesizing a body ourselves.
        for cfg in v2configs.load("TcMailReplace"):
            if (isinstance(cfg, dict) and _as_int(cfg.get("Id")) == replace_id
                    and cfg.get("Content")):
                return str(cfg["Content"])
    return ""


def _row_attachment(row: dict | None) -> list:
    """Normalize a stored attachment to the client's list-of-arrays shape."""
    if not isinstance(row, dict):
        return []
    raw = row.get("Attachment")
    if not isinstance(raw, (list, tuple)):
        return []
    result: list = []
    for entry in raw:
        if isinstance(entry, dict):
            item_id, num, third = entry.get("ItemId"), entry.get("Num"), entry.get("Third")
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            item_id, num = entry[0], entry[1]
            third = entry[2] if len(entry) >= 3 else None
        else:
            continue
        try:
            pair = [int(item_id or 0), int(num or 0)]
        except (TypeError, ValueError):
            continue
        if third is not None:
            try:
                pair.append(int(third))
            except (TypeError, ValueError):
                pass
        result.append(pair)
    return result


def _request_mail_id(payload: dict) -> int:
    return _as_int(payload.get("MailId", payload.get("mailId")))


# --------------------------------------------------------------------------
# action 12112 -- open/read a mail
# --------------------------------------------------------------------------
def action_mail_open(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """Return the mail content consumed by the _mailMod 12112 callback.

    Every field the client reads is always present: ``Attachment`` is a real
    list (even when empty), ``Title``/``Content`` are strings.  ``Awards`` is
    intentionally not returned: the v2 client builds it from ``Attachment``
    in the response callback before handing the content to the scene.
    """
    mail_id = _request_mail_id(payload)
    row = _find_row(player, mail_id)
    response = {
        "MailId": mail_id,
        "Title": _row_title(row),
        "Content": _row_content(row),
        "Attachment": _row_attachment(row),
    }
    if row is not None:
        row["IsRead"] = 1  # mirrors _mailMod.setMailRead on the client
        store.save(player)
    return response


# --------------------------------------------------------------------------
# action 12113 -- claim the mail attachment
# --------------------------------------------------------------------------
def action_mail_claim(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """Grant an unclaimed attachment once; ship the post-grant RemainList.

    The scene's claim callback reads ``RemainList`` from this response, so
    rewards flow through the standard grant/remain helpers.  A missing mail,
    an empty attachment or an already-claimed mail grants nothing and still
    returns a Lua-safe empty ``RemainList``.
    """
    mail_id = _request_mail_id(payload)
    row = _find_row(player, mail_id)
    pairs: list[list[int]] = []
    bag_ids: list[int] = []
    if row is not None:
        row["IsRead"] = 1
        if _as_int(row.get("IsReceive")) != 1:
            attachment = _row_attachment(row)
            if attachment and _as_int(row.get("HasAttachment"), 1) != 0:
                pairs, bag_ids = _grant_award_pairs(player, attachment)
                row["IsReceive"] = 1
        store.save(player)
    response: dict = {"MailId": mail_id}
    resources = [int(pair[0]) for pair in pairs if int(pair[0]) in _REMAIN_LIST_MAP]
    return attach_remain_list(response, player, items=bag_ids, resources=resources)


# Complete cluster map; action 12111 is the pre-existing mail_list handler.
MAIL_HANDLERS = {
    "12111": action_mail_list,
    "12112": action_mail_open,
    "12113": action_mail_claim,
}
