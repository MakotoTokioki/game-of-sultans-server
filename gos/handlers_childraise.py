"""Child raising/education (11410-11419) and child marriage (11702-11713).

Standalone handler module: the coordinator merges ``CHILDRAISE_HANDLERS`` into
``gos.handlers.HANDLERS``.  Do not edit handlers.py / defaults.py here.  Child
state conventions (``RoleChildren`` records, ``_child_view``, level ceiling,
vitality cooldown) are reused from :mod:`gos.handlers_children` and lazily
seeded via ``setdefault`` so no defaults.py change is needed.

Response shapes come from the decompiled client callbacks:

* ``modules/_childrenRaiseMod.lua`` (11410..11419) and its scene logic
  ``childrenRaiseScene.logic.childrenTravelLogic.lua`` /
  ``childrenRaiseScene.logic.childrenApprenticedLogic.lua``.
* ``modules/_childrenMod.lua`` (11702..11713) and
  ``scene/marriageScene.*``.

``tools/lua_shape_harness/extract_fields.py`` was run for every action; all
listed fields are present (plus fields read through the callback's inner
``setData`` list, which the harness cannot see).

All balance numbers are read from original v2 configs bundled under
``gos/v2configs_data``:

* TcCultivateChildrenVisitCfg  - travel/visit cost (BaseConsume, IncreaseConst,
  BaseConst), Duration.
* TcCultivateAdditionCfg       - PowerAddition (CultivatePercentage /
  CultivateConst) for the apprentice teacher.
* TcCultivateChildrenAptitudeCfg / TcChildrenAptitudeCfg /
  TcChildrenLvUpgradeCfg       - level / star ceilings for child summaries.
* TcMarried                    - IsMarried flag used when a child is married.
* TcVIP                        - ChildMaxVitality (marriage candidate vitality).
* TcChildrenSeatCfg            - child capacity (used to bound child lists).

UNSOURCED / server-authoritative values (documented, never invented as
balance data): the marriage candidate pool, marriage applications, red-dot
flags, animation lists, back-item props and exact match times are produced by
the real server.  In this single-player reimplementation they stay at their
persisted values or at empty Lua-safe containers (``[]`` / ``{}``); empty
containers are safe because the client iterates them with ``pairs``/``ipairs``
and guards optional fields with ``if X then``.  Status 0 is the client's own
success code (``if A0_3.Status == 0 then``), not a guessed balance number.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from . import v2configs
from .handlers import _as_int, _now, attach_remain_list
from .handlers_children import (
    _children,
    _child_view,
    _find_child,
    _max_child_level,
    _rows,
    _sync,
)


# ---------------------------------------------------------------------------
# config helpers
# ---------------------------------------------------------------------------
def _visit_cfg(country_id) -> dict:
    """TcCultivateChildrenVisitCfg row for a country (Id == CountryId), with a
    safe fallback to the first row / empty dict."""
    rows = _rows("TcCultivateChildrenVisitCfg")
    row = next((r for r in rows if _as_int(r.get("Id")) == _as_int(country_id)), None)
    return row if isinstance(row, dict) else (rows[0] if rows else {})


def _visit_cost(visits: int, country_id, visit_count: int) -> int:
    """Mirror of childrenTravelLogic.countMoney/countAllMoney: for each visit
    n in (visit_count, visit_count + visits] the client pays
    ``BaseConsume + IncreaseConst * BaseConst ^ n``.  Every term comes from
    TcCultivateChildrenVisitCfg."""
    visits = max(0, int(visits))
    if visits == 0:
        return 0
    cfg = _visit_cfg(country_id)
    base = _as_int(cfg.get("BaseConsume"))
    increase = _as_int(cfg.get("IncreaseConst"))
    try:
        base_const = float(cfg.get("BaseConst") or 1)
    except (TypeError, ValueError):
        base_const = 1.0
    total = float(base * visits)
    for n in range(visit_count + 1, visit_count + visits + 1):
        total += increase * (base_const ** n)
    return int(math.ceil(total)) if total > 0 else 0


def _power_addition() -> dict:
    """PowerAddition for the apprentice teacher.  The client indexes
    ``.CultivatePercentage`` / ``.CultivateConst`` (ministerMod), so we always
    return both keys when the config is available.  Values come from the first
    TcCultivateAdditionCfg row's ``AdditionValue = [percentage, const]``."""
    rows = _rows("TcCultivateAdditionCfg")
    row = rows[0] if rows else {}
    value = row.get("AdditionValue")
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return {
            "CultivatePercentage": _as_int(value[0]),
            "CultivateConst": _as_int(value[1]),
        }
    return {"CultivatePercentage": 0, "CultivateConst": 0}


