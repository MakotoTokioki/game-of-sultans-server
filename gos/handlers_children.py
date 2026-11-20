"""Children core cluster (actions 11400-11407).

Standalone handler module — the coordinator merges ``CHILDREN_HANDLERS`` into
``gos.handlers.HANDLERS``.  Do not edit handlers.py / defaults.py here.

Response shapes come from the decompiled client callbacks in
``modules/_childrenMod.lua`` (functions assigned to ``action11400``..``11407``;
the decompiler reuses the local ``L12_1`` so each request function is the most
recent ``function L12_1(...)`` before its assignment) and were cross-checked
with ``tools/lua_shape_harness/extract_fields.py``:

    11400 -> ChildList[{ChildId, LastRefreshTimeUnix}]
    11401 -> SensitiveWord (optional error), NewName echo + RemainList
    11402 -> Child{Lv, Exp, Force, Intelligence, Politics, Charm,
                   LastRefreshTimeUnix, ...}, MiracleTrigger
    11403 -> Children[...same fields...], MiracleTrigger
    11404 -> LastRefreshTimeUnix
    11405 -> NewTimeList[{ChildId, LastRefreshTimeUnix}]
    11406 -> Capacity
    11407 -> ChildDegree

All balance numbers are read from original v2 configs:
  * ConfigEnvSet  (InitChildrenCapacity, ChangeChildrenNameCost,
                   ChildrenRecoveryVitalityCd, MaxChildrenCount) — already
                   bundled.
  * TcChildrenAptitudeCfg / TcChildrenLvUpgradeCfg (level ceiling)
  * TcChildrenSeatCfg (capacity price)
  * TcChildrenDegreeCfg (investiture degree ids)

UNSOURCED / server-authoritative values (documented, not invented as balance
data): child attribute points (Force/Intelligence/Politics/Charm) and the
per-educate attribute/exp gain are rolled by the real server (the v7 capture
shows account-specific 15/7/3/15 -> 21/10/7/18); we keep them at their
persisted values, i.e. no fabricated gain.  MiracleTrigger is always False
because its bonus multipliers are not modelled here.  ChildId is an opaque
string id generated deterministically ("child_1") rather than a server GUID.
"""
from __future__ import annotations

from . import v2configs
from .handlers import _as_int, _now, attach_remain_list


# ---------------------------------------------------------------------------
# config helpers
# ---------------------------------------------------------------------------
def _rows(name: str) -> list:
    """All dict rows of a v2 config as a list (load() flattens 1..n tables)."""
    table = v2configs.load(name)
    if isinstance(table, list):
        return [r for r in table if isinstance(r, dict)]
    if isinstance(table, dict):
        # Tolerate a keyed table (e.g. ConfigEnvSet-style config maps).
        return [r for r in table.values() if isinstance(r, dict)]
    return []


def _env_int(key: str) -> int:
    cfg = v2configs.load("ConfigEnvSet")
    if isinstance(cfg, dict) and key in cfg:
        return _as_int(cfg.get(key))
    return 0


def _max_child_level() -> int:
    """Level ceiling for the starter (tier-1) child: the smallest MaxLv in
    TcChildrenAptitudeCfg (tier 1 = 10 in the v7 snapshot) capped by the last
    TcChildrenLvUpgradeCfg row.  Both numbers come from original configs."""
    aptitude = _rows("TcChildrenAptitudeCfg")
    upgrade = _rows("TcChildrenLvUpgradeCfg")
    apt_max = min((_as_int(r.get("MaxLv")) for r in aptitude
                   if _as_int(r.get("MaxLv")) > 0), default=0)
    up_max = max((_as_int(r.get("Lv")) for r in upgrade), default=0)
    return apt_max if apt_max and (not up_max or apt_max <= up_max) else up_max


def _recovery_cd() -> int:
    return _env_int("ChildrenRecoveryVitalityCd")


# ---------------------------------------------------------------------------
# child state (lazily seeded, no defaults.py edit)
# ---------------------------------------------------------------------------
_STARTER_ID = "child_1"


