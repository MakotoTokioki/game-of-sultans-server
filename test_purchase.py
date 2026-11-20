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
    r=handlers.apply_purchase(p, tier=2, store=store)
    check('purchase grants 250 diamonds', p.data['RoleData']['Gold']==gold0+250, r)
    check('purchase returns tier/diamonds', r['tier']==2 and r['diamonds']==250, r)
    check('purchase sets VIP', p.data['RoleData'].get('VipLv')==1, p.data['RoleData'].get('VipLv'))

    # product-based purchase
    p2=Player(uid='buy2',data=default_player('buy2'),path=Path(tempfile.mkdtemp())/'p2.json')
    g0=p2.data['RoleData']['Gold']
    r2=handlers.apply_purchase(p2, product_id=1, store=store)
    check('product 1 grants 50 diamonds', p2.data['RoleData']['Gold']==g0+50, r2)

    # ack action is Lua-safe
    p3=Player(uid='buy3',data=default_player('buy3'),path=Path(tempfile.mkdtemp())/'p3.json')
    d=handlers.action_purchase_ack({'productId':2},p3,None,store)
    check('11511 ack returns Code=0', d.get('Code')==0, d)
    check('11511 ack grants diamonds', p3.data['RoleData']['Gold']>g0-250, p3.data['RoleData']['Gold'])

    print(f'\n{sum(ok)}/{len(ok)} checks passed'); assert all(ok)
if __name__=='__main__': main()
