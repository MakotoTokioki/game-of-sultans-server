"""Handlers for the remaining limited-time / cross-service / valentine gaps (v2.005).

Actions implemented (see ``LIMITED_HANDLERS`` at the bottom):

* 10012 -- item rename (``_itemMod.lua``); success omits the optional
  ``SensitiveWord`` field and persists the echoed nickname.
* 10104/10105 -- minister figure select / halo level response (``_ministerMod.lua``).
* 11469 -- alliance leftover alias (``_allianceMod.lua``); callback reads no fields.
* 11908 -- paid-minister award claim (``_paidMinisterMod.lua``); RemainList envelope.
* 11909 -- rank-up activity ping (``_rankUpActivityMod.lua``); no response fields.
* 11911/11912/11913/11914/11915/11917/11918 -- cross-service activity cluster
  (``_crossServiceActivityMod.lua``): rank overview, award preview, self rank,
  award claim, ticket claim, refresh ping and main overview.
* 12305/12308/12309 -- chat send / shield list / shield cancel (``_chatMod.lua``).
* 12902-12909 -- valentine cluster (``_valentineMod.lua``): wreath info, send
  flower, make bouquet, rank, credit/shop, exchange, score, rank-award claim.
* 14301 -- archive/server setting leftover (``_settingMod.lua``); no v2 caller.
* 12820/12821 (friend list / no-field action) and 17103/17104 (achievement
  leftovers) -- safe Lua-shaped stubs; live rows are server-authoritative.

FILE ISOLATION
--------------
``gos/handlers.py`` and ``gos/defaults.py`` are NOT edited.  The coordinator
merges ``LIMITED_HANDLERS`` through ``handlers._merge_cluster_handlers`` (the
entry ``("gos.handlers_limited", "LIMITED_HANDLERS")`` is already present).
All NEW state is lazy under ``player.data["LimitedExtra"]`` -- nothing is
seeded in defaults.py.  Valentine state deliberately reuses the
``ActivityModExtra`` slice of ``gos.handlers_activitymod`` so 12901 (already
implemented there) and 12902-12909 (here) read/write the same player data.

SHAPE DISCOVERY
---------------
``extract_fields.py`` gave the direct fields of each response callback; where
a callback forwarded the whole response to a scene listener, the listener was
read in the decompiled scene modules.  Field map actually returned:

* 10012: ``SensitiveWord`` intentionally absent (client guards with
  ``if A0_3.SensitiveWord then`` -- no v2 sensitive-word config exists).
  10104: ``Figure`` (int).  10105: ``HaloBreakLv``, ``HaloLv``, ``RemainList``.
* 11469: ``{}`` (callback only invokes its listener).
* 11908: ``RemainList`` (rows scanned for first entry without CounsellorId).
* 11909: ``{}`` (callback only invokes its listener).
* 11911: ``RankList`` (rows Alliance/Score/...), ``SelfPlace``,
  ``SelfAwardList``, ``ServerAwardList``, ``FirstAward``, ``FirstReward``.
* 11912: ``ReturnList`` (rows ServerPlace/ServerName/ServerScore/NickName),
  ``SelfAwardList``, ``ServerAwardList``, ``ServerRank``, ``SelfPlace``,
  ``SelfServerName``, ``SelfScore``, ``RemainList``.
* 11913: ``ReturnList``, ``ServerRank``, ``SelfPlace``, ``SelfServerName``,
  ``SelfScore`` (exactly what ``setRankData`` indexes), plus ``RemainList``.
* 11914: ``RemainList`` envelope (the scene reads no response field).
* 11915/11917: ``RemainList`` envelope / ``{}`` (no field read).
* 11918: ``SelfAwardList``, ``ServerAwardList``, ``FirstAward``,
  ``FirstReward`` (client derives FirstReward from FirstAward row [1] == 1000),
  plus the shared cross-service superset and ``RemainList``.
* 12305: ``{}``.  12308: ``ShieldList`` (list of rows with ``Rid``).
  12309: ``{}`` (shield removal persisted lazily).
* 12902: ``WreathInfo`` (FlowerMap/FlowerNum/Score/HighScoreArea/IsMaster/Grade),
  ``NextWreathInfo``, ``FlowerNum`` (pairs()-safe map), plus optional
  ``Wreath``/``Bouquet`` consumed by the shared formatter.
* 12903: ``AwardNum``, ``Likability``, ``Rank`` (+ optional Wreath/Bouquet,
  ``RemainList``).  12904: ``FlowerNum`` (+ optional Wreath/Bouquet).
* 12905: ``SingleRankList``/``AllianceRankList`` (RankList + SelfRankData with
  Ranking/Score/Name), ``ReceivedSAward``, ``ReceivedAAward``.
* 12906: ``RoleScore``, ``ItemList`` (rows Stage/ItemId/ExchangeTimes).
* 12907: ``ExchangeScore``, ``ExchangeTimes`` (+ RemainList).
* 12908: ``RoleScore``.  12909: ``RemainList`` envelope (no field read).
* 14301: ``{}`` (no v2 caller; the callback forwards to a listener only).

REWARD / CONFIG POLICY
----------------------
No number is invented.  Every returned value is either a Lua-safe default
(0 / "" / empty table/list), an echo of a request field, or lazy player state.
Bundled originals parsed with ``v2configs.load`` are used only for config-
sourced lookups: ``TcCounsellor`` (10104 Figure = ImgId), ``TcRankUpActivityCfg``
(11909 activity id validation), ``TcCrossServiceActivityCfg``,
``TcLimitedActivity``, ``TcIndianaActivityCfg`` (cross-service label/state key),
``TcValentineAchievementCfg`` (valentine state key via the reused slice).

UNSOURCED FIELDS / RESEARCH ITEMS (server-authoritative; no client data exists)
------------------------------------------------------------------------------
* Live schedules, leaderboard rows, rank numbers and award-list contents for
  the cross-service cluster are server state; empty tables/lists are served
  until a real cross-service backend supplies them (no fabricated ranks).
* FirstAward/FirstReward rows (item id 1000 special reward) need the official
  award table; an empty list is served so the client's pairs() loop is safe.
* 10105 halo upgrade cost/delta: TcCounsellorHalo / TcCounsellorHaloBreakCfg
  exist in the decompilation but the request carries no halo type or step, so
  levels are echoed from lazy state (0) instead of guessing a progression.
* 11908 paid-minister grant item: no v2 config maps CounsellorId -> award row,
  so the RemainList envelope is empty instead of inventing an item.
* chat ShieldList contents, 10012 sensitive-word dictionary, 14301 payload:
  live server-side data; default empty/absent.
"""
from __future__ import annotations

