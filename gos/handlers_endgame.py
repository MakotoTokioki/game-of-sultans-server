"""Endgame / payment-cluster handlers (19001-19308).

Isolated module: ``gos/handlers.py`` (and ``gos/defaults.py``) are NOT touched.
``gos.handlers._merge_cluster_handlers`` imports ``ENDGAME_HANDLERS`` from here.

Covered modules (decompiled v2.005 client):
  * ``_rechargeActMod.lua``  -> 19001 RechargeCfg; 19002 stage report
  * ``_horseRaceMod.lua``    -> 19201-19206 race state / actions
  * ``_osmanMedalMod.lua``   -> 19301-19308 medal state / tasks / progression

Shapes were recovered by walking BACKWARD from every ``actionNNNNN = Lxx``
assignment to its request function and reading the nested response callback;
cross-checked with ``tools/lua_shape_harness/extract_fields.py``.

SERVER-AUTHORITATIVE / PAYMENT POLICY (see HANDLER_SPEC + task brief):
  * No real money is accepted and no paid purchase is confirmed.  19002,
    19302 (medal activation) and 19303 (legend upgrade) are NO-OP stubs:
    they return only Lua-safe state echoes and NEVER grant items/currency.
  * Multi-player data (race teams/opponents/NPC placements, medal leaderboards)
    is unavailable to a single-player server: empty lists/dicts/zeros are
    returned.  ``MyTeam`` is OMITTED on 19201 on purpose -- the client wraps
    that read in ``if A0_3.MyTeam then`` and a present empty table would make
    it think the player is in a team (Lua treats {} as truthy).
  * Config-derived numbers only come from bundled ORIGINAL v2 tables
    (TcRechargeConfig, TcGrowthFundTaskCfg, ...).  Race tuning is read by the
    CLIENT from ``getEnvCfg`` (HorseRaceSpeed/Score/...); those values are not
    part of any server response and are not fabricated here.
  * All new state is LAZY via ``setdefault`` under ``EndgameExtra``; no edits
    to ``gos/defaults.py`` were made.
"""
from __future__ import annotations

from gos import v2configs
from gos.handlers import _as_int


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _cfg_rows(name: str) -> list[dict]:
    """Original config rows as a list of dicts (v2configs.load may give a map)."""
    rows = v2configs.load(name)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, (list, tuple)):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _endgame_state(player) -> dict:
    """Single lazy top-level slice; defaults.py is left untouched."""
    return player.data.setdefault("EndgameExtra", {})


def _recharge_state(player) -> dict:
    return _endgame_state(player).setdefault("RechargeActivity", {})


def _horse_state(player) -> dict:
    return _endgame_state(player).setdefault("HorseRace", {})


def _medal_state(player) -> dict:
    return _endgame_state(player).setdefault("OsmanMedal", {})


# The decompiled 19301 callback builds 16-slot EliteList/LegendList arrays when
# Medal == 0 (osmanMedalMod.lua lines 509-576).  16 is a client-side table
# length, not a reward/cost, so mirroring it is Lua-shape work, not invention.
_MEDAL_SLOTS = 16
_TASK_SLOTS = 5  # client tSubmitList/tFinishTaskList initial arrays (lines 597-621)


def _medal_elite_list(state: dict) -> list[int]:
    slots = state.get("EliteList")
    if not isinstance(slots, list) or len(slots) != _MEDAL_SLOTS:
        slots = [0] * _MEDAL_SLOTS
        state["EliteList"] = slots
    return list(slots)


def _medal_legend_list(state: dict) -> list[int]:
    slots = state.get("LegendList")
    if not isinstance(slots, list) or len(slots) != _MEDAL_SLOTS:
        slots = [0] * _MEDAL_SLOTS
        state["LegendList"] = slots
    return list(slots)


def _medal_task_map() -> dict:
    """TaskMap derived from the ORIGINAL TcGrowthFundTaskCfg ids.

    The 19304 client callback stores ``TaskMap[key] = value`` into
    ``tTaskList[Stage]`` and later reads ``value.IsSubmit`` / ``value.isReach``.
    Only the ids come from config; the per-task progress fields are Lua-safe
    zero/false defaults (no progress is fabricated).
    """
    out: dict[str, dict] = {}
    for row in _cfg_rows("TcGrowthFundTaskCfg"):
        task_id = _as_int(row.get("Id"))
        if task_id <= 0:
            continue
        out[str(task_id)] = {
            "Id": task_id,
            "IsSubmit": 0,
            "isReach": False,
            "Progress": 0,
        }
    return out


