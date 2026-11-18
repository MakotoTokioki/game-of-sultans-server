"""Find / Masquerade scene (10601-10606) regressions.

The v7 capture had eight 10602 requests answered by the permissive fallback
(no VisitData), and _findMod.action10602 indexes VisitData.{Vitality,Fate,
FateTime,VitalityTime,TransferNum,PraiseGuide} with no nil guard, so its
callback threw.  Every value here is either the current time, a neutral zero,
or TcVIP[VipLv].MaxVitality / TcVIP[VipLv].TransferNum from the original
bundled config; no beauty or reward is invented.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers, v2configs
from gos.defaults import default_player
from gos.store import Player

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def make_player(uid: str = "find_test") -> Player:
    data = default_player(uid, "FindTest")
    tmp = Path(tempfile.mkdtemp(prefix="gos_find_")) / f"{uid}.json"
    return Player(uid=uid, data=data, path=tmp)


class MemStore:
    def __init__(self) -> None:
        self.saved = 0

    def save(self, player: Player) -> None:
        self.saved += 1


def test_10602_visit_data_shape() -> None:
    player = make_player()
    before = handlers._now()
    d = handlers.action_visit_info({}, player, None, MemStore())
    visit = d.get("VisitData")
    expected_keys = {"Vitality", "Fate", "FateTime", "VitalityTime",
                     "TransferNum", "PraiseGuide"}
    check("10602 has exactly the VisitData slice the callback indexes",
          isinstance(visit, dict) and set(visit) == expected_keys, d)
    max_vitality = int(v2configs.vip_row(0).get("MaxVitality") or 0)
    check("10602 Vitality comes from TcVIP[VipLv].MaxVitality",
          visit.get("Vitality") == max_vitality, (visit, max_vitality))
    check("10602 starts with zero Fate/TransferNum/PraiseGuide",
          (visit["Fate"], visit["TransferNum"], visit["PraiseGuide"]) == (0, 0, 0), visit)
    # The client feeds FateTime to tools.common.dateToSec (os.time(table)) and
    # uses VitalityTime as raw seconds in startVitalityTime.  An epoch int in
    # FateTime raises "field 'day' missing in date table" and blocks entry.
    import datetime
    parsed = datetime.datetime.strptime(visit["FateTime"], "%Y-%m-%d %H:%M:%S")
    delta = abs((parsed.replace(tzinfo=handlers._GAME_TZ).timestamp()) - before)
    check("10602 FateTime is a parseable game-day datetime string", delta < 5,
          (visit["FateTime"], delta))
    cd = int(v2configs.env_value("VitalityCD") or 1800)
    check("10602 VitalityTime is elapsed seconds in the current tick",
          isinstance(visit["VitalityTime"], int)
          and 0 <= visit["VitalityTime"] < cd, (visit["VitalityTime"], cd))
    check("10602 persisted VisitData keeps raw epoch timestamps",
          isinstance(player.data["VisitData"]["FateTime"], int)
          and isinstance(player.data["VisitData"]["VitalityTime"], int),
          player.data.get("VisitData"))


def test_10601_top_level_fields() -> None:
    player = make_player()
    d = handlers.action_visit_state({}, player, None, MemStore())
    need = {"VitalityTime", "FateTime", "Fate", "Vitality", "PraiseGuide"}
    check("10601 returns all top-level fields the callback reads",
          need <= set(d), d)
    check("10601 EventType is 0 and RemainList is a safe empty table",
          d.get("EventType") == 0 and d.get("RemainList") == [], d)
    visit = player.data["VisitData"]
    check("10601 plain values mirror VisitData",
          all(d[k] == visit[k] for k in ("Fate", "Vitality", "PraiseGuide")),
          (d, visit))
    import datetime
    datetime.datetime.strptime(d["FateTime"], "%Y-%m-%d %H:%M:%S")
    cd = int(v2configs.env_value("VitalityCD") or 1800)
    check("10601 timestamps use the client's formats",
          0 <= d["VitalityTime"] < cd, (d["FateTime"], d["VitalityTime"]))
    check("10601 response is json-serializable",
          json.loads(json.dumps(d)) == d)


def test_10603_10604_10605_10606_mutate_visit_data() -> None:
    player = make_player()
    store = MemStore()
    max_vitality = int(v2configs.vip_row(0).get("MaxVitality") or 0)
    recovered = handlers.action_visit_recover({"Num": 1}, player, None, store)
    check("10603 refills Vitality to the config maximum",
          recovered.get("RecoverVitality") == max_vitality
          and player.data["VisitData"]["Vitality"] == max_vitality, recovered)
    synced = handlers.action_visit_sync({}, player, None, store)
    cd = int(v2configs.env_value("VitalityCD") or 1800)
    check("10604 echoes Fate/Vitality and refreshes timestamps",
          synced.get("Fate") == 0 and synced.get("Vitality") == max_vitality
          and 0 <= synced["VitalityTime"] < cd
          and isinstance(synced["FateTime"], str), synced)
    transferred = handlers.action_visit_transfer({"Num": 5}, player, None, store)
    max_transfer = int(v2configs.vip_row(0).get("TransferNum") or 0)
    check("10605 TransferNum is capped at TcVIP[VipLv].TransferNum",
          transferred.get("TransferNum") == max_transfer
          and player.data["VisitData"]["TransferNum"] == max_transfer, transferred)
    status = handlers.action_visit_status({"Status": 3}, player, None, store)
    check("10606 echoes Status and keeps VisitData",
          status.get("Status") == 3 and player.data["VisitData"]["Status"] == 3, status)
    check("all four mutating find handlers persisted state", store.saved == 4, store.saved)


def test_stale_epoch_visit_data_is_converted() -> None:
    """The exact persisted devtest row that crashed Find on 2026-11-18."""
    player = make_player("stale_find")
    player.data["VisitData"] = {
        "Vitality": 3, "Fate": 0, "FateTime": 1794941131,
        "VitalityTime": 1794941131, "TransferNum": 0, "PraiseGuide": 0,
    }
    d = handlers.action_visit_info({}, player, None, MemStore())
    visit = d["VisitData"]
    import datetime
    datetime.datetime.strptime(visit["FateTime"], "%Y-%m-%d %H:%M:%S")
    check("stale epoch FateTime becomes a parseable string",
          isinstance(visit["FateTime"], str), visit["FateTime"])
    check("stale epoch VitalityTime becomes a small in-range int",
          0 <= visit["VitalityTime"] < int(v2configs.env_value("VitalityCD") or 1800),
          visit["VitalityTime"])
    check("stale VisitData is not mutated on the player",
          player.data["VisitData"]["FateTime"] == 1794941131,
          player.data["VisitData"])


def test_10307_jump_conf_map() -> None:
    """_itemMod.action10307 replaces its jump table with response.JumpConfMap."""
    check("10307 has a dedicated handler",
          handlers.HANDLERS.get("10307") is handlers.action_item_jump,
          handlers.HANDLERS.get("10307"))
    d = handlers.action_item_jump({"ItemId": 563}, make_player(), None, MemStore())
    check("10307 returns a real JumpConfMap table (never nil)",
          isinstance(d.get("JumpConfMap"), dict) and d["JumpConfMap"] == {},
          d)


def test_all_find_actions_are_routed() -> None:
    for action in ("10601", "10602", "10603", "10604", "10605", "10606"):
        check(f"{action} is mapped in HANDLERS", action in handlers.HANDLERS,
              sorted(handlers.HANDLERS))


def main() -> None:
    test_10602_visit_data_shape()
    test_10601_top_level_fields()
    test_10603_10604_10605_10606_mutate_visit_data()
    test_stale_epoch_visit_data_is_converted()
    test_10307_jump_conf_map()
    test_all_find_actions_are_routed()
    print(f"\n{sum(OK)}/{len(OK)} checks passed")
    sys.exit(0 if all(OK) else 1)


if __name__ == "__main__":
    main()
