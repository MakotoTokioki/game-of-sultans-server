"""Regression tests for the store/shop cluster (11102-11105).

Response shapes come from tools/lua_shape_harness/extract_fields.py and the
decompiled client callbacks; all costs come from original v2 configs.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import v2configs
from gos.defaults import default_player
from gos.handlers_store import (
    STORE_HANDLERS,
    action_cash_gift_buy,
    action_skin_buy,
    action_store_buy,
    action_store_buy_records,
)
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="storetest", data=default_player("storetest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def _stub():
    return SimpleNamespace(save=lambda p: None), None


def main() -> None:
    store, session = _stub()

    check("STORE_HANDLERS registers 11102-11105",
          set(STORE_HANDLERS) == {"11102", "11103", "11104", "11105"},
          list(STORE_HANDLERS))

    # ---- 11102 netStoreBuy: client reads Num + RemainList -----------------
    p = _player()
    before_gold = p.data["RoleData"].get("Gold")
    d = action_store_buy({"ActivityId": -1, "StoreItemId": 1}, p, session, store)
    check("11102 response has Num (int) and RemainList (list)",
          isinstance(d.get("Num"), int) and isinstance(d.get("RemainList"), list), d)
    check("11102 first purchase Num == 1", d["Num"] == 1, d)
    check("11102 does not invent a currency delta",
          p.data["RoleData"].get("Gold") == before_gold,
          (before_gold, p.data["RoleData"].get("Gold")))
    d2 = action_store_buy({"ActivityId": -1, "StoreItemId": 1}, p, session, store)
    check("11102 second purchase increments Num", d2["Num"] == 2, d2)

    # ---- 11103 action11103: RoleBuyRecord list ---------------------------
    recs = action_store_buy_records({"ActivityId": 7}, p, session, store).get("RoleBuyRecord")
    check("11103 RoleBuyRecord is a Lua-safe list of dict rows",
          isinstance(recs, list) and len(recs) >= 1
          and all(isinstance(r, dict) for r in recs), recs)
    check("11103 rows carry Activity/StoreItemId/Num as real ints",
          all(all(isinstance(r.get(k), int)
                  for k in ("Activity", "StoreItemId", "Num")) for r in recs), recs)
    check("11103 reflects the 11102 purchase",
          any(r["Activity"] == -1 and r["StoreItemId"] == 1 and r["Num"] == 2
              for r in recs), recs)

    # ---- 11104 action11104: RoleBuyRecord + HasChance -------------------
    d = action_cash_gift_buy({"ActivityId": 7, "ItemId": 42}, p, session, store)
    check("11104 response has RoleBuyRecord + bool HasChance",
          isinstance(d.get("RoleBuyRecord"), list)
          and isinstance(d.get("HasChance"), bool), d)
    check("11104 echoes persisted records", len(d["RoleBuyRecord"]) == len(recs), d)

    # ---- 11105 action11105: config-driven skin purchase -----------------
    skin_rows = {int(r.get("ItemId") or 0): r
                 for r in v2configs.load("TcSkinStore") if isinstance(r, dict)}
    check("TcSkinStore bundled + parseable", len(skin_rows) >= 4, list(skin_rows))
    skin_id = 1601
    price_pair = skin_rows[skin_id]["Price"][0]
    currency_id, amount = int(price_pair[0]), int(price_pair[1])

    p = _player()
    p.data["RoleItem"] = [{"ItemId": currency_id, "Num": 100}]
    d = action_skin_buy({"SkinId": skin_id}, p, session, store)
    counts = {int(e["ItemId"]): int(e["Num"]) for e in p.data["RoleItem"]}
    check("11105 consumes TcSkinStore Price currency",
          counts.get(currency_id) == 100 - amount, (counts, currency_id, amount))
    check("11105 grants the skin item", counts.get(skin_id) == 1, counts)
    check("11105 RemainList ships changed bag items",
          isinstance(d.get("RemainList"), list)
          and {int(e["ItemId"]) for e in d["RemainList"]} >= {currency_id, skin_id}, d)
    check("11105 response is Lua-safe with SkinId echo",
          isinstance(d.get("SkinId"), int), d)

    # Second skin uses the other configured price row (587/60).
    skin_id2 = 1702
    pair2 = skin_rows[skin_id2]["Price"][0]
    cur2, amt2 = int(pair2[0]), int(pair2[1])
    p.data["RoleItem"] = [{"ItemId": cur2, "Num": 200}]
    action_skin_buy({"SkinId": skin_id2}, p, session, store)
    counts = {int(e["ItemId"]): int(e["Num"]) for e in p.data["RoleItem"]}
    check("11105 uses each row's own Price from config",
          counts.get(cur2) == 200 - amt2 and counts.get(skin_id2) == 1,
          (counts, cur2, amt2))

    # Insufficient currency -> no invented grant.
    p = _player()
    d = action_skin_buy({"SkinId": skin_id}, p, session, store)
    check("11105 without currency grants nothing but stays Lua-safe",
          isinstance(d, dict) and isinstance(d.get("RemainList"), list)
          and not any(int(e["ItemId"]) == skin_id for e in d["RemainList"]), d)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
