"""Handlers for the generic ``activityMod.lua`` activity cluster (v2.005).

Actions implemented (see ``ACTIVITYMOD_HANDLERS`` at the bottom):

* 12814/12815 -- fight-pirate / horse-race rank overview + rank award claim.
* 12901       -- valentine activity overview (``_valentineMod.lua``).
* 17101/17102 -- generic achievement overview + achievement award claim.
* 17201-17204 -- generic rank overview / rank award claim.
* 17301-17305 -- history-awards query and (absent from the v2 client) 17302-05.
* 17403/17404 -- king-boss shop query + exchange.
* 17501-17503 -- ramadan / valentine activity-shop query + buy + refresh.
* 17601       -- update-prize ping (``_updatePrizeMod.lua``).
* 18601/18602 -- king-boss rank overview + rank award claim.

FILE ISOLATION
--------------
``gos/handlers.py`` and ``gos/defaults.py`` are NOT edited.  The coordinator
merges ``ACTIVITYMOD_HANDLERS`` through ``handlers._merge_cluster_handlers``
(the entry ``("gos.handlers_activitymod", "ACTIVITYMOD_HANDLERS")`` is already
present there).  All state is lazy under ``player.data["ActivityModExtra"]``.

SHAPE DISCOVERY
---------------
``tools/lua_shape_harness/extract_fields.py`` reports no direct fields for most
of these actions because the ``activityMod.lua`` request callbacks only forward
the whole response to a listener.  The listener was read in the decompiled
scene modules, so the fields below are the union of what those listeners read:

* 12814: ``HintData`` (dict; ``dealHintData`` reads ``HintType``), ``RankData``
  (``GetRoleAward``/``RoleRank``/``RoleScore``), single/alliance rank lists.
* 12815/17204/18602: ``RemainList`` (reward envelope).
* 12901: ``ActivityCfg`` (optional -- intentionally omitted, see below),
  ``AwardNum``, ``FlowerNum``, ``Likability``, ``Rank``, ``WreathInfo``
  (``FlowerMap``/``Score``/``HighScoreArea``/``IsMaster``).
* 17101: ``PersonalAchievement``/``ServerAchievement`` maps (id -> Count +
  AwardsRecord) consumed by ``activityMod.resetAchievementData``.
* 17203: ``SingleRankList``, ``AllianceRankList``, ``SingleAwardStatus``.
* 17301: ``ItemList`` (list of TimeSec/ItemId/Count history rows).
* 17404: ``Score``, ``ExchangeCount``.
* 17501/17503: ``ItemList``, ``ItemNum``, ``HasBuyItem`` plus the valentine
  shop/wreath fields (``FlowerMap``, ``Score``, ``HighScoreArea``, ``Grade``,
  ``IsMaster``).  17502 reads ``ItemNum``.
* 18601: ``SingleRankList``.  17601/17102/17403/17302-05 read no fields.

REWARD / CONFIG POLICY
----------------------
No number is invented.  All gameplay amounts are Lua-safe defaults (0 / empty
tables) or echoes of request fields / lazy player state.  The only bundled
config with an ``AwardItem`` column reachable here is ``TcActivityAward``, but
none of these requests carries an award id, so no item grant is fabricated;
claim actions attach an empty ``RemainList`` through ``attach_remain_list``.
``TcValentineActivityCfg`` and ``TcUpdatePrizeCfg`` do not exist in the v2.005
decompilation, so valentine's ActivityCfg is left absent (the client's own
guarded read then uses its bundled ConfigEnvSet data) and 17601 returns ``{}``.
Configs that DO exist were copied verbatim from
``/tmp/opencode/gos/decomp_all/configs/`` into ``gos/v2configs_data/`` and are
used only for config-sourced ids/names (achievement seeds, activity label).
Live schedules, leaderboard contents, rank numbers and exchange/score deltas
are server-authoritative: they are listed as research items in the report.
"""
from __future__ import annotations