def _married_flag() -> int:
    """IsMarried value from the original TcMarried row whose dialog is for our
    own child (IsMyChild = 1, IsMarried = 1).  Falls back to 0."""
    for row in _rows("TcMarried"):
        if _as_int(row.get("IsMyChild")) == 1 and _as_int(row.get("IsMarried")) == 1:
            return _as_int(row.get("IsMarried"))
    return 0


def _child_capacity() -> int:
    """Child capacity ceiling from TcChildrenSeatCfg (largest SeatNum)."""
    seats = [_as_int(r.get("SeatNum")) for r in _rows("TcChildrenSeatCfg")]
    return max(seats, default=0)


def _star_ceiling() -> int:
    """Aptitude star ceiling from TcCultivateChildrenAptitudeCfg."""
    stars = [_as_int(r.get("MaxStarNum")) for r in _rows("TcCultivateChildrenAptitudeCfg")]
    return max(stars, default=0)


def _vip_child_vitality() -> int:
    """ChildMaxVitality from the starter TcVIP row (0)."""
    rows = _rows("TcVIP")
    row = next((r for r in rows if _as_int(r.get("Level")) == 0), None)
    return _as_int(row.get("ChildMaxVitality")) if row else 0


# ---------------------------------------------------------------------------
# lazily seeded state (no defaults.py edit)
# ---------------------------------------------------------------------------
_RAISE_KEY = "RoleChildrenRaise"
_MARRIAGE_KEY = "RoleChildrenMarriage"


def _raise_state(player) -> dict:
    st = player.data.setdefault(_RAISE_KEY, {})
    if not isinstance(st, dict):
        st = {}
        player.data[_RAISE_KEY] = st
    st.setdefault("ChildrenTravelList", [])
    st.setdefault("ChildrenBackList", [])
    st.setdefault("GainItemRemainTimes", {})
    st.setdefault("VisitCount", 0)
    st.setdefault("StudentList", [])
    st.setdefault("ConsumeStage", 0)
    st.setdefault("TotalScore", 0)
    st.setdefault("NewUnlocLvPos", [])
    st.setdefault("RedPointCfg", {"ChildRed": [], "NoChildRed": 0})
    st.setdefault("ChildrenSelectList", [])
    st.setdefault("ResetAttribute", {"MainAttr": 0, "Score": 0})
    st.setdefault("PowerAddition", _power_addition())
    return st


def _marriage_state(player) -> dict:
    st = player.data.setdefault(_MARRIAGE_KEY, {})
    if not isinstance(st, dict):
        st = {}
        player.data[_MARRIAGE_KEY] = st
    st.setdefault("WaitMarryChildren", [])
    st.setdefault("RefreshTime", 0)
    st.setdefault("MarryApply", {})
    st.setdefault("ApplyList", [])
    st.setdefault("SelfApplyList", {})
    st.setdefault("AnimationList", [])
    st.setdefault("ApplyCount", 0)
    st.setdefault("BackItem", {})
    st.setdefault("MarriedChildren", {})
    return st


def _child_id(payload: dict, *keys: str) -> str:
    for key in keys:
        if payload.get(key) is not None:
            return str(payload.get(key) or "")
    return ""


# ---------------------------------------------------------------------------
# shared child-summary shapes
# ---------------------------------------------------------------------------
def _child_summary(child: dict) -> dict:
    """ChildrenSelectList entry (fields from childrenApprenticedLogic.setGuideData
    and the selection entries used by childrenApprenticed.lua)."""
    child = child if isinstance(child, dict) else {}
    return {
        "ChildId": str(child.get("ChildId") or ""),
        "Nickname": str(child.get("Nickname") or ""),
        "Lv": _as_int(child.get("Lv")),
        "MaxLv": _as_int(child.get("MaxLv")) or _max_child_level(),
        "Character": _as_int(child.get("Character")),
        "MainAttr": _as_int(child.get("MainAttr")),
        "Sex": _as_int(child.get("Sex")),
        "Score": _as_int(child.get("Score")),
        "StarCeiling": _star_ceiling(),
    }


