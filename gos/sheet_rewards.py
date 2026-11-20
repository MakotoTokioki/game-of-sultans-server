"""Parse reward tokens from the community spreadsheet into v2 item IDs.

The sheet (recon/community-research/spreadsheet) uses Turkish item names like
"Altın Kuponu (3 adet)", "Rastgele Kitap +1000 (+1)", "Paye Parçası (2 adet)".
Item names map to original v2 TcItem IDs via Tctr.lua (saved in item-names.json).
Tokens whose item is NOT in v2 TcItem (event-only flowers/food) are skipped —
they cannot be rendered by the v2 client, consistent with our ground rule.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_NAMES_PATH = Path(__file__).parent.parent.parent / "recon" / "community-research" / "spreadsheet" / "item-names.json"
_NAMES = json.loads(_NAMES_PATH.read_text(encoding="utf-8"))["tr"]
# exact normalized name -> id
_BY_NAME = {n.strip().lower(): int(i) for i, n in _NAMES.items() if n.strip()}


def parse_token(token: str) -> tuple[int, int] | None:
    """Return (ItemId, Num) or None if the item isn't in v2 TcItem."""
    s = token.strip()
    if not s or s in ("-", "✓"):
        return None
    # base resources: "100.000 Altın" / "1.300 Yeniçeri"
    m = re.match(r"^([\d.,]+)\s+(Altın|Yeniçeri|Tahıl|Elmas|Tecrübe)$", s)
    if m:
        num = int(m.group(1).replace(".", "").replace(",", ""))
        name = m.group(2)
        iid = _BY_NAME.get(name.lower())
        return (iid, num) if iid else None
    # "Name (N adet)" or "Name (+N)" or "Name (N)"
    m = re.match(r"^(.+?)\s*\((\+?\d+)\s*(?:adet)?\)$", s)
    if m:
        name = m.group(1).strip()
        # strip a trailing "+NNN" variant qualifier (e.g. "Rastgele Kitap +1000")
        name = re.sub(r"\s*\+\d+$", "", name).strip()
        num = int(m.group(2).lstrip("+"))
        return (_BY_NAME.get(name.lower()), num) if _BY_NAME.get(name.lower()) else None
    # "Name +500" with no count, "Name"
    name = re.sub(r"\s*\+\d+$", "", s).strip()
    iid = _BY_NAME.get(name.lower())
    return (iid, 1) if iid else None


def parse_rewards(tokens: list[str]) -> list[dict]:
    out: list[dict] = []
    for t in tokens:
        parsed = parse_token(t)
        if parsed and parsed[0] > 0 and parsed[1] > 0:
            out.append({"ItemId": parsed[0], "Num": parsed[1]})
    return out


if __name__ == "__main__":
    for t in ["Altın Kuponu (3 adet)", "Rastgele Kitap +1000 (+1)", "Paye Parçası (2 adet)",
              "Gül (5 adet)", "100.000 Altın", "Kıymetli Altın Kuponu (+1)",
              "Gelişim Kesesi (+1)", "Menekşe (5 adet)"]:
        print(t, "->", parse_token(t))
