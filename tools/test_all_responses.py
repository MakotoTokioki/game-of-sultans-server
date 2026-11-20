#!/usr/bin/env python3
"""Test EVERY server response against the real decompiled v2.005 client callback.

For each implemented action id:
  1. Call our handler with an enriched player (high level, resources, unlocked
     state) so it returns a REAL response instead of a `_fail` stub.
  2. Run that response through the real client response callback in the LuaJIT
     sandbox (tools/lua_shape_harness/harness.lua) with strict nil-read logging.
  3. Classify: OK / ERROR (client callback threw) / NIL_READS / harness-gap.

Output: recon/protocol/client-response-test.md

Usage: python3 tools/test_all_responses.py [--only NNNNN] [--verbose]
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

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
HARNESS_DIR = SERVER / "tools" / "lua_shape_harness"
HARNESS = HARNESS_DIR / "harness.lua"
RECON = SERVER.parent / "recon" / "protocol"


def rich_player():
    from gos import handlers
    from gos.defaults import default_player
    from gos.store import Player
    p = Player(uid="responsetest", data=default_player("responsetest", "Sultan"),
               path=Path("/tmp/responsetest.json"))
    d = p.data
    rd = d.setdefault("RoleData", {})
    rd.update({"Lv": 80, "VipLv": 10, "Exp": 9999999, "Power": 99999999,
               "Silver": 99999999, "Food": 99999999, "Soldier": 9999999,
               "Gold": 9999999, "BossScore": 999999})
    rb = d.setdefault("RoleBase", {})
    rb.update({"Lv": 80, "VipLv": 10, "Power": 99999999})
    # seed a few wives/counsellors so harem/wife handlers have rows
    try:
        row = handlers._beauty_row(1)
        d.setdefault("RoleBeauty", {})["1"] = row
    except Exception:
        pass
    return p


def load_payload_map() -> dict[str, dict]:
    """Real request payloads recorded by the device (logs/actions.jsonl)."""
    import json as _json
    m: dict[str, dict] = {}
    p = SERVER / "logs" / "actions.jsonl"
    if not p.exists():
        return m
    skip = {"Sess", "appId", "ActId", "Ver", "serverId", "RetailId", "T", "Lang",
            "actionid", "actionId", "Token", "HotVersion"}
    for line in p.open():
        try:
            o = _json.loads(line)
        except Exception:
            continue
        a = str(o.get("action") or o.get("ActId") or "")
        raw = o.get("payload")
        if not a or not isinstance(raw, dict):
            continue
        q = {k: v for k, v in raw.items() if k not in skip}
        if q and a not in m:
            m[a] = q
    return m


PAYLOAD_MAP = None

def get_response(action, player, payload=None):
    from gos import handlers
    from types import SimpleNamespace
    store = SimpleNamespace(save=lambda p: None)
    sess = None
    try:
        out = handlers.dispatch(action, payload or {}, player, sess, store)
    except Exception as exc:
        return None, f"handler-exception: {exc!r}"
    if not isinstance(out, dict):
        return None, "non-dict response"
    out = dict(out)
    # a `_fail` response is the server telling the client to show a failure —
    # the response parser isn't exercised by it; record it separately.
    if "_fail" in out:
        return out, "FAIL_STUB"
    for k in ("Sess", "SysTime", "Code", "Info"):
        out.pop(k, None)
    return out, None


def find_module(action):
    sys.path.insert(0, str(HARNESS_DIR))
    from run import build_action_index
    return build_action_index().get(action)


def run_client(action, response, module):
    with tempfile.TemporaryDirectory(prefix="resp_") as td:
        td = Path(td)
        rf = td / "response.json"; rf.write_text(json.dumps(response, ensure_ascii=False))
        af = td / "args.json"; af.write_text("[]")
        cmd = ["luajit", str(HARNESS), str(module), action, str(rf), module.stem, str(af)]
        env = os.environ.copy(); env["HARNESS_DEBUG"] = "0"
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=25, env=env)
        except subprocess.TimeoutExpired:
            return {"status": "TIMEOUT", "raw": ""}
        raw = (res.stdout + res.stderr).strip()
    result = {"status": "UNKNOWN", "raw": raw}
    for line in raw.splitlines():
        if line.startswith("CALLBACK_OK"):
            result["status"] = "OK"
        elif line.startswith("CALLBACK_ERROR:"):
            result["status"] = "ERROR"
            result["error"] = line.split(":", 1)[1].strip()
        elif line.startswith("NIL_READS"):
            m = re.match(r"NIL_READS(?:\((\d+)\))?:\s*(.*)", line)
            if m:
                result["nil_count"] = int(m.group(1) or 0)
                result["nil"] = "" if m.group(2) == "none" else m.group(2)
        elif line.startswith(("NO_CALLBACK", "NO_REQ_FN", "MODULE_LOAD_FAIL", "NO_MODULE")):
            result["status"] = line.split(":", 1)[0]
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="test a single action id")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    from gos import handlers
    ids = [a.only] if a.only else sorted(k for k in handlers.HANDLERS if k.isdigit())

    global PAYLOAD_MAP
    PAYLOAD_MAP = load_payload_map()
    rows = []
    player = rich_player()
    for action in ids:
        payload = PAYLOAD_MAP.get(action)
        response, herr = get_response(action, player, payload)
        if herr == "FAIL_STUB":
            rows.append((action, "-", "FAIL_STUB", "", "", str(response)[:120]))
            continue
        if herr:
            rows.append((action, "-", "HANDLER_FAIL", herr, "", ""))
            continue
        module = find_module(action)
        if not module:
            rows.append((action, "-", "NO_MODULE", "", "", ""))
            continue
        r = run_client(action, response, module)
        rows.append((action, module.name, r["status"], r.get("error", ""),
                     r.get("nil", ""), json.dumps(response, ensure_ascii=False)[:120]))

    RECON.mkdir(parents=True, exist_ok=True)
    out = RECON / "client-response-test.md"
    lines = [
        "# Client response test — every action against the real Lua callback",
        "",
        "Generated by `python3 tools/test_all_responses.py`. Each implemented action's",
        "server response (enriched player) is fed to the real decompiled v2.005 client",
        "response callback under LuaJIT with strict nil-read logging. `ERROR` means the",
        "client callback threw; harness-gap statuses (NO_CALLBACK/NO_REQ_FN/etc.) are",
        "coverage limitations of the sandbox, not client failures.",
        "",
        "| Action | Module | Status | Error / Nil reads | Sample response |",
        "|---|---|---|---|---|",
    ]
    for action, mod, status, err, nil, sample in rows:
        detail = (err or "") + (" | NIL:" + nil if nil else "")
        lines.append(f"| {action} | {mod} | {status} | {detail[:160]} | {sample} |")
    out.write_text("\n".join(lines) + "\n")

    from collections import Counter
    counts = Counter(r[2] for r in rows)
    print(f"Tested {len(rows)} actions -> {out}")
    for k, v in counts.most_common():
        print(f"  {k}: {v}")
    if a.verbose:
        for action, mod, status, err, nil, sample in rows:
            if status == "ERROR":
                print(f"  ERROR {action} ({mod}): {err} | NIL:{nil}")


if __name__ == "__main__":
    main()
