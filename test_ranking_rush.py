"""Ranking Rush 'activate all' — 11901 CompetitionRank list + 11906 rank lists."""
from __future__ import annotations
import sys, tempfile
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gos import handlers, v2configs
from gos.defaults import default_player
from gos.store import Player

ok=[]
def check(name, cond, detail=""):
    ok.append(bool(cond)); print(("PASS" if cond else "FAIL"), name, "" if cond else detail)

def main():
    p=Player(uid='rt',data=default_player('rt'),path=Path(tempfile.mkdtemp())/'p.json')
    store=SimpleNamespace(save=lambda x:None)
    rows=v2configs.load('TcRankUpActivityCfg')
    check('TcRankUpActivityCfg has 9 activity types', len(rows)==9, len(rows))

    r=handlers.dispatch('11901',{'CompetitionRank':[1,2,3]},p,None,store)
    lst=r.get('CompetitionRank')
    check('11901 CompetitionRank is a list of all 9 rushes', isinstance(lst,list) and len(lst)==9, type(lst).__name__ if lst is not None else None)
    types=[x['ActivityType'] for x in lst]
    check('all 9 original activity types present', types==list(range(1,10)), types)
    check('CompetitionRankReceive present', isinstance(r.get('CompetitionRankReceive'),dict), None)
    check('CompetitionRankCfg still keyed by requested id', isinstance(r.get('CompetitionRankCfg'),dict) and '1' in r['CompetitionRankCfg'], None)
    for x in lst:
        check(f"rush {x['Id']} has valid active window", x['StartTime']<x['EndTime'], x)

    r2=handlers.dispatch('11906',{},p,None,store)
    check('11906 returns Lua-safe empty rank lists', r2.get('RankInfo')==[] and r2.get('RankList')==[], r2)

    print(f"\n{sum(ok)}/{len(ok)} checks passed"); assert all(ok)
if __name__=='__main__': main()
