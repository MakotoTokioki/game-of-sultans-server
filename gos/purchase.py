"""Simulated real-money purchases for the private server.

There is no payment processor here, so "buying" means granting the items the
official store would deliver, using ORIGINAL config tables only:

  - Diamonds per tier: from the community spreadsheet VIP tab
    (recon/community-research/spreadsheet/tabs/27_vip.csv, columns "Hediye Elmas")
    and the original TcRechargeConfig product/Money rows.
  - VIP level: from TcVIP.VipRechargeRequire thresholds (USD amounts).
  - VIP level-up awards: TcVIP[level].AwardItem (original v2 config).
  - Shop packs: original TcItem pack ids + the sheet's Market tab contents.

No real money is charged and no order is sent to any payment gateway. This is
the correct behaviour for a private server: the player can obtain the premium
items by calling the dev endpoint (or the in-game flow) without a real payment.
"""
from __future__ import annotations

from . import v2configs

# Diamond amounts per purchase tier (community sheet, "Hediye Elmas" column).
# Index 1..N; the original TcRechargeConfig products map to tiers 1-4.
_TIER_DIAMONDS = {
    1: 50, 2: 250, 3: 500, 4: 1000, 5: 2500,
    6: 5000, 7: 10000, 8: 20000, 9: 50000, 10: 100000,
}

# TcRechargeConfig: ProductId -> (Money USD, Name). Original config.
def recharge_products() -> list[dict]:
    return [dict(r) for r in v2configs.load("TcRechargeConfig")]


# Exact product prices from the in-game Recharge screen (appConfig APPIAPLIST +
# the community sheet). These are the amounts the client sends in trans_center.
_PRODUCT_USD = {
    0.99: 50, 4.99: 250, 9.99: 500, 19.99: 1000,
    49.99: 2500, 99.99: 5000,
}


def diamonds_for_usd(usd: float) -> int:
    """Diamonds for a product price, from the in-game Recharge catalogue."""
    amt = abs(float(usd))
    if amt in _PRODUCT_USD:
        return _PRODUCT_USD[amt]
    # nearest known price for safety
    return _PRODUCT_USD.get(min(_PRODUCT_USD, key=lambda k: abs(k - amt)), 50)


def tier_for_usd(usd: float) -> int:
    """Map a USD amount to the tier whose diamond count matches the product."""
    d = diamonds_for_usd(usd)
    for t, count in _TIER_DIAMONDS.items():
        if count == d:
            return t
    return 1


def diamonds_for_tier(tier: int) -> int:
    return _TIER_DIAMONDS.get(int(tier), 0)


def vip_level_for_recharge(total_usd: float) -> int:
    """Highest VIP level whose VipRechargeRequire <= total recharge (original)."""
    level = 0
    for row in v2configs.load("TcVIP"):
        req = float(row.get("VipRechargeRequire") or 0)
        if total_usd + 1e-6 >= req and int(row.get("Level") or 0) > level:
            level = int(row.get("Level") or 0)
    return level


def vip_awards(level: int) -> list[dict]:
    """TcVIP[level].AwardItem -> [{ItemId, Num}] (original config)."""
    out: list[dict] = []
    for row in v2configs.load("TcVIP"):
        if int(row.get("Level") or 0) != int(level):
            continue
        for a in row.get("AwardItem") or []:
            if isinstance(a, (list, tuple)) and len(a) >= 2:
                out.append({"ItemId": int(a[0]), "Num": int(a[1])})
        break
    return out
