"""Regression tests for the ranking action set (11001-11007).

Grounded in the v2.0.05 decomp and the official v7 capture:
  modules/_rankingMod.lua                  action11001-11007 callbacks
  scene/rankingScene.rankingScene.lua      drawViewG row fields / tab lists
  scene/rankingScene.rankEntranceDialog.lua Top1 entrance fields
  recon/v7-capture/v7_findings.md          truncated 11001 + 11002 rewards

Run: python3 test_rankings.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from gos import handlers
from gos.defaults import default_player
from gos.store import Player

ok: list[bool] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player(uid: str, nick: str, power: int, chapter: int, sec: int, phase: int) -> Player:
    data = default_player(uid, nick)
    data["RoleBase"]["Rid"] = uid
    data["RoleBase"]["Nickname"] = nick
    data["RoleData"]["Power"] = power
    data["RoleSection"].update({"ChapterId": chapter, "SectionId": sec, "PhaseId": phase})
    return Player(uid=uid, data=data, path=Path("/tmp/opencode/gos_test_players/rank_%s.json" % uid))


def _fixture(intimacy: bool = False):
    requester = _player("rank_self", "SelfSultan", 1500, 2, 3, 4)
    rival = _player("rank_rival", "RivalSultan", 4200, 4, 1, 2)
    if intimacy:
        # real unlocked-wife style value (mirror of _wivesMod.get_total_intimacy)
        requester.data["RoleBeauty"] = {"1": {"Intimacy": 700}}
        rival.data["RoleBeauty"] = {"1": {"Intimacy": 950}}
    store = SimpleNamespace(_players={p.uid: p for p in (requester, rival)},
                            save=lambda p: None)
    session = SimpleNamespace(jwt="jwt", user_id=requester.uid)
    return requester, rival, store, session


# rankingScene.rankingScene.lua 406-854 reads these keys from each row
ROW_FIELDS = {
    "RoleId", "Nickname", "Sex", "Lv", "VipLv", "Avatar", "ChatAvatar",
    "Figure", "Dress", "TitleId", "TotalPower", "TotalSecRecord", "TotalIntimacy",
    # per-player identity fields from the official 11001 RoleSection rows
    "AvatarBox", "AvatarBoxEffect", "AvatarBoxData", "ItemTitleId",
    "RankBottomId", "GloryData", "FashionEffectInfo",
}
ENTRANCE_FIELDS = {"Name", "Power", "Lv", "Avatar", "Dress", "Figure", "ChatAvatar"}


def test_rank_info_lists_and_self() -> None:
    requester, rival, store, session = _fixture()
    d = handlers.action_rank_info({}, requester, session, store)
    for key in ("RolePower", "RoleSection", "BeautyIntimacy"):
        check("11001 %s is a list (not a scalar)" % key, isinstance(d.get(key), list),
              type(d.get(key)))
    check("11001 power rows sorted desc by real RoleData.Power",
          [r["TotalPower"] for r in d["RolePower"]] == [4200, 1500], d["RolePower"])
    check("11001 section rows sorted desc by TotalSecRecord",
          [r["TotalSecRecord"] for r in d["RoleSection"]] == [4102, 2304], d["RoleSection"])
    check("11001 requester included in every real list",
          all(any(r["RoleId"] == "rank_self" for r in d[k])
              for k in ("RolePower", "RoleSection")), d)
    check("11001 no fake intimacy rows without real values", d["BeautyIntimacy"] == [],
          d["BeautyIntimacy"])

    self_rank = d["RankSelf"]
    check("11001 RankSelf uses the keys rankingScene actually indexes",
          isinstance(self_rank, dict)
          and set(self_rank) == {"TotalPower", "Section", "Intimacy"}, self_rank)
    check("11001 RankSelf ranks are real positions / unranked sentinel 0",
          self_rank["TotalPower"] == 2 and self_rank["Section"] == 2
          and self_rank["Intimacy"] == 0, self_rank)
    check("11001 RankWorshipTime is a keyed dict of date strings",
          isinstance(d["RankWorshipTime"], dict)
          and all(isinstance(v, str) and "-" in v
                  for v in d["RankWorshipTime"].values()), d["RankWorshipTime"])
    check("11001 cross-tab rows are empty tables",
          d["RolePowerRank"] == [] and d["AllianceRank"] == [], d)


def test_rank_info_row_fields() -> None:
    requester, rival, store, session = _fixture(intimacy=True)
    d = handlers.action_rank_info({}, requester, session, store)
    for key in ("RolePower", "RoleSection", "BeautyIntimacy"):
        for row in d[key]:
            missing = sorted(ROW_FIELDS - set(row))
            check("11001 %s row has all scene/capture fields" % key, not missing, missing)
            check("11001 %s Dress/Box/Glory are tables" % key,
                  isinstance(row["Dress"], list)
                  and isinstance(row["AvatarBoxData"], dict)
                  and isinstance(row["FashionEffectInfo"], list)
                  and row["GloryData"] is None, row)
    check("11001 intimacy rows use real total intimacy",
          [r["TotalIntimacy"] for r in d["BeautyIntimacy"]] == [950, 700],
          d["BeautyIntimacy"])
    # _rankingMod.get_section (lines 119-153): chapter = floor(tsr/1000),
    # section = floor((tsr%%1000)/100/10*10) -> verify the inverse mapping
    for row in d["RoleSection"]:
        tsr = row["TotalSecRecord"]
        check("11001 TotalSecRecord %d decodes via get_section" % tsr,
              tsr // 1000 in (2, 4) and (tsr % 1000) // 100 in (1, 3), tsr)
    check("11001 response is json-serializable",
          json.loads(json.dumps(d, ensure_ascii=False)) == d)


def test_rank_top1() -> None:
    requester, rival, store, session = _fixture()
    d = handlers.action_rank_top1({}, requester, session, store)
    for key in ("Top1", "CrossTop1"):
        check("11005 %s is a dict with entrance fields" % key,
              isinstance(d.get(key), dict) and ENTRANCE_FIELDS <= set(d[key]), d.get(key))
    check("11005 Top1 uses the real highest-power player",
          d["Top1"]["Name"] == "RivalSultan" and d["Top1"]["Power"] == 4200, d["Top1"])
    check("11005 IsCrossRankOpen mirrors real dev flag",
          d["IsCrossRankOpen"] == int(requester.data.get("IsCrossRankOpen") or 0), d)


def test_rank_info_uses_stored_worship_dates() -> None:
    requester, rival, store, session = _fixture()
    stored = dict(requester.data["RankWorshipTime"])
    d = handlers.action_rank_info({}, requester, session, store)
    check("11001 RankWorshipTime serves the stored per-key dates",
          d["RankWorshipTime"] == stored, (d["RankWorshipTime"], stored))
    today = handlers._game_day_number(handlers._game_date_string())
    check("fresh account's stored dates are NOT today (button enabled)",
          all(handlers._game_day_number(v) != today
              for v in d["RankWorshipTime"].values()), d["RankWorshipTime"])
    # After a worship only that key changes, and 11001 reflects the new date.
    handlers.action_rank_worship({"WorshipType": 1}, requester, session, store)
    d2 = handlers.action_rank_info({}, requester, session, store)
    check("11001 reflects the stored date written by 11002",
          handlers._game_day_number(d2["RankWorshipTime"]["SectionRank"]) == today
          and d2["RankWorshipTime"]["PowerRank"] == stored["PowerRank"],
          d2["RankWorshipTime"])


def test_rank_worship_daily_gold_and_guard() -> None:
    requester, rival, store, session = _fixture()
    gold = int(requester.data["RoleData"]["Gold"])
    d = handlers.action_rank_worship({"WorshipType": 0}, requester, session, store)
    check("11002 grants +10 Gold (absolute RemainList first)",
          d.get("RemainList") == [{"ItemId": 1, "Num": gold + 10}]
          and requester.data["RoleData"]["Gold"] == gold + 10, d)
    check("11002 sends the +10 increment and MiracleTrigger false",
          d.get("IncrRemainList") == [{"ItemId": 1, "Num": 10}]
          and d.get("MiracleTrigger") is False, d)
    # Same game day -> refused, no second grant.
    again = handlers.action_rank_worship({"WorshipType": 0}, requester, session, store)
    check("11002 refuses a second worship on the same game day",
          "RemainList" not in again and "IncrRemainList" not in again
          and again.get("MiracleTrigger") is False, again)
    check("refused worship does not change Gold",
          requester.data["RoleData"]["Gold"] == gold + 10,
          requester.data["RoleData"]["Gold"])
    # A different worship key is independent (per-key guard).
    other = handlers.action_rank_worship({"WorshipType": 2}, requester, session, store)
    check("11002 other rank key is still collectable",
          other.get("RemainList") == [{"ItemId": 1, "Num": gold + 20}], other)
    # v7 types 3/16 have no v2 config mapping and always keep MiracleTrigger false.
    for worship_type in (3, 16):
        d = handlers.action_rank_worship({"WorshipType": worship_type},
                                         requester, session, store)
        check("11002 MiracleTrigger is false (type %s)" % worship_type,
              d.get("MiracleTrigger") is False, d)


def test_rank_other_actions_safe_shapes() -> None:
    requester, rival, store, session = _fixture()
    detail = handlers.action_rank_player_detail({"QueryId": "rank_rival"},
                                                requester, session, store)
    check("11003 known player returns a detail table", isinstance(detail, dict)
          and detail.get("RoleId") == "rank_rival", detail)
    check("11003 unknown player returns an empty table",
          handlers.action_rank_player_detail({"QueryId": "nobody"},
                                             requester, session, store) == {})

    cross = handlers.action_rank_cross_list({"RankType": 12}, requester, session, store)
    check("11004 cross lists are tables, never 0",
          all(isinstance(cross.get(k), list)
              for k in ("CrossPower", "CrossAlliancePower", "CrossIntimacy")), cross)
    check("11006/11007 return tables", isinstance(
        handlers.action_rank_alliance_detail({}, requester, session, store), dict)
        and isinstance(handlers.action_rank_alliance_rank_detail(
            {}, requester, session, store), dict))


def test_v2_red_point_keys_are_numeric() -> None:
    """v2 redPointMod / myWidget tab selectIndex does `0 < redPoint[key]`.

    A key missing from the map is nil and aborts the rankings showOverlay with
    "attempt to compare number with nil".  The v2 client uses its own key set
    (competitionRank, ranking, arena, ...) distinct from the v7 capture keys.
    """
    rp = handlers._client_red_point()
    for key in ("competitionRank", "ranking", "arena", "school", "vip",
                "boss", "dailyTask", "achievement"):
        check(f"red point {key!r} is numeric",
              isinstance(rp.get(key), int), rp.get(key))
    check("all red point values are numbers",
          all(isinstance(v, int) for v in rp.values()), rp)
    # redPointMod.updateRedPoint does string.find(key, ":") + 1 on any "uni*"
    # key; official keys are "uni:<allianceId>". A bare "uni" crashes the main
    # scene with "attempt to perform arithmetic on a nil value".
    check("no bare 'uni' red-point key (client expects 'uni:<id>')",
          not any(k.startswith("uni") for k in rp),
          [k for k in rp if k.startswith("uni")])
    requester, _rival, store, _session = _fixture()
    d = handlers.attach_task_progress(
        handlers.action_rank_info({}, requester, None, store), requester)
    check("rank response carries the full numeric red point map",
          isinstance(d.get("RedPoint"), dict)
          and all(isinstance(v, int) for v in d["RedPoint"].values()),
          d.get("RedPoint"))


def main() -> None:
    test_v2_red_point_keys_are_numeric()
    test_rank_info_lists_and_self()
    test_rank_info_row_fields()
    test_rank_top1()
    test_rank_info_uses_stored_worship_dates()
    test_rank_worship_daily_gold_and_guard()
    test_rank_other_actions_safe_shapes()
    print("\n%d/%d checks passed" % (sum(ok), len(ok)))
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