def _fmt_time(stamp: int) -> str:
    try:
        return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


# ===========================================================================
# Child raising / education: 11410-11419
# ===========================================================================
def action_child_travel(payload, player, session, store) -> dict:
    """11410: begin a child's travel.  Request {CountryId} (optional ChildId).
    Response: ChildrenTravelList, GainItemRemainTimes, VisitCount."""
    st = _raise_state(player)
    country = _as_int(payload.get("CountryId") or payload.get("countryId"))
    cfg = _visit_cfg(country)
    finish = _now() + _as_int(cfg.get("Duration"))
    child_id = _child_id(payload, "ChildId", "childId") or "child_1"
    travel = st["ChildrenTravelList"]
    existing = next((e for e in travel
                     if isinstance(e, dict) and str(e.get("ChildId")) == child_id
                     and _as_int(e.get("CountryId")) == country), None)
    if existing is not None:
        existing["FinishTime"] = finish
    else:
        travel.append({"ChildId": child_id, "CountryId": country, "FinishTime": finish})
    _children(player)  # seed child state so later actions see a consistent roster
    store.save(player)
    return {
        "ChildrenTravelList": travel,
        "GainItemRemainTimes": st["GainItemRemainTimes"],
        "VisitCount": st["VisitCount"],
    }


def action_child_visit(payload, player, session, store) -> dict:
    """11411: visit a travelling child.  Request {ChildId, CountryId,
    OneKeyNum}.  Cost per visit is TcCultivateChildrenVisitCfg-derived; deduct
    gold and advance VisitCount.  Response: ChildrenUpdateList, VisitCount."""
    st = _raise_state(player)
    one_key = max(0, _as_int(payload.get("OneKeyNum") or payload.get("oneKeyNum") or 1))
    country = _as_int(payload.get("CountryId") or payload.get("countryId"))
    child_id = _child_id(payload, "ChildId", "childId")
    finish = _now() + _as_int(_visit_cfg(country).get("Duration"))

    cost = _visit_cost(one_key, country, _as_int(st["VisitCount"]))
    changed = []
    if cost > 0:
        rd = player.data.setdefault("RoleData", {})
        if "Gold" in rd:
            rd["Gold"] = max(0, _as_int(rd.get("Gold")) - cost)
            changed = [1]
    st["VisitCount"] = _as_int(st["VisitCount"]) + one_key
    for entry in st["ChildrenTravelList"]:
        if isinstance(entry, dict) and (not child_id or str(entry.get("ChildId")) == child_id):
            entry["FinishTime"] = finish
    updates = [{"ChildId": child_id, "FinishTime": finish}]
    store.save(player)
    return attach_remain_list(
        {"ChildrenUpdateList": updates, "VisitCount": st["VisitCount"]},
        player, resources=changed,
    )


def action_child_back(payload, player, session, store) -> dict:
    """11412: collect finished travels.  Response: ChildrenBackList."""
    st = _raise_state(player)
    now = _now()
    rows = _children(player)
    travel = st["ChildrenTravelList"]
    finished = [e for e in travel
                if isinstance(e, dict) and _as_int(e.get("FinishTime")) <= now]
    back = st["ChildrenBackList"]
    for entry in finished:
        child = _find_child(rows, entry.get("ChildId"))
        back.append({
            "ChildId": str(entry.get("ChildId") or ""),
            "childInfo": _child_view(child) if child is not None else {},
            "FinishTime": _as_int(entry.get("FinishTime")),
        })
    st["ChildrenTravelList"] = [e for e in travel if e not in finished]
    store.save(player)
    return {"ChildrenBackList": back}


