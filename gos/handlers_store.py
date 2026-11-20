"""Store / shop handlers for the Game of Sultans v2.005 cluster 11101-11105.

11101 (store_items) stays in :mod:`gos.handlers`; this module holds the four
missing actions.  Keep every response shape in sync with the decompiled client
callbacks in ``_storeMod.lua`` / ``scene/storeScene.*.lua``.

Ground rules (see tools/HANDLER_SPEC.md):
  * no invented numbers -- costs/prices come from original v2 config tables or
    are echoed from the request;
  * Lua-safe shapes -- lists for arrays, dicts for maps, real ints/bools;
  * missing state is created lazily with ``player.data.setdefault``.

Server-authoritative gap
------------------------
The normal/paid store product catalogue (Price / ItemList / Limit / IsCash)
is NOT an original v2 config file: the client receives it in the 11101 response
as ``Data.StoreItemCfg`` (see the v7 capture).  There is therefore no local,
sourceable cost/reward for actions 11102 and 11104.  They only maintain the
client's ``RoleBuyRecord`` bookkeeping and never fabricate a currency delta.
The skin shop, by contrast, is fully config-driven by ``settings.TcSkinStore``.
"""
from __future__ import annotations

from . import v2configs
from .handlers import (
    _REMAIN_LIST_MAP,
    _as_int,
    _grant_award_pairs,
    _now,
    _role_item_consume,
    _role_item_count,
    attach_remain_list,
)


# ---------------------------------------------------------------------------
# RoleBuyRecord helpers
# ---------------------------------------------------------------------------
# _storeMod.lua L2_1 is a map keyed by "<Activity>_<StoreItemId>" holding a
# purchase count.  The wire shape used by 11101/11103/11104 is a LIST of
# {Activity, StoreItemId, Num} rows (the client iterates pairs() over it and
# calls setRoleBuyRecord(Activity, StoreItemId, Num)).  We persist that list
# directly so the response is a faithful copy of server state.
def _store_records(player) -> list:
    records = player.data.setdefault("StoreBuyRecord", [])
    if not isinstance(records, list):
        records = []
        player.data["StoreBuyRecord"] = records
    # Repair any row that was persisted with a wrong shape.
    clean = []
    for row in records:
        if isinstance(row, dict):
            clean.append({
                "Activity": _as_int(row.get("Activity")),
                "StoreItemId": _as_int(row.get("StoreItemId")),
                "Num": _as_int(row.get("Num")),
            })
    player.data["StoreBuyRecord"] = clean
    return clean


def _record_num(records: list, activity: int, store_item_id: int) -> int:
    for row in records:
        if (_as_int(row.get("Activity")) == activity
                and _as_int(row.get("StoreItemId")) == store_item_id):
            return _as_int(row.get("Num"))
    return 0


def _record_bump(records: list, activity: int, store_item_id: int, amount: int = 1) -> int:
    num = _record_num(records, activity, store_item_id) + int(amount)
    for row in records:
        if (_as_int(row.get("Activity")) == activity
                and _as_int(row.get("StoreItemId")) == store_item_id):
            row["Num"] = num
            return num
    records.append({"Activity": activity, "StoreItemId": store_item_id, "Num": num})
    return num


# ---------------------------------------------------------------------------
# 11102 - netStoreBuy(ActivityId, StoreItemId, callback)
# ---------------------------------------------------------------------------
def action_store_buy(payload: dict, player, session, store) -> dict:
    """_storeMod action11102: buy a normal/gift store product.

    Request  : {"ActivityId": int, "StoreItemId": int}
    Response : {"Num": <new RoleBuyRecord count>,
                "RemainList": [{"ItemId": int, "Num": int}, ...]}

    Both fields are read by the client: storeScene.storeScene.lua ~274 reads
    ``A0_5.Num`` and stores it with setRoleBuyRecord(); the _storeMod callback
    pairs() over ``A0_3.RemainList`` (~529) for item-533/534 red points.

    The product definition (Price/ItemList/Limit) is server-authoritative
    (11101 Data.StoreItemCfg) and is not a bundled v2 config, so no cost is
    charged and no item is granted here -- inventing one would break the
    "never invent numbers" rule.  The purchase count is still persisted.
    """
    activity = _as_int(payload.get("ActivityId"))
    store_item_id = _as_int(payload.get("StoreItemId"))
    records = _store_records(player)
    num = _record_bump(records, activity, store_item_id)
    store.save(player)
    # resources=[] -> an explicit empty (Lua-safe) list, no bogus +0 rows.
    return attach_remain_list({"Num": num}, player, resources=[])


