"""Feast / banquet (dinner) cluster — actions 12400-12467 (27 actions).

Standalone handler module.  The coordinator merges ``FEAST_HANDLERS`` into
``gos.handlers.HANDLERS`` (see ``_merge_cluster_handlers``); this file never
edits handlers.py / defaults.py.  All persistent state is created lazily with
``setdefault`` under ``player.data["Feast"]``.

Response shapes were recovered with
``python3 tools/lua_shape_harness/extract_fields.py ACTION`` and verified by
walking BACKWARD in ``modules/_feastModOld.lua`` (12400-12409) and
``modules/_feastMod.lua`` (12450-12467) from each ``actionNNNNN = Lxx``
assignment to the most recent ``function Lxx(...)`` and reading its nested
``netProtocol.action`` callback:

    12400 Dinners, HavingDinnerNum, OldDinnerCloseTime, SelfDinner
    12401 DinnerId
    12402/12464 DinnerPoint, GoldRefreshNum, ItemList, LastAuthRefreshTime
    12405 DinnerList
    12408/12465 ItemList
    12409/12466 RemainScore
    12450 Dinners, Famous, FamousTimes, FinishData, IsFamous, SelfDinner
    12451 DinnerId
    12452 Famous, FamousFeastList, SelfFeast, TimeData
    12453 SelfFeast
    12455 FinishData
    12456 LogList, Reward, Score, SeatList, Status
    12457 FinishData, HostList, InviteStatus, LogList, SeatList, Status
    12461 Status
    12467 FinishData, LogList, Score, SeatList, Status
    12403/12404/12406/12407/12458/12459/12460/12462/12463 -> no fields read

Config-derived numbers (original v2 tables only):
  * ConfigEnvSet.DinnerGoldRefreshNum / DinnerHavingNum / DinnerAuthRefreshTimeCd
  * TcDinnerPointExchangeCfg.ItemId / ConsumePoint / WeightItem -> exchange
    ItemList and the point cost debited by 12409/12466.
  * TcDinnerRefactorConfig.WiseDinnerLimit -> FamousTimes;
    WiseDinnerAward -> Famous.WiseKingFashion award list.
  * TcDinnerRefactorFinishAward tier rows (DinnerType, BeginScore/EndScore,
    MasterAward, GuestAward) -> Reward / master-award grant.
  * TcDinnerRefactorSeatCfg.EndIndex (SeatType 4) -> HostList.MaxSeat.

UNSOURCED / server-authoritative values (documented, NOT fabricated as
balance data): all other players' dinners (Dinners, DinnerList, SeatList,
LogList, FamousFeastList, HostList, FinishData), DinnerId (the server allocates
it), live scores, invitation/read status, the exchange ItemList positional
layout beyond ItemId/ConsumePoint/WeightItem (the client's getStoreData indexes
[1]..[4] numerically), and the award-tier *roll* (we deliberately grant only
the lowest config tier, once).  These are returned as Lua-safe neutral defaults
(0 / "" / False / {} / []) and must be simulated by the real game server.
"""
from __future__ import annotations

from gos import v2configs
from gos.handlers import (
    _REMAIN_LIST_MAP,
    _as_int,
    _grant_award_pairs,
    _now,
    attach_remain_list,
)


# ---------------------------------------------------------------------------
# config helpers (no invented numbers)
# ---------------------------------------------------------------------------
def _rows(name: str) -> list[dict]:
    """All dict rows of a list-style v2 config as a fresh list."""
    table = v2configs.load(name)
    if isinstance(table, dict):
        table = [v for v in table.values() if isinstance(v, dict)]
    if isinstance(table, list):
        return [r for r in table if isinstance(r, dict)]
    return []


def _cfg_value(name: str, key: str, default=0):
    """Scalar value of a keyed (dict-style) v2 config such as
    TcDinnerRefactorConfig; returns `default` when the key is absent."""
    table = v2configs.load(name)
    if isinstance(table, dict) and key in table:
        value = table[key]
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return default
        return value
    return default


def _env_int(key: str, default: int = 0) -> int:
    cfg = v2configs.load("ConfigEnvSet")
    if isinstance(cfg, dict) and key in cfg:
        return _as_int(cfg.get(key), default)
    return default


def _exchange_rows() -> list[dict]:
    return _rows("TcDinnerPointExchangeCfg")


def _exchange_items() -> list[list[int]]:
    """Server ItemList for 12402/12408/12464/12465.

    The client only stores this list in its response callback and its
    getStoreData() indexes each entry numerically ([1] ItemId, [2]/[3]/[4]
    numeric auxiliaries).  ItemId/ConsumePoint/WeightItem are copied verbatim
    from TcDinnerPointExchangeCfg; the fixed 1 matches the client's own
    itemList[2] placeholder.  The remaining positional semantics are
    server-authoritative and are documented as unsourced in the docstring.
    """
    items: list[list[int]] = []
    for row in _exchange_rows():
        items.append([
            _as_int(row.get("ItemId")),
            _as_int(row.get("ConsumePoint")),
            1,
            _as_int(row.get("WeightItem")),
        ])
    return items