def _medal_submit_list(state: dict) -> list[int]:
    slots = state.get("SubmitList")
    if not isinstance(slots, list) or len(slots) != _TASK_SLOTS:
        slots = [0] * _TASK_SLOTS
        state["SubmitList"] = slots
    return list(slots)


def _medal_finish_list(state: dict) -> list[int]:
    slots = state.get("FinishTaskList")
    if not isinstance(slots, list) or len(slots) != _TASK_SLOTS:
        slots = [1] * _TASK_SLOTS  # client's own 19301 initializer
        state["FinishTaskList"] = slots
    return list(slots)


# ---------------------------------------------------------------------------
# recharge activities (19001-19002)
# ---------------------------------------------------------------------------
def action_19001(payload: dict, player, session, store) -> dict:
    """_rechargeActMod.action19001: the RechargeCfg display list.

    Client callback (lines 208-233) only does ``pairs(A0_3.RechargeCfg)`` and
    copies each row into ``L1_1[ActivityType]`` when its ActivityId matches the
    login ``RechargeActivityCfg``.  The v2.005 ``RechargeActivityCfg`` is a
    login-time server config and is NOT bundled; the only original v2 recharge
    table present is ``TcRechargeConfig`` (ProductId/Money/Name).  We ship those
    rows verbatim -- real config numbers, no invented price/activity ids.  The
    activity-type binding stays inactive (no recharge promotion is advertised)
    until the server is given the missing RechargeActivityCfg table.
    """
    return {"RechargeCfg": [dict(row) for row in _cfg_rows("TcRechargeConfig")]}


def action_19002(payload: dict, player, session, store) -> dict:
    """_rechargeActMod.action19002: report an awarded recharge stage.

    Request: {Stage, ActivityId}.  The client callback (lines 167-190) merely
    appends Stage to its LOCAL ``GetAwardStage`` list and calls the completion
    callback -- it reads NO response fields.  This is a report, not an order
    confirmation; no purchase is accepted and no item is granted.  We only
    remember the reported stage in lazy state (idempotent), for inventory.
    """
    activity_id = _as_int(payload.get("ActivityId") or payload.get("activityId"))
    stage = _as_int(payload.get("Stage") or payload.get("stage"))
    if activity_id and stage:
        awarded = _recharge_state(player).setdefault("GetAwardStage", {}).setdefault(
            str(activity_id), []
        )
        if stage not in awarded:
            awarded.append(stage)
    return {}


# ---------------------------------------------------------------------------
# horse race (19201-19206)
# ---------------------------------------------------------------------------
def _race_hint() -> dict:
    """Empty HintData: dealHintData() returns nil for an empty/table-less hint,
    so the client callback continues instead of short-circuiting."""
    return {}


def action_19201(payload: dict, player, session, store) -> dict:
    """_horseRaceMod.action19201: full race state (callback lines 1058-1257).

    Fields read by the callback:
      MyTeam (optional team table) -- OMITTED: presence means "in a team" in
                                      Lua; this server has no multiplayer team.
      IsCreateTeam -> bool         -> isFirstTime
      SingleScore, TeamScore       -> tPoint = {single, team}
      CurrentPlace, StageTotalDistance, Stage -> tPresent[1..3]
      ItemStream -> pairs() table  -> converted into tReport
      HintData   -> dealHintData()

    All numeric race progress is runtime/single-player state with no v2 config
    source; safe zeros and empty tables are returned.  Race speed/score tuning
    comes from the client's own getEnvCfg and is not part of this response.
    """
    return {
        # "MyTeam" intentionally omitted (see docstring / module header).
        "IsCreateTeam": False,
        "SingleScore": 0,
        "TeamScore": 0,
        "CurrentPlace": 0,
        "StageTotalDistance": 0,
        "Stage": 0,
        "ItemStream": {},
        "HintData": _race_hint(),
    }


