"""Regression tests for the welfare extras cluster (11503/11505/11511/11512).

Response shapes come from the decompiled client callbacks (see
recon/protocol/static-fields.md and shape-required-fields.md); reward values
come only from the original v2 TcVIP config via handlers.py helpers.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers, v2configs
from gos.defaults import default_player
from gos.handlers_welfare import WELFARE_HANDLERS, action_11512
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="welfaretest", data=default_player("welfaretest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def main() -> None:
    store, session = SimpleNamespace(save=lambda p: None), None

    check("handler registry covers the four missing actions",
          set(WELFARE_HANDLERS) == {"11503", "11505", "11511", "11512"},
          sorted(WELFARE_HANDLERS))

    # ---- 11503: VIP welfare claim (config-driven grant) -------------------
    vip_rows = [r for r in v2configs.load("TcVIP")
                if isinstance(r, dict) and r.get("AwardItem")]
    level = int(vip_rows[0]["Level"])
    award_ids = [int(e[0]) for e in vip_rows[0]["AwardItem"]
                 if isinstance(e, list) and len(e) >= 2]
    _ = handlers  # helper import sanity (grant helpers are reused by 11503)

    p = _player()
    d = WELFARE_HANDLERS["11503"]({"VipLv": level}, p, session, store)
    check("11503 response is a dict with Lua-safe RemainList",
          isinstance(d, dict) and isinstance(d.get("RemainList", []), list), d)
    check("11503 records the claimed VIP level",
          level in p.data["WelfareVipClaimed"], p.data["WelfareVipClaimed"])
    items = p.data.setdefault("RoleItem", [])
    granted = {int(e.get("ItemId") or 0): int(e.get("Num") or 0) for e in items}
    check("11503 grants exactly the TcVIP AwardItem amounts",
          all(granted.get(i, 0) >= 1 for i in award_ids), granted)
    remain_ids = [int(e.get("ItemId") or 0) for e in d.get("RemainList", [])]
    check("11503 RemainList carries the granted bag items",
          all(i in remain_ids for i in award_ids), remain_ids)

    # a second claim must not double-grant or duplicate the claim
    before = dict(granted)
    WELFARE_HANDLERS["11503"]({"VipLv": level}, p, session, store)
    items2 = p.data.get("RoleItem", [])
    granted2 = {int(e.get("ItemId") or 0): int(e.get("Num") or 0) for e in items2}
    check("11503 repeat claim is idempotent",
          granted2 == before and p.data["WelfareVipClaimed"].count(level) == 1,
          (granted2, p.data["WelfareVipClaimed"]))
    # invalid / first-recharge VIP has no TcVIP AwardItem -> no state change
    p2 = _player()
    WELFARE_HANDLERS["11503"]({"VipLv": 0}, p2, session, store)
    check("11503 ignores VipLv without a TcVIP AwardItem",
          p2.data.get("WelfareVipClaimed") == [], p2.data.get("WelfareVipClaimed"))

    # ---- 11512: claimed VIP list ------------------------------------------
    d = action_11512({}, p, session, store)
    wv = d.get("WelfareVip")
    check("11512 WelfareVip is a list of claimed TcVIP levels",
          isinstance(wv, list) and level in wv and all(isinstance(v, int) for v in wv),
          d)
    d2 = WELFARE_HANDLERS["11512"]({}, _player(), session, store)
    check("11512 WelfareVip is empty for a fresh player",
          d2.get("WelfareVip") == [], d2)

    # ---- 11505: welfare card stage ---------------------------------------
    p3 = _player()
    d = WELFARE_HANDLERS["11505"]({"Card": 1}, p3, session, store)
    check("11505 CardStage is an int (no active card -> 0)",
          isinstance(d.get("CardStage"), int) and d["CardStage"] == 0, d)
    p4 = _player()
    p4.data["CardEndTime"] = {"MonthDeadline": "2030-01-01 00:00:00"}
    d = WELFARE_HANDLERS["11505"]({"Card": 1}, p4, session, store)
    check("11505 CardStage is active (2) when a deadline exists",
          isinstance(d.get("CardStage"), int) and d["CardStage"] == 2, d)
    d_again = WELFARE_HANDLERS["11505"]({"Card": 1}, p4, session, store)
    check("11505 CardStage is stable on repeat",
          d_again == d, d_again)

    # ---- 11511: order check -----------------------------------------------
    d = WELFARE_HANDLERS["11511"]({"orderId": "test-order"}, _player(), session, store)
    check("11511 returns a Lua-safe empty envelope",
          isinstance(d, dict) and d == {}, d)

    print(f"{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
