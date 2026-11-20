"""Build v2 time-limited AwardCfg rows from the community spreadsheet.

The sheet's 20_events tab has, for each of the 4 Devir cycles, a
"Sınırlı Süreli Ödüller / Elmas Harca" 32-tier spend→reward table. Our v2
client's time-limited events (11901 AwardCfg, read by _limitTimeMod) show 9
activity slots; we map activity slots 1..4 to the 4 cycles' Elmas Harca tables.

Rewards are parsed to original v2 TcItem IDs via sheet_rewards; event-only
items (flowers/food absent from v2 TcItem) are skipped. RequireNum = the
diamond spend threshold from the sheet; IsShow=1 keeps rows visible.
"""
from __future__ import annotations

import csv
from pathlib import Path

from . import sheet_rewards

_CSV = Path(__file__).parent.parent.parent / "recon" / "community-research" / "spreadsheet" / "tabs" / "20_events.csv"


def _rows() -> list[list[str]]:
    return list(csv.reader(_CSV.open(encoding="utf-8")))


def elmas_harca_table(cycle: int) -> list[dict]:
    """32-tier Elmas Harca (spend-diamonds) table for a Devir cycle (1-4)."""
    rows = _rows()
    # task name col, threshold col, reward cols — per cycle from header row 3
    cols = {1: (5, 7, range(8, 13)),
            2: (20, 22, range(23, 28)),
            3: (30, 32, range(33, 38)),
            4: (39, 41, range(42, 47))}
    name_c, thr_c, rew_c = cols.get(cycle, cols[1])
    out: list[dict] = []
    for i in range(6, 40):
        r = rows[i]
        thr = r[thr_c].strip() if len(r) > thr_c else ""
        if not thr or thr == "-":
            continue
        try:
            num = int(thr.replace(".", ""))
        except ValueError:
            continue
        toks = [r[j].strip() for j in rew_c if j < len(r) and r[j].strip() and r[j].strip() != "-"]
        awards = sheet_rewards.parse_rewards(toks)
        if not awards:
            continue
        out.append({"IsShow": 1, "RequireNum": num, "Award": awards})
    return out


def activity_awards() -> dict[int, list[dict]]:
    """Map activity slots 1..4 to cycle Elmas Harca tables."""
    return {cycle: elmas_harca_table(cycle) for cycle in range(1, 5)}


if __name__ == "__main__":
    for cycle in range(1, 5):
        t = elmas_harca_table(cycle)
        print(f"Devir {cycle}: {len(t)} tiers, first={t[0] if t else None}")
