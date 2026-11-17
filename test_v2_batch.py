"""In-memory tests for the second handler batch (client 2.0.05).

Covers 10302 (item use), 11502/11507 (check-in + cumulative claim),
10102/10103/10106 (counsellor talent/peerage/skin) and 10902/10903/10904
(school completion + capacity).  Every expected value is taken from the v2
client configs parsed by gos.v2configs - nothing is hardcoded from memory.

Same harness as test_remain_list.py: no network, no live DB, no files
written outside the OS temp dir.
"""
from __future__ import annotations

import datetime
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers, v2configs
from gos.defaults import default_player
from gos.store import Player, Session

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def make_player(uid: str = "batch_test") -> Player:
    data = default_player(uid, "BatchTest")
    tmp = Path(tempfile.mkdtemp(prefix="gos_test_")) / f"{uid}.json"
    return Player(uid=uid, data=data, path=tmp)


def make_session(player: Player) -> Session:
    return Session(jwt="test-jwt", user_id=player.uid, sk="test-key",
                   keypair=None, player_id=player.uid, server_id=1, app_id=1020)


class MemStore:
    def __init__(self) -> None:
        self.saved: list[str] = []

    def save(self, player: Player) -> None:
        self.saved.append(player.uid)


def remain_map(data: dict) -> dict:
    return {entry["ItemId"]: entry["Num"] for entry in data.get("RemainList", [])}


def give(player: Player, item_id: int, num: int) -> None:
    handlers._role_item_add(player, item_id, num)


def counsellor(player: Player, cid: int) -> dict:
    return next(c for c in player.data["RoleCounsellor"] if c["CounsellorId"] == cid)


# --------------------------------------------------------------------------
# 10302 item use
# --------------------------------------------------------------------------
def test_item_use_resource_coupon() -> None:
    # TcItem Id 102 "Gold Coupon": Type1=2 Type2=2 AddNum=100000 -> Silver
    cfg = v2configs.item_by_id(102)
    add = int(cfg["AddNum"])
    player = make_player("use_coupon")
    give(player, 102, 3)
    silver_before = player.data["RoleData"]["Silver"]
    store = MemStore()
    d = handlers.action_item_use({"ItemId": 102, "UseNum": 2}, player,
                                 make_session(player), store)
    check("10302 deducts the used item",
          handlers._role_item_count(player, 102) == 1, d)
    check("10302 grants AddNum*UseNum",
          player.data["RoleData"]["Silver"] == silver_before + add * 2,
          (player.data["RoleData"]["Silver"], silver_before, add))
    rem = remain_map(d)
    check("10302 RemainList has the new resource value",
          rem.get(2) == player.data["RoleData"]["Silver"], rem)
    check("10302 RemainList has the item's new count",
          rem.get(102) == 1, rem)
    check("10302 saved", "use_coupon" in store.saved)


def test_item_use_union_chest() -> None:
    # TcItem Id 113: Type1=2 Type2=12 AddNum=100; the client's 10302 callback
    # shows +AddNum*UseNum of item 11 and item 12.
    cfg = v2configs.item_by_id(113)
    add = int(cfg["AddNum"])
    player = make_player("use_chest")
    give(player, 113, 1)
    store = MemStore()
    d = handlers.action_item_use({"ItemId": 113, "UseNum": 1}, player,
                                 make_session(player), store)
    check("10302 chest consumed", 113 not in [e["ItemId"] for e in player.data["RoleItem"]], d)
    check("10302 chest grants item 11", handlers._role_item_count(player, 11) == add, d)
    check("10302 chest grants item 12", handlers._role_item_count(player, 12) == add, d)
    rem = remain_map(d)
    check("10302 chest RemainList carries 11/12",
          rem.get(11) == add and rem.get(12) == add, rem)


