"""Seed data for a brand-new account.

The client treats the action-10000 Data object as the whole server state:
dataMod stores it verbatim and every module reads its slice lazily.  We keep
the well-known top-level keys present so login/event dispatch has something to
walk, and grow this file as the client tells us what it actually needs.
"""

from __future__ import annotations

import time

from . import v2configs


def default_counsellor(counsellor_id: int) -> dict:
    """Unlocked-vizier record understood by _ministerMod.

    Talent/Speciality come from the original TcCounsellor config for EVERY id.
    The old hard-coded table only covered ids 1-3, so viziers unlocked by
    TcLvUpgradeCfg.UnlockCounsellor (6, 7, 8, ...) were created with no talents
    at all.  The literals remain as a fallback if the config is unavailable.
    """
    talents = {
        1: [2, 39, 55, 75],
        2: [2, 4, 38, 54, 75],
        3: [1, 47, 55, 75],
    }
    speciality = {
        1: [0, 0, 0, 0],
        2: [1, 0, 0, 0],
        3: [0, 1, 0, 0],
    }
    _cfg = None
    for row in v2configs.load("TcCounsellor"):
        if isinstance(row, dict) and int(row.get("CounsellorId") or 0) == int(counsellor_id):
            _cfg = row
            break
    if _cfg:
        if isinstance(_cfg.get("Talent"), list) and _cfg["Talent"]:
            talents[int(counsellor_id)] = [int(t) for t in _cfg["Talent"]]
        if isinstance(_cfg.get("Speciality"), list) and _cfg["Speciality"]:
            speciality[int(counsellor_id)] = [int(s) for s in _cfg["Speciality"]]
    return {
        "CounsellorId": counsellor_id,
        "Id": counsellor_id,
        "CounsellorType": 1,
        "Lv": 1,
        "Peerages": 1,
        "NowSilver": 0,
        "HaloAdd": [0, 0, 0, 0],
        "BeautyAdd": [0, 0, 0, 0],
        "PillAdd": [0, 0, 0, 0],
        "AttributeValue": [0, 0, 0, 0],
        "AptitudeExp": 0,
        "SkillExp": 0,
        "IsLock": 0,
        "eloquence_lv": 1,
        "knowledge_lv": 1,
        "Talent": [[t, 1] for t in talents.get(counsellor_id, [])],
        "Speciality": speciality.get(counsellor_id, [0, 0, 0, 0]),
        "HaloLv": 0,
        "SkillLv": 1,
        "HaloBreakLv": 0,
        "DressSkin": {"SkinId": counsellor_id, "SkinLv": 0},
        "HaveSkinId": [{"SkinId": counsellor_id, "SkinLv": 0}],
        "PowerAddition": {"CultivatePercentage": 0, "CultivateConst": 0},
    }


def level_unlocked_counsellors(level: int) -> list[int]:
    """Vizier ids granted by original TcLvUpgradeCfg up to ``level``.

    character.upOfficerScene.lua reads UnlockCounsellor from the newly reached
    level's row and calls `_ministerMod.setMinisterInfo(id, "IsLock", 0)` — a
    purely client-side change.  Our server must mirror it or the unlock is
    lost on the next login (the login snapshot overwrites RoleCounsellor).
    """
    result: list[int] = []
    for row in v2configs.load("TcLvUpgradeCfg"):
        if not isinstance(row, dict):
            continue
        if int(row.get("Level") or 0) > int(level):
            continue
        cid = int(row.get("UnlockCounsellor") or 0)
        if cid > 0 and cid not in result:
            result.append(cid)
    return result


def heal_cosmetics(data: dict, now: int | None = None) -> None:
    """Ensure legacy accounts carry the config-derived starter cosmetics.

    Accounts persisted before the cosmetic seed existed have RoleItem/
    DressList/AvatarList == [] and AvatarBox 0.  Login calls this so the
    appearance fix applies to returning players too, not just new ones.  Ids
    are the verified TcItem starters (400 fashion, 1400 box, 1500 skin); the
    +365-day expiry mirrors the official v7 13310 capture.
    """
    now = int(now if now is not None else time.time())
    expire = now + 365 * 86400
    items = data.get("RoleItem")
    if not isinstance(items, list):
        items = []
        data["RoleItem"] = items
    have = {int(e.get("ItemId") or 0) for e in items if isinstance(e, dict)}
    for item_id in (400, 1400, 1500):
        if item_id not in have:
            items.append({"ItemId": item_id, "Num": 1, "ExpireTime": expire})
    for key, item_id in (("DressList", 400), ("AvatarList", 1400)):
        rows = data.get(key)
        if not isinstance(rows, list):
            rows = []
            data[key] = rows
        if not any(int(e.get("ItemId") or 0) == item_id for e in rows if isinstance(e, dict)):
            rows.append({"ItemId": item_id, "Num": 1, "Timestamp": now})
    for key in ("RoleBase", "RoleData"):
        block = data.get(key)
        if not isinstance(block, dict):
            block = {}
            data[key] = block
        if not block.get("AvatarBox"):
            block["AvatarBox"] = 1400
        if not block.get("DressTitle"):
            block["DressTitle"] = 0
        avatar = block.get("ChatAvatar")
        if (not isinstance(avatar, str) or avatar[:1] not in ("B", "C", "F")
                or avatar == "B1"):
            # Legacy numeric strings ("1", "0", ...) have no prefix and the
            # client falls back to the generic level portrait.  The starter
            # fashion avatar 400 is config-derived (TcItem 400).
            block["ChatAvatar"] = "F400"
    base = data.get("RoleBase") or {}
    dress = base.get("DressList")
    if not isinstance(dress, list) or not dress:
        base["DressList"] = [dict(e) for e in (data.get("DressList") or [])]


