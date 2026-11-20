"""Regression tests for the isolated alliance cluster (11601-11649 remainder).

Response field sets are the static fields extracted from the v2.005 client
callbacks in _allianceMod.lua; numeric expectations come only from the bundled
original TcAlliance* config tables.  No ADB/device tests, no server restart.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers, v2configs
from gos.defaults import default_player
from gos.handlers_alliance_more import (
    ALLIANCE_MORE_HANDLERS,
    action_alliance_donate,
    action_alliance_exchange,
    action_alliance_store_info,
    action_alliance_task_info,
    action_alliance_task_claim,
    action_alliance_trade_start,
    action_alliance_transfer_leader,
)
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else str(detail))


def _stub():
    return SimpleNamespace(save=lambda p: None), None


def _player(uid="alliancetest") -> Player:
    return Player(uid=uid, data=default_player(uid),
                  path=Path(tempfile.mkdtemp()) / "p.json")


# Minimal request payloads for the static shape sweep.
PAYLOADS = {
    "11604": {"AllianceGuid": "a1", "NewName": "X"},
    "11605": {"AllianceGuid": "a1", "FaceBook": "", "OutsideNotice": "", "InsideNotice": ""},
    "11606": {"AllianceGuid": "a1"},
    "11608": {"AllianceGuid": "a1", "NeedCheck": 0},
    "11609": {"AllianceGuid": "a1", "ApplyRid": "99", "IsAgree": 1},
    "11610": {"AllianceGuid": "a1", "RefuseList": []},
    "11611": {"AllianceGuid": "a1", "OperateId": "alliancetest", "Position": 3},
    "11612": {"AllianceGuid": "a1"},
    "11613": {"AllianceGuid": "a1", "Password": ""},
    "11614": {"AllianceGuid": "a1"},
    "11615": {"AllianceGuid": "a1", "ContributionType": 1},
    "11616": {"AllianceGuid": "a1", "ItemId": 102},
    "11618": {"AllianceGuid": "a1"},
    "11619": {"AllianceGuid": "a1"},
    "11620": {"AllianceGuid": "a1", "TradeLv": 1, "UseGold": 0},
    "11621": {"AllianceGuid": "a1", "TradeId": 1},
    "11622": {"AllianceGuid": "a1", "TradeId": 1, "CounsellorId": 2, "UseItem": 0, "TradeLv": 1},
    "11623": {"AllianceGuid": "a1", "TradeId": 1},
    "11624": {"allianceId": 1, "bossLv": 1},
    "11625": {"allianceId": 1, "bossLv": 1},
    "11626": {"AllianceGuid": "a1", "TransferRid": "2", "Password": ""},
    "11627": {"OprType": 1},
    "11628": {"AllianceGuid": "a1", "flag": True, "NewPassword": "x"},
    "11631": {"AllianceGuid": "a1", "FlagId": 2},
    "11632": {"AllianceGuid": "a1", "FlagId": 2},
    "11635": {"AllianceGuid": "a1", "DeviceId": "d", "Text": "hi"},
    "11640": {"AllianceGuid": "a1"},
    "11641": {"Stage": 1, "AllianceGuid": "a1", "TaskId": 1},
    "11642": {"AllianceGuid": "a1", "TaskId": 1},
    "11646": {"AllianceGuid": "a1", "GiftId": 7, "GiftList": []},
    "11647": {"allianceGuid": "a1", "packageId": 1},
    "11648": {"allianceGuid": "a1"},
    "11649": {"AllianceGuid": "a1", "Index": 1},
}

# Fields the decompiled response callback actually reads (extract_fields.py).
REQUIRED = {
    "11604": [], "11605": ["HintData", "NoticeChangeFreeTimes", "IsNeedPopup"],
    "11606": ["ApplyMember"], "11608": [], "11609": [], "11610": [],
    "11611": [], "11612": [], "11613": [],
    "11614": ["BatchSendingStatus", "MemberList", "NoticeChangeFreeTimes", "NoticeChangePrice"],
    "11615": ["AllianceLv", "ContributionTime", "ContributionNum", "ContributionTop",
              "AllianceExp", "AllianceFortune"],
    "11616": ["ExchangeItems", "ContributionNum"], "11618": [],
    "11619": ["Awards", "ContributionNum", "ContributionTop", "TradeCounsellors",
              "TradeData", "TradeLevels"],
    "11620": ["AllianceFortune", "TradeId"], "11621": ["TradeJoinData"],
    "11622": ["NextPointSeconds", "Point", "Status", "TradeCounsellors", "TradeLevels", "TradeValue"],
    "11623": [], "11624": [], "11625": ["recordList"], "11626": ["Position"],
    "11627": [], "11628": [], "11631": [], "11632": [],
    "11635": ["BatchSendingStatus"], "11640": ["AllianceTaskInfo"], "11641": [],
    "11642": ["TaskDetail"],
    "11646": ["AddPackCfg", "CurrentPackStatus", "RemainListCfg", "Status"],
    "11647": ["AllianceFortune", "CurrentPurchasedNum", "Status"],
    "11648": ["AlliancePackCfg", "CashPackCfg", "CurrentPurchasedNum", "AlliancePackClaimNeed",
              "CashPackClaimNeed", "PurchaseCreditNeed"],
    "11649": [],
}


def main() -> None:
    store, session = _stub()

    # Isolate the shared alliance registry from the real data dir.
    original_file = handlers._alliance_file
    tmp_registry = Path(tempfile.mkdtemp()) / "alliances.json"
    handlers._alliance_reset(tmp_registry)

    try:
        check("33 handlers exported (7 remain in handlers.py)",
              len(ALLIANCE_MORE_HANDLERS) == 33, len(ALLIANCE_MORE_HANDLERS))
        check("no collision with the 7 existing alliance actions",
              not (set(ALLIANCE_MORE_HANDLERS) &
                   {"11601", "11602", "11603", "11607", "11617", "11629", "11645"}))

        # ---- static shape sweep (member state) ----
        p = _player()
        handlers.action_alliance_create({"Name": "ShapeTest"}, p, session, store)
        for action, fn in sorted(ALLIANCE_MORE_HANDLERS.items()):
            resp = fn(dict(PAYLOADS[action]), p, session, store)
            present = all(f in resp for f in REQUIRED[action])
            types_ok = (
                isinstance(resp.get("ApplyMember"), list)
                and isinstance(resp.get("MemberList"), list)
                and isinstance(resp.get("recordList"), list)
                and isinstance(resp.get("TaskDetail"), list)
                and isinstance(resp.get("TradeJoinData"), list)
                and isinstance(resp.get("ExchangeItems"), dict)
                and isinstance(resp.get("TradeCounsellors"), dict)
                and isinstance(resp.get("TradeData"), dict)
                and isinstance(resp.get("Awards"), dict)
                and isinstance(resp.get("CurrentPackStatus"), dict)
                and isinstance(resp.get("AddPackCfg"), list)
                and isinstance(resp.get("RemainListCfg"), list)
                and isinstance(resp.get("CashPackCfg"), dict)
                and isinstance(resp.get("AlliancePackCfg"), dict)
                and isinstance(resp.get("CurrentPurchasedNum"), dict)
            )
            check(f"{action} response has all callback fields", present,
                  (action, sorted(resp), REQUIRED[action]))
            check(f"{action} list/map shapes are Lua-safe", types_ok, resp)

        # ---- no-alliance safety: same shapes, no crash, no bare {} ----
        lone = _player("lonely")
        for action in ("11615", "11616", "11619", "11620", "11626", "11640", "11647"):
            resp = ALLIANCE_MORE_HANDLERS[action](dict(PAYLOADS[action]), lone, session, store)
            check(f"{action} no-alliance keeps its shape",
                  all(f in resp for f in REQUIRED[action]), (action, resp))

        # ---- 11615 donate: TcAllianceContribution values ----
        p2 = _player("donor")
        handlers.action_alliance_create({"Name": "DonateTest"}, p2, session, store)
        cfg = next(r for r in v2configs.load("TcAllianceContribution")
                   if int(r["ContributionType"]) == 1)
        gold_before = int(p2.data["RoleData"]["Gold"])
        d = action_alliance_donate({"ContributionType": 1}, p2, session, store)
        check("11615 fields complete",
              all(k in d for k in REQUIRED["11615"]), d)
        check("11615 consumes config ConsumeItem from Gold",
              p2.data["RoleData"]["Gold"] == gold_before - int(cfg["ConsumeItem"][1]),
              p2.data["RoleData"]["Gold"])
        check("11615 ContributionNum from config GainPersonalDonate",
              d["ContributionNum"] == int(cfg["GainPersonalDonate"]), d)
        check("11615 AllianceFortune/Exp from config",
              d["AllianceFortune"] == int(cfg["GainFortune"])
              and d["AllianceExp"] == int(cfg["GainExp"]), d)
        check("11615 ContributionTime is a date string",
              isinstance(d["ContributionTime"], str) and len(d["ContributionTime"]) >= 19,
              d["ContributionTime"])
        remain = {int(e["ItemId"]): int(e["Num"]) for e in d.get("RemainList", [])}
        check("11615 RemainList carries the changed Gold only",
              remain.get(1) == p2.data["RoleData"]["Gold"] and set(remain) == {1}, remain)
        # Unaffordable type-5 (bag item 513) must refuse without changing state.
        before = d["ContributionNum"]
        d5 = action_alliance_donate({"ContributionType": 5}, p2, session, store)
        check("11615 unaffordable donate is a safe refusal",
              d5["ContributionNum"] == before, d5)

        # ---- 11616 exchange: TcAllianceExchange Consume/NumLimit ----
        ex = next(r for r in v2configs.load("TcAllianceExchange")
                  if int(r["ItemId"]) == 102 and int(r["AllianceLv"]) == 1)
        e1 = action_alliance_exchange({"ItemId": 102}, p2, session, store)
        check("11616 spends config Consume from ContributionNum",
              e1["ContributionNum"] == before - int(ex["Consume"]), e1)
        check("11616 ExchangeItems maps itemId to count",
              e1["ExchangeItems"].get("102") == 1, e1)
        e2 = action_alliance_exchange({"ItemId": 102}, p2, session, store)
        check("11616 respects config NumLimit (idempotent refusal)",
              e2["ExchangeItems"].get("102") == 1
              and e2["ContributionNum"] == e1["ContributionNum"], e2)

        # ---- 11620 trade start: TcAllianceTrade NeedGold/NeedFortune ----
        p3 = _player("trader")
        handlers.action_alliance_create({"Name": "TradeTest"}, p3, session, store)
        tcfg = next(r for r in v2configs.load("TcAllianceTrade") if int(r["Lv"]) == 1)
        guid = p3.data["RoleBase"]["AllianceGuid"]
        alliance = handlers._alliance_registry()["items"][guid]
        alliance["AllianceFortune"] = 1000
        gold0 = int(p3.data["RoleData"]["Gold"])
        t = action_alliance_trade_start({"TradeLv": 1, "UseGold": 0}, p3, session, store)
        check("11620 TradeId is generated locally",
              t["TradeId"] == 1, t)
        check("11620 spends config NeedGold/NeedFortune",
              p3.data["RoleData"]["Gold"] == gold0 - int(tcfg["NeedGold"])
              and t["AllianceFortune"] == 1000 - int(tcfg["NeedFortune"]), t)

        # ---- 11626 transfer leader: TcAlliancePosition ----
        p4 = _player("leader")
        handlers.action_alliance_create({"Name": "LeaderTest"}, p4, session, store)
        pos = action_alliance_transfer_leader({"TransferRid": "2"}, p4, session, store)
        rows = v2configs.load("TcAlliancePosition")
        leader = next(int(r["PositionLv"]) for r in rows if int(r["TransferPosition"]) == 1)
        check("11626 Position is config-derived (former leader -> deputy)",
              pos["Position"] == leader - 1
              and any(int(r["PositionLv"]) == leader - 1 for r in rows), (pos, rows))

        # ---- 11640/11641 tasks: TcAllianceDailyTaskCfg ----
        p5 = _player("tasker")
        handlers.action_alliance_create({"Name": "TaskTest"}, p5, session, store)
        info = action_alliance_task_info({}, p5, session, store)["AllianceTaskInfo"]
        check("11640 AllianceTaskInfo is a list of task dicts",
              isinstance(info, list) and info
              and all(isinstance(t, dict) for t in info), info)
        type1 = info[0]
        check("11640 TaskType/AwardRequireNums from config",
              type1["TaskType"] == 1
              and type1["AwardRequireNums"] == [20, 40, 80, 140, 200], type1)
        cfg1 = next(r for r in v2configs.load("TcAllianceDailyTaskCfg")
                    if int(r["TaskType"]) == 1 and int(r["TaskStage"]) == 1)
        counts_before = {int(e["ItemId"]): int(e["Num"]) for e in p5.data["RoleItem"]}
        action_alliance_task_claim({"TaskId": 1, "Stage": 1}, p5, session, store)
        counts_after = {int(e["ItemId"]): int(e["Num"]) for e in p5.data["RoleItem"]}
        expected_gain = {int(item): int(num) for item, num in cfg1["AwardItem"]}
        check("11641 grants only config AwardItem values",
              all(counts_after.get(i, 0) - counts_before.get(i, 0) == n
                  for i, n in expected_gain.items()), (counts_before, counts_after, expected_gain))
        action_alliance_task_claim({"TaskId": 1, "Stage": 1}, p5, session, store)
        counts_again = {int(e["ItemId"]): int(e["Num"]) for e in p5.data["RoleItem"]}
        check("11641 claim is idempotent",
              counts_again == counts_after, (counts_after, counts_again))

        # ---- 11648 store config nested shape ----
        store_resp = action_alliance_store_info({}, _player("shopper"), session, store)
        cp = store_resp["CurrentPurchasedNum"]
        check("11648 CurrentPurchasedNum has both pack tabs as maps",
              set(cp) == {"CashPack", "AlliancePack"}
              and isinstance(cp["CashPack"], dict) and isinstance(cp["AlliancePack"], dict), cp)
    finally:
        handlers._alliance_reset(original_file)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok), "alliance cluster test failed"


if __name__ == "__main__":
    main()
