"""Small self-contained clusters: prison (105xx), seven-day (12601),
synthesis (15100), binding-info (16001).

Shapes come from the decompiled v2.005 client callbacks. Prison/synthesis
response values are initialized from lazy player state (no original config
table exists for them); no numeric rewards or costs are invented.
"""
from __future__ import annotations

from . import v2configs
from .handlers import _as_int, _now, attach_remain_list, _grant_award_pairs, _REMAIN_LIST_MAP


def _prison(player) -> dict:
    p = player.data.setdefault("Prison", {})
    p.setdefault("prisoners", {})          # map: prisonerId -> data (client pairs() it)
    p.setdefault("currentChapter", 0)
    p.setdefault("soldiers", 0)
    p.setdefault("blood", 0)
    return p


def action_prison_info(payload, player, session, store) -> dict:
    """_prisonMod getPrisoners (10501): callback reads prisoners (map),
    currentChapter, soldiers, blood. No v2 config table exists for prison, so
    an unstarted prison (empty map, zero counters) is the safe initial state."""
    p = _prison(player)
    return {"prisoners": dict(p["prisoners"]),
            "currentChapter": int(p["currentChapter"]),
            "soldiers": int(p["soldiers"]), "blood": int(p["blood"])}


def action_prison_punish(payload, player, session, store) -> dict:
    """_prisonMod punishPrisoner (10502): callback reads prisoners, fame,
    remainList. With no seeded prisoners nothing is fabricated; fame is echoed
    from RoleData (the client key is lowercase 'fame' for this callback)."""
    p = _prison(player)
    pid = payload.get("prisoner_id") or payload.get("PrisonerId")
    many = _as_int(payload.get("many") or payload.get("Many") or 1)
    if pid is not None:
        key = str(pid)
        row = p["prisoners"].get(key)
        if isinstance(row, dict):
            left = _as_int(row.get("num") or row.get("Num") or 0) - max(0, many)
            row["num"] = max(0, left)
            if row["num"] <= 0:
                p["prisoners"].pop(key, None)
    fame = _as_int(player.data.get("RoleData", {}).get("fame") or
                   player.data.get("RoleData", {}).get("Fame") or 0)
    store.save(player)
    return {"prisoners": dict(p["prisoners"]), "fame": fame, "remainList": []}


def action_seven_days(payload, player, session, store) -> dict:
    """_sevenDaysMod.action12601: callback just forwards the response to A1_2.
    An empty table is Lua-safe; seven-day reward state lives in ExtraData/login."""
    return {}


def action_synthesis(payload, player, session, store) -> dict:
    """_synthesisMod.action15100: callback writes A0_3.SynthesisNum into the
    matching activity row. Echo a lazy counter keyed by (ActivityId, ItemId)."""
    aid = _as_int(payload.get("ActivityId") or 0)
    iid = _as_int(payload.get("ItemId") or 0)
    num = _as_int(payload.get("Num") or 0)
    state = player.data.setdefault("Synthesis", {})
    key = f"{aid}:{iid}"
    if num:
        state[key] = _as_int(state.get(key) or 0) + num
    store.save(player)
    return {"SynthesisNum": _as_int(state.get(key) or 0)}


def action_binding_info(payload, player, session, store) -> dict:
    """roleMod.binding (16001): callback reads A0_3.Status."""
    base = player.data.get("RoleBase") or {}
    return {"Status": _as_int(base.get("BindingStatus") or 0)}


MISC_HANDLERS = {
    "10501": action_prison_info,
    "10502": action_prison_punish,
    "12601": action_seven_days,
    "15100": action_synthesis,
    "16001": action_binding_info,
}