def default_player(uid: str, nickname: str = "Sultan") -> dict:
    now = int(time.time())
    try:
        show_id = int(uid) if str(uid).isdigit() else abs(hash(uid)) % 900000 + 100000
    except Exception:
        show_id = 100001

    # Starter cosmetics.  Item ids 400 (fashion), 1400 (avatar box) and 1500
    # (skin) are verified present in the bundled TcItem config (see
    # v2configs.item_by_id).  No v2 config stores an expiry, so the +365-day
    # ExpireTime mirrors the official v7 13310 capture (ExpireTime = now +
    # one year); it is a captured value, not an invented schedule.  ChatAvatar
    # "F400" is keyed to the starter fashion id (v7 uses "7"; v2 has no
    # chat-avatar config table, so the fashion id is the only local source).
    cosmetic_expire = now + 365 * 86400
    starter_role_items = [
        {"ItemId": item_id, "Num": 1, "ExpireTime": cosmetic_expire}
        for item_id in (400, 1400, 1500)
    ]
    starter_dress = [{"ItemId": 400, "Num": 1, "Timestamp": now}]
    starter_avatar = [{"ItemId": 1400, "Num": 1, "Timestamp": now}]

    role_base = {
        "Rid": str(uid),
        "Nickname": nickname,
        "Name": nickname,
        "Sex": 1,
        # 0 = no avatar chosen yet -> client shows createRoleDialog (normal
        # first-run flow).  Any nonzero value makes loginScene jump straight
        # into placeMap.mainScene and skip creation/tutorials.
        "Avatar": 0,
        "ChatAvatar": "F400",
        "Figure": 0,
        "Lv": 1,
        "VipLv": 0,
        "ShowId": show_id,
        "Power": 1000,
        "Exp": 0,
        "DressList": [dict(entry) for entry in starter_dress],
        "AvatarBox": 1400,
        "DressTitle": 0,
        # Main-scene map skin. roleMod.getSceneSkin returns this value verbatim
        # and skinLogic builds "assets/unpack/bg/place_map/skin_<id>/bg1_*.jpg".
        # 0 would request skin_0 which does not exist: display.newImageRect
        # returns nil and bgWgt.createBigBg crashes ("attempt to index a nil
        # value" at createBigBg <- sceneSkin <- updateScene), leaving the main
        # scene black. Only 1500/1501/1502 ship in the APK.
        "DressSceneId": 1500,
        "SysGuid": 0,
    }

    role_data = {
        "Lv": 1,
        "VipLv": 0,
        "Exp": 0,
        "Power": 1000,
        "Silver": 500000,
        "Food": 500000,
        "Soldier": 5000,
        "Gold": 5000,
        "Status": 0,
        "LastStatus": "story-0",
        "Figure": 0,
        "ChatAvatar": "F400",
        "AvatarBox": 1400,
        "DressTitle": 0,
        # Must mirror RoleBase.DressSceneId (client renders from RoleBase,
        # but several payloads echo it from RoleData).
        "DressSceneId": 1500,
        "StudyArray": [],
        # [silver, food, soldier] last-collection timestamps
        "LevyTimeUTC": [now, now, now],
        "GovTimeUTC": now,
        "VigorTimeUnix": now,
        "BossScore": 0,
        "BossCredits": 0,
        "fame": 0,
        # roleMod.getRoleData(key) falls back to the whole RoleData table when
        # the key is absent; _wifeCompeteMod then compares that table to a
        # number (SysGuid) and crashes.
        "SysGuid": 0,
        "Nickname": nickname,
    }

    role_section = {
        "ChapterId": 1,
        "SectionId": 1,
        "PhaseId": 1,
        "BossBlood": 0,
        "Soldiers": 0,
        "PlayAnimation": 0,
        "ArtilleryLv": 1,
        "ArtilleryAddPr": 0,
        "ArtilleryUseNum": 0,
        "ArtilleryNowSilver": 0,
    }

    extra = {
        # Keep the 7-day login event closed until it is actually configured.
        # _sevenDaysMod.isActivity returns ExtraData.lwReceiveNum; 0 is truthy
        # in Lua, which creates the Benefits button at the top-left corner.
        "RechargeAmount": 0,
        "MonthDeadline": 0,
        "DayVedioTimes": 0,
        "LastLoginTime": now,
    }

    return {
        "SysTime": now,
        "TimeZone": 3,
        "TcVersion": 2,
        "OpenServerDay": 1,
        "ServerOpenTime": now - 86400,
        "MusicUrl": "",
        "Frozen": 0,
        "RoleBase": role_base,
        "RoleData": role_data,
        "RoleSection": role_section,
        "ExtraData": extra,
        # collections / activity state
        "RoleItem": [dict(entry) for entry in starter_role_items],
        "RoleCounsellor": [default_counsellor(i) for i in (1, 2, 3)],
        "RoleBeauty": {},
        "GrowthFundMap": {},
        "RedPoint": {},
        "OChildren": [],
        "Children": [],
        "DressList": [dict(entry) for entry in starter_dress],
        "AvatarList": [dict(entry) for entry in starter_avatar],
        "ShieldList": [],
        "CloseModuleList": [],
        "TimeLimitAwardGet": {},
        "TimeLimitComplete": {},
        "SingleItemAward": {},
        "ManifestoDict": {},
        # Number of unlocked tribute countries; roleMod.unlockRule compares
        # this numerically.  A table here throws "compare number with table".
        "Lifanyuan": 0,
        # Activity feeds _settingCatchMod.start (v2 client): for every key the
        # client knows (CompetitionRank, TimeLimitActivity, ...) it queues the
        # {Id, Version} entries with Version ~= -1 and fetches their configs
        # via action 11901; saveRouteSetting stores the returned
        # "CompetitionRankCfg" map (keyed by tostring(Id)) for
        # _rankUpActivityMod (window checks via timeToZone0/dateToSec).
        # Only CompetitionRank is advertised, mirroring the official capture
        # (recon/v7-capture/v7_actions.jsonl 10000/11901, 2026-11-16): ids 1-3
        # with Version 0.  Every other activity key stays absent, which the
        # client treats exactly like the previous empty Activity table.
        "Activity": {
            "CompetitionRank": [
                {"Id": 1, "Version": 0},
                {"Id": 2, "Version": 0},
                {"Id": 3, "Version": 0},
            ],
        },
        # scalar flags
        "RechargeAmount": 0,
        "IsCrossRankOpen": 0,
        "IsBinding": 0,
        "LoginWelfare": 0,
        "UpdateVersionAward": 0,
        "BossSingleUTC": [0, 0],
        "BossGlobalUTC": [0, 0],
        "MonthDeadline": 0,
        "DayVedioTimes": 0,
        "LastLoginTime": now,
        "PlayerComebackIcon": 0,
        "PlayerComebackCanUseCode": "",
        "PlayerComebackPopup": 0,
        "ShowCashView": 0,
        "AllianceGuid": "",
        # Slices read during login-time module init.  Missing tables here
        # show up as Lua runtime errors before the first scene is entered.
        "MainTask": {"TaskId": 1, "TaskNum": 0},
        # persisted main-quest counters (10015 NextTaskId/NextNum); grown by
        # gos.tasks when gameplay actions are observed
        "mainTaskProgress": {"taskId": 0, "taskNum": 0},
        # Numeric 0 is truthy in Lua.  _welfCardMod.isRedPoint calls
        # dateToSec(CardEndTime.MonthDeadline) and crashes with
        # "field 'day' missing in date table" if these are 0.  Empty tables
        # make them nil, so the no-card path is skipped correctly.
        "CardEndTime": {},
        "CardAwardTime": {},
        "CheckInDay": 1,
        # _signMod feeds this straight into dateToNumber/dateToSec, which parse
        # it as a "YYYY-MM-DD HH:MM:SS" string with string.match.  A unix int
        # crashes mainScene's red-point scan (black screen).
        "CheckInReFreshTime": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
        "CumulativeList": [],
        # _rankingMod.init (modules/_rankingMod.lua 21-56) reads
        # tbData.RankWorshipTime on __login and hands it to isRedPoint (214-246),
        # which pairs() over it.  A missing table made that __login listener
        # abort; because eventManager.dispatchEvent has no per-listener error
        # guard, the rest of the __login chain (including timerListener.init,
        # which creates the 1-second clock) never ran.  Seed the three keys
        # with yesterday's UTC+3 date string so the client's
        # dateToNumber(secToDate(now),"day") > stored-day check lights up once
        # and never indexes/pairs a nil value.
        "RankWorshipTime": {
            "PowerRank": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now - 86400)),
            "SectionRank": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now - 86400)),
            "IntimacyRank": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now - 86400)),
        },
    }
