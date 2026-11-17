"""Loader for the v2 client's Lua config tables.

The required gameplay tables are bundled from the v2 client (2.0.05) under
gos/v2configs_data/settings.*.lua.  The server must not invent
levels/costs/rewards, so every handler that needs a number reads it here.

For development, GOS_CONFIG_DIR can point at the decompiled config directory
(e.g. /tmp/opencode/gos/decomp_all/configs).  If it is not set, the bundled
snapshot is used.

The files are machine-generated and use a tiny, rigid Lua subset:

    local L0_1, L1_1
    L0_1 = {}
    L1_1 = {}
    L1_1.Type2 = 12
    L0_1[201] = L1_1
    return L0_1

plus nested table constructors and `[expr]` indexes with string variables
(ConfigEnvSet.lua: `L1_1 = "StudyCd"; L0_1[L1_1] = L2_1`).  This module
implements just enough of that grammar; anything else (functions, long
strings, multi-line constructs) raises and the caller gets an empty table
instead of a wrong one.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_BUNDLED_CONFIG_DIR = Path(__file__).with_name("v2configs_data")
_DECOMP_CONFIG_DIR = Path(
    os.environ.get("GOS_DECOMP_ROOT", "/tmp/opencode/gos/decomp_all")
) / "configs"
CONFIG_DIR = Path(os.environ.get("GOS_CONFIG_DIR") or _BUNDLED_CONFIG_DIR)
if not CONFIG_DIR.is_dir():
    CONFIG_DIR = _DECOMP_CONFIG_DIR

_LOCAL = re.compile(r"^local\s")
_RETURN = re.compile(r"^return\s+(.+)$")
_FUNC = re.compile(r"function\s*\(")
_ASSIGN = re.compile(
    r"^(?P<var>[A-Za-z_]\w*)(?:\.(?P<field>[A-Za-z_]\w*)|\[(?P<index>[^\]]+)\])?"
    r"\s*=\s*(?P<expr>.+)$"
)


class LuaParseError(Exception):
    pass


def _strip_comment(line: str) -> str:
    out = []
    in_str = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == "\\" and i + 1 < len(line):
                out.append(line[i : i + 2])
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "-" and line.startswith("--", i):
            break
        out.append(ch)
        i += 1
    return "".join(out).strip()


class _Reader:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def eof(self) -> bool:
        self.skip_ws()
        return self.pos >= len(self.text)


def _parse_expr(rd: _Reader, env: dict) -> Any:
    rd.skip_ws()
    if rd.eof():
        raise LuaParseError("unexpected end of expression")
    ch = rd.text[rd.pos]
    if ch == "{":
        return _parse_table(rd, env)
    if ch == '"':
        return _parse_string(rd)
    m = re.match(r"-?\d+(?:\.\d+)?", rd.text[rd.pos :])
    if m:
        token = m.group(0)
        rd.pos += len(token)
        return float(token) if "." in token else int(token)
    m = re.match(r"[A-Za-z_]\w*", rd.text[rd.pos :])
    if m:
        token = m.group(0)
        rd.pos += len(token)
        if token == "nil":
            return None
        if token == "true":
            return True
        if token == "false":
            return False
        value = env.get(token)
        # dotted lookup (var.field) is not generated in these files, but
        # tolerate one level so a stray reference degrades to None cleanly.
        while rd.text[rd.pos : rd.pos + 1] == "." and not rd.eof():
            m2 = re.match(r"\.([A-Za-z_]\w*)", rd.text[rd.pos :])
            if not m2 or not isinstance(value, dict):
                break
            rd.pos += len(m2.group(0))
            value = value.get(m2.group(1))
        return value
    raise LuaParseError(f"cannot parse expression at: {rd.text[rd.pos:rd.pos + 40]!r}")


def _parse_string(rd: _Reader) -> str:
    out = []
    rd.pos += 1  # opening quote
    while rd.pos < len(rd.text):
        ch = rd.text[rd.pos]
        if ch == "\\" and rd.pos + 1 < len(rd.text):
            out.append(rd.text[rd.pos + 1])
            rd.pos += 2
            continue
        if ch == '"':
            rd.pos += 1
            return "".join(out)
        out.append(ch)
        rd.pos += 1
    raise LuaParseError("unterminated string")


def _parse_table(rd: _Reader, env: dict) -> dict:
    """Parse a `{...}` constructor into a plain dict.

    Table constructors always decode to a dict here; the array conversion
    (contiguous 1..n integer keys -> list) happens once, on the returned
    root table, so field assignments like `L1_1.Type2 = 1` keep working on
    a table that was created empty with `L1_1 = {}`.
    """
    rd.pos += 1  # '{'
    table: dict = {}
    while True:
        rd.skip_ws()
        if rd.eof():
            raise LuaParseError("unterminated table constructor")
        ch = rd.text[rd.pos]
        if ch == "}":
            rd.pos += 1
            break
        m = re.match(r"(?:\[([^\]]+)\]|([A-Za-z_]\w*))\s*=\s*", rd.text[rd.pos :])
        if m:
            key_expr, name = m.group(1), m.group(2)
            rd.pos += len(m.group(0))
            if name is not None:
                key: Any = name
            else:
                key = _parse_expr(rd, env)
            if isinstance(key, str) and key.isdigit():
                key = int(key)
            table[key] = _parse_expr(rd, env)
        else:
            next_int = 1 + max([k for k in table if isinstance(k, int)] or [0])
            table[next_int] = _parse_expr(rd, env)
        rd.skip_ws()
        if rd.text[rd.pos : rd.pos + 1] == ",":
            rd.pos += 1
        elif rd.text[rd.pos : rd.pos + 1] == "}":
            rd.pos += 1
            break
        elif rd.eof():
            raise LuaParseError("unterminated table constructor")
        elif rd.text[rd.pos] not in ",}":
            raise LuaParseError(f"bad table separator at: {rd.text[rd.pos:rd.pos + 30]!r}")
    return table


def _to_lua_value(value: Any) -> Any:
    """Recursive dict->list normalisation of a decoded Lua table.

    A table whose keys are exactly the contiguous integers 1..n becomes a
    Python list; anything else (string keys, gaps, mixed keys) stays a dict
    with the original keys.
    """
    if isinstance(value, dict):
        int_keys = sorted(k for k in value if isinstance(k, int))
        if int_keys and int_keys == list(range(1, len(int_keys) + 1)) and not any(
            not isinstance(k, int) for k in value
        ):
            return [_to_lua_value(value[k]) for k in int_keys]
        return {k: _to_lua_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_lua_value(v) for v in value]
    return value


def _as_list(table: Any) -> list:
    """Convert a Lua table with contiguous 1..n integer keys into a list."""
    if not isinstance(table, dict):
        return table if isinstance(table, list) else []
    keys = [k for k in table if isinstance(k, int)]
    if not keys:
        return []
    if sorted(keys) == list(range(1, len(keys) + 1)):
        return [table[k] for k in sorted(keys)]
    return [table[k] for k in sorted(keys)]


def parse_lua_table_file(path: Path) -> Any:
    """Parse a generated settings.*.lua file and return its `return` value."""
    env: dict[str, Any] = {}
    returned: Any = None
    saw_return = False
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = _strip_comment(raw_line)
        if not line or line.startswith("local"):
            continue
        if _FUNC.match(line):
            raise LuaParseError(f"unsupported construct in {path.name}: {line[:60]!r}")
        m = _RETURN.match(line)
        if m:
            rd = _Reader(m.group(1))
            returned = _to_lua_value(_parse_expr(rd, env))
            saw_return = True
            continue
        m = _ASSIGN.match(line)
        if not m:
            raise LuaParseError(f"unsupported statement in {path.name}: {line[:60]!r}")
        var, field, index, expr = (
            m.group("var"),
            m.group("field"),
            m.group("index"),
            m.group("expr"),
        )
        rd = _Reader(expr)
        value = _parse_expr(rd, env)
        if rd.text[rd.pos :].strip():
            raise LuaParseError(f"trailing tokens in {path.name}: {line[:60]!r}")
        if field is not None or index is not None:
            target = env.setdefault(var, {})
            if not isinstance(target, dict):
                raise LuaParseError(f"assignment to non-table {var} in {path.name}")
            if field is not None:
                target[field] = value
            else:
                key = _parse_expr(_Reader(index), env)
                if isinstance(key, str) and key.isdigit():
                    key = int(key)
                target[key] = value
        else:
            env[var] = value
    if not saw_return:
        raise LuaParseError(f"no return statement in {path.name}")
    return returned


_CACHE: dict[str, Any] = {}


def load(name: str) -> Any:
    """Parse configs/settings.<name>.lua once and cache the result.

    Returns {} when the file is missing or unreadable so callers degrade to
    "no config" instead of crashing the action.
    """
    if name in _CACHE:
        return _CACHE[name]
    path = CONFIG_DIR / f"settings.{name}.lua"
    try:
        value = parse_lua_table_file(path)
    except Exception:
        value = {}
    _CACHE[name] = value
    return value


def clear_cache() -> None:
    _CACHE.clear()


# ---- typed accessors used by the handlers ---------------------------------

def item_by_id(item_id: int) -> dict:
    """TcItem row for an item id (the client's getItemCfg(id) equivalent)."""
    for row in _as_list(load("TcItem")):
        if isinstance(row, dict) and int(row.get("Id") or 0) == int(item_id):
            return row
    return {}


def talent_by_id(talent_id: int) -> dict:
    for row in _as_list(load("TcTalent")):
        if isinstance(row, dict) and int(row.get("Id") or 0) == int(talent_id):
            return row
    return {}


def star_by_level(star_level: int) -> dict:
    for row in _as_list(load("TcStar")):
        if isinstance(row, dict) and int(row.get("StarLevel") or 0) == int(star_level):
            return row
    return {}


def peerage_rows() -> list:
    """TcPeeragesUpgrade rows sorted by LvLimit (the client's
    get_setting_peerages: table.sort by LvLimit ascending)."""
    rows = [r for r in _as_list(load("TcPeeragesUpgrade")) if isinstance(r, dict)]
    rows.sort(key=lambda r: int(r.get("LvLimit") or 0))
    return rows


def school_rows() -> list:
    return [r for r in _as_list(load("TcSchool")) if isinstance(r, dict)]


def cumulative_reward_rows() -> list:
    return [r for r in _as_list(load("TcSignInCumulativeRewards")) if isinstance(r, dict)]


def counsellor_type(counsellor_id: int) -> int:
    row = None
    for r in _as_list(load("TcCounsellor")):
        if isinstance(r, dict) and int(r.get("CounsellorId") or 0) == int(counsellor_id):
            row = r
            break
    return int(row.get("CounsellorType") or 0) if row else 0


def counsellor_skin(counsellor_id: int) -> dict:
    for r in _as_list(load("TcCounsellorSkin")):
        if isinstance(r, dict) and int(r.get("CounsellorId") or 0) == int(counsellor_id):
            return r
    return {}


def maintask_award_items(task_id: int) -> list[list[int]]:
    """TcMainTask[task_id].AwardItem as [[ItemId, Num], ...].

    Bundled from the *original* settings.TcMainTask.lua (v2 client 2.0.05);
    no reward value is invented server-side.  e.g. MainTaskId 1 awards
    [[2, 100000]] (Silver +100000), matching the official v7 10015 capture.
    """
    for row in _as_list(load("TcMainTask")):
        if isinstance(row, dict) and int(row.get("MainTaskId") or 0) == int(task_id):
            award = row.get("AwardItem")
            pairs: list[list[int]] = []
            if isinstance(award, (list, tuple)):
                for pair in award:
                    if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                        try:
                            pairs.append([int(pair[0]), int(pair[1])])
                        except (TypeError, ValueError):
                            continue
            return pairs
    return []


def vip_row(vip_level: int) -> dict:
    """TcVIP row keyed by Level (0 = starter VIP)."""
    for row in _as_list(load("TcVIP")):
        if isinstance(row, dict) and int(row.get("Level") or 0) == int(vip_level):
            return row
    return {}


def env_value(name: str, default: Any = None) -> Any:
    value = load("ConfigEnvSet")
    if isinstance(value, dict) and name in value:
        return value[name]
    return default