from gos import v2configs
from gos.handlers import _as_int, attach_remain_list
# Valentine shares the 12901 state slice/formatter with handlers_activitymod.
from gos.handlers_activitymod import (
    _activity_state,
    _int_map,
    _wreath_info,
)

_STATE_KEY = "LimitedExtra"

# Parseable originals bundled under gos/v2configs_data (no invented tables).
_CROSS_CONFIGS = (
    "TcCrossServiceActivityCfg",
    "TcLimitedActivity",
    "TcIndianaActivityCfg",
    "TcRankUpActivityCfg",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _state(player, name: str) -> dict:
    """Lazy per-feature slice; player.data is never seeded in defaults.py."""
    bucket = player.data.setdefault(_STATE_KEY, {})
    value = bucket.get(name)
    if not isinstance(value, dict):
        value = {}
        bucket[name] = value
    return value


def _rows(name: str) -> list[dict]:
    rows = v2configs.load(name)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, (list, tuple)):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _reward(data: dict, player, items: list | None = None,
            resources: list | None = None) -> dict:
    """Attach the reward envelope without fabricating any item grant."""
    return attach_remain_list(data, player, items=items or [],
                              resources=resources or [])


def _list(value) -> list:
    return [dict(row) for row in value if isinstance(row, dict)] \
        if isinstance(value, list) else []


