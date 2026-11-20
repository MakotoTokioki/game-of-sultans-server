"""Simulated real-money purchases (diamonds/VIP/packs) from original configs."""
from __future__ import annotations
import sys, tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gos import handlers, purchase, v2configs
from gos.defaults import default_player
from gos.store import Player

ok=[]
def check(name, cond, detail=""):
    ok.append(bool(cond)); print(("PASS" if cond else "FAIL"), name, "" if cond else detail)

def main():
    store=SimpleNamespace(save=lambda p:None)
    check('TcRechargeConfig has 4 products', len(purchase.recharge_products())==4)
    check('tier 2 = 250 diamonds (community sheet)', purchase.diamonds_for_tier(2)==250)
    check('VIP level for $5 = 1 (TcVIP threshold)', purchase.vip_level_for_recharge(5)==1)
    check('VIP level for $100 = 4', purchase.vip_level_for_recharge(100)==4)
    check('VIP2 awards from TcVIP', any(a['ItemId']==303 for a in purchase.vip_awards(2)))

    p=Player(uid='buy',data=default_player('buy'),path=Path(tempfile.mkdtemp())/'p.json')
    gold0=p.data['RoleData']['Gold']
    r=handlers.apply_purchase(p, usd=0.99, store=store)
    # first purchase doubles: 50 * 2 = 100
    check('first $0.99 purchase doubles to 100 diamonds',
          p.data['RoleData']['Gold']==gold0+100 and r['first_purchase_bonus'] is True, r)
    r2=handlers.apply_purchase(p, usd=0.99, store=store)
    check('second purchase is 50 diamonds (no bonus)',
          r2['diamonds']==50 and r2['first_purchase_bonus'] is False, r2)

    # product-id based purchase (no usd given)
    p2=Player(uid='buy2',data=default_player('buy2'),path=Path(tempfile.mkdtemp())/'p2.json')
    g0=p2.data['RoleData']['Gold']
    r3=handlers.apply_purchase(p2, product_id=1, store=store)
    check('product 1 (month card $4.99) first purchase = 500 doubled',
          p2.data['RoleData']['Gold']==g0+500, r3)

    # ack action is Lua-safe
    p3=Player(uid='buy3',data=default_player('buy3'),path=Path(tempfile.mkdtemp())/'p3.json')
    d=handlers.action_purchase_ack({'productId':2},p3,None,store)
    check('11511 ack returns Code=0', d.get('Code')==0, d)
    check('11511 ack does NOT double-grant', p3.data['RoleData']['Gold']==default_player('x')['RoleData']['Gold'], p3.data['RoleData']['Gold'])

    print(f'\n{sum(ok)}/{len(ok)} checks passed'); assert all(ok)
if __name__=='__main__': main()