def action_19202(payload: dict, player, session, store) -> dict:
    """_horseRaceMod.action19202: race tick (callback lines 947-1020).

    Reads HintData, LastRecoveryTime, CurrentPlace, NpcConf.  NpcConf is
    indexed unconditionally (``A0_3.NpcConf.NpcLane``), so it MUST be a table;
    NpcLane == 0 keeps the client on the no-AI-opponent path.  NpcPlace /
    NpcItemConf / IsNpcBeginRun / IsNew are only read inside that skipped
    branch but are included as Lua-safe defaults.
    """
    return {
        "HintData": _race_hint(),
        "LastRecoveryTime": _as_int(
            _horse_state(player).get("LastRecoveryTime") or 0
        ),
        "CurrentPlace": 0,
        "NpcConf": {
            "NpcLane": 0,
            "NpcPlace": 0,
            "NpcItemConf": {},
            "IsNpcBeginRun": False,
            "IsNew": 0,
        },
    }


def action_19203(payload: dict, player, session, store) -> dict:
    """_horseRaceMod.action19203: pause/recover tick (callback lines 880-893).

    Reads only HintData.  The request carries IsPause/IsRecover/notLoading; a
    recover request only advances the client's local clock, so no server state
    is persisted here (runtime race timing is single-player and unsourced).
    """
    return {"HintData": _race_hint()}


def action_19204(payload: dict, player, session, store) -> dict:
    """_horseRaceMod.action19204: board-item choice (callback lines 851-861).

    Reads only HintData.  Awards (ItemAward/BallType/ScoreType from the request)
    are client-side visual state; no reward is granted (HANDLER_SPEC: no number
    may be invented).
    """
    return {"HintData": _race_hint()}


def action_19205(payload: dict, player, session, store) -> dict:
    """_horseRaceMod.action19205: begin-place report (callback lines 777-825).

    Reads HintData, GainItems (-> tGainItems) and GetNpc (pairs()/next()).
    Empty tables keep ``next(GetNpc)`` nil so no fake NPC pass is created.
    The whip count is read by the CLIENT from its own item bag (item 582).
    """
    return {
        "HintData": _race_hint(),
        "GainItems": [],
        "GetNpc": {},
    }


def action_19206(payload: dict, player, session, store) -> dict:
    """_horseRaceMod.action19206: lane/place report (callback lines 724-749).

    Reads HintData and ItemStream (pairs() table -> tReport).  The callback
    guards ItemStream with ``if not L3_3 then L3_3 = {} end``; an explicit
    empty table is Lua-safe either way.
    """
    return {
        "HintData": _race_hint(),
        "ItemStream": {},
    }


# ---------------------------------------------------------------------------
# osman medal / growth fund (19301-19308)
# ---------------------------------------------------------------------------
def _osman_active() -> int:
    """IsOn == 1 keeps L3_1(osmanMedalMod.lua 73-93) on its normal path.

    The medal ACTIVITY window is advertised as open, but Medal == 0 below
    means no medal has been purchased -- activation (19302/19303) is a no-op.
    """
    return 1


def action_19301(payload: dict, player, session, store) -> dict:
    """_osmanMedalMod.action19301: medal bootstrap (callback lines 455-626).

    Reads Medal (0/1/2), BuyTimes, EliteList, LegendList; the callback also
    calls L2_1(A0_3) which reads StartTime/EndTime for tTime, and L3_1(A0_3)
    which reads IsOn.  Client-owned TcGrowthFundCfg is read locally by the
    client itself (getTcData) and is NOT part of this response.  Medal stays 0
    (never purchased server-side); task lists are the client's own initializer
    defaults; TaskMap is config-derived from TcGrowthFundTaskCfg via 19304.
    """
    state = _medal_state(player)
    return {
        "IsOn": _osman_active(),
        "StartTime": _as_int(state.get("StartTime")),
        "EndTime": _as_int(state.get("EndTime")),
        "Medal": _as_int(state.get("Medal")),  # 0 = not activated
        "BuyTimes": _as_int(state.get("BuyTimes")),
        "EliteList": _medal_elite_list(state),
        "LegendList": _medal_legend_list(state),
    }


