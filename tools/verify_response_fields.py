#!/usr/bin/env python3
"""Verify every server response contains the fields its client callback reads.

Two complementary checks (no device needed):
  1. STATIC: extract_fields.py lists every field the real decompiled callback
     reads from the response parameter. Check each is present in our response
     (or is an intentionally-omitted guarded-optional field).
  2. RUNTIME: feed the response to the Lua callback (harness) — already done by
     test_all_responses.py; this file focuses on the deterministic static check.

This is the reliable signal: a missing required field is what makes the client
callback throw. Output: recon/protocol/response-field-verification.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
sys.path.insert(0, str(SERVER / "tools" / "lua_shape_harness"))

from test_all_responses import rich_player, get_response, load_payload_map
from extract_fields import extract
from run import build_action_index


def main() -> None:
    from gos import handlers
    payloads = load_payload_map()
    ids = sorted(k for k in handlers.HANDLERS if k.isdigit())
    idx = build_action_index()
    player = rich_player()

    rows = []
    for action in ids:
        resp, herr = get_response(action, player, payloads.get(action))
        if herr == "FAIL_STUB":
            rows.append((action, "FAIL_STUB", "", "", ""))
            continue
        if herr or not isinstance(resp, dict):
            rows.append((action, "HANDLER_FAIL", herr or "", "", ""))
            continue
        info = extract(action)
        fields = info.get("fields", [])
        missing = [f for f in fields if f not in resp]
        rows.append((action, "OK" if not missing else "MISSING",
                     ",".join(fields), ",".join(missing),
                     json.dumps(resp, ensure_ascii=False)[:80]))

    out = SERVER.parent / "recon" / "protocol" / "response-field-verification.md"
    lines = [
        "# Response field verification (all actions)",
        "",
        "For each implemented action, every field its real client callback reads",
        "(statically extracted from the decompiled Lua) is checked against the",
        "server response. `MISSING` = a required field the response doesn't provide",
        "(the main cause of client callback errors). `FAIL_STUB` = the handler",
        "correctly returned a failure (parser not exercised).",
        "",
        "| Action | Status | Fields read | Missing | Sample |",
        "|---|---|---|---|---|",
    ]
    for action, status, fields, missing, sample in rows:
        lines.append(f"| {action} | {status} | {fields} | {missing} | {sample} |")
    out.write_text("\n".join(lines) + "\n")

    from collections import Counter
    c = Counter(r[1] for r in rows)
    print(f"Verified {len(rows)} actions -> {out}")
    for k, v in c.most_common():
        print(f"  {k}: {v}")
    print("\nMISSING-field actions:")
    for action, status, fields, missing, sample in rows:
        if status == "MISSING":
            print(f"  {action}: missing [{missing}] (reads: {fields})")


if __name__ == "__main__":
    main()
