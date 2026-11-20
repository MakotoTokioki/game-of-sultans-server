#!/usr/bin/env python3
"""Drive the Lua shape harness against GoS v2.005 client response callbacks.

For each action we locate the decompiled module that defines its request
function, ask the reimplemented server for a sample response (or use a golden
v7 / fixture response), and run the real decompiled Lua response callback
under a sandbox with strict nil-read logging. This validates that a response
has the exact shape the client expects — without running the full game.

Usage:
  python3 tools/lua_shape_harness/run.py --action 11302
  python3 tools/lua_shape_harness/run.py --action 11302 --response fixture.json
  python3 tools/lua_shape_harness/run.py --all-implemented
  python3 tools/lua_shape_harness/run.py --scan-missing      # empty response -> required fields
  python3 tools/lua_shape_harness/run.py --regen-configs

Output reports go to recon/protocol/shape-*.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]        # /root/game-of-sultans/server
ROOT = SERVER.parent                               # /root/game-of-sultans
DECOMP = Path("/tmp/opencode/gos/decomp_all")
HARNESS = Path(__file__).parent / "harness.lua"
CONFIG_DIR = Path("/tmp/opencode/gos/harness_configs")
RECON = ROOT / "recon" / "protocol"
LUA = "luajit"

sys.path.insert(0, str(SERVER))


# ---------------------------------------------------------------------------
# config generation
# ---------------------------------------------------------------------------
def regen_configs() -> int:
    from gos import v2configs
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in (DECOMP / "configs").glob("settings.*.lua"):
        name = f.name[len("settings."):-len(".lua")]
        try:
            rows = v2configs.parse_lua_table_file(f)
            if isinstance(rows, dict):
                rows = list(rows.values())
            (CONFIG_DIR / f"{name}.json").write_text(
                json.dumps(rows, ensure_ascii=False, default=str))
            n += 1
        except Exception:
            pass
    return n


# ---------------------------------------------------------------------------
# action -> module index
# ---------------------------------------------------------------------------
_ACTION_RE = re.compile(r"\baction(\d{5})\s*=")

def build_action_index() -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for base in ("modules", "scene"):
        for p in sorted((DECOMP / base).rglob("*.lua")):
            try:
                s = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for a in _ACTION_RE.findall(s):
                idx.setdefault(a, p)
    return idx


def implemented_actions() -> set[str]:
    from pathlib import Path as P
    s = (SERVER / "gos" / "handlers.py").read_text()
    block = s.split("HANDLERS = {")[1].split("\n}")[0]
    return set(re.findall(r'"(\d{5})"\s*:', block))


# ---------------------------------------------------------------------------
# sample response from our server
# ---------------------------------------------------------------------------
def server_response(action: str) -> dict | None:
    """Call our handler on a fresh default player to get a sample response."""
    try:
        from gos import handlers
        from gos.defaults import default_player
        from gos.store import Player, Session, Store
        from pathlib import Path as P
        p = Player(uid="shape_probe", data=default_player("shape_probe", "Sultan"),
                   path=P("/tmp/shape_probe.json"))
        sess = Session(jwt="shape", user_id="shape_probe", sk="shape", keypair=None,
                       player_id="shape_probe", server_id=1, app_id=1020)
        store = Store()
        out = handlers.dispatch(action, {}, p, sess, store)
        if isinstance(out, dict):
            out = dict(out)
            out.pop("Sess", None)
            out.pop("SysTime", None)
            return out
    except Exception as exc:
        print(f"[warn] server_response({action}) failed: {exc!r}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# harness invocation
# ---------------------------------------------------------------------------
def run_harness(action: str, response: dict, module_file: Path,
                request_args: list | None = None) -> dict:
    with tempfile.TemporaryDirectory(prefix="shape_") as td:
        td = Path(td)
        rf = td / "response.json"
        rf.write_text(json.dumps(response, ensure_ascii=False))
        args_file = td / "args.json"
        args_file.write_text(json.dumps(request_args or []))
        modname = module_file.stem
        cmd = [LUA, str(HARNESS), str(module_file), action, str(rf), modname, str(args_file)]
        env = os.environ.copy()
        env["HARNESS_DEBUG"] = "0"
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20, env=env)
        except subprocess.TimeoutExpired:
            return {"action": action, "status": "TIMEOUT", "raw": ""}
        raw = (res.stdout + res.stderr).strip()
    out: dict = {"action": action, "raw": raw, "module": module_file.name}
    for line in raw.splitlines():
        if line.startswith("CALLBACK_OK"):
            out["status"] = "OK"
        elif line.startswith("CALLBACK_ERROR:"):
            out["status"] = "ERROR"
            out["error"] = line.split(":", 1)[1].strip()
        elif line.startswith("NIL_READS"):
            m = re.match(r"NIL_READS(?:\((\d+)\))?:\s*(.*)", line)
            if m:
                out["nil_count"] = int(m.group(1) or (0 if m.group(2) == "none" else 1))
                out["nil_reads"] = [] if m.group(2) == "none" else [x.strip() for x in m.group(2).split(",")]
        elif line.startswith("NO_REQUEST_FUNCTION"):
            out["status"] = "NO_REQ_FN"
        elif line.startswith("NO_CALLBACK_CAPTURED"):
            out["status"] = "NO_CALLBACK"
        elif line.startswith("MODULE_LOAD_FAIL"):
            out["status"] = "MODULE_LOAD_FAIL"
            out["error"] = line.split(":", 1)[1].strip()
    out.setdefault("status", "UNKNOWN")
    return out


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_action(action: str, response_file: str | None, request_args: list | None) -> None:
    idx = build_action_index()
    mod = idx.get(action)
    if not mod:
        print(f"action {action}: no decompiled module found")
        return
    if response_file:
        response = json.loads(Path(response_file).read_text())
    else:
        response = server_response(action) or {}
    print(f"action={action} module={mod.name}")
    print(f"response={json.dumps(response, ensure_ascii=False)[:300]}")
    result = run_harness(action, response, mod, request_args)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_all_implemented() -> None:
    if not list(CONFIG_DIR.glob("*.json")):
        regen_configs()
    idx = build_action_index()
    rows = []
    for action in sorted(implemented_actions()):
        mod = idx.get(action)
        if not mod:
            rows.append((action, "-", "NO_MODULE", "", ""))
            continue
        response = server_response(action) or {}
        r = run_harness(action, response, mod)
        rows.append((action, mod.name, r["status"], r.get("error", ""),
                     ", ".join(r.get("nil_reads", []))[:200]))
    RECON.mkdir(parents=True, exist_ok=True)
    out = RECON / "shape-report-implemented.md"
    lines = ["# Shape harness report — implemented actions", "",
             "Generated by `tools/lua_shape_harness/run.py --all-implemented`.", "",
             "| Action | Module | Status | Error | Nil reads |",
             "|---|---|---|---|---|"]
    for a, m, s, e, n in rows:
        lines.append(f"| {a} | {m} | {s} | {e[:120]} | {n} |")
    out.write_text("\n".join(lines) + "\n")
    counts: dict[str, int] = {}
    for _, _, s, _, _ in rows:
        counts[s] = counts.get(s, 0) + 1
    print(f"wrote {out} — {counts}")


def cmd_scan_missing() -> None:
    """For every client action, run the callback with an empty response to
    discover which fields it reads (required shape) — no golden capture needed."""
    if not list(CONFIG_DIR.glob("*.json")):
        regen_configs()
    idx = build_action_index()
    impl = implemented_actions()
    rows = []
    for action in sorted(idx):
        if action in impl:
            continue
        mod = idx[action]
        r = run_harness(action, {}, mod)
        if r["status"] in ("NO_REQ_FN", "MODULE_LOAD_FAIL"):
            continue
        rows.append((action, mod.name, r["status"], r.get("error", ""),
                     ", ".join(r.get("nil_reads", []))[:300]))
    RECON.mkdir(parents=True, exist_ok=True)
    out = RECON / "shape-required-fields.md"
    lines = ["# Required response fields discovered from client callbacks", "",
             "Generated by `tools/lua_shape_harness/run.py --scan-missing`.",
             "Each unimplemented action was run with an empty response; the",
             "`nil_reads` column lists exactly which fields the client callback",
             "accesses (i.e. the minimal response shape we must implement).", "",
             "| Action | Module | Status | Error | Fields read (nil) |",
             "|---|---|---|---|---|"]
    for a, m, s, e, n in rows:
        lines.append(f"| {a} | {m} | {s} | {e[:100]} | {n} |")
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out} — {len(rows)} actions scanned")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", help="single action id")
    ap.add_argument("--response", help="JSON response fixture for --action")
    ap.add_argument("--args", help="JSON array of request args for --action")
    ap.add_argument("--all-implemented", action="store_true")
    ap.add_argument("--scan-missing", action="store_true")
    ap.add_argument("--regen-configs", action="store_true")
    a = ap.parse_args()
    if a.regen_configs:
        print(f"regenerated {regen_configs()} config json files")
    if a.action:
        args = json.loads(a.args) if a.args else None
        cmd_action(a.action, a.response, args)
    if a.all_implemented:
        cmd_all_implemented()
    if a.scan_missing:
        cmd_scan_missing()


if __name__ == "__main__":
    main()
