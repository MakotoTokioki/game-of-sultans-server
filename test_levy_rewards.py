"""Regression tests for real levy resource grants (action 10001 / 10002).

The v2 client displays each levy card's "+N" as

    N = floor(getRolePower(ItemId) * (1 + BuffAdd))

(scene/homeScene.manageAssetsDialog.lua L9_1, lines 816-848 + 1178-1188),
with card 1 -> ItemId 2 (Silver), card 2 -> 3 (Food), card 3 -> 4 (Soldier).
That same number must be credited by the server, persisted as an absolute
RoleData total and returned first in RemainList.  Proof sources:

  * live v2 phone (devtest viziers Lv 1/45/3): +291 / +258 / +475
  * v7 official capture (recon/v7-capture/v7_actions.jsonl 10001):
        EstimatedRevenue {'2':180,'3':147,'4':198}
        RemainList 1000 -> 1180 -> 1360  (+180 per collect)
        EstimatedRevenue {'2':2200,'3':2155,'4':2218}

Run: python3 test_levy_rewards.py
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers
from gos.defaults import default_player
from gos.store import Player

OK: list[bool] = []

KEY = {2: "Silver", 3: "Food", 4: "Soldier"}


def check(name: str, cond: bool, detail: object = "") -> None:
    OK.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


class MemStore:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def save(self, player: Player) -> None:
        self.saved.append(player.uid)


def make_player(uid: str = "rewardtest", vizier_levels=(1, 45, 3),
                queued: int = 2) -> Player:
    """The exact account state behind the phone screenshot.

    default_player's starter viziers carry the same talents the live devtest
    account had when the screenshot was taken; only their levels differ.
    RoleData.Power stays 1000 -> stage cd 60 s.
    """
    data = default_player(uid)
    for row, level in zip(data["RoleCounsellor"], vizier_levels):
        row["Lv"] = level
    now = int(time.time())
    cd = 60
    data["RoleData"]["LevyTimeUTC"] = [now - queued * cd] * 3
    data["RoleData"]["Silver"] = 500000
    data["RoleData"]["Food"] = 500000
    data["RoleData"]["Soldier"] = 5000
    return Player(uid=uid, data=data, path=Path("/tmp/opencode/gos_test_players") / f"{uid}.json")


def call(action, payload: dict, player: Player, store: MemStore) -> dict:
    return action(payload, player, SimpleNamespace(jwt="jwt", user_id=player.uid), store)


def remain_map(data: dict) -> dict:
    return {int(e["ItemId"]): int(e["Num"]) for e in data.get("RemainList", [])}


def test_exact_live_regression_targets() -> None:
    """devtest viziers Lv 1/45/3 -> the three numbers on the phone."""
    player = make_player()
    check("config-derived revenue == phone screenshot 291/258/475",
          handlers._levy_revenue(player) == {2: 291, 3: 258, 4: 475},
          handlers._levy_revenue(player))

    store = MemStore()
    for ctype, item_id, amount in ((1, 2, 291), (2, 3, 258), (3, 4, 475)):
        p = make_player(f"exact_{ctype}")
        before = p.data["RoleData"][KEY[item_id]]
        d = call(handlers.action_manage_assets, {"CollectType": ctype}, p, store)
        check(f"10001 CollectType={ctype} credits exactly +{amount}",
              p.data["RoleData"][KEY[item_id]] == before + amount,
              (before, p.data["RoleData"][KEY[item_id]]))
        check(f"10001 CollectType={ctype} EstimatedRevenue carries all three",
              d["EstimatedRevenue"] == {"2": 291, "3": 258, "4": 475},
              d["EstimatedRevenue"])
        check(f"10001 CollectType={ctype} LevyList carries the grant",
              d["LevyList"] == {str(ctype): [{"ItemId": item_id, "Num": amount}]},
              d["LevyList"])
    check("all collects saved", len(store.saved) == 3, store.saved)


def test_absolute_remain_list_granted_first() -> None:
    p = make_player("remain_first")
    d = call(handlers.action_manage_assets, {"CollectType": 1}, p, MemStore())
    rem = d["RemainList"]
    rd = p.data["RoleData"]
    check("granted resource is RemainList[0]",
          rem and int(rem[0]["ItemId"]) == 2, rem[:2])
    check("RemainList[0].Num is the new absolute Silver total",
          int(rem[0]["Num"]) == rd["Silver"] == 500291, (rem[:2], rd["Silver"]))
    rmap = remain_map(d)
    check("all four resource bars present with absolute values",
          all(rmap.get(i) == int(rd[k]) for i, k in
              ((1, "Gold"), (2, "Silver"), (3, "Food"), (4, "Soldier"))),
          (rmap, {k: rd[k] for k in ("Gold", "Silver", "Food", "Soldier")}))


def test_no_double_grant() -> None:
    store = MemStore()
    # 1 queued tick: the first collect consumes it, the identical replay at
    # the now-anchored timestamp consumes nothing.
    p = make_player("nodouble", queued=1)
    d1 = call(handlers.action_manage_assets, {"CollectType": 1}, p, store)
    check("first collect grants +291", p.data["RoleData"]["Silver"] == 500291, d1)
    d2 = call(handlers.action_manage_assets, {"CollectType": 1}, p, store)
    check("identical replay after the queue drained grants nothing",
          p.data["RoleData"]["Silver"] == 500291, d2)
    check("replay LevyList is empty (no tick consumed)", d2["LevyList"] == {"1": []}, d2)

    # Anchor already at `now` -> zero queued levies even on the first request.
    p = make_player("nodouble_idle", queued=0)
    call(handlers.action_manage_assets, {"CollectType": 2}, p, store)
    check("idle anchor never grants", p.data["RoleData"]["Food"] == 500000,
          p.data["RoleData"]["Food"])

    # A very stale anchor is capped at GainTimes*cd; one collect must never
    # turn thousands of idle seconds into thousands of grants.
    p = make_player("nodouble_stale")
    p.data["RoleData"]["LevyTimeUTC"] = [int(time.time()) - 86400] * 3
    before = p.data["RoleData"]["Silver"]
    call(handlers.action_manage_assets, {"CollectType": 1}, p, store)
    check("stale anchor still grants only one tick",
          p.data["RoleData"]["Silver"] == before + 291,
          p.data["RoleData"]["Silver"] - before)


def test_other_resources_untouched() -> None:
    store = MemStore()
    for ctype, item_id in ((1, 2), (2, 3), (3, 4)):
        p = make_player(f"untouched_{ctype}")
        before = {i: p.data["RoleData"][KEY[i]] for i in (2, 3, 4)}
        call(handlers.action_manage_assets, {"CollectType": ctype}, p, store)
        after = {i: p.data["RoleData"][KEY[i]] for i in (2, 3, 4)}
        check(f"CollectType={ctype} changes only ItemId {item_id}",
              after[item_id] == before[item_id] + handlers._levy_revenue(p)[item_id]
              and all(after[i] == before[i] for i in (2, 3, 4) if i != item_id),
              (before, after))


def test_one_key_grants_sum_of_consumed_ticks() -> None:
    p = make_player("onekey")
    store = MemStore()
    before = {i: p.data["RoleData"][KEY[i]] for i in (2, 3, 4)}
    d = call(handlers.action_manage_assets, {"CollectType": 4}, p, store)
    rev = d["EstimatedRevenue"]
    check("one-key EstimatedRevenue still complete",
          rev == {"2": 291, "3": 258, "4": 475}, rev)
    check("one-key grants the sum of all three ticks",
          all(p.data["RoleData"][KEY[i]] == before[i] + rev[str(i)]
              for i in (2, 3, 4)),
          {i: p.data["RoleData"][KEY[i]] - before[i] for i in (2, 3, 4)})
    check("one-key LevyList carries all grants",
          d["LevyList"] == {"4": [{"ItemId": 2, "Num": 291},
                                  {"ItemId": 3, "Num": 258},
                                  {"ItemId": 4, "Num": 475}]},
          d["LevyList"])
    check("one-key RemainList[0] is the first granted resource",
          int(d["RemainList"][0]["ItemId"]) == 2, d["RemainList"][:2])


def test_v7_capture_oracle_sequence() -> None:
    """With the oracle's revenue, each collect raises the absolute total by
    exactly that amount (v7: 1000 -> 1180 -> 1360 for revenue 180)."""
    for revenue in ({"2": 180, "3": 147, "4": 198},
                    {"2": 2200, "3": 2155, "4": 2218}):
        p = make_player(f"oracle_{revenue['2']}")
        p.data["RoleData"]["Silver"] = 1000
        p.data["RoleData"]["LevyTimeUTC"] = [int(time.time()) - 5 * 60] * 3
        store = MemStore()

        def fake_power(player: Player, index: int, _r=revenue) -> int:
            return _r[str(index)]

        with patch.object(handlers, "_gov_role_power", fake_power):
            totals = []
            for _ in range(3):
                d = call(handlers.action_manage_assets, {"CollectType": 1}, p, store)
                totals.append(int(remain_map(d)[2]))
                check(f"oracle revenue {revenue['2']}: EstimatedRevenue matches",
                      d["EstimatedRevenue"] == revenue, d["EstimatedRevenue"])
        check(f"oracle revenue {revenue['2']}: absolute total rises by exactly "
              f"that amount each collect",
              totals == [1000 + revenue["2"] * n for n in (1, 2, 3)], totals)


def test_recover_has_no_premature_credit() -> None:
    """10002 only adds queued ticks (Levy Seal); 10001 credits the resources."""
    p = make_player("recover")
    before = {i: p.data["RoleData"][KEY[i]] for i in (2, 3, 4)}
    d = call(handlers.action_recover, {"RecoverType": 2, "RecoverTimes": 2}, p, MemStore())
    after = {i: p.data["RoleData"][KEY[i]] for i in (2, 3, 4)}
    check("recover does not credit resources (no double grant)",
          before == after, (before, after))
    check("recover still ships absolute RemainList",
          all(int(remain_map(d).get(i, -1)) == p.data["RoleData"][KEY[i]]
              for i in (2, 3, 4)), d.get("RemainList"))
    check("recovered resource is first in RemainList",
          int(d["RemainList"][0]["ItemId"]) == 3, d["RemainList"][:2])
    check("recover advertises the config-derived revenue",
          d["EstimatedRevenue"] == {"2": 291, "3": 258, "4": 475},
          d["EstimatedRevenue"])


def main() -> None:
    test_exact_live_regression_targets()
    test_absolute_remain_list_granted_first()
    test_no_double_grant()
    test_other_resources_untouched()
    test_one_key_grants_sum_of_consumed_ticks()
    test_v7_capture_oracle_sequence()
    test_recover_has_no_premature_credit()
    print("\n%d/%d checks passed" % (sum(OK), len(OK)))
    sys.exit(0 if all(OK) else 1)


if __name__ == "__main__":
    main()