def test_item_use_refuses_unverified_effects() -> None:
    # Item 401 (Type1=5 VIP card): no verified v2 effect -> refuse untouched.
    player = make_player("use_vip")
    give(player, 401, 2)
    d = handlers.action_item_use({"ItemId": 401, "UseNum": 1}, player,
                                 make_session(player), MemStore())
    check("10302 refuses complex item", d.get("_fail"), d)
    check("10302 refusal keeps the item", handlers._role_item_count(player, 401) == 2, d)
    check("10302 refusal has no RemainList", "RemainList" not in d, d)
    d = handlers.action_item_use({"ItemId": 102, "UseNum": 5}, player,
                                 make_session(player), MemStore())
    check("10302 refuses when short", d.get("_fail"), d)


# --------------------------------------------------------------------------
# 11502 / 11507
# --------------------------------------------------------------------------
def test_sign_checkin_shape() -> None:
    # defaults.py seeds CheckInDay=1 in the login state, so the first
    # check-in lands on cycle day 2 (CheckInDay counts signed days).
    player = make_player("sign_day")
    store = MemStore()
    d = handlers.action_sign_checkin({}, player, make_session(player), store)
    check("11502 advances the day", d["CheckInDay"] == 2
          and player.data["CheckInDay"] == 2, d)
    rows = len(v2configs.cumulative_reward_rows())
    check("11502 CumulativeList has one flag per config row",
          d["CumulativeList"] == [0] * rows, d["CumulativeList"])
    rec = d["CheckInRecord"][0]
    check("11502 record carries the signScene keys",
          rec.get("CheckTypeId") == 10 and isinstance(rec.get("TodayAward"), list)
          and isinstance(rec.get("CheckInRefreshTime"), str), rec)
    check("11502 keeps CheckTypeId/CheckInReFreshTime",
          d["CheckTypeId"] == 10 and "CheckInReFreshTime" in d, d)
    check("11502 includes RemainList", "RemainList" in d, d)
    check("11502 saved", "sign_day" in store.saved)
    # 21-day cycle: CheckInDay 21 -> next check-in wraps to sign day 1.
    # The daily guard now requires a new UTC+3 game day, so simulate that by
    # moving the stored last-check-in timestamp back one day.
    player.data["CheckInDay"] = 21
    player.data["CumulativeList"] = [1, 1, 1, 1]
    player.data["CheckInRefreshTime"] = (
        handlers._game_time() - datetime.timedelta(days=1)
    ).strftime(handlers._GAME_DATE_FMT)
    d = handlers.action_sign_checkin({}, player, make_session(player), store)
    check("11502 wraps the 21-day cycle", d["CheckInDay"] == 22
          and handlers._sign_day(22) == 1, d)
    check("11502 resets milestones on wrap", d["CumulativeList"] == [0, 0, 0, 0], d)


def test_sign_cumulative_claim() -> None:
    rows = v2configs.cumulative_reward_rows()
    days = int(rows[0]["CumulativeDays"])
    rewards = rows[0]["CumulativeRewards"][0]
    player = make_player("sign_cum")
    player.data["CheckInDay"] = days  # reached the first milestone
    give(player, 210, 0)  # ensure the bag list exists
    store = MemStore()
    d = handlers.action_sign_cumulative({"CumuDay": 1}, player,
                                        make_session(player), store)
    rem = remain_map(d)
    ok = True
    for item_id, num in rewards:
        ok = ok and handlers._role_item_count(player, int(item_id)) == int(num)
        ok = ok and rem.get(int(item_id)) == int(num)
    check("11507 grants the config rewards", ok, (rewards, rem))
    check("11507 flags the milestone", d["CumulativeList"][0] == 1, d)
    check("11507 keeps the other flags", d["CumulativeList"][1:] == [0, 0, 0], d)
    d = handlers.action_sign_cumulative({"CumuDay": 1}, player,
                                        make_session(player), store)
    check("11507 refuses a second claim", d.get("_fail"), d)
    player.data["CheckInDay"] = days - 1
    player2_flag = handlers._cumulative_flags(player)
    player2_flag[1] = 0
    player.data["CumulativeList"] = player2_flag
    d = handlers.action_sign_cumulative({"CumuDay": 2}, player,
                                        make_session(player), store)
    check("11507 refuses an unreached milestone", d.get("_fail"), d)
    d = handlers.action_sign_cumulative({"CumuDay": 99}, player,
                                        make_session(player), store)
    check("11507 refuses a bad box id", d.get("_fail"), d)


