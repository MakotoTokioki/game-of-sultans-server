"""Alliance core cluster 11601-11649 — actions NOT already in gos/handlers.py.

The 7 implemented actions (11601/11602/11603/11607/11617/11629/11645) stay in
handlers.py; this module only carries the remaining 33.  Shapes come from the
v2.005 decompiled `_allianceMod.lua` callbacks (walk backward from
``actionNNNNN = Lxx`` to the most recent ``function Lxx(``) and the scene
consumers under decomp_all/scene.  Numbers come only from original v2 config
rows bundled in gos/v2configs_data (TcAlliance*).

Single-machine policy: this is a server-authoritative, multi-player system.
Query/list responses that would name other players (applicants, members,
boats, boss records, task detail) return Lua-safe EMPTY lists/maps.  Nothing
fabricates a second player.  Mutation actions persist under
``player.data["AllianceData"]`` (lazy via setdefault) and, for alliance-wide
fields, through the existing handlers.py alliance registry.

Unsourced fields (no original v2 config exists) are marked UNSOURCED inline
and use the neutral Lua value 0/{} /"" instead of an invented number.
"""
from __future__ import annotations

from . import v2configs
from .handlers import (
    Player, Session, Store,
    _REMAIN_LIST_MAP,
    _alliance_lock,
    _alliance_member_of,
    _alliance_registry,
    _alliance_save,
    _as_int,
    _game_date_string,
    _grant_award_pairs,
    _role_item_add,
    _role_item_consume,
    _role_item_count,
    attach_remain_list,
)


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------
def _pstate(player: Player) -> dict:
    """Lazy per-player alliance slice (never invent a default slice in defaults.py)."""
    st = player.data.setdefault("AllianceData", {})
    if not isinstance(st, dict):
        st = {}
        player.data["AllianceData"] = st
    st.setdefault("ContributionNum", 0)
    st.setdefault("ContributionTop", 0)
    st.setdefault("ContributionTime", "")
    st.setdefault("ExchangeItems", {})        # map itemId(str) -> times exchanged
    st.setdefault("Trades", {})               # map tradeId(str) -> trade state
    st.setdefault("NextTradeId", 1)
    st.setdefault("ClaimedTaskStages", {})    # map taskId(str) -> [stage, ...]
    st.setdefault("BatchSendingStatus", 0)
    st.setdefault("NoticeChangeFreeTimes", 0)
    st.setdefault("NoticeChangePrice", 0)     # UNSOURCED: no v2 config table
    st.setdefault("GiftStatus", {})           # map giftId -> status string
    st.setdefault("CurrentPurchasedNum", {"CashPack": {}, "AlliancePack": {}})
    st.setdefault("NeedCheck", 0)
    st.setdefault("FlagId", 1)
    st.setdefault("Position", 1)
    return st


def _my_alliance(player: Player) -> tuple[str, dict | None, dict | None]:
    guid, membership = _alliance_member_of(player)
    if not guid:
        return "", None, None
    alliance = _alliance_registry()["items"].get(guid)
    if not isinstance(alliance, dict):
        return guid, membership, None
    return guid, membership, alliance


def _lv(alliance: dict | None) -> int:
    return max(1, _as_int(alliance.get("AllianceLv") if alliance else None, 1))


def _fortune(alliance: dict | None) -> int:
    return _as_int(alliance.get("AllianceFortune") if alliance else None)


def _cfg(name: str) -> list:
    try:
        rows = v2configs.load(name) or []
    except Exception:
        return []
    return [r for r in rows if isinstance(r, dict)]


def _cfg_lv_row(lv: int) -> dict:
    rows = _cfg("TcAllianceLvCfg")
    return next((r for r in rows if _as_int(r.get("AllianceLv")) == lv),
                rows[0] if rows else {})