def _finish_tiers(dinner_type: int) -> list[dict]:
    return [r for r in _rows("TcDinnerRefactorFinishAward")
            if _as_int(r.get("DinnerType")) == int(dinner_type)]


def _award_pairs(award) -> list[list[int]]:
    """Normalize config award entries to [ItemId, Num] pairs.

    The feast tables use ``[ItemId, Flag, Num]`` triples (e.g. MasterAward
    ``[205, 1, 30]``); older tables use plain ``[ItemId, Num]`` pairs.
    """
    pairs: list[list[int]] = []
    for entry in award or []:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        item_id = _as_int(entry[0])
        num = _as_int(entry[2]) if len(entry) >= 3 else _as_int(entry[1])
        if item_id > 0 and num > 0:
            pairs.append([item_id, num])
    return pairs


def _resources_from_pairs(pairs: list[list[int]]) -> list[int]:
    return [int(p[0]) for p in pairs if int(p[0]) in _REMAIN_LIST_MAP]


def _max_seat(dinner_type: int) -> int:
    """Last seat index (SeatType 4) from TcDinnerRefactorSeatCfg."""
    for row in _rows("TcDinnerRefactorSeatCfg"):
        if (_as_int(row.get("DinnerType")) == int(dinner_type)
                and _as_int(row.get("SeatType")) == 4):
            return _as_int(row.get("EndIndex"))
    return 0


def _time_data() -> dict:
    now = _now()
    cd = _env_int("DinnerAuthRefreshTimeCd")
    return {"cdTime": cd, "freshTime": now, "endTime": now + cd}


def _role_base(player) -> dict:
    rb = player.data.get("RoleBase")
    return rb if isinstance(rb, dict) else {}


# ---------------------------------------------------------------------------
# lazy state (no defaults.py edit)
# ---------------------------------------------------------------------------
_STATE_KEY = "Feast"


def _state(player) -> dict:
    """All feast state is created on first use; no global seed required."""
    st = player.data.setdefault(_STATE_KEY, {})
    st.setdefault("DinnerId", 0)
    st.setdefault("DinnerType", 1)
    st.setdefault("HavingDinnerNum", 0)
    st.setdefault("OldDinnerCloseTime", 0)
    st.setdefault("DinnerPoint", 0)
    st.setdefault("GoldRefreshNum", 0)
    st.setdefault("LastAuthRefreshTime", 0)
    st.setdefault("Score", 0)
    st.setdefault("Status", 0)
    st.setdefault("IsFamous", False)
    st.setdefault("FamousTimes", _cfg_value("TcDinnerRefactorConfig",
                                           "WiseDinnerLimit", 0))
    st.setdefault("MasterAwardClaimed", False)
    return st


def _self_dinner_old(player, st: dict) -> list:
    """12400 SelfDinner: only the local player's own dinner, never others."""
    if _as_int(st.get("DinnerId")) <= 0:
        return []
    rb = _role_base(player)
    return [{
        "DinnerId": _as_int(st.get("DinnerId")),
        "Nickname": rb.get("Nickname", ""),
        "DinnerWay": _as_int(st.get("DinnerType")),
        "ShowId": rb.get("ShowId", 0),
    }]


def _self_dinner_new(player, st: dict) -> list:
    """12450 SelfDinner (refactor feast), local player only."""
    if _as_int(st.get("DinnerId")) <= 0:
        return []
    rb = _role_base(player)
    return [{
        "DinnerId": _as_int(st.get("DinnerId")),
        "Nickname": rb.get("Nickname", ""),
        "DinnerWay": _as_int(st.get("DinnerType")),
        "IsFamousFeast": bool(st.get("IsFamous")),
    }]


def _host_list(player, st: dict) -> dict:
    """12457 HostList: the host record the client itself builds on create."""
    rb = _role_base(player)
    dinner_type = _as_int(st.get("DinnerType"))
    return {
        "IsInviteVip": 0,
        "DinnerWay": dinner_type,
        "ShowId": rb.get("ShowId", 0),
        "NickName": rb.get("Nickname", ""),
        "IsFamousFeast": bool(st.get("IsFamous")),
        "IsPublic": False,
        "StartTime": _now(),
        "Score": _as_int(st.get("Score")),
        "IsOpenPush": False,
        "MaxSeat": _max_seat(dinner_type),
        "DinnerId": _as_int(st.get("DinnerId")),
    }


