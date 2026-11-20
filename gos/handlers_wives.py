"""Game of Sultans v2 wives / harem cluster (actions 10402-10412).

Self-contained module so it can be merged into ``gos.handlers.HANDLERS`` by the
coordinator without editing ``gos/handlers.py``.

Ground rules (tools/HANDLER_SPEC.md):
  * every number comes from an original v2 config or from the request / player
    state; nothing is invented;
  * response shapes are the exact fields the v2 client callback reads
    (modules/_wivesMod.lua 1616-2000), cross-checked with
    ``tools/lua_shape_harness/extract_fields.py``;
  * optional fields the client guards with ``if X then`` are omitted when the
    server has no value for them (Children / per-wife MiracleTrigger).

Player-owned state lives in ``RoleBeauty`` (a dict keyed by the string beauty
id, exactly like ``gos.handlers._beauty_row`` builds).  New state slices are
seeded lazily here; ``gos/defaults.py`` is untouched.
"""
from __future__ import annotations

import time as _time

from gos import v2configs

# Import the shared helpers when possible.  If the coordinator merges this
# module from the top of gos/handlers.py, that module is only partially
# initialised and these names do not exist yet -- fall back to local,
# config-sourced equivalents so the merge cannot break the import chain.
try:
    from gos.handlers import _as_int, _beauty_row, _now
except (ImportError, AttributeError):  # pragma: no cover - merge-time only
    def _as_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _now() -> int:
        return int(_time.time())

    def _beauty_row(beauty_id: int) -> dict:
        cfg = {}
        for row in v2configs.load("TcBeauty"):
            if isinstance(row, dict) and int(row.get("BeautyId") or 0) == int(beauty_id):
                cfg = row
                break
        skills = [[int(s), 0, 0] for s in (cfg.get("Skills") or [])
                  if str(s).isdigit()]
        skin = {"SkinId": int(beauty_id), "SkinLv": 0}
        return {
            "BeautyId": int(beauty_id),
            "Intimacy": 0,
            "Charm": int(cfg.get("InitCharm") or 0),
            "Position": int(cfg.get("Position") or 0),
            "BeautyExp": 0,
            "ChildrenNum": 0,
            "Favor": 0,
            "IsUnlock": 1,
            "DressSkin": dict(skin),
            "HaveSkinId": [dict(skin)],
            "Skills": skills,
        }


# ---------------------------------------------------------------------------
# small helpers (config access + RoleBeauty state)
# ---------------------------------------------------------------------------
def _cfg_rows(name: str) -> list[dict]:
    """Normalise ``v2configs.load`` output to a list of row dicts."""
    rows = v2configs.load(name)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, (list, tuple)):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _wives(player):
    wives = player.data.get("RoleBeauty")
    if not isinstance(wives, dict):
        wives = {}
        player.data["RoleBeauty"] = wives
    return wives


def _first_beauty_id() -> int:
    """Fallback beauty id when the request omits it (config-sourced, never 1)."""
    rows = _cfg_rows("TcBeauty")
    return _as_int(rows[0].get("BeautyId")) if rows else 0


def _beauty_id(payload: dict) -> int:
    return _as_int(payload.get("BeautyId") or payload.get("beautyId") or 0) or _first_beauty_id()


def _wife_row(player, beauty_id: int) -> dict:
    """Get (or config-seed) the player's RoleBeauty row for ``beauty_id``."""
    wives = _wives(player)
    key = str(int(beauty_id))
    row = wives.get(key)
    if not isinstance(row, dict):
        row = _beauty_row(int(beauty_id))
        wives[key] = row
    return row


def _skills(row: dict) -> list:
    slots = row.get("Skills")
    if not isinstance(slots, list):
        slots = []
        row["Skills"] = slots
    for slot in slots:
        if not isinstance(slot, list):
            # A malformed legacy slot must not crash the client callback.
            slots[slots.index(slot)] = [0, 0, 0]
    return slots


def _position_levels() -> list[int]:
    """Configured harem position tiers, ascending (TcBeautyPosition.LV)."""
    levels = sorted(
        {_as_int(row.get("LV")) for row in _cfg_rows("TcBeautyPosition")}
    )
    return [lv for lv in levels if lv > 0]


def _position_row(level: int) -> dict:
    for row in _cfg_rows("TcBeautyPosition"):
        if _as_int(row.get("LV")) == int(level):
            return row
    return {}


def _save(store, player) -> None:
    save = getattr(store, "save", None)
    if callable(save):
        save(player)


# ---------------------------------------------------------------------------
# 10402 - patronize a wife (silver cost is computed client-side)
# ---------------------------------------------------------------------------
def action_wives_patronize(payload: dict, player, session, store) -> dict:
    """wivesInfo "Patronize" -> action10402.

    Callback (modules/_wivesMod.lua 1623-1660) sets Intimacy / BeautyExp to the
    absolute values returned, then shows the deltas.  The per-action increase
    is server-authoritative and not present in any v2 config, so the current
    (config-seeded) totals are echoed; this is Lua-safe and never invents a
    reward.  ``Children`` is optional and omitted.
    """
    row = _wife_row(player, _beauty_id(payload))
    intimacy = _as_int(row.get("Intimacy"))
    exp = _as_int(row.get("BeautyExp"))
    row["Intimacy"] = intimacy
    row["BeautyExp"] = exp
    _save(store, player)
    return {"Intimacy": intimacy, "Exp": exp, "MiracleTrigger": False}