def _cfg_label(payload: dict) -> str:
    """Config-derived activity label for cross-service state keying."""
    aid = _as_int(payload.get("ActivityId") or payload.get("activityId") or 0)
    atype = _as_int(payload.get("ActivityType") or payload.get("activityType") or 0)
    for name in _CROSS_CONFIGS:
        for row in _rows(name):
            row_id = _as_int(row.get("ActivityId") or row.get("Id") or 0)
            row_type = _as_int(row.get("ActivityType") or row.get("Type") or 0)
            if (aid and row_id == aid) or (atype and row_type == atype):
                return str(row.get("TitleName") or row.get("Name")
                           or row.get("ActivityName") or "")
    return ""


def _cross_state(player, payload: dict) -> dict:
    """Per-activity lazy slice, keyed by config-derived label + request echo."""
    aid = _as_int(payload.get("ActivityId") or payload.get("activityId") or 0)
    key = f"{_cfg_label(payload)}|{aid}"
    bucket = _state(player, "cross_service")
    value = bucket.get(key)
    if not isinstance(value, dict):
        value = {}
        bucket[key] = value
    return value


def _first_reward(first_award: list) -> list:
    """The client selects the FirstAward row whose first element is 1000."""
    for row in first_award:
        if isinstance(row, (list, tuple)) and len(row) >= 1 \
                and _as_int(row[0]) == 1000:
            return list(row)
    return []


def _cross_response(player, payload: dict) -> dict:
    """Superset of the shapes read by the cross-service scene listeners.

    Every value is a Lua-safe default or a persisted echo; live ranks, award
    lists and server names are server-authoritative (empty by default).
    """
    st = _cross_state(player, payload)
    rank_rows = _list(st.get("RankList"))
    return_rows = _list(st.get("ReturnList"))
    self_award = _list(st.get("SelfAwardList"))
    server_award = st.get("ServerAwardList")
    first_award = _list(st.get("FirstAward"))
    return {
        "RankList": rank_rows,
        "ReturnList": return_rows,
        "SelfAwardList": self_award,
        "ServerAwardList": {str(k): dict(v) for k, v in server_award.items()
                            if isinstance(v, dict)} if isinstance(server_award, dict) else {},
        "FirstAward": first_award,
        "FirstReward": _first_reward(first_award),
        "SelfPlace": _as_int(st.get("SelfPlace")),
        "SelfServerName": str(st.get("SelfServerName") or ""),
        "SelfScore": _as_int(st.get("SelfScore")),
        "ServerRank": _as_int(st.get("ServerRank")),
        "TicketState": _as_int(st.get("TicketState")),
        "IsRuleShown": _as_int(st.get("IsRuleShown")),
    }


def _valentine_rank_list() -> dict:
    """12905 listener indexes RankList and SelfRankData.Ranking/Score/Name."""
    return {
        "RankList": [],
        "SelfRankData": {"Ranking": 0, "Score": 0, "Name": ""},
    }


def _wreath_dict(value) -> dict:
    """Lua-safe wreath info, including the optional NextWreathInfo shape."""
    if not isinstance(value, dict):
        value = {}
    return {
        "FlowerMap": _int_map(value.get("FlowerMap")),
        "FlowerNum": _int_map(value.get("FlowerNum")),
        "Score": _as_int(value.get("Score")),
        "HighScoreArea": _int_map(value.get("HighScoreArea")),
        "IsMaster": _as_int(value.get("IsMaster")),
        "Grade": _as_int(value.get("Grade")),
        "WreathNum": _as_int(value.get("WreathNum")),
    }


def _valentine_extras(st: dict) -> dict:
    """Optional Wreath/Bouquet maps consumed by _valentineMod.L3_1 formatter."""
    wreath = st.get("Wreath") if isinstance(st.get("Wreath"), dict) else {}
    bouquet = st.get("Bouquet") if isinstance(st.get("Bouquet"), dict) else {}
    return {
        "Wreath": {"WreathNum": _as_int(wreath.get("WreathNum")),
                   "Grade": _as_int(wreath.get("Grade"))},
        "Bouquet": {"BouquetNum": _as_int(bouquet.get("BouquetNum")),
                    "FlowerId": _as_int(bouquet.get("FlowerId"))},
    }


