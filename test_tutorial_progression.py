"""Regression tests for novice-tutorial / main-task progression.

The v2 client's _mainTaskMod reads only the login MainTask slice, and its
novice guide ends at "upLv", which requires TcLvUpgradeCfg[Lv+1].Exp (200 at
level 1).  These tests lock in the server contract derived from the client
code and the official v7 capture:

  * login MainTask mirrors the persisted mainTaskProgress counter
  * a type-9 stage task is advanced by 10201 phase clears only, not by
    10018 guide-status steps
  * 10201 grants the captured EXP drop (23 for 1-1 phase 1) and advances the
    section record
  * 10010 refuses a level-up below the configured EXP threshold
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers, tasks
from gos.defaults import default_player
from gos.store import Player, Session

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def make_player(uid: str = "tutorial_test") -> Player:
    data = default_player(uid, "TutorialTest")
    tmp = Path(tempfile.mkdtemp(prefix="gos_tutorial_")) / f"{uid}.json"
    return Player(uid=uid, data=data, path=tmp)


def make_session(player: Player) -> Session:
    return Session(jwt="test-jwt", user_id=player.uid, sk="test-key",
                   keypair=None, player_id=player.uid, server_id=1, app_id=1020)


class MemStore:
    def save(self, player: Player) -> None:
        pass


def tutorial_player() -> Player:
    player = make_player()
    player.data["RoleData"]["Lv"] = 1
    player.data["RoleData"]["Exp"] = 0
    player.data["RoleData"]["LastStatus"] = "upLv-0"
    player.data["RoleSection"] = {
        "ChapterId": 1, "SectionId": 1, "PhaseId": 1,
        "BossBlood": 0, "Soldiers": 0, "PlayAnimation": 0,
        "ArtilleryLv": 1, "ArtilleryAddPr": 0,
        "ArtilleryUseNum": 0, "ArtilleryNowSilver": 0,
    }
    player.data["mainTaskProgress"] = {"taskId": 6, "taskNum": 14}
    player.data["MainTask"] = {"TaskId": 1, "TaskNum": 0}
    return player


def test_login_syncs_main_task() -> None:
    player = tutorial_player()
    session = make_session(player)
    data = handlers.action_login({}, player, session, MemStore())
    check("login MainTask mirrors mainTaskProgress",
          data.get("MainTask") == {"TaskId": 6, "TaskNum": 14},
          data.get("MainTask"))
    check("login keeps RoleData.Exp",
          data["RoleData"].get("Exp") == 0, data["RoleData"].get("Exp"))


def test_guide_status_does_not_bump_stage_task() -> None:
    player = tutorial_player()
    tasks.apply_status(player.data, {"StatusStr": "upLv-0"})
    check("10018 upLv-0 leaves a type-9 counter alone",
          tasks.snapshot(player.data) == {"taskId": 6, "taskNum": 14},
          tasks.snapshot(player.data))


def test_stage_fight_grants_exp_and_advances() -> None:
    player = tutorial_player()
    session = make_session(player)
    store = MemStore()
    before_soldier = player.data["RoleData"]["Soldier"]
    before_silver = player.data["RoleData"]["Silver"]
    res = handlers.action_stage_fight({}, player, session, store)
    remain = res.get("RemainList", [])
    rem = {e["ItemId"]: e["Num"] for e in remain}
    check("1-1 phase 1 grants the captured 23 EXP", rem.get(5) == 23, rem)
    check("10201 RemainList is the exact 3-entry v7 list (Soldier, Silver, Exp)",
          [e["ItemId"] for e in remain] == [4, 2, 5], remain)
    check("1-1 phase 1 loses the captured 775 soldiers",
          player.data["RoleData"]["Soldier"] == before_soldier - 775
          and rem.get(4) == before_soldier - 775,
          (before_soldier, player.data["RoleData"]["Soldier"], rem))
    check("1-1 phase 1 gains the constant +1022 silver",
          player.data["RoleData"]["Silver"] == before_silver + 1022
          and rem.get(2) == before_silver + 1022,
          (before_silver, player.data["RoleData"]["Silver"], rem))
    check("SectionRecord.Soldiers stays the NEXT phase's PhaseCfg.Soldiers",
          res["SectionRecord"]["Soldiers"] == 1050, res["SectionRecord"])
    check("1-1 phase 1 advances to phase 2",
          (res["SectionRecord"]["SectionId"], res["SectionRecord"]["PhaseId"]) == (1, 2),
          res["SectionRecord"])
    check("stage task counter advances once",
          tasks.snapshot(player.data) == {"taskId": 6, "taskNum": 15},
          tasks.snapshot(player.data))

    cfg = res.get("PhaseCfg", {})
    check("PhaseCfg carries Force/Soldiers/SoldierDead",
          cfg.get("Force") == 104 and cfg.get("Soldiers") == 1050
          and cfg.get("SoldierDead") == "MIDDLE", cfg)


def test_stage_campaign_reward_table_and_fallback() -> None:
    """Every delta comes from the official v7 10201 capture; anything not in
    the observed chapter-1 table uses the documented conservative fallback."""
    check("ch1 1-1 p1..p7 soldier losses match capture",
          [handlers._stage_soldier_loss(1, 1, p) for p in range(1, 8)]
          == [775, 827, 881, 937, 993, 1052, 1111])
    check("ch1 1-2 p1..p5 soldier losses match capture",
          [handlers._stage_soldier_loss(1, 2, p) for p in range(1, 6)]
          == [2894, 3098, 3308, 3524, 3745])
    check("1-1 p8 uses the labelled floor(Soldiers*0.75) fallback",
          handlers._stage_soldier_loss(1, 1, 8)
          == int(handlers._phase_cfg(1, 1, 8)["Soldiers"] * 0.75),
          handlers._stage_soldier_loss(1, 1, 8))
    check("unknown chapters use the labelled approximation",
          handlers._stage_soldier_loss(2, 1, 1)
          == int(handlers._phase_cfg(2, 1, 1)["Soldiers"] * 0.75))
    # Community-derived Tactics scaling applies ONLY to the fallback; the
    # chapter-1 capture table stays untouched even for a high-Tactics player.
    strong = tutorial_player()
    for c in strong.data["RoleCounsellor"]:
        c["Lv"] = 100
    check("verified ch1 losses ignore Tactics scaling",
          handlers._stage_soldier_loss(1, 1, 1, strong) == 775)
    base = handlers._stage_soldier_loss(2, 1, 1)
    scaled = handlers._stage_soldier_loss(2, 1, 1, strong)
    check("higher Tactics lowers approximate losses", 0 < scaled < base,
          (base, scaled))
    check("loss factor is clamped to 0.25..2.0",
          handlers._stage_soldier_loss(2, 1, 1, strong)
          >= int(base * handlers._TACTICS_LOSS_MIN), (base, scaled))
    weak = tutorial_player()
    for c in weak.data["RoleCounsellor"]:
        c["Talent"] = []
    check("low Tactics raises approximate losses",
          handlers._stage_soldier_loss(2, 1, 1, weak) == int(base * 2.0))
    # Two sequential clears: 1-1 p2 uses the second captured loss (827).
    player = tutorial_player()
    player.data["RoleSection"]["PhaseId"] = 2
    res = handlers.action_stage_fight({}, player, make_session(player), MemStore())
    check("1-1 phase 2 loses the captured 827 soldiers",
          res["RemainList"][0] == {"ItemId": 4, "Num": 5000 - 827},
          res["RemainList"])


def test_stage_counter_clamps_and_level_up_gate() -> None:
    player = tutorial_player()
    session = make_session(player)
    store = MemStore()
    # Two more clears reach the RequireNum (16); further clears must not
    # inflate the counter past it.
    for _ in range(4):
        handlers.action_stage_fight({}, player, session, store)
    check("type-9 counter clamps at RequireNum 16",
          tasks.snapshot(player.data) == {"taskId": 6, "taskNum": 16},
          tasks.snapshot(player.data))
    # 23 + 28 + 33 + 38 = 122 EXP from four clears; still below 200.
    check("four clears leave EXP below level-2 requirement",
          player.data["RoleData"]["Exp"] == 122,
          player.data["RoleData"]["Exp"])
    refused = handlers.action_level_up({}, player, session, store)
    check("10010 refuses level-up below 200 EXP",
          refused.get("_fail") is not None, refused)

    # Two more clears reach 213 EXP.
    for _ in range(2):
        handlers.action_stage_fight({}, player, session, store)
    check("six clears reach 213 EXP",
          player.data["RoleData"]["Exp"] == 213,
          player.data["RoleData"]["Exp"])
    leveled = handlers.action_level_up({}, player, session, store)
    check("10010 levels up at 200+ EXP", leveled.get("NewLevel") == 2, leveled)
    check("cumulative EXP is kept after level-up",
          player.data["RoleData"]["Exp"] == 213,
          player.data["RoleData"]["Exp"])


def test_levy_advances_only_matching_task_type() -> None:
    """TcMainTaskTypeName: 11 grain, 12 trade/silver, 13 soldier.

    Levying Silver must not advance the soldier-recruitment quest (id 3).
    """
    from gos import tasks as taskmod
    p = make_player("levytype")
    # quest 3 = soldier recruitment (MainTaskType 13), requirement 3
    taskmod.set_task(p.data, 3, 0)
    taskmod.apply_levy(p.data, taskmod.LEVY_TYPE_BY_INDEX[1])  # silver
    check("silver levy does not advance soldier quest",
          taskmod.get_task_num(p.data) == 0, p.data["mainTaskProgress"])
    taskmod.apply_levy(p.data, taskmod.LEVY_TYPE_BY_INDEX[2])  # grain
    check("grain levy does not advance soldier quest",
          taskmod.get_task_num(p.data) == 0, p.data["mainTaskProgress"])
    taskmod.apply_levy(p.data, taskmod.LEVY_TYPE_BY_INDEX[3])  # soldier
    check("soldier levy advances soldier quest",
          taskmod.get_task_num(p.data) == 1, p.data["mainTaskProgress"])
    # non-levy quests are untouched by any levy
    taskmod.set_task(p.data, 5, 0)  # type 3 (limited-level ministers)
    taskmod.apply_levy(p.data, taskmod.LEVY_TYPE_BY_INDEX[1])
    taskmod.apply_levy(p.data, None)
    check("non-levy quest ignores all levies",
          taskmod.get_task_num(p.data) == 0, p.data["mainTaskProgress"])


def test_scripted_tutorial_battle_rewards() -> None:
    """User-directed: scripted 10202/10204 stages show 20 EXP / 1000 Silver,
    paid once per stage (official v7 has no reward on these actions)."""
    p = make_player("tutorialbattle")
    store = MemStore()
    p.data["RoleData"]["LastStatus"] = "stage-1"
    before_exp = int(p.data["RoleData"]["Exp"])
    before_silver = int(p.data["RoleData"]["Silver"])
    d = handlers.action_stage_info({}, p, None, store)
    rl = d.get("RemainList") or []
    check("stage battle returns EXP and Silver rows",
          [r["ItemId"] for r in rl] == [5, 2], rl)
    check("stage battle grants 20 EXP / 1000 Silver",
          int(p.data["RoleData"]["Exp"]) == before_exp + 20
          and int(p.data["RoleData"]["Silver"]) == before_silver + 1000,
          (rl, p.data["RoleData"]["Exp"], p.data["RoleData"]["Silver"]))
    check("stage battle response is absolute post-grant totals",
          rl[0]["Num"] == before_exp + 20 and rl[1]["Num"] == before_silver + 1000, rl)
    # same status again (10204) must not pay twice
    d2 = handlers.action_stage_result({}, p, None, store)
    check("scripted stage pays only once (10204 no double grant)",
          "RemainList" not in d2
          and int(p.data["RoleData"]["Silver"]) == before_silver + 1000, d2)

    p2 = make_player("tutorialbattle2")
    p2.data["RoleData"]["LastStatus"] = "stage2-1"
    d = handlers.action_stage_info({}, p2, None, MemStore())
    check("stage2 battle also grants 20 EXP / 1000 Silver",
          [r["ItemId"] for r in d.get("RemainList", [])] == [5, 2]
          and int(p2.data["RoleData"]["Silver"]) == 501000, d)

    p3 = make_player("tutorialbattle3")
    p3.data["RoleData"]["LastStatus"] = "collectGuide-1"
    check("non-scripted status gets no tutorial reward",
          "RemainList" not in handlers.action_stage_info({}, p3, None, MemStore()))


def main() -> None:
    test_scripted_tutorial_battle_rewards()
    test_levy_advances_only_matching_task_type()
    test_login_syncs_main_task()
    test_guide_status_does_not_bump_stage_task()
    test_stage_fight_grants_exp_and_advances()
    test_stage_campaign_reward_table_and_fallback()
    test_stage_counter_clamps_and_level_up_gate()
    total = len(OK)
    passed = sum(OK)
    print(f"{passed}/{total} checks passed")
    if passed != total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
