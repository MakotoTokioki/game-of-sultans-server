"""Regression tests for the wives/harem cluster (10402-10412).

Response shapes come from the v2 client callbacks in modules/_wivesMod.lua
(verified with tools/lua_shape_harness/extract_fields.py); all values are
sourced from original v2 configs (TcBeauty, TcBeautySkillsUpgrade,
TcBeautyStarLevel, TcBeautyPosition, ConfigEnvSet) or from player state.

This test imports the coordinator-merged module ``gos.handlers_wives`` directly.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers_wives, v2configs
from gos.handlers import _beauty_row
from gos.defaults import default_player
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="wivestest", data=default_player("wivestest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def _stub():
    return SimpleNamespace(save=lambda p: None), None


def _beauty_cfg(beauty_id: int) -> dict:
    for row in v2configs.load("TcBeauty"):
        if isinstance(row, dict) and int(row.get("BeautyId") or 0) == beauty_id:
            return row
    return {}


def _seed(player, beauty_id: int) -> dict:
    row = _beauty_row(beauty_id)
    player.data.setdefault("RoleBeauty", {})[str(beauty_id)] = row
    return row


def main() -> None:
    store, session = _stub()
    hw = handlers_wives

    check("all ten new actions are registered",
          set(hw.WIVES_HANDLERS) == {"10402", "10403", "10404", "10405",
                                     "10406", "10408", "10409", "10410",
                                     "10411", "10412"}
          and all(callable(f) for f in hw.WIVES_HANDLERS.values()),
          hw.WIVES_HANDLERS.keys())

    # ---- 10402 patronize: Intimacy / Exp / MiracleTrigger ----
    p = _player()
    row = _seed(p, 1)
    upgrade2 = next(r for r in v2configs.load("TcBeautySkillsUpgrade")
                    if int(r.get("SkillLv") or 0) == 2)
    row["BeautyExp"] = int(upgrade2["Skill1"])   # 7, from original config
    row["Intimacy"] = 5
    d = hw.action_wives_patronize({"BeautyId": 1, "ItemId": 1101}, p, session, store)
    check("10402 has exact callback fields with correct types",
          set(d) == {"Intimacy", "Exp", "MiracleTrigger"}
          and isinstance(d["Intimacy"], int) and isinstance(d["Exp"], int)
          and isinstance(d["MiracleTrigger"], bool), d)
    check("10402 Exp is the config-derived current total (no invented reward)",
          d["Exp"] == int(upgrade2["Skill1"]) == 7, d)

    # ---- 10403 skill upgrade: SkillLv / BeautyExp ----
    p = _player()
    row = _seed(p, 1)
    row["Skills"][0][1] = 1          # current skill level 1
    row["BeautyExp"] = int(upgrade2["Skill1"])
    d = hw.action_wives_skill_up({"BeautyId": 1, "SkillNum": 1}, p, session, store)
    check("10403 has SkillLv + BeautyExp ints",
          set(d) == {"SkillLv", "BeautyExp"}
          and isinstance(d["SkillLv"], int) and isinstance(d["BeautyExp"], int), d)
    check("10403 SkillLv is the next TcBeautySkillsUpgrade level from config",
          d["SkillLv"] == int(upgrade2["SkillLv"]) == 2, d)
    check("10403 persists skill level and echoes cumulative BeautyExp",
          p.data["RoleBeauty"]["1"]["Skills"][0][1] == 2
          and d["BeautyExp"] == int(upgrade2["Skill1"]), d)

    # ---- 10404 gift item: Charm / Intimacy ----
    p = _player()
    row = _seed(p, 1)
    init_charm = int(_beauty_cfg(1)["InitCharm"])
    d = hw.action_wives_gift_item({"BeautyId": 1, "ItemId": 1101, "ItemNum": 2},
                                  p, session, store)
    check("10404 has Charm + Intimacy ints",
          set(d) == {"Charm", "Intimacy"}
          and isinstance(d["Charm"], int) and isinstance(d["Intimacy"], int), d)
    check("10404 Charm comes from TcBeauty.InitCharm (config)",
          d["Charm"] == init_charm == 40, d)

    # ---- 10405 vigor recovery: VigorTimeUnix ----
    p = _player()
    d = hw.action_wives_recover_vigor({}, p, session, store)
    check("10405 has a valid VigorTimeUnix",
          set(d) == {"VigorTimeUnix"} and isinstance(d["VigorTimeUnix"], int)
          and d["VigorTimeUnix"] > 0, d)
    check("10405 stamps RoleData.VigorTimeUnix (VigorCd config present)",
          p.data["RoleData"]["VigorTimeUnix"] == d["VigorTimeUnix"]
          and v2configs.env_value("VigorCd") == 1800, d)

    # ---- 10406 group call: ChoiceBeauty / VigorTimeUnix ----
    p = _player()
    a = _seed(p, 1)
    b = _seed(p, 2)
    a["BeautyExp"] = int(upgrade2["Skill1"])
    b["BeautyExp"] = int(upgrade2["Skill2"])
    d = hw.action_wives_group_call({}, p, session, store)
    choice = d.get("ChoiceBeauty")
    check("10406 has ChoiceBeauty + VigorTimeUnix",
          set(d) == {"ChoiceBeauty", "VigorTimeUnix"} and isinstance(choice, list)
          and isinstance(d["VigorTimeUnix"], int), d)
    check("10406 entries carry BeautyId + Exp ints",
          len(choice) == 2
          and all(set(e) == {"BeautyId", "Exp"}
                  and isinstance(e["BeautyId"], int) and isinstance(e["Exp"], int)
                  for e in choice), choice)
    check("10406 Exp values are config-derived current totals",
          {e["BeautyId"]: e["Exp"] for e in choice}
          == {1: int(upgrade2["Skill1"]), 2: int(upgrade2["Skill2"])}, choice)

    # ---- 10408 harem open: VigorTimeUnix ----
    p = _player()
    d = hw.action_harem_open({}, p, session, store)
    check("10408 has a valid VigorTimeUnix",
          set(d) == {"VigorTimeUnix"} and isinstance(d["VigorTimeUnix"], int)
          and d["VigorTimeUnix"] > 0, d)

    # ---- 10409 promote: Position from TcBeautyPosition ----
    p = _player()
    row = _seed(p, 1)
    pos2 = next(r for r in v2configs.load("TcBeautyPosition")
                if int(r.get("LV") or 0) == 2)
    row["Position"] = int(_beauty_cfg(1)["Position"])   # 1 from TcBeauty
    row["Charm"] = int(pos2["NeedCharm"])
    row["Intimacy"] = int(pos2["NeedIntimacy"])
    d = hw.action_wives_promote({"BeautyId": 1, "Promote": 1}, p, session, store)
    check("10409 has Position int",
          set(d) == {"Position"} and isinstance(d["Position"], int), d)
    check("10409 Position advances to the configured next tier",
          d["Position"] == int(pos2["LV"]) == 2, d)
    check("10409 persists the new position",
          p.data["RoleBeauty"]["1"]["Position"] == 2, p.data["RoleBeauty"]["1"])

    # ---- 10410 beauty list: BeautyList map keyed by position tier ----
    p = _player()
    a = _seed(p, 1)
    b = _seed(p, 2)
    a["Position"] = 1
    b["Position"] = 2
    d = hw.action_wives_beauty_list({}, p, session, store)
    bl = d.get("BeautyList")
    levels = sorted({int(r.get("LV") or 0)
                     for r in v2configs.load("TcBeautyPosition")
                     if isinstance(r, dict) and int(r.get("LV") or 0) > 0})
    check("10410 has BeautyList keyed by every configured tier",
          set(d) == {"BeautyList"} and isinstance(bl, dict)
          and set(bl) == {str(lv) for lv in levels}, bl)
    check("10410 tier lists are [count, beautyId...] Lua-safe arrays",
          all(isinstance(v, list) and isinstance(v[0], int) for v in bl.values()), bl)
    check("10410 tier 1/2 contain the right wives",
          bl["1"] == [1, 1] and bl["2"] == [1, 2], {k: bl[k] for k in ("1", "2")})

    # ---- 10411 star up: StarLv from TcBeautyStarLevel ----
    p = _player()
    row = _seed(p, 1)
    d = hw.action_wives_star_up({"BeautyId": 1, "SkillId": 1, "SkillNum": 1},
                                p, session, store)
    star_levels = sorted({int(r.get("Level") or 0)
                          for r in v2configs.load("TcBeautyStarLevel")
                          if isinstance(r, dict) and int(r.get("AdditionId") or 0) == 8})
    check("10411 has StarLv int",
          set(d) == {"StarLv"} and isinstance(d["StarLv"], int), d)
    check("10411 StarLv is the first configured star level",
          d["StarLv"] == star_levels[0] == 1, d)
    check("10411 persists StarLv in Skills[skillNum][3]",
          p.data["RoleBeauty"]["1"]["Skills"][0][2] == 1,
          p.data["RoleBeauty"]["1"]["Skills"][0])

    # ---- 10412 dress skin: empty response, state updated from request ----
    p = _player()
    _seed(p, 1)
    skin_id = int(_beauty_cfg(1)["SkinId"][0])
    d = hw.action_wives_dress_skin({"skinData": {"id": 1, "skinId": skin_id,
                                                 "skinLv": 1}}, p, session, store)
    check("10412 returns an empty Lua-safe table",
          isinstance(d, dict) and d == {}, d)
    check("10412 persists DressSkin from the request / config",
          p.data["RoleBeauty"]["1"]["DressSkin"] == {"SkinId": skin_id, "SkinLv": 1},
          p.data["RoleBeauty"]["1"]["DressSkin"])

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