def _starter_child() -> dict:
    now = _now()
    upgrade = _rows("TcChildrenLvUpgradeCfg")
    first = min(upgrade, key=lambda r: _as_int(r.get("Lv"))) if upgrade else {}
    return {
        # server-authoritative opaque id (deterministic placeholder)
        "ChildId": _STARTER_ID,
        "Nickname": "",
        # Lv / Exp come from the first TcChildrenLvUpgradeCfg row (1 / 0).
        "Lv": _as_int(first.get("Lv")),
        "Exp": _as_int(first.get("NeedExp")),
        "MaxLv": _max_child_level(),
        # Attribute points are born-rolled on the real server; neutral 0.
        "Force": 0,
        "Intelligence": 0,
        "Politics": 0,
        "Charm": 0,
        # Full vitality: current time minus the configured recovery period.
        "LastRefreshTimeUnix": now - _recovery_cd(),
        "Degree": 0,
        "HasShow": 0,
        "Sex": 0,
        "Character": 0,
        "MainAttr": 0,
        "BeautyId": 0,
        "TeacherId": 0,
        "Married": 0,
        "IsApplying": 0,
        "Score": 0,
        "BornTimestamp": now,
        "MarryTime": "",
        "MarryChildrenId": "",
        "RoleId": "",
    }


def _children(player) -> list:
    """Return the persistent RoleChildren list, seeding one starter child from
    config on first access.  Also mirror into the existing top-level
    ``Children`` slice used by rank/power calculations and login."""
    rows = player.data.setdefault("RoleChildren", [])
    if not isinstance(rows, list):
        rows = []
        player.data["RoleChildren"] = rows
    if not rows:
        rows.append(_starter_child())
    player.data["Children"] = [dict(c) for c in rows]
    return rows


def _find_child(rows: list, child_id) -> dict | None:
    wanted = str(child_id or "")
    for child in rows:
        if isinstance(child, dict) and str(child.get("ChildId") or "") == wanted:
            return child
    # A missing/omitted id targets the first (starter) child.
    return rows[0] if rows and isinstance(rows[0], dict) else None


def _sync(player, rows: list) -> None:
    player.data["Children"] = [dict(c) for c in rows]


def _child_view(child: dict) -> dict:
    """Fields the client callback writes via setChildInfo, plus the extra
    full-record fields present in the v7 child snapshot.  All are real
    ints/strings so Lua never indexes nil."""
    child = child if isinstance(child, dict) else {}
    return {
        "ChildId": str(child.get("ChildId") or ""),
        "Nickname": str(child.get("Nickname") or ""),
        "Lv": _as_int(child.get("Lv")),
        "Exp": _as_int(child.get("Exp")),
        "MaxLv": _as_int(child.get("MaxLv")),
        "Force": _as_int(child.get("Force")),
        "Intelligence": _as_int(child.get("Intelligence")),
        "Politics": _as_int(child.get("Politics")),
        "Charm": _as_int(child.get("Charm")),
        "LastRefreshTimeUnix": _as_int(child.get("LastRefreshTimeUnix")),
        "Degree": _as_int(child.get("Degree")),
        "HasShow": _as_int(child.get("HasShow")),
        "OverTime": _recovery_cd(),
        "Sex": _as_int(child.get("Sex")),
        "Character": _as_int(child.get("Character")),
        "Married": _as_int(child.get("Married")),
    }


def _child_time_entry(child: dict) -> dict:
    return {
        "ChildId": str(child.get("ChildId") or ""),
        "LastRefreshTimeUnix": _as_int(child.get("LastRefreshTimeUnix")),
    }


def _educate(child: dict) -> None:
    """One educate/raise step: advance one level (bounded by the config level
    ceiling) and stamp a fresh full-vitality refresh time.  Attribute and EXP
    gains are server-rolled in the real game and are left untouched here."""
    new_lv = min(_as_int(child.get("Lv")) + 1, _max_child_level())
    child["Lv"] = new_lv
    child["LastRefreshTimeUnix"] = _now() - _recovery_cd()


# ---------------------------------------------------------------------------
# action handlers
# ---------------------------------------------------------------------------
def action_children_list(payload, player, session, store) -> dict:
    """11400: haremScene asks for the child roster (request {})."""
    rows = _children(player)
    store.save(player)
    return {"ChildList": [_child_time_entry(c) for c in rows]}