# ---------------------------------------------------------------------------
# 11103 - action11103() : cash-gift purchase-record query
# ---------------------------------------------------------------------------
def action_store_buy_records(payload: dict, player, session, store) -> dict:
    """_storeMod action11103: refresh the cash-gift RoleBuyRecord map.

    Request  : {"ActivityId": int}  (the cash-gift activity id; unused here)
    Response : {"RoleBuyRecord": [{"Activity": int, "StoreItemId": int,
                                   "Num": int}, ...]}

    The callback (_storeMod.lua ~593) iterates the list and calls
    setRoleBuyRecord(Activity, StoreItemId, Num) for every row.
    """
    return {"RoleBuyRecord": [dict(row) for row in _store_records(player)]}


# ---------------------------------------------------------------------------
# 11104 - action11104(ItemId) : cash-gift purchase eligibility check
# ---------------------------------------------------------------------------
def action_cash_gift_buy(payload: dict, player, session, store) -> dict:
    """_storeMod action11104: pre-purchase check for a cash gift (IAP).

    Request  : {"ActivityId": int, "ItemId": int}
    Response : {"RoleBuyRecord": [...], "HasChance": bool}

    storeScene.cashGift.lua ~553 calls this before rechargeMod.pay(); the
    callback (_storeMod.lua ~633) refreshes RoleBuyRecord and only starts the
    payment when ``A0_3.HasChance`` is truthy.  The per-product purchase Limit
    is part of the server's StoreItemCfg (not a local v2 config), so it cannot
    be sourced offline; HasChance is always True and the current record list
    is echoed.  No product is granted here (payment is server-authoritative).
    """
    # ActivityId / ItemId are echoed only through the record lookup; the
    # client does not read them back from the response.
    _activity = _as_int(payload.get("ActivityId"))
    _item_id = _as_int(payload.get("ItemId") or payload.get("StoreItemId"))
    records = _store_records(player)
    return {
        "RoleBuyRecord": [dict(row) for row in records],
        "HasChance": True,
    }


# ---------------------------------------------------------------------------
# 11105 - action11105(skinId) : skin-store purchase
# ---------------------------------------------------------------------------
def _skin_price_row(skin_id: int) -> dict:
    """settings.TcSkinStore row whose ItemId is this skin (client skinId)."""
    for row in v2configs.load("TcSkinStore"):
        if isinstance(row, dict) and _as_int(row.get("ItemId")) == skin_id:
            return row
    return {}


def _spend(player, item_id: int, num: int) -> bool:
    """Consume a config-defined cost; False when the player cannot afford it."""
    num = _as_int(num)
    if num <= 0:
        return True
    item_id = _as_int(item_id)
    if item_id in _REMAIN_LIST_MAP:
        key = _REMAIN_LIST_MAP[item_id]
        role_data = player.data.setdefault("RoleData", {})
        have = _as_int(role_data.get(key))
        if have < num:
            return False
        role_data[key] = have - num
        return True
    if _role_item_count(player, item_id) < num:
        return False
    _role_item_consume(player, item_id, num)
    return True


def action_skin_buy(payload: dict, player, session, store) -> dict:
    """_storeMod action11105 -> skinStoreLogic.buySkin(): buy a skin.

    Request  : {"SkinId": int}  (the skin item id, e.g. 1601/1701)
    Response : {} -- the client callback only calls the listener and reads
               no field from the response, so a Lua-safe table is enough.
               ``SkinId``/``RemainList`` are included for parity/debugging.

    Cost comes from the original ``settings.TcSkinStore.lua``:
    Price = [[586, 40]], [[587, 40]], [[587, 60]] -- (currency item id, num).
    The granted skin item id is the row's ItemId (TcCounsellorSkin /
    TcBeautySkin use the same ids).  No cost/reward is invented.
    """
    skin_id = _as_int(payload.get("SkinId") or payload.get("skinId"))
    row = _skin_price_row(skin_id)
    prices = row.get("Price") if isinstance(row, dict) else None
    changed_items: list[int] = []
    changed_resources: list[int] = []
    if prices and isinstance(prices, (list, tuple)):
        pair = prices[0]
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            currency_id = _as_int(pair[0])
            amount = _as_int(pair[1])
            if _spend(player, currency_id, amount):
                _, bag_ids = _grant_award_pairs(player, [[skin_id, 1]])
                changed_items.extend(bag_ids)
                if currency_id in _REMAIN_LIST_MAP:
                    changed_resources.append(currency_id)
                else:
                    changed_items.append(currency_id)
                owned = player.data.setdefault("OwnedStoreSkins", {})
                owned[str(skin_id)] = 1
                store.save(player)
    return attach_remain_list(
        {"SkinId": skin_id, "SysTime": _now()},
        player, items=changed_items, resources=changed_resources,
    )


STORE_HANDLERS = {
    "11102": action_store_buy,
    "11103": action_store_buy_records,
    "11104": action_cash_gift_buy,
    "11105": action_skin_buy,
}