# ---------------------------------------------------------------------------
# 10403 - level up one of a wife's skills (spends accumulated BeautyExp)
# ---------------------------------------------------------------------------
def action_wives_skill_up(payload: dict, player, session, store) -> dict:
    """action10403: setWivesInfo(beauty, "Skills", SkillLv, skillNum) and
    setWivesInfo(beauty, "BeautyExp", BeautyExp).

    SkillLv is the next level present in TcBeautySkillsUpgrade (the same table
    the client's judge_skill_red reads).  BeautyExp is cumulative in v2 (the
    SkillN column is a monotonic threshold), so it is echoed, not spent.
    """
    row = _wife_row(player, _beauty_id(payload))
    skill_num = _as_int(payload.get("SkillNum") or payload.get("skillNum"))
    slots = _skills(row)
    exp = _as_int(row.get("BeautyExp"))
    skill_lv = 0
    if 1 <= skill_num <= len(slots):
        slot = slots[skill_num - 1]
        current = _as_int(slot[1])
        # TcBeautySkillsUpgrade rows are the levels the client can reach.
        next_rows = [r for r in _cfg_rows("TcBeautySkillsUpgrade")
                     if _as_int(r.get("SkillLv")) == current + 1]
        if next_rows:
            skill_lv = _as_int(next_rows[0].get("SkillLv"))
            slot[1] = skill_lv
    row["BeautyExp"] = exp
    _save(store, player)
    return {"SkillLv": skill_lv, "BeautyExp": exp}


# ---------------------------------------------------------------------------
# 10404 - give an item to a wife (charm / intimacy)
# ---------------------------------------------------------------------------
def action_wives_gift_item(payload: dict, player, session, store) -> dict:
    """action10404 callback sets Intimacy and Charm to absolute totals.

    The item's per-use charm/intimacy values are not in any bundled v2 config,
    so the current (config-derived) totals are echoed instead of invented.
    """
    row = _wife_row(player, _beauty_id(payload))
    charm = _as_int(row.get("Charm"))
    intimacy = _as_int(row.get("Intimacy"))
    row["Charm"] = charm
    row["Intimacy"] = intimacy
    _save(store, player)
    return {"Charm": charm, "Intimacy": intimacy}


# ---------------------------------------------------------------------------
# 10405 / 10408 - vigor recovery timestamp
# ---------------------------------------------------------------------------
def _stamp_vigor(player, store) -> int:
    now = _now()
    player.data.setdefault("RoleData", {})["VigorTimeUnix"] = now
    _save(store, player)
    return now


def action_wives_recover_vigor(payload: dict, player, session, store) -> dict:
    """10405 (use recover item): callback only reads VigorTimeUnix."""
    return {"VigorTimeUnix": _stamp_vigor(player, store)}


def action_harem_open(payload: dict, player, session, store) -> dict:
    """10408 (open the harem scene): callback only reads VigorTimeUnix."""
    return {"VigorTimeUnix": _stamp_vigor(player, store)}


# ---------------------------------------------------------------------------
# 10406 - group call / patronize several wives at once
# ---------------------------------------------------------------------------
def action_wives_group_call(payload: dict, player, session, store) -> dict:
    """10406 callback iterates A0_3.ChoiceBeauty pairs and reads, per entry:
    BeautyId, Exp (new BeautyExp total) and optional MiracleTrigger.
    VigorTimeUnix is stamped as on 10401/10405.
    """
    choice = []
    for key in sorted(_wives(player), key=lambda k: _as_int(k)):
        row = _wives(player)[key]
        if not isinstance(row, dict) or _as_int(row.get("IsUnlock")) != 1:
            continue
        exp = _as_int(row.get("BeautyExp"))
        choice.append({"BeautyId": _as_int(row.get("BeautyId") or key), "Exp": exp})
        row["BeautyExp"] = exp
    return {"ChoiceBeauty": choice, "VigorTimeUnix": _stamp_vigor(player, store)}