from gos import v2configs
from gos.handlers import _as_int, attach_remain_list

_STATE_KEY = "ActivityModExtra"

# Parseable originals bundled under gos/v2configs_data (no invented tables).
_ACTIVITY_CONFIGS = (
    "TcCrossServiceActivityCfg",
    "TcIndianaActivityCfg",
    "TcLimitedActivity",
    "TcRankUpActivityCfg",
)
_ACHIEVEMENT_CONFIGS = (
    "TcValentineAchievementCfg",
    "TcLampActivityAchievement",
    "TcBoardActivityAchievementCfg",
)


# ---------------------------------------------------------------------------
# config / lazy-state helpers
# ---------------------------------------------------------------------------
def _rows(name: str) -> list[dict]:
    rows = v2configs.load(name)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, (list, tuple)):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _activity_label(payload: dict) -> str:
    """Config-derived label for the requested activity (never a made-up id)."""
    atype = _as_int(payload.get("ActivityType") or payload.get("activityType") or 0)
    aid = _as_int(payload.get("ActivityId") or payload.get("activityId") or 0)
    for name in _ACTIVITY_CONFIGS:
        for row in _rows(name):
            row_type = _as_int(row.get("ActivityType") or row.get("Type") or 0)
            row_id = _as_int(row.get("ActivityId") or row.get("Id") or 0)
            if (atype and row_type == atype) or (aid and row_id == aid):
                return str(row.get("ActivityName") or row.get("TitleName")
                           or row.get("Name") or "")
    return ""


def _state(player, name: str) -> dict:
    bucket = player.data.setdefault(_STATE_KEY, {})
    value = bucket.get(name)
    if not isinstance(value, dict):
        value = {}
        bucket[name] = value
    return value


def _activity_state(player, payload: dict) -> dict:
    """Per-activity lazy slice, keyed by request echo + config-derived label."""
    atype = _as_int(payload.get("ActivityType") or payload.get("activityType") or 0)
    aid = _as_int(payload.get("ActivityId") or payload.get("activityId") or 0)
    rank = str(payload.get("RankName") or payload.get("rankName") or "")
    key = f"{_activity_label(payload)}|{atype}|{aid}|{rank}"
    return _state(player, "activities").setdefault(key, {})


def _int_map(value) -> dict:
    """Lua-safe numeric map (client pairs() over FlowerMap/FlowerNum/etc.)."""
    if not isinstance(value, dict):
        return {}
    return {str(k): _as_int(v) for k, v in value.items()}


def _rank_list() -> dict:
    """Scene listeners index RankList and SelfRankData.Ranking directly."""
    return {"RankList": [], "SelfRankData": {"Ranking": 0, "Score": 0}}


def _rank_response(payload: dict, player) -> dict:
    return {
        "SingleRankList": _rank_list(),
        "AllianceRankList": _rank_list(),
    }


def _reward(data: dict, player, items: list | None = None,
            resources: list | None = None) -> dict:
    """Attach the reward envelope without fabricating any item grant."""
    return attach_remain_list(data, player, items=items or [], resources=resources or [])


def _wreath_info(state: dict) -> dict:
    stored = state.get("WreathInfo")
    if not isinstance(stored, dict):
        stored = {}
    return {
        "FlowerMap": _int_map(stored.get("FlowerMap")),
        "FlowerNum": _int_map(stored.get("FlowerNum")),
        "Score": _as_int(stored.get("Score")),
        "HighScoreArea": _int_map(stored.get("HighScoreArea")),
        "IsMaster": _as_int(stored.get("IsMaster")),
        "Grade": _as_int(stored.get("Grade")),
    }