# --------------------------------------------------------------------------
# 10102 talent book upgrade
# --------------------------------------------------------------------------
def test_talent_upgrade_book_exp_path() -> None:
    # UpgradeType 2: talent 2 (TcTalent StarLevel 2) consumes
    # TcStar[2].ConsumeExp = 400 book exp and always succeeds.
    talent = v2configs.talent_by_id(2)
    star = v2configs.star_by_level(int(talent["StarLevel"]))
    need = int(star["ConsumeExp"])
    player = make_player("talent_exp")
    c = counsellor(player, 1)
    c["AptitudeExp"] = need + 100
    store = MemStore()
    d = handlers.action_counsellor_talent(
        {"CounsellorId": 1, "TalentId": 2, "UpgradeType": 2}, player,
        make_session(player), store)
    t = next(t for t in c["Talent"] if t[0] == 2)
    check("10102 levels the talent", t[1] == 2 and d["TalentLv"] == 2, (t, d))
    check("10102 deducts ConsumeExp", c["AptitudeExp"] == 100, c)
    check("10102 echoes the new AptitudeExp", d["AptitudeExp"] == 100, d)
    d = handlers.action_counsellor_talent(
        {"CounsellorId": 1, "TalentId": 2, "UpgradeType": 2}, player,
        make_session(player), store)
    check("10102 refuses without book exp", d.get("_fail"), d)
    check("10102 refusal leaves the talent", next(t for t in c["Talent"] if t[0] == 2)[1] == 2, c)


def test_talent_upgrade_item_paths() -> None:
    talent = v2configs.talent_by_id(2)
    star = v2configs.star_by_level(int(talent["StarLevel"]))
    scroll = int(star["ConsumeScroll"])
    book_id = int(talent["TalentType"]) + 500  # UpgradeType 1 book
    player = make_player("talent_item")
    give(player, book_id, 3)
    give(player, 581, 2)
    store = MemStore()
    saved_roll = handlers._roll
    handlers._roll = lambda pct: False  # force the probability roll to fail
    try:
        d = handlers.action_counsellor_talent(
            {"CounsellorId": 1, "TalentId": 2, "UpgradeType": 1}, player,
            make_session(player), store)
    finally:
        handlers._roll = saved_roll
    c = counsellor(player, 1)
    t = next(t for t in c["Talent"] if t[0] == 2)
    check("10102 failed roll keeps the level", t[1] == 1 and d["TalentLv"] == 1, (t, d))
    check("10102 failed roll still consumes the book",
          handlers._role_item_count(player, book_id) == 3 - scroll, d)
    rem = remain_map(d)
    check("10102 RemainList carries the consumed book", rem.get(book_id) == 3 - scroll, rem)
    # UpgradeType 3 always succeeds and consumes item 581
    d = handlers.action_counsellor_talent(
        {"CounsellorId": 1, "TalentId": 2, "UpgradeType": 3}, player,
        make_session(player), store)
    t = next(t for t in c["Talent"] if t[0] == 2)
    check("10102 universal book succeeds", t[1] == 2 and d["TalentLv"] == 2, (t, d))
    check("10102 universal book consumed",
          handlers._role_item_count(player, 581) == 2 - scroll, d)
    # cap gate: TcPeeragesUpgrade[row(Peerages=1)].TalentLvLimit
    rows = v2configs.peerage_rows()
    limit = int(rows[0]["TalentLvLimit"])
    t[1] = limit
    d = handlers.action_counsellor_talent(
        {"CounsellorId": 1, "TalentId": 2, "UpgradeType": 3}, player,
        make_session(player), store)
    check("10102 refuses at TalentLvLimit", d.get("_fail"), d)
    check("10102 cap refusal consumes nothing",
          handlers._role_item_count(player, 581) == 2 - scroll, d)


