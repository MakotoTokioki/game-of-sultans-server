"""Deterministic 4-week event rotation (community spreadsheet Devir 1-4)."""
from __future__ import annotations
import sys, tempfile, datetime
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gos import rotation, handlers
from gos.defaults import default_player
from gos.store import Player

ok=[]
def check(name, cond, detail=""):
    ok.append(bool(cond)); print(("PASS" if cond else "FAIL"), name, "" if cond else detail)

def ts_of(d):
    return int(datetime.datetime.combine(d, datetime.time(12,0), rotation._GAME_TZ).timestamp())

def main():
    anchor=datetime.date(2026,1,5)  # Monday
    # 4 cycles in order, then repeat
    expected=[(1,4),(2,1),(3,3),(4,5),(1,4),(2,1),(3,3),(4,5)]
    got=[]
    for w in range(8):
        d=anchor+datetime.timedelta(weeks=w)
        c=rotation.current_cycle(ts_of(d))
        got.append((c['devir'], c['ranking_type']))
    check('4-week cycle repeats in Devir 1-2-3-4 order', got==expected, got)

    # window covers full week and contains the query time
    d=anchor+datetime.timedelta(weeks=10, days=4)  # a Friday
    ts=ts_of(d)
    start,end=rotation.cycle_window(ts)
    fmt="%Y-%m-%d %H:%M:%S"
    sdt=datetime.datetime.strptime(start,fmt).replace(tzinfo=rotation._GAME_TZ)
    edt=datetime.datetime.strptime(end,fmt).replace(tzinfo=rotation._GAME_TZ)
    qdt=datetime.datetime.fromtimestamp(ts,rotation._GAME_TZ)
    check('cycle window contains the current week', sdt<=qdt<=edt, (start,end,str(qdt)))
    check('cycle window is ~7 days', 6 <= (edt-sdt).days <= 7, (edt-sdt))

    # active ranking type is only the current cycle's
    active=rotation.active_ranking_types(ts)
    check('only current cycle rush active', active=={rotation.current_cycle(ts)['ranking_type']}, active)

    # 11901 integration: active rush gets live window, others get the open-ended
    # advertisement. Use real "now" — the handler uses rotation._now() internally.
    p=Player(uid='rot',data=default_player('rot'),path=Path(tempfile.mkdtemp())/'p.json')
    store=SimpleNamespace(save=lambda x:None)
    ids=list(range(1,10))
    r=handlers.dispatch('11901',{'CompetitionRank':ids},p,None,store)
    cfg=r.get('CompetitionRankCfg',{})
    cur=rotation.current_cycle()          # real now
    active_id=[a for a,t in handlers._RANK_RUSH_TYPES.items() if t==cur['ranking_type']][0]
    s,e=rotation.cycle_window()           # real now
    check('active rush uses live cycle window', cfg[str(active_id)]['StartTime']==s, (active_id,cfg[str(active_id)],s))
    other=[a for a in ids if a!=active_id][0]
    check('non-active rush keeps advertised window', cfg[str(other)]['EndTime']=='2030-01-01 00:00:00', cfg[str(other)])

    print(f'\n{sum(ok)}/{len(ok)} checks passed'); assert all(ok)
if __name__=='__main__': main()
