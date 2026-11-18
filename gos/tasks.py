"""Main-task (quest) progress model.

The client's _mainTaskMod keeps `taskId` / `taskNum` in module state and
drives everything from the *local* TcMainTask config; the server only stores
and re-serves the two counters.

Extracted from the client config (configs/settings.TcMainTask.lua):
  MainTaskId 0..162  (MainTaskId n = quest chain step n)
  MainTaskType       the quest kind the *type dialog* branches on (types 1/2/
                     4/5/40/9/... have custom text; the rest are generic)
  SpecNum            per-type reference value (e.g. CounsellorId, ItemId,
                     SpecNum 0 for pure counters)
  RequireNum         completion threshold for the counter
  Redirect           MainTaskId the client jumps to from the task dialog
                     (mainTaskDialog "Go" button)
Type 1 ("ZHENG_ZHAN_LING" chapter quest) is SpecialNum==0 tasks handled
locally by the client (it flips taskId itself); we never auto-advance those.
"""
from __future__ import annotations

from typing import Any

MAINTASK_TOTAL = 162  # highest MainTaskId in the client's TcMainTask config


def _task_num(player: dict) -> dict:
    num = player.get("mainTaskProgress")
    if not isinstance(num, dict):
        num = {}
        player["mainTaskProgress"] = num
    return num


def get_task_id(player: dict) -> int:
    prog = _task_num(player)
    try:
        return int(prog.get("taskId") or 0)
    except (TypeError, ValueError):
        return 0


def get_task_num(player: dict) -> int:
    prog = _task_num(player)
    try:
        return int(prog.get("taskNum") or 0)
    except (TypeError, ValueError):
        return 0


def set_task(player: dict, task_id: int, task_num: int) -> None:
    prog = _task_num(player)
    prog["taskId"] = int(task_id)
    prog["taskNum"] = max(0, int(task_num))
    _sync_login_fields(player)


def bump_task_num(player: dict, delta: int) -> None:
    prog = _task_num(player)
    value = max(0, get_task_num(player) + int(delta))
    limit = _require_num(get_task_id(player))
    if limit is not None and limit > 0:
        value = min(value, limit)
    prog["taskNum"] = value
    _sync_login_fields(player)


def _sync_login_fields(player: dict) -> None:
    """Keep the login `MainTask` slice in step with mainTaskProgress.

    _mainTaskMod.init reads only tbData.MainTask.TaskId/TaskNum on login and
    ignores mainTaskProgress, so a stale login slice makes the client restart
    the quest chain from an old task.  Every counter mutation updates both.
    """
    player["MainTask"] = {
        "TaskId": get_task_id(player),
        "TaskNum": get_task_num(player),
    }


def sync_login_fields(player: dict) -> None:
    """Public entry point used by the login handler."""
    _sync_login_fields(player)


# RequireNum and MainTaskType per MainTaskId 0..162, verbatim from the
# client config configs/settings.TcMainTask.lua.  These tables are indexed
# by MainTaskId, so entry 0 is TcMainTask[1] (MainTaskId = 0).
MAINTASK_REQUIRE_NUM = [
    1, 3, 3, 3, 5, 3, 16, 6, 6, 6, 1, 1, 32, 3, 49, 2000,
    2, 3, 1, 1, 2, 12, 12, 12, 6, 5, 5000, 82, 3, 1, 7, 123,
    20, 20, 20, 1, 147, 3, 1, 20000, 3, 1, 3, 6, 5, 164, 40, 40,
    40, 4, 5, 30000, 205, 9, 6, 7, 1, 12, 3, 12, 246, 1, 40000, 2,
    80, 80, 80, 287, 4, 2, 3, 5, 6, 60000, 24, 7, 1, 3, 5, 7,
    328, 6, 10, 110, 110, 110, 80000, 1, 369, 4, 40, 10, 40, 1, 410, 2,
    20, 100000, 4, 451, 5, 1, 6, 1, 2, 492, 140, 140, 140, 20, 120000, 3,
    3, 533, 2, 4, 6, 9, 5, 8, 60, 80, 574, 4, 20, 140000, 30, 1,
    615, 3, 3, 2, 170, 170, 170, 6, 1, 656, 2, 3, 160000, 697, 1, 1,
    4, 5, 6, 738, 200, 200, 200, 779, 40, 20, 1, 820, 6, 2, 4, 1,
    180000, 12, 1,
]

