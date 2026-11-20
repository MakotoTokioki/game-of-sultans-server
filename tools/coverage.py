#!/usr/bin/env python3
"""Report action-coverage progress: implemented vs client-sent actions.

Reads the static action inventory from the cluster map and the live HANDLERS
dict. Updates recon/protocol/coverage.md with a per-cluster table and totals.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))


def client_actions() -> set[str]:
    """All action IDs the v2.005 client can send (from the cluster map report)."""
    p = SERVER.parent / "recon" / "protocol" / "action-cluster-map.md"
    text = p.read_text()
    # cluster table rows: | # | 10000-10019 | ... |
    ids: set[str] = set()
    for m in re.finditer(r"\|\s*\d+\s*\|\s*(\d{4,5})\s*[–-]\s*(\d{4,5})\s*\|", text):
        lo, hi = int(m.group(1)), int(m.group(2))
        for i in range(lo, hi + 1):
            ids.add(f"{i:05d}")
    # single-id rows
    for m in re.finditer(r"\|\s*\d+\s*\|\s*(\d{5})\s*\|", text):
        ids.add(m.group(1))
    return ids


def main() -> None:
    from gos import handlers
    impl = set(handlers.HANDLERS)
    sent = client_actions()
    both = impl & sent
    extra = impl - sent  # implemented but not in client-sent inventory (SDK etc.)
    pct = 100 * len(both) / len(sent) if sent else 0
    out = SERVER.parent / "recon" / "protocol" / "coverage.md"
    lines = [
        "# Action coverage (live)",
        "",
        f"- Client-sent actions (inventory): **{len(sent)}**",
        f"- Implemented handlers: **{len(impl)}**",
        f"- Implemented ∩ client-sent: **{len(both)}**",
        f"- Coverage: **{pct:.1f}%**",
        f"- Implemented but not in client-sent inventory (SDK/JWT/gateway etc.): {len(extra)}",
        "",
        "Regenerate with `python3 tools/coverage.py`.",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