def _self_feast(player, st: dict) -> dict:
    """12452/12453 SelfFeast: local-only summary; other fields are empty."""
    rb = _role_base(player)
    return {
        "DinnerId": _as_int(st.get("DinnerId")),
        "DinnerWay": _as_int(st.get("DinnerType")),
        "ShowId": rb.get("ShowId", 0),
        "NickName": rb.get("Nickname", ""),
        "Score": _as_int(st.get("Score")),
        "IsFamousFeast": bool(st.get("IsFamous")),
        "MaxSeat": _max_seat(_as_int(st.get("DinnerType"))),
        "StartTime": 0,
        "DishesId": {},
        "SeatList": [],
        "LogList": [],
    }


def _famous() -> dict:
    """12450/12452 Famous: WiseKingFashion is an award list consumed by
    _itemMod.saveAward; sourced from TcDinnerRefactorConfig.WiseDinnerAward."""
    return {"WiseKingFashion": _cfg_value("TcDinnerRefactorConfig",
                                          "WiseDinnerAward", [])}


def _finish_reward(st: dict) -> list:
    """Config-derived award *preview* from the lowest finish tier.

    The actual tier depends on the server-authoritative Score; with no live
    dinner we return the lowest tier's MasterAward triples verbatim rather
    than inventing a roll or a score.
    """
    tiers = _finish_tiers(_as_int(st.get("DinnerType")))
    if not tiers:
        return []
    tiers = sorted(tiers, key=lambda r: _as_int(r.get("BeginScore")))
    award = tiers[0].get("MasterAward")
    return [list(e) for e in award if isinstance(e, (list, tuple))]


# ---------------------------------------------------------------------------
# OLD dinner system (12400-12409)
# ---------------------------------------------------------------------------
def action_12400(payload, player, session, store) -> dict:
    st = _state(player)
    return {
        "Dinners": [],
        "HavingDinnerNum": _as_int(st.get("HavingDinnerNum")),
        "OldDinnerCloseTime": _as_int(st.get("OldDinnerCloseTime")),
        "SelfDinner": _self_dinner_old(player, st),
    }


def action_12401(payload, player, session, store) -> dict:
    st = _state(player)
    dinner_type = _as_int(payload.get("DinnerType") or payload.get("dinnerType"))
    if dinner_type > 0:
        st["DinnerType"] = dinner_type
    # DinnerId is allocated by the real server; echo a supplied id if present,
    # otherwise the neutral default 0 (never invented).
    st["DinnerId"] = _as_int(payload.get("DinnerId") or payload.get("dinner_id") or 0)
    st["HavingDinnerNum"] = 1 if _as_int(st.get("DinnerId")) > 0 else 0
    return {"DinnerId": _as_int(st.get("DinnerId"))}


def _exchange_info(player) -> dict:
    st = _state(player)
    return {
        "DinnerPoint": _as_int(st.get("DinnerPoint")),
        "GoldRefreshNum": _as_int(st.get("GoldRefreshNum")),
        "ItemList": _exchange_items(),
        "LastAuthRefreshTime": _as_int(st.get("LastAuthRefreshTime")),
    }


def action_12402(payload, player, session, store) -> dict:
    return _exchange_info(player)


def action_12403(payload, player, session, store) -> dict:
    return {}


def action_12404(payload, player, session, store) -> dict:
    return {}


def action_12405(payload, player, session, store) -> dict:
    # Attendees are other players; a single-machine server never fabricates them.
    return {"DinnerList": []}


def action_12406(payload, player, session, store) -> dict:
    return {}


def action_12407(payload, player, session, store) -> dict:
    return {}


def action_12408(payload, player, session, store) -> dict:
    st = _state(player)
    st["GoldRefreshNum"] = _as_int(st.get("GoldRefreshNum")) + 1
    st["LastAuthRefreshTime"] = _now()
    return _exchange_info(player)


def _exchange(payload, player) -> dict:
    """12409/12466: consume DinnerPoint and grant one configured item."""
    st = _state(player)
    index = _as_int(payload.get("ItemIndex") or payload.get("item_index") or 0)
    rows = _exchange_rows()
    row = rows[index] if 0 <= index < len(rows) else None
    if not isinstance(row, dict):
        return attach_remain_list({"RemainScore": _as_int(st.get("DinnerPoint"))},
                                  player, resources=[])
    cost = _as_int(row.get("ConsumePoint"))
    points = _as_int(st.get("DinnerPoint"))
    if cost <= 0 or points < cost:
        return attach_remain_list({"RemainScore": points}, player, resources=[])
    st["DinnerPoint"] = points - cost
    # The exchange config has no amount column: one unit per exchange (the
    # client's credit_exchange animation credits exactly one item).
    pairs, bag_ids = _grant_award_pairs(player, [[_as_int(row.get("ItemId")), 1]])
    return attach_remain_list(
        {"RemainScore": _as_int(st.get("DinnerPoint"))},
        player, items=bag_ids, resources=_resources_from_pairs(pairs))