def _shop_state(state: dict) -> dict:
    """Lua-safe shop fields shared by 17501/17502/17503."""
    items = _int_map(state.get("Items"))
    item_list = [{"ItemId": int(k), "Count": v, "Num": v}
                 for k, v in sorted(items.items())]
    return {
        "ItemList": item_list,
        "ItemNum": _as_int(state.get("ItemNum")),
        "HasBuyItem": [],
        "RemainList": [],
        "Score": _as_int(state.get("Score")),
        "ExchangeCount": _as_int(state.get("ExchangeCount")),
        "FlowerMap": _int_map(state.get("FlowerMap")),
        "HighScoreArea": _int_map(state.get("HighScoreArea")),
        "Grade": _as_int(state.get("Grade")),
        "IsMaster": _as_int(state.get("IsMaster")),
    }


# ---------------------------------------------------------------------------
# 12814 / 12815 -- rank overview and rank award claim
# ---------------------------------------------------------------------------
def action_12814(payload: dict, player, session, store) -> dict:
    """Fight-pirate / horse-race rank overview.

    Listeners read ``HintData`` (passed to dealHintData), ``RankData`` and the
    two rank lists.  All values are live server state -> safe defaults from
    the per-activity lazy slice.
    """
    st = _activity_state(player, payload)
    rank = st.get("RankData") if isinstance(st.get("RankData"), dict) else {}
    return {
        "HintData": st.get("HintData") if isinstance(st.get("HintData"), dict) else {},
        "RankData": {
            "GetRoleAward": _as_int(rank.get("GetRoleAward")),
            "RoleRank": _as_int(rank.get("RoleRank")),
            "RoleScore": _as_int(rank.get("RoleScore")),
        },
        **_rank_response(payload, player),
    }


def action_12815(payload: dict, player, session, store) -> dict:
    """Rank award claim.  No config AwardItem is reachable -> empty envelope."""
    _activity_state(player, payload)  # lazy state slice for this activity
    return _reward({}, player)


# ---------------------------------------------------------------------------
# 12901 -- valentine (from _valentineMod.lua)
# ---------------------------------------------------------------------------
def action_12901(payload: dict, player, session, store) -> dict:
    """Valentine overview.

    The callback reads ``ActivityCfg`` only under ``if A0_3.ActivityCfg then``;
    that table requires BeautyId/FavorBoxAwards/Threshold from
    ``TcValentineActivityCfg``, which is absent from the v2.005 decompilation.
    It is therefore omitted so the client uses its bundled ConfigEnvSet values
    (no fabricated ids).  All other fields are player state / Lua-safe maps.
    """
    st = _activity_state(player, payload)
    st["FlowerNum"] = _int_map(st.get("FlowerNum"))
    wreath = st.get("WreathInfo") if isinstance(st.get("WreathInfo"), dict) else {}
    wreath["FlowerMap"] = _int_map(wreath.get("FlowerMap"))
    wreath["FlowerNum"] = _int_map(wreath.get("FlowerNum"))
    st["WreathInfo"] = wreath
    return {
        "AwardNum": _as_int(st.get("AwardNum")),
        "FlowerNum": st["FlowerNum"],
        "Likability": _as_int(st.get("Likability")),
        "Rank": _as_int(st.get("Rank")),
        "WreathInfo": _wreath_info(st),
    }


# ---------------------------------------------------------------------------
# 17101 / 17102 -- generic achievements
# ---------------------------------------------------------------------------
def action_17101(payload: dict, player, session, store) -> dict:
    """Achievement overview for ``activityMod.resetAchievementData``.

    The achievement id list comes from the client's own AchievementCfg; the
    response only supplies per-id ``Count``/``AwardsRecord`` progress.  Ids are
    seeded from the bundled achievement configs (no invented numbers) and the
    values come from the lazy player slice (0 / [] for a fresh player).
    """
    st = _activity_state(player, payload)
    out = {"PersonalAchievement": {}, "ServerAchievement": {}}
    for name in _ACHIEVEMENT_CONFIGS:
        for row in _rows(name):
            aid = _as_int(row.get("AchievementId"))
            if not aid:
                continue
            server = _as_int(row.get("IsServerAchievement",
                                     row.get("IsSrvAchievement",
                                             row.get("AchievementType", 0))))
            bucket = "ServerAchievement" if server else "PersonalAchievement"
            key = str(aid)
            if key in out[bucket]:
                continue
            stored = (st.get(bucket) or {}).get(key)
            if not isinstance(stored, dict):
                stored = {}
            records = stored.get("AwardsRecord")
            out[bucket][key] = {
                "Count": _as_int(stored.get("Count")),
                "AwardsRecord": list(records) if isinstance(records, list) else [],
            }
    return out