# --------------------------------------------------------------------------
# 10103 peerage ladder
# --------------------------------------------------------------------------
def test_peerage_upgrade() -> None:
    rows = v2configs.peerage_rows()
    nxt = rows[1]  # TcPeeragesUpgrade[2]: Peerages 2, ConsumeItem, Award
    costs = [(int(p[0]), int(p[1])) for p in nxt["ConsumeItem"]]
    award_exp = int(nxt["Award"][1])
    player = make_player("peerage")
    for item_id, num in costs:
        give(player, item_id, num)
    store = MemStore()
    d = handlers.action_counsellor_peerage({"CounsellorId": 1}, player,
                                           make_session(player), store)
    c = counsellor(player, 1)
    check("10103 advances Peerages", d["Peerages"] == int(nxt["Peerages"])
          and c["Peerages"] == int(nxt["Peerages"]), (d, c))
    check("10103 awards book exp", c["AptitudeExp"] == award_exp, c)
    ok = all(handlers._role_item_count(player, item_id) == 0 for item_id, _ in costs)
    check("10103 consumes the config items", ok, [handlers._role_item_count(player, i) for i, _ in costs])
    rem = remain_map(d)
    check("10103 RemainList carries the consumed items",
          all(rem.get(item_id) == 0 for item_id, _ in costs), rem)
    # missing items -> refusal, nothing changes
    player.data["RoleCounsellor"][0]["Peerages"] = 1
    player.data["RoleCounsellor"][0]["AptitudeExp"] = 0
    d = handlers.action_counsellor_peerage({"CounsellorId": 1}, player,
                                           make_session(player), store)
    check("10103 refuses without items", d.get("_fail"), d)
    check("10103 refusal keeps Peerages", counsellor(player, 1)["Peerages"] == 1, c)
    check("10103 refusal keeps AptitudeExp", counsellor(player, 1)["AptitudeExp"] == 0, c)
    # break step: same Peerages, raised LvLimit -> response carries BreakLv
    # (TcPeeragesUpgrade row 9: Peerages 8, LvLimit 410, ConsumeItem 583x10)
    break_row = next(r for r in rows
                     if int(r["Peerages"]) == int(rows[7]["Peerages"])
                     and int(r["LvLimit"]) > int(rows[7]["LvLimit"]))
    player.data["RoleCounsellor"][0]["Peerages"] = int(rows[7]["Peerages"])
    player.data["RoleCounsellor"][0]["Lv"] = int(rows[7]["LvLimit"])
    player.data["RoleCounsellor"][0]["AptitudeExp"] = 0
    give(player, int(break_row["ConsumeItem"][0][0]), int(break_row["ConsumeItem"][0][1]))
    d = handlers.action_counsellor_peerage({"CounsellorId": 1}, player,
                                           make_session(player), store)
    c = counsellor(player, 1)
    check("10103 break step keeps the peerage",
          d["Peerages"] == int(break_row["Peerages"]) == c["Peerages"], (d, c))
    check("10103 break step reports the new LvLimit as BreakLv",
          d.get("BreakLv") == int(break_row["LvLimit"]), d)
    check("10103 break step awards book exp",
          c["AptitudeExp"] == int(break_row["Award"][1]), c)
    check("10103 break step consumes its item",
          handlers._role_item_count(player, int(break_row["ConsumeItem"][0][0])) == 0, d)


# --------------------------------------------------------------------------
# 10106 skin dress
# --------------------------------------------------------------------------
def test_counsellor_skin() -> None:
    player = make_player("skin")
    store = MemStore()
    d = handlers.action_counsellor_skin({"CounsellorId": 2, "SkinId": 2}, player,
                                        make_session(player), store)
    c = counsellor(player, 2)
    check("10106 dresses the owned skin", d == {} and c["DressSkin"]["SkinId"] == 2, (d, c))
    check("10106 keeps SkinLv from HaveSkinId", c["DressSkin"]["SkinLv"] == 0, c)
    d = handlers.action_counsellor_skin({"CounsellorId": 2, "SkinId": 1601}, player,
                                        make_session(player), store)
    check("10106 refuses an unowned skin", d.get("_fail"), d)
    check("10106 refusal keeps DressSkin", counsellor(player, 2)["DressSkin"]["SkinId"] == 2, c)