def action_apprentice_info(payload, player, session, store) -> dict:
    """11413: apprentice panel query.  Response: StudentList, ConsumeStage,
    TotalScore, NewUnlocLvPos, RedPointCfg (plus child summary/attribute fields
    the scene caches through the same data module)."""
    st = _raise_state(player)
    rows = _children(player)
    st["ChildrenSelectList"] = [_child_summary(c) for c in rows][:max(1, _child_capacity())]
    first = rows[0] if rows else {}
    st["ResetAttribute"] = {
        "MainAttr": _as_int(first.get("MainAttr")),
        "Score": _as_int(first.get("Score")),
    }
    st["TotalScore"] = sum(_as_int(e.get("Score")) for e in st["StudentList"]
                           if isinstance(e, dict))
    store.save(player)
    return {
        "StudentList": st["StudentList"],
        "ConsumeStage": st["ConsumeStage"],
        "TotalScore": st["TotalScore"],
        "NewUnlocLvPos": st["NewUnlocLvPos"],
        "RedPointCfg": st["RedPointCfg"],
        "ChildrenSelectList": st["ChildrenSelectList"],
        "ResetAttribute": st["ResetAttribute"],
    }


def action_apprentice_settle(payload, player, session, store) -> dict:
    """11414: assign a counsellor/teacher to a child.  Status 0 is the client
    success code; on success the callback also reads TotalScore, RedPointCfg
    and PowerAddition, so all are included."""
    st = _raise_state(player)
    counsellor = _as_int(payload.get("CounsellorId") or 0)
    rows = _children(player)
    child = _find_child(rows, payload.get("ChildId") or payload.get("childId"))
    if child is not None and counsellor:
        child["TeacherId"] = counsellor
    ori = payload.get("oriChildId") or payload.get("OriChildId")
    if ori:
        old = _find_child(rows, ori)
        if old is not None:
            old["TeacherId"] = 0
    _sync(player, rows)
    st["PowerAddition"] = _power_addition()
    store.save(player)
    return {
        "Status": 0,
        "TotalScore": st["TotalScore"],
        "RedPointCfg": st["RedPointCfg"],
        "PowerAddition": st["PowerAddition"],
    }


def action_apprentice_add_student(payload, player, session, store) -> dict:
    """11415: add a student slot.  The client appends an entry to StudentList
    locally on success; we persist an echoed entry and return ConsumeStage."""
    st = _raise_state(player)
    stage = _as_int(payload.get("ConsumeStage") if payload.get("ConsumeStage") is not None
                    else st["ConsumeStage"])
    st["ConsumeStage"] = stage
    if payload:
        st["StudentList"].append({
            "Position": _as_int(payload.get("Position")),
            "ChildId": _child_id(payload, "ChildId", "childId"),
        })
    store.save(player)
    return {"ConsumeStage": stage}


def action_apprentice_select(payload, player, session, store) -> dict:
    """11416: select a child for a student position.  Response:
    ChildrenSelectList + Status (0 = success)."""
    st = _raise_state(player)
    rows = _children(player)
    child = _find_child(rows, payload.get("ChildId") or payload.get("childId"))
    select = st["ChildrenSelectList"]
    if child is not None:
        entry = _child_summary(child)
        if payload.get("Position") is not None:
            entry["Position"] = _as_int(payload.get("Position"))
        for i, existing in enumerate(select):
            if isinstance(existing, dict) and existing.get("ChildId") == entry["ChildId"]:
                select[i] = entry
                break
    store.save(player)
    return {"ChildrenSelectList": select, "Status": 0}


def action_apprentice_reset(payload, player, session, store) -> dict:
    """11417: reset a child's apprentice attributes.  Response:
    ResetAttribute (MainAttr, Score)."""
    st = _raise_state(player)
    ra = st["ResetAttribute"]
    for key in ("MainAttr", "Score"):
        if payload.get(key) is not None:
            ra[key] = _as_int(payload.get(key))
    store.save(player)
    return {"ResetAttribute": ra}


def action_apprentice_reset_confirm(payload, player, session, store) -> dict:
    """11418: confirm reset attribute change.  Response: ResetAttribute."""
    st = _raise_state(player)
    return {"ResetAttribute": st["ResetAttribute"]}


def action_apprentice_confirm(payload, player, session, store) -> dict:
    """11419: finalise apprenticing.  Request {ChildId, IsConfirm}.  Response:
    PowerAddition, ResetAttribute, Status."""
    st = _raise_state(player)
    rows = _children(player)
    child = _find_child(rows, payload.get("ChildId") or payload.get("childId"))
    ra = st["ResetAttribute"]
    if child is not None:
        child["MainAttr"] = _as_int(ra.get("MainAttr"))
        child["Score"] = _as_int(ra.get("Score"))
        if payload.get("CounsellorId") is not None:
            child["TeacherId"] = _as_int(payload.get("CounsellorId"))
    _sync(player, rows)
    st["PowerAddition"] = _power_addition()
    store.save(player)
    return {
        "PowerAddition": st["PowerAddition"],
        "ResetAttribute": ra,
        "Status": 0,
    }


