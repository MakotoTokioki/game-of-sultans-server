"""Time-limited events: rewards for all 9 activities + progress tracking."""
from __future__ import annotations
import sys, tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gos import handlers
from gos.defaults import default_player
from gos.store import Player

ok=[]
def check(name, cond, detail=""):
    ok.append(bool(cond)); print(("PASS" if cond else "FAIL"), name, "" if cond else detail)

def main():
    store=SimpleNamespace(save=lambda p:None)
    p=Player(uid='tl',data=default_player('tl'),path=Path(tempfile.mkdtemp())/'p.json')

    # All 9 activities have reward rows now
    r=handlers.dispatch('11901',{'TimeLimitActivity':list(range(1,10))},p,None,store)
    cfg=r.get('TimeLimitCfg',{})
    missing=[a for a in range(1,10) if not cfg.get(str(a),{}).get('AwardCfg')]
    check('all 9 time-limited activities have rewards', not missing, missing)

    # Progress starts at 0
    r2=handlers.dispatch('11902',{},p,None,store)
    prog=r2.get('TimeLimitComplete',{})
    check('progress starts 0', _as_int(prog.get('1'))==0, prog)

    # Simulate gameplay that bumps progress (levy + gold spend + login)
    handlers._bump_time_limit(p,'LevySilver',3)
    handlers._bump_time_limit(p,'AltinHarca',100)
    r2=handlers.dispatch('11902',{},p,None,store)
    prog=r2.get('TimeLimitComplete',{})
    check('levy bumps activity 1 progress', _as_int(prog.get('1'))>=3, prog)
    check('gold spend bumps activity 2 progress', _as_int(prog.get('2'))>=100, prog)

    # Claim stage 1 now succeeds (RequireNum=1)
    d=handlers.dispatch('11904',{'ActivityId':1,'Stage':1},p,None,store)
    check('claim stage1 succeeds with progress', d.get('Code',0)==0 and '_fail' not in d, d)
    check('claim grants items', bool(d.get('RemainList')), d.get('RemainList'))

    # Rank > 0 after progress
    r3=handlers.dispatch('11903',{'ActivityId':1},p,None,store)
    check('rank self score > 0', r3.get('RankSelf',[0,0])[1]>0, r3.get('RankSelf'))

    print(f'\n{sum(ok)}/{len(ok)} checks passed'); assert all(ok)

def _as_int(x):
    try:return int(x)
    except:return 0

if __name__=='__main__': main()