# ---------------------------------------------------------------------------
# 10409 - promote / demote a wife's harem position
# ---------------------------------------------------------------------------
def action_wives_promote(payload: dict, player, session, store) -> dict:
    """10409 callback forwards the whole response; the caller reads
    ``Position`` (the new absolute position tier) from it.
    Tiers come from TcBeautyPosition.LV; promotion also obeys that tier's
    NeedCharm / NeedIntimacy and slot Limit (same checks as the client).
    """
    row = _wife_row(player, _beauty_id(payload))
    promote = _as_int(payload.get("Promote") or payload.get("promote"))
    levels = _position_levels()
    if not levels:
        return {"Position": _as_int(row.get("Position"))}
    current = _as_int(row.get("Position"))
    if current not in levels:
        current = levels[0]
    idx = levels.index(current)
    step = 1 if promote > 0 else (-1 if promote < 0 else 0)
    target_idx = min(max(idx + step, 0), len(levels) - 1)
    target = levels[target_idx]
    if target_idx > idx:
        need = _position_row(target)
        if (_as_int(row.get("Charm")) < _as_int(need.get("NeedCharm"))
                or _as_int(row.get("Intimacy")) < _as_int(need.get("NeedIntimacy"))):
            target = current
        else:
            occupied = sum(
                1 for r in _wives(player).values()
                if isinstance(r, dict) and _as_int(r.get("Position")) == target
            )
            if occupied >= _as_int(need.get("Limit")):
                target = current
    row["Position"] = target
    _save(store, player)
    return {"Position": target}


# ---------------------------------------------------------------------------
# 10410 - refresh the harem position map (BeautyList)
# ---------------------------------------------------------------------------
def action_wives_beauty_list(payload: dict, player, session, store) -> dict:
    """10410 callback assigns ``A0_3.BeautyList`` to the module's position map.

    Lua shape: a dict keyed by the string tier LV, each value a list whose
    first element is the member count and whose remaining elements are the
    beauty ids in that tier (the client mutates element 1 as the count).
    Every configured tier is present so indexed reads never hit nil.
    """
    wives = _wives(player)
    beauty_list = {}
    for level in _position_levels():
        members = []
        for key in sorted(wives, key=lambda k: _as_int(k)):
            row = wives[key]
            if (isinstance(row, dict) and _as_int(row.get("Position")) == level
                    and _as_int(row.get("IsUnlock")) == 1):
                members.append(_as_int(row.get("BeautyId") or key))
        beauty_list[str(level)] = [len(members)] + members
    return {"BeautyList": beauty_list}


# ---------------------------------------------------------------------------
# 10411 - raise a skill's star level
# ---------------------------------------------------------------------------
def action_wives_star_up(payload: dict, player, session, store) -> dict:
    """10411 callback does setWivesInfo(wife, "Skills", StarLv, skillNum, 3).

    Star levels are taken from TcBeautyStarLevel rows filtered by the skill's
    AdditionType (TcBeautySkills), exactly like get_skill_Star_Info does.
    """
    row = _wife_row(player, _beauty_id(payload))
    skill_id = _as_int(payload.get("SkillId") or payload.get("skillId"))
    skill_num = _as_int(payload.get("SkillNum") or payload.get("skillNum"))
    slots = _skills(row)
    idx = skill_num - 1 if 1 <= skill_num <= len(slots) else -1
    if idx < 0 and skill_id:
        for i, slot in enumerate(slots):
            if _as_int(slot[0]) == skill_id:
                idx = i
                break

    skill_cfg = next((r for r in _cfg_rows("TcBeautySkills")
                      if _as_int(r.get("SkillId")) == skill_id), {})
    star_levels = sorted({_as_int(r.get("Level")) for r in _cfg_rows("TcBeautyStarLevel")
                          if _as_int(r.get("AdditionId")) == _as_int(skill_cfg.get("AdditionType"))})
    star_levels = [lv for lv in star_levels if lv > 0]

    star_lv = 0
    if idx >= 0 and star_levels:
        current = _as_int(slots[idx][2])
        higher = [lv for lv in star_levels if lv > current]
        if higher:
            star_lv = higher[0]
            slots[idx][2] = star_lv
    _save(store, player)
    return {"StarLv": star_lv}


# ---------------------------------------------------------------------------
# 10412 - equip a wife's skin (dress)
# ---------------------------------------------------------------------------
def action_wives_dress_skin(payload: dict, player, session, store) -> dict:
    """10412 callback builds DressSkin from the request's skinData fields and
    reads no response field, so an empty table is the correct Lua-safe answer.
    """
    skin = payload.get("skinData") if isinstance(payload.get("skinData"), dict) else {}
    beauty_id = _as_int(skin.get("id") or payload.get("BeautyId")
                            or payload.get("beautyId") or 0)
    if beauty_id:
        row = _wife_row(player, beauty_id)
        row["DressSkin"] = {
            "SkinId": _as_int(skin.get("skinId") or payload.get("SkinId")),
            "SkinLv": _as_int(skin.get("skinLv") or payload.get("SkinLv")),
        }
        _save(store, player)
    return {}


# Coordinator merges this dict into gos.handlers.HANDLERS.
WIVES_HANDLERS = {
    "10402": action_wives_patronize,
    "10403": action_wives_skill_up,
    "10404": action_wives_gift_item,
    "10405": action_wives_recover_vigor,
    "10406": action_wives_group_call,
    "10408": action_harem_open,
    "10409": action_wives_promote,
    "10410": action_wives_beauty_list,
    "10411": action_wives_star_up,
    "10412": action_wives_dress_skin,
}