# --------------------------------------------------------------------------
# 10902 / 10903 / 10904
# --------------------------------------------------------------------------
def _skill_need(level: int) -> int:
    # Independent copy of _ministerMod.getSkillLvExp for test verification.
    m = (int(level) + 1) // 2
    return int(0.03 * m * m + 1.03 * m + 1 + 0.5)


def test_school_complete() -> None:
    cd = int(v2configs.env_value("StudyCd", 10800))
    book_gain = int(v2configs.env_value("StudyBookExp", 10))
    skill_gain = int(v2configs.env_value("StudySkillExp", 100))
    player = make_player("school_done")
    store = MemStore()
    # still studying -> refusal
    handlers.action_school_assign({"PositionAndMinisterId": [{"MinisterId": 1, "Position": 1}]},
                                  player, make_session(player), store)
    d = handlers.action_school_complete({"CounsellorId": 1}, player,
                                        make_session(player), store)
    check("10902 refuses an unfinished study", d.get("_fail"), d)
    # finish the session
    player.data["School"]["Slots"]["1"]["StudyTime"] = int(time.time()) - cd - 1
    apt_before = counsellor(player, 1)["AptitudeExp"]
    skill_before = counsellor(player, 1)["SkillExp"]
    skill_lv_before = counsellor(player, 1)["SkillLv"]
    d = handlers.action_school_complete({"CounsellorId": 1}, player,
                                        make_session(player), store)
    c = counsellor(player, 1)
    check("10902 grants StudyBookExp", c["AptitudeExp"] == apt_before + book_gain, c)
    # skill exp levels the skill while it covers getSkillLvExp(lv)
    lv, exp = int(skill_lv_before), skill_before + skill_gain
    while exp >= _skill_need(lv):
        exp -= _skill_need(lv)
        lv += 1
    check("10902 grants StudySkillExp and levels per getSkillLvExp",
          c["SkillLv"] == lv and c["SkillExp"] == exp, (c["SkillLv"], c["SkillExp"], lv, exp))
    check("10902 levels the skill", d["SkillLv"] == lv and d["SkillUp"] == 1, d)
    check("10902 echoes absolute BookExp", d["BookExp"] == c["AptitudeExp"], d)
    check("10902 echoes absolute SkillExp", d["SkillExp"] == c["SkillExp"], d)
    check("10902 clears the slot", player.data["School"]["Slots"] == {}, d)
    check("10902 includes RemainList", "RemainList" in d, d)
    # 10903 collects every finished session
    player2 = make_player("school_all")
    handlers.action_school_assign(
        {"PositionAndMinisterId": [{"MinisterId": 1, "Position": 1},
                                   {"MinisterId": 2, "Position": 2}]},
        player2, make_session(player2), store)
    now = int(time.time())
    for slot in player2.data["School"]["Slots"].values():
        slot["StudyTime"] = now - cd - 1
    d = handlers.action_school_complete_all({}, player2, make_session(player2), store)
    recs = d.get("CompleteCounsellors") or []
    check("10903 completes both counsellors",
          sorted(r["CounsellorId"] for r in recs) == [1, 2], d)
    check("10903 entries carry the callback fields",
          all(all(k in r for k in ("CounsellorId", "BookExp", "SkillExpAdd",
                                   "SkillExp", "SkillLv", "MiracleTrigger"))
              for r in recs), recs)
    check("10903 clears all slots", player2.data["School"]["Slots"] == {}, d)
    d = handlers.action_school_complete_all({}, player2, make_session(player2), store)
    check("10903 refuses when nothing is finished", d.get("_fail"), d)