# ===========================================================================
# Child marriage: 11702-11713
# ===========================================================================
def action_marriage_refresh(payload, player, session, store) -> dict:
    """11702: refresh the marriage candidates (zhaoQin).  Request
    {ChildId, UseGold}.  The candidate pool is server-authoritative; empty list
    is Lua-safe.  Response: WaitMarryChildren, RefreshTime."""
    st = _marriage_state(player)
    st["RefreshTime"] = _now()
    store.save(player)
    return {
        "WaitMarryChildren": st["WaitMarryChildren"],
        "RefreshTime": st["RefreshTime"],
    }


def action_marriage_marry(payload, player, session, store) -> dict:
    """11703: marry our child to the selected candidate.  Request
    {ChildId, OChildId, CostType}.  The candidate is single-player here, so the
    OChild record is derived from config + the request echo; our child is
    marked married via TcMarried.IsMarried.  Response: OChild."""
    st = _marriage_state(player)
    child_id = _child_id(payload, "ChildId", "children_id")
    other_id = _child_id(payload, "OChildId", "o_children_id")
    rows = _children(player)
    child = _find_child(rows, child_id)
    stamp = _now()
    marry_time = _fmt_time(stamp)
    flag = _married_flag()
    if child is not None:
        child["Married"] = flag
        child["MarryChildrenId"] = other_id
        child["MarryTime"] = marry_time
        child["IsApplying"] = 0
    _sync(player, rows)
    ochild = _child_view(child or {})
    ochild.update({
        "ChildId": other_id,
        "MarryChildId": child_id,
        "MarryTime": marry_time,
        "marry_second": stamp,
        "Married": flag,
        "Score": _as_int(child.get("Score")) if child else 0,
    })
    st["MarriedChildren"][child_id] = {
        "MarryChildId": other_id, "MarryTime": marry_time, "Married": flag,
    }
    for key in list(st["MarryApply"]):
        if key == child_id:
            st["MarryApply"].pop(key, None)
    store.save(player)
    return {"OChild": ochild}


def _record_apply(payload, player, st, with_father: bool) -> dict:
    child_id = _child_id(payload, "ChildId", "children_id")
    stamp = _now()
    apply = {"IsApplying": 1, "ApplyTime": stamp}
    if with_father:
        apply["FatherName"] = str(payload.get("FatherName") or "")
    st["MarryApply"][child_id] = dict(apply)
    if payload.get("ShowId") is not None:
        apply["ApplyTargetId"] = _child_id(payload, "ShowId", "show_id")
        st["MarryApply"][child_id]["ApplyTargetId"] = apply["ApplyTargetId"]
    rows = _children(player)
    child = _find_child(rows, child_id)
    if child is not None:
        child["IsApplying"] = 1
        child["ApplyTime"] = stamp
        child["Reject"] = 0
        if payload.get("ShowId") is not None:
            child["ApplyTargetId"] = apply["ApplyTargetId"]
    _sync(player, rows)
    st["SelfApplyList"][child_id] = {
        "IsApplying": 1, "Reject": 0,
        "ApplyTargetId": apply.get("ApplyTargetId", ""),
    }
    return apply


def action_marriage_apply(payload, player, session, store) -> dict:
    """11704: send a marriage application (tiQin).  Request
    {ChildId, CostType, ShowId}.  Response: MarryApply."""
    st = _marriage_state(player)
    apply = _record_apply(payload, player, st, with_father=True)
    store.save(player)
    return {"MarryApply": apply}


def action_marriage_apply_push(payload, player, session, store) -> dict:
    """11705: send a marriage application with push notification.  Request
    {ChildId, CostType, DeviceId, IsPush}.  Response: MarryApply."""
    st = _marriage_state(player)
    apply = _record_apply(payload, player, st, with_father=False)
    store.save(player)
    return {"MarryApply": apply}


