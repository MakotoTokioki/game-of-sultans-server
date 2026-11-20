"""Campaign stage actions 10203/10205/10206/10207.

Shapes discovered from the decompiled client callbacks (static-fields.md) and
values from original v2 configs (TcCountry, TcArtilleryCfg). No invented numbers.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import handlers, v2configs
from gos.defaults import default_player
from gos.store import Player

ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _player() -> Player:
    return Player(uid="campaigntest", data=default_player("campaigntest"),
                  path=Path(tempfile.mkdtemp()) / "p.json")


def _stub():
    return SimpleNamespace(save=lambda p: None), None


def main() -> None:
    store, session = _stub()

    # ---- 10203 requestStageData ----
    p = _player()
    d = handlers.action_stage_data({"CounsellorId": 2}, p, session, store)
    sr = d.get("SectionRecord") or {}
    check("10203 has SectionRecord with all callback-read fields",
          all(k in sr for k in ("ChapterId", "SectionId", "PhaseId", "BossBlood",
                                "Soldiers", "PlayAnimation", "Counsellors", "HasUsedOrder")), sr)
    check("10203 PhaseCfg + award envelopes present",
          "PhaseCfg" in d and "CityAwardGet" in d and "MinisterList" in d
          and "SectionAward" in d, d)
    check("10203 appends requested counsellor",
          2 in (p.data["RoleSection"].get("Counsellors") or []),
          p.data["RoleSection"].get("Counsellors"))

    # ---- 10205 country unlock (config-driven) ----
    stages = [int(r.get("UnlockStage") or 0)
              for r in v2configs.load("TcCountry") if isinstance(r, dict)]
    d = handlers.action_country_unlock({"ChapterId": 3}, _player(), session, store)
    unlock = d.get("UnlockCountry")
    check("10205 UnlockCountry is a list from TcCountry",
          isinstance(unlock, list), unlock)
    expected_ids = {int(r.get("Id") or 0) for r in v2configs.load("TcCountry")
                    if isinstance(r, dict) and int(r.get("UnlockStage") or 0) == 3}
    got_ids = {int(r.get("Id") or 0) for r in unlock}
    check("10205 unlocks countries with UnlockStage==chapter (config)",
          got_ids == expected_ids and expected_ids, (got_ids, expected_ids, stages))

    # ---- 10206 artillery upgrade ----
    p = _player()
    p.data["RoleData"]["Silver"] = 5000000
    d = handlers.action_artillery_upgrade({"SilverNum": 1000000}, p, session, store)
    check("10206 returns all three artillery fields",
          all(k in d for k in ("ArtilleryLv", "ArtilleryNowSilver", "ArtilleryAddPr")), d)
    lvl2 = next(r for r in v2configs.load("TcArtilleryCfg")
                if int(r.get("Lv") or 0) == 2)
    check("10206 level advances using config ConsumeSilver",
          d["ArtilleryLv"] == 2 and d["ArtilleryNowSilver"] == 0, d)
    check("10206 ArtilleryAddPr from new level's config Effect",
          abs(d["ArtilleryAddPr"] - float(lvl2.get("Effect") or 0)) < 1e-9, d)
    check("10206 consumes requested silver",
          p.data["RoleData"]["Silver"] == 4000000, p.data["RoleData"]["Silver"])

    # ---- 10207 phase data ----
    d = handlers.action_phase_data({}, _player(), session, store)
    check("10207 has SectionRecord + PhaseCfg",
          "SectionRecord" in d and "PhaseCfg" in d, d)

    # ---- 10019 chat head ----
    p = _player()
    d = handlers.action_chat_head({"chatAvatar": "402", "chooseType": 0}, p, session, store)
    check("10019 echoes NewHeadImg and persists ChatAvatar",
          d.get("NewHeadImg") == "F402"
          and p.data["RoleBase"].get("ChatAvatar") == "F402", (d, p.data["RoleBase"].get("ChatAvatar")))

    # ---- 12001 tribute info ----
    d = handlers.action_tribute_info({}, _player(), session, store)
    cs = d.get("Countrys")
    check("12001 Countrys rows carry Country key from TcCountry",
          isinstance(cs, list) and all("Country" in r for r in cs) and cs,
          cs[:1] if cs else cs)

    # ---- 12701 notice ----
    d = handlers.action_notice({}, _player(), session, store)
    check("12701 notice is Lua-safe map",
          isinstance(d, dict) and "Announcements" in d, d)

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    assert all(ok)


if __name__ == "__main__":
    main()