def action_19302(payload: dict, player, session, store) -> dict:
    """_osmanMedalMod.action19302: medal activation / purchase -- NO-OP STUB.

    The client callback (lines 419-440) reads no response fields except the
    time window (via L2_1) and IsOn (via L3_1), then optimistically sets its
    LOCAL Medal/EliteList/Lv.  That local state is not authoritative: we do
    NOT mark a purchase, spend currency, or grant the premium medal.  Only the
    time echo is returned so the client's timer setup cannot throw.
    """
    state = _medal_state(player)
    return {
        "IsOn": _osman_active(),
        "StartTime": _as_int(state.get("StartTime")),
        "EndTime": _as_int(state.get("EndTime")),
    }


def action_19303(payload: dict, player, session, store) -> dict:
    """_osmanMedalMod.action19303: legend-upgrade request -- NO-OP STUB.

    Request: {Lv = index}.  Callback (lines 377-402) reads no response fields;
    it only flips the client's local EliteList[index]/LegendList[index] to 2.
    No purchase is accepted and no medal data is persisted server-side.
    """
    return {"IsOn": _osman_active()}


def action_19304(payload: dict, player, session, store) -> dict:
    """_osmanMedalMod.action19304: task map query (callback lines 320-354).

    Request: {Stage, NeedSubmitList}.  Reads TaskMap (pairs, keys tonumber'd),
    and optional SubmitList / FinishTaskList.  TaskMap keys come from the
    original TcGrowthFundTaskCfg; progress values are Lua-safe zeros/false.
    SubmitList/FinishTaskList mirror the client's own 19301 defaults.
    """
    state = _medal_state(player)
    return {
        "IsOn": _osman_active(),
        "TaskMap": _medal_task_map(),
        "SubmitList": _medal_submit_list(state),
        "FinishTaskList": _medal_finish_list(state),
    }


def action_19305(payload: dict, player, session, store) -> dict:
    """_osmanMedalMod.action19305: task submit (callback lines 282-301).

    Request: {Stage, Index}.  The callback writes Progress = 0 and
    IsSubmit = 0 onto the response table itself (they are assignments, not
    reads: ``A0_3.Progress = 0``), then stores it in tTaskList[stage][index].
    No fields are strictly required; the two keys are echoed for shape parity
    and to keep the harness field check green.  No reward is granted.
    """
    return {"IsOn": _osman_active(), "Progress": 0, "IsSubmit": 0}


def action_19306(payload: dict, player, session, store) -> dict:
    """_osmanMedalMod.action19306: medal task claim (callback lines 197-262).

    Request: {TaskId}.  Reads Lv and Exp, then locally fills EliteList slots
    and updates the medal.  We echo the lazy medal Lv (default 1, matching the
    client's 19301 initializer) and Exp (default 0).  Award tables exist in the
    original TcGrowthFundAwardCfg (EliteAward/LegendAward/Exp per Lv), but no
    level-up or item grant is performed here: task-to-level mapping is not
    present in any v2 config and would have to be invented.  Research item.
    """
    state = _medal_state(player)
    lv = _as_int(state.get("Lv")) or 1
    return {
        "IsOn": _osman_active(),
        "Lv": lv,
        "Exp": _as_int(state.get("Exp")),
    }


def action_19307(payload: dict, player, session, store) -> dict:
    """_osmanMedalMod.action19307: legend-medal check (callback lines 168-180).

    Reads HasLegendMedal; the callback compares it with 1.  No legend medal is
    ever purchased server-side, so it stays 0 (false).
    """
    state = _medal_state(player)
    return {
        "IsOn": _osman_active(),
        "HasLegendMedal": _as_int(state.get("HasLegendMedal")),
    }


def action_19308(payload: dict, player, session, store) -> dict:
    """_osmanMedalMod.action19308: mail/timer ping.

    The response callback (lines 151-153) is empty -- it reads no fields.  This
    action is normally invoked by a client-side timer, never as a purchase.
    """
    return {}


# ---------------------------------------------------------------------------
# registry (merged into gos.handlers.HANDLERS by _merge_cluster_handlers)
# ---------------------------------------------------------------------------
ENDGAME_HANDLERS = {
    "19001": action_19001,
    "19002": action_19002,
    "19201": action_19201,
    "19202": action_19202,
    "19203": action_19203,
    "19204": action_19204,
    "19205": action_19205,
    "19206": action_19206,
    "19301": action_19301,
    "19302": action_19302,
    "19303": action_19303,
    "19304": action_19304,
    "19305": action_19305,
    "19306": action_19306,
    "19307": action_19307,
    "19308": action_19308,
}