def action_children_name(payload, player, session, store) -> dict:
    """11401: {ChildId, Nickname}.  First naming is free (v7 capture shows an
    empty RemainList); renaming consumes ConfigEnvSet.ChangeChildrenNameCost
    gold.  A sensitive-word rejection is server-side and omitted (the client
    guards it with `if SensitiveWord then`)."""
    name = str(payload.get("Nickname") or payload.get("nickname") or "").strip()
    rows = _children(player)
    child = _find_child(rows, payload.get("ChildId") or payload.get("children_id"))
    renaming = bool(child and str(child.get("Nickname") or ""))
    if child is not None:
        child["Nickname"] = name
    _sync(player, rows)
    data = {"NewName": name}
    changed = []
    if renaming:
        rd = player.data.setdefault("RoleData", {})
        cost = _env_int("ChangeChildrenNameCost")
        rd["Gold"] = max(0, _as_int(rd.get("Gold")) - cost)
        changed = [1]
    store.save(player)
    return attach_remain_list(data, player, resources=changed)


def action_children_educate(payload, player, session, store) -> dict:
    """11402: {ChildId} -> updated single child + MiracleTrigger."""
    rows = _children(player)
    child = _find_child(rows, payload.get("ChildId") or payload.get("children_id"))
    if child is None:
        return {"Child": _child_view({}), "MiracleTrigger": False}
    _educate(child)
    _sync(player, rows)
    store.save(player)
    return {"Child": _child_view(child), "MiracleTrigger": False}


def action_children_educate_all(payload, player, session, store) -> dict:
    """11403: one-key educate (request {}) -> all updated children."""
    rows = _children(player)
    for child in rows:
        if isinstance(child, dict):
            _educate(child)
    _sync(player, rows)
    store.save(player)
    return {"Children": [_child_view(c) for c in rows], "MiracleTrigger": False}


def action_children_recover(payload, player, session, store) -> dict:
    """11404: {ChildId} -> one refreshed LastRefreshTimeUnix."""
    rows = _children(player)
    child = _find_child(rows, payload.get("ChildId") or payload.get("children_id"))
    if child is None:
        return {"LastRefreshTimeUnix": _now() - _recovery_cd()}
    child["LastRefreshTimeUnix"] = _now() - _recovery_cd()
    _sync(player, rows)
    store.save(player)
    return {"LastRefreshTimeUnix": _as_int(child.get("LastRefreshTimeUnix"))}


def action_children_recover_all(payload, player, session, store) -> dict:
    """11405: one-key recover (request {}) -> NewTimeList."""
    rows = _children(player)
    stamp = _now() - _recovery_cd()
    for child in rows:
        if isinstance(child, dict):
            child["LastRefreshTimeUnix"] = stamp
    _sync(player, rows)
    store.save(player)
    return {"NewTimeList": [_child_time_entry(c) for c in rows]}


def action_children_seat_expand(payload, player, session, store) -> dict:
    """11406: add a child seat.  Request is {}; the price comes from the
    TcChildrenSeatCfg row whose SeatNum is current capacity + 1 (exactly how
    the client's get_seat_cost selects it)."""
    _children(player)
    rd = player.data.setdefault("RoleData", {})
    capacity = _as_int(rd.get("ChildrenCapacity"))
    changed = []
    if capacity <= 0:
        capacity = _env_int("InitChildrenCapacity")
    seat = next((r for r in _rows("TcChildrenSeatCfg")
                 if _as_int(r.get("SeatNum")) == capacity + 1), None)
    if seat is not None:
        rd["Gold"] = max(0, _as_int(rd.get("Gold")) - _as_int(seat.get("Price")))
        capacity += 1
        changed = [1]
    rd["ChildrenCapacity"] = capacity
    store.save(player)
    return attach_remain_list({"Capacity": capacity}, player, resources=changed)


def action_children_degree(payload, player, session, store) -> dict:
    """11407: {ChildId} -> investiture; advance Degree to the next
    TcChildrenDegreeCfg id (initial 0 in the v7 snapshot)."""
    rows = _children(player)
    child = _find_child(rows, payload.get("ChildId") or payload.get("children_id"))
    degrees = sorted(_as_int(r.get("Id")) for r in _rows("TcChildrenDegreeCfg"))
    top = degrees[-1] if degrees else 0
    degree = min((_as_int(child.get("Degree")) if child else 0) + 1, top)
    if child is not None:
        child["Degree"] = degree
    _sync(player, rows)
    store.save(player)
    return {"ChildDegree": degree}


CHILDREN_HANDLERS = {
    "11400": action_children_list,
    "11401": action_children_name,
    "11402": action_children_educate,
    "11403": action_children_educate_all,
    "11404": action_children_recover,
    "11405": action_children_recover_all,
    "11406": action_children_seat_expand,
    "11407": action_children_degree,
}
