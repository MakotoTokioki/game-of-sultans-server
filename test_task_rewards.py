"""Main-quest (10015) reward regressions.

Ground truth: the *original* settings.TcMainTask.lua bundled under
gos/v2configs_data/ (MainTaskId -> AwardItem [[ItemId, Num], ...]) and the
official v7 10015 capture (recon/v7-capture/v7_actions.jsonl, 2026-11-16):
claiming a completed task returns its AwardItem as absolute RemainList
values; task 1's [[2, 100000]] is Silver +100000.  No reward is invented.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers, tasks, v2configs
from gos.defaults import default_player
from gos.store import Player

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def make_player(uid: str = "task_test") -> Player:
    data = default_player(uid, "TaskTest")
    tmp = Path(tempfile.mkdtemp(prefix="gos_task_")) / f"{uid}.json"
    return Player(uid=uid, data=data, path=tmp)


class MemStore:
    def __init__(self) -> None:
        self.saved = 0

    def save(self, player: Player) -> None:
        self.saved += 1


def claim_ready(uid: str, task_id: int) -> Player:
    player = make_player(uid)
    need = int(tasks.MAINTASK_REQUIRE_NUM[task_id])
    player.data["mainTaskProgress"] = {"taskId": task_id, "taskNum": need}
    player.data["MainTask"] = {"TaskId": task_id, "TaskNum": need}
    return player


def remain_map(data: dict) -> dict:
    return {e["ItemId"]: e["Num"] for e in data.get("RemainList", [])}


def test_config_is_bundled_and_parses() -> None:
    rows = v2configs.load("TcMainTask")
    by_id = {int(r.get("MainTaskId") or 0): r for r in rows if isinstance(r, dict)}
    check("v2configs.load('TcMainTask') reads the bundled config", len(rows) > 100, len(rows))
    check("TcMainTask[1].AwardItem == [[2, 100000]]",
          by_id[1]["AwardItem"] == [[2, 100000]], by_id[1]["AwardItem"])
    check("TcMainTask[10].AwardItem are bag items",
          by_id[10]["AwardItem"] == [[508, 2], [509, 1]], by_id[10]["AwardItem"])


def test_task_one_claim_grants_configured_silver() -> None:
    player = claim_ready("task_one", 1)
    store = MemStore()
    before = player.data["RoleData"]["Silver"]
    d = handlers.action_task({}, player, None, store)
    rem = remain_map(d)
    check("claim advances to task 2", d.get("NextTaskId") == 2 and d.get("NextNum") == 0, d)
    check("claim grants the configured +100000 Silver",
          player.data["RoleData"]["Silver"] == before + 100000
          and rem.get(2) == before + 100000, (before, rem))
    check("RemainList carries the absolute resource totals",
          set(rem) >= {1, 2, 3, 4, 5}, rem)
    check("claim persists the reward", store.saved == 1, store.saved)


def test_incomplete_task_refuses_without_reward() -> None:
    player = make_player("task_incomplete")
    player.data["mainTaskProgress"] = {"taskId": 0, "taskNum": 0}
    store = MemStore()
    d = handlers.action_task({}, player, None, store)
    check("incomplete claim returns plain NextTaskId/NextNum",
          d == {"NextTaskId": 0, "NextNum": 0}, d)
    check("incomplete claim grants nothing",
          "RemainList" not in d
          and player.data["RoleData"]["Silver"] == 500000, d)
    check("incomplete claim does not need to save", store.saved == 0, store.saved)


def test_bag_item_task_grants_role_items() -> None:
    player = claim_ready("task_bag", 10)
    d = handlers.action_task({}, player, None, MemStore())
    counts = {e["ItemId"]: e["Num"] for e in player.data["RoleItem"]}
    check("bag item 508 x2 from TcMainTask[10]",
          counts.get(508) == 2, counts)
    check("bag item 509 x1 from TcMainTask[10]",
          counts.get(509) == 1, counts)
    check("bag items appear in RemainList with absolute counts",
          remain_map(d).get(508) == 2 and remain_map(d).get(509) == 1, d)
    check("claim of task 10 advances to 11", d.get("NextTaskId") == 11, d)


def main() -> None:
    test_config_is_bundled_and_parses()
    test_task_one_claim_grants_configured_silver()
    test_incomplete_task_refuses_without_reward()
    test_bag_item_task_grants_role_items()
    print(f"\n{sum(OK)}/{len(OK)} checks passed")
    sys.exit(0 if all(OK) else 1)


if __name__ == "__main__":
    main()