def test_school_capacity() -> None:
    rows = v2configs.school_rows()
    nxt = next(r for r in rows if int(r["Capacity"]) == handlers.SCHOOL_CAPACITY + 1)
    cost = int(nxt["NeedGold"])
    player = make_player("school_cap")
    player.data["RoleData"]["Gold"] = cost
    store = MemStore()
    d = handlers.action_school_capacity({}, player, make_session(player), store)
    check("10904 buys the next slot", d["Capacity"] == int(nxt["Capacity"]), d)
    check("10904 deducts TcSchool.NeedGold",
          player.data["RoleData"]["Gold"] == 0, player.data["RoleData"])
    check("10904 persists the capacity",
          player.data["School"]["Capacity"] == int(nxt["Capacity"]), player.data["School"])
    d2 = handlers.action_school_list({}, player, make_session(player), store)
    check("10905 reports the purchased capacity", d2["Capacity"] == int(nxt["Capacity"]), d2)
    rem = remain_map(d)
    check("10904 RemainList has the new Gold", rem.get(1) == 0, rem)
    # cannot afford
    player2 = make_player("school_poor")
    player2.data["RoleData"]["Gold"] = cost - 1
    d = handlers.action_school_capacity({}, player2, make_session(player2), store)
    check("10904 refuses when short", d.get("_fail"), d)
    check("10904 refusal keeps the gold",
          player2.data["RoleData"]["Gold"] == cost - 1, player2.data["RoleData"])
    check("10904 refusal keeps the capacity",
          handlers._school_capacity(player2) == handlers.SCHOOL_CAPACITY, d)
    # max capacity
    player3 = make_player("school_max")
    player3.data["School"] = {"Capacity": int(rows[-1]["Capacity"]), "Slots": {}}
    d = handlers.action_school_capacity({}, player3, make_session(player3), store)
    check("10904 refuses at max capacity", d.get("_fail"), d)


def test_fail_envelope() -> None:
    """handle_forward must turn {"_fail": msg} into the v2 Code-1 envelope
    (net/dataNetMod.lua lines 1105-1119: prompt Info, skip the callback)."""
    from gos.crypto import b64e, decode_request_data, decode_response_data, rc4

    class FailStore(MemStore):
        def __init__(self, player: Player) -> None:
            super().__init__()
            self.player = player
            self.session = make_session(player)

        def find_session_for_data(self, jwt, data_b64):  # noqa: ARG002
            return self.session

        def get_or_create(self, uid):
            return self.player

        def touch(self, session):
            pass

    player = make_player("fail_env")
    give(player, 401, 1)
    body = {"jwt": "test-jwt", "server_id": 1, "app_id": 1020,
            "data": b64e(rc4("test-key", b'{"actionid": 10302, "ItemId": 401, "UseNum": 1}'))}
    outer = handlers.handle_forward(body, FailStore(player))
    inner = decode_response_data("test-key", outer["data"])
    check("Code 1 envelope on refusal", inner.get("Code") == 1 and "Info" in inner, inner)
    check("Code 1 has no Data", "Data" not in inner, inner)
    check("refusal kept the item", handlers._role_item_count(player, 401) == 1)
    # and a success keeps the plain-Data envelope
    give(player, 102, 1)
    body = {"jwt": "test-jwt", "server_id": 1, "app_id": 1020,
            "data": b64e(rc4("test-key", b'{"actionid": 10302, "ItemId": 102, "UseNum": 1}'))}
    outer = handlers.handle_forward(body, FailStore(player))
    inner = decode_response_data("test-key", outer["data"])
    check("Code 0 envelope on success", inner.get("Code") == 0 and "Data" in inner, inner)
    check("success payload carries RemainList", "RemainList" in inner["Data"], inner["Data"])
    check("success payload carries Extra.mainTask",
          isinstance(inner["Data"].get("Extra"), dict), inner["Data"])


def main() -> None:
    test_item_use_resource_coupon()
    test_item_use_union_chest()
    test_item_use_refuses_unverified_effects()
    test_sign_checkin_shape()
    test_sign_cumulative_claim()
    test_talent_upgrade_book_exp_path()
    test_talent_upgrade_item_paths()
    test_peerage_upgrade()
    test_counsellor_skin()
    test_school_complete()
    test_school_capacity()
    test_fail_envelope()
    if all(OK):
        print(f"ALL PASS ({len(OK)} checks)")
    else:
        print(f"FAILURES: {OK.count(False)}/{len(OK)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
