"""Community spreadsheet reward tables -> original v2 TcItem IDs."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gos import sheet_rewards, sheet_awards, handlers, v2configs

ok=[]
def check(name, cond, detail=""):
    ok.append(bool(cond)); print(("PASS" if cond else "FAIL"), name, "" if cond else detail)

def main():
    # token parsing -> original item IDs
    check('Altın Kuponu -> 102', sheet_rewards.parse_token('Altın Kuponu (3 adet)')==(102,3))
    check('Rastgele Kitap +1000 -> 215', sheet_rewards.parse_token('Rastgele Kitap +1000 (+1)')==(215,1))
    check('Paye Parçası -> 529', sheet_rewards.parse_token('Paye Parçası (2 adet)')==(529,2))
    check('event-only flower skipped', sheet_rewards.parse_token('Gül (5 adet)') is None)
    check('base resource Altın -> 2', sheet_rewards.parse_token('100.000 Altın')==(2,100000))

    # all mapped IDs exist in v2 TcItem
    valid={int(r.get('Id')) for r in v2configs.load('TcItem')}
    for cycle in range(1,5):
        table=sheet_awards.elmas_harca_table(cycle)
        check(f'Devir {cycle} has tiers', len(table)>=30, len(table))
        ids={a['ItemId'] for row in table for a in row['Award']}
        check(f'Devir {cycle} all item IDs valid', ids<=valid, ids-valid)

    rows=handlers._time_limit_rows(1)
    check('activity 1 has real award stages', len(rows)>=30 and rows[0]['Award'], len(rows))
    check('activity 1 stage num sequential', rows[0]['RequireNum']==1 and rows[1]['RequireNum']==2)

    print(f'\n{sum(ok)}/{len(ok)} checks passed'); assert all(ok)
if __name__=='__main__': main()