def action_17102(payload: dict, player, session, store) -> dict:
    """Claim an achievement stage award.  Callback reads no response field."""
    st = _activity_state(player, payload)
    ach_id = str(_as_int(payload.get("AchievementId")
                         or payload.get("achievementId") or 0))
    stage = _as_int(payload.get("StageId") or payload.get("stageId") or 0)
    if ach_id != "0":
        claimed = st.setdefault("AchievementAwards", {})
        claimed[ach_id] = max(stage, _as_int(claimed.get(ach_id)))
        store.save(player)
    return {}


# ---------------------------------------------------------------------------
# 17201-17204 -- generic rank queries / rank award claim
# ---------------------------------------------------------------------------
def _rank_award_status(payload: dict, player) -> dict:
    st = _activity_state(player, payload)
    return {
        "SingleAwardStatus": _as_int(st.get("SingleAwardStatus")),
        "AllianceAwardStatus": _as_int(st.get("AllianceAwardStatus")),
    }


def action_17201(payload: dict, player, session, store) -> dict:
    """Rank list query (rank type in the request).  Live data -> empty lists."""
    return {**_rank_response(payload, player), **_rank_award_status(payload, player)}


def action_17202(payload: dict, player, session, store) -> dict:
    """Rank list query variant.  Live data -> empty lists."""
    return {**_rank_response(payload, player), **_rank_award_status(payload, player)}


def action_17203(payload: dict, player, session, store) -> dict:
    """Rank overview: exploreMysteryScene reads both lists + SingleAwardStatus."""
    return {**_rank_response(payload, player), **_rank_award_status(payload, player)}


def action_17204(payload: dict, player, session, store) -> dict:
    """Rank award claim -> reward envelope (no config AwardItem reachable)."""
    _activity_state(player, payload)
    return _reward({}, player)


# ---------------------------------------------------------------------------
# 17301-17305 -- history awards
# ---------------------------------------------------------------------------
def action_17301(payload: dict, player, session, store) -> dict:
    """History-award query.  Response is passed to the formatter that reads
    ``ItemList`` rows with TimeSec/ItemId/Count.  Timestamps are live state.
    """
    st = _activity_state(player, payload)
    history = st.get("AwardHistory")
    return {"ItemList": [dict(row) for row in history]
            if isinstance(history, list) else []}


def action_17302(payload: dict, player, session, store) -> dict:
    """No v2.005 client module/request callback exists for 17302."""
    _activity_state(player, payload)
    return _reward({}, player)


def action_17303(payload: dict, player, session, store) -> dict:
    """No v2.005 client module/request callback exists for 17303."""
    _activity_state(player, payload)
    return _reward({}, player)


def action_17304(payload: dict, player, session, store) -> dict:
    """No v2.005 client module/request callback exists for 17304."""
    _activity_state(player, payload)
    return _reward({}, player)


def action_17305(payload: dict, player, session, store) -> dict:
    """No v2.005 client module/request callback exists for 17305."""
    _activity_state(player, payload)
    return _reward({}, player)


# ---------------------------------------------------------------------------
# 17403 / 17404 -- king-boss exchange shop
# ---------------------------------------------------------------------------
def action_17403(payload: dict, player, session, store) -> dict:
    """Shop query.  ``extract_fields`` reports no direct fields: the callback
    sets the listener on the request context and passes that context to
    ``drawShop``.  Return the Lua-safe shop map for the other 17xxx callers."""
    return _shop_state(_activity_state(player, payload))


