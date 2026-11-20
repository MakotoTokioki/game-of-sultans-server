"""Sign-in / welfare extras cluster (actions 11503, 11505, 11511, 11512).

Kept in a separate module so the existing gos/handlers.py and gos/defaults.py
stay untouched; the coordinator merges WELFARE_HANDLERS into HANDLERS.

Response shapes come from the decompiled client callbacks:
  * 11503 _welfareMod.action11503      -> callback ignores the response entirely
  * 11505 _welfCardMod.action11505     -> resp.CardStage (single int stage value)
  * 11511 _rechargeMod.action11511     -> callback forwards the response; no fields read
  * 11512 _welfareMod.action11512      -> resp.WelfareVip (list of claimed VIP levels)

Rewards come only from the original v2 TcVIP AwardItem column via the shared
handlers.py helpers; no numbers are fabricated.
"""
from __future__ import annotations

from gos import v2configs
from gos.handlers import (
    _REMAIN_LIST_MAP,
    _as_int,
    _grant_award_pairs,
    attach_remain_list,
)


# --------------------------------------------------------------------------
# config / state helpers (new state is created lazily; no defaults.py edits)
# --------------------------------------------------------------------------
def _vip_rows() -> list[dict]:
    rows = v2configs.load("TcVIP")
    if isinstance(rows, dict):
        rows = list(rows.values())
    return [r for r in rows if isinstance(r, dict)]


def _vip_award_row(vip_lv: int) -> dict | None:
    """TcVIP row whose Level is vip_lv and which actually has an AwardItem.

    The welfare UI rows (see _welfareMod.init) are the TcVIP levels with a
    non-empty AwardItem, plus a synthetic Vip=0 first-recharge row whose award
    lives in ConfigEnvSet.RechargeFirstAward and is delivered by the recharge
    flow, not by the 11503 claim action.
    """
    for row in _vip_rows():
        if _as_int(row.get("Level")) == vip_lv and row.get("AwardItem"):
            return row
    return None


def _claimed_vip(player) -> list[int]:
    return [int(v) for v in player.data.setdefault("WelfareVipClaimed", [])]


# Client welfCardMod maps card index -> card end-time key (showDeadLineTime)
# and uses stage 2 for an active card, 0 for one not owned / expired (init and
# setCardDatas).  No new numeric constants are introduced here.
_CARD_DEADLINE_KEYS = {1: "MonthDeadline", 2: "SuperMonthDeadline", 3: "YearDeadline"}
_CARD_NAME_INDEX = {"monthCard": 1, "supermonthCard": 2, "yearCard": 3}


# --------------------------------------------------------------------------
# handlers
# --------------------------------------------------------------------------
def action_11503(payload: dict, player, session, store) -> dict:
    """_welfareMod.action11503: claim a VIP welfare gift.

    Request carries ``VipLv`` (the welfareScene row clicked).  The client
    callback only calls its success callback and reads no response field, so a
    RemainList-bearing empty envelope is the minimal Lua-safe response.
    """
    vip_lv = _as_int(payload.get("VipLv"))
    claimed = player.data.setdefault("WelfareVipClaimed", [])
    pairs: list[list[int]] = []
    bag_ids: list[int] = []
    row = _vip_award_row(vip_lv) if vip_lv > 0 else None
    if row is not None and vip_lv not in claimed:
        pairs, bag_ids = _grant_award_pairs(player, row.get("AwardItem"))
        claimed.append(vip_lv)
        store.save(player)
    return attach_remain_list(
        {}, player, items=bag_ids,
        resources=[int(p[0]) for p in pairs if int(p[0]) in _REMAIN_LIST_MAP],
    )


def action_11505(payload: dict, player, session, store) -> dict:
    """_welfCardMod.action11505: refresh one welfare card's stage.

    Request carries ``Card`` (1/2/3 for month / supermonth / year, or the
    matching card name).  The callback writes resp.CardStage into
    L0_1.CardStage[Card] via setCardDatas.  Stage is derived from the login
    CardEndTime deadline: 2 while a card is active, 0 when none is owned.
    """
    card = payload.get("Card")
    stages = player.data.setdefault("WelfareCardStage", {})
    key = str(card)
    if key not in stages:
        idx = card if isinstance(card, int) else _CARD_NAME_INDEX.get(str(card))
        deadline_key = _CARD_DEADLINE_KEYS.get(idx)
        deadlines = player.data.get("CardEndTime") or {}
        active = bool(deadline_key and deadlines.get(deadline_key))
        stages[key] = 2 if active else 0
        store.save(player)
    return {"CardStage": int(stages[key])}


def action_11511(payload: dict, player, session, store) -> dict:
    """_rechargeMod.action11511 (orderCheck): verified IAP order delivery.

    The payload is the platform's verified order object and the client passes
    the response straight to its callback, which reads no fields (the shape
    harness flags the callback's own nil-successListener, not a response read).
    Actual payment fulfillment is server-authoritative and cannot be simulated
    from v2 configs, so the neutral empty envelope is returned.
    """
    return {}


def action_11512(payload: dict, player, session, store) -> dict:
    """_welfareMod.action11512: list VIP welfare gifts already claimed.

    The callback does ``for _, vip in pairs(resp.WelfareVip) do ... Stage = 1``
    so the list contains the VIP levels whose award was claimed through 11503
    (Stage 1 = claimed).  Values are filtered against TcVIP's AwardItem rows so
    only levels the client can match are sent.
    """
    valid = {_as_int(r.get("Level")) for r in _vip_rows() if r.get("AwardItem")}
    welfare_vip = [v for v in _claimed_vip(player) if v in valid]
    return {"WelfareVip": welfare_vip}


WELFARE_HANDLERS = {
    "11503": action_11503,
    "11505": action_11505,
    "11511": action_11511,
    "11512": action_11512,
}
