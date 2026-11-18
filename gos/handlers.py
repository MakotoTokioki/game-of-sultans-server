from __future__ import annotations

import datetime
import json
import math
import random
import threading
import time
from typing import Any

from . import config, defaults, tasks, v2configs
from .crypto import decode_request_data, encode_response_data, generate_keypair
from .store import Player, Session, Store, new_jwt


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _now() -> int:
    return int(time.time())


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


_log_lock = threading.Lock()


def log_action(action: str, payload: dict, user_id: str) -> None:
    record = {
        "ts": time.time(),
        "action": action,
        "user": user_id,
        "known": action in HANDLERS,
        "payload": payload,
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with _log_lock:
        with open(config.LOG_DIR / "actions.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


_UNKNOWN_IGNORE = {"ad_trace_new", "sdk_err_agent", "sdk_device_push"}


def log_unknown(action: str, payload: dict, response: Any, user_id: str) -> None:
    """Persist unknown menu actions for later handler derivation.

    Kept separate from actions.jsonl so the GLM/debug loop can consume a
    clean queue (full request payload plus the fallback response that was
    actually sent).
    """
    if action in _UNKNOWN_IGNORE:
        return
    record = {
        "ts": time.time(),
        "action": action,
        "user": user_id,
        "payload": payload,
        "fallback_response": response,
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with _log_lock:
        with open(config.LOG_DIR / "unknown_actions.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _outer_error(msg: str) -> dict:
    return {"stat": 0, "info": msg}


def _player_from_distribute(body: dict, store: Store) -> Player:
    data = body.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}
    uid = (
        data.get("UId")
        or data.get("uid")
        or data.get("UserId")
        or data.get("user_id")
        or data.get("Account")
        or data.get("account")
    )
    if uid:
        return store.get_or_create(str(uid))
    # Last resort: reuse an existing player, otherwise a dev account.
    # This keeps the dev server usable while the client is being wired up.
    sessions = getattr(store, "_by_user", {})
    if sessions:
        uid = next(iter(sessions))
        return store.get_or_create(str(uid))
    return store.get_or_create("dev")


# --------------------------------------------------------------------------
# /frontend/jwt_action  (9999 / 9998)
# --------------------------------------------------------------------------
def handle_jwt_action(body: dict, store: Store) -> dict:
    act = str(body.get("act_id") or "")
    c_pub = body.get("c_pub")
    if not c_pub:
        return _outer_error("missing c_pub")

    keypair = generate_keypair()
    sk = keypair.shared_key(c_pub)
    user_id = str(body.get("user_id") or body.get("UId") or "dev")
    app_id = int(body.get("app_id") or 0)
    server_id = int(body.get("server_id") or config.SERVER_ID)

    jwt = new_jwt()
    player = store.get_or_create(user_id)
    session = Session(
        jwt=jwt,
        user_id=user_id,
        sk=sk,
        keypair=keypair,
        player_id=player.uid,
        server_id=server_id,
        app_id=app_id,
    )
    store.add_session(session)

    inner = {
        "Code": 0,
        "jwt": jwt,
        "sPub": keypair.public_pem,
        "Sess": jwt,
        "SysTime": _now(),
        "UserId": user_id,
    }
    # dataNetMod hands 9999/9998 responses to the caller without decrypting
    # Data, so this inner JSON is sent as a plain string.
    return {"stat": 1, "data": _json(inner)}


# --------------------------------------------------------------------------
# /frontend/distribute  (9004 / 9005 / 9006 and other routed calls)
# --------------------------------------------------------------------------
def handle_distribute(body: dict, store: Store) -> dict:
    has_actionid = body.get("actionid") is not None or body.get("actionId") is not None
    action = str(body.get("actionid") or body.get("actionId") or "")
    nested: dict = {}
    raw_nested = body.get("data")
    if isinstance(raw_nested, str):
        try:
            nested = json.loads(raw_nested)
        except Exception:
            nested = {}
    if not action or action.startswith("hwsj_route_"):
        action = str(nested.get("ActionId") or nested.get("actionId") or action)

    # Push registration is not implemented.  A success envelope makes the SDK
    # callback dereference getLonge9Role().roleId before roleLogin has finished,
    # so acknowledge with stat=0 and let the client skip it.
    if body.get("act_id") == "sdk_device_push" or action == "1432":
        return {"stat": 0, "info": "push disabled"}

    player = _player_from_distribute(body, store)
    log_action(action or str(body.get("act_id") or ""), nested or body, player.uid)

    # Official v7 9004 Root.servers[0] carries all of these keys
    # (recon/v7-capture/v7_actions.jsonl idx 3/153): id, name, state,
    # priority, canRegister, hasAccount, auditServer, socket{port,server},
    # mergeIds.  The SDK's route filter (action.routeAction.lua 346-430)
    # sorts by tonumber(priority) and removes state==0 rows when
    # getIsHideUnknowServer() is true.  `http`/`ip`/`port` are the v2
    # dev-server additions used by baseInfo.setServerInfo.
    route_servers = [
        {
            "id": config.SERVER_ID,
            "name": config.SERVER_NAME,
            "state": 1,
            "priority": 1,
            "random": 0,
            "canRegister": 1,
            "hasAccount": 0,
            "auditServer": 0,
            "socket": {"port": config.PORT, "server": "127.0.0.1"},
            "http": config.PUBLIC_BASE_URL,
            "ip": "127.0.0.1",
            "port": config.PORT,
            "new": 0,
            "hot": 0,
            "full": 0,
            # baseInfo.getServerInfoById() json.decodes this for every
            # server when the requested sid is not an exact id match.
            # Missing mergeIds makes json.decode(nil) abort scene creation.
            "mergeIds": "[]",
        }
    ]

    data: Any
    if action in {"1011", "9002"}:
        # SDK route request.  The official v7 9004/1011 response (capture idx
        # 3/153 in recon/v7-capture/v7_actions.jsonl) carries a Root with
        # servers, gzip, updateurl, switch, version, connect, qq and
        # transactionUrl, plus top-level LastRegionId / IsTestAccount / ...
        # Fields whose values are host-specific (updateurl, connect,
        # transactionUrl) are pointed at this dev server; all boolean/enum
        # values are the real ones from the capture.
        _root = config.PUBLIC_BASE_URL.split("/frontend/")[0] if "/frontend/" in config.PUBLIC_BASE_URL else config.PUBLIC_BASE_URL.rstrip("/")
        data = {
            # The SDK does json.decode(Root.version) and then indexes the
            # result as an array of supported versions.  It must be a JSON
            # array string, not a bare number.  Keep it equal to the client
            # version so the updater does not block login.
            "Root": {
                "version": "[2.005]",
                "servers": route_servers,
                "gzip": 0,
                "updateurl": _root + "/",
                "switch": "10011",
                "connect": _root + "/api/interface/contact_notice_data",
                "qq": "[]",
                "transactionUrl": _root + "/",
            },
            "LastRegionId": config.SERVER_ID,
            "AuditRegionId": 0,
            "RegionId": 1,
            "LastAppId": 0,
            "AuditAppId": 0,
            "AppId": body.get("app_id", 1020),
            "UId": "",
            "IsTestAccount": 0,
            "IsAuditVersion": 0,
            "LoginRecord": None,
            "GZip": 0,
            "IsSentClientLog": 0,
            "SentLogTime": 5000,
            # netTestMod.testAllRegions requires this to consider a region
            # usable; without it the client retries forever and shows
            # "Connection lost".
            "IsOpen": 1,
            "IsWarnPrivacy": 0,
        }
    elif action in {"1105", "1002", "1115", "1101"}:
        # SDK user-center login / register / third-party login.
        # Keep the shape the SDK success callback reads: Uid, Token, Account.
        now = _now()
        data = {
            "Uid": player.uid,
            "Token": "dev-sdk-token",
            "Account": player.uid,
            "Password": "devpass123",
            "AccStatus": 0,
            "Status": 0,
            "Info": "",
            "Platform": str(nested.get("Platform") or "guest"),
            "IsGuest": 1,
            "IsNew": 1,
            "TokenExpire": now + 86400 * 365,
            "CreateTime": now - 3600,
            "LastLoginTime": now,
            "ServerTime": now,
            "BindInfo": {},
            "IsNeedProtect": 0,
        }
    elif action == "1113":
        data = {"status": 1}
    elif action == "1208":
        data = {"isprotect": 0, "cdtime": 0}
    elif action == "1006":
        # SDK account-list callback does #A0_3.UserList; it must be an array.
        data = {"accounts": [], "UserList": [], "Total": 0}
    elif action == "9004":
        # Same official v7 9004 shape as ActionId 1011 (capture idx 3/153).
        _root = config.PUBLIC_BASE_URL.split("/frontend/")[0] if "/frontend/" in config.PUBLIC_BASE_URL else config.PUBLIC_BASE_URL.rstrip("/")
        data = {
            "Root": {
                "servers": [dict(route_servers[0])],
                "version": "[2.005]",
                "gzip": 0,
                "updateurl": _root + "/",
                "switch": "10011",
                "connect": _root + "/api/interface/contact_notice_data",
                "qq": "[]",
                "transactionUrl": _root + "/",
            },
            "LastRegionId": config.SERVER_ID,
            "AuditRegionId": 0,
            "RegionId": 1,
            "LastAppId": 0,
            "AuditAppId": 0,
            "AppId": body.get("app_id", 1020),
            "UId": "",
            "IsTestAccount": 0,
            "IsAuditVersion": 0,
            "LoginRecord": None,
            "GZip": 0,
            "IsSentClientLog": 1,
            "SentLogTime": 5000,
            "IsOpen": 1,
            "SysTime": _now(),
        }
    elif action == "9005":
        # Same complete server row as 1011/9002/9004; routeAction.L12_1 sorts
        # by priority and getServerInfoById reads id/mergeIds on every row.
        data = {"Root": {"servers": [dict(route_servers[0])]}}
    elif action == "9006":
        # update/announcement payload.  ForceUpdateInfo must be a table:
        # _updatePrizeMod.isCrossVersion reads updateInfo.ForceUpdateInfo.Version
        # unconditionally, so null here crashes createBtnActive and aborts the
        # fixed-strip layout.  An empty table means "no update".
        # MusicUrl is read by loginMod's 9006 callback and handed to
        # musicMod.setDownloadUrl: an absent/empty value makes the story
        # voice downloads build relative URLs and hang the story dialog.
        # v7 9006 Data also carries SysTime; dataNetMod resyncs runtime.SysTime
        # from Data.SysTime on every response, including SDK routes.
        data = {
            "ForceUpdateInfo": {},
            "VersionAward": {},
            "MusicUrl": config.MUSIC_BASE_URL,
            "SysTime": _now(),
        }
    else:
        data = {}

    # SDK transport responses (including the /distribute route actions
    # 1011/9002/9004/9005) all pass through sdks/netModule.lua's response
    # handler.  It json.decodes the outer `data` string, then REQUIRES the
    # decoded inner object to have Stat==0 (or stat==1) before it calls the
    # successListener (netModule.lua 128-139):
    #
    #   L4_2 = L3_2.Stat; if L4_2 ~= 0 then
    #     L4_2 = L3_2.stat; if L4_2 ~= 1 then goto fail
    #   end
    #   successListener(L3_2)
    #
    # The route successListener (action.routeAction.lua 453-486) then does
    # setRoute(A0_3) and reads A0_3.Root.servers.  So the inner MUST carry
    # Stat/stat AND Root at the TOP level (not nested under Data).  Without
    # Stat/stat the SDK treats the route response as a failure, never sets
    # ROUTEINFO, and loginScene's getServerInfoById indexes
    # getRoute().Root.servers on nil -> "attempt to index a nil value".
    # This matches the official v7 capture, whose 9004 `Data` object has Root
    # and all other fields at its top level.
    is_sdk_route = action in {"1011", "9002", "9004", "9005"}
    if not has_actionid or is_sdk_route:
        inner = {"Stat": 0, "stat": 1, "Code": 0}
        if isinstance(data, dict):
            inner["Data"] = data
            inner.update(data)  # Root (and every payload field) at top level
        if is_sdk_route:
            inner.setdefault("Info", "")
            inner.setdefault("Sess", "")
            inner.setdefault("SysTime", _now())
    else:
        inner = {"Code": 0, "Data": data, "Sess": "", "SysTime": _now()}
    return {"stat": 1, "data": _json(inner)}


# --------------------------------------------------------------------------
# /frontend/forward  (normal encrypted calls)
# --------------------------------------------------------------------------
def handle_forward(body: dict, store: Store) -> dict:
    jwt = str(body.get("jwt") or "")
    data_b64 = body.get("data")
    if not data_b64:
        return _outer_error("missing data")

    session = store.find_session_for_data(jwt, data_b64)
    if session is None:
        return _outer_error("unknown session")

    # The client's liblua.shareKey() returns base64(secret) + "\n" (OpenSSL
    # EVP_EncodeFinal appends the newline).  find_session_for_data records the
    # exact spelling that decoded this request in ``wire_sk``; use the same key
    # for the response, otherwise action 10000 is received but its reply is
    # undecryptable and the client never reaches "Tap to start".
    wire_sk = getattr(session, "wire_sk", None) or session.sk
    payload = decode_request_data(wire_sk, data_b64)
    action = str(payload.get("actionid") or payload.get("ActionId") or "")
    player = store.get_or_create(session.user_id)
    session.player_id = player.uid
    store.touch(session)
    log_action(action, payload, player.uid)

    data = dispatch(action, payload, player, session, store)
    if data is None:
        data = {}
    if action not in HANDLERS:
        log_unknown(action, payload, data, player.uid)
    # v2 failure shape (net/dataNetMod.lua lines 1105-1119): a Code 1
    # envelope shows Info as a prompt and skips the action callback AND
    # serverUpToData entirely, so a handler that refuses a mutation returns
    # {"_fail": "..."} and no Data at all.
    fail = data.get("_fail") if isinstance(data, dict) else None
    if fail is None and isinstance(data, dict):
        # dataNetMod.serverUpToData reads Data.Extra.mainTask on every
        # action response and calls _mainTaskMod.setMainTaskServerData ->
        # stores the counter and dispatches "updateMainTask", so the main
        # quest UI follows the counter immediately (the 10001/10002
        # callbacks only touch LevyTimeUTC; without this the UI stays stale
        # until 10015).  Login needs the same treatment: _mainTaskMod.init
        # reads Data.MainTask.
        attach_task_progress(data, player)

    if fail is not None:
        inner = {
            "Code": 1,
            "Info": str(fail),
            "Sess": session.jwt,
            "SysTime": _now(),
        }
    else:
        # dataNetMod decrypts the outer "data" string, then expects the inner
        # envelope to contain the *plain* state under "Data" and passes that
        # straight to the action callback.  Encrypting Data as well would hand
        # loginMod a bare (non-table) value.
        inner = {
            "Code": 0,
            "Data": data,
            "Sess": session.jwt,
            "SysTime": _now(),
        }
    return {"stat": 1, "data": encode_response_data(wire_sk, inner)}


# --------------------------------------------------------------------------
# action implementations
# --------------------------------------------------------------------------
def action_full_state(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """Permissive fallback: expose the usual response fields at top level.

    Many action callbacks read one slice (`Silver`, `GovTimeUTC`,
    `LevyTimeUTC`, `RoleItem`, ...) rather than a nested envelope.  Merging the
    login state gives those handlers a value while we implement the action
    properly.
    """
    out = dict(player.data)
    for key in ("RoleData", "RoleBase", "RoleSection", "ExtraData"):
        value = player.data.get(key)
        if isinstance(value, dict):
            for k, v in value.items():
                out.setdefault(k, v)
    out["SysTime"] = _now()
    return out


def _heal_counsellor_unlocks(player: Player) -> list[int]:
    """Persist the viziers the client unlocks from TcLvUpgradeCfg on level-up.

    character.upOfficerScene.lua (the action-10010 callback) reads
    ``UnlockCounsellor`` and unlocks that vizier locally via
    ``_ministerMod.setMinisterInfo(id, "IsLock", 0)`` — it never sends an
    action to the server.  Without mirroring that here, the new vizier is lost
    on the next login, where the server snapshot wins and RoleCounsellor is
    back to the starter three.  Ids come only from the original v2 config.
    """
    rd = player.data.get("RoleData") if isinstance(player.data.get("RoleData"), dict) else {}
    rb = player.data.get("RoleBase") if isinstance(player.data.get("RoleBase"), dict) else {}
    level = int(rd.get("Lv") or rb.get("Lv") or 1)
    rows = player.data.setdefault("RoleCounsellor", [])
    if not isinstance(rows, list):
        rows = []
        player.data["RoleCounsellor"] = rows
    have = {int(c.get("CounsellorId") or c.get("Id") or 0)
            for c in rows if isinstance(c, dict)}
    added: list[int] = []
    for cid in defaults.level_unlocked_counsellors(level):
        if cid in have:
            continue
        rows.append(defaults.default_counsellor(cid))
        have.add(cid)
        added.append(cid)
    return added


def action_login(payload: dict, player: Player, session: Session, store: Store) -> dict:
    now = _now()
    player.data["SysTime"] = now
    player.data["LastLoginTime"] = now
    player.data.setdefault("RoleBase", {})
    player.data.setdefault("RoleData", {})
    player.data.setdefault("RoleSection", {})
    player.data.setdefault("ExtraData", {})
    # roleMod.getSceneSkin returns RoleBase.DressSceneId verbatim and
    # skinLogic turns it into "skin_<id>/bg1_*.jpg". A 0/nil skin makes
    # display.newImageRect fail and bgWgt.createBigBg crash with
    # "attempt to index a nil value" (createBigBg <- sceneSkin <- updateScene),
    # leaving the main scene black. Heal any stale value.
    if not player.data["RoleBase"].get("DressSceneId"):
        player.data["RoleBase"]["DressSceneId"] = 1500
    if not player.data["RoleData"].get("DressSceneId"):
        player.data["RoleData"]["DressSceneId"] = 1500
    # instanceMod.isShowInMain indexes BossSingleUTC[1]/[2]; the official
    # login snapshot carries the current game-day window (v7 capture 156).
    # Seeding real windows instead of [0,0] is what makes the Frontier entry
    # visible and the events open at their times.
    _refresh_boss_windows(player)
    # _settingCatchMod.start queues login Activity entries with Version ~= -1
    # for an 11901 config fetch.  Accounts created before CompetitionRank was
    # added to defaults still persist "Activity": [], so heal the key here;
    # any other activity entries already present are left untouched.
    activity = player.data.get("Activity")
    if not isinstance(activity, dict):
        activity = {}
        player.data["Activity"] = activity
    if not activity.get("CompetitionRank"):
        activity["CompetitionRank"] = [{"Id": i, "Version": 0} for i in (1, 2, 3)]
    if not activity.get("TimeLimitActivity"):
        # Same official 1..9 queue (see defaults.py); existing accounts need
        # it too or the 11901 TimeLimitCfg listener never runs.
        activity["TimeLimitActivity"] = [
            {"Id": i, "Version": 0} for i in range(1, 10)
        ]
    # _mainTaskMod.init reads only tbData.MainTask; mainTaskProgress is the
    # authoritative persisted counter.  Keeping them in sync here prevents
    # the client from restarting the quest chain at an old TaskId.
    tasks.sync_login_fields(player.data)
    # _governmentMod.init (modules/_governmentMod.lua 341-364) reads
    # RoleData.GovEvenId on __login to seed the current event; seed it (and
    # the config-backed Lv values) before the state snapshot is sent so the
    # first government-screen open never indexes a nil event id.
    _gov_state(player)
    # _rankingMod.init + isRedPoint (modules/_rankingMod.lua 21-56, 214-246)
    # pairs() over tbData.RankWorshipTime on __login.  If it is nil/absent the
    # listener aborts; dispatchEvent has no pcall, so the whole remaining
    # __login chain stops -- including tools.timerListener.init, which is
    # registered on __login (tools/timerListener.lua end of file) and creates
    # the global 1-second timer that drives every countdown (levy included).
    # Heal legacy accounts created before RankWorshipTime existed.
    # Use yesterday's UTC+3 game day so the collectable-once semantics match
    # the 11001 response (a real date string, never an empty string that
    # dateToNumber would reject).
    rwt = player.data.get("RankWorshipTime")
    if not isinstance(rwt, dict) or not all(
        isinstance(rwt.get(k), str) and rwt.get(k)
        for k in ("PowerRank", "SectionRank", "IntimacyRank")
    ):
        yesterday = (_game_time() - datetime.timedelta(days=1)).strftime(_GAME_DATE_FMT)
        player.data["RankWorshipTime"] = {
            "PowerRank": yesterday,
            "SectionRank": yesterday,
            "IntimacyRank": yesterday,
        }
    # _signMod.init reads tbData.CheckInReFreshTime at login; seed the same
    # neutral value the 11501 response uses so a new account is collectable
    # once and a returning account stays locked until its next UTC+3 game day.
    player.data["CheckInReFreshTime"] = _stored_checkin_refresh(player)
    # Legacy accounts were created with empty RoleItem/DressList/AvatarList
    # and ChatAvatar "B1"; seed the config-derived starter cosmetics so the
    # appearance fix reaches returning players too (new accounts already have
    # them from defaults.default_player).
    defaults.heal_cosmetics(player.data, now)
    _heal_counsellor_unlocks(player)
    # Accounts that reached the tutorial level-up before this fix kept an
    # empty RoleBeauty; seed Canfeza so the Harem unlocks on next login.
    _grant_tutorial_beauty(player, int(player.data.get("RoleData", {}).get("Lv") or 1))
    store.save(player)
    return player.data


def action_create_role(payload: dict, player: Player, session: Session, store: Store) -> dict:
    rb = player.data.setdefault("RoleBase", {})
    if payload.get("Rid"):
        rb["Rid"] = str(payload["Rid"])
    if payload.get("Sex") is not None:
        rb["Sex"] = payload["Sex"]
    if payload.get("Avatar") is not None:
        rb["Avatar"] = payload["Avatar"]
    rb.setdefault("Nickname", "Sultan")
    rb.setdefault("Lv", 1)
    store.save(player)
    return {}


def action_role_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    return player.data


def _beauty_row(beauty_id: int) -> dict:
    """Full RoleBeauty row, mirroring _wivesMod.init_wives_info (the client
    replaces its config-seeded row wholesale with ours, so every field it
    reads must be present).  Values come from the original TcBeauty /
    TcBeautySkills configs; no cosmetic or charm amount is invented.
    """
    cfg = {}
    for row in v2configs.load("TcBeauty"):
        if isinstance(row, dict) and int(row.get("BeautyId") or 0) == int(beauty_id):
            cfg = row
            break
    skills = [[int(s), 0, 0] for s in (cfg.get("Skills") or [])
              if str(s).isdigit()]
    skin = {"SkinId": int(beauty_id), "SkinLv": 0}
    return {
        "BeautyId": int(beauty_id),
        "Intimacy": 0,
        "Charm": int(cfg.get("InitCharm") or 0),
        "Position": int(cfg.get("Position") or 0),
        "BeautyExp": 0,
        "ChildrenNum": 0,
        "Favor": 0,
        "IsUnlock": 1,
        "DressSkin": dict(skin),
        "HaveSkinId": [dict(skin)],
        "Skills": skills,
    }


def _grant_tutorial_beauty(player: Player, level: int) -> list[dict]:
    """Canfeza (Beauty_001) joins the harem at the tutorial's level-2 upgrade.

    roleMod.action10010 (modules/roleMod.lua 1013-1028) unlocks wives from the
    response's NewBeautyList; our old handler returned an empty list, so the
    client showed her from story data but RoleBeauty on the server stayed
    empty and the Harem never unlocked.
    """
    if level < 2:
        return []
    wives = player.data.setdefault("RoleBeauty", {})
    if not isinstance(wives, dict):
        wives = {}
        player.data["RoleBeauty"] = wives
    if isinstance(wives.get("1"), dict) and int(wives["1"].get("IsUnlock") or 0) == 1:
        return []
    row = _beauty_row(1)
    wives["1"] = row
    return [row]


def action_level_up(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """roleMod.upOfficeRequist action10010: consume the Sultan's level-up.

    upOfficerScene enables the button only when RoleData.Exp reaches
    TcLvUpgradeCfg[Lv+1].Exp, so validate the same config here rather than
    incrementing unconditionally.  TcLvUpgradeCfg.Exp is the cumulative
    requirement (0, 200, 400, ...), not a per-level cost, so Exp is kept.
    """
    rb = player.data.setdefault("RoleBase", {})
    rd = player.data.setdefault("RoleData", {})
    level = int(rd.get("Lv") or rb.get("Lv") or 1)
    exp = int(rd.get("Exp") or 0)
    rows = v2configs.load("TcLvUpgradeCfg")
    if isinstance(rows, dict):
        rows = list(rows.values())
    required = None
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and int(row.get("Level") or 0) == level + 1:
                required = int(row.get("Exp") or 0)
                break
    if required is None:
        return {"_fail": "Maximum level reached"}
    if exp < required:
        return {"_fail": "Not enough EXP"}
    level += 1
    rd["Lv"] = level
    rd["Exp"] = exp
    rb["Lv"] = level
    # Mirror the client's client-side unlock so it survives the next login.
    _heal_counsellor_unlocks(player)
    # Government config values (affair EXP/rate/capacity) are level-indexed;
    # refresh the persisted slice so this level-up takes effect immediately.
    _gov_state(player)
    new_beauties = _grant_tutorial_beauty(player, level)
    store.save(player)
    return {"NewLevel": level, "GovOverTime": 0, "NewBeautyList": new_beauties}


def action_task(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_mainTaskMod action10015 (the CLAIM call: both client call sites are
    the task-dialog claim buttons, guarded by getTaskStatus()=="CLAIM").

    Completes the current task when taskNum >= TcMainTask.RequireNum and
    returns the new counters; the callback stores NextTaskId/NextNum into
    module state (taskId/taskNum) and dispatches "updateMainTask", which
    refreshes the main quest UI.  Counters live in
    player.data["mainTaskProgress"] so they survive re-login."""
    # Capture the id BEFORE claim(): claim() advances the chain, and the
    # reward belongs to the task that was actually completed.
    claimed_id = tasks.get_task_id(player.data)
    completed = tasks.claim(player.data)
    response = tasks.task_response(player.data)
    if not completed:
        return response
    bag_ids: list[int] = []
    resource_ids: list[int] = []
    role_data = player.data.setdefault("RoleData", {})
    for item_id, num in v2configs.maintask_award_items(claimed_id):
        key = _REMAIN_LIST_MAP.get(item_id)
        if key is not None:
            # ItemIds 1-6 are the top resource bar.  RemainList carries the
            # new absolute value, which attach_remain_list builds from RoleData.
            role_data[key] = int(role_data.get(key) or 0) + int(num)
            resource_ids.append(int(item_id))
        elif item_id >= 100:
            _role_item_add(player, item_id, num)
            bag_ids.append(item_id)
    store.save(player)
    # Only the resources actually granted: the client renders one popup row
    # per RemainList entry, so untouched resources must not appear as +0.
    return attach_remain_list(response, player, items=bag_ids,
                              resources=resource_ids)


# Canonical client red-point map.  Every official v7 action response carries
# Data.RedPoint with these eight keys and numeric (1) values, i.e. the exact
# dict action_11901 already returns.  dataNetMod.serverUpToData feeds it to
# redPointMod.refreshRedPoint, whose "updateRedBall" listeners repaint the tab
# widgets: ui/myWidget.lua's tab selectIndex/showRedPoint path compares each
# value with `0 < count`.  When a screen action (11001 Rankings, 12311 Chat,
# 18121 Friends, 10015 Task claim, ...) omitted RedPoint the tab's red-point
# table was left with a nil number slot and `0 < nil` aborted the
# selectIndex -> showOverlay -> callBackFunc chain on device 2026-11-17
# 21:47:28-34 UTC+3.  Serve the same original dict on every response; no new
# values are invented (this is the very map the dev server already sends for
# 11901 and that the v7 capture shows on every action).
# --- Main-page red dots (redPointMod.lua) ----------------------------------
# net/dataNetMod.lua 636-642 hands Data.RedPoint to redPointMod.refreshRedPoint
# on EVERY action response.  refreshRedPoint (redPointMod.lua 258-369) loops a
# fixed key list and dispatches updateRedBall with isShow = whether the key is
# PRESENT in the map (line 324: `if L8_2 then L7_2 = true end`) -- the value is
# ignored there.  So a key sent as 0 STILL lights its dot; only OMITTING it
# hides the dot.  The main scene create instead uses updateRedPoint (215-254),
# where isShow = (value == 1).  The official capture varies the key set per
# action accordingly.  Our previous ~70-key all-present map force-lit every
# dot (including dailyTask/achievement) even with nothing claimable.
# The v2 main-page Quest/Achievement dots are the bottom-bar entries with
# red_name "dailyTask"/"achievement" (mainUi.bottomActiveDialog.lua 11/35).
def _daily_task_red_point(player: Player) -> bool:
    """Mirror _dailyTaskMod.updateRedPoint (133-172): claimable daily task."""
    state = _daily_state(player)
    for row in _daily_task_rows():
        tid = int(row.get("TaskId") or 0)
        if int(state["task_flags"].get(str(tid)) or 0):
            continue
        key = _DAILY_TASK_KEYS.get(tid, "")
        current = int(state["counters"].get(key) or 0) if key else 0
        need = int(row.get("RequireNum") or 0)
        if need > 0 and current >= need:
            return True
    activity = int(player.data.get("RoleActivity") or 0)
    for row in _activity_award_rows():
        aid = int(row.get("ActivityAwardId") or 0)
        if int(state["activity_flags"].get(str(aid)) or 0):
            continue
        if activity >= int(row.get("RequireActivity") or 0):
            return True
    return False


def _achievement_red_point(player: Player) -> bool:
    """Mirror _achieveMod.getIsRedShow (9-28): claimable achievement stage."""
    current = _achievement_current(player)
    stages = player.data.get("AchievementStages") or {}
    rows_by_aid: dict[int, list[dict]] = {}
    for row in _achievement_award_rows():
        aid = int(row.get("AchievementId") or 0)
        if aid:
            rows_by_aid.setdefault(aid, []).append(row)
    for aid, rows in rows_by_aid.items():
        stored = int(stages.get(str(aid)) or 0)
        max_stage = max(int(r.get("Stage") or 0) for r in rows)
        if stored >= max_stage:
            continue
        row = next((r for r in rows
                    if int(r.get("Stage") or 0) == stored + 1), None)
        if row is None:
            continue
        if int(current.get(aid) or 0) >= int(row.get("RequireNum") or 0):
            return True
    return False


def _client_red_point(player: Player | None = None) -> dict[str, int]:
    """Red-point map for v7 + v2 clients.  Presence drives the dots.

    The six static keys are the always-on activity dots in the v7 capture.
    dailyTask/achievement are attached only when real claimable state says so
    and OMITTED otherwise.  All other v2 keys stay absent: client mods manage
    them, and sending them (even as 0) force-lights their dots.  No "uni*" key
    may ever be produced: refreshRedPoint's copy loop does
    string.find(key, ":") + 1 for uni* (redPointMod.lua 284-297).
    """
    point = {
        "HaloCounsellorRankChange": 1, "SpiritPetRankChange": 1,
        "SpiritPetStage": 1, "FbFansFeedback": 1, "SpecialDiscount": 1,
        "boss": 1,
    }
    if player is not None:
        if _daily_task_red_point(player):
            point["dailyTask"] = 1
        if _achievement_red_point(player):
            point["achievement"] = 1
    return point


def attach_task_progress(data: dict, player: Player) -> dict:
    """Add the live task counters to any action response.

    dataNetMod.serverUpToData (net/dataNetMod.lua) reads A0_2.Extra.mainTask
    for every action response and calls _mainTaskMod.setMainTaskServerData,
    whose callback stores taskNum and dispatches "updateMainTask" -> the
    task dialog / main-scene bar refresh immediately.  The 10001/10002
    callbacks themselves only touch LevyTimeUTC, so without this the main
    quest UI would stay stale until the task dialog sends 10015.
    """
    data["SysTime"] = _now()
    # Must be set before the early-return below: red-point values need to be
    # real numbers (never nil) for the client's Lua tab comparisons.
    data["RedPoint"] = _client_red_point(player)
    try:
        taskId = int(tasks.get_task_id(player.data))
        taskNum = int(tasks.get_task_num(player.data))
    except Exception:
        return data
    extra = data.get("Extra")
    if not isinstance(extra, dict):
        extra = {}
        data["Extra"] = extra
    extra["mainTask"] = taskNum
    data["MainTask"] = {"TaskId": taskId, "TaskNum": taskNum}
    # dataNetMod reads Data.SysTime (NOT the envelope's SysTime) on every
    # action response and sets runtime.SysTime = SysTime*1000 - getTimer().
    # That clock drives every client countdown, including the levy timer in
    # homeScene.manageAssetsDialog.  Every official v7 action response carries
    # it inside Data.  Without it the clock is only seeded at login and never
    # resynced, so a countdown started from an action response can stay frozen.
    data["SysTime"] = _now()
    return data


# ItemId -> RoleData key, verbatim from the v2 client map in
# modules/_itemMod.lua (L3_1, lines 11-16).  When dataNetMod.serverUpToData
# (net/dataNetMod.lua lines 564-634) sees A0_2.RemainList in an action
# response it hands the entries without an ActivityId to
# _itemMod.setCurrRewardData, which for an ItemId present in this map calls
# roleMod.setRoleData(key, entry.Num) — the Num is the ABSOLUTE new value
# (roleMod.lua lines 631-656 store it, lines 602-627 then dispatch
# "updateTopRes", which the top resource bar listens to: ui/gameUi.lua
# line 1152 "updateTopRes" <- getResourceBar).  Without a RemainList the
# 10001/10002 and 10101 callbacks never touch Gold/Silver/Food/Soldier
# (_manageAssetsMod.lua lines 296-341, _ministerMod.lua lines 3095-3115),
# so the top bar stays stale after spending/collecting.
# Note: IncrRemainList (v7) does not exist anywhere in the v2 client.
# Note: the client key is "Fame"; our defaults store lowercase "fame", so
# ItemId 6 is only emitted when a "Fame" key is actually present.
_REMAIN_LIST_MAP = {
    1: "Gold",
    2: "Silver",
    3: "Food",
    4: "Soldier",
    5: "Exp",
    6: "Fame",
}


def attach_remain_list(data: dict, player: Player, items: list | None = None,
                       first_item_id: int | None = None,
                       resources: list[int] | None = None) -> dict:
    """Add the absolute post-action resource snapshot to an action response.

    Every entry is {"ItemId": id, "Num": absolute value}; the client
    overwrites its local state and refreshes the top bar:
      - resource ids (1-6) -> roleMod.setRoleData(key, Num)
      - bag item ids       -> _itemMod.getItemInfo/saveAward (absolute count)
    `items` lists bag ItemIds whose counts must ship alongside the resources
    (used when an action consumes or grants inventory items).  When
    `first_item_id` is given its entry is moved to the front: governmentScene
    updateEvent reads RemainList[1].ItemId to build the floating reward text,
    so the granted item must be first (scene ~1860-1915).  Existing response
    keys are preserved; data is returned.
    """
    # The client builds the floating reward popup from EVERY RemainList row,
    # so sending a full resource snapshot displays bogus "+0" entries for
    # resources this action did not grant.  Callers that know what changed
    # pass `resources` (item ids 1-6); the default keeps the old behavior for
    # handlers whose grants are not tracked.
    only_ids = set(int(i) for i in resources) if resources is not None else None
    role_data = player.data.get("RoleData") or {}
    remain = []
    for item_id in sorted(_REMAIN_LIST_MAP):
        if only_ids is not None and item_id not in only_ids:
            continue
        key = _REMAIN_LIST_MAP[item_id]
        if key not in role_data:
            continue
        try:
            num = int(role_data.get(key) or 0)
        except (TypeError, ValueError):
            num = 0
        remain.append({"ItemId": item_id, "Num": num})
    if items:
        counts = {int(e.get("ItemId") or 0): int(e.get("Num") or 0) for e in _role_items(player)}
        for item_id in dict.fromkeys(int(i) for i in items):
            remain.append({"ItemId": int(item_id), "Num": counts.get(int(item_id), 0)})
    if first_item_id is not None:
        for index, entry in enumerate(remain):
            if int(entry.get("ItemId") or 0) == int(first_item_id):
                remain.insert(0, remain.pop(index))
                break
    data["RemainList"] = remain
    return data


# --------------------------------------------------------------------------
# RoleItem (bag) helpers: entries are {ItemId, Num}; login ships the list
# verbatim and the client keys it with tbItemDataToKey (_itemMod.lua line 228).
# Items whose count reaches 0 are dropped, matching the client's
# getItemInfo() which treats a Num==0 entry as absent.
# --------------------------------------------------------------------------
def _role_items(player: Player) -> list:
    items = player.data.get("RoleItem")
    if not isinstance(items, list):
        items = []
        player.data["RoleItem"] = items
    return items


def _role_item_count(player: Player, item_id: int) -> int:
    for entry in _role_items(player):
        if isinstance(entry, dict) and int(entry.get("ItemId") or 0) == int(item_id):
            return int(entry.get("Num") or 0)
    return 0


def _role_item_consume(player: Player, item_id: int, num: int) -> bool:
    items = _role_items(player)
    for index, entry in enumerate(items):
        if isinstance(entry, dict) and int(entry.get("ItemId") or 0) == int(item_id):
            left = int(entry.get("Num") or 0) - int(num)
            if left > 0:
                entry["Num"] = left
            else:
                del items[index]
            return True
    return False


def _role_item_add(player: Player, item_id: int, num: int) -> None:
    items = _role_items(player)
    for entry in items:
        if isinstance(entry, dict) and int(entry.get("ItemId") or 0) == int(item_id):
            entry["Num"] = int(entry.get("Num") or 0) + int(num)
            return
    items.append({"ItemId": int(item_id), "Num": int(num)})


PHASES_PER_SECTION = 8   # TcPhaseCfg rows 1..8
SECTIONS_PER_CHAPTER = 5  # TcSectionCfg: 5 sections per chapter


def _phase_name(phase: int) -> str:
    rows = v2configs.load("TcPhaseCfg")
    if isinstance(rows, dict):
        for row in rows.values():
            if isinstance(row, dict) and int(row.get("PhaseId") or 0) == phase:
                return str(row.get("Name") or f"PhaseCfg_{phase:03d}_Name")
    return f"PhaseCfg_{phase:03d}_Name"


def _phase_cfg(chapter: int, section: int, phase: int) -> dict:
    """PhaseCfg display slice read by _stageMod.getPower/getSoldier.

    The official v7 capture for chapter 1 gives:
      1-1 p1 Force 102 Soldiers 1000, +2/+50 per phase,
      1-2 p1 Force 204 Soldiers 2000 (same step inside the section).
    That is Force = 2*section*(phase+50) and Soldiers = section*(950+50*phase)
    for chapter 1; later chapters scale linearly until a capture proves
    otherwise.
    """
    chapter = max(1, int(chapter))
    section = max(1, int(section))
    phase = max(1, int(phase))
    return {
        "PhaseId": phase,
        "Name": _phase_name(phase),
        "Force": 2 * chapter * section * (phase + 50),
        "Soldiers": chapter * section * (950 + 50 * phase),
        "SoldierDead": "MIDDLE" if section <= 1 else "SERIOUS",
    }


def _next_stage(chapter: int, section: int, phase: int) -> tuple[int, int, int]:
    if phase < PHASES_PER_SECTION:
        return chapter, section, phase + 1
    if section < SECTIONS_PER_CHAPTER:
        return chapter, section + 1, 1
    return chapter + 1, 1, 1


def _stage_exp(chapter: int, section: int, phase: int) -> int:
    """EXP for clearing one campaign phase.

    Official v7 capture (fresh Lv1 account, same tutorial point):
      1-1 p1..p8 -> 23,28,33,38,43,48,53,58
      1-2 p1     -> 63
    i.e. 18 + 5 * phase_index within the chapter (8 phases per section).
    """
    index = (max(1, int(section)) - 1) * PHASES_PER_SECTION + max(1, int(phase))
    return 18 + 5 * index


# Official v7 10201 RemainList (session-20261116-093154.log, 2026-11-16):
#   ItemId 4 (Soldier, after loss), ItemId 2 (Silver), ItemId 5 (Exp), in
#   that exact order.  Silver rises by a constant +1022 per phase; EXP is
#   _stage_exp().  The soldier losses below are the deltas of the captured
#   absolute ItemId 4 values (e.g. 349870 -> 349095 = 775 for 1-1 p1).
_STAGE_SILVER_GAIN = 1022
_STAGE_SOLDIER_LOSS = {
    # chapter 1, section 1, phases 1..7 (phase 8 was not captured separately)
    (1, 1): {1: 775, 2: 827, 3: 881, 4: 937, 5: 993, 6: 1052, 7: 1111},
    # chapter 1, section 2, phases 1..5
    (1, 2): {1: 2894, 2: 3098, 3: 3308, 4: 3524, 5: 3745},
}


# --- Tactics-scaled losses on the approximate fallback ---------------------
# Community research (archived in recon/community-research/, RU wiki [S13]):
#   "if your total Tactics is 2x the enemy's, you lose 2x fewer soldiers".
# The v2 configs carry no enemy-attribute field, so the enemy side stays
# folded into the capture-derived base below (Soldiers already scales with
# chapter/section/phase).  We scale ONLY by the player's Tactics relative to
# a fresh starter account, computed from the original TcTalent/TcStar configs
# and the client's own formula in ministerDialog.lua:
#   attr = floor((10 + 0.1*(Lv-1)*(Lv+2)) * sum(StarLevel * talentLevel))
# ministerDialog.lua attribute order: [1] Military/Tactics, [2] Research,
# [3] Politics, [4] Prestige.  Values are still APPROXIMATIONS, never claimed
# as captured data.
_TACTICS_ATTR_INDEX = 1
_TACTICS_LOSS_MIN = 0.25   # cap the "more losses" side at 4x
_TACTICS_LOSS_MAX = 2.0
_starter_tactics_cache: int | None = None


def _talent_cfg() -> dict[int, dict]:
    return {
        int(row.get("Id") or 0): row
        for row in v2configs.load("TcTalent")
        if isinstance(row, dict)
    }


def _counsellor_tactics_attr(c: dict, talents: dict[int, dict]) -> int:
    """Tactics attribute of one vizier, using the client's formula."""
    points = 0
    for pair in c.get("Talent") or []:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        row = talents.get(int(pair[0]) or 0)
        if not row or int(row.get("TalentType") or 0) != _TACTICS_ATTR_INDEX:
            continue
        points += int(pair[1] or 0) * int(row.get("StarLevel") or 0)
    lvl = max(1, int(c.get("Lv") or 1))
    attr = int(math.floor((10 + 0.1 * (lvl - 1) * (lvl + 2)) * points))
    # Attribute books are a flat addend on the same index (client PillAdd /
    # BeautyAdd / HaloAdd are display-only bonuses and left out deliberately).
    values = c.get("AttributeValue")
    if isinstance(values, (list, tuple)) and len(values) > _TACTICS_ATTR_INDEX:
        attr += int(values[_TACTICS_ATTR_INDEX] or 0)
    return max(0, attr)


def _player_tactics(player: Player | None) -> int:
    """Total Tactics of a player's unlocked viziers (starter baseline for None)."""
    source = (player.data.get("RoleCounsellor") if player is not None else None)
    if source is None:
        source = [defaults.default_counsellor(i) for i in (1, 2, 3)]
    talents = _talent_cfg()
    total = 0
    for c in source or []:
        if isinstance(c, dict) and not int(c.get("IsLock") or 0):
            total += _counsellor_tactics_attr(c, talents)
    return max(1, total)


def _starter_tactics() -> int:
    global _starter_tactics_cache
    if _starter_tactics_cache is None:
        _starter_tactics_cache = _player_tactics(None)
    return _starter_tactics_cache


def _stage_soldier_loss(chapter: int, section: int, phase: int,
                        player: Player | None = None) -> int:
    """Soldiers lost clearing one campaign phase.

    Exact, capture-verified values are used for chapter 1 sections 1/2 and are
    never modified.  Everything else is an explicitly APPROXIMATE fallback:
        floor(PhaseCfg.Soldiers * 0.75) * clamp(starter_tactics / player_tactics)
    so a vizier team with 2x the starter Tactics loses half as many soldiers.
    No loss schedule exists in the v2 configs, so no other value is claimed as
    verified.  ``player=None`` keeps the old fallback for callers/tests that
    have no player context (factor 1.0).
    """
    exact = _STAGE_SOLDIER_LOSS.get((int(chapter), int(section)), {}).get(int(phase))
    if exact is not None:
        return int(exact)
    soldiers = int(_phase_cfg(chapter, section, phase).get("Soldiers") or 0)
    base = int(math.floor(soldiers * 0.75))  # capture-derived, not verified here
    if player is None:
        return base
    factor = _starter_tactics() / _player_tactics(player)
    factor = min(_TACTICS_LOSS_MAX, max(_TACTICS_LOSS_MIN, factor))
    return int(math.floor(base * factor))


# User-directed deviation from the official capture: the two SCRIPTED
# tutorial battles (actions 10202/10204, guide statuses "stage"/"stage2")
# carry no reward fields in v7, so the client shows all-zero Victory screens.
# Per explicit request, grant a one-time 20 EXP / 1000 Silver per scripted
# stage so the popup has real values.  These amounts are user-specified, NOT
# derived from the v7 oracle; real 10201 fights keep their captured rewards.
_TUTORIAL_BATTLE_EXP = 20
_TUTORIAL_BATTLE_SILVER = 1000


def _tutorial_battle_remain_list(player: Player, store: Store) -> list[dict] | None:
    """Pay each scripted tutorial battle once and return its reward rows."""
    role = player.data.get("RoleData") if isinstance(player.data.get("RoleData"), dict) else {}
    status = str(role.get("LastStatus") or "")
    name, _, _tail = status.rpartition("-")
    if name not in ("stage", "stage2"):
        return None
    granted = player.data.get("TutorialBattleRewards")
    if not isinstance(granted, dict):
        granted = {}
        player.data["TutorialBattleRewards"] = granted
    if granted.get(name):
        return None
    role["Exp"] = int(role.get("Exp") or 0) + _TUTORIAL_BATTLE_EXP
    role["Silver"] = int(role.get("Silver") or 0) + _TUTORIAL_BATTLE_SILVER
    granted[name] = 1
    store.save(player)
    return [{"ItemId": 5, "Num": int(role["Exp"])},
            {"ItemId": 2, "Num": int(role["Silver"])}]


def action_stage_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    section = _section_record(player)
    result = {
        "SectionRecord": section,
        "PhaseCfg": _phase_cfg(section.get("ChapterId", 1),
                               section.get("SectionId", 1),
                               section.get("PhaseId", 1)),
        "CityAwardGet": [],
        "MinisterList": [],
        "SectionAward": {},
    }
    reward = _tutorial_battle_remain_list(player, store)
    if reward is not None:
        result["RemainList"] = reward
    return result


def action_stage_result(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_stageMod action10204: the scripted battle's result/confirmation step.
    Start from the full-state fallback (the client reads Artillery* fields
    from it) and attach the one-time tutorial reward when this status is a
    scripted stage that has not been paid yet.
    """
    result = action_full_state(payload, player, session, store)
    reward = _tutorial_battle_remain_list(player, store)
    if reward is not None:
        result["RemainList"] = reward
    return result


def action_stage_fight(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_stageMod.action10201: one campaign phase battle.

    The request carries only UseArtillery; the server owns the result.  Clear
    the current phase, grant the official EXP drop (ItemId 5, absolute value)
    and advance RoleSection.  Character EXP is what lets the tutorial's
    "upLv" step finish (TcLvUpgradeCfg level 2 needs 200).
    """
    state = player.data.setdefault("RoleSection", {})
    chapter = int(state.get("ChapterId") or 1)
    section_id = int(state.get("SectionId") or 1)
    phase_id = int(state.get("PhaseId") or 1)

    role_data = player.data.setdefault("RoleData", {})
    exp = int(role_data.get("Exp") or 0) + _stage_exp(chapter, section_id, phase_id)
    role_data["Exp"] = exp
    # v7 10201 also pays a constant Silver drop and consumes soldiers.  Keep
    # RoleData as the single source of truth so the 3-entry absolute
    # RemainList below mirrors the client's post-battle state exactly.
    soldier = max(0, int(role_data.get("Soldier") or 0)
                  - _stage_soldier_loss(chapter, section_id, phase_id, player))
    role_data["Soldier"] = soldier
    silver = int(role_data.get("Silver") or 0) + _STAGE_SILVER_GAIN
    role_data["Silver"] = silver

    next_chapter, next_section, next_phase = _next_stage(chapter, section_id, phase_id)
    state["ChapterId"] = next_chapter
    state["SectionId"] = next_section
    state["PhaseId"] = next_phase
    state["Soldiers"] = _phase_cfg(next_chapter, next_section, next_phase)["Soldiers"]
    state["PlayAnimation"] = 1
    tasks.apply_stage_clear(player.data, chapter, section_id, phase_id)
    # Lifetime/daily progress for achievements and the Daily Tasks menu; the
    # tutorial EXP/phase logic above is untouched.
    _bump_achievement(player, "CampaignVictories")
    _bump_daily(player, "Campaign")
    store.save(player)

    return {
        "SectionRecord": _section_record(player),
        "PhaseCfg": _phase_cfg(next_chapter, next_section, next_phase),
        "CityAwardGet": [],
        "CompetitionRankClose": 0,
        # Order is the v7 capture order: Soldier, Silver, Exp.  Do NOT use
        # attach_remain_list here: the client expects exactly these 3 entries.
        "RemainList": [
            {"ItemId": 4, "Num": soldier},
            {"ItemId": 2, "Num": silver},
            {"ItemId": 5, "Num": exp},
        ],
        "SysTime": _now(),
    }


def _levy_stage_cd(player: Player) -> int:
    """_manageAssetsMod.get_stage_cd (modules/_manageAssetsMod.lua 18-37):

        min(ceil(RolePower / 10000) * 60, 1800)

    The 60-second step and 1800-second cap are the client's own literals;
    10000 is its power divisor.  RoleData.Power defaults to 1000 on this
    server (defaults.py 98), so a fresh account's cd is 60 s.  Guard against
    a zero Power (the client would divide by zero in sceneTimeDataHandler):
    a cooldown of at least one second keeps the countdown finite.
    """
    power = int((player.data.get("RoleData") or {}).get("Power") or 0)
    cd = min(math.ceil(power / 10000) * 60, 1800)
    return max(1, cd)


def _levy_max_gain(player: Player) -> int:
    """_manageAssetsMod.get_max_GainTimes (modules/_manageAssetsMod.lua
    39-58): TcLvUpgradeCfg[RoleData.Lv].GainTimes (3 at Lv 1)."""
    level = int((player.data.get("RoleData") or {}).get("Lv") or 1)
    for row in v2configs.load("TcLvUpgradeCfg"):
        if isinstance(row, dict) and int(row.get("Level") or 0) == level:
            return int(row.get("GainTimes") or 0)
    return 0


def _levy_timestamps(player: Player, now: int) -> list[int]:
    """Three last-tick anchors (1=Silver, 2=Food, 3=Soldier).

    homeScene.manageAssetsDialog.lua sceneTimeDataHandler (422-454) reads
    LevyTimeUTC[i] and computes remainCount = min(floor((now-ts)/cd), max).
    A stored anchor in the future would make that negative, so clamp to now.
    """
    role_data = player.data.setdefault("RoleData", {})
    stamps = role_data.get("LevyTimeUTC")
    if not isinstance(stamps, list) or len(stamps) != 3:
        stamps = [now, now, now]
    else:
        stamps = [int(s or 0) for s in stamps[:3]]
    stamps = [min(s, now) for s in stamps]
    role_data["LevyTimeUTC"] = stamps
    return stamps


def _levy_index(value) -> int:
    """CollectType/RecoverType -> 0-based index; 0/unknown means 'all'."""
    try:
        index = int(value)
    except (TypeError, ValueError):
        return 0
    return index if index in (1, 2, 3) else 0


def _levy_buff_add(player: Player) -> float:
    """_manageAssetsMod.getBuffAdd: the BuffAdd cached from action 10003.

    requireWarBuff (modules/_manageAssetsMod.lua 365-390) stores the response's
    BuffAdd in a module-level slot; our 10003 handler returns 0, so this is 0
    on a normal account.  Read an explicitly persisted value if one exists so
    the server credits exactly what the card shows when a buff is active.
    """
    for container in (player.data.get("RoleData") or {}, player.data or {}):
        if isinstance(container, dict) and container.get("BuffAdd") is not None:
            try:
                return float(container.get("BuffAdd") or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _levy_revenue(player: Player) -> dict[int, int]:
    """Per-tick production credited by a levy, keyed by ItemId 2/3/4.

    EXACT v2 client derivation (scene/homeScene.manageAssetsDialog.lua L9_1,
    lines 816-848 + 1178-1188): each resource card's "+N" is

        N = floor(getRolePower(ItemId) * (1 + BuffAdd))

    with card 1 -> ItemId 2 (Silver), card 2 -> ItemId 3 (Food), card 3 ->
    ItemId 4 (Soldier).  getRolePower is the already-verified config port
    _gov_role_power; on the live devtest viziers (Lv 1/45/3) it returns
    291 / 258 / 475, exactly the three numbers on the phone screenshot.

    The official v7 oracle confirms the same numbers are the server-side
    revenue: EstimatedRevenue {'2':180,'3':147,'4':198} (and later
    {'2':2200,'3':2155,'4':2218}), with each RemainList total rising by
    exactly EstimatedRevenue[ItemId] per collect.
    """
    buff = _levy_buff_add(player)
    revenue: dict[int, int] = {}
    for item_id in (2, 3, 4):
        base = _gov_role_power(player, item_id)
        if buff:
            base = int(math.floor(base * (1 + buff)))
        revenue[item_id] = max(0, int(base))
    return revenue


def _levy_ticks_consumed(old_ts: int, new_ts: int, now: int, cd: int,
                         max_gain: int) -> int:
    """How many complete queued levy ticks a single collect really consumed.

    LevyTimeUTC is a LAST-TICK anchor; remainCount (sceneTimeDataHandler
    422-453) is min(floor((now-ts)/cd), GainTimes).  A collect advances the
    anchor by at most one cd, so the queued count drops by one whenever a tick
    was actually available.  A stale anchor is capped to GainTimes*cd first,
    so the surplus idle time is discarded (v7 semantics) -- never converted
    into a giant multi-tick grant.  An anchor already at `now` yields 0 ticks.
    """
    def queued(ts: int) -> int:
        return min(max_gain, max(0, math.floor((now - int(ts)) / cd)))
    return max(0, queued(old_ts) - queued(new_ts))


def _levy_v7_fields(over_times: list[int], revenue: dict[int, int] | None = None,
                    levy_list: dict | None = None, miracle: bool = False) -> dict:
    """v7-shaped 10001/10002 fields.

    EstimatedRevenue now carries the config-derived per-tick production the
    client displays, mirroring the official capture (v7_actions.jsonl 10001:
    EstimatedRevenue {'2':180,'3':147,'4':198}, LevyList
    {'1':[{'ItemId':2,'Num':180}]}).  The v2 client has no reader for these
    maps -- they are kept for wire parity and as the authoritative amount the
    server credits through RemainList.
    """
    return {
        "OverTimes": list(over_times),
        "EstimatedRevenue": {str(k): int(v) for k, v in (revenue or {}).items()},
        "LevyList": levy_list or {},
        "LevyNum": 0,
        "LevyTriggerNum": 0,
        "LevySoldierNum": 0,
        "MiracleTrigger": miracle,
    }


def action_manage_assets(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_manageAssetsMod.collect (modules/_manageAssetsMod.lua 293-322).

    EXACT v7 semantics (recon/v7-capture/v7_actions.jsonl 10001 responses):
    LevyTimeUTC[i] is a LAST-TICK anchor.  A collect consumes ONE queued tick:

        base = max(old_ts, now - GainTimes*stage_cd)   # cap the queued levies
        new_ts = min(now, base + stage_cd)             # advance exactly one
        OverTimes = [now - new_ts]

    Capture proof (Power 1000 -> cd 60, GainTimes 3): 1794735136 -> 1794821561
    (= now-120), -> 1794821621 (= now-61), -> 1794821681 (= now-2), with
    OverTimes [120], [61], [2].  Setting the anchor to `now` (our earlier bug)
    consumed every queued levy at once and froze the client's remainCount.
    Only the collected resource's anchor moves; the other two are preserved.

    Each consumed tick now credits the per-resource production the v2 card
    displays ("+N", see _levy_revenue): Silver +floor(getRolePower(2)), Food
    +floor(getRolePower(3)), Soldier +floor(getRolePower(4)).  The new absolute
    total is persisted in RoleData and shipped first in RemainList so the top
    bar refreshes.  The v7 oracle proves the rule: RemainList 1180 -> 1360
    after a collect whose EstimatedRevenue[2] == 180.
    """
    now = _now()
    role_data = player.data.setdefault("RoleData", {})
    stamps = _levy_timestamps(player, now)
    index = _levy_index(payload.get("CollectType"))
    cd = _levy_stage_cd(player)
    max_gain = _levy_max_gain(player)
    revenue = _levy_revenue(player)
    grants: list[tuple[int, int]] = []  # (ItemId, amount) in collection order
    over_times = [0]
    if index:
        old = stamps[index - 1]
        base = max(old, now - max_gain * cd)
        stamps[index - 1] = min(now, base + cd)
        over_times = [max(0, now - stamps[index - 1])]
        ticks = _levy_ticks_consumed(old, stamps[index - 1], now, cd, max_gain)
        if ticks:
            grants.append((index + 1, revenue[index + 1] * ticks))
        levy_key = ("LevySilver", "LevyGrain", "LevySoldiers")[index - 1]
        _bump_achievement(player, levy_key)
        _bump_daily(player, levy_key)
    else:
        # CollectType 4 = one-key collect: consume exactly one queued tick
        # from EACH resource (the client's auto-levy button).  Never reset
        # the anchors to now -- that discards every queued levy and is what
        # made the countdown look frozen.  The v7 oracle has no 4 sample,
        # but the per-resource +cd rule is the same one verified for 1/2/3.
        # All consumed ticks are granted (sum over the three resources).
        new_stamps = []
        for slot, old in enumerate(stamps):
            item_id = slot + 2  # LevyTimeUTC slot 0/1/2 -> ItemId 2/3/4
            base = max(old, now - max_gain * cd)
            new_ts = min(now, base + cd)
            new_stamps.append(new_ts)
            ticks = _levy_ticks_consumed(old, new_ts, now, cd, max_gain)
            if ticks:
                grants.append((item_id, revenue[item_id] * ticks))
        stamps = new_stamps
        over_times = [max(max(0, now - s) for s in stamps)]
        for key in ("LevySilver", "LevyGrain", "LevySoldiers"):
            _bump_achievement(player, key)
            _bump_daily(player, key)
    role_data["LevyTimeUTC"] = stamps
    # Sultan Boost: 15% chance (ConfigEnvSet) to multiply levy earnings 10x.
    miracle = _miracle_roll() if grants else False
    if miracle:
        grants = [(item_id, amount * 10) for item_id, amount in grants]
    # Persist the absolute post-grant totals; the client overwrites its own
    # RoleData from RemainList (there is no other resource channel in v2).
    for item_id, amount in grants:
        key = _REMAIN_LIST_MAP[item_id]
        role_data[key] = int(role_data.get(key) or 0) + amount
    tasks.apply_levy(player.data, tasks.LEVY_TYPE_BY_INDEX.get(index))
    store.save(player)
    # _manageAssetsMod's 10001 callback only stores LevyTimeUTC; the resource
    # gain reaches the top bar solely through RemainList -> setCurrRewardData.
    first_item_id = grants[0][0] if grants else None
    levy_list = {str(index or 4): [{"ItemId": i, "Num": n} for i, n in grants]}
    data = {"LevyTimeUTC": list(stamps), "MiracleTrigger": miracle}
    data.update(_levy_v7_fields(over_times, revenue, levy_list, miracle=miracle))
    data = attach_task_progress(data, player)
    # Diagnostic: record the exact wire values for a collect so we can prove
    # the client clock resync (Data.SysTime), the anchor and the granted total.
    try:
        with open(config.LOG_DIR / "levy_debug.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": _now(),
                "uid": player.uid,
                "collect_type": payload.get("CollectType"),
                "LevyTimeUTC": list(stamps),
                "Revenue": revenue,
                "Granted": grants,
                "SysTime": data.get("SysTime"),
                "OverTimes": data.get("OverTimes"),
            }) + "\n")
    except Exception:
        pass
    return attach_remain_list(data, player, first_item_id=first_item_id)


def action_recover(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_manageAssetsMod.recover (modules/_manageAssetsMod.lua 324-355).

    No 10002 responses exist in the v7 capture, so keep the established
    backward shift: each recovered tick moves ONLY that resource's last-tick
    anchor back by exactly one stage_cd (clamped at GainTimes*cd), which makes
    sceneTimeDataHandler report one more available levy.  Mirrors the v7
    10001 wire fields (including the config-derived EstimatedRevenue).

    A recovery does not itself consume a levy tick -- it ADDS collectable
    ticks.  The client's recover callback (modules/_manageAssetsMod.lua
    324-355) only stores the new LevyTimeUTC and flies no reward animation;
    the resources are credited later by the matching 10001 collect.  So no
    amount is credited here (that would double-grant the same levy), but the
    recovered resource is placed first in the absolute RemainList and its
    production is still advertised in EstimatedRevenue.
    """
    now = _now()
    role_data = player.data.setdefault("RoleData", {})
    stamps = _levy_timestamps(player, now)
    index = _levy_index(payload.get("RecoverType"))
    revenue = _levy_revenue(player)
    try:
        times = int(payload.get("RecoverTimes") or 0)
    except (TypeError, ValueError):
        times = 0
    over_times = [0]
    if index and times > 0:
        cd = _levy_stage_cd(player)
        floor_ts = now - _levy_max_gain(player) * cd
        stamps[index - 1] = max(floor_ts, stamps[index - 1] - times * cd)
        over_times = [max(0, now - stamps[index - 1])]
    role_data["LevyTimeUTC"] = stamps
    tasks.apply_levy(player.data, tasks.LEVY_TYPE_BY_INDEX.get(index))
    store.save(player)
    data = {"LevyTimeUTC": list(stamps)}
    data.update(_levy_v7_fields(over_times, revenue))
    return attach_remain_list(attach_task_progress(data, player), player,
                              first_item_id=(index + 1 if index else None))


def action_war_buff(payload: dict, player: Player, session: Session, store: Store) -> dict:
    return {"BuffAdd": 0}


def action_worship_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    return {"fashionDict": {}}


def action_princess_list(payload: dict, player: Player, session: Session, store: Store) -> dict:
    return {"record": []}


def action_rename(payload: dict, player: Player, session: Session, store: Store) -> dict:
    # createRoleDialog sends {Rid, Name, Sex, Avatar}; the in-game rename dialog
    # sends {NewName}.  Accept both shapes.
    new_name = str(payload.get("NewName") or payload.get("Name") or "").strip()
    if not new_name:
        return {"Stat": 1, "Info": "empty name"}
    rb = player.data.setdefault("RoleBase", {})
    rb["Nickname"] = new_name
    rb["Name"] = new_name
    if payload.get("Sex") is not None:
        rb["Sex"] = payload["Sex"]
    if payload.get("Avatar") is not None:
        rb["Avatar"] = payload["Avatar"]
    player.data.setdefault("RoleData", {})["Nickname"] = new_name
    store.save(player)
    return {"Stat": 0, "NewName": new_name}


def action_items(payload: dict, player: Player, session: Session, store: Store) -> dict:
    return action_full_state(payload, player, session, store)


def _alliance_neutral(guid: str) -> dict:
    """No-alliance envelope for 11601.  AllianceId is OMITTED (Lua 0 is
    truthy, allianceScene.lua:27-29); ContributionTime is omitted because a
    numeric 0 crashes the client's dateToNumber (logcat 13:23:56).
    """
    return {
        "AllianceData": {
            "AllianceGuid": guid, "AllianceName": "",
            "AllianceLv": 1, "AllianceLeader": "", "AlliancePower": 0,
            "FaceBook": "", "AllianceExp": 0, "AllianceFortune": 0,
            "AllianceMemberNum": 0, "Position": 0,
            "ContributionType": 0, "ContributionNum": 0, "ContributionTop": 0,
            "InsideNotice": "", "OutsideNotice": "", "QQGroup": "",
            "WeChatNum": "", "IsOpenBoss": 0, "NeedCheck": 0,
            "ResetPassword": 0, "FlagId": 1,
        },
        "AllianceWarOpen": 0, "AllianceWarUTC": 0, "NewWar": 0,
        "LastDamageRole": 0, "RefusedList": [], "TradeInfo": [],
    }


# Minimal union registry for 11602 create / 11603 join / 11607 inquire /
# 11617+11629 rank.  No 116xx response exists in the v7 capture, so the
# on-disk layout is a dev-server choice; client-visible values come from the
# request or the original config baseline (TcAllianceLvCfg row 1, level 1;
# TcAlliancePosition: leader=4, member=1).
_alliance_lock = threading.RLock()  # nested helpers re-enter it
_alliance_file = config.DATA_DIR / "alliances.json"
_alliance_state: dict = {}
_alliance_loaded = False


def _alliance_reset(file_path=None) -> None:
    """Test hook: isolate/clear the registry."""
    global _alliance_file, _alliance_loaded
    with _alliance_lock:
        if file_path is not None:
            _alliance_file = file_path
        _alliance_state.clear()
        _alliance_loaded = False


def _alliance_registry() -> dict:
    global _alliance_loaded
    with _alliance_lock:
        if not _alliance_loaded:
            try:
                raw = json.loads(_alliance_file.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
            if isinstance(raw, dict):
                _alliance_state.update(raw)
            _alliance_state.setdefault("next_id", 1)
            _alliance_state.setdefault("items", {})
            _alliance_loaded = True
        return _alliance_state


def _alliance_save() -> None:
    with _alliance_lock:
        tmp = _alliance_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(_alliance_state, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(_alliance_file)


def _alliance_member_of(player: Player) -> tuple:
    base = player.data.get("RoleBase") if isinstance(player.data.get("RoleBase"), dict) else {}
    guid = base.get("AllianceGuid") or player.data.get("AllianceGuid")
    guid = str(guid) if guid else ""
    alliance = _alliance_registry()["items"].get(guid)
    if not isinstance(alliance, dict):
        return None, None
    membership = (alliance.get("Members") or {}).get(player.uid)
    if not isinstance(membership, dict):
        return None, None
    return guid, membership


def _alliance_join_member(alliance: dict, player: Player, position: int) -> dict:
    rb = player.data.setdefault("RoleBase", {})
    role = player.data.get("RoleData") if isinstance(player.data.get("RoleData"), dict) else {}
    membership = {
        "Position": position,
        "JoinTime": _game_date_string(),
        "Power": _as_int(role.get("Power") or rb.get("Power")),
        "Name": str(rb.get("Nickname") or role.get("Nickname") or ""),
    }
    members = alliance.setdefault("Members", {})
    members[player.uid] = membership
    alliance["AlliancePower"] = sum(_as_int(m.get("Power")) for m in members.values())
    rb["AllianceGuid"] = str(alliance.get("AllianceGuid") or "")
    rb["AllianceId"] = _as_int(alliance.get("AllianceId"))
    return membership


def _alliance_envelope(alliance: dict, membership: dict) -> dict:
    members = alliance.get("Members") or {}
    return {
        "AllianceId": _as_int(alliance.get("AllianceId")),
        "AllianceGuid": str(alliance.get("AllianceGuid") or ""),
        "AllianceName": str(alliance.get("AllianceName") or ""),
        # allianceJoinedDialog.new indexes filterTcData("TcAllianceLvCfg",
        # {AllianceLv=...}).TopMemberLimit with no nil guard (2110-2131);
        # level 1 is a real config row, level 0 crashes (logcat 13:23:51).
        "AllianceLv": max(1, _as_int(alliance.get("AllianceLv"), 1)),
        "AllianceLeader": str(alliance.get("AllianceLeader") or ""),
        "AlliancePower": _as_int(alliance.get("AlliancePower")),
        "AllianceExp": _as_int(alliance.get("AllianceExp")),
        "AllianceFortune": _as_int(alliance.get("AllianceFortune")),
        "AllianceMemberNum": len(members),
        "Position": _as_int(membership.get("Position"), 1),
        "ContributionType": 0,
        # dateToNumber parses only "YYYY-MM-DD HH:MM:SS"; a number crashes
        # with "arithmetic on field 'year'" (logcat 13:23:56).
        "ContributionTime": str(membership.get("JoinTime") or _game_date_string()),
        "ContributionNum": 0, "ContributionTop": 0,
        "InsideNotice": str(alliance.get("InsideNotice") or ""),
        "OutsideNotice": str(alliance.get("OutsideNotice") or ""),
        "FaceBook": str(alliance.get("FaceBook") or ""),
        "QQGroup": str(alliance.get("QQGroup") or ""),
        "WeChatNum": str(alliance.get("WeChatNum") or ""),
        "IsOpenBoss": _as_int(alliance.get("IsOpenBoss")),
        "ApplyNum": _as_int(alliance.get("ApplyNum")),
        "NeedCheck": _as_int(alliance.get("NeedCheck")),
        "ResetPassword": 0,
        "FlagId": max(1, _as_int(alliance.get("FlagId"), 1)),
        "ExchangeItems": [],
    }


def _alliance_envelope_full(data: dict) -> dict:
    return {
        "AllianceData": data,
        "AllianceWarOpen": 0, "AllianceWarUTC": 0, "NewWar": 0,
        "LastDamageRole": 0, "RefusedList": [], "TradeInfo": [],
    }


def _alliance_row(alliance: dict, rank: int) -> dict:
    return {
        "Rank": rank,
        "AllianceId": _as_int(alliance.get("AllianceId")),
        "AllianceGuid": str(alliance.get("AllianceGuid") or ""),
        "AllianceName": str(alliance.get("AllianceName") or ""),
        "AllianceLv": max(1, _as_int(alliance.get("AllianceLv"), 1)),
        "AllianceLeader": str(alliance.get("AllianceLeader") or ""),
        "AlliancePower": _as_int(alliance.get("AlliancePower")),
        "AllianceMenNum": len(alliance.get("Members") or {}),
        "FlagId": max(1, _as_int(alliance.get("FlagId"), 1)),
        "HadApply": 0,
        "OutsideNotice": str(alliance.get("OutsideNotice") or ""),
    }


def _alliance_rows() -> list:
    items = _alliance_registry()["items"]
    order = sorted(items.values(),
                   key=lambda a: (_as_int(a.get("AlliancePower")),
                                  _as_int(a.get("AllianceId"))), reverse=True)
    return [_alliance_row(a, rank) for rank, a in enumerate(order, 1)]


def action_alliance_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11601: alliance state.  A member gets their union's data;
    everyone else the neutral no-alliance envelope.  Date-like fields are
    omitted (nil-safe in the client's copy chain).
    """
    guid, membership = _alliance_member_of(player)
    if guid:
        return _alliance_envelope_full(
            _alliance_envelope(_alliance_registry()["items"][guid], membership))
    base = player.data.get("RoleBase") if isinstance(player.data.get("RoleBase"), dict) else {}
    guid = str(base.get("AllianceGuid") or player.data.get("AllianceGuid") or "")
    return _alliance_neutral(guid)


def action_alliance_join(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11603: join / apply / random join (random sends {}).
    With no approval-free union, refuse with Code 1 so the client shows a
    toast instead of opening a phantom joined dialog.
    """
    with _alliance_lock:
        items = _alliance_registry()["items"]
        guid, membership = _alliance_member_of(player)
        if guid:
            out = _alliance_envelope_full(_alliance_envelope(items[guid], membership))
            out["IsJoin"] = 1
            return out
        target = str(payload.get("AllianceGuid") or "").strip()
        if not target:
            candidates = [g for g, a in items.items() if not _as_int(a.get("NeedCheck"))]
            if not candidates:
                return {"_fail": "No union available right now"}
            target = random.choice(candidates)
            alliance = items[target]
        else:
            alliance = items.get(target)
            if not isinstance(alliance, dict):
                return {"_fail": "Union not found"}
            if _as_int(alliance.get("NeedCheck")):
                return {"IsJoin": 0,
                        "AllianceData": _alliance_envelope(
                            alliance, {"Position": 1, "JoinTime": _game_date_string()})}
        membership = _alliance_join_member(alliance, player, position=1)
        _alliance_save()
        store.save(player)
        out = _alliance_envelope_full(_alliance_envelope(alliance, membership))
        out["IsJoin"] = 1
        return out


def action_alliance_create(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11602 {Name, ...}: create a union.  Success carries
    AlreadyJoin 0 + a full AllianceData envelope; already-a-member returns
    AlreadyJoin 1 (the client just closes the form).
    """
    if _alliance_member_of(player)[0]:
        return {"AlreadyJoin": 1}
    name = str(payload.get("Name") or "").strip()
    if not name:
        return {"_fail": "empty union name"}
    with _alliance_lock:
        registry = _alliance_registry()
        alliance_id = _as_int(registry.get("next_id"), 1)
        registry["next_id"] = alliance_id + 1
        rb = player.data.setdefault("RoleBase", {})
        role = player.data.get("RoleData") if isinstance(player.data.get("RoleData"), dict) else {}
        alliance = {
            "AllianceId": alliance_id,
            "AllianceGuid": f"a{alliance_id}",
            "AllianceName": name,
            "AllianceLv": 1,
            "AllianceLeader": str(rb.get("Nickname") or role.get("Nickname") or ""),
            "AllianceExp": 0, "AllianceFortune": 0,
            "AlliancePower": _as_int(role.get("Power") or rb.get("Power")),
            "FaceBook": str(payload.get("FaceBook") or ""),
            "InsideNotice": str(payload.get("InsideNotice") or ""),
            "OutsideNotice": str(payload.get("OutsideNotice") or ""),
            "NeedCheck": 1 if _as_int(payload.get("NeedCheck")) else 0,
            "Password": str(payload.get("Password") or ""),
            "FlagId": max(1, _as_int(payload.get("FlagId"), 1)),
            "QQGroup": "", "WeChatNum": "", "IsOpenBoss": 0, "ApplyNum": 0,
            "Members": {},
        }
        registry["items"][alliance["AllianceGuid"]] = alliance
        membership = _alliance_join_member(alliance, player, position=4)  # leader
        _alliance_save()
        store.save(player)
        return {"AlreadyJoin": 0,
                "AllianceData": _alliance_envelope(alliance, membership)}


def action_alliance_rank(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11617: allianceRankingDialog.new reads #AllianceLvRank
    unguarded (569-570).  An empty list renders the dialog's empty prompt.
    """
    return {"AllianceLvRank": _alliance_rows()}


def action_alliance_rank_all(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11629: show-all toggle copies AllianceData into ranks."""
    return {"AllianceData": _alliance_rows()}


def action_alliance_inquire(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_allianceMod 11607 {AllianceId}: inquire-by-id; response Data is the
    union row drawn verbatim.  Unknown ids refuse with Code 1.
    """
    alliance_id = _as_int(payload.get("AllianceId"))
    for alliance in _alliance_registry()["items"].values():
        if _as_int(alliance.get("AllianceId")) == alliance_id:
            return _alliance_row(alliance, 1)
    return {"_fail": "Union not found"}


def action_item_jump(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_itemMod.action10307: the callback replaces its jump table with
    response["JumpConfMap"] verbatim (_itemMod.lua 2032-2041).  A missing key
    sets it to nil, and showShortItemDialog -> getItemJumpAndNoJumo then
    indexes that nil table (_itemMod.lua 1291-1292) with the exact error
    "attempt to index upvalue '?' (a nil value)" seen on the device when the
    Masquerade transfer/recovery dialogs request item 563.  An empty table is
    the neutral original-shaped value: every PossibleJump of TcItemOutputJump
    falls back to the closure's default tab index.  No unlock state invented.
    """
    return {"JumpConfMap": {}, "SysTime": _now()}


def _roll(percent: int) -> bool:
    """Success roll against a config probability (TcStar.Probability)."""
    try:
        return random.randint(1, 100) <= int(percent)
    except (TypeError, ValueError):
        return False


def action_item_use(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_itemMod.action10302 {ItemId, UseNum[, UseId]} (client 2.0.05).

    The request shape and the two verified effects come from
    modules/_itemMod.lua lines 1698-1777:
      - the knapsack/minister callers send {ItemId, UseNum} (UseId when the
        item is used on a target);
      - ItemId in {113,114,115} (union chests) -> the callback itself
        computes tempAddItem = [{ItemId 11, Num}, {ItemId 12, Num}] with
        Num = TcItem.AddNum * UseNum, so the server grants exactly those two
        bag items in the same amount;
      - every other resource coupon is a TcItem Type1==2 row whose Type2 is
        the granted resource id, amount AddNum * UseNum (TcItem.lua rows
        "Item_101".."Item_112": Type2 2=Silver 3=Food 4=Soldier 5=Exp).

    State rides back through the generic dataNetMod path only: the response
    carries the absolute RemainList (used item + granted resources/items)
    and the callback adds A0_3.useItemId itself.  The client never deducts
    locally, so a refusal (Code 1) leaves both sides consistent.

    Complex effects with no verifiable v2 semantics (Type1 103 beauty
    cards, Type1 5 VIP cards, Type1 101/102/12 pill and book-exp items used
    with UseId, ...) are refused instead of invented; see the report.
    """
    try:
        item_id = int(payload.get("ItemId") or 0)
        use_num = int(payload.get("UseNum") or 0)
    except (TypeError, ValueError):
        return {"_fail": "bad item use request"}
    if item_id <= 0 or use_num <= 0:
        return {"_fail": "bad item use request"}
    cfg = v2configs.item_by_id(item_id)
    if not cfg:
        return {"_fail": f"unknown item {item_id}"}
    if _role_item_count(player, item_id) < use_num:
        return {"_fail": f"not enough item {item_id}"}

    type1 = int(cfg.get("Type1") or 0)
    type2 = int(cfg.get("Type2") or 0)
    try:
        add_num = int(cfg.get("AddNum") or 0)
    except (TypeError, ValueError):
        add_num = 0

    touched = [item_id]
    resource_key = None
    book_pill_id = None
    book_pill_num = 0
    book_target = None
    if type1 == 2 and 1 <= type2 <= 6:
        resource_key = _REMAIN_LIST_MAP[type2]
    elif item_id in (113, 114, 115):
        pass  # union chest: grant bag items 11 and 12 below
    elif type1 == 3 and type2 == 1005:
        # V2 "book" items (205/210/215, also 201): official v7 responses
        # consume the book and grant one attribute pill (1001..1004) with
        # Num = TcItem.AddNum to the UseId counsellor.  The client's own
        # setCurrRewardData then writes that absolute value into PillAdd.
        try:
            use_id = int(payload.get("UseId") or 0)
        except (TypeError, ValueError):
            use_id = 0
        if not use_id:
            return {"_fail": "book needs a target counsellor"}
        book_target = next(
            (c for c in (player.data.get("RoleCounsellor") or [])
             if isinstance(c, dict) and int(c.get("CounsellorId") or 0) == use_id),
            None,
        )
        if book_target is None:
            return {"_fail": f"unknown counsellor {use_id}"}
        pills = [
            (pid, int(v2configs.item_by_id(pid).get("Type2") or 0))
            for pid in (1001, 1002, 1003, 1004)
        ]
        pills = [(pid, t2) for pid, t2 in pills if 1 <= t2 <= 4]
        if not pills:
            return {"_fail": "no attribute pill configs"}
        pill_add = book_target.get("PillAdd")
        pill_add = pill_add if isinstance(pill_add, list) else [0, 0, 0, 0]

        def _pill_rank(entry):
            pid, t2 = entry
            try:
                current = int(pill_add[t2 - 1] or 0)
            except (IndexError, TypeError, ValueError):
                current = 0
            return (current, t2)

        book_pill_id, book_pill_type = min(pills, key=_pill_rank)
        book_pill_num = add_num * use_num
        # Persist the absolute PillAdd slot the client will set from RemainList.
        while len(pill_add) < 4:
            pill_add.append(0)
        pill_add[book_pill_type - 1] = book_pill_num
        book_target["PillAdd"] = pill_add
    elif type1 == 3 and type2 in (1006, 1007):
        # "Book XP Pack" (221) / "Skill XP Pack" (222).  _itemMod.lua lines
        # 10-26 map 1006 -> AptitudeExp, 1007 -> SkillExp, and
        # setCurrRewardData (477-610) writes the response RemainList's
        # absolute value back with setMinisterInfo(CounsellorId, field, Num).
        # Same wire shape as the verified 1005 pill path.
        try:
            use_id = int(payload.get("UseId") or 0)
        except (TypeError, ValueError):
            use_id = 0
        if not use_id:
            return {"_fail": "xp pack needs a target counsellor"}
        book_target = next(
            (c for c in (player.data.get("RoleCounsellor") or [])
             if isinstance(c, dict) and int(c.get("CounsellorId") or 0) == use_id),
            None,
        )
        if book_target is None:
            return {"_fail": f"unknown counsellor {use_id}"}
        field = "AptitudeExp" if type2 == 1006 else "SkillExp"
        book_pill_id = type2
        book_pill_num = int(book_target.get(field) or 0) + add_num * use_num
        book_target[field] = book_pill_num
    else:
        return {"_fail": f"item {item_id} effect not supported by this server"}

    _role_item_consume(player, item_id, use_num)
    role_data = player.data.setdefault("RoleData", {})
    if resource_key is not None:
        role_data[resource_key] = int(role_data.get(resource_key) or 0) + add_num * use_num
    elif book_pill_id is not None:
        pass  # book grants an attribute pill to the target, appended below
    else:
        # Union chest (TcItem 113/114/115, Type2 12): the client's own 10302
        # callback shows +AddNum*UseNum of item 11 and item 12.
        for granted in (11, 12):
            _role_item_add(player, granted, add_num * use_num)
            touched.append(granted)
    store.save(player)
    response = attach_remain_list({}, player, items=touched, first_item_id=item_id)
    if book_pill_id is not None:
        response["RemainList"].append({
            "ItemId": book_pill_id,
            "CounsellorId": int(payload.get("UseId") or 0),
            "Num": book_pill_num,
        })
    return response


def _chat_sent(player: Player) -> list:
    sent = player.data.get("ChatSent")
    if not isinstance(sent, list):
        sent = []
        player.data["ChatSent"] = sent
    return sent


def _normalize_chat_msg(msg: dict) -> dict:
    """Drop zero avatar-frame ids: Lua treats 0 as truthy, and
    gameUI.createUserHead's updateBg then looks up TcAvatarBoxCfg[0] and
    indexes nil.  Leaving the key absent uses the client's default frame.
    """
    out = dict(msg)
    for key in ("AvatarBox", "RankBottomId", "TitleId", "Figure"):
        try:
            if int(out.get(key) or 0) <= 0:
                out.pop(key, None)
        except (TypeError, ValueError):
            out.pop(key, None)
    return out


def _chat_refresh_identity(msg: dict, player: Player) -> dict:
    """Keep replayed chat messages in sync with the player's current avatar.

    Old ChatSent rows were stored before avatar selection worked (ChatAvatar
    "B1"/"1", AvatarBox 0).  Re-sending those verbatim shows the wrong head
    in the chat list even after the player picks a new avatar.
    """
    out = _normalize_chat_msg(msg)
    base = player.data.get("RoleBase") if isinstance(player.data.get("RoleBase"), dict) else {}
    role = player.data.get("RoleData") if isinstance(player.data.get("RoleData"), dict) else {}
    avatar = base.get("ChatAvatar") or role.get("ChatAvatar")
    if isinstance(avatar, str) and avatar[:1] in ("B", "C", "F"):
        out["ChatAvatar"] = avatar
    box = base.get("AvatarBox") or role.get("AvatarBox")
    try:
        if int(box or 0) > 0:
            out["AvatarBox"] = int(box)
    except (TypeError, ValueError):
        pass
    return out


def _chat_identity(player: Player) -> dict:
    """Sender identity fields echoed into every chat MsgData record."""
    base = player.data.get("RoleBase") if isinstance(player.data.get("RoleBase"), dict) else {}
    role = player.data.get("RoleData") if isinstance(player.data.get("RoleData"), dict) else {}
    dress_src = base.get("DressList") or role.get("DressList") or player.data.get("DressList") or []
    dress = []
    if isinstance(dress_src, list):
        for entry in dress_src:
            item_id = entry.get("ItemId") if isinstance(entry, dict) else entry
            if _as_int(item_id):
                dress.append({"ItemId": _as_int(item_id)})
    return {
        "Rid": str(base.get("Rid") or player.uid),
        "ShowId": _as_int(base.get("ShowId")),
        "Nickname": str(base.get("Nickname") or role.get("Nickname") or ""),
        "Name": str(base.get("Name") or base.get("Nickname") or ""),
        "Sex": _as_int(base.get("Sex") or role.get("Sex"), 1),
        "Lv": _as_int(role.get("Lv") or base.get("Lv"), 1),
        "VipLv": _as_int(role.get("VipLv") or base.get("VipLv")),
        "Avatar": _as_int(base.get("Avatar") or role.get("Avatar")),
        "ChatAvatar": str(base.get("ChatAvatar") or role.get("ChatAvatar") or "F400"),
        "Figure": _as_int(base.get("Figure") or role.get("Figure")),
        "Dress": dress,
        "AvatarBox": _as_int(base.get("AvatarBox") or role.get("AvatarBox")),
        "RankBottomId": _as_int(base.get("RankBottomId")),
        "TitleId": _as_int(base.get("TitleId")),
        "Power": _as_int(role.get("Power") or base.get("Power")),
        # v7 MsgData shape: optional lists/tables are always present (empty
        # when unknown) so the chat row builder never pairs() over nil.
        "AvatarList": [],
        "AliasList": [],
        "FashionEffectInfo": [],
        "GloryData": [],
        "CustomizeTitleList": [],
        "AvatarBoxData": {"SeasonRank": 0},
        "Manifesto": "",
        "ServerId": str(config.SERVER_ID),
        "ServerName": config.SERVER_NAME,
        "ChatBox": _as_int(base.get("ChatBox"), 4000),
        "ItemTitleId": _as_int(base.get("ItemTitleId")),
        "FamilyLv": 0,
        "PrincessFamilyLv": 0,
        "ValentineGardenFamilyLv": 0,
        "BeautyAndBeastFamilyLv": 0,
        "FriendType": 0,
        "FriendshipValue": 0,
    }


def action_chat_send(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """v7 action12301: echo the sent message back in a v7-shaped MsgData.

    The v2 client's callback (_chatMod.lua 396-408) reads SystemInfo and
    HaveCashPack; chatDialog.refreshChat then reads A0_3.MsgList[1].MsgData.
    Every field below is either echoed from the request or taken from the
    persisted RoleBase/RoleData; no message content is invented server-side.
    """
    text_value = payload.get("Text") if payload.get("Text") is not None else payload.get("text")
    text = str(text_value or "")
    channel_value = payload.get("Channel") if payload.get("Channel") is not None else payload.get("channel")
    channel = str(channel_value or "c")
    time_value = payload.get("Time") if payload.get("Time") is not None else payload.get("time")
    when = time_value if time_value not in (None, "") else str(int(_now() * 1000)) + ".0"

    sent = _chat_sent(player)
    msg_id = _as_int(player.data.get("ChatSentNextId") or 0) + 1
    player.data["ChatSentNextId"] = msg_id
    msg = {"Id": msg_id, "MsgType": 1, "Text": text, "Channel": channel,
           "Time": when, "MsgTime": when}
    msg.update(_normalize_chat_msg(_chat_identity(player)))
    msg["ext"] = {"Type": 1, "pre_text": text, "MentionMsg": [], "ReplyMsg": {}}
    sent.append(msg)
    if len(sent) > 20:  # small ring: keep the newest 20 sent messages
        del sent[:-20]
    store.save(player)
    return {"MsgList": [msg],
            "NewestMsg": {"MsgData": msg, "Channel": channel},
            "SystemInfo": {}, "HaveCashPack": False, "ShareRemainSeconds": 0}


def action_chat(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """Shape required by _chatMod's 123xx callbacks and chatDialog.

    MsgList/NewestMsg must exist even when chat is empty; otherwise the chat
    scene indexes nil and aborts the main-scene transition.  Sent messages
    from 12301 are persisted in player.data["ChatSent"] and replayed here.
    HaveCashPack/IsExistNationsManeuverRedBag must be real booleans: a Python
    0 is truthy in Lua, which made the cash-pack icon permanently visible
    (v7 sends boolean false).
    """
    sent = [_chat_refresh_identity(msg, player) for msg in _chat_sent(player)]
    newest = ({"MsgData": sent[-1], "Channel": sent[-1].get("Channel", "c")}
              if sent else {})
    # v7 12302 carries these optional tables on the envelope and on every
    # MsgData.  The v2 scene pairs()/iterates several of them while building
    # rows; sending empty Lua tables is the neutral original-shaped value
    # and prevents nil-table errors when a field has never been populated.
    return {
        "SystemInfo": {},
        "HaveCashPack": False,
        "IsExistNationsManeuverRedBag": False,
        "ShareRemainSeconds": 0,
        "MsgList": sent,
        "NewestMsg": newest,
        "ShieldList": [],
        "RecentChatList": [],
        "ChatList": [],
        "CrossChat": [],
        "RedBagList": [],
    }


def action_diplomacy_chat(payload: dict, player: Player, session: Session, store: Store) -> dict:
    return {"ChatList": [], "RecentChatList": []}


def action_alliance_pack(payload: dict, player: Player, session: Session, store: Store) -> dict:
    # _allianceMod.setPackCfg stores this and later indexes PackCfg by id.
    return {"PackCfg": {}}


def action_logout(payload: dict, player: Player, session: Session, store: Store) -> dict:
    store.save(player)
    return {}


def action_status(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """roleMod.action10018 {StatusStr: "collectGuide-0"}: the client keeps its
    own lastStatus locally; mirroring it server-side makes tutorial progress
    survive a relaunch (roleMod.getStatus reads RoleData.LastStatus)."""
    status_str = str(payload.get("StatusStr") or "")
    if status_str:
        tasks.apply_status(player.data, payload)  # guide-step quest counters
        player.data.setdefault("RoleData", {})["LastStatus"] = status_str
        store.save(player)
    return {}


# --------------------------------------------------------------------------
# sign-in / Divination day boundary helpers
# --------------------------------------------------------------------------
# The login snapshot advertises TimeZone: 3 (defaults.py 152), and the client
# compares dateToNumber(secToDate(now), "day") with the stored refresh string
# (_signMod.isRedPoint, modules/_signMod.lua 103-159), where secToDate uses
# the device's adjusted local time.  Use the same UTC+3 game-day boundary for
# both storing and comparing so a "day" means the same thing on both sides.
_GAME_TZ = datetime.timezone(datetime.timedelta(hours=3))
_GAME_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def _game_time(ts: int | None = None) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(_now() if ts is None else ts, _GAME_TZ)


def _game_date_string(ts: int | None = None) -> str:
    return _game_time(ts).strftime(_GAME_DATE_FMT)


# --- Masquerade / Find (10601-10606) response conversions -------------------
# Stored VisitData keeps epochs, but the client's timer code does NOT:
#   _findMod.startFateTime (600-605) -> tools.common.dateToSec (460-541) parses
#     only "YYYY-MM-DD HH:MM:SS" then calls os.time; an epoch int makes all
#     captures nil and raises "field 'day' missing in date table" (device log
#     2026-11-18 09:21:40, blocks Find scene entry).
#   _findMod.startVitalityTime (485-514) does raw arithmetic with VitalityTime,
#     so it must be seconds elapsed in the current VitalityCD tick, not an epoch.
def _visit_fate_time(value) -> str:
    if isinstance(value, str) and len(value) >= 19:
        try:
            datetime.datetime.strptime(value[:19], _GAME_DATE_FMT)
            return value
        except ValueError:
            pass
    try:
        ts = int(value)
    except (TypeError, ValueError):
        ts = _now()
    return _game_date_string(ts)


def _visit_vitality_time(value) -> int:
    try:
        elapsed = _now() - int(value)
    except (TypeError, ValueError):
        elapsed = 0
    if elapsed < 0:
        elapsed = 0
    try:
        cd = int(v2configs.env_value("VitalityCD") or 1800)
    except (TypeError, ValueError):
        cd = 1800
    return elapsed % max(1, cd)


def _visit_client_slice(visit: dict) -> dict:
    """VisitData slice in the exact shape _findMod.action10602 reads."""
    return {
        "Vitality": _as_int(visit.get("Vitality") or 0),
        "Fate": _as_int(visit.get("Fate") or 0),
        "FateTime": _visit_fate_time(visit.get("FateTime")),
        "VitalityTime": _visit_vitality_time(visit.get("VitalityTime")),
        "TransferNum": _as_int(visit.get("TransferNum") or 0),
        "PraiseGuide": _as_int(visit.get("PraiseGuide") or 0),
    }


def _game_day_number(value) -> int:
    """Client dateToNumber(str, "day") equivalent: YYYYMMDD as an int."""
    try:
        parsed = datetime.datetime.strptime(str(value)[:19], _GAME_DATE_FMT)
    except (TypeError, ValueError):
        return 0
    return parsed.year * 10000 + parsed.month * 100 + parsed.day


def _stored_checkin_refresh(player: Player | None) -> str:
    """Last successful check-in, stored server-side under CheckInRefreshTime.

    A brand-new account has no stored value.  Return the previous UTC+3 game
    day as a neutral fallback so isRedPoint sees today > yesterday and lets
    the player collect exactly once (no reward is invented on the read).
    """
    value = player.data.get("CheckInRefreshTime") if player else None
    if isinstance(value, str) and _game_day_number(value):
        return value
    return (_game_time() - datetime.timedelta(days=1)).strftime(_GAME_DATE_FMT)


def _normalize_checkin_records(player: Player) -> list[dict]:
    """Repair legacy CheckInRecord rows before they reach signScene.

    signScene's history overlay does string.sub(record.CheckInRefreshTime)
    and pairs(record.TodayAward).  Old dev records created before the daily
    guard only had {Day, Time}, so they crashed the "best gains" info page.
    """
    raw = player.data.get("CheckInRecord")
    raw = [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
    fixed: list[dict] = []
    fallback = _game_date_string()
    for record in raw:
        row = dict(record)
        stamp = row.get("CheckInRefreshTime")
        if not isinstance(stamp, str) or not stamp:
            legacy = row.get("Time")
            try:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(int(legacy)))
            except (TypeError, ValueError, OverflowError):
                stamp = fallback
        row["CheckInRefreshTime"] = stamp
        try:
            row["CheckTypeId"] = int(row.get("CheckTypeId") or 10)
        except (TypeError, ValueError):
            row["CheckTypeId"] = 10
        award = row.get("TodayAward")
        row["TodayAward"] = [
            [int(pair[0]), int(pair[1])]
            for pair in award
            if isinstance(pair, (list, tuple)) and len(pair) >= 2
        ] if isinstance(award, list) else []
        try:
            row["Day"] = int(row.get("Day") or 0)
        except (TypeError, ValueError):
            row["Day"] = 0
        fixed.append(row)
    player.data["CheckInRecord"] = fixed
    return fixed


def _sign_daily_award() -> list[dict]:
    """Daily Divination reward, from the official v7 11501/11502 capture.

    v7 11501 CheckInfo.Award = [{ItemId:1,Num:60} x3] and 11502 grants the
    sum via RemainList.  The v2 client has no per-day reward config
    (TcSignInType is only flavour text), so this captured server payload is
    the only non-invented source.  11507 cumulative milestones are separate.
    """
    return [{"ItemId": 1, "Num": 60} for _ in range(3)]


def action_sign_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_signMod.action11501 (modules/_signMod.lua 163-193): the callback does
    `CheckInfo = A0_3.CheckInfo`, so the refresh timestamp and the record list
    must live under CheckInfo.  signScene then pages CheckInRecords (line 316).
    """
    check_in_day = int(player.data.get("CheckInDay") or 0) if player else 0
    cumulative = _cumulative_flags(player) if player else [0, 0, 0, 0]
    records = _normalize_checkin_records(player)
    store.save(player)
    return {
        "CheckInfo": {
            "CheckInDay": check_in_day,
            "CheckInReFreshTime": _stored_checkin_refresh(player),
            "CumulativeList": cumulative,
            "CheckTypeId": 10,
            "CheckInRecords": records,
            # signScene.signView pairs() over getSignInfo().Award; 11501 is
            # the first call when opening the Divination page, so Award must
            # already exist as a table.  The v2 config has no per-day reward
            # table, so an empty array is the neutral, non-invented value.
            "Award": _sign_daily_award(),
        },
        "CheckInDay": check_in_day,
        "CumulativeList": cumulative,
        "CheckTypeId": 10,
        "CheckInRecord": [{"Day": check_in_day}],
    }


# --------------------------------------------------------------------------
# recently-observed menu actions (client module callbacks verified in Lua)
# --------------------------------------------------------------------------
def _section_record(player: Player) -> dict:
    section = dict(player.data.get("RoleSection") or {})
    section.setdefault("ChapterId", 1)
    section.setdefault("SectionId", 1)
    section.setdefault("PhaseId", 1)
    section.setdefault("BossBlood", 0)
    section.setdefault("Soldiers", 0)
    section.setdefault("PlayAnimation", 0)
    section.setdefault("ArtilleryLv", 1)
    section.setdefault("ArtilleryAddPr", 0)
    section.setdefault("ArtilleryUseNum", 0)
    section.setdefault("ArtilleryNowSilver", 0)
    # Match the official SectionRecord: Soldiers mirrors the current phase's
    # PhaseCfg.Soldiers (10201/10202 display and win detection read it).
    if not section.get("Soldiers"):
        section["Soldiers"] = _phase_cfg(
            section.get("ChapterId", 1),
            section.get("SectionId", 1),
            section.get("PhaseId", 1),
        )["Soldiers"]
    # _stageMod's 10202 callback indexes these; the official v7 response
    # always carries the three date strings and the recover list.
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(_now()))
    section.setdefault("ArtilleryUseTime", stamp)
    section.setdefault("LastBossAttackTime", stamp)
    section.setdefault("RefreshTime", stamp)
    section.setdefault("RecoverCountList", [])
    section.setdefault("Counsellors", [])
    section.setdefault("HasUsedOrder", [])
    return section


def action_section_push(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_stageMod.action10209 (one-key push): the callback copies
    A0_3.SectionRecord.{ChapterId,SectionId,PhaseId,BossBlood,Soldiers,
    PlayAnimation,Artillery*} into tbData.RoleSection and reads A0_3.PhaseCfg.
    This is a mutation: the requested section becomes the player's progress."""
    section = player.data.setdefault("RoleSection", {})
    for key in ("ChapterId", "SectionId", "PhaseId"):
        value = payload.get(key)
        if value is not None:
            try:
                section[key] = int(value)
            except (TypeError, ValueError):
                pass
    section.setdefault("ChapterId", 1)
    section.setdefault("SectionId", 1)
    section.setdefault("PhaseId", 1)
    tasks.apply_stage_push(player.data, payload)
    store.save(player)
    return {"SectionRecord": _section_record(player),
            "PhaseCfg": _phase_cfg(section.get("ChapterId", 1),
                                   section.get("SectionId", 1),
                                   section.get("PhaseId", 1))}


def action_stage_info_v2(payload: dict, player: Player, session: Session, store: Store) -> dict:
    section = _section_record(player)
    return {"SectionRecord": section,
            "PhaseCfg": _phase_cfg(section.get("ChapterId", 1),
                                   section.get("SectionId", 1),
                                   section.get("PhaseId", 1)),
            "CityAwardGet": [],
            "MinisterList": [],
            "SectionAward": {}}


def action_welf_card(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_welfCardMod.action11504: L0_1.CardStage = A0_3.CardAward.CardStage
    (then updateCardTime reads tbData.CardEndTime, which comes from login)."""
    return {"CardAward": {"CardStage": {}}, "CardStage": {}}


# --------------------------------------------------------------------------
# achievements (11201/11202) and daily tasks (11301-11303)
# --------------------------------------------------------------------------
def _achievement_rows() -> list[dict]:
    return [r for r in v2configs.load("TcAchievementName") if isinstance(r, dict)]


def _achievement_award_rows() -> list[dict]:
    return [r for r in v2configs.load("TcAchievementAward") if isinstance(r, dict)]


def _daily_task_rows() -> list[dict]:
    return [r for r in v2configs.load("TcDailyTask") if isinstance(r, dict)]


def _activity_award_rows() -> list[dict]:
    return [r for r in v2configs.load("TcActivityAward") if isinstance(r, dict)]


def _iter_award_pairs(award):
    """Yield [ItemId, Num] pairs from the config's nested award tables.

    TcAchievementAward/TcDailyTask use [[id,num], ...]; TcActivityAward wraps
    each pair in an extra group.  Both shapes occur in the v2 client.
    """
    if not isinstance(award, (list, tuple)):
        return
    if award and isinstance(award[0], (int, float)) and len(award) >= 2:
        yield [int(award[0]), int(award[1])]
        return
    for entry in award:
        yield from _iter_award_pairs(entry)


def _grant_award_pairs(player: Player, award) -> tuple[list[list[int]], list[int]]:
    """Grant only explicit positive config amounts; return (pairs, bag ids).

    TcActivityAward rows contain entries like [2, 0, 100] where Num 0 means
    "client computes from RolePower*Rate".  We cannot source that sub-stat, so
    such entries are skipped rather than fabricated.
    """
    pairs: list[list[int]] = []
    bag_ids: list[int] = []
    role_data = player.data.setdefault("RoleData", {})
    for item_id, num in _iter_award_pairs(award):
        if num <= 0:
            continue
        if 1 <= item_id <= 6:
            key = _REMAIN_LIST_MAP[item_id]
            role_data[key] = int(role_data.get(key) or 0) + num
        else:
            _role_item_add(player, item_id, num)
            bag_ids.append(item_id)
        pairs.append([item_id, num])
    return pairs, bag_ids


def _bump_achievement(player: Player, key: str, num: int = 1) -> None:
    """Lifetime counter bump from an existing gameplay handler."""
    counters = player.data.setdefault("AchievementCounters", {})
    counters[key] = int(counters.get(key) or 0) + int(num)


def _daily_state(player: Player) -> dict:
    """UTC+3 daily reset, using the same game-day helper as the sign-in fix.

    Only daily counters/flags/activity reset; lifetime AchievementCounters,
    CheckInDay and GovAffairs.Completed are untouched.
    """
    today = _game_date_string()
    if _game_day_number(player.data.get("DailyTaskDate")) != _game_day_number(today):
        player.data["DailyTaskDate"] = today
        player.data["DailyCounters"] = {}
        player.data["DailyTaskFlags"] = {}
        player.data["ActivityAwardFlags"] = {}
        player.data["RoleActivity"] = 0
    return {
        "counters": player.data.setdefault("DailyCounters", {}),
        "task_flags": player.data.setdefault("DailyTaskFlags", {}),
        "activity_flags": player.data.setdefault("ActivityAwardFlags", {}),
    }


def _bump_daily(player: Player, key: str, num: int = 1) -> None:
    state = _daily_state(player)
    state["counters"][key] = int(state["counters"].get(key) or 0) + int(num)


# TaskId -> the real daily counter that drives it.  Tasks with no tracked
# gameplay source map to "" and stay at 0 (never fake completion).
_DAILY_TASK_KEYS = {
    1: "",             # Vizier Level-up (no tracked source)
    2: "LevySilver",   # Levy Trade
    3: "LevyGrain",    # Levy Grain
    4: "LevySoldiers", # Levy Soldiers
    5: "Government",   # Government Affairs
    6: "Campaign",     # Campaign Rush
}


def _achievement_current(player: Player) -> dict[int, int]:
    """AchievementId -> REAL player metric; unknown metrics stay 0."""
    role_data = player.data.get("RoleData") or {}
    role_base = player.data.get("RoleBase") or {}
    gov = player.data.get("GovAffairs") or {}
    counters = player.data.get("AchievementCounters") or {}

    def counter(key: str) -> int:
        return int(counters.get(key) or 0)

    def count(key: str) -> int:
        value = player.data.get(key)
        return len(value) if isinstance(value, (list, tuple, dict)) else 0

    return {
        1: int(player.data.get("CheckInDay") or 0),                       # Total Sign-ins
        2: int(role_data.get("Lv") or role_base.get("Lv") or 0),          # Sultan Lv
        3: int(role_data.get("Power") or role_base.get("Power") or 0),    # Empire Power
        4: count("RoleCounsellor"),                                       # Total Viziers
        5: counter("CampaignVictories"),                                  # Campaign Victories
        6: counter("LevySilver"),                                         # Levied Trade
        7: counter("LevyGrain"),                                          # Levied Grain
        8: counter("LevySoldiers"),                                       # Levied Soldiers
        9: max(int(gov.get("Completed") or 0), counter("GovernmentAffairs")),  # Government
        10: int(role_data.get("VipLv") or role_base.get("VipLv") or 0),   # VIP Level
        11: 0,  # Arena Fight - no arena state on the dev server
        12: 0,  # Imperial Library - no library state
        13: 0,  # Envoy Tribute - no tribute counters
        14: count("RoleBeauty"),                                          # Total Consorts
        15: counter("HaremVisits"),                                       # Harem Visits
        16: 0,  # Daily Masquerades - no masquerade state
        17: count("Children") + count("OChildren"),                      # Royal Descendant
        18: 0,  # Marriage Bond - no marriage state
        19: 0,  # Fortress Victories - no fortress state
        20: 0,  # Union Contribution - no alliance state
        21: 0,  # Feast Enthusiasm - no feast state
        22: 0,  # Voyage Expedition - no voyage state
        23: 0,  # Union War Registration - no union-war state
        24: count("FriendList"),                                          # Make Friends
        25: 0,  # Consort Teaching - no teaching counters
        26: 0,  # Consort Learning - no learning counters
        27: 0,  # Feast Organizer - no feast state
    }


def action_achieve(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_achieveMod.action11201 (modules/_achieveMod.lua 202-246): the callback
    pairs() over A0_3.AchievementData and copies only AchievementId,
    CurrentNum and GetAwardStage onto the 27 local config rows."""
    current = _achievement_current(player)
    stages = player.data.get("AchievementStages") or {}
    data = []
    for row in _achievement_rows():
        aid = int(row.get("AchievementId") or 0)
        data.append({
            "AchievementId": aid,
            "CurrentNum": int(current.get(aid) or 0),
            "GetAwardStage": int(stages.get(str(aid)) or 0),
        })
    return {"AchievementData": data}


def action_achieve_claim(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_achieveMod.action11202 (modules/_achieveMod.lua 248-289).

    achieveScene sends {achievementId, getAwardStage = storedStage+1}
    (scene lines 303-315); accept the capitalised spelling too.  The stage is
    the 1-based TcAchievementAward.Stage being claimed.
    """
    aid_raw = payload.get("AchievementId")
    if aid_raw is None:
        aid_raw = payload.get("achievementId")
    stage_raw = payload.get("GetAwardStage")
    if stage_raw is None:
        stage_raw = payload.get("getAwardStage")
    try:
        aid, requested = int(aid_raw or 0), int(stage_raw or 0)
    except (TypeError, ValueError):
        return {"_fail": "bad achievement claim request"}

    rows = [r for r in _achievement_award_rows() if int(r.get("AchievementId") or 0) == aid]
    if not rows:
        return {"_fail": "unknown achievement"}
    stages = player.data.setdefault("AchievementStages", {})
    stored = int(stages.get(str(aid)) or 0)
    max_stage = max(int(r.get("Stage") or 0) for r in rows)
    if requested <= stored:
        # Also accept a caller that passes the already-claimed stage and means
        # "claim the next one"; a client sending stored+1 takes the branch below.
        if requested == stored and stored < max_stage:
            target = stored + 1
        else:
            return {"_fail": "achievement stage already claimed"}
    elif requested == stored + 1:
        target = requested
    else:
        return {"_fail": "invalid achievement stage"}

    row = next((r for r in rows if int(r.get("Stage") or 0) == target), None)
    if row is None:
        return {"_fail": "achievement stage not found"}
    need = int(row.get("RequireNum") or 0)
    current = int(_achievement_current(player).get(aid) or 0)
    if current < need:
        return {"_fail": f"achievement progress {current}/{need}"}

    pairs, bag_ids = _grant_award_pairs(player, row.get("AwardItem"))
    stages[str(aid)] = target
    store.save(player)
    return attach_remain_list(
        {"AchievementId": aid, "GetAwardStage": target}, player, items=bag_ids,
        resources=[int(p[0]) for p in pairs if int(p[0]) in _REMAIN_LIST_MAP])


def action_daily_task(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_dailyTaskMod.action11301 (modules/_dailyTaskMod.lua 303-374): the
    callback pairs() over DailyTaskData / ActivityAwardData and reads the
    RoleActivity number.  RequireNum is included for the client's grouping.
    """
    state = _daily_state(player)
    tasks = []
    for row in _daily_task_rows():
        tid = int(row.get("TaskId") or 0)
        key = _DAILY_TASK_KEYS.get(tid, "")
        tasks.append({
            "TaskId": tid,
            "CurrentNum": int(state["counters"].get(key) or 0),
            "RequireNum": int(row.get("RequireNum") or 0),
            "IsGetAward": int(state["task_flags"].get(str(tid)) or 0),
        })
    awards = []
    for row in _activity_award_rows():
        aid = int(row.get("ActivityAwardId") or 0)
        awards.append({
            "ActivityAwardId": aid,
            "IsGetAward": int(state["activity_flags"].get(str(aid)) or 0),
            "RequireActivity": int(row.get("RequireActivity") or 0),
        })
    store.save(player)
    return {"DailyTaskData": tasks, "ActivityAwardData": awards,
            "RoleActivity": int(player.data.get("RoleActivity") or 0)}


def action_daily_task_claim(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_dailyTaskMod.action11302 (modules/_dailyTaskMod.lua 205-241): reads
    RoleActivity and GainActivityItem (config-style [ItemId, Num] pairs)."""
    state = _daily_state(player)
    try:
        tid = int(payload.get("TaskId") or 0)
    except (TypeError, ValueError):
        return {"_fail": "bad daily task id"}
    row = next((r for r in _daily_task_rows() if int(r.get("TaskId") or 0) == tid), None)
    if row is None:
        return {"_fail": "unknown daily task"}
    if int(state["task_flags"].get(str(tid)) or 0):
        return {"_fail": "daily task already claimed"}
    need = int(row.get("RequireNum") or 0)
    key = _DAILY_TASK_KEYS.get(tid, "")
    current = int(state["counters"].get(key) or 0) if key else 0
    if need <= 0 or current < need:
        return {"_fail": f"daily task progress {current}/{need}"}

    pairs, bag_ids = _grant_award_pairs(player, row.get("AwardItem"))
    activity = int(row.get("AwardActivity") or 0)
    player.data["RoleActivity"] = int(player.data.get("RoleActivity") or 0) + activity
    state["task_flags"][str(tid)] = 1
    store.save(player)
    return attach_remain_list({
        "TaskId": tid,
        "RoleActivity": player.data["RoleActivity"],
        "GainActivityItem": pairs,
    }, player, items=bag_ids)


def action_activity_award_claim(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_dailyTaskMod.action11303 (modules/_dailyTaskMod.lua 176-203): the
    callback only flips its local IsGetAward flag after a successful claim."""
    state = _daily_state(player)
    try:
        aid = int(payload.get("ActivityAwardId") or 0)
    except (TypeError, ValueError):
        return {"_fail": "bad activity award id"}
    row = next((r for r in _activity_award_rows()
                if int(r.get("ActivityAwardId") or 0) == aid), None)
    if row is None:
        return {"_fail": "unknown activity award"}
    if int(state["activity_flags"].get(str(aid)) or 0):
        return {"_fail": "activity award already claimed"}
    need = int(row.get("RequireActivity") or 0)
    activity = int(player.data.get("RoleActivity") or 0)
    if need <= 0 or activity < need:
        return {"_fail": f"activity points {activity}/{need}"}

    pairs, bag_ids = _grant_award_pairs(player, row.get("AwardItem"))
    state["activity_flags"][str(aid)] = 1
    store.save(player)
    return attach_remain_list(
        {"ActivityAwardId": aid, "RoleActivity": activity}, player, items=bag_ids,
        resources=[int(p[0]) for p in pairs if int(p[0]) in _REMAIN_LIST_MAP])


# --------------------------------------------------------------------------
# 11901 — activity config fetch (_settingCatchMod.start -> updateSetting)
# --------------------------------------------------------------------------
# The v2 client queues every login-Activity entry with Version ~= -1
# (modules/_settingCatchMod.lua) and requests it here as bare ids, e.g.
# {"CompetitionRank": [1, 2, 3]}.  The response must carry a "<key>Cfg" table
# keyed by tostring(Id) for each requested key: _settingCatchMod.saveRouteSetting
# reads response["CompetitionRankCfg"][tostring(Id)], stores the entries under
# their string ids and hands the map to
# modManager._rankUpActivityMod.updataRankUpActivity (rankUpActivityData).
#
# Fields the v2 client reads from each CompetitionRankCfg entry
# (modules/_rankUpActivityMod.lua + scene/rankUpActivityScene):
#   Id            check_activity_state sorts the active list by it
#   ActivityType  getNumLogo / onArenaActivity / isRankUpItem / getRuleId and
#                 rankUpActivityScene (card image, temp labels)
#   StartTime     timeToZone0() (type-checks, converts date strings via
#                 dateToSec) and loginTimeDataHandler's dateToSec(), which
#                 requires a "YYYY-MM-DD HH:MM:SS" string or nil — a unix
#                 number would crash dateToSec.  Strings are the format the
#                 official capture uses as well.
#   EndTime       same as StartTime
# The official capture also carries StartTimeUnix/EndTimeUnix/AllianceMark,
# but no CompetitionRank consumer reads them (mailDialog's EndTimeUnix is
# unrelated), so they are omitted.
#
# Schedule = the official one served on 2026-11-16
# (recon/v7-capture/v7_actions.jsonl, 11901 response, CompetitionRankCfg).
# The post-EndTime claim window is CompetitionRankExpirationSeconds from the
# client's own ConfigEnvSet (= 86400); no reward/rank data is required by the
# 11901 path itself.
_COMPETITION_RANK_SCHEDULE: dict[int, tuple[str, str]] = {
    1: ("2026-11-16 00:00:00", "2026-11-18 21:59:59"),
    2: ("2026-11-16 00:00:00", "2026-11-18 21:59:59"),
    3: ("2026-11-16 00:00:00", "2026-11-21 21:59:59"),
}
_COMPETITION_RANK_ACTIVITY_TYPE: dict[int, int] = {1: 1, 2: 2, 3: 13}

# The official 11901 TimeLimitCfg body is truncated in every v7 capture (the
# log prints only the bare "TimeLimitCfg" name nine times, once per requested
# id).  The only original window we can quote is the longest one served in the
# same capture's CompetitionRankCfg[3] (2026-11-16 .. 2026-11-21).  Reusing it
# here is a documented JUDGMENT CALL, not a verified TimeLimit schedule; the
# icon disappears after EndTime unless a newer capture is obtained.
_TIME_LIMIT_SCHEDULE = ("2026-11-16 00:00:00", "2026-11-21 21:59:59")

# _settingCatchMod.lua registers one descriptor per activity config.  The
# official 11901 response carries ALL of these "<name>" keys (verified in the
# v7 capture).  We previously returned only CompetitionRankCfg (+CrossChat),
# so listeners for the other activities received nil and several called
# pairs(nil) on __login, aborting the rest of the response callback chain —
# including the ranking-activity state update that ranking tabs depend on.
# An empty table is the neutral original-shaped value; no schedule or reward
# is invented for activities the v2 client never exercises here.
_SETTING_CATCH_CFG_NAMES = (
    "TimeLimitCfg", "CompetitionRankCfg", "CounsellorExchangeCfg",
    "UniversalCfg", "UniversalItem", "BoardCfg", "LampCfg",
    "RechargeActivityCfg", "PackageActivityCfg", "SeaKingActivityCfg",
    "RelicActivityCfg", "CrossCompetitionRankCfg", "CommanderActivityCfg",
    "CrossChatCfg", "HorseRaceActivityCfg", "TimeLimitSynthesisActivityCfg",
    "ValentineActivityCfg", "CrossWarCfg", "MarketingLoginActivityCfg",
    "MarketingLuckyDrawActivityCfg", "MarketingGroupBuyActivityCfg",
    "RamadanCfg", "KingBossCfg",
)


def action_11901(payload: dict, player: Player, session: Session, store: Store) -> dict:
    requested = payload.get("CompetitionRank")
    ids: list[int] = []
    if isinstance(requested, list):
        for item in requested:
            try:
                ids.append(int(item))
            except (TypeError, ValueError):
                continue
    # _settingCatchMod queues TimeLimitActivity ids from the login Activity
    # slice (defaults.py advertises 1..9, matching the official capture).
    tl_ids: list[int] = []
    tl_requested = payload.get("TimeLimitActivity")
    if isinstance(tl_requested, list):
        for item in tl_requested:
            try:
                tl_ids.append(int(item))
            except (TypeError, ValueError):
                continue
    # _settingCatchMod's listeners iterate every "<name>Cfg" key on the
    # response; a missing key is nil and breaks pairs()/indexing mid-chain.
    # Always return the full original-shaped envelope, even when no ids were
    # requested (the client may still dispatch activityRefresh on it).
    envelope: dict[str, Any] = {name: {} for name in _SETTING_CATCH_CFG_NAMES}
    # Kept from the earlier chat fix: _chatMod.setCrossChat reads the bare
    # CrossChat key as well as CrossChatCfg; sending both is harmless.
    envelope["CrossChat"] = []
    # _limitTimeMod.dataDeal (the TimeLimitCfg listener) reads the companion
    # progress keys; empty tables are the same neutral values 10000 carries,
    # so no progress or reward is invented.
    envelope["TimeLimitComplete"] = {}
    envelope["TimeLimitAwardGet"] = {}
    if tl_ids:
        # ActivityType doubles as the TcLimitedActivity row id on the client
        # (card art / title lookup) and must be numeric for check_activity_state
        # sorting.  Identity mapping Id -> ActivityType stays within the
        # original config's 1..24 rows (judgment call: official mapping unknown).
        envelope["TimeLimitCfg"] = {
            str(i): {
                "Id": i,
                "ActivityType": i,
                "StartTime": _TIME_LIMIT_SCHEDULE[0],
                "EndTime": _TIME_LIMIT_SCHEDULE[1],
                # AwardCfg MUST be a LIST: the client's sortData walks
                # `for i = 1, #AwardCfg` (modules/_limitTimeMod.lua 687-690);
                # a string-keyed map has # == 0 in Lua, so no row ever draws.
                "AwardCfg": _time_limit_rows(i),
            }
            for i in dict.fromkeys(tl_ids)
        }
    if not ids and not tl_ids:
        return envelope
    # saveRouteSetting indexes response["CompetitionRankCfg"][tostring(Id)] for
    # every requested id, so the table must exist even when all ids are
    # unknown to us (missing entries would make the client index nil);
    # unknown ids simply store nil client-side.
    cfgs: dict[str, dict] = {}
    for activity_id in dict.fromkeys(ids):  # de-dup, keep order
        schedule = _COMPETITION_RANK_SCHEDULE.get(activity_id)
        if schedule is None:
            continue
        cfgs[str(activity_id)] = {
            "Id": activity_id,
            "ActivityType": _COMPETITION_RANK_ACTIVITY_TYPE[activity_id],
            "StartTime": schedule[0],
            "EndTime": schedule[1],
        }
    envelope["CompetitionRankCfg"] = cfgs
    return envelope


# --------------------------------------------------------------------------
# 11902/11903/11904 — time-limited events (_limitTimeMod / limitTimeScene)
# --------------------------------------------------------------------------
# The Time Limited icon fires 11902 before the scene opens; its callback
# feeds TimeLimitAwardGet/TimeLimitComplete to setActivityComplete, which
# builds L1_1[id] = {complete, awardget}.  Missing per-id entries make
# limitTimeSubDialog.dataProcess call pairs(nil) (device log 2026-11-18
# 12:50:37: _onRelease -> selectIndex -> new -> dataProcess -> pairs).
# Official 11902 shape (v7 capture): string ids -> arrays of 0/1 flags and
# int progress.  Our progress stays 0 (no v2 action maps to v7 counters).
_TIME_LIMIT_EXPIRATION = 86400  # ConfigEnvSet "TimeLimitExpirationSeconds"

# Original award rows, quoted from the official 11904 responses (activity 9
# stages 1-5: 50k silver + item 201 + item 203).  Activities 1-8 keep neutral
# empty AwardCfg: their captured v7 rewards use item ids absent from the v2
# TcItem, so the v2 client cannot render their icons.
# JUDGMENT CALLS (not in the capture): RequireNum 0 makes these original
# rewards claimable; IsShow 1 keeps the rows visible.
_TIME_LIMIT_AWARD_CFG: dict[int, list[dict]] = {
    1: [
        # Activity 1 stage 1, quoted from the official 11904 response
        # (v7_actions.jsonl 12:40:10Z).  The captured row also carried four
        # ActivityId-5 items absent from the v2 TcItem; omitted.
        {"IsShow": 1, "RequireNum": 0,
         "Award": [{"ItemId": 509, "Num": 1},
                   {"ItemId": 210, "Num": 1},
                   {"ItemId": 505, "Num": 1},
                   {"ItemId": 507, "Num": 1}]},
    ],
    9: [
        {"IsShow": 1, "RequireNum": 0,
         "Award": [{"ItemId": 2, "Num": 50000},
                   {"ItemId": 201, "Num": 1},
                   {"ItemId": 203, "Num": 1}]}
        for _stage in range(1, 6)
    ],
}


def _time_limit_rows(activity_id: int) -> list[dict]:
    """AwardCfg rows as a LIST with explicit stage index (sortData defaults
    a missing index to the array position, but drawViewG titles by row.index)."""
    return [
        {**row, "index": stage,
         "Award": [dict(item) for item in row.get("Award") or []]}
        for stage, row in enumerate(_TIME_LIMIT_AWARD_CFG.get(int(activity_id), []), 1)
    ]


def _leading_claimed_stages(flags: list[int]) -> int:
    prefix = 0
    for flag in flags:
        if flag != 1:
            break
        prefix += 1
    return prefix


def _time_limit_served_flags(award_get: dict, activity_id: int) -> list[int]:
    """11902 wire flags: dense [1]*k for the leading claimed stages.

    The v2 client's sortData (modules/_limitTimeMod.lua 698-726) renders a
    row unclaimed only when its flag is nil, and claimed only when it is 1;
    any other truthy value (the official v7 0) makes the row vanish.  JSON
    cannot punch holes in an array, so claims are sequential (see 11904).
    """
    raw = award_get.get(str(activity_id))
    flags = [int(f) for f in raw] if isinstance(raw, list) else []
    return [1] * _leading_claimed_stages(flags)
    return [
        {**row, "Award": [dict(item) for item in row.get("Award") or []]}
        for row in _TIME_LIMIT_AWARD_CFG.get(int(activity_id), [])
    ]


def _time_limit_ids(player: Player) -> list[int]:
    activity = player.data.get("Activity")
    queued = activity.get("TimeLimitActivity") if isinstance(activity, dict) else None
    ids: list[int] = []
    if isinstance(queued, list):
        for entry in queued:
            if isinstance(entry, dict):
                try:
                    activity_id = int(entry.get("Id"))
                except (TypeError, ValueError):
                    continue
                if activity_id > 0 and activity_id not in ids:
                    ids.append(activity_id)
    return ids or list(range(1, 10))


def _time_limit_state(player: Player) -> tuple[dict, dict]:
    award_get = player.data.get("TimeLimitAwardGet")
    if not isinstance(award_get, dict):
        award_get = {}
        player.data["TimeLimitAwardGet"] = award_get
    complete = player.data.get("TimeLimitComplete")
    if not isinstance(complete, dict):
        complete = {}
        player.data["TimeLimitComplete"] = complete
    return award_get, complete


def _time_limit_flags(award_get: dict, activity_id: int, row_count: int) -> list[int]:
    raw = award_get.get(str(activity_id))
    flags = [int(f) for f in raw] if isinstance(raw, list) else []
    if len(flags) < row_count:
        flags += [0] * (row_count - len(flags))
    return flags


def _time_limit_window_open(ts: int | None = None) -> bool:
    now = _game_time(ts)
    try:
        start = datetime.datetime.strptime(
            _TIME_LIMIT_SCHEDULE[0], _GAME_DATE_FMT).replace(tzinfo=_GAME_TZ)
        end = datetime.datetime.strptime(
            _TIME_LIMIT_SCHEDULE[1], _GAME_DATE_FMT).replace(tzinfo=_GAME_TZ)
    except ValueError:
        return False
    return start <= now <= end + datetime.timedelta(seconds=_TIME_LIMIT_EXPIRATION)


def action_11902(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_limitTimeMod.netActivitySchedule: every advertised id must appear in
    both maps, with flags as a list, or the sub scene's dataProcess pairs(nil)s.
    """
    award_get, complete = _time_limit_state(player)
    served: dict[str, list[int]] = {}
    progress: dict[str, int] = {}
    for activity_id in _time_limit_ids(player):
        served[str(activity_id)] = _time_limit_served_flags(award_get, activity_id)
        progress[str(activity_id)] = _as_int(complete.get(str(activity_id)))
    return {"TimeLimitAwardGet": served, "TimeLimitComplete": progress}


def action_11903(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_limitTimeMod.netActivityRank -> RankList (array of {NickName, Score,
    RId}) and RankSelf ([rank, score]).  Rows are real dev-store players
    ranked by their persisted TimeLimitComplete[activityId]; no fake ranks.
    """
    activity_id = _as_int(payload.get("ActivityId"))
    rows: list[dict] = []
    scores: dict[str, int] = {}
    for uid, data in _ranking_players(store, player).items():
        complete = data.get("TimeLimitComplete") if isinstance(data, dict) else None
        score = _as_int(complete.get(str(activity_id))) if isinstance(complete, dict) else 0
        scores[uid] = score
        base = data.get("RoleBase") if isinstance(data.get("RoleBase"), dict) else {}
        rows.append({
            "NickName": str(base.get("Nickname") or ""),
            "Score": score,
            "RId": str(base.get("Rid") or uid),
        })
    rows.sort(key=lambda r: (-r["Score"], r["RId"]))
    base = player.data.get("RoleBase") if isinstance(player.data.get("RoleBase"), dict) else {}
    self_id = str(base.get("Rid") or player.uid)
    self_rank = 0
    for index, row in enumerate(rows, 1):
        if row["RId"] == self_id:
            self_rank = index
            break
    return {"RankList": rows, "RankSelf": [self_rank, scores.get(str(player.uid), 0)]}


def action_11904(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_limitTimeMod.netGetAward: claim one stage with the original award rows.
    Validates window / bounds / claimed flag / progress; grants only config
    amounts and persists the flag for the next 11902.
    """
    activity_id = _as_int(payload.get("ActivityId"))
    if activity_id <= 0:
        return {"_fail": "Unknown activity"}
    stage = _as_int(payload.get("Stage"))
    rows = _time_limit_rows(activity_id)
    if not rows or not 1 <= stage <= len(rows):
        return {"_fail": "No reward available"}
    row = rows[stage - 1]
    if not _time_limit_window_open():
        return {"_fail": "Event over"}
    award_get, complete = _time_limit_state(player)
    flags = _time_limit_flags(award_get, activity_id, len(rows))
    if flags[stage - 1]:
        return {"_fail": "Already claimed"}
    # Sequential claims keep the served flag prefix dense (JSON cannot encode
    # holes); an out-of-order tap gets an Info toast instead of a grant that
    # 11902's wire form cannot represent.
    if stage != _leading_claimed_stages(flags) + 1:
        return {"_fail": "Claim the previous reward first"}
    if _as_int(complete.get(str(activity_id))) < _as_int(row.get("RequireNum")):
        return {"_fail": "Not enough progress"}
    bag_ids: list[int] = []
    role_data = player.data.setdefault("RoleData", {})
    for item in row.get("Award") or []:
        item_id = _as_int(item.get("ItemId"))
        num = _as_int(item.get("Num"))
        if item_id <= 0 or num <= 0:
            continue
        key = _REMAIN_LIST_MAP.get(item_id)
        if key is not None:
            role_data[key] = _as_int(role_data.get(key)) + num
        else:
            _role_item_add(player, item_id, num)
            bag_ids.append(item_id)
    flags[stage - 1] = 1
    award_get[str(activity_id)] = flags
    store.save(player)
    response = {"FinalShowAwardCfg": [dict(item) for item in row["Award"]]}
    return attach_remain_list(response, player, items=bag_ids)


# action_achieve / action_achieve_claim live in the achievements section above.


def action_store_items(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_storeMod action11101: setItemData(IsPackage, A0_3) iterates the item
    tables; the callback also pairs() over A0_3.RemainList."""
    return {"IsPackage": payload.get("IsPackage", 0), "ItemData": [],
            "PackageData": [], "RemainList": [], "LimitList": []}


def action_mail_list(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_mailMod action12111: fCopyTab(A0_3.MailList) must be a table; MailExpireDay
    feeds the 30-day purge default."""
    return {"MailList": [], "MailExpireDay": 30}


# --------------------------------------------------------------------------
# rankings (11001-11007)
# --------------------------------------------------------------------------
_RANK_WORSHIP_KEYS = ("PowerRank", "SectionRank", "IntimacyRank")
_CROSS_WORSHIP_KEYS = ("CrossPower", "CrossAlliance", "CrossIntimacy")


def _rank_worship_time(player: Player | None = None) -> dict[str, str]:
    """Per-key last-worship dates parseable by dateToNumber(date, "day").

    _rankingMod.lua 314-352 writes a *stored* date for the worshipped key and
    rankingScene 210-218 disables the button when that date is not older than
    today.  Returning "now" made the button permanently disabled, so the
    stored player.data["RankWorshipTime"] values are served verbatim.  A fresh
    account (or a missing key) falls back to yesterday UTC+3 so one collect
    is possible; nothing is granted by this read.
    """
    stored = player.data.get("RankWorshipTime") if player else None
    fallback = (_game_time() - datetime.timedelta(days=1)).strftime(_GAME_DATE_FMT)
    times: dict[str, str] = {}
    for key in _RANK_WORSHIP_KEYS:
        value = stored.get(key) if isinstance(stored, dict) else None
        times[key] = (value if isinstance(value, str) and value
                      and _game_day_number(value) else fallback)
    return times


def _worship_key(payload: dict) -> str:
    """Map WorshipType -> RankWorshipTime key (v2 _rankingMod.lua 314-330).

    Types 0/1/2 are the three normal rank tabs, 10/11/12 the cross-server
    tabs.  The v7 capture also used 3 and 16, which have no v2 config
    mapping; those are folded into the first key so the daily guard still
    has a stable slot (documented, not invented).
    """
    try:
        worship_type = int(payload.get("WorshipType") or 0)
    except (TypeError, ValueError):
        worship_type = 0
    if 0 <= worship_type < len(_RANK_WORSHIP_KEYS):
        return _RANK_WORSHIP_KEYS[worship_type]
    if 10 <= worship_type < 10 + len(_CROSS_WORSHIP_KEYS):
        return _CROSS_WORSHIP_KEYS[worship_type - 10]
    return _RANK_WORSHIP_KEYS[0]


def _ranking_players(store: Store, requester: Player) -> dict[str, dict]:
    """Real dev-store players only; never invent NPC competitors."""
    pool: dict[str, dict] = {}
    players = getattr(store, "_players", None)
    if isinstance(players, dict):
        for uid, pl in players.items():
            data = getattr(pl, "data", None)
            if isinstance(data, dict):
                pool[str(uid)] = data
    # The requester is always in the pool, with its freshest in-memory data.
    pool[str(requester.uid)] = requester.data
    return pool


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --- Sultan Boost (client "MiracleTrigger") -------------------------------
# ConfigEnvSet.MiracleProbability is [0.15] x7 in the original v2 config —
# the real odds are 15%, not a guess.  The client plays a visual effect when
# MiracleTrigger is true; the multiplied amounts ship in RemainList.  Client
# text gives the multipliers: levy/affairs resources 10x, affairs EXP/books 3x,
# ranking worship +110 instead of +10 (11x, proven by the v7 capture).
def _miracle_roll() -> bool:
    cfg = v2configs.load("ConfigEnvSet")
    probs = cfg.get("MiracleProbability") if isinstance(cfg, dict) else None
    chance = float(probs[0]) if isinstance(probs, list) and probs else 0.15
    return random.random() < chance


def _miracle_multiplier(item_id: int) -> int:
    """Affairs: resources 10x, EXP/attribute items 3x (client text)."""
    return 10 if int(item_id) in (2, 3, 4) else 3


def _rank_section_record(section: dict) -> int:
    """Encode RoleSection -> the client's TotalSecRecord ordering.

    VERIFIED against modules/_rankingMod.lua 119-153 (get_section):
    math.modf(TotalSecRecord / 1000) yields the chapter as the integer part and
    math.modf(frac * 10) yields the section, i.e. the wire value is
    chapter*1000 + section*100 + phase.  The alternative chapter*1000000 +
    section*1000 + phase would make getChapterName read chapter*1000.  The
    official 11001 RoleSection rows (session-20261116-093154.log line 17909:
    23110, 18110, 9310) decode to chapters 23/18/9 and sections 1/1/3 with
    this formula.
    """
    chapter = int(section.get("ChapterId") or 1)
    sec = int(section.get("SectionId") or 1)
    phase = int(section.get("PhaseId") or 0)
    return chapter * 1000 + sec * 100 + max(0, phase)


def _total_intimacy(data: dict):
    """Client parity with _wivesMod.get_total_intimacy (modules/_wivesMod.lua
    896-908): sum the Intimacy of unlocked wives.  Dev accounts have no
    unlocked wives, so None => no intimacy rows (never a fake amount)."""
    wives = data.get("RoleBeauty")
    if isinstance(wives, dict):
        candidates = list(wives.values())
    elif isinstance(wives, list):
        candidates = wives
    else:
        return None
    total = 0
    found = False
    for wife in candidates:
        if isinstance(wife, dict) and isinstance(wife.get("Intimacy"), (int, float)):
            total += int(wife["Intimacy"])
            found = True
    return total if found else None


def _rank_row(uid: str, data: dict) -> dict:
    """One player row for RolePower / RoleSection / BeautyIntimacy.

    Fields read by rankingScene.rankingScene.lua drawViewG (406-854):
    Nickname (482/492), TitleId (535), Dress (579), Lv (659),
    TotalPower (706), TotalSecRecord (via get_section 744-748),
    TotalIntimacy (779), RoleId (830).  The remaining identity cosmetics are
    the exact keys of the official 11001 RoleSection rows (session log 17909).
    """
    base = data.get("RoleBase") if isinstance(data.get("RoleBase"), dict) else {}
    role = data.get("RoleData") if isinstance(data.get("RoleData"), dict) else {}
    section = data.get("RoleSection") if isinstance(data.get("RoleSection"), dict) else {}
    box = base.get("AvatarBoxData") if isinstance(base.get("AvatarBoxData"), dict) else {}
    dress_src = base.get("DressList") or role.get("DressList") or []
    if not isinstance(dress_src, (list, tuple)):
        dress_src = []
    dress = []
    for entry in dress_src:
        item_id = entry.get("ItemId") if isinstance(entry, dict) else entry
        item_id = _as_int(item_id)
        if item_id:
            dress.append({"ItemId": item_id})
    return {
        # identity/cosmetic fields from the official 11001 capture rows
        "RoleId": str(base.get("Rid") or uid),
        "Nickname": str(base.get("Nickname") or role.get("Nickname") or ""),
        "Sex": _as_int(base.get("Sex"), 1),
        "Lv": _as_int(role.get("Lv") or base.get("Lv"), 1),
        "VipLv": _as_int(role.get("VipLv") or base.get("VipLv")),
        "Avatar": _as_int(base.get("Avatar") or role.get("Avatar")),
        "ChatAvatar": str(base.get("ChatAvatar") or role.get("ChatAvatar") or "F400"),
        "Figure": _as_int(base.get("Figure") or role.get("Figure")),
        "Dress": dress,
        "AvatarBox": _as_int(base.get("AvatarBox")),
        "AvatarBoxEffect": _as_int(base.get("AvatarBoxEffect")),
        "AvatarBoxData": {"SeasonRank": _as_int(box.get("SeasonRank"))},
        "ItemTitleId": _as_int(base.get("ItemTitleId")),
        "TitleId": _as_int(base.get("TitleId")),  # rankingScene 535; 0 = no icon
        "RankBottomId": _as_int(base.get("RankBottomId")),
        "GloryData": None,        # official capture uses null
        "FashionEffectInfo": [],  # official capture uses [] / {} (empty Lua table)
        # sort / display values consumed by drawViewG
        "TotalPower": _as_int(role.get("Power") or base.get("Power")),
        "TotalSecRecord": _rank_section_record(section),
        "TotalIntimacy": 0,  # overwritten for the intimacy tab below
    }


def _player_four_attributes(data: dict) -> list[int]:
    """The four profile attributes, summed from unlocked counsellors and
    children (client roleMod.lua 288-355 / _ministerMod.lua 1586-1609).
    Dev accounts persist AttributeValue [0,0,0,0] and no children, so the
    honest aggregate is [0,0,0,0]; nothing fabricated."""
    attrs = [0, 0, 0, 0]

    def add(entry: object) -> None:
        if not isinstance(entry, dict):
            return
        for index, key in enumerate(("Force", "Intelligence", "Politics", "Charm")):
            if isinstance(entry.get(key), (int, float)):
                attrs[index] += int(entry[key])

    for c in (data.get("RoleCounsellor") or []):
        if isinstance(c, dict) and int(c.get("IsLock") or 0) == 0:
            values = c.get("AttributeValue")
            if isinstance(values, list):
                for index in range(4):
                    attrs[index] += _as_int(values[index]) if index < len(values) else 0
    for child in (data.get("Children") or []):
        add(child)
    for o_child in (data.get("OChildren") or []):
        if isinstance(o_child, dict):
            add(o_child.get("oChild"))
    return attrs


def _rank_profile_row(uid: str, data: dict) -> dict:
    """Full row for gameUi.showRoleInformation (ui/gameUi.lua 9130-10341):
    every field the dialog reads unguarded must be present with the right
    type, or the profile draws empty.  Neutral values mirror the client's
    own customer-service profile mock (modules/_diplomacyMod.lua 461-490).
    """
    row = _rank_row(uid, data)
    base = data.get("RoleBase") if isinstance(data.get("RoleBase"), dict) else {}
    section = data.get("RoleSection") if isinstance(data.get("RoleSection"), dict) else {}
    attrs = _player_four_attributes(data)
    intimacy = _total_intimacy(data)
    row.update({
        "ShowId": _as_int(base.get("ShowId")),
        "DressList": list(row["Dress"]),       # dialog reads DressList, not Dress
        "Section": _rank_section_record(section),
        "AllianceId": 0, "AllianceName": "",
        "Intimacy": intimacy if intimacy is not None else 0,
        "Force": attrs[0], "Intelligence": attrs[1],
        "Politics": attrs[2], "Charm": attrs[3],
        "TitleList": [], "IsBlack": 0, "IsFriend": 0,
    })
    return row


def _rank_of(rows: list[dict], self_id: str) -> int:
    for index, row in enumerate(rows, 1):
        if str(row["RoleId"]) == self_id:
            return index
    # The client shows "9999+" only for 0 or >9999 (rankingScene 55-56), so 0
    # is its unranked sentinel; 9999 would render as a literal "9999".
    return 0


def action_rank_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_rankingMod.action11001 (modules/_rankingMod.lua 261-299) stores
    RankSelf/RolePower/RoleSection/BeautyIntimacy/RankWorshipTime in tbData.
    rankingScene.rankingScene.lua 1035-1054 passes RolePower/RoleSection/
    BeautyIntimacy directly to drawScrollList, and 1081 takes #L11_2, so all
    three MUST be arrays (the old scalar RoleSection:1 crashed the scene).
    RankSelf keys are PowerRank/SectionRank/IntimacyRank (rankingScene 43-56).
    """
    pool = _ranking_players(store, player)
    rows = [_rank_row(uid, data) for uid, data in pool.items()]
    power_rows = sorted(rows, key=lambda r: r["TotalPower"], reverse=True)
    section_rows = sorted(rows, key=lambda r: r["TotalSecRecord"], reverse=True)

    intimacy_rows = []
    for uid, data in pool.items():
        intimacy = _total_intimacy(data)
        if intimacy is None:
            continue
        row = _rank_row(uid, data)
        row["TotalIntimacy"] = intimacy
        intimacy_rows.append(row)
    intimacy_rows.sort(key=lambda r: r["TotalIntimacy"], reverse=True)

    base = player.data.get("RoleBase") if isinstance(player.data.get("RoleBase"), dict) else {}
    self_id = str(base.get("Rid") or player.uid)
    worship_time = _rank_worship_time(player)
    return {
        # rankingScene.rankingScene.lua reads RankSelf through the
        # {"TotalPower","Section","Intimacy"} key array (lines 36-54).  The
        # PowerRank/SectionRank/IntimacyRank names belong to RankWorshipTime
        # (client lines 43-49 + 209-216), NOT to RankSelf.  Using the latter
        # here made RankSelf["TotalPower"] nil and `9999 < nil` aborted
        # showOverlay with "attempt to compare number with nil".
        "RankSelf": {
            "TotalPower": _rank_of(power_rows, self_id),
            "Section": _rank_of(section_rows, self_id),
            "Intimacy": _rank_of(intimacy_rows, self_id),
        },
        "RolePower": power_rows,
        "RoleSection": section_rows,
        "BeautyIntimacy": intimacy_rows,
        "RankWorshipTime": worship_time,
        # cross-server tab rows (rankingScene 1045-1050); closed in dev
        "RolePowerRank": [],
        "AllianceRank": [],
        "MiracleTrigger": False,
    }


def action_rank_worship(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_rankingMod.action11002 (modules/_rankingMod.lua 301-369).

    Official v7 capture: RemainList[{ItemId:1, Num:absolute Gold}] and
    IncrRemainList[{ItemId:1, Num:10}] -- +10 Gold per worship (source:
    recon/v7-capture/v7_actions.jsonl, 11002 responses 2026-11-16).  The
    11002 response also carried MiracleTrigger true once (+110 increment), but
    no client config defines that rule, so it stays False.  Mirroring the
    sign-in guard, the +10 is collectable once per UTC+3 game day per worship
    key; the stored date is what 11001 (and the client button) reads.
    """
    key = _worship_key(payload)
    rwt = player.data.get("RankWorshipTime")
    if not isinstance(rwt, dict):
        rwt = {}
        player.data["RankWorshipTime"] = rwt
    today = _game_date_string()
    if _game_day_number(rwt.get(key)) == _game_day_number(today):
        return {"MiracleTrigger": False}  # already collected today
    role_data = player.data.setdefault("RoleData", {})
    # Sultan Boost: the v7 capture has one MiracleTrigger=true worship with
    # +110 instead of the usual +10 (11x total).
    miracle = _miracle_roll()
    increment = 110 if miracle else 10
    gold = int(role_data.get("Gold") or 0) + increment
    role_data["Gold"] = gold
    rwt[key] = today
    store.save(player)
    return {
        "RemainList": [{"ItemId": 1, "Num": gold}],
        "IncrRemainList": [{"ItemId": 1, "Num": increment}],
        "MiracleTrigger": miracle,
    }


def action_rank_player_detail(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_rankingMod.action11003 (modules/_rankingMod.lua 371-407): the response
    is cached by QueryId and passed to gameUi.showRoleInformation (gameUi.lua
    10399-10424).  Unknown id -> empty table, never a numeric scalar."""
    query_id = str(payload.get("QueryId") or "")
    if not query_id:
        return {}
    data = _ranking_players(store, player).get(query_id)
    if data is None:
        return {}
    return _rank_profile_row(query_id, data)


def action_rank_cross_list(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_rankingMod.action11004 (modules/_rankingMod.lua 409-530): the request
    RankType is the tab index + 10, and the callback pairs() over
    A0_3[CrossPower|CrossAlliancePower|CrossIntimacy] and reads
    RankWorshipTime / SelfRank (SelfAlliance is optional).  The dev server is
    single-server (IsCrossRankOpen=0, defaults.py 200), so there are no cross
    rows: return empty tables, not 0."""
    return {
        "CrossPower": [],
        "CrossAlliancePower": [],
        "CrossIntimacy": [],
        "RankWorshipTime": _rank_worship_time(),
        "SelfRank": 0,
    }


def action_rank_top1(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_rankingMod.action11005 (modules/_rankingMod.lua 532-608): Top1 and
    CrossTop1 become tPlayer[1..2]; rankEntranceDialog.lua 403-469 reads
    name/power/lv/avatar/dressList/figure/chatAvatar.  Both entrances use the
    real top player (single dev server), never a made-up competitor."""
    rows = [_rank_row(uid, data) for uid, data in _ranking_players(store, player).items()]
    rows.sort(key=lambda r: r["TotalPower"], reverse=True)
    open_cross = _as_int(player.data.get("IsCrossRankOpen"))
    if not rows:  # defensive: the requester is normally always present
        return {"IsCrossRankOpen": open_cross, "Top1": {}, "CrossTop1": {}}
    top = rows[0]

    def entrance(row: dict) -> dict:
        return {
            "Name": row["Nickname"],
            "Power": row["TotalPower"],
            "Lv": row["Lv"],
            "Avatar": row["Avatar"],
            "Dress": row["Dress"],
            "Figure": row["Figure"],
            "ChatAvatar": row["ChatAvatar"],
        }

    best = entrance(top)
    return {"IsCrossRankOpen": open_cross, "Top1": best, "CrossTop1": dict(best)}


def action_rank_cross_player_detail(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_rankingMod.action11006 (modules/_rankingMod.lua 610-628) is the
    cross-server PLAYER detail: showRoleInformation's other-server branch
    (ui/gameUi.lua 10426-10451) sends {RoleId, QueryServerId} and feeds the
    response straight into the profile dialog.  Chat MsgData.ServerId is a
    string ('1') while the login route id is numeric, so Lua '1' ~= 1 makes
    every chat profile click land here.  The old alliance stub returned {}
    and the dialog drew empty.  Single dev server: resolve locally.
    """
    role_id = str(payload.get("RoleId") or "")
    if not role_id:
        return {}
    data = _ranking_players(store, player).get(role_id)
    if data is None:
        return {}
    return _rank_profile_row(role_id, data)


def action_rank_alliance_rank_detail(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_rankingMod.action11007 (modules/_rankingMod.lua 630-665) maps
    Name/Lv/FlagId/MemNum/Power/AllianceId/FaceBook/Notice into the alliance
    detail dialog.  No alliance data exists in the dev store -> empty table."""
    return {}


def _sign_day(check_in_day: int) -> int:
    """_signMod.getSignDay (modules/_signMod.lua lines 55-62): the 21-day
    sign-in cycle day = CheckInDay % 21, where an exact multiple maps to 21."""
    day = int(check_in_day or 0)
    sign_day = day % 21
    if day > 0 and sign_day == 0:
        sign_day = 21
    return sign_day


def _cumulative_flags(player: Player) -> list:
    """CumulativeList holds one 0/1 claim flag per TcSignInCumulativeRewards
    milestone.  _signMod.isRedPoint pairs() over it and indexes
    TcSignInCumulativeRewards[key].CumulativeDays with the SAME key, so the
    list must have exactly one entry per config row (4 in the v2 config)."""
    rows = len(v2configs.cumulative_reward_rows()) or 4
    flags = player.data.get("CumulativeList")
    if (
        isinstance(flags, list)
        and len(flags) == rows
        and all(isinstance(f, int) and 0 <= f <= 1 for f in flags)
    ):
        return list(flags)
    return [0] * rows


def action_sign_checkin(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_signMod.action11502 (modules/_signMod.lua lines 198-275).

    The client sends an empty request; the callback copies
      CheckInDay, CumulativeList, CheckTypeId (and stamps its own
      CheckInReFreshTime), then inserts A0_3.CheckInRecord[1] into its
      CheckInRecords (max 30, newest first).
    The daily reward display comes from getTempItemData() and the response's
    RemainList.  The v2 client defines NO per-day reward table (TcSignInType
    is only flavour text), so the daily grant uses the official v7 capture
    (three 60-Gold awards, credited to Gold).  Cumulative milestone rewards
    are config-defined and claimed separately through 11507.

    CheckInRecord entries must carry CheckInRefreshTime / CheckTypeId /
    TodayAward (signScene.signScene.lua lines 366/411/449 read exactly
    those), with TodayAward a table (pairs() over it).
    """
    # Daily guard: isRedPoint (_signMod.lua 103-159) only lights up when the
    # current game day is strictly greater than the stored refresh day.  Match
    # that exactly; >= also rejects a stored time from the future (clock skew).
    stored = _stored_checkin_refresh(player)
    today_number = _game_day_number(_game_date_string())
    if _game_day_number(stored) >= today_number:
        # handle_forward turns _fail into a Code 1 envelope: a prompt is shown
        # and the callback is skipped, so no state is touched and no reward is
        # granted on a repeat collect.
        return {"_fail": "already checked in today"}

    day = int(player.data.get("CheckInDay") or 0)
    old_sign_day = _sign_day(day)
    day += 1
    new_sign_day = _sign_day(day)
    flags = _cumulative_flags(player)
    if old_sign_day == 21 and new_sign_day == 1:
        flags = [0] * len(flags)  # a new 21-day cycle resets the milestones
    record = _normalize_checkin_records(player)
    award = _sign_daily_award()
    today_award_pairs = [[int(e["ItemId"]), int(e["Num"])] for e in award]
    # Credit the captured daily reward (v7: three 60-Gold awards).  v2 has no
    # per-day reward config, so this comes from the official server response.
    role_data = player.data.setdefault("RoleData", {})
    gold_gain = sum(int(e["Num"]) for e in award if int(e["ItemId"]) == 1)
    role_data["Gold"] = int(role_data.get("Gold") or 0) + gold_gain
    refresh = _game_date_string()  # UTC+3, the same boundary isRedPoint uses
    record.insert(0, {
        "CheckInRefreshTime": refresh,  # signScene string.sub key
        "CheckTypeId": 10,              # TcSignInType id used by the record list
        "TodayAward": today_award_pairs,  # pairs() over it in signScene
        "Day": day,
    })
    del record[30:]
    player.data["CheckInDay"] = day
    player.data["CumulativeList"] = flags
    player.data["CheckInRecord"] = record
    player.data["CheckInRefreshTime"] = refresh
    # Keep the client-facing login field in sync too (RoleData shape stable).
    player.data["CheckInReFreshTime"] = refresh
    store.save(player)
    return attach_remain_list({
        "CheckInDay": day,
        "CumulativeList": flags,
        "CheckInRecord": record,
        "CheckInRecords": record,
        "CheckTypeId": 10,
        "CheckInReFreshTime": refresh,
        "Award": award,
    }, player)


def action_sign_cumulative(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_signMod.action11507 {CumuDay: milestone box id}.

    Claim a TcSignInCumulativeRewards milestone once its CumulativeDays is
    reached in the current 21-day cycle: grants the row's CumulativeRewards
    ({ItemId, Num} pairs) and flips CumulativeList[box] to 1.  The callback
    reads only A0_3.CumulativeList; the granted items/resources must be in
    RemainList so they reach the bag/top bar.
    """
    try:
        box = int(payload.get("CumuDay") or 0)
    except (TypeError, ValueError):
        box = 0
    rows = v2configs.cumulative_reward_rows()
    if box < 1 or box > len(rows):
        return {"_fail": "bad cumulative reward box"}
    cfg = rows[box - 1] or {}
    days = int(cfg.get("CumulativeDays") or 0)
    if _sign_day(player.data.get("CheckInDay") or 0) < days:
        return {"_fail": f"cumulative reward needs {days} sign-in days"}
    flags = _cumulative_flags(player)
    if flags[box - 1] == 1:
        return {"_fail": "cumulative reward already claimed"}
    granted_items = []
    role_data = player.data.setdefault("RoleData", {})
    for group in cfg.get("CumulativeRewards") or []:
        if not isinstance(group, list):
            continue
        for pair in group:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            try:
                item_id = int(pair[0])
                num = int(pair[1])
            except (TypeError, ValueError):
                continue
            if 1 <= item_id <= 6:
                key = _REMAIN_LIST_MAP[item_id]
                role_data[key] = int(role_data.get(key) or 0) + num
            else:
                _role_item_add(player, item_id, num)
                granted_items.append(item_id)
    flags[box - 1] = 1
    player.data["CumulativeList"] = flags
    store.save(player)
    return attach_remain_list({"CumulativeList": flags}, player, items=granted_items)


def action_diplomacy_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_diplomacyMod action18129 {Login:1}: the callback ignores the payload,
    but the sibling actions (18120/18121) read BlackList / FriendList from the
    same module state, so return the consistent empty diplomacy snapshot."""
    return {"FriendList": [], "BlackList": [], "RecentList": [], "SysGuid": int(player.data.get("SysGuid") or 0)}


def action_diplomacy_black(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_diplomacyMod action18120 callback: pairs() over A0_3.BlackList."""
    return {"BlackList": [], "RecentList": []}


def action_diplomacy_friends(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_diplomacyMod action18121 callback.

    The response callback (modules/_diplomacyMod.lua, `action18121`) does an
    UNGUARDED `pairs(A0_3.RecentChatList)` when it rebuilds the Recent-chat tab
    (TeamChat itself is guarded by `if A0_3.TeamChat then`).  The field the
    client reads is `RecentChatList`; the old dev response sent `RecentList`,
    which no 18121 callback reads, so `pairs(nil)` aborted the dispatch ->
    callBackFunc chain ("bad argument #1 to 'pairs' (table expected, got nil)")
    on device 2026-11-17 21:47:13-15 UTC+3.  Send the real field name with an
    empty table (the dev store has no recent-chat entries); keep the original
    list data and shape, do not invent entries.
    """
    return {"FriendList": [], "RecentList": [], "RecentChatList": []}


def action_wives_guide(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_wifeCompeteMod action18100 {SysId}: set the requested system-guide bit
    in the persisted RoleData.SysGuid mask.

    The client tests guide N via roleMod.judgeIsGuided/unlockRule: bit N from
    the LSB of RoleData.SysGuid (roleMod.lua 225-248).  The 18100 callback
    (_wifeCompeteMod.lua 793-801) adopts the response only when
    getRoleData("SysGuid") < response.SysGuid; echoing 0 forever made the
    Social/diplomacy tutorial replay on every tab entry (repeated 18100
    SysId=2 in the action log).  Bits are OR-ed in (1-based from LSB), so the
    response is monotonically non-decreasing and the adopt guard passes once.
    """
    try:
        sys_id = int(payload.get("SysId") or 0)
    except (TypeError, ValueError):
        sys_id = 0
    role_data = player.data.setdefault("RoleData", {})
    mask = int(role_data.get("SysGuid") or 0)
    if 1 <= sys_id <= 31:
        mask |= 1 << (sys_id - 1)
        role_data["SysGuid"] = mask
        store.save(player)
    return {"SysGuid": mask}


# --------------------------------------------------------------------------
# school (vizier academy) actions: 10901 assign, 10905 list, 10906/10907 study
# --------------------------------------------------------------------------
# Capacity reported to the client for the study-slot list (_schoolMod stores
# it via setCapacityLimit and the study scene sizes its slot list with it).
SCHOOL_CAPACITY = 5

# Counsellor (minister) silver needed to REACH each level, i.e. the silver
# consumed when upgrading from level L-1 to level L.  Extracted from the
# client config configs/settings.TcCounsellorUpgrade.lua
# (TcCounsellorUpgrade[Lv].NeedItem = {2, silver}); the client's upgrade exp
# bar is NowSilver / COUNSELLOR_LEVEL_SILVER[lv + 1].
COUNSELLOR_LEVEL_SILVER = [
    48, 100, 159, 228, 310, 408, 525, 664, 828, 1020, 1243, 1500, 1794, 2128,
    2505, 2928, 3400, 3924, 4503, 5140, 5838, 6600, 7429, 8328, 9300, 10348, 11475, 12684,
    13978, 15360, 16833, 18400, 20064, 21828, 23695, 25668, 27750, 29944, 32253, 34680, 37228, 39900,
    42699, 45628, 48690, 51888, 55225, 58704, 62328, 66100, 70023, 74100, 78334, 82728, 87285, 92008,
    96900, 101964, 107203, 112620, 118218, 124000, 129969, 136128, 142480, 149028, 155775, 162724, 169878, 177240,
    184813, 192600, 200604, 208828, 217275, 225948, 234850, 243984, 253353, 262960, 272808, 282900, 293239, 303828,
    314670, 325768, 337125, 348744, 360628, 372780, 385203, 397900, 410874, 424128, 437665, 451488, 465600, 480004,
    494703, 509700, 524998, 540600, 556509, 572728, 589260, 606108, 623275, 640764, 658578, 676720, 695193, 714000,
    733144, 752628, 772455, 792628, 813150, 834024, 855253, 876840, 898788, 921100, 943779, 966828, 990250, 1014048,
    1038225, 1062784,
]


def _level_silver(level: int):
    """Silver needed to REACH `level` (the cost of the lv-1 -> lv upgrade)."""
    if 1 <= level <= len(COUNSELLOR_LEVEL_SILVER):
        return COUNSELLOR_LEVEL_SILVER[level - 1]
    return None


def _school_capacity(player: Player) -> int:
    """Study slots bought through TcSchool (10904); fresh players keep the
    dev-server default of 5 so existing accounts behave as before."""
    school = player.data.get("School")
    if isinstance(school, dict):
        try:
            return int(school.get("Capacity") or SCHOOL_CAPACITY)
        except (TypeError, ValueError):
            pass
    return SCHOOL_CAPACITY


def _school_slots(player: Player) -> dict:
    """Server-side school state: position -> assigned counsellor.

    client shape per slot (seen in _schoolMod requireAcademy / action10907
    callbacks): {CounsellorId, StudyPosition, STUnix}.
    """
    school = player.data.get("School")
    if not isinstance(school, dict):
        school = {}
        player.data["School"] = school
    slots = school.get("Slots")
    if not isinstance(slots, dict):
        slots = {}
        school["Slots"] = slots
    return slots


def _school_data_list(player: Player) -> list:
    slots = _school_slots(player)
    out = []
    for key in sorted(slots, key=lambda k: int(k) if str(k).isdigit() else 0):
        slot = slots[key]
        out.append({
            "CounsellorId": int(slot.get("MinisterId") or 0),
            "StudyPosition": int(slot.get("Position") or 0),
            "STUnix": int(slot.get("StudyTime") or 0),
        })
    return out


def action_school_assign(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_schoolMod action10901 {PositionAndMinisterId: [{MinisterId, Position}]}.

    The client's callback applies the assignment locally from the request
    (setOneschoolInfo per entry: CounsellorId=MinisterId, StudyPosition=
    Position, StudyTime=now) and ignores the response Data, but we persist the
    assignment so action10905 can restore it after a re-login.
    """
    now = _now()
    entries = payload.get("PositionAndMinisterId")
    if not isinstance(entries, list):
        entries = []
    slots = _school_slots(player)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            minister_id = int(entry.get("MinisterId") or 0)
            position = int(entry.get("Position") or 0)
        except (TypeError, ValueError):
            continue
        if not minister_id or not position:
            continue
        # one counsellor can only sit in one position: drop stale slots
        for key in [k for k, v in slots.items() if int(v.get("MinisterId") or 0) == minister_id]:
            del slots[key]
        slots[str(position)] = {"Position": position, "MinisterId": minister_id, "StudyTime": now}
    store.save(player)
    return {"DataList": _school_data_list(player), "Capacity": _school_capacity(player)}


def action_school_list(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_schoolMod action10905: A0_3.DataList iterated with pairs() (counsellor
    study slots; each entry feeds StudyPosition/STUnix/CounsellorId) and
    A0_3.Capacity -> setCapacityLimit."""
    return {"DataList": _school_data_list(player), "Capacity": _school_capacity(player)}


def action_school_study(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_schoolMod action10906 {StudyArray: [counsellor ids]}: the callback
    stores roleMod.setRoleData("StudyArray", <request array>) locally; we
    persist it so the selection survives a re-login (it also ships inside the
    login RoleData)."""
    study = payload.get("StudyArray")
    if not isinstance(study, list):
        study = []
    try:
        study = [int(v) for v in study]
    except (TypeError, ValueError):
        study = []
    player.data.setdefault("RoleData", {})["StudyArray"] = study
    store.save(player)
    return {"StudyArray": study}


def _school_dispatch_pending(player: Player) -> list[dict]:
    """Seat the pending StudyArray lineup into free study slots.

    The "Dispatch All" button (BtnOneKey, schoolScene.lua 1931-2059) sends
    ONLY action10907 with an empty request; the lineup is the StudyArray
    persisted by 10906.  The client callback (_schoolMod.lua 574-610) appends
    each response entry without dedup, so DataList must contain ONLY the
    counsellors seated by this call; 10905 remains the full-list refresh.
    """
    slots = _school_slots(player)
    capacity = _school_capacity(player)
    occupied = {int(v.get("Position") or 0) for v in slots.values()
                if isinstance(v, dict)}
    seated = {int(v.get("MinisterId") or 0) for v in slots.values()
              if isinstance(v, dict) and v.get("MinisterId")}
    known = {int(c.get("CounsellorId") or 0)
             for c in (player.data.get("RoleCounsellor") or [])
             if isinstance(c, dict)}
    role_data = player.data.get("RoleData") if isinstance(player.data.get("RoleData"), dict) else {}
    study = role_data.get("StudyArray")
    if not isinstance(study, list):
        study = []
    now = _now()
    fresh: list[dict] = []
    seen: set[int] = set()
    for value in study:
        try:
            cid = int(value)
        except (TypeError, ValueError):
            continue
        if not cid or cid in seen:
            continue
        seen.add(cid)
        if cid in seated or cid not in known:
            continue
        position = next((p for p in range(1, capacity + 1) if p not in occupied), None)
        if position is None:
            break
        occupied.add(position)
        slot = {"Position": position, "MinisterId": cid, "StudyTime": now}
        slots[str(position)] = slot
        fresh.append(slot)
    fresh.sort(key=lambda s: int(s.get("Position") or 0))
    return fresh


def action_school_study_list(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_schoolMod action10907 (empty request): the "Dispatch All" button.

    The callback pairs() over A0_3.DataList and appends each entry's
    CounsellorId/StudyPosition locally; seating the persisted 10906 lineup
    here is what makes the button work (the client sends no assignments).
    """
    fresh = _school_dispatch_pending(player)
    if fresh:
        store.save(player)
    data_list = [{
        "CounsellorId": int(s.get("MinisterId") or 0),
        "StudyPosition": int(s.get("Position") or 0),
        "STUnix": int(s.get("StudyTime") or 0),
    } for s in fresh]
    return {"DataList": data_list, "Capacity": _school_capacity(player)}


# --------------------------------------------------------------------------
# school completion (10902/10903) and capacity purchase (10904)
# --------------------------------------------------------------------------
def _skill_level_cost(level: int) -> int:
    """_ministerMod.getSkillLvExp(lv) (modules/_ministerMod.lua lines
    2358-2374): the skill exp needed to go from SkillLv `level` to `level+1`
    is round(0.03*floor((lv+1)/2)^2 + 1.03*floor((lv+1)/2) + 1); the knapsack
    and court scenes display it as the "needExp" bar (knapsackSceneTab1.lua
    line 1346, courtBattleEndDialog.lua line 1705)."""
    m = (int(level) + 1) // 2
    return int(math.floor(0.03 * m * m + 1.03 * m + 1 + 0.5))


def _apply_skill_levels(counsellor: dict) -> int:
    """Level SkillLv up while SkillExp covers getSkillLvExp(lv); consumed exp
    mirrors the client's needExp bar.  Returns 1 when at least one level was
    gained (the 10902/10903 callbacks read A0_3.SkillUp)."""
    cap = int(v2configs.env_value("MaxEloquenceLv", 286) or 286)
    level = int(counsellor.get("SkillLv") or 1)
    exp = int(counsellor.get("SkillExp") or 0)
    up = 0
    while level < cap:
        need = _skill_level_cost(level)
        if exp < need:
            break
        exp -= need
        level += 1
        up = 1
    counsellor["SkillExp"] = exp
    counsellor["SkillLv"] = level
    return up


def _school_complete_one(player: Player, counsellor_id: int) -> dict | None:
    """Finish one counsellor's study session and return the record the
    10902/10903 callbacks consume, or None when the session is still running.

    Gains come from ConfigEnvSet (server-side values; the v2 client never
    reads them): StudyBookExp/StudySkillExp per StudyCd session.  Completion
    is StudyCd elapsed since the slot's StudyTime (_schoolMod stores
    StudyTime on 10901; schoolScene gates the collect on getEnvCfg
    "StudyCd").  Miracle (prodigy) sessions have no config-defined rate, so
    MiracleTrigger stays 0.
    """
    slots = _school_slots(player)
    slot = None
    for value in slots.values():
        if isinstance(value, dict) and int(value.get("MinisterId") or 0) == int(counsellor_id):
            slot = value
            break
    if slot is None:
        return None
    cd = int(v2configs.env_value("StudyCd", 10800) or 10800)
    if _now() - int(slot.get("StudyTime") or 0) < cd:
        return None
    counsellor = None
    for entry in player.data.get("RoleCounsellor", []):
        if isinstance(entry, dict) and int(entry.get("CounsellorId") or 0) == int(counsellor_id):
            counsellor = entry
            break
    if counsellor is None:
        return None
    book_gain = int(v2configs.env_value("StudyBookExp", 10) or 10)
    skill_gain = int(v2configs.env_value("StudySkillExp", 100) or 100)
    apt = int(counsellor.get("AptitudeExp") or 0) + book_gain
    counsellor["AptitudeExp"] = apt
    counsellor["SkillExp"] = int(counsellor.get("SkillExp") or 0) + skill_gain
    skill_up = _apply_skill_levels(counsellor)
    # the client's setSchoolInfo(counsellorId) removes the slot locally
    for key in [k for k, v in slots.items()
                if isinstance(v, dict) and int(v.get("MinisterId") or 0) == int(counsellor_id)]:
        del slots[key]
    return {
        "CounsellorId": int(counsellor_id),
        "BookExp": apt,                      # absolute AptitudeExp
        "SkillExp": int(counsellor.get("SkillExp") or 0),
        "SkillExpAdd": skill_gain,           # gain shown by 10903's callback
        "SkillLv": int(counsellor.get("SkillLv") or 1),
        "SkillUp": skill_up,
        "MiracleTrigger": 0,
    }


def action_school_complete(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_schoolMod action10902 {CounsellorId}: collect one finished study.

    The callback (modules/_schoolMod.lua lines 283-346) reads
      BookExp (absolute AptitudeExp), SkillExp, SkillLv, SkillUp,
      MiracleTrigger
    and then clears the counsellor's school slot locally."""
    try:
        counsellor_id = int(payload.get("CounsellorId") or 0)
    except (TypeError, ValueError):
        counsellor_id = 0
    record = _school_complete_one(player, counsellor_id) if counsellor_id else None
    if record is None:
        return {"_fail": "study session not finished"}
    store.save(player)
    return attach_remain_list({
        "BookExp": record["BookExp"],
        "SkillExp": record["SkillExp"],
        "SkillLv": record["SkillLv"],
        "SkillUp": record["SkillUp"],
        "MiracleTrigger": record["MiracleTrigger"],
    }, player)


def action_school_complete_all(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_schoolMod action10903 (empty request): collect every finished study.

    The callback (modules/_schoolMod.lua lines 348-470) pairs() over
    A0_3.CompleteCounsellors; each entry needs CounsellorId, BookExp,
    SkillExpAdd, SkillExp, SkillLv and MiracleTrigger."""
    slots = _school_slots(player)
    ids = sorted({int(v.get("MinisterId") or 0) for v in slots.values()
                  if isinstance(v, dict) and v.get("MinisterId")})
    records = []
    for counsellor_id in ids:
        record = _school_complete_one(player, counsellor_id)
        if record is not None:
            records.append(record)
    if not records:
        return {"_fail": "no finished study session"}
    store.save(player)
    return attach_remain_list({"CompleteCounsellors": records}, player)


def action_school_capacity(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_schoolMod action10904 (empty request): buy the next study slot.

    The price is the TcSchool row whose Capacity == current + 1
    (schoolScene.schoolScene.lua lines 1503-1517: costNum = the row with
    Capacity == getCapacityLimit() + 1 .NeedGold), paid from RoleData.Gold;
    the callback only reads A0_3.Capacity -> setCapacityLimit."""
    capacity = _school_capacity(player)
    row = None
    for entry in v2configs.school_rows():
        if int(entry.get("Capacity") or 0) == capacity + 1:
            row = entry
            break
    if row is None:
        return {"_fail": "study capacity maxed"}
    cost = int(row.get("NeedGold") or 0)
    role_data = player.data.setdefault("RoleData", {})
    gold = int(role_data.get("Gold") or 0)
    if gold < cost:
        return {"_fail": f"needs {cost} gold"}
    role_data["Gold"] = gold - cost
    player.data.setdefault("School", {})["Capacity"] = int(row.get("Capacity") or capacity + 1)
    store.save(player)
    return attach_remain_list({"Capacity": int(row.get("Capacity") or capacity + 1)}, player)


# --------------------------------------------------------------------------
# minister / counsellor upgrade (10101)
# --------------------------------------------------------------------------
def action_counsellor_upgrade(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_ministerMod action10101 {CounsellorId, IsUpTen}.

    The response callback needs exactly:
      CounsellorLv     -> setMinisterInfo(id, "Lv", ...)
      CounsellorSilver -> setMinisterInfo(id, "NowSilver", ...)
      MiracleTrigger   -> passed to the caller continuation
    (ministerScene.ministerInfo renders the exp bar as
    NowSilver / TcCounsellorUpgrade[Lv+1].NeedItem[2]).

    Server model: each upgrade step invests silver up to the level cost from
    the client's own cost table; when the invested silver covers the level the
    counsellor levels up and the invested silver is consumed.  Level/NowSilver
    persist in RoleCounsellor, the spent silver is deducted from RoleData.
    """
    try:
        counsellor_id = int(payload.get("CounsellorId") or 0)
    except (TypeError, ValueError):
        counsellor_id = 0
    up_ten = bool(payload.get("IsUpTen"))

    counsellor = None
    for entry in player.data.get("RoleCounsellor", []):
        if isinstance(entry, dict) and int(entry.get("CounsellorId") or 0) == counsellor_id:
            counsellor = entry
            break
    if counsellor is None:
        return {"CounsellorLv": 1, "CounsellorSilver": 0, "MiracleTrigger": 0}

    level = int(counsellor.get("Lv") or 1)
    invested = int(counsellor.get("NowSilver") or 0)
    role_data = player.data.setdefault("RoleData", {})
    silver = int(role_data.get("Silver") or 0)

    # Sultan Boost on single-level upgrades only (community: the 10x button
    # never triggers it): +2 free bonus levels on top of the normal one, so
    # "Vizier level up 3x" (client text) means Lv +3 total.  Bonus levels cost
    # no silver and are capped at the original config's max level.
    miracle = (not up_ten) and _miracle_roll()
    bonus_left = 2 if miracle else 0
    steps = 10 if up_ten else 1
    for _ in range(steps):
        need = _level_silver(level + 1)
        if need is None:
            break  # max level reached
        if silver <= 0:
            break
        take = min(need - invested, silver)
        if take > 0:
            invested += take
            silver -= take
        if invested >= need:
            invested -= need
            level += 1
            # Free bonus levels from the boost (no extra silver spent).
            while bonus_left > 0 and _level_silver(level + 1) is not None:
                level += 1
                bonus_left -= 1

    counsellor["Lv"] = level
    counsellor["NowSilver"] = invested
    role_data["Silver"] = silver
    store.save(player)
    # The 10101 callback only updates Lv/NowSilver for the counsellor; the
    # spent silver must reach the top bar through RemainList (ItemId 2).
    return attach_remain_list(
        {"CounsellorLv": level, "CounsellorSilver": invested,
         "MiracleTrigger": miracle},
        player,
    )


# --------------------------------------------------------------------------
# counsellor talent book (10102), peerage (10103) and skin (10106)
# --------------------------------------------------------------------------
def _find_counsellor(player: Player, counsellor_id: int) -> dict | None:
    for entry in player.data.get("RoleCounsellor", []):
        if isinstance(entry, dict) and int(entry.get("CounsellorId") or 0) == int(counsellor_id):
            return entry
    return None


def _peerage_row_index(counsellor: dict, rows: list) -> int | None:
    """_ministerMod.getIndexByMInfo (modules/_ministerMod.lua lines
    676-717) over the LvLimit-sorted TcPeeragesUpgrade rows: the row the
    counsellor currently sits on."""
    try:
        lv = int(counsellor.get("Lv") or 1)
        peerages = int(counsellor.get("Peerages") or 1)
    except (TypeError, ValueError):
        return None
    break_lv = counsellor.get("BreakLv")
    for index, row in enumerate(rows):
        if lv > int(row.get("LvLimit") or 0):
            continue
        if break_lv:
            if peerages <= int(row.get("Peerages") or 0) and int(break_lv) <= int(row.get("LvLimit") or 0):
                return index
        elif peerages == int(row.get("Peerages") or 0):
            return index
    return None


def _consume_item_list(player: Player, costs: list) -> tuple[bool, list]:
    """Check then deduct a TcPeeragesUpgrade ConsumeItem list
    ([[ItemId, Num], ...]).  Returns (ok, consumed ItemIds)."""
    checked = []
    for pair in costs:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        try:
            item_id, num = int(pair[0]), int(pair[1])
        except (TypeError, ValueError):
            return False, []
        if num <= 0:
            continue
        if _role_item_count(player, item_id) < num:
            return False, []
        checked.append((item_id, num))
    for item_id, num in checked:
        _role_item_consume(player, item_id, num)
    return True, [item_id for item_id, _ in checked]


def action_counsellor_talent(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_ministerMod.action10102 {CounsellorId, TalentId, UpgradeType}.

    UpgradeType is {1,3,2}[nType] (modules/_ministerMod.lua line 3147).  The
    book-upgrade dialog (ministerScene.ministerDialog.lua lines 2408-2905)
    defines the three verified cost paths for a talent of TcTalent row
    {StarLevel, TalentType}:
      UpgradeType 1: consume (TalentType + 500) x TcStar.ConsumeScroll,
                     success chance TcStar.Probability (the dialog shows it
                     only for this type; the other two force 100);
      UpgradeType 3: consume item 581 x TcStar.ConsumeScroll, always
                     succeeds (the dialog's dataSource hardcodes 581);
      UpgradeType 2: consume TcStar.ConsumeExp from the counsellor's
                     AptitudeExp, always succeeds, and the response must
                     carry the new AptitudeExp.
    Gate: TalentLv < TcPeeragesUpgrade[current row].TalentLvLimit, else the
    client prompts "BookLvCapped" and never sends the action.

    Response (callback lines 3156-3186): TalentLv (absolute, written into
    the matching Talent [id, lv] pair) plus AptitudeExp for UpgradeType 2.
    """
    try:
        counsellor_id = int(payload.get("CounsellorId") or 0)
        talent_id = int(payload.get("TalentId") or 0)
        upgrade_type = int(payload.get("UpgradeType") or 0)
    except (TypeError, ValueError):
        return {"_fail": "bad talent upgrade request"}
    counsellor = _find_counsellor(player, counsellor_id)
    if counsellor is None:
        return {"_fail": "counsellor not found"}
    talent_entry = None
    for entry in counsellor.get("Talent") or []:
        if isinstance(entry, list) and len(entry) >= 2 and int(entry[0] or 0) == talent_id:
            talent_entry = entry
            break
    if talent_entry is None:
        return {"_fail": "talent not owned"}
    level = int(talent_entry[1] or 1)
    tc_talent = v2configs.talent_by_id(talent_id)
    if not tc_talent:
        return {"_fail": f"unknown talent {talent_id}"}
    star = v2configs.star_by_level(int(tc_talent.get("StarLevel") or 1))
    if not star:
        return {"_fail": "no TcStar row for this talent"}
    rows = v2configs.peerage_rows()
    row_index = _peerage_row_index(counsellor, rows)
    row = rows[row_index] if row_index is not None else None
    limit = int(row.get("TalentLvLimit") or 0) if row else 0
    if limit and level >= limit:
        return {"_fail": "talent level capped by peerage"}

    consumed_items: list = []
    if upgrade_type == 2:
        need = int(star.get("ConsumeExp") or 0)
        apt = int(counsellor.get("AptitudeExp") or 0)
        if apt < need:
            return {"_fail": "not enough book exp"}
        counsellor["AptitudeExp"] = apt - need
        new_level = level + 1
    elif upgrade_type in (1, 3):
        item_id = int(tc_talent.get("TalentType") or 1) + 500 if upgrade_type == 1 else 581
        need = int(star.get("ConsumeScroll") or 1)
        if _role_item_count(player, item_id) < need:
            return {"_fail": f"needs item {item_id} x{need}"}
        ok = _roll(int(star.get("Probability") or 0)) if upgrade_type == 1 else True
        _role_item_consume(player, item_id, need)
        consumed_items = [item_id]
        new_level = level + 1 if ok else level
    else:
        return {"_fail": "unsupported upgrade type"}
    talent_entry[1] = new_level
    store.save(player)
    data = {"TalentLv": new_level}
    if upgrade_type == 2:
        data["AptitudeExp"] = int(counsellor.get("AptitudeExp") or 0)
    return attach_remain_list(data, player, items=consumed_items)


def action_counsellor_peerage(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_ministerMod.action10103 {CounsellorId} (peerage ladder step).

    The peerage dialog (ministerScene.ministerDialog.lua lines 941-1356)
    shows the NEXT TcPeeragesUpgrade row (get_peerages(minister, 2), i.e.
    getIndexByMInfo(current) + 1) and its ConsumeItem as the price; the
    callback (modules/_ministerMod.lua lines 3211-3253) writes
      Peerages (and BreakLv when present)
    and adds get_peerages(minister, 1).Award[2] (book exp) to AptitudeExp
    itself, so the server applies the same award to stay in sync.

    Rows that keep the same Peerages value are the "break" steps (LvLimit
    +5, ConsumeItem [[583, n]]); for those the response carries
    BreakLv = the new row's LvLimit, the only value that keeps
    getIndexByMInfo on the new row afterwards.  That reading of BreakLv is
    an inference (see the report); rows 2-7 are fully verified.
    """
    try:
        counsellor_id = int(payload.get("CounsellorId") or 0)
    except (TypeError, ValueError):
        counsellor_id = 0
    counsellor = _find_counsellor(player, counsellor_id)
    if counsellor is None:
        return {"_fail": "counsellor not found"}
    rows = v2configs.peerage_rows()
    row_index = _peerage_row_index(counsellor, rows)
    if row_index is None:
        return {"_fail": "no peerage row for this counsellor"}
    if row_index + 1 >= len(rows):
        return {"_fail": "peerage maxed"}
    nxt = rows[row_index + 1]
    ok, consumed = _consume_item_list(player, nxt.get("ConsumeItem") or [])
    if not ok:
        return {"_fail": "missing peerage items"}
    old_peerages = int(counsellor.get("Peerages") or 1)
    new_peerages = int(nxt.get("Peerages") or old_peerages + 1)
    counsellor["Peerages"] = new_peerages
    award = nxt.get("Award") or []
    if isinstance(award, list) and len(award) >= 2:
        counsellor["AptitudeExp"] = int(counsellor.get("AptitudeExp") or 0) + int(award[1] or 0)
    data = {"Peerages": new_peerages}
    if new_peerages == old_peerages:  # break step: peerage unchanged
        data["BreakLv"] = int(nxt.get("LvLimit") or 0)
    store.save(player)
    return attach_remain_list(data, player, items=consumed)


def action_counsellor_skin(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_ministerMod.action10106 {CounsellorId, SkinId} (dress a skin).

    The callback (modules/_ministerMod.lua lines 3362-3388) reads nothing
    from the response: it writes setMinisterInfo(id, "DressSkin",
    {SkinId, SkinLv}) from the request's own data.  The server persists the
    same pair, taking SkinLv from the counsellor's HaveSkinId record (the
    unlock lives there; TcCounsellorSkin maps CounsellorId -> skin ItemId).
    Dressing a skin the counsellor does not own is refused."""
    try:
        counsellor_id = int(payload.get("CounsellorId") or 0)
        skin_id = int(payload.get("SkinId") or 0)
    except (TypeError, ValueError):
        return {"_fail": "bad skin request"}
    counsellor = _find_counsellor(player, counsellor_id)
    if counsellor is None:
        return {"_fail": "counsellor not found"}
    if not skin_id:
        return {"_fail": "missing SkinId"}
    skin_lv = 0
    owned = False
    for entry in counsellor.get("HaveSkinId") or []:
        if isinstance(entry, dict) and int(entry.get("SkinId") or 0) == skin_id:
            owned = True
            skin_lv = int(entry.get("SkinLv") or 0)
            break
    if not owned and skin_id != counsellor_id:
        return {"_fail": "skin not owned"}
    counsellor["DressSkin"] = {"SkinId": skin_id, "SkinLv": skin_lv}
    store.save(player)
    return {}


# --------------------------------------------------------------------------
# instance (boss) actions: 11801 info, 11808 rank, 11810 credit store
# --------------------------------------------------------------------------
def _boss_utc(player: Player, key: str) -> list:
    value = player.data.get(key)
    if isinstance(value, list) and len(value) >= 2:
        return [int(value[0] or 0), int(value[1] or 0)]
    return [0, 0]


# Frontier event windows.  The official v7 capture carries the CURRENT game
# day's windows in both the 10000 snapshot and the 11801 response
# (v7_actions.jsonl 156/210/222):
#   BossSingleUTC [1794819600, 1794826800] = 2026-11-16 12:00-14:00 GMT+3
#   BossGlobalUTC [1794848400, 1794852000] = 2026-11-16 20:00-21:00 GMT+3
# The v2 client gates the Frontier buttons on now ∈ [start,end) (instanceScene
# 33-46/96-112) and its mod-86400 daily wrap assumes the same wall clock is
# re-issued each day.  The client's own rule text says "12:00-14:00 (GMT +3)"
# and "20:00-21:00 (GMT +3)" (Tcen.lua Rule_054/055), matching TimeZone:3.
_BOSS_WINDOWS = {
    "BossSingleUTC": (12, 0, 14, 0),   # Hunting Grounds
    "BossGlobalUTC": (20, 0, 21, 0),   # Fortress Siege
}


def _boss_window(key: str, ts: int | None = None) -> list:
    """Epoch window [start, end] for a Frontier event on the UTC+3 game day
    containing ``ts`` (default now), in the oracle's absolute-epoch format."""
    sh, sm, eh, em = _BOSS_WINDOWS[key]
    day = _game_time(ts)
    start = day.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = day.replace(hour=eh, minute=em, second=0, microsecond=0)
    return [int(start.timestamp()), int(end.timestamp())]


def _refresh_boss_windows(player: Player) -> None:
    for key in _BOSS_WINDOWS:
        player.data[key] = _boss_window(key)


def _boss_credits(player: Player) -> int:
    role_data = player.data.get("RoleData") or {}
    return int(role_data.get("BossCredits") or 0)


def action_instance_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_instanceMod netGetInstanceInfo (action11801 {updataTesting:1}).

    The callback stores exactly five slices:
      BossSingleUTC/BossGlobalUTC -> getSortTime() indexes [1]/[2] (arrays!)
      bossSingleComplete          -> truthiness gate in instanceSignalBattleDialog
      damageRankAward             -> {RemainList, RoleRank}; RemainList is #-counted
      bossGlobalDead              -> consumers do `if it then it.KillerName`,
                                     so it must be a table or absent, never 0
    """
    # Re-issue today's window on every page entry, exactly like the official
    # capture (taken after the window but still carrying that day's times).
    _refresh_boss_windows(player)
    store.save(player)
    return {
        "BossSingleUTC": _boss_window("BossSingleUTC"),
        "BossGlobalUTC": _boss_window("BossGlobalUTC"),
        "BossSingleComplete": _boss_single_complete(player),
        "DamageRankAward": {"RemainList": [], "RoleRank": 0},
        "BossGlobalDead": {},
    }


def action_instance_rank(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_instanceMod netRankData (action11808): the callback reads
    ScoreRank, KillRank (pairs over entries {KillTime, Name, RId}),
    RoleScore -> MyData.Score and RoleScoreRank -> MyData.Ranking.
    Empty rank arrays are rendered with the dialog's emptyLabels."""
    return {
        "ScoreRank": [],
        "KillRank": [],
        "RoleScore": _boss_credits(player),
        "RoleScoreRank": 0,
    }


def action_instance_store(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_instanceMod netStroeList (action11810, "score claim" button in
    instanceScene): the response is handed verbatim to
    instanceCreditsDialog.newShop, which needs
      RoleScore -> shop score display
      ItemList  -> initStoreData indexes A0_2[1] (safe when empty)
    The shop items themselves come from the client's own credit config."""
    return {"RoleScore": _boss_credits(player), "ItemList": []}


# --- Hunting Grounds fight state (11802 info / 11803 attack) ----------------
# Wave table: original configs/settings.TcBossSingle.lua (20 waves, Stage 1
# HP 20010 "wildDuck", Stage 2 HP 450310 "rabbit").  The v7 oracle confirms
# both on the wire (v7_actions.jsonl 214/220), so no HP value is invented.
def _boss_single_rows() -> list:
    rows = v2configs.load("TcBossSingle")
    if isinstance(rows, dict):
        rows = list(rows.values())
    return [r for r in rows if isinstance(r, dict) and int(r.get("Stage") or 0) > 0]


def _boss_single_wave(stage: int) -> dict:
    return next((r for r in _boss_single_rows()
                 if int(r.get("Stage") or 0) == int(stage)), {})


def _boss_single_max_stage() -> int:
    return max((int(r.get("Stage") or 0) for r in _boss_single_rows()), default=0)


def _boss_single_kill_award(stage: int) -> dict:
    """Guaranteed hunting currencies for a wave kill (TcBossSingle.AwardItem).

    Each wave leads with weight-100 drops for item 9 (BossCredits) and
    10 (BossScore); stage 1 = +3/+3, exactly the v7 oracle's captured kill.
    Sub-100 chance groups are not granted (no sourcing for the roll).
    """
    award: dict[int, int] = {}
    for group in _boss_single_wave(stage).get("AwardItem") or []:
        if not isinstance(group, list):
            continue
        for item in group:
            if (isinstance(item, (list, tuple)) and len(item) >= 3
                    and int(item[2] or 0) == 100 and int(item[0] or 0) in (9, 10)):
                iid = int(item[0])
                award[iid] = award.get(iid, 0) + int(item[1] or 0)
    return award


_BOSS_SINGLE_STATE_KEY = "BossSingleFight"


def _boss_single_state(player: Player) -> dict:
    """Per-UTC+3-game-day wave state: {Day, BossId, BossHP, Attacks}.

    A new game day restarts at wave 1 / full HP / empty allotment.  Attacks
    persisting across waves within a day is oracle-backed (v7_actions.jsonl
    220 kept CounsellorAttackData after the wave 1 -> 2 rotation).
    """
    day = _game_time().strftime("%Y-%m-%d")
    state = player.data.get(_BOSS_SINGLE_STATE_KEY)
    if not isinstance(state, dict) or state.get("Day") != day:
        state = {
            "Day": day,
            "BossId": 1,
            "BossHP": int(_boss_single_wave(1).get("HP") or 0),
            "Attacks": {},
        }
        player.data[_BOSS_SINGLE_STATE_KEY] = state
    return state


def _boss_single_complete(player: Player) -> bool:
    """Return a REAL boolean: the client does `if data.bossSingleComplete then`
    and Lua treats 0 as truthy, which showed 'congratulations, completed'
    on the very first entry.  The v7 oracle sends JSON false here."""
    state = player.data.get(_BOSS_SINGLE_STATE_KEY)
    if not isinstance(state, dict):
        return False
    if state.get("Day") != _game_time().strftime("%Y-%m-%d"):
        return False
    # Do NOT use `state.get("BossId") or -1`: 0 is falsy in Python.
    raw = state.get("BossId")
    if raw is None:
        return False
    return int(raw) == 0


def action_instance_boss_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_instanceMod netBossInfomation (11802): the client reads BossId/BossHP/
    BossHPMax (set_cur_enemy) and CounsellorAttackData (truthiness gate), and
    draws the animal from its own TcBossSingle by BossId.  With no handler the
    generic fallback had none of these fields -> no animal appeared.
    """
    if not _boss_single_wave(1):
        return {"_fail": "TcBossSingle config missing"}
    state = _boss_single_state(player)
    boss_id = int(state.get("BossId") or 0)
    complete = boss_id == 0
    wave = _boss_single_wave(boss_id) if not complete else {}
    store.save(player)
    return {
        "BossId": boss_id,
        "BossHP": 0 if complete else int(state.get("BossHP") or 0),
        "BossHPMax": 0 if complete else int(wave.get("HP") or 0),
        "CounsellorAttackData": dict(state.get("Attacks") or {}),
        "RecoverCountList": [], "BoxRewardList": [],
        # Lua truthiness: 0 is truthy, so a real boolean is required here
        # (the v7 oracle sends false).  Otherwise the dialog opens straight
        # into the "congratulations, completed today's hunting" screen.
        "BossSingleComplete": bool(complete),
    }


def action_instance_boss_attack(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_instanceMod netAttack (11803 {CounsellorId, IsHeadShot}).  Per-attack
    damage is not recoverable from any config; the single captured official
    attack killed a full-HP wave, so we deal exactly the wave's remaining HP
    (one attack per wave) with every number from TcBossSingle.
    """
    if not _boss_single_wave(1):
        return {"_fail": "TcBossSingle config missing"}
    try:
        counsellor_id = int(payload.get("CounsellorId") or 0)
    except (TypeError, ValueError):
        counsellor_id = 0
    unlocked = {int(c.get("CounsellorId") or 0)
                for c in (player.data.get("RoleCounsellor") or [])
                if isinstance(c, dict) and not int(c.get("IsLock") or 0)}
    state = _boss_single_state(player)
    attacks = state.setdefault("Attacks", {})
    if (counsellor_id not in unlocked or str(counsellor_id) in attacks
            or int(state.get("BossId") or 0) == 0):
        return {"BossStasus": 1}
    hp = int(state.get("BossHP") or 0)
    damage = float(hp)
    if payload.get("IsHeadShot"):
        try:
            damage *= float(v2configs.env_value("BossSingleHeadShotDamage", 1) or 1)
        except (TypeError, ValueError):
            pass
    hp = max(0, hp - int(math.floor(damage)))
    attacks[str(counsellor_id)] = 1
    role_data = player.data.setdefault("RoleData", {})
    if hp > 0:
        state["BossHP"] = hp
    else:
        killed = int(state.get("BossId") or 0)
        nxt = killed + 1
        row = _boss_single_wave(nxt)
        if row and nxt <= _boss_single_max_stage():
            state["BossId"], state["BossHP"] = nxt, int(row.get("HP") or 0)
        else:
            state["BossId"], state["BossHP"] = 0, 0
        # Kill reward: the wave's guaranteed AwardItem currencies.
        for iid, num in _boss_single_kill_award(killed).items():
            key = "BossScore" if iid == 10 else "BossCredits"
            role_data[key] = int(role_data.get(key) or 0) + num
    complete = int(state.get("BossId") or 0) == 0
    wave = _boss_single_wave(state.get("BossId") or 0) if not complete else {}
    store.save(player)
    data = {
        "BossId": int(state.get("BossId") or 0),
        "BossHP": int(state.get("BossHP") or 0),
        "BossHPMax": int(wave.get("HP") or 0),
        "CounsellorAttackData": dict(attacks),
        # Same Lua-truthiness rule as BossSingleComplete (oracle sends false).
        "IsComplete": bool(complete),
        "IsBoxAward": False,
        "RecoverCountList": [{"MinisterId": counsellor_id, "RecoverCount": 0}],
        # v7 oracle trio, exact order (v7_actions.jsonl 220).  The client
        # derives +N from the absolute Num itself; any OTHER row would be
        # appended to the kill popup's icon list and could nil-crash its
        # table.sort, so do NOT use attach_remain_list here.
        "RemainList": [
            {"ItemId": 10, "Num": int(role_data.get("BossScore") or 0)},
            {"ItemId": 2, "Num": int(role_data.get("Silver") or 0)},
            {"ItemId": 9, "Num": int(role_data.get("BossCredits") or 0)},
        ],
    }
    return data


# --------------------------------------------------------------------------
# wives call/favor action (10401)
# --------------------------------------------------------------------------
def action_wives_call(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_wivesMod action10401 {cTime}: the "call wife" / favor button in
    wivesScene.  The callback needs exactly:
      BeautyId      -> wives module state key (must exist in TcBeauty config)
      BeautyExp     -> new favor total; the scene shows + (BeautyExp - current)
      Children      -> optional; when present children logic runs (we omit it)
      VigorTimeUnix -> roleMod.setRoleData("VigorTimeUnix", ...)
      MiracleTrigger
    The request carries no beauty id, so favor accrues on the first config
    wife; counters are kept server-side in WivesFavor (persisted, ignored by
    the client at login) and vigor regen is stamped in RoleData."""
    now = _now()
    beauty_id = 1
    favor = player.data.setdefault("WivesFavor", {})
    exp = int(favor.get(str(beauty_id)) or 0) + 20
    favor[str(beauty_id)] = exp
    player.data.setdefault("RoleData", {})["VigorTimeUnix"] = now
    _bump_achievement(player, "HaremVisits")  # achievement 15, real visit event
    store.save(player)
    return {"BeautyId": beauty_id, "BeautyExp": exp, "VigorTimeUnix": now,
            "MiracleTrigger": 0}


# --------------------------------------------------------------------------
# government / imperial affairs (10701, 10702)
# --------------------------------------------------------------------------
def _gov_rows() -> list[dict]:
    """TcGovEvent rows from the v2 client config (12 events, ids 1..12)."""
    rows = v2configs.load("TcGovEvent")
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        return []
    return [row for row in rows
            if isinstance(row, dict) and int(row.get("Id") or 0) > 0]


def _gov_event_ids() -> list[int]:
    return sorted(int(row["Id"]) for row in _gov_rows())


def _gov_cooldown() -> int:
    """GovEventCd from the client's ConfigEnvSet.lua 854-856 (= 1800 s)."""
    return int(v2configs.env_value("GovEventCd", 1800) or 1800)


def _gov_max_events(player: Player) -> int:
    """TcLvUpgradeCfg[Lv].GovEventTimes (3 at Lv 1) - the client's
    _governmentMod.get_max_event (modules/_governmentMod.lua 12-31)."""
    level = int((player.data.get("RoleData") or {}).get("Lv") or 1)
    for row in v2configs.load("TcLvUpgradeCfg"):
        if isinstance(row, dict) and int(row.get("Level") or 0) == level:
            return int(row.get("GovEventTimes") or 0)
    return 0


def _gov_state(player: Player) -> tuple[dict, dict]:
    """Lazily initialise the persisted government state.

    RoleData.GovEvenId / GovTimeUTC are the exact login keys the v7 capture
    carries (session log 32735: GovEvenId 8, GovTimeUTC 1794821694); the
    completed count lives outside RoleData so the client never sees it.
    """
    role_data = player.data.setdefault("RoleData", {})
    ids = _gov_event_ids()
    if ids and not role_data.get("GovEvenId"):
        role_data["GovEvenId"] = ids[0]  # first TcGovEvent id (config-sourced)
    role_data.setdefault("GovTimeUTC", _now())
    # v7 login carries GovEventTime as a "YYYY-MM-DD HH:MM:SS" string.
    if not role_data.get("GovEventTime"):
        role_data["GovEventTime"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(_now()))
    # Config-backed values from TcLvUpgradeCfg[Level].  These MUST track the
    # current Sultan level: GovAchievement is the EXP granted by the "No"
    # affair button, GovAchievementRate scales the "Yes" resource reward, and
    # GovEventTimes is the daily affair capacity.  A plain setdefault froze
    # them at the level-1 values (1 / 0.1 / 3) forever, so levelling up never
    # increased the No reward.  Recompute every time so legacy accounts heal
    # and a level-up takes effect immediately.
    level = int(role_data.get("Lv") or 1)
    for row in v2configs.load("TcLvUpgradeCfg"):
        if isinstance(row, dict) and int(row.get("Level") or 0) == level:
            for key in ("GovEventTimes", "GovAchievement", "GovAchievementRate"):
                if row.get(key) is not None and role_data.get(key) != row[key]:
                    role_data[key] = row[key]
            break
    gov = player.data.setdefault("GovAffairs", {})
    gov.setdefault("Completed", 0)
    gov.setdefault("OverTime", 0)
    gov.setdefault("AchievementClaimed", False)
    return role_data, gov


def _gov_unlocked_counsellors(player: Player) -> list[dict]:
    """_ministerMod.get_minister_UnLock (modules/_ministerMod.lua 1141-1194):
    the counsellors whose IsLock == 0."""
    return [row for row in (player.data.get("RoleCounsellor") or [])
            if isinstance(row, dict) and int(row.get("IsLock") or 0) == 0]


def _gov_counsellor_aptitude(counsellor: dict) -> list[int]:
    """get_treasure_aptitude (modules/_ministerMod.lua 1402-1464).

    For a counsellor's [[talentId, level], ...] list, each talent contributes
    `level * TcStar[TcTalent.StarLevel].Effect` to the slot given by its
    TalentType (1..4).  No rounding here: the client sums these raw aptitudes.
    """
    apt = [0, 0, 0, 0]
    for entry in counsellor.get("Talent") or []:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        try:
            talent_id, level = int(entry[0] or 0), int(entry[1] or 0)
        except (TypeError, ValueError):
            continue
        row = v2configs.talent_by_id(talent_id)
        talent_type = int(row.get("TalentType") or 0)
        star_level = int(row.get("StarLevel") or 0)
        if not 1 <= talent_type <= 4:
            continue
        effect = int(v2configs.star_by_level(star_level).get("Effect") or 0)
        apt[talent_type - 1] += level * effect
    return apt


def _gov_role_power_stats(player: Player) -> list[int]:
    """roleMod.getRolePower(1..4) - the four vizier power stats.

    Faithful port of roleMod.setRolePower (modules/roleMod.lua 288-334) ->
    _ministerMod.get_all_attribute (1613-1655) -> get_minister_attribute
    (1586-1608) -> AttributeValue's calc() (_ministerMod.lua 122-288):

        AttributeValue[i] = floor(((Lv-1)*0.1*(Lv+2) + 10) * aptitude[i])

    Wives/PillAdd/HaloAdd are all zero on this dev server and the starter
    viziers are CounsellorType 1 (so the halo loop is skipped); there are no
    children, so the remaining terms of calc() and get_children_attribute()
    contribute 0.  Both TalentType and the power index use the same 1..4
    slots; the client's language file lists them in the order Military,
    Research, Politics, Prestige (settings.Clienten.lua ~6693).

    With the devtest viziers (Lv 1/14/3) this yields [215, 105, 72, 103],
    matching the four stats displayed by the client on-device.
    """
    stats = [0, 0, 0, 0]
    for counsellor in _gov_unlocked_counsellors(player):
        apt = _gov_counsellor_aptitude(counsellor)
        lv = int(counsellor.get("Lv") or 1)
        factor = (lv - 1) * 0.1 * (lv + 2) + 10
        for i in range(4):
            stats[i] += int(math.floor(factor * apt[i]))
    return stats


def _gov_role_power(player: Player, index: int) -> int:
    """roleMod.getRolePower(index) for index 2/3/4 (the 10701 yes-button
    resource types).  The client uses getRolePower(ItemId) directly; in the
    client's attribute order (Military, Research, Politics, Prestige) the
    power slots 2/3/4 map to Research / Politics / Prestige."""
    stats = _gov_role_power_stats(player)
    return stats[index - 1] if 1 <= index <= 4 else 0


def _gov_response(player: Player, now: int | None = None,
                  first_item_id: int | None = None,
                  items: list | None = None,
                  miracle: bool = False) -> dict:
    """Exact fields read by _governmentMod.action10702/10701 callbacks
    (modules/_governmentMod.lua 33-101) and governmentScene's updateEvent
    (scene 1842-1913: RemainList[1].ItemId, MiracleTrigger)."""
    role_data, gov = _gov_state(player)
    now = _now() if now is None else now
    anchor = int(role_data.get("GovTimeUTC") or now)
    data = {
        "EventId": int(role_data.get("GovEvenId") or 0),
        "GovTimeUTC": anchor,
        # OverTime in the official capture is now - GovTimeUTC
        # (1794821694-1794818094=3600, 1794821696-1794819894=1802, ...).
        "OverTime": max(0, now - anchor),
        # v7 ships this boolean; the v2 scene does not read it, so keep it
        # neutral unless a real achievement has been recorded.
        "Achievement": bool(gov.get("AchievementClaimed")),
        "MiracleTrigger": miracle,
    }
    # Non-empty RemainList is mandatory: updateEvent indexes RemainList[1].
    # All values are absolute post-grant totals; first_item_id puts the
    # just-granted item at [1] so the floating text shows the right icon.
    # `items` carries bag item ids (e.g. 205/210/215 talent books) so their
    # absolute counts reach setCurrRewardData and the reward animation.
    return attach_remain_list(data, player, first_item_id=first_item_id, items=items)


def action_government_event(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_governmentMod.action10701 (modules/_governmentMod.lua 69-103).

    Request {OptionNum: 1|2} (governmentScene 1170-1198 / 1407-1435).  The
    callback stores EventId + GovTimeUTC, then the scene's updateEvent
    (governmentScene 1842+) consumes RemainList[1].ItemId / MiracleTrigger.

    The exact reward shown on the buttons is computed here from the v2
    configs (never hardcoded):

      yes (OptionNum 1), for the *current* TcGovEvent row's ItemId:
        ItemId 2/3/4 -> floor(AffairResourceBase
                              + GovAchievementRate * getRolePower(ItemId))
        otherwise    -> 1
      no (OptionNum 2): GovAchievement EXP (icon/5.png)

    Each processed affair consumes exactly one config cooldown: the v7
    capture moves GovTimeUTC 1794818094 -> 1794819894 -> 1794821694, each
    step == GovEventCd 1800.  The next event id follows TcGovEvent order.
    """
    role_data, gov = _gov_state(player)
    now = _now()

    # The reward belongs to the event the player is currently looking at,
    # so resolve the row BEFORE advancing GovEvenId.
    current_id = int(role_data.get("GovEvenId") or 0)
    row = next((r for r in _gov_rows() if int(r.get("Id") or 0) == current_id), {})

    option = int(payload.get("OptionNum") or 0)
    # Sultan Boost: client text gives 10x for gold/grain/soldiers and 3x for
    # Sultan EXP / attribute books (ConfigEnvSet odds = 15%).
    miracle = _miracle_roll()
    granted_item_id: int | None = None
    granted_amount = 0
    bag_items: list[int] = []
    if option == 2:
        # No button: +GovAchievement EXP (governmentScene 1341, icon 5.png).
        achievement = role_data.get("GovAchievement")
        granted_item_id = 5
        try:
            granted_amount = int(float(achievement or 0))
        except (TypeError, ValueError):
            granted_amount = 0
        if miracle:
            granted_amount *= 3  # "Player XP 3x"
        role_data["Exp"] = int(role_data.get("Exp") or 0) + granted_amount
    elif option == 1:
        item_id = int(row.get("ItemId") or 0)
        granted_item_id = item_id
        if item_id in (2, 3, 4):
            # governmentScene 1038-1077: base + rate * getRolePower(ItemId),
            # then math.floor before the abbreviation/reward text.
            try:
                base = float(v2configs.env_value("AffairResourceBase", 5000) or 5000)
            except (TypeError, ValueError):
                base = 5000.0
            try:
                rate = float(role_data.get("GovAchievementRate") or 0)
            except (TypeError, ValueError):
                rate = 0.0
            granted_amount = int(math.floor(base + rate * _gov_role_power(player, item_id)))
            if miracle:
                granted_amount *= 10  # "Gold/Grain/Soldier 10x"
            key = _REMAIN_LIST_MAP[item_id]
            role_data[key] = int(role_data.get(key) or 0) + granted_amount
        else:
            # governmentScene 1077-1079: any other ItemId grants exactly 1
            # of that item (the special events carry bag ItemIds 205/210/215).
            granted_amount = 3 if miracle else 1  # "Attribute Book/Tome 3x"
            if item_id >= 100:
                _role_item_add(player, item_id, granted_amount)
                bag_items.append(item_id)
            elif item_id in _REMAIN_LIST_MAP:
                key = _REMAIN_LIST_MAP[item_id]
                role_data[key] = int(role_data.get(key) or 0) + granted_amount

    anchor = int(role_data.get("GovTimeUTC") or now)
    # v7: each processed affair consumes ONE accumulated cooldown tick.  The
    # anchor is the time of the last processed affair, so it may sit in the
    # past by up to GovEventTimes*cd (that backlog is the available count),
    # but it must never move into the future.  Normalise ancient legacy
    # anchors to the maximum backlog first, otherwise an anchor hours old
    # would stay "3/3" forever (the client caps the count at GovEventTimes).
    cd = _gov_cooldown()
    anchor = max(anchor, now - _gov_max_events(player) * cd)
    role_data["GovTimeUTC"] = min(now, anchor + cd)

    ids = _gov_event_ids()
    if ids:
        current = int(role_data.get("GovEvenId") or ids[0])
        index = ids.index(current) if current in ids else -1
        role_data["GovEvenId"] = ids[(index + 1) % len(ids)]
    # Same date-string format the v7 login carries (GovEventTime).
    role_data["GovEventTime"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now))
    gov["Completed"] = int(gov.get("Completed") or 0) + 1
    gov["OverTime"] = max(0, now - int(role_data["GovTimeUTC"]))
    # A real government affair just completed: feed achievements + daily task 5.
    _bump_achievement(player, "GovernmentAffairs")
    _bump_daily(player, "Government")
    store.save(player)
    return _gov_response(player, now,
                         first_item_id=granted_item_id if granted_amount else None,
                         items=bag_items or None, miracle=miracle)


def action_government_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_governmentMod.action10702 (modules/_governmentMod.lua 33-67).

    Sent with {RestoreNum} when the player uses the restore item 509
    (governmentScene 584-660).  The item id comes from the client; the number
    of affairs restored is the client-provided RestoreNum and the time step
    is the config GovEventCd, so nothing is invented.  With no item 509 in
    the bag the request is a pure state read (neutral, no mutation).
    The callback stores OverTime + GovTimeUTC; updateEvent then reads the
    same RemainList[1]/MiracleTrigger fields as 10701.
    """
    role_data, gov = _gov_state(player)
    restore = max(0, int(payload.get("RestoreNum") or 0))
    have = _role_item_count(player, 509)
    use = min(restore, have, _gov_max_events(player))
    if use > 0:
        _role_item_consume(player, 509, use)
        anchor = int(role_data.get("GovTimeUTC") or _now())
        role_data["GovTimeUTC"] = min(_now(), anchor - use * _gov_cooldown())
        store.save(player)
    gov["OverTime"] = max(0, _now() - int(role_data.get("GovTimeUTC") or _now()))
    return _gov_response(player)


# --------------------------------------------------------------------------
# binding actions: 16003 bind state, 16004 bound account list
# --------------------------------------------------------------------------
def action_binding_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_bindingMod action16003: setData("IsBinding", A0_3.IsBinding); the
    bind-popup hints read the stored value (0 = not bound, 1 = bound,
    2 = hint done)."""
    return {"IsBinding": int(player.data.get("IsBinding") or 0)}


def action_binding_accounts(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_bindingMod action16004: setData("accountList", A0_3.AccountList);
    no accounts are bound on this dev server."""
    return {"AccountList": []}


# --------------------------------------------------------------------------
# Find / Masquerade scene (_findMod, actions 10601-10606)
# --------------------------------------------------------------------------
def _player_vip_row(player: Player) -> dict:
    role_data = player.data.get("RoleData") if isinstance(player.data.get("RoleData"), dict) else {}
    role_base = player.data.get("RoleBase") if isinstance(player.data.get("RoleBase"), dict) else {}
    level = _as_int(role_data.get("VipLv") or role_base.get("VipLv") or 0)
    return v2configs.vip_row(level)


def _visit_regen(visit: dict, vip: dict, now: int) -> None:
    """Server-side time catch-up for Fate and Vitality ("as-of" stamps).

    The client regenerates these locally only while the Find scene is open
    (_findMod.lua 588-670 / 481-514) and gates the visit on
    fate >= VisitReduceFate (findScene.lua:1987, config value 2).  A stale
    persisted Fate=0 therefore permanently shows "insufficient fortune".
    Regen server-side at the original config rates:
      Fate     1 / FateCD(900)s,  cap CommonMaxFate(100)
      Vitality 1 / VitalityCD(1800)s, cap TcVIP[VipLv].MaxVitality
    Storage keeps epoch "as-of" stamps; _visit_client_slice converts them.
    """
    max_fate = _as_int(v2configs.env_value("CommonMaxFate") or 0)
    cd = _as_int(v2configs.env_value("FateCD") or 900) or 900
    fate = _as_int(visit.get("Fate") or 0)
    fate_time = _as_int(visit.get("FateTime") or 0)
    if max_fate > 0 and fate < max_fate and fate_time > 0:
        gained = (now - fate_time) // cd
        if gained > 0:
            fate = min(max_fate, fate + gained)
            fate_time += gained * cd  # keep the sub-CD remainder
            if fate >= max_fate:
                fate_time = now
            visit["Fate"], visit["FateTime"] = fate, fate_time

    max_vitality = _as_int(vip.get("MaxVitality") or 0)
    vcd = _as_int(v2configs.env_value("VitalityCD") or 1800) or 1800
    vitality = _as_int(visit.get("Vitality") or 0)
    vitality_time = _as_int(visit.get("VitalityTime") or 0)
    if max_vitality > 0 and vitality < max_vitality and vitality_time > 0:
        gained = (now - vitality_time) // vcd
        if gained > 0:
            vitality = min(max_vitality, vitality + gained)
            vitality_time += gained * vcd
            if vitality >= max_vitality:
                vitality_time = now
            visit["Vitality"], visit["VitalityTime"] = vitality, vitality_time


def _visit_data(player: Player) -> dict:
    """Persisted VisitData slice; the 10602 callback indexes it directly.

    _findMod.action10602 (modules/_findMod.lua 848-902) reads
    VisitData.{Vitality,Fate,FateTime,VitalityTime,TransferNum,PraiseGuide}
    with no nil guard.  A missing VisitData made the callback throw and the
    scene fall back with no data.  Every seeded value has a source: Vitality
    is TcVIP[VipLv].MaxVitality, the rest are neutral zeros / the current time.
    """
    visit = player.data.get("VisitData")
    if not isinstance(visit, dict):
        visit = {}
        player.data["VisitData"] = visit
    vip = _player_vip_row(player)
    max_vitality = _as_int(vip.get("MaxVitality") or 0)
    now = _now()
    visit.setdefault("Vitality", max_vitality)
    # Fresh rows start with a full Fortune meter, matching the Vitality seed
    # (TcVIP[VipLv].MaxVitality).  Value is CommonMaxFate from ConfigEnvSet.
    visit.setdefault("Fate", _as_int(v2configs.env_value("CommonMaxFate") or 0))
    visit.setdefault("FateTime", now)
    visit.setdefault("VitalityTime", now)
    visit.setdefault("TransferNum", 0)
    visit.setdefault("PraiseGuide", 0)
    _visit_regen(visit, vip, now)
    return visit


# --- Find / Masquerade encounter selection (10601) --------------------------
# The 10601 response is authoritative: _findMod._getEvent (78-113) only maps
# the server's EventType, and nothing client-side rolls TcVisitEvent/TcVisitFate.
# So the server picks the outcome from the original configs:
#   TcVisitFate: post-visit Fate selects the weighted [event id, weight] row.
#   TcVisitEvent 11 == ConfigEnvSet.NothingEvent -> EventType 0.
#   TcVisitEvent 1 (no People, no Award) -> EventType 1 (consort).
#   TcVisitEvent 2-10 (People pool) -> EventType 2, with optional resource award.
# Rewards ride the generic RemainList channel with ABSOLUTE values.
def _visit_nothing_event_id() -> int:
    return _as_int(v2configs.env_value("NothingEvent") or 0)


def _visit_event_row(event_id: int) -> dict | None:
    rows = v2configs.load("TcVisitEvent")
    if not isinstance(rows, list):
        return None
    return next((r for r in rows if isinstance(r, dict)
                 and _as_int(r.get("Id") or 0) == int(event_id)), None)


def _visit_event_people(row: dict) -> list:
    people = row.get("People")
    if not isinstance(people, list):
        return []
    return [_as_int(pid) for pid in people if isinstance(pid, (int, float))]


def _visit_fate_event(fate: int) -> int | None:
    rows = v2configs.load("TcVisitFate")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        region = row.get("Region")
        if not (isinstance(region, list) and len(region) >= 2):
            continue
        try:
            lo, hi = int(region[0]), int(region[1])
        except (TypeError, ValueError):
            continue
        if not (lo <= fate <= hi):
            continue
        pairs = []
        for event in row.get("Events") or []:
            if isinstance(event, (list, tuple)) and len(event) >= 2:
                try:
                    weight = int(event[1])
                except (TypeError, ValueError):
                    continue
                if weight > 0:
                    pairs.append((int(event[0]), weight))
        if not pairs:
            return None
        roll = random.random() * sum(w for _, w in pairs)
        acc = 0
        for event_id, weight in pairs:
            acc += weight
            if roll < acc:
                return event_id
        return pairs[-1][0]
    return None


def _visit_building_beauty_ids() -> set:
    rows = v2configs.load("TcVisitBuilding")
    out = set()
    if isinstance(rows, list):
        for row in rows:
            people = row.get("People") if isinstance(row, dict) else None
            if not isinstance(people, list):
                continue
            for pair in people:
                if isinstance(pair, list) and len(pair) == 2 and _as_int(pair[0]) == 1:
                    out.add(_as_int(pair[1]))
    return out


def _visit_owned_beauty_rows(player: Player) -> list:
    wives = player.data.get("RoleBeauty")
    rows: list = []
    if isinstance(wives, dict):
        for key, row in wives.items():
            bid = row.get("BeautyId") if isinstance(row, dict) else None
            if bid is None:
                try:
                    bid = int(key)
                except (TypeError, ValueError):
                    continue
            rows.append((int(bid), row if isinstance(row, dict) else {}))
    elif isinstance(wives, list):
        for row in wives:
            if isinstance(row, dict) and row.get("BeautyId") is not None:
                rows.append((int(row["BeautyId"]), row))
    return rows


def _visit_consort_entry(player: Player) -> dict | None:
    """RemainList[1] for a consort encounter: +1 Intimacy on an owned wife.

    ItemId 1101 -> wife's Intimacy (itemMod.lua 22); Num is the absolute new
    value.  Returns None when no eligible consort is owned, so the caller
    keeps the nothing event rather than inventing a wife.
    """
    known = _visit_building_beauty_ids()
    owned = [(bid, row) for bid, row in _visit_owned_beauty_rows(player)
             if bid in known]
    if not owned:
        return None
    beauty_id, row = random.choice(owned)
    current = row.get("Intimacy")
    new_intimacy = (int(current) if isinstance(current, (int, float)) else 0) + 1
    row["Intimacy"] = new_intimacy
    return {"ItemId": 1101, "Num": new_intimacy, "BeautyId": beauty_id}


def _visit_people_result(player: Player, visit: dict, row: dict) -> dict | None:
    """EventType 2 payload + optional resource award (absolute RemainList)."""
    ids = _visit_event_people(row)
    if not ids:
        return None
    people_id = random.choice(ids)
    met = visit.setdefault("MetPeople", {})
    key = str(people_id)
    is_first = 0 if not _as_int(met.get(key) or 0) else 1
    met[key] = _as_int(met.get(key) or 0) + 1
    award_id = _as_int(row.get("Award") or 0)
    ratio = row.get("AwardRatio")
    if award_id in (2, 3, 4) and isinstance(ratio, list) and len(ratio) >= 2:
        try:
            chance, amount = float(ratio[0]), int(ratio[1])
        except (TypeError, ValueError):
            chance, amount = 0.0, 0
        if amount > 0 and random.random() < chance:
            key_name = {2: "Silver", 3: "Food", 4: "Soldier"}[award_id]
            role_data = player.data.setdefault("RoleData", {})
            role_data[key_name] = _as_int(role_data.get(key_name) or 0) + amount
    return {"VisitPeople": people_id, "IsFirst": is_first}


def action_visit_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_findMod.action10602: seed/extend the persisted VisitData slice."""
    visit = _visit_data(player)
    store.save(player)
    return {"VisitData": _visit_client_slice(visit), "SysTime": _now()}


def action_visit_state(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_findMod.action10601: top-level state + a safe event.

    The callback (_findMod.lua 918-968) reads VitalityTime/FateTime/Fate/
    Vitality/PraiseGuide at top level, then _getEvent (findMod ~60-95) with
    EventType 0 reads only the client-side TcVisitNothingEvent config, so an
    empty RemainList is safe (no VisitData / beauty id is dereferenced).
    """
    visit = _visit_data(player)
    # 10601 is the actual visit: deduct the original config costs so the
    # client-side fate >= VisitReduceFate gate stays honest on later visits.
    now = _now()
    visit["Fate"] = max(0, _as_int(visit.get("Fate") or 0)
                        - _as_int(v2configs.env_value("VisitReduceFate") or 0))
    visit["FateTime"] = now
    visit["Vitality"] = max(0, _as_int(visit.get("Vitality") or 0)
                            - _as_int(v2configs.env_value("VisitReduceVitality") or 0))
    visit["VitalityTime"] = now
    out = _visit_client_slice(visit)
    response = {
        "VitalityTime": out["VitalityTime"],
        "FateTime": out["FateTime"],
        "Fate": out["Fate"],
        "Vitality": out["Vitality"],
        "PraiseGuide": out["PraiseGuide"],
        "EventType": 0,
        "RemainList": [],
        "SysTime": _now(),
    }
    # Roll the visit from the original configs; fall back to the nothing
    # event when the TcVisit* tables are unavailable.
    event_id = _visit_fate_event(_as_int(out["Fate"]))
    event_row = _visit_event_row(event_id) if event_id is not None else None
    if event_row is not None and event_id != _visit_nothing_event_id():
        if not _visit_event_people(event_row) and not _as_int(event_row.get("Award") or 0):
            entry = _visit_consort_entry(player)
            if entry is not None:
                response["EventType"] = 1
                response["RemainList"] = [entry]  # client reads RemainList[1]
        else:
            payload_out = _visit_people_result(player, visit, event_row)
            if payload_out is not None:
                response["EventType"] = 2
                response["VisitPeople"] = payload_out["VisitPeople"]
                response["IsFirst"] = payload_out["IsFirst"]
                attach_remain_list(response, player)
    store.save(player)
    return response


def action_visit_recover(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_findMod.action10603: refill Vitality to the configured maximum."""
    visit = _visit_data(player)
    max_vitality = _as_int(_player_vip_row(player).get("MaxVitality") or 0)
    visit["Vitality"] = max_vitality
    visit["VitalityTime"] = _now()
    store.save(player)
    return {"RecoverVitality": max_vitality,
            "VisitData": _visit_client_slice(visit), "SysTime": _now()}


def action_visit_sync(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_findMod.action10604: state sync after a vitality/fate timer tick."""
    visit = _visit_data(player)
    now = _now()
    # Stamps are "as-of" values; do not reset them on a timer sync or regen
    # progress is lost.  _visit_data already caught both meters up.
    store.save(player)
    out = _visit_client_slice(visit)
    return {"Fate": out["Fate"], "Vitality": out["Vitality"],
            "VitalityTime": out["VitalityTime"], "FateTime": out["FateTime"],
            # sortFindRespond does pairs(A0_2.AwardList) unguarded; an empty
            # table keeps the one-key timer path safe.
            "AwardList": [], "PraiseGuide": out["PraiseGuide"],
            "VisitData": out, "SysTime": now}


def action_visit_transfer(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_findMod.action10605: consume TransferNum up to TcVIP[VipLv].TransferNum.

    The callback (_findMod.lua 723-752) computes the remaining transfers as
    TcVIP.TransferNum - response.TransferNum, so the response carries the
    number already used.  No Fate/beauty amount is invented server-side.
    """
    visit = _visit_data(player)
    max_transfer = _as_int(_player_vip_row(player).get("TransferNum") or 0)
    requested = max(0, _as_int(payload.get("Num") or 0))
    used = min(max_transfer, _as_int(visit.get("TransferNum") or 0) + requested)
    visit["TransferNum"] = used
    store.save(player)
    return {"Fate": _as_int(visit.get("Fate") or 0), "TransferNum": used,
            "VisitData": _visit_client_slice(visit), "SysTime": _now()}


def action_visit_status(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_findMod.action10606: echo the client's Status; callback reads no fields."""
    visit = _visit_data(player)
    status = payload.get("Status")
    visit["Status"] = status
    store.save(player)
    return {"Status": status, "VisitData": _visit_client_slice(visit),
            "SysTime": _now()}


# --------------------------------------------------------------------------
# Exterior / appearance (13301-13305, 13310)
# --------------------------------------------------------------------------
def _appearance_lists(player: Player) -> tuple[list, list]:
    """Dress/Avatar slices; seeded in defaults.py from TcItem ids 400/1400/1500."""
    out = []
    for key in ("DressList", "AvatarList"):
        value = player.data.get(key)
        rows = [dict(e) for e in value if isinstance(e, dict)] if isinstance(value, list) else []
        player.data[key] = rows
        out.append(rows)
    return out[0], out[1]


def action_appearance_fashion(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_exteriorMod.action13301: dress a fashion item (client sets its own list)."""
    item_id = _as_int(payload.get("ItemId") or payload.get("itemId") or 0)
    dress, _ = _appearance_lists(player)
    if item_id and not any(_as_int(e.get("ItemId")) == item_id for e in dress):
        dress.append({"ItemId": item_id, "Num": 1, "Timestamp": _now()})
    base = player.data.setdefault("RoleBase", {})
    base["DressList"] = [dict(e) for e in dress]
    store.save(player)
    return {"ItemId": item_id, "DressList": [dict(e) for e in dress], "SysTime": _now()}


def action_appearance_title(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_exteriorMod.action13302: equip a title -> RoleBase.DressTitle."""
    item_id = _as_int(payload.get("ItemId") or payload.get("itemId") or 0)
    for key in ("RoleBase", "RoleData"):
        slice_ = player.data.get(key)
        if isinstance(slice_, dict):
            slice_["DressTitle"] = item_id
    store.save(player)
    return {"ItemId": item_id, "DressTitle": item_id, "SysTime": _now()}


def action_appearance_avatar(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_exteriorMod.action13303: set ChatAvatar; the callback reads NewChatAvatar.

    No v2 config maps an AvatarId to a chat-avatar string, so the id is echoed
    (F-prefixed for the fashion type, matching the seeded "F400"); this is
    derived from the request, not an invented reward.
    """
    avatar_id = _as_int(payload.get("AvatarId") or payload.get("avatarId") or 0)
    avatar_type = _as_int(payload.get("AvatarType") or payload.get("avatarType") or 0)
    base = player.data.setdefault("RoleBase", {})
    if avatar_id:
        # avatarLogic.lua: type 0/1 = fashion ("F<id>"), 2/4 wife ("B<id>"),
        # 3/4 counsellor ("C<id>").  A bare number has no prefix and the
        # client silently falls back to the generic level portrait.
        counsellor_ids = {
            int(c.get("CounsellorId") or 0)
            for c in (player.data.get("RoleCounsellor") or [])
            if isinstance(c, dict)
        }
        if avatar_type in (1, 0):
            prefix = "F"
        elif avatar_type == 3 or (avatar_type == 4 and avatar_id in counsellor_ids):
            prefix = "C"
        else:
            prefix = "B"
        new_avatar = f"{prefix}{avatar_id}"
    else:
        new_avatar = str(base.get("ChatAvatar") or "F400")
    for key in ("RoleBase", "RoleData"):
        slice_ = player.data.get(key)
        if isinstance(slice_, dict):
            slice_["ChatAvatar"] = new_avatar
    # Keep the persisted chat history in sync so every message shows the
    # avatar the player just selected, not the old head stored with it.
    for msg in _chat_sent(player):
        if isinstance(msg, dict):
            msg["ChatAvatar"] = new_avatar
            box = base.get("AvatarBox") or 1400
            try:
                msg["AvatarBox"] = int(box)
            except (TypeError, ValueError):
                msg["AvatarBox"] = 1400
    store.save(player)
    return {"AvatarId": avatar_id, "AvatarType": avatar_type,
            "NewChatAvatar": new_avatar, "SysTime": _now()}


def action_appearance_avatar_box(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_exteriorMod.action13304: equip an AvatarBox (id from TcItem)."""
    box_id = _as_int(payload.get("AvatarBox") or payload.get("avatarBox") or 0)
    for key in ("RoleBase", "RoleData"):
        slice_ = player.data.get(key)
        if isinstance(slice_, dict):
            slice_["AvatarBox"] = box_id
    store.save(player)
    return {"AvatarBox": box_id, "SysTime": _now()}


def action_appearance_scene_skin(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """_exteriorMod.action13305: set DressSceneId (1500/1501/1502 ship in APK)."""
    skin_id = _as_int(payload.get("SceneSkinId") or payload.get("sceneSkinId") or 0)
    for key in ("RoleBase", "RoleData"):
        slice_ = player.data.get(key)
        if isinstance(slice_, dict):
            slice_["DressSceneId"] = skin_id or 1500
    store.save(player)
    return {"SceneSkinId": skin_id or 1500, "SysTime": _now()}


def action_appearance_info(payload: dict, player: Player, session: Session, store: Store) -> dict:
    """v7-only action13310: full exterior/avatar info snapshot.

    AvatarBox / DressList / RoleItems / ChatAvatar come from the persisted
    config-derived seed (TcItem ids 400/1400/1500).  The remaining cosmetic
    ids (ChatBox 4000, AvatarBoxEffect 1900, RankBottomId 2700,
    RoleInfoBackGround 5900, ImageId 1) have NO v2 config source; they are the
    literal values from the official v7 13310 capture
    (recon/v7-capture/v7_actions.jsonl, 2026-11-16) and are documented here
    rather than presented as config-derived.
    """
    base = player.data.get("RoleBase") if isinstance(player.data.get("RoleBase"), dict) else {}
    dress, avatar = _appearance_lists(player)
    role_items = player.data.get("RoleItem")
    items = [dict(e) for e in role_items if isinstance(e, dict)] if isinstance(role_items, list) else []
    now = _now()
    store.save(player)
    return {
        "ImageId": 1,                      # v7 capture (no config source)
        "InviteJoinAllianceNum": 0,
        "Identifier": 0,
        "AliasBg": 0,
        "HonorList": [],
        "AvatarBoxData": {"SeasonRank": 0},
        "RankBottomId": 2700,              # v7 capture (no config source)
        "FashionEffectInfo": {},
        "RoleInfoBackGround": 5900,        # v7 capture (no config source)
        "RoleItems": items,
        "ChatAvatar": str(base.get("ChatAvatar") or "F400"),
        "CustomizeTitleList": [],
        "ServerVersion": "7.9",
        "CustomizeTitleId": "",
        "ChatFontColorId": 0,
        "ChatAvatarExpireTime": -1,
        "ChatBox": 4000,                   # v7 capture (no config source)
        "AvatarBoxEffect": 1900,           # v7 capture (no config source)
        "DressList": [dict(e) for e in dress],
        "AvatarBox": _as_int(base.get("AvatarBox") or 0),
        "ItemTitleId": 0,
        "AvatarBoxEffectExpireTime": -1,
        "DressTitle": _as_int(base.get("DressTitle") or 0),
        "AvatarList": [dict(e) for e in avatar],
        "RedPoint": _client_red_point(player),
        "SysTime": now,
    }


HANDLERS = {
    "10000": action_login,
    "10013": action_create_role,
    "10010": action_level_up,
    "10011": action_rename,
    "10015": action_task,
    "10202": action_stage_info,
    "10204": action_stage_result,
    "10201": action_stage_fight,
    "10001": action_manage_assets,
    "10002": action_recover,
    "10003": action_war_buff,
    "10016": action_worship_info,
    "10017": action_princess_list,
    "10301": action_items,
    "10302": action_item_use,
    "10303": action_items,
    "10304": action_items,
    "10305": action_items,
    "10306": action_items,
    "10307": action_item_jump,
    "11601": action_alliance_info,
    "11602": action_alliance_create,
    "11603": action_alliance_join,
    "11607": action_alliance_inquire,
    "11617": action_alliance_rank,
    "11629": action_alliance_rank_all,
    "10308": action_items,
    "10309": action_items,
    "10391": action_items,
    "12301": action_chat_send,
    "12302": action_chat,
    "12303": action_chat,
    "12306": action_chat,
    "12307": action_chat,
    "12310": action_chat,
    "12311": action_chat,
    "11645": action_alliance_pack,
    "10209": action_section_push,
    "10208": action_stage_info_v2,
    "11504": action_welf_card,
    "11301": action_daily_task,
    "11302": action_daily_task_claim,
    "11303": action_activity_award_claim,
    "11901": action_11901,
    "11902": action_11902,
    "11903": action_11903,
    "11904": action_11904,
    "11201": action_achieve,
    "11202": action_achieve_claim,
    "11101": action_store_items,
    "12111": action_mail_list,
    "11001": action_rank_info,
    "11002": action_rank_worship,
    "11003": action_rank_player_detail,
    "11004": action_rank_cross_list,
    "11005": action_rank_top1,
    "11006": action_rank_cross_player_detail,
    "11007": action_rank_alliance_rank_detail,
    "10701": action_government_event,
    "10702": action_government_info,
    "11502": action_sign_checkin,
    "11507": action_sign_cumulative,
    "10905": action_school_list,
    "10901": action_school_assign,
    "10906": action_school_study,
    "10907": action_school_study_list,
    "10902": action_school_complete,
    "10903": action_school_complete_all,
    "10904": action_school_capacity,
    "10101": action_counsellor_upgrade,
    "10102": action_counsellor_talent,
    "10103": action_counsellor_peerage,
    "10106": action_counsellor_skin,
    "11801": action_instance_info,
    "11802": action_instance_boss_info,
    "11803": action_instance_boss_attack,
    "11808": action_instance_rank,
    "11810": action_instance_store,
    "10401": action_wives_call,
    "16003": action_binding_info,
    "16004": action_binding_accounts,
    "18129": action_diplomacy_info,
    "18100": action_wives_guide,
    "18120": action_diplomacy_black,
    "18121": action_diplomacy_friends,
    "15000": action_logout,
    "11501": action_sign_info,
    "10018": action_status,
    "10601": action_visit_state,
    "10602": action_visit_info,
    "10603": action_visit_recover,
    "10604": action_visit_sync,
    "10605": action_visit_transfer,
    "10606": action_visit_status,
    "13301": action_appearance_fashion,
    "13302": action_appearance_title,
    "13303": action_appearance_avatar,
    "13304": action_appearance_avatar_box,
    "13305": action_appearance_scene_skin,
    "13310": action_appearance_info,
}


def dispatch(action: str, payload: dict, player: Player, session: Session, store: Store) -> dict:
    handler = HANDLERS.get(str(action))
    if handler is None:
        # Guess: most handlers read one or two fields out of the login state.
        # Returning the merged state keeps those fields present; the action log
        # tells us which ids still need a dedicated implementation.
        return action_full_state(payload, player, session, store)
    try:
        return handler(payload, player, session, store)
    except Exception as exc:  # keep the protocol alive and make the failure visible
        print(f"[handler] action {action} failed: {exc!r}", flush=True)
        return action_full_state(payload, player, session, store)
