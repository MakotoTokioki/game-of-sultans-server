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
    """All action IDs the v2.005 client can send.

    Use the same static index as the shape harness (actual actionNNNNN = ...
    definitions across the decompiled tree).  The cluster-map ranges are labels
    (e.g. 10301-10391 is not 91 contiguous actions) and inflate the count.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent / "lua_shape_harness"))
    from run import build_action_index
    return set(build_action_index())


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