MAINTASK_TYPE = [
    35, 12, 11, 13, 3, 8, 9, 12, 11, 13, 3, 5, 9, 3, 9, 25,
    1, 28, 7, 31, 30, 12, 11, 13, 8, 3, 25, 9, 21, 14, 3, 9,
    12, 11, 13, 3, 9, 1, 34, 25, 33, 5, 3, 5, 5, 9, 12, 11,
    13, 3, 3, 25, 9, 8, 3, 3, 27, 28, 23, 33, 9, 3, 25, 14,
    12, 11, 13, 9, 1, 31, 3, 3, 23, 25, 33, 3, 3, 3, 3, 3,
    9, 21, 5, 12, 11, 13, 25, 3, 9, 27, 28, 23, 33, 29, 9, 3,
    8, 25, 3, 9, 1, 50, 3, 3, 29, 9, 12, 11, 13, 30, 25, 29,
    14, 9, 3, 3, 3, 21, 34, 27, 28, 33, 9, 29, 5, 25, 30, 3,
    9, 32, 31, 26, 12, 11, 13, 29, 5, 9, 3, 3, 25, 9, 5, 22,
    3, 3, 3, 9, 12, 11, 13, 9, 20, 5, 19, 9, 1, 50, 14, 3,
    25, 21, 55,
]


def _require_num(task_id: int):
    if 0 <= task_id < len(MAINTASK_REQUIRE_NUM):
        return MAINTASK_REQUIRE_NUM[task_id]
    return None


def task_type(task_id: int) -> int:
    """TcMainTask[task_id].MainTaskType, or 0 when unknown."""
    if 0 <= task_id < len(MAINTASK_TYPE):
        return MAINTASK_TYPE[task_id]
    return 0


def advance_task(player: dict) -> None:
    """One task finished: jump to the next MainTaskId, reset the counter."""
    task_id = get_task_id(player)
    if 0 <= task_id < MAINTASK_TOTAL:
        set_task(player, task_id + 1, 0)
    else:
        # past the chain end: the client treats an unknown taskId as FINISHED
        # (getTaskStatus returns "FINISH" when TcMainTask[taskId] is nil), so
        # pin the counter and keep the id stable.
        set_task(player, task_id, 0)
    # Levy quests are cumulative: the next threshold may already be met by
    # levies done while earlier quests were current.
    _refresh_levy_task_num(player)


def sync_task(player: dict) -> None:
    """Advance the quest when the persisted counter meets the current task's
    TcMainTask.RequireNum.  Never advances early: MainTaskId only moves once
    the threshold from the client config is reached."""
    if get_task_num(player) >= (_require_num(get_task_id(player)) or 0):
        advance_task(player)


def claim(player: dict) -> bool:
    """The 10015 request IS the claim: the client only sends it from the task
    dialog claim buttons, both guarded by getTaskStatus()=="CLAIM" (i.e.
    taskNum >= TcMainTask[taskId].RequireNum).  Complete the current task and
    move to the next MainTaskId.  Returns True when a task was completed."""
    completed = get_task_num(player) >= (_require_num(get_task_id(player)) or 0)
    if completed:
        advance_task(player)
    return completed


def snapshot(player: dict) -> dict:
    return {"taskId": get_task_id(player), "taskNum": get_task_num(player)}


def task_response(player: dict) -> dict:
    """Body of the 10015 callback: A0_3.NextTaskId / A0_3.NextNum."""
    return {"NextTaskId": get_task_id(player), "NextNum": get_task_num(player)}


# --------------------------------------------------------------------------
# progress sources: map gameplay actions to task-counter deltas / advances.
# Values come from observed client payloads; nothing is invented server-side.
# --------------------------------------------------------------------------
def apply_stage_push(player: dict, payload: dict) -> None:
    """action10209 {ChapterId, SectionId, PhaseId}: a section phase was
    pushed.  Type-9 tasks are per-section/phase counts (SpecNum picks the
    chapter); reaching a new phase means the previous one was cleared."""
    try:
        chapter = int(payload.get("ChapterId") or 0)
        section = int(payload.get("SectionId") or 0)
        phase = int(payload.get("PhaseId") or 0)
    except (TypeError, ValueError):
        return
    if not (chapter and section and phase):
        return
    section_state = player.setdefault("RoleSection", {})
    try:
        old_section = int(section_state.get("SectionId") or 0)
        old_phase = int(section_state.get("PhaseId") or 0)
    except (TypeError, ValueError):
        old_section, old_phase = 0, 0
    # Phases advance 1..8 inside a section; a push across sections keeps
    # the same arithmetic (8 phases per section in TcPhaseCfg).
    old_index = max(0, (old_section - 1) * 8 + old_phase)
    new_index = max(0, (section - 1) * 8 + phase)
    delta = new_index - old_index
    if delta > 0 and task_type(get_task_id(player)) == 9:
        bump_task_num(player, delta)  # section phases cleared