def _consume_cost(player: Player, consume) -> tuple[bool, int]:
    """Consume one TcAlliance* ConsumeItem pair [id, num].

    Returns (ok, changed_item_id).  Resource ids 1-6 come from RoleData,
    bag ids from RoleItem.  Availability is checked before deducting so the
    action is idempotent/refusal-safe.
    """
    if not isinstance(consume, (list, tuple)) or len(consume) < 2:
        return False, 0
    item_id = _as_int(consume[0])
    num = max(0, _as_int(consume[1]))
    if item_id <= 0 or num <= 0:
        return True, 0
    if item_id in _REMAIN_LIST_MAP:
        key = _REMAIN_LIST_MAP[item_id]
        role = player.data.setdefault("RoleData", {})
        if _as_int(role.get(key)) < num:
            return False, item_id
        role[key] = _as_int(role.get(key)) - num
        return True, item_id
    if _role_item_count(player, item_id) < num:
        return False, item_id
    _role_item_consume(player, item_id, num)
    return True, item_id


def _save_all(player: Player, store: Store) -> None:
    with _alliance_lock:
        _alliance_save()
    store.save(player)


# --------------------------------------------------------------------------
# 11604-11614 — alliance management / notices / applications
# --------------------------------------------------------------------------
def action_alliance_rename(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11604 {AllianceGuid, NewName}: the callback mirrors NewName
    into the client's own alliance data; it reads NO response field, so {} is
    the exact safe shape.  Persist the rename to the registry when possible.
    """
    new_name = str(payload.get("NewName") or "").strip()
    _, _, alliance = _my_alliance(player)
    if new_name and alliance:
        with _alliance_lock:
            alliance["AllianceName"] = new_name
        _save_all(player, store)
    return {}


def action_alliance_notice_change(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11605 {AllianceGuid, FaceBook, OutsideNotice,
    InsideNotice, IsFree}: callback reads HintData.Type (guarded by
    `if HintData then`), NoticeChangeFreeTimes and IsNeedPopup (guarded by a
    caller callback).  Mirror the request into the registry and echo counts.
    """
    st = _pstate(player)
    _, _, alliance = _my_alliance(player)
    if alliance:
        with _alliance_lock:
            for field in ("FaceBook", "OutsideNotice", "InsideNotice"):
                if payload.get(field) is not None:
                    alliance[field] = str(payload[field])
    free = _as_int(alliance.get("NoticeChangeFreeTimes") if alliance else None)
    if not free:
        free = _as_int(st.get("NoticeChangeFreeTimes"))
    st["NoticeChangeFreeTimes"] = free
    _save_all(player, store)
    return {
        "HintData": {},                 # Type nil -> client falls through (safe)
        "NoticeChangeFreeTimes": free,
        "IsNeedPopup": 0,               # UNSOURCED: neutral "no popup"
    }


def action_alliance_apply_list(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11606: callback reads ApplyMember (list) and computes
    ApplyNum = #ApplyMember locally.  Applicants are other players -> [].
    """
    _, _, alliance = _my_alliance(player)
    raw = alliance.get("ApplyMember") or [] if alliance else []
    members = [dict(m) for m in raw if isinstance(m, dict)] if isinstance(raw, list) else []
    return {"ApplyMember": members}


def action_alliance_set_need_check(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11608 {AllianceGuid, NeedCheck}: callback mirrors the
    request NeedCheck into client data; no response field is read.
    """
    st = _pstate(player)
    need = 1 if _as_int(payload.get("NeedCheck")) else 0
    st["NeedCheck"] = need
    _, _, alliance = _my_alliance(player)
    if alliance:
        with _alliance_lock:
            alliance["NeedCheck"] = need
        _save_all(player, store)
    return {}


def action_alliance_apply_decide(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11609 {AllianceGuid, ApplyRid, IsAgree}: callback updates
    ApplyNum/AllianceMenNum locally; no response field read.  Remove the
    applicant from our registry so a re-query stays consistent.
    """
    rid = str(payload.get("ApplyRid") or "")
    agree = _as_int(payload.get("IsAgree"))
    _, membership, alliance = _my_alliance(player)
    if alliance and rid:
        with _alliance_lock:
            raw = alliance.get("ApplyMember") or []
            alliance["ApplyMember"] = [m for m in raw
                                        if not (isinstance(m, dict)
                                                and str(m.get("RoleId") or "") == rid)]
            if agree:
                alliance.setdefault("Members", {})[rid] = {
                    "Position": 1, "JoinTime": _game_date_string(),
                    "Power": 0, "Name": "",
                }
        _save_all(player, store)
    return {}


def action_alliance_apply_refuse_all(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11610 {AllianceGuid, RefuseList}: callback clears ApplyNum
    and the applicant cache locally; no response field read.
    """
    _, _, alliance = _my_alliance(player)
    if alliance:
        with _alliance_lock:
            alliance["ApplyMember"] = []
        _save_all(player, store)
    return {}


def action_alliance_appoint(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11611 {AllianceGuid, OperateId, Position}: callback edits
    its cached member table; no response field read.  Position is sourced from
    TcAlliancePosition (PositionLv 1=member .. 4=leader).  OperateId names
    another player, so on a single-machine server only a self-target is real.
    """
    rid = str(payload.get("OperateId") or "")
    position = _as_int(payload.get("Position"))
    _, _, alliance = _my_alliance(player)
    if alliance and rid and rid == str(player.uid):
        with _alliance_lock:
            members = alliance.setdefault("Members", {})
            if position == 0:
                members.pop(rid, None)
            elif isinstance(members.get(rid), dict):
                members[rid]["Position"] = position
        _pstate(player)["Position"] = position
        _save_all(player, store)
    return {}


def action_alliance_quit(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11612 {AllianceGuid}: callback clears client-side
    AllianceGuid/AllianceId; no response field read.
    """
    guid, _, alliance = _my_alliance(player)
    if alliance:
        with _alliance_lock:
            (alliance.get("Members") or {}).pop(str(player.uid), None)
    base = player.data.setdefault("RoleBase", {})
    base.pop("AllianceGuid", None)
    base.pop("AllianceId", None)
    player.data.pop("AllianceGuid", None)
    _pstate(player)["Position"] = 1
    _save_all(player, store)
    return {}


def action_alliance_quit_password(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11613 {AllianceGuid, Password}: quit-with-password.
    Callback clears client data and sets QuitTime locally; no response field.
    Password verification is server-authoritative; a local quit is safe.
    """
    return action_alliance_quit(payload, player, session, store)


def action_alliance_member_list(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11614: callback reads BatchSendingStatus, MemberList
    (list of OTHER players), NoticeChangeFreeTimes, NoticeChangePrice.
    MemberList is intentionally empty — fabricating members is forbidden.
    The two notice counters are UNSOURCED (no v2 config) and default to 0.
    """
    st = _pstate(player)
    return {
        "BatchSendingStatus": _as_int(st.get("BatchSendingStatus")),
        "MemberList": [],
        "NoticeChangeFreeTimes": _as_int(st.get("NoticeChangeFreeTimes")),
        "NoticeChangePrice": _as_int(st.get("NoticeChangePrice")),
    }


# --------------------------------------------------------------------------
# 11615-11616 — donate / exchange
# --------------------------------------------------------------------------
def _donate_response(alliance: dict | None, st: dict) -> dict:
    return {
        "AllianceLv": _lv(alliance),
        "ContributionTime": str(st.get("ContributionTime") or _game_date_string()),
        "ContributionNum": _as_int(st.get("ContributionNum")),
        "ContributionTop": _as_int(st.get("ContributionTop")),
        "AllianceExp": _as_int(alliance.get("AllianceExp") if alliance else None),
        "AllianceFortune": _fortune(alliance),
    }


def action_alliance_donate(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11615 {AllianceGuid, ContributionType}: callback writes
    AllianceLv, ContributionTime, ContributionNum, ContributionTop,
    AllianceExp, AllianceFortune.  All amounts come from the original
    TcAllianceContribution row (ConsumeItem / GainFortune / GainExp /
    GainPersonalDonate).  ContributionTop is the local maximum (a real
    cross-player top is server-authoritative and cannot be fabricated).
    """
    st = _pstate(player)
    _, _, alliance = _my_alliance(player)
    ctype = _as_int(payload.get("ContributionType"), 1)
    row = next((r for r in _cfg("TcAllianceContribution")
                if _as_int(r.get("ContributionType")) == ctype), None)
    if not alliance or not row:
        return _donate_response(alliance, st)

    ok, changed = _consume_cost(player, row.get("ConsumeItem"))
    if not ok:
        return _donate_response(alliance, st)

    fortune = _as_int(row.get("GainFortune"))
    exp = _as_int(row.get("GainExp"))
    personal = _as_int(row.get("GainPersonalDonate"))
    with _alliance_lock:
        alliance["AllianceFortune"] = _fortune(alliance) + fortune
        alliance["AllianceExp"] = _as_int(alliance.get("AllianceExp")) + exp
    st["ContributionNum"] = _as_int(st.get("ContributionNum")) + personal
    st["ContributionTop"] = max(_as_int(st.get("ContributionTop")),
                                _as_int(st.get("ContributionNum")))
    st["ContributionTime"] = _game_date_string()
    out = _donate_response(alliance, st)
    _save_all(player, store)
    resources = [changed] if changed in _REMAIN_LIST_MAP else []
    items = [] if changed in _REMAIN_LIST_MAP else [changed]
    return attach_remain_list(out, player, items=items, resources=resources)


def action_alliance_exchange(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11616 {AllianceGuid, ItemId}: callback writes
    ExchangeItems (map itemId -> exchanged count) and ContributionNum.
    Costs/limits come from TcAllianceExchange (Consume, NumLimit, AllianceLv).
    The config has NO AwardItem column, so no item grant amount is invented —
    only the server-authoritative counters are updated.
    """
    st = _pstate(player)
    _, _, alliance = _my_alliance(player)
    counts = st.setdefault("ExchangeItems", {})
    if not isinstance(counts, dict):
        counts = {}
        st["ExchangeItems"] = counts
    contribution = _as_int(st.get("ContributionNum"))
    item_id = _as_int(payload.get("ItemId"))
    rows = [r for r in _cfg("TcAllianceExchange")
            if _as_int(r.get("ItemId")) == item_id
            and _as_int(r.get("AllianceLv")) <= _lv(alliance)]
    row = max(rows, key=lambda r: _as_int(r.get("AllianceLv")), default=None) if rows else None
    key = str(item_id)
    used = _as_int(counts.get(key))
    limit = _as_int(row.get("NumLimit")) if row else 0
    cost = _as_int(row.get("Consume")) if row else 0
    if not alliance or row is None or used >= limit or contribution < cost:
        return {"ExchangeItems": dict(counts), "ContributionNum": contribution}
    counts[key] = used + 1
    st["ContributionNum"] = contribution - cost
    store.save(player)
    return {"ExchangeItems": dict(counts), "ContributionNum": st["ContributionNum"]}


# --------------------------------------------------------------------------
# 11618-11623 — alliance voyage / trade
# --------------------------------------------------------------------------
def action_alliance_apply_cancel(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11618 {AllianceGuid}: callback forwards response to the
    caller; it reads no field.  {} is the exact safe shape.
    """
    return {}


def action_alliance_trade_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11619: callback stores TradeLevels, ContributionNum,
    ContributionTop, TradeCounsellors, TradeData and Awards, then feeds
    TradeCounsellors/TradeData into setMinistersData / setRouteData.
    setRouteData seeds all 9 (TcAllianceTrade Lv 1..9) rows itself, so empty
    maps are the correct Lua-safe "no other player / no running trade" state.
    """
    st = _pstate(player)
    return {
        "TradeLevels": [],
        "ContributionNum": _as_int(st.get("ContributionNum")),
        "ContributionTop": _as_int(st.get("ContributionTop")),
        "TradeCounsellors": {},
        "TradeData": {},
        "Awards": {},
    }


def action_alliance_trade_start(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11620 {AllianceGuid, TradeLv, UseGold}: callback reads
    AllianceFortune and TradeId, then seeds a local route with
    TradeValue=0/Point=0/Status=0.  Costs come from TcAllianceTrade
    (NeedGold, NeedFortune); TradeId is a locally generated id (UNSOURCED).
    """
    st = _pstate(player)
    _, _, alliance = _my_alliance(player)
    level = _as_int(payload.get("TradeLv"), 1)
    row = next((r for r in _cfg("TcAllianceTrade")
                if _as_int(r.get("Lv")) == level), None)
    if not alliance or row is None:
        return {"AllianceFortune": _fortune(alliance), "TradeId": 0}

    gold_cost = _as_int(row.get("NeedGold"))
    fortune_cost = _as_int(row.get("NeedFortune"))
    role = player.data.setdefault("RoleData", {})
    if _as_int(role.get("Gold")) < gold_cost or _fortune(alliance) < fortune_cost:
        return {"AllianceFortune": _fortune(alliance), "TradeId": 0}
    role["Gold"] = _as_int(role.get("Gold")) - gold_cost
    with _alliance_lock:
        alliance["AllianceFortune"] = _fortune(alliance) - fortune_cost
    trade_id = _as_int(st.get("NextTradeId"), 1)
    st["NextTradeId"] = trade_id + 1
    trades = st.setdefault("Trades", {})
    if not isinstance(trades, dict):
        trades = {}
        st["Trades"] = trades
    trades[str(trade_id)] = {
        "TradeLv": level, "TradeValue": 0, "Point": 0,
        "Status": 0, "NextPointSeconds": 0, "SelfCounsellorNum": 0,
        "OpenTime": _game_date_string(),
    }
    out = {"AllianceFortune": _fortune(alliance), "TradeId": trade_id}
    _save_all(player, store)
    return attach_remain_list(out, player, resources=[1])


def action_alliance_trade_boats(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11621: callback passes TradeJoinData to setBoatData,
    which pairs() it and splits MyBoat/OthersBoat by RoleId.  Boats belong to
    other players -> empty list (setBoatData then leaves both lists empty).
    """
    return {"TradeJoinData": []}


def action_alliance_trade_join(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11622 {AllianceGuid, TradeId, CounsellorId, UseItem}:
    callback reads TradeValue, Point, Status, NextPointSeconds, TradeLevels,
    TradeCounsellors.  TradeValue/Point targets come from TcAllianceTrade
    (TargetTradeValue, PointNum); progress starts at 0.  NextPointSeconds is
    UNSOURCED (no v2 config) and returns 0.  TradeCounsellors is a map of
    OTHER players -> {}.
    """
    st = _pstate(player)
    trade_id = str(payload.get("TradeId") or "")
    level = _as_int(payload.get("TradeLv"), 1)
    row = next((r for r in _cfg("TcAllianceTrade")
                if _as_int(r.get("Lv")) == level), None)
    target = _as_int(row.get("TargetTradeValue")) if row else 0
    point_num = _as_int(row.get("PointNum")) if row else 0
    trades = st.setdefault("Trades", {})
    if not isinstance(trades, dict):
        trades = {}
        st["Trades"] = trades
    state = trades.get(trade_id)
    if not isinstance(state, dict):
        state = {"TradeLv": level, "TradeValue": 0, "Point": 0,
                 "Status": 0, "NextPointSeconds": 0, "SelfCounsellorNum": 0}
        trades[trade_id] = state
    store.save(player)
    return {
        "TradeValue": _as_int(state.get("TradeValue")),
        "Point": _as_int(state.get("Point")),
        "Status": _as_int(state.get("Status")),
        "NextPointSeconds": _as_int(state.get("NextPointSeconds")),
        "TradeLevels": [],
        "TradeCounsellors": {},
    }


def action_alliance_trade_leave(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11623 {AllianceGuid, TradeId}: callback reads no field;
    it only invokes the caller.  Drop our local trade state if present.
    """
    st = _pstate(player)
    trade_id = str(payload.get("TradeId") or "")
    trades = st.get("Trades")
    if isinstance(trades, dict) and trade_id:
        trades.pop(trade_id, None)
        store.save(player)
    return {}


# --------------------------------------------------------------------------
# 11624-11632 — alliance boss, transfer, flags, passwords
# --------------------------------------------------------------------------
def action_alliance_boss_donate(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11624 {allianceId, bossLv}: callback reads no response
    field.  Any cost validation is server-authoritative (TcAllianceInstance
    Donate/NeedGold/NeedFortune); no client-visible value to fabricate.
    """
    return {}


def action_alliance_boss_records(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11625: callback passes recordList to the caller.  Boss
    damage ranks are other players -> empty list.
    """
    return {"recordList": []}


def action_alliance_transfer_leader(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11626 {AllianceGuid, TransferRid, Password}: callback
    sets the local Position from the response.  After handing over leadership
    the former leader becomes deputy: TcAlliancePosition PositionLv 4 is the
    leader (TransferPosition=1), so the new local position is 3 (the row one
    step below, which exists in the same config).
    """
    st = _pstate(player)
    rows = _cfg("TcAlliancePosition")
    leader_lv = next((_as_int(r.get("PositionLv"))
                      for r in rows if _as_int(r.get("TransferPosition")) == 1), 4)
    new_pos = leader_lv - 1
    if not any(_as_int(r.get("PositionLv")) == new_pos for r in rows):
        new_pos = 1
    _, membership, alliance = _my_alliance(player)
    if alliance and isinstance(membership, dict):
        with _alliance_lock:
            membership["Position"] = new_pos
        st["Position"] = new_pos
        _save_all(player, store)
    return {"Position": new_pos}


def action_alliance_boss_operation(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11627 {OprType}: callback reads no field.  {} is safe."""
    return {}


def action_alliance_password_change(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11628 {AllianceGuid, [flag, NewPassword] | [OldPassword,
    NewPassword]}: callback reads no field.  Persist the password locally.
    """
    _, _, alliance = _my_alliance(player)
    new_password = payload.get("NewPassword")
    if alliance and new_password is not None:
        with _alliance_lock:
            alliance["Password"] = str(new_password)
        _save_all(player, store)
    return {}


def action_alliance_set_flag_1(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11631 {AllianceGuid, FlagId}: callback mirrors the
    request FlagId into client data; no response field read.
    """
    flag = _as_int(payload.get("FlagId"), 1)
    st = _pstate(player)
    st["FlagId"] = flag
    _, _, alliance = _my_alliance(player)
    if alliance:
        with _alliance_lock:
            alliance["FlagId"] = flag
        _save_all(player, store)
    return {}


def action_alliance_set_flag_2(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11632 {AllianceGuid, FlagId}: same flag setter as 11631
    (two entry points in the client); no response field read.
    """
    return action_alliance_set_flag_1(payload, player, session, store)


# --------------------------------------------------------------------------
# 11635 — batch sending
# --------------------------------------------------------------------------
def action_alliance_batch_sending(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11635 {AllianceGuid, DeviceId, Text}: callback reads
    BatchSendingStatus and sets it locally.  The real status is
    server-authoritative (group messaging to other players); 0 is the neutral
    "idle" value (UNSOURCED — no v2 config).
    """
    st = _pstate(player)
    status = _as_int(st.get("BatchSendingStatus"))
    return {"BatchSendingStatus": status}


# --------------------------------------------------------------------------
# 11640-11642 — alliance daily tasks
# --------------------------------------------------------------------------
def _task_info(player: Player, alliance: dict | None) -> list:
    """Build AllianceTaskInfo rows from TcAllianceDailyTaskCfg.

    The client reads TaskType, TaskLevel, TaskId, CurrentNum,
    AwardRequireNums and GetAwardStages on AllianceTaskInfo[1].  Config rows
    are grouped by TaskType (TaskStage 1..5 inside each group); the Lua table
    key 1..40 is used as TaskId because that is the only stable id present.
    """
    st = _pstate(player)
    rows = _cfg("TcAllianceDailyTaskCfg")
    by_type: dict[int, list[dict]] = {}
    for index, row in enumerate(rows, 1):
        by_type.setdefault(_as_int(row.get("TaskType")), []).append(row)
    claimed = st.get("ClaimedTaskStages", {})
    if not isinstance(claimed, dict):
        claimed = {}
        st["ClaimedTaskStages"] = claimed
    out = []
    for task_type in sorted(by_type):
        stages = sorted(by_type[task_type], key=lambda r: _as_int(r.get("TaskStage")))
        first = stages[0]
        task_id = rows.index(first) + 1 if first in rows else task_type
        done = claimed.get(str(task_id)) or []
        if not isinstance(done, list):
            done = []
        out.append({
            "TaskId": task_id,
            "TaskType": task_type,
            "TaskLevel": _lv(alliance),
            "CurrentNum": 0,
            "AwardRequireNums": [_as_int(r.get("RequireNum")) for r in stages],
            "GetAwardStages": [int(s) for s in done],
        })
    return out


def action_alliance_task_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11640: callback stores AllianceTaskInfo (list of task
    dicts) into client data.  Values are config-derived; per-player progress
    is lazy state (no progress is fabricated).
    """
    _, _, alliance = _my_alliance(player)
    return {"AllianceTaskInfo": _task_info(player, alliance)}


def action_alliance_task_claim(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11641 {Stage, AllianceGuid, TaskId}: callback forwards
    the response to the caller; no field read.  Grant the stage award from the
    matching TcAllianceDailyTaskCfg row (TaskType via TaskId -> config key,
    TaskStage via Stage), once per stage, then persist claimed stages.
    """
    st = _pstate(player)
    stage = _as_int(payload.get("Stage"))
    task_id = _as_int(payload.get("TaskId"))
    rows = _cfg("TcAllianceDailyTaskCfg")
    row = rows[task_id - 1] if 1 <= task_id <= len(rows) else None
    if row is None:
        return {}
    task_type = _as_int(row.get("TaskType"))
    target = next((r for r in rows
                   if _as_int(r.get("TaskType")) == task_type
                   and _as_int(r.get("TaskStage")) == stage), None)
    if target is None:
        return {}
    claimed = st.setdefault("ClaimedTaskStages", {})
    if not isinstance(claimed, dict):
        claimed = {}
        st["ClaimedTaskStages"] = claimed
    key = str(task_id)
    done = claimed.get(key)
    if not isinstance(done, list):
        done = []
    if stage in done:
        return {}
    done.append(stage)
    claimed[key] = done
    pairs, bag_ids = _grant_award_pairs(player, target.get("AwardItem") or [])
    store.save(player)
    out = {}
    if pairs:
        return attach_remain_list(out, player, items=bag_ids,
                                  resources=[int(p[0]) for p in pairs
                                             if int(p[0]) in _REMAIN_LIST_MAP])
    return out


def action_alliance_task_detail(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11642: callback reads TaskDetail (list of per-member
    contribution rows with Num/Lv/ChatAvatar/RoleId ...).  Those are other
    players -> empty list (client renders its empty prompt).
    """
    return {"TaskDetail": []}


# --------------------------------------------------------------------------
# 11646-11649 — alliance gift packs / store
# --------------------------------------------------------------------------
def action_alliance_gift_claim(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11646 {AllianceGuid, GiftId, GiftList}: callback reads
    Status, CurrentPackStatus (map giftId -> status), AddPackCfg (list) and
    RemainListCfg (only when Status == "CLAIMED").  No gift config ships with
    v2.005, so no reward amount is invented: empty maps/list and a CLAIMED
    status are the safe, self-contained result.
    """
    st = _pstate(player)
    gift_id = str(payload.get("GiftId") or "")
    status = st.setdefault("GiftStatus", {})
    if not isinstance(status, dict):
        status = {}
        st["GiftStatus"] = status
    claim_status = "CLAIMED"  # UNSOURCED: status string from the client enum
    if gift_id:
        status[gift_id] = claim_status
    store.save(player)
    return {
        "Status": claim_status,
        "CurrentPackStatus": {gift_id: claim_status} if gift_id else {},
        "AddPackCfg": [],
        "RemainListCfg": [],
    }


def action_alliance_pack_buy(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11647 {AllianceGuid, PackageId}: callback reads
    CurrentPurchasedNum, AllianceFortune and Status.  Pack pricing is
    UNSOURCED (no v2 pack config), so no cost is invented; purchase counts
    are tracked per package id in lazy state and the status is "NORMAL".
    """
    st = _pstate(player)
    _, _, alliance = _my_alliance(player)
    package_id = str(payload.get("PackageId") or "")
    purchased = st.setdefault("PurchasedPackages", {})
    if not isinstance(purchased, dict):
        purchased = {}
        st["PurchasedPackages"] = purchased
    count = _as_int(purchased.get(package_id)) + 1
    if package_id:
        purchased[package_id] = count
    store.save(player)
    return {
        "CurrentPurchasedNum": count,
        "AllianceFortune": _fortune(alliance),
        "Status": "NORMAL",  # UNSOURCED: client status enum value
    }


def action_alliance_store_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11648 {AllianceGuid}: callback reads CashPackCfg and
    AlliancePackCfg (both maps, sorted by Price by a client helper), plus
    PurchaseCreditNeed / CashPackClaimNeed / AlliancePackClaimNeed and
    CurrentPurchasedNum (a map keyed by "CashPack"/"AlliancePack" -> map of
    pack id -> purchased count).  No v2 pack config exists, so all config
    maps are empty and all needs are 0 (UNSOURCED).
    """
    st = _pstate(player)
    purchased = st.get("CurrentPurchasedNum")
    if not isinstance(purchased, dict):
        purchased = {"CashPack": {}, "AlliancePack": {}}
        st["CurrentPurchasedNum"] = purchased
    for tab in ("CashPack", "AlliancePack"):
        if not isinstance(purchased.get(tab), dict):
            purchased[tab] = {}
    return {
        "CashPackCfg": {},
        "AlliancePackCfg": {},
        "PurchaseCreditNeed": 0,
        "CashPackClaimNeed": 0,
        "AlliancePackClaimNeed": 0,
        "CurrentPurchasedNum": {
            "CashPack": dict(purchased["CashPack"]),
            "AlliancePack": dict(purchased["AlliancePack"]),
        },
    }


def action_alliance_pack_index(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod action11649 (decompiled as action11469) {AllianceGuid,
    Index}: its callback only calls A1_2() and reads no response field.
    """
    return {}


ALLIANCE_MORE_HANDLERS = {
    "11604": action_alliance_rename,
    "11605": action_alliance_notice_change,
    "11606": action_alliance_apply_list,
    "11608": action_alliance_set_need_check,
    "11609": action_alliance_apply_decide,
    "11610": action_alliance_apply_refuse_all,
    "11611": action_alliance_appoint,
    "11612": action_alliance_quit,
    "11613": action_alliance_quit_password,
    "11614": action_alliance_member_list,
    "11615": action_alliance_donate,
    "11616": action_alliance_exchange,
    "11618": action_alliance_apply_cancel,
    "11619": action_alliance_trade_info,
    "11620": action_alliance_trade_start,
    "11621": action_alliance_trade_boats,
    "11622": action_alliance_trade_join,
    "11623": action_alliance_trade_leave,
    "11624": action_alliance_boss_donate,
    "11625": action_alliance_boss_records,
    "11626": action_alliance_transfer_leader,
    "11627": action_alliance_boss_operation,
    "11628": action_alliance_password_change,
    "11631": action_alliance_set_flag_1,
    "11632": action_alliance_set_flag_2,
    "11635": action_alliance_batch_sending,
    "11640": action_alliance_task_info,
    "11641": action_alliance_task_claim,
    "11642": action_alliance_task_detail,
    "11646": action_alliance_gift_claim,
    "11647": action_alliance_pack_buy,
    "11648": action_alliance_store_info,
    "11649": action_alliance_pack_index,
}
