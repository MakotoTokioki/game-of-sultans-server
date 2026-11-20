"""Regression tests for the hunting/instance extras cluster
(11804-11816 minus the five handlers already in gos/handlers.py).

Run from the server directory:  python3 test_instance_more_cluster.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers, v2configs
from gos.defaults import default_player
from gos.handlers_instance_more import (
    INSTANCE_MORE_HANDLERS,
    action_instance_exchange,
    action_instance_global_attack,
    action_instance_global_boss_attack,
    action_instance_global_damage,
    action_instance_global_damage_rank,
    action_instance_global_info,
    action_instance_global_use_item,
    action_instance_hunt_box,
    action_instance_receive_hunt_box,
    action_instance_share_box,
    action_instance_single_use_item,
)
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="instancemoretest", data=default_player("instancemoretest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def _stub():
    return SimpleNamespace(save=lambda p: None), None


def main() -> None:
    store, session = _stub()

    # registration reaches the live dispatcher
    check("all 11 ids exported",
          set(INSTANCE_MORE_HANDLERS) == {
              "11804", "11805", "11806", "11807", "11809", "11811",
              "11812", "11813", "11814", "11815", "11816"},
          sorted(INSTANCE_MORE_HANDLERS))
    check("merged into handlers.HANDLERS",
          all(handlers.HANDLERS.get(a) is fn
              for a, fn in INSTANCE_MORE_HANDLERS.items()))

    # config-backed expectations
    alliance = min((r for r in v2configs.load("TcAllianceInstance")
                    if isinstance(r, dict)), key=lambda r: int(r.get("Lv") or 0))
    boss_hp = int(alliance.get("Blood") or 0)
    exchange = next(r for r in v2configs.load("TcBossExchange")
                    if int(r.get("ItemId")) == 210)
    box1 = next(r for r in v2configs.load("TcBossSingleAwardBox")
                if int(r.get("BoxId")) == 1)

    # ---- 11805 global info ----
    p = _player()
    d = action_instance_global_info({}, p, session, store)
    for field in ("BossId", "BossHP", "BossHPMax", "CounsellorAttackData",
                  "DamageRank", "KillerName", "RoleDamage", "RoleRank",
                  "DamageRankAward", "BossGlobalDead", "KillRank",
                  "BossSingleComplete"):
        check(f"11805 has {field}", field in d, d)
    check("11805 BossHPMax from TcAllianceInstance.Blood", d["BossHPMax"] == boss_hp, d)
    check("11805 Lua-safe table/list/bool types",
          isinstance(d["CounsellorAttackData"], dict)
          and isinstance(d["DamageRank"], list)
          and isinstance(d["KillRank"], list)
          and isinstance(d["BossGlobalDead"], dict)
          and d["BossSingleComplete"] is False, d)

    # ---- 11815 global attack (BossStasus + nonzero HP ratio) ----
    hp_before = d["BossHP"]
    d15 = action_instance_global_boss_attack({}, p, session, store)
    check("11815 has BossStasus/BossId/BossHP/BossHPMax",
          all(k in d15 for k in ("BossStasus", "BossId", "BossHP", "BossHPMax")), d15)
    check("11815 BossHPMax nonzero (no div-by-zero in scene)",
          d15["BossHPMax"] > 0, d15)
    check("11815 config-sourced damage applied",
          0 < d15["BossHP"] < hp_before, (hp_before, d15))

    # ---- 11806 global attack ----
    counsellor = int(p.data["RoleCounsellor"][0].get("CounsellorId") or 0)
    d06 = action_instance_global_attack({"CounsellorId": counsellor}, p, session, store)
    check("11806 success has all callback fields",
          d06.get("BossStasus") == 0 and all(
              k in d06 for k in ("BossId", "BossHP", "BossHPMax",
                                 "CounsellorAttackData", "RemainList",
                                 "DamageRank", "RoleDamage", "RoleRank",
                                 "DamageRankAward", "BossGlobalDead", "KillRank")), d06)
    check("11806 CounsellorAttackData records the attacker",
          str(counsellor) in d06["CounsellorAttackData"], d06)
    d06b = action_instance_global_attack({"CounsellorId": counsellor}, p, session, store)
    check("11806 duplicate attacker returns BossStasus=1", d06b == {"BossStasus": 1}, d06b)

    # ---- 11807 global use item recovers a vizier ----
    d07 = action_instance_global_use_item({"CounsellorId": counsellor}, p, session, store)
    check("11807 returns CounsellorAttackData dict",
          isinstance(d07.get("CounsellorAttackData"), dict), d07)
    check("11807 recovered attacker can attack again",
          str(counsellor) not in d07["CounsellorAttackData"], d07)
    d06c = action_instance_global_attack({"CounsellorId": counsellor}, p, session, store)
    check("11807 recovery lets 11806 succeed again", d06c.get("BossStasus") == 0, d06c)

    # ---- 11804 single use item (reuse existing single fight state) ----
    p2 = _player()
    d03 = handlers.action_instance_boss_attack({"CounsellorId": counsellor}, p2, session, store)
    check("11804 setup: 11803 recorded the attacker",
          str(counsellor) in (d03.get("CounsellorAttackData") or {}), d03)
    d04 = action_instance_single_use_item({"CounsellorId": counsellor}, p2, session, store)
    check("11804 returns CounsellorAttackData dict",
          isinstance(d04.get("CounsellorAttackData"), dict), d04)
    check("11804 recovered single-fight attacker",
          str(counsellor) not in d04["CounsellorAttackData"], d04)

    # ---- 11809 / 11816 damage rank ----
    d09 = action_instance_global_damage({}, p, session, store)
    check("11809 shape + config-sourced accumulated damage",
          set(d09) == {"DamageRank", "RoleRank", "Damage", "RId"}
          and isinstance(d09["DamageRank"], list)
          and d09["Damage"] > 0, d09)
    d16 = action_instance_global_damage_rank({}, p, session, store)
    check("11816 DamageRank is a list", d16.get("DamageRank") == [], d16)

    # ---- 11811 credit exchange ----
    p3 = _player()
    p3.data["RoleData"]["BossCredits"] = 100000
    d11 = action_instance_exchange({"ItemId": 210, "ItemCount": 2}, p3, session, store)
    check("11811 RemainList[1] is the new BossCredits (ItemId 9)",
          len(d11.get("RemainList") or []) >= 1
          and d11["RemainList"][0]["ItemId"] == 9
          and d11["RemainList"][0]["Num"] < 100000, d11)
    check("11811 RequireScore/ExchangeTimes present and config-derived",
          isinstance(d11.get("RequireScore"), int)
          and d11["RequireScore"] > int(exchange.get("InitNeed"))
          and d11.get("ExchangeTimes") == 9, d11)
    bag = {int(e.get("ItemId")): int(e.get("Num")) for e in p3.data["RoleItem"]
           if isinstance(e, dict)}
    check("11811 grants config ItemId x payload count", bag.get(210) == 2, bag)

    # ---- 11812 / 11813 / 11814 hunt boxes ----
    p4 = _player()
    d12 = action_instance_hunt_box({"BoxId": 1}, p4, session, store)
    for field in ("IsFinish", "HasReceivedByOthers", "RemainTimes",
                  "RemainList", "InReceiverList", "ReceiverList"):
        check(f"11812 has {field}", field in d12, d12)
    check("11812 Lua-safe box lists",
          isinstance(d12["RemainList"], list)
          and isinstance(d12["InReceiverList"], list)
          and isinstance(d12["ReceiverList"], list), d12)
    bag4 = {int(e.get("ItemId")): int(e.get("Num")) for e in p4.data["RoleItem"]
            if isinstance(e, dict)}
    self_items = [int(g[0]) for g in box1.get("SelfAwardItem") or []
                  if len(g) >= 3 and int(g[2]) == 100]
    check("11812 grants TcBossSingleAwardBox.SelfAwardItem (weight 100)",
          all(i in bag4 for i in self_items), (bag4, self_items))
    check("11812 RemainTimes from ConfigEnvSet.OpenHuntBoxLimit",
          d12["RemainTimes"] == 9, d12)
    d13 = action_instance_share_box({"BoxId": 1}, p4, session, store)
    check("11813 forward-only empty map", d13 == {}, d13)
    d14 = action_instance_receive_hunt_box({"BoxId": 1}, p4, session, store)
    check("11814 IsFinish=1", d14.get("IsFinish") == 1, d14)
    global_items = [int(g[0]) for g in box1.get("GlobalAwardItem") or []]
    bag4b = {int(e.get("ItemId")) for e in p4.data["RoleItem"] if isinstance(e, dict)}
    check("11814 grants TcBossSingleAwardBox.GlobalAwardItem",
          all(i in bag4b for i in global_items), (bag4b, global_items))
    d14b = action_instance_receive_hunt_box({"BoxId": 1}, p4, session, store)
    check("11814 GlobalAwardItem granted once",
          d14b.get("RemainList") == [], d14b)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok), "instance-more cluster regression FAILED"


if __name__ == "__main__":
    main()