def action_12409(payload, player, session, store) -> dict:
    return _exchange(payload, player)


# ---------------------------------------------------------------------------
# NEW refactor feast system (12450-12467)
# ---------------------------------------------------------------------------
def action_12450(payload, player, session, store) -> dict:
    st = _state(player)
    return {
        "Dinners": [],
        "Famous": _famous(),
        "FamousTimes": _as_int(st.get("FamousTimes")),
        "FinishData": {},
        "IsFamous": bool(st.get("IsFamous")),
        "SelfDinner": _self_dinner_new(player, st),
    }


def action_12451(payload, player, session, store) -> dict:
    st = _state(player)
    dinner_type = _as_int(payload.get("DinnerType") or payload.get("dinnerType"))
    if dinner_type > 0:
        st["DinnerType"] = dinner_type
    st["IsFamous"] = _as_int(payload.get("IsWiseKing")) == 1
    st["DinnerId"] = _as_int(payload.get("DinnerId") or payload.get("dinnerId") or 0)
    st["HavingDinnerNum"] = 1 if _as_int(st.get("DinnerId")) > 0 else 0
    return {"DinnerId": _as_int(st.get("DinnerId"))}


def action_12452(payload, player, session, store) -> dict:
    st = _state(player)
    return {
        "Famous": _famous(),
        "FamousFeastList": [],
        "SelfFeast": _self_feast(player, st),
        "TimeData": _time_data(),
    }


def action_12453(payload, player, session, store) -> dict:
    return {"SelfFeast": _self_feast(player, _state(player))}


def action_12455(payload, player, session, store) -> dict:
    return {"FinishData": {}}


def action_12456(payload, player, session, store) -> dict:
    st = _state(player)
    return {
        "LogList": [],
        "Reward": _finish_reward(st),
        "Score": _as_int(st.get("Score")),
        "SeatList": [],
        "Status": _as_int(st.get("Status")),
    }


def action_12457(payload, player, session, store) -> dict:
    st = _state(player)
    return {
        "FinishData": {},
        "HostList": _host_list(player, st),
        "InviteStatus": {},
        "LogList": [],
        "SeatList": [],
        "Status": _as_int(st.get("Status")),
    }


def action_12458(payload, player, session, store) -> dict:
    return {}


def action_12459(payload, player, session, store) -> dict:
    return {}


def action_12460(payload, player, session, store) -> dict:
    # The client's action12460 dispatches protocol 12820 (friend list); the
    # 12460 response callback reads no fields, so an empty table is Lua-safe.
    return {}


def action_12461(payload, player, session, store) -> dict:
    return {"Status": _as_int(_state(player).get("Status"))}


def action_12462(payload, player, session, store) -> dict:
    """Master-award claim: grant the lowest config finish tier once."""
    st = _state(player)
    if _as_int(payload.get("IsMasterAward")) != 1 or st.get("MasterAwardClaimed"):
        return {}
    pairs, bag_ids = _grant_award_pairs(player, _award_pairs(_finish_reward(st)))
    st["MasterAwardClaimed"] = True
    return attach_remain_list({}, player, items=bag_ids,
                              resources=_resources_from_pairs(pairs))


def action_12463(payload, player, session, store) -> dict:
    return {}


def action_12464(payload, player, session, store) -> dict:
    return _exchange_info(player)


def action_12465(payload, player, session, store) -> dict:
    st = _state(player)
    st["GoldRefreshNum"] = _as_int(st.get("GoldRefreshNum")) + 1
    st["LastAuthRefreshTime"] = _now()
    return {"ItemList": _exchange_items()}


def action_12466(payload, player, session, store) -> dict:
    return _exchange(payload, player)


def action_12467(payload, player, session, store) -> dict:
    st = _state(player)
    return {
        "FinishData": {},
        "LogList": [],
        "Score": _as_int(st.get("Score")),
        "SeatList": [],
        "Status": _as_int(st.get("Status")),
    }


# Registry consumed by gos.handlers._merge_cluster_handlers().
FEAST_HANDLERS = {
    "12400": action_12400,
    "12401": action_12401,
    "12402": action_12402,
    "12403": action_12403,
    "12404": action_12404,
    "12405": action_12405,
    "12406": action_12406,
    "12407": action_12407,
    "12408": action_12408,
    "12409": action_12409,
    "12450": action_12450,
    "12451": action_12451,
    "12452": action_12452,
    "12453": action_12453,
    "12455": action_12455,
    "12456": action_12456,
    "12457": action_12457,
    "12458": action_12458,
    "12459": action_12459,
    "12460": action_12460,
    "12461": action_12461,
    "12462": action_12462,
    "12463": action_12463,
    "12464": action_12464,
    "12465": action_12465,
    "12466": action_12466,
    "12467": action_12467,
}