# ---------------------------------------------------------------------------
# 10012 / 10104 / 10105 / 11469 -- small leftovers
# ---------------------------------------------------------------------------
def action_10012(payload: dict, player, session, store) -> dict:
    """Item rename: success omits SensitiveWord and persists the echoed name."""
    name = payload.get("Name") or payload.get("name")
    if isinstance(name, str) and name:
        base = player.data.setdefault("RoleBase", {})
        base["Nickname"] = name
        store.save(player)
    # No v2 sensitive-word table exists: omit the guarded SensitiveWord key
    # (sending "" would be truthy in Lua and wrongly prompt the filter dialog).
    return {}


def action_10104(payload: dict, player, session, store) -> dict:
    """Minister figure toggle: Figure is config-sourced from TcCounsellor.ImgId."""
    cid = _as_int(payload.get("CounsellorId") or payload.get("counsellorId"))
    st = _state(player, "minister")
    if cid:
        for row in _rows("TcCounsellor"):
            if _as_int(row.get("CounsellorId")) == cid:
                st["Figure"] = _as_int(row.get("ImgId"))
                break
    figure = _as_int(st.get("Figure"))
    player.data.setdefault("RoleBase", {})["Figure"] = figure
    store.save(player)
    return {"Figure": figure}


def action_10105(payload: dict, player, session, store) -> dict:
    """Halo level response; levels come from lazy state (no v2 increment data)."""
    cid = _as_int(payload.get("CounsellorId") or payload.get("counsellorId"))
    rec = _state(player, "halo").setdefault(str(cid), {})
    rec.setdefault("HaloLv", 0)
    rec.setdefault("HaloBreakLv", 0)
    store.save(player)
    return _reward({"HaloBreakLv": _as_int(rec["HaloBreakLv"]),
                    "HaloLv": _as_int(rec["HaloLv"])}, player)


def action_11469(payload: dict, player, session, store) -> dict:
    """Alliance leftover alias; callback only invokes its listener."""
    st = _state(player, "alliance_leftover")
    guid = payload.get("AllianceGuid") or payload.get("allianceGuid")
    if guid is not None:
        st["AllianceGuid"] = str(guid)
    return {}


# ---------------------------------------------------------------------------
# 11908 / 11909 -- paid minister + rank-up activity leftovers
# ---------------------------------------------------------------------------
def action_11908(payload: dict, player, session, store) -> dict:
    """Paid-minister award claim -> reward envelope (no config-sourced grant)."""
    cid = _as_int(payload.get("CounsellorId") or payload.get("counsellorId"))
    if cid:
        claimed = _state(player, "paid_minister").setdefault("claimed", {})
        claimed[str(cid)] = 1
        store.save(player)
    return _reward({}, player)


def action_11909(payload: dict, player, session, store) -> dict:
    """Rank-up activity ping; validates the id against TcRankUpActivityCfg."""
    aid = _as_int(payload.get("ActivityId") or payload.get("activityId"))
    if any(_as_int(row.get("ActivityType")) == aid
           for row in _rows("TcRankUpActivityCfg")):
        _state(player, "rank_up").setdefault("visited", {})[str(aid)] = 1
        store.save(player)
    return {}


# ---------------------------------------------------------------------------
# 11911-11918 -- cross-service activity cluster
# ---------------------------------------------------------------------------
def action_11911(payload: dict, player, session, store) -> dict:
    """Participant dialog: rank list, self place and award preview."""
    _cross_state(player, payload)  # lazy slice
    return _reward(_cross_response(player, payload), player)