def action_marriage_accept(payload, player, session, store) -> dict:
    """11706: accept a marriage proposal.  Request {ORid, OChildId}.  The
    client callback only dispatches a UI event; no response fields are read."""
    _marriage_state(player)
    child_id = _child_id(payload, "OChildId", "o_children_id")
    rows = _children(player)
    child = _find_child(rows, child_id)
    if child is not None:
        child["IsApplying"] = 0
    _sync(player, rows)
    store.save(player)
    return {}


def action_marriage_reject(payload, player, session, store) -> dict:
    """11707: reject a marriage proposal (confirm refuse).  Request
    {children_id}.  Response: AnimationList, SelfApply, msg, table."""
    st = _marriage_state(player)
    child_id = _child_id(payload, "ChildId", "children_id")
    self_apply = {
        "IsApplying": 0,
        "Reject": _as_int(payload.get("Reject") if payload.get("Reject") is not None else 1),
        "ApplyTargetId": str(payload.get("ApplyTargetId") or ""),
    }
    rows = _children(player)
    child = _find_child(rows, child_id)
    if child is not None:
        child["IsApplying"] = 0
        child["Reject"] = self_apply["Reject"]
        child["ApplyTime"] = 0
    _sync(player, rows)
    st["SelfApplyList"][child_id] = self_apply
    st["MarryApply"].pop(child_id, None)
    store.save(player)
    return {
        "AnimationList": st["AnimationList"],
        "SelfApply": self_apply,
        "msg": str(payload.get("msg") or ""),
        "table": {},
    }


def action_marriage_apply_list(payload, player, session, store) -> dict:
    """11708: query pending applications.  Response: AnimationList,
    ApplyCount, ApplyList, animationList (both spellings are read/written by
    the client callback)."""
    st = _marriage_state(player)
    return {
        "AnimationList": st["AnimationList"],
        "ApplyCount": _as_int(st["ApplyCount"]),
        "ApplyList": st["ApplyList"],
        "animationList": st["AnimationList"],
    }


def action_marriage_wait_list(payload, player, session, store) -> dict:
    """11709: query the marriage candidate pool.  Response:
    WaitMarryChildren."""
    st = _marriage_state(player)
    return {"WaitMarryChildren": st["WaitMarryChildren"]}


def action_marriage_reset_apply(payload, player, session, store) -> dict:
    """11710: clear applications/counters.  The client callback only
    dispatches a UI event; no response fields are read."""
    st = _marriage_state(player)
    st["ApplyCount"] = 0
    st["ApplyList"] = []
    st["MarryApply"] = {}
    st["SelfApplyList"] = {}
    store.save(player)
    return {}


def action_marriage_self_apply_list(payload, player, session, store) -> dict:
    """11711: query our own applications.  Request {ChildrenId}.  Response:
    SelfApplyList (dict keyed by child id, Lua-safe)."""
    st = _marriage_state(player)
    return {"SelfApplyList": st["SelfApplyList"]}


def action_marriage_noop(payload, player, session, store) -> dict:
    """11712: no client module exists for this id in v2.005 (the shape harness
    reports NO_MODULE).  Keep a harmless, Lua-safe no-op response."""
    return {}


def action_marriage_back_item(payload, player, session, store) -> dict:
    """11713: query rejected-props item.  Request {ChildId}.  The item is
    server-authoritative; empty dict is Lua-safe for the client's storage.
    Response: BackItem."""
    st = _marriage_state(player)
    return {"BackItem": st["BackItem"]}


# ---------------------------------------------------------------------------
CHILDRAISE_HANDLERS = {
    "11410": action_child_travel,
    "11411": action_child_visit,
    "11412": action_child_back,
    "11413": action_apprentice_info,
    "11414": action_apprentice_settle,
    "11415": action_apprentice_add_student,
    "11416": action_apprentice_select,
    "11417": action_apprentice_reset,
    "11418": action_apprentice_reset_confirm,
    "11419": action_apprentice_confirm,
    "11702": action_marriage_refresh,
    "11703": action_marriage_marry,
    "11704": action_marriage_apply,
    "11705": action_marriage_apply_push,
    "11706": action_marriage_accept,
    "11707": action_marriage_reject,
    "11708": action_marriage_apply_list,
    "11709": action_marriage_wait_list,
    "11710": action_marriage_reset_apply,
    "11711": action_marriage_self_apply_list,
    "11712": action_marriage_noop,
    "11713": action_marriage_back_item,
}