def apply_stage_clear(player: dict, chapter: int, section: int, phase: int) -> bool:
    """action10201 (a phase battle won): type-9 tasks count cleared phases."""
    if task_type(get_task_id(player)) != 9:
        return False
    bump_task_num(player, 1)
    return True


def apply_status(player: dict, payload: dict) -> None:
    """action10018 {StatusStr}: tutorial/scene status transitions.

    TcMainTask.MainTaskType decides which source owns the counter:
      * type 9  -> cleared section phases (10201/10209 only)
      * type 13 -> completed guides; a guide name change counts once, while
                   the numbered sub-steps inside one guide do not
      * others  -> legacy rising-step counter (the early tutorial tasks)
    Without this gate a guide step such as "upLv-0" used to bump a type-9
    stage task, which is what left mainTaskProgress at 6/14.
    """
    status = str(payload.get("StatusStr") or "")
    if not status:
        return
    name, _, tail = status.rpartition("-")
    if not name:
        name, tail = status, ""
    try:
        step = int(tail)
    except ValueError:
        step = 0
    last_status = str((player.get("RoleData") or {}).get("LastStatus") or "")
    last_name, _, last_tail = last_status.rpartition("-")
    if not last_name:
        last_name, last_tail = last_status, ""
    try:
        last_step = int(last_tail)
    except ValueError:
        last_step = -1

    current_type = task_type(get_task_id(player))
    if current_type == 9:
        return
    if current_type == 13:
        if name and name != last_name:
            bump_task_num(player, 1)
        return
    if step > last_step:
        bump_task_num(player, 1)


def apply_stage_info(player: dict) -> None:
    """action10202/10208 (stage read).  Counters are not advanced here: the
    client shows CLAIM when taskNum reaches RequireNum and the 10015 claim
    request is what advances the chain."""
    return


# TcMainTaskTypeName: 11 = 征粮 (grain), 12 = 贸易 (trade/silver),
# 13 = 募兵 (soldier).  The rising RequireNum chain (3, 6, 12, 20, 40, …)
# is a set of CUMULATIVE lifetime thresholds, not per-quest counters: a
# "levy grain 3 times" quest counts every grain levy in the play, including
# those done before the quest became current.
LEVY_TYPE_BY_INDEX = {1: 12, 2: 11, 3: 13}  # CollectType index -> MainTaskType
_LEVY_COUNTER_BY_TYPE = {11: "LevyGrain", 12: "LevySilver", 13: "LevySoldiers"}
_LEVY_COUNTER_KEYS = tuple(_LEVY_COUNTER_BY_TYPE.values())
_LIFETIME_LEVY_KEY = "LifetimeLevyCounters"


def _lifetime_levy_counters(player: dict) -> dict:
    counters = player.get(_LIFETIME_LEVY_KEY)
    if not isinstance(counters, dict):
        counters = {}
        player[_LIFETIME_LEVY_KEY] = counters
    # Legacy accounts: seed from today's counters so levy history is kept.
    daily = player.get("DailyCounters")
    if isinstance(daily, dict):
        for key in _LEVY_COUNTER_KEYS:
            if key not in counters:
                counters[key] = int(daily.get(key) or 0)
    return counters


def _refresh_levy_task_num(player: dict) -> None:
    """Recompute the current levy quest's progress from the lifetime total."""
    task_id = get_task_id(player)
    key = _LEVY_COUNTER_BY_TYPE.get(task_type(task_id))
    if key is None:
        return
    total = int(_lifetime_levy_counters(player).get(key) or 0)
    need = _require_num(task_id) or 0
    prog = _task_num(player)
    prog["taskNum"] = min(total, need) if need > 0 else total
    _sync_login_fields(player)


def apply_levy(player: dict, levy_type: int | None = None) -> None:
    """action10001/10002 (levy collect / timer recovery).

    Count the collected resource in a persistent lifetime total, then derive
    the current quest's progress from it.  ``levy_type=None`` is the one-key
    collect covering all three resources.
    """
    counters = _lifetime_levy_counters(player)
    if levy_type is None:
        keys = _LEVY_COUNTER_KEYS
    else:
        key = _LEVY_COUNTER_BY_TYPE.get(levy_type)
        if key is None:
            return
        keys = (key,)
    for counter_key in keys:
        counters[counter_key] = int(counters.get(counter_key) or 0) + 1
    _refresh_levy_task_num(player)