def action_11912(payload: dict, player, session, store) -> dict:
    """Rank overview: ReturnList (sorted by ServerPlace) + award lists."""
    rows = _list(_cross_state(player, payload).get("ReturnList"))
    rows.sort(key=lambda r: _as_int(r.get("ServerPlace")))
    data = _cross_response(player, payload)
    data["ReturnList"] = rows
    return _reward(data, player)


def action_11913(payload: dict, player, session, store) -> dict:
    """Self rank: exactly the fields ``setRankData`` indexes."""
    data = _cross_response(player, payload)
    return _reward({k: data[k] for k in (
        "ReturnList", "ServerRank", "SelfPlace", "SelfServerName", "SelfScore")},
        player)


def action_11914(payload: dict, player, session, store) -> dict:
    """Award-type action; the scene listener reads no response field."""
    atype = _as_int(payload.get("AwardType") or payload.get("awardType"))
    _cross_state(player, payload).setdefault("award_types", {})[str(atype)] = 1
    store.save(player)
    return _reward({}, player)


def action_11915(payload: dict, player, session, store) -> dict:
    """Ticket claim; marker is persisted, reward prompt needs no field."""
    st = _cross_state(player, payload)
    st["TicketState"] = 1
    store.save(player)
    return _reward({}, player)


def action_11917(payload: dict, player, session, store) -> dict:
    """Refresh ping; callback forwards to a listener that reads no field."""
    _cross_state(player, payload)
    return {}


def action_11918(payload: dict, player, session, store) -> dict:
    """Cross-service overview: award lists + FirstAward/FirstReward."""
    return _reward(_cross_response(player, payload), player)


# ---------------------------------------------------------------------------
# 12305 / 12308 / 12309 -- chat leftovers
# ---------------------------------------------------------------------------
def action_12305(payload: dict, player, session, store) -> dict:
    """Chat send; the callback forwards to the caller's listener only."""
    _state(player, "chat")
    return {}


def action_12308(payload: dict, player, session, store) -> dict:
    """Shield list: list of rows with Rid (isBlock iterates pairs())."""
    st = _state(player, "chat")
    shields = st.get("ShieldList")
    return {"ShieldList": _list(shields)}


def action_12309(payload: dict, player, session, store) -> dict:
    """Shield cancel; removal is persisted lazily, no response field."""
    st = _state(player, "chat")
    current = _list(st.get("ShieldList"))
    targets = payload.get("CancelList")
    target_rids = set()
    if isinstance(targets, list):
        target_rids.update(str(v) for v in targets if v is not None)
    rid = payload.get("Rid") or payload.get("rid")
    if rid is not None:
        target_rids.add(str(rid))
    if payload.get("IsAll") or payload.get("isAll"):
        current = []
    elif target_rids:
        current = [row for row in current if str(row.get("Rid")) not in target_rids]
    st["ShieldList"] = current
    store.save(player)
    return {}


# ---------------------------------------------------------------------------
# 12902-12909 -- valentine (shares ActivityModExtra with 12901)
# ---------------------------------------------------------------------------
def action_12902(payload: dict, player, session, store) -> dict:
    """Wreath finish/query; wreath info, next info and flower map."""
    st = _activity_state(player, payload)
    st["FlowerNum"] = _int_map(st.get("FlowerNum"))
    st["WreathInfo"] = _wreath_info(st)
    next_info = st.get("NextWreathInfo")
    data = {
        "WreathInfo": _wreath_info(st),
        "NextWreathInfo": _wreath_dict(next_info),
        "FlowerNum": st["FlowerNum"],
        **_valentine_extras(st),
    }
    return _reward(data, player)


def action_12903(payload: dict, player, session, store) -> dict:
    """Send flower; AwardNum/Likability/Rank from the shared state slice."""
    st = _activity_state(player, payload)
    data = {
        "AwardNum": _as_int(st.get("AwardNum")),
        "Likability": _as_int(st.get("Likability")),
        "Rank": _as_int(st.get("Rank")),
        **_valentine_extras(st),
    }
    return _reward(data, player)