def action_17404(payload: dict, player, session, store) -> dict:
    """Exchange request.  The listener reads ``Score`` and ``ExchangeCount``.
    Both are server-authoritative; the count advances by the requested amount
    (request echo, no config value invented).
    """
    st = _activity_state(player, payload)
    count = max(0, _as_int(payload.get("itemCount")
                           or payload.get("ItemCount") or 0))
    total = _as_int(st.get("ExchangeCount")) + count
    st["ExchangeCount"] = total
    store.save(player)
    return _reward({"Score": _as_int(st.get("Score")),
                    "ExchangeCount": total}, player)


# ---------------------------------------------------------------------------
# 17501-17503 -- activity shop (ramadan / valentine)
# ---------------------------------------------------------------------------
def action_17501(payload: dict, player, session, store) -> dict:
    """Shop overview.  Ramadan listeners read ItemList/ItemNum; valentine's
    setShopData reads HasBuyItem/FlowerMap/Score/HighScoreArea/Grade/IsMaster.
    All come from the lazy slice with Lua-safe defaults.
    """
    st = _activity_state(player, payload)
    return _reward(_shop_state(st), player)


def action_17502(payload: dict, player, session, store) -> dict:
    """Buy items.  Valentine's listener reads ``ItemNum`` (remaining stock)."""
    st = _activity_state(player, payload)
    item_id = _as_int(payload.get("ItemId") or payload.get("itemId") or 0)
    count = _as_int(payload.get("ItemCount") or payload.get("itemCount") or 1)
    items = _int_map(st.get("Items"))
    key = str(item_id)
    if item_id:
        remaining = max(0, _as_int(items.get(key)) - count)
        items[key] = remaining
        st["Items"] = items
        st["ItemNum"] = remaining
        store.save(player)
    else:
        remaining = _as_int(st.get("ItemNum"))
    return _reward({"ItemNum": remaining}, player,
                   items=[item_id] if item_id else [], resources=[])


def action_17503(payload: dict, player, session, store) -> dict:
    """Shop refresh after a buy -- same shapes as 17501."""
    st = _activity_state(player, payload)
    return _reward(_shop_state(st), player)


# ---------------------------------------------------------------------------
# 17601 -- update prizes (from _updatePrizeMod.lua)
# ---------------------------------------------------------------------------
def action_17601(payload: dict, player, session, store) -> dict:
    """The callback only invokes a stored callback with no arguments; it reads
    no response field.  ``TcUpdatePrizeCfg`` is absent from the decompilation,
    so there is no config-sourced data to return."""
    return {}


# ---------------------------------------------------------------------------
# 18601 / 18602 -- king-boss ranks
# ---------------------------------------------------------------------------
def action_18601(payload: dict, player, session, store) -> dict:
    """King-boss rank overview; kingBossSceneLogic reads SingleRankList."""
    return _rank_response(payload, player)


def action_18602(payload: dict, player, session, store) -> dict:
    """King-boss rank award claim; registered as an ActionListener only.
    No response field is read before the reward prompt -> empty envelope."""
    _activity_state(player, payload)
    return _reward({}, player)


ACTIVITYMOD_HANDLERS = {
    "12814": action_12814,
    "12815": action_12815,
    "12901": action_12901,
    "17101": action_17101,
    "17102": action_17102,
    "17201": action_17201,
    "17202": action_17202,
    "17203": action_17203,
    "17204": action_17204,
    "17301": action_17301,
    "17302": action_17302,
    "17303": action_17303,
    "17304": action_17304,
    "17305": action_17305,
    "17403": action_17403,
    "17404": action_17404,
    "17501": action_17501,
    "17502": action_17502,
    "17503": action_17503,
    "17601": action_17601,
    "18601": action_18601,
    "18602": action_18602,
}