def action_12904(payload: dict, player, session, store) -> dict:
    """Make bouquet: return the pairs()-safe flower-count map."""
    st = _activity_state(player, payload)
    data = {
        "FlowerNum": _int_map(st.get("FlowerNum")),
        **_valentine_extras(st),
    }
    return _reward(data, player)


def action_12905(payload: dict, player, session, store) -> dict:
    """Valentine rank overview: two rank lists + received flags."""
    st = _activity_state(player, payload)
    return {
        "SingleRankList": _valentine_rank_list(),
        "AllianceRankList": _valentine_rank_list(),
        "ReceivedSAward": _as_int(st.get("ReceivedSAward")),
        "ReceivedAAward": _as_int(st.get("ReceivedAAward")),
    }


def action_12906(payload: dict, player, session, store) -> dict:
    """Credit/shop query: RoleScore + ItemList (Stage/ItemId/ExchangeTimes)."""
    st = _activity_state(player, payload)
    items = st.get("ItemList")
    return {
        "RoleScore": _as_int(st.get("RoleScore")),
        "ItemList": _list(items),
    }


def action_12907(payload: dict, player, session, store) -> dict:
    """Exchange request: Echo state; advance times by the requested count."""
    st = _activity_state(player, payload)
    count = max(0, _as_int(payload.get("ItemCount") or payload.get("itemCount") or 0))
    times = _as_int(st.get("ExchangeTimes")) + count
    st["ExchangeTimes"] = times
    store.save(player)
    return _reward({"ExchangeScore": _as_int(st.get("ExchangeScore")),
                    "ExchangeTimes": times}, player)


def action_12908(payload: dict, player, session, store) -> dict:
    """Role score query."""
    st = _activity_state(player, payload)
    return {"RoleScore": _as_int(st.get("RoleScore"))}


def action_12909(payload: dict, player, session, store) -> dict:
    """Rank award claim: callback reads no field -> reward envelope only."""
    _activity_state(player, payload)
    return _reward({}, player)


# ---------------------------------------------------------------------------
# 12820/12821 (activityMod) + 17103/17104 (achievement) -- late leftovers
# ---------------------------------------------------------------------------
def action_12820(payload: dict, player, session, store) -> dict:
    """Activity friend list: Lua-safe list (live data is server-authoritative)."""
    st = _state(player, "activity_friends")
    return {"FriendList": _list(st.get("FriendList"))}


def action_12821(payload: dict, player, session, store) -> dict:
    """Callback reads no response field."""
    _state(player, "activity_friends")
    return _reward({}, player)


def action_17103(payload: dict, player, session, store) -> dict:
    """Achievement leftover; callback reads no response field."""
    _state(player, "achievement_leftover")
    return _reward({}, player)


def action_17104(payload: dict, player, session, store) -> dict:
    """Achievement leftover; callback reads no response field."""
    _state(player, "achievement_leftover")
    return _reward({}, player)


# ---------------------------------------------------------------------------
# 14301 -- archive/server setting leftover
# ---------------------------------------------------------------------------
def action_14301(payload: dict, player, session, store) -> dict:
    """No v2.005 caller; the request callback only forwards to a listener."""
    _state(player, "setting_archive")
    return {}


LIMITED_HANDLERS = {
    "12820": action_12820,
    "12821": action_12821,
    "17103": action_17103,
    "17104": action_17104,
    "10012": action_10012,
    "10104": action_10104,
    "10105": action_10105,
    "11469": action_11469,
    "11908": action_11908,
    "11909": action_11909,
    "11911": action_11911,
    "11912": action_11912,
    "11913": action_11913,
    "11914": action_11914,
    "11915": action_11915,
    "11917": action_11917,
    "11918": action_11918,
    "12305": action_12305,
    "12308": action_12308,
    "12309": action_12309,
    "12902": action_12902,
    "12903": action_12903,
    "12904": action_12904,
    "12905": action_12905,
    "12906": action_12906,
    "12907": action_12907,
    "12908": action_12908,
    "12909": action_12909,
    "14301": action_14301,
}
