"""Server handlers for the v2.005 "misc event" clusters.

Covered action ids (all absent from ``gos/handlers.py`` at the time of writing):

* 13001        -- redeem code (``_itemMod.lua``; request ``RedeemCode``, the
                  setting-scene callback reads ``Stat`` and ``RemainList``).
* 13051, 13053-13064 -- pirates / Sea King (``_fightPirateMod.lua``).
                  13052 is "open box", 13053 full state, 13054 change/search
                  area, 13055 observe, 13056 open box (other type), 13057
                  observation list, 13058 attack boss/event, 13059 end event,
                  13060 replace event, 13061 replace event (alternate), 13063
                  log list, 13064 give up.  13062 does not exist in v2.005.
* 13101-13105 -- Facebook invitation (``_invitationMod.lua``).
* 13200-13204, 13206-13209 -- holiday marketing (``_holidayMarketingMod.lua``:
                  login check-in / turntable / group-buy).  13205 does not
                  exist in v2.005.
* 14301        -- settings/misc (``_settingMod.lua``; request ``UId``, opaque
                  response).

13301-13305 (exterior / skins) are intentionally NOT reimplemented here: they
are already registered in ``gos/handlers.py`` as ``action_appearance_fashion``,
``action_appearance_title``, ``action_appearance_avatar``,
``action_appearance_avatar_box`` and ``action_appearance_scene_skin``.

Response shapes were recovered with
``python3 tools/lua_shape_harness/extract_fields.py ACTION`` and by walking
backwards from each ``actionNNNNN = Lxx`` assignment to its request function and
nested ``netProtocol.action`` callback in the decompiled module (plus the
setting-scene callback for 13001).  Field map:

    13001  Stat, RemainList(+ scene)
    13051  (opaque envelope)
    13052  FoodNum
    13053  ActivityCfg, CurrentAreaId, HintData, IsHaveCreateTeam, KillBossNum,
           KillPirateNum, MyScore, MyTeam, Overlord, QuitTime, UnOpenBoxNum
    13054  CurrentAreaId, CurrentEvent, FoodNum, HintData, IsObservationFull,
           LastRecoveryTime, MyScore, ObservationNum
    13055  CurrentEvent, FoodNum, HintData, LastRecoveryTime, ObservationNum,
           UnlockAreaAward
    13056  FoodNum
    13057  HintData, MyScore, ObservationList
    13058  CurrentBlood, HintData, LastAttackTime, MyScore, Status
    13059  HintData
    13060  CurrentEvent, HintData, MyScore
    13061  HintData
    13063  HintData, LogList
    13064  HintData
    13101  InvitedFriends, InvitedStage, IsFirst, IsInvited, Point, RewardData
    13102  (opaque envelope)
    13103  InvitedFriends, InvitedStage
    13104  (opaque envelope)
    13105  IsFirst
    13200  LoginActivity
    13201  HistoricalReward, IsAllComplete, NextCheckInTime
    13202  TurntableLottery
    13203  FreeTimes, LogList, LotteryList, NextFreeTime, NowTimes, RemainTimes
    13204  NowGrade, NowTimes, UpgradeTimes
    13206  IsChangeCfg, RemainTimes, NowBuyNum, NextStageNum, IsStageReward,
           MarketingGroupBuyActivityCfg (nested 11901 callback)
    13207  (opaque envelope)
    13208  GiftPackage
    13209  NextStageNum, NowBuyNum
    14301  (opaque envelope)

Reward policy (per HANDLER_SPEC): no number is invented.  The only reachable
config ``AwardItem``/``AwardList`` columns are ``TcFBInvitationAward`` (13102)
and ``TcCheckInAward`` (13201); those two grant through the shared
``_grant_award_pairs`` + ``attach_remain_list`` helpers.  Sea-King box/buff
contents, turntable awards and group-buy packages live in client assets
(``sceneCfg.fightPirateCfg`` / ``AwardCfg`` /
``MarketingGroupBuyActivityCfg`` / ``TurntableGradeCfg``), not in any
parseable v2 server config, so those actions return Lua-safe empty/zero values
and are listed as server-authoritative in the report.  Redeem codes (13001)
have no code->award table in the v2 configs either; ``Stat=0`` (invalid) is
returned rather than fabricating a successful redemption.

All new persistent state is lazy (``setdefault``); no defaults.py change.
"""
from __future__ import annotations

from gos import v2configs
from gos.handlers import (
    _REMAIN_LIST_MAP,
    _as_int,
    _grant_award_pairs,
    attach_remain_list,
)


# ---------------------------------------------------------------------------
# config / state helpers
# ---------------------------------------------------------------------------
def _cfg_rows(name: str) -> list[dict]:
    rows = v2configs.load(name)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, (list, tuple)):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _area_rows() -> list[dict]:
    return _cfg_rows("TcSeaKingAreaInfoCfg")


def _invitation_rows() -> list[dict]:
    return _cfg_rows("TcFBInvitationAward")


def _checkin_rows() -> list[dict]:
    return _cfg_rows("TcCheckInAward")


def _seaking(player) -> dict:
    return player.data.setdefault("SeaKing", {})


def _invitation(player) -> dict:
    return player.data.setdefault("InvitationExtra", {})


def _holiday(player) -> dict:
    return player.data.setdefault("HolidayMarketing", {})


def _login_activity(player) -> dict:
    return _holiday(player).setdefault("LoginActivity", {})


def _turntable(player) -> dict:
    return _holiday(player).setdefault("TurntableLottery", {})


def _groupbuy(player) -> dict:
    return _holiday(player).setdefault("GroupBuy", {})


def _hint() -> dict:
    """``dealHintData`` reads ``HintData.HintType``; an empty table is Lua-safe
    and makes the client fall through to the normal response path."""
    return {}


def _grant(data: dict, player, award, store) -> dict:
    """Grant config AwardItem pairs and attach the absolute resource snapshot."""
    pairs, bag_ids = _grant_award_pairs(player, award)
    store.save(player)
    return attach_remain_list(
        data, player, items=bag_ids,
        resources=[int(p[0]) for p in pairs if int(p[0]) in _REMAIN_LIST_MAP],
    )


# ---------------------------------------------------------------------------
# 13001 -- redeem code (cdkey) -- _itemMod.lua + settingScene
# ---------------------------------------------------------------------------
def action_13001(payload: dict, player, session, store) -> dict:
    """Redeem-code response.  The setting scene callback reads ``Stat``
    (1 = success, 2 = used, 294 = expired, anything else = error) and
    ``RemainList`` (list).  No v2 config maps a code to an award, so validating
    codes and computing their rewards is server-authoritative: return the
    safe "code invalid" status with an empty reward list."""
    return {"Stat": 0, "RemainList": []}


# ---------------------------------------------------------------------------
# pirates / Sea King (13051, 13053-13064)
# ---------------------------------------------------------------------------
def action_13051(payload: dict, player, session, store) -> dict:
    """Opaque refresh envelope; the client callback forwards it unchanged and
    reads no response field (extract_fields: [])."""
    return {}


def action_13052(payload: dict, player, session, store) -> dict:
    """Open a food box: callback reads only ``FoodNum`` (live Lazy SeaKing
    state; no config seeds food)."""
    return {"FoodNum": _as_int(_seaking(player).get("FoodNum") or 0)}


def action_13053(payload: dict, player, session, store) -> dict:
    """Full Sea King state.

    * ``ActivityCfg.NormalItems`` / ``UnusualItems`` are client preview lists
      (no server table) -> empty lists.
    * ``ActivityCfg.UnlockRequireNum`` is config-derived from the five
      ``TcSeaKingAreaInfoCfg.RequireNum`` rows and ``KillBossNum`` is the
      matching 5-entry per-area counter list (the client indexes both by
      area-1 in ``judgeUnlockAreaIndex``).
    * ``MyTeam``/``Overlord``/ranks are multiplayer data -> safe empty shapes;
      counters are lazy player state (0 on a fresh account).
    """
    st = _seaking(player)
    areas = _area_rows()
    unlock = [_as_int(r.get("RequireNum")) for r in areas]
    return {
        "ActivityCfg": {
            "NormalItems": [],
            "UnusualItems": [],
            "UnlockRequireNum": unlock,
        },
        "CurrentAreaId": _as_int(st.get("CurrentAreaId") or 1),
        "HintData": _hint(),
        "IsHaveCreateTeam": _as_int(st.get("IsHaveCreateTeam") or 0),
        "KillBossNum": [0] * max(1, len(areas)),
        "KillPirateNum": _as_int(st.get("KillPirateNum") or 0),
        "MyScore": _as_int(st.get("MyScore") or 0),
        "MyTeam": {"TeamId": _as_int(st.get("TeamId") or 0)},
        "Overlord": {},
        "QuitTime": _as_int(st.get("QuitTime") or 0),
        "UnOpenBoxNum": _as_int(st.get("UnOpenBoxNum") or 0),
    }


def action_13054(payload: dict, player, session, store) -> dict:
    """Change/search area.  ``CurrentAreaId`` is echoed from the request when
    present, otherwise taken from lazy state (default 1).  ``CurrentEvent`` is
    a table the client indexes ``.AreaId``/``.DetailedEventType`` on, so both
    keys are supplied.  Observation/food/score are live state (no config)."""
    st = _seaking(player)
    area = _as_int(payload.get("AreaId") or payload.get("areaId")
                   or st.get("CurrentAreaId") or 1)
    st["CurrentAreaId"] = area
    return {
        "CurrentAreaId": area,
        "CurrentEvent": {"AreaId": area, "DetailedEventType": 0},
        "FoodNum": _as_int(st.get("FoodNum") or 0),
        "HintData": _hint(),
        "IsObservationFull": _as_int(st.get("IsObservationFull") or 0),
        "LastRecoveryTime": _as_int(st.get("LastRecoveryTime") or 0),
        "MyScore": _as_int(st.get("MyScore") or 0),
        "ObservationNum": _as_int(st.get("ObservationNum") or 0),
    }


def action_13055(payload: dict, player, session, store) -> dict:
    """Observe pirates.  ``UnlockAreaAward`` is pairs()'d and sorted by
    ``AreaId``; unlock-award contents live only in client assets
    (TcSeaKingAchievementCfg has no AwardItem column), so return an empty
    list rather than inventing an award."""
    st = _seaking(player)
    area = _as_int(st.get("CurrentAreaId") or 1)
    return {
        "CurrentEvent": {"AreaId": area, "DetailedEventType": 0},
        "FoodNum": _as_int(st.get("FoodNum") or 0),
        "HintData": _hint(),
        "LastRecoveryTime": _as_int(st.get("LastRecoveryTime") or 0),
        "ObservationNum": _as_int(st.get("ObservationNum") or 0),
        "UnlockAreaAward": [],
    }


def action_13056(payload: dict, player, session, store) -> dict:
    """Open the second box type; callback reads only ``FoodNum``."""
    return {"FoodNum": _as_int(_seaking(player).get("FoodNum") or 0)}


def action_13057(payload: dict, player, session, store) -> dict:
    """Observation list (other players' live data).  The client iterates and
    takes ``#ObservationList``, so an empty list is the Lua-safe default."""
    st = _seaking(player)
    return {
        "HintData": _hint(),
        "MyScore": _as_int(st.get("MyScore") or 0),
        "ObservationList": [],
    }


def action_13058(payload: dict, player, session, store) -> dict:
    """Attack a boss/event.  Boss HP / attack cooldown are server-authoritative
    (no v2 config for them); keep the lazy values and let ``Status=0`` let the
    client store the score.  ``CurrentBlood <= 0`` clears the local event."""
    st = _seaking(player)
    return {
        "CurrentBlood": _as_int(st.get("CurrentBlood") or 0),
        "HintData": _hint(),
        "LastAttackTime": _as_int(st.get("LastAttackTime") or 0),
        "MyScore": _as_int(st.get("MyScore") or 0),
        "Status": _as_int(st.get("Status") or 0),
    }


def action_13059(payload: dict, player, session, store) -> dict:
    """End the current event; callback only handles ``HintData``."""
    return {"HintData": _hint()}


def action_13060(payload: dict, player, session, store) -> dict:
    """Replace the current event; callback stores ``CurrentEvent``/``MyScore``
    and handles ``HintData``."""
    st = _seaking(player)
    area = _as_int(st.get("CurrentAreaId") or 1)
    return {
        "CurrentEvent": {"AreaId": area, "DetailedEventType": 0},
        "HintData": _hint(),
        "MyScore": _as_int(st.get("MyScore") or 0),
    }


def action_13061(payload: dict, player, session, store) -> dict:
    """Alternate replace-event request; callback only handles ``HintData``."""
    return {"HintData": _hint()}


def action_13063(payload: dict, player, session, store) -> dict:
    """Log list query: ``LogList`` is forwarded to a listener that iterates it,
    so return a Lua-safe empty list.  ``HintData`` is handled first."""
    return {"HintData": _hint(), "LogList": []}


def action_13064(payload: dict, player, session, store) -> dict:
    """Give up / quit: callback only handles ``HintData``."""
    return {"HintData": _hint()}


# ---------------------------------------------------------------------------
# invitation (13101-13105)
# ---------------------------------------------------------------------------
def action_13101(payload: dict, player, session, store) -> dict:
    """Full invitation state.  Stage awards are claimed via 13102; the
    accumulated ``Point``, claimed ``RewardData`` (map stage -> 1), ``IsFirst``
    / ``IsInvited`` flags and invited-friend counters are live state."""
    st = _invitation(player)
    return {
        "InvitedFriends": list(st.get("InvitedFriends") or []),
        "InvitedStage": _as_int(st.get("InvitedStage") or 0),
        "IsFirst": _as_int(st.get("IsFirst") or 0),
        "IsInvited": _as_int(st.get("IsInvited") or 0),
        "Point": _as_int(st.get("Point") or 0),
        "RewardData": dict(st.get("RewardData") or {}),
    }


def action_13102(payload: dict, player, session, store) -> dict:
    """Claim an invitation stage award.  Request carries ``AwardStage``; the
    client marks the stage claimed locally and reads no response field.  The
    only config-sourced grant available is the matching ``AwardType=2`` row of
    ``TcFBInvitationAward`` (AwardType 1 is the first-invite gift, 3 is the
    per-friend-count record; neither is granted by this action)."""
    stage = _as_int(payload.get("AwardStage") or payload.get("awardStage") or 0)
    row = next(
        (r for r in _invitation_rows()
         if _as_int(r.get("AwardType")) == 2 and _as_int(r.get("Stage")) == stage),
        None,
    )
    st = _invitation(player)
    rewards = st.setdefault("RewardData", {})
    rewards[str(stage)] = 1
    data = {"RewardData": dict(rewards), "Stage": stage}
    if row is None:
        store.save(player)
        return data
    return _grant(data, player, row.get("AwardItem"), store)


def action_13103(payload: dict, player, session, store) -> dict:
    """Invited-friends query: callback stores ``InvitedStage`` and
    ``InvitedFriends`` (a list)."""
    st = _invitation(player)
    return {
        "InvitedFriends": list(st.get("InvitedFriends") or []),
        "InvitedStage": _as_int(st.get("InvitedStage") or 0),
    }


def action_13104(payload: dict, player, session, store) -> dict:
    """Submit an invitation code.  Request carries ``InvitationCode``; the
    callback only sets ``isInvited = 1`` locally and reads no response field.
    Code validation / cross-player attributed points are server-authoritative.
    """
    st = _invitation(player)
    st["IsInvited"] = 1
    store.save(player)
    return {}


def action_13105(payload: dict, player, session, store) -> dict:
    """First-invite flag query; callback reads only ``IsFirst``."""
    return {"IsFirst": _as_int(_invitation(player).get("IsFirst") or 0)}


# ---------------------------------------------------------------------------
# holiday marketing (13200-13209)
# ---------------------------------------------------------------------------
def _history(player) -> list:
    return list(_login_activity(player).get("HistoricalReward") or [])


def action_13200(payload: dict, player, session, store) -> dict:
    """Login-activity query.  The callback stores the whole ``LoginActivity``
    table, then reads ``.AwardCfg`` (client asset -> empty map) and later
    ``.HistoricalReward``/check-in fields.  Keeping the keys present avoids
    nil-indexing in the client."""
    st = _login_activity(player)
    rows = _checkin_rows()
    return {"LoginActivity": {
        "AwardCfg": {},
        "CheckInDay": _as_int(st.get("CheckInDay") or 0),
        "HistoricalReward": _history(player),
        "IsAllComplete": _as_int(st.get("IsAllComplete") or 0),
        "NextCheckInTime": _as_int(st.get("NextCheckInTime") or 0),
    }}


def action_13201(payload: dict, player, session, store) -> dict:
    """Holiday login check-in (grant).  ``HistoricalReward`` is the list of
    already-claimed ``TcCheckInAward.AwardId`` values, ``IsAllComplete`` is
    derived from the number of config rows and ``NextCheckInTime`` is 0 (the
    activity schedule is client-asset/server-authoritative; no timing config
    exists).  The award for the current day comes only from
    ``TcCheckInAward.AwardList``."""
    st = _login_activity(player)
    rows = _checkin_rows()
    day = _as_int(st.get("CheckInDay") or 0) + 1
    row = next((r for r in rows if _as_int(r.get("AwardId")) == day), None)
    history = _history(player)
    if row is not None:
        history.append(day)
    st["CheckInDay"] = day
    st["HistoricalReward"] = history
    st["IsAllComplete"] = 1 if rows and day >= len(rows) else 0
    data = {
        "HistoricalReward": list(history),
        "IsAllComplete": int(st["IsAllComplete"]),
        "NextCheckInTime": _as_int(st.get("NextCheckInTime") or 0),
    }
    if row is None:
        store.save(player)
        return data
    return _grant(data, player, row.get("AwardList"), store)


def action_13202(payload: dict, player, session, store) -> dict:
    """Turntable query.  ``TurntableLottery`` is stored wholesale when the
    client already has config, otherwise it reads the scalar counters out of
    it.  ``AwardCfg`` / ``TurntableGradeCfg`` come from client assets (no v2
    server table) -> empty maps, which are safe for the client's ``next()``."""
    st = _turntable(player)
    return {"TurntableLottery": {
        "AwardCfg": {},
        "FreeTimes": _as_int(st.get("FreeTimes") or 0),
        "HaveVipExp": _as_int(st.get("HaveVipExp") or 0),
        "LotteryNeedVipExp": _as_int(st.get("LotteryNeedVipExp") or 0),
        "NextFreeTime": _as_int(st.get("NextFreeTime") or 0),
        "NowTimes": _as_int(st.get("NowTimes") or 0),
        "RemainTimes": _as_int(st.get("RemainTimes") or 0),
        "TurntableGradeCfg": {},
    }}


def action_13203(payload: dict, player, session, store) -> dict:
    """Spin the turntable.  Request carries ``IsContinuous``.  The lottery
    result (award tier/items) and the log come from client ``AwardCfg``, not
    from any server config, so no item is granted; only the live counters are
    maintained (free/remain decrement, total draws increment)."""
    st = _turntable(player)
    free = max(0, _as_int(st.get("FreeTimes") or 0) - 1)
    remain = max(0, _as_int(st.get("RemainTimes") or 0) - 1)
    now = _as_int(st.get("NowTimes") or 0) + 1
    st["FreeTimes"], st["RemainTimes"], st["NowTimes"] = free, remain, now
    store.save(player)
    return {
        "FreeTimes": free,
        "LogList": [],
        "LotteryList": [],
        "NextFreeTime": _as_int(st.get("NextFreeTime") or 0),
        "NowTimes": now,
        "RemainTimes": remain,
    }


def action_13204(payload: dict, player, session, store) -> dict:
    """Upgrade the turntable grade; callback stores ``NowTimes``,
    ``UpgradeTimes`` and ``NowGrade`` (lazy live state)."""
    st = _turntable(player)
    grade = _as_int(st.get("NowGrade") or 0) + 1
    upgrades = _as_int(st.get("UpgradeTimes") or 0) + 1
    st["NowGrade"], st["UpgradeTimes"] = grade, upgrades
    store.save(player)
    return {
        "NowGrade": grade,
        "NowTimes": _as_int(st.get("NowTimes") or 0),
        "UpgradeTimes": upgrades,
    }


def action_13206(payload: dict, player, session, store) -> dict:
    """Buy a group-buy package.  Request carries ``ActivityId``, ``PackageId``,
    ``Version`` and ``CurRemainNum``.  The primary callback reads
    ``IsChangeCfg``/``RemainTimes``/``NowBuyNum``/``NextStageNum``/
    ``IsStageReward``; the nested 11901 callback reads
    ``MarketingGroupBuyActivityCfg`` (client asset -> empty list here because
    the config is optional).  Purchase counts are lazy live state."""
    pid = _as_int(payload.get("PackageId") or payload.get("packageId") or 0)
    pkg = _groupbuy(player).setdefault("Packages", {}).setdefault(str(pid), {})
    buy = _as_int(pkg.get("NowBuyNum") or 0) + 1
    remain = max(0, _as_int(payload.get("CurRemainNum") or 0) - 1)
    pkg["NowBuyNum"] = buy
    pkg["RemainTimes"] = remain
    store.save(player)
    return {
        "IsChangeCfg": 0,
        "MarketingGroupBuyActivityCfg": [],
        "IsStageReward": 0,
        "NextStageNum": _as_int(pkg.get("NextStageNum") or 0),
        "NowBuyNum": buy,
        "RemainTimes": remain,
    }


def action_13207(payload: dict, player, session, store) -> dict:
    """Claim a group-buy stage reward.  The callback forwards an opaque
    envelope and reads no field; stage rewards come from the client
    ``MarketingGroupBuyActivityCfg`` (no server AwardItem column), so no item
    is fabricated."""
    return {}


def action_13208(payload: dict, player, session, store) -> dict:
    """Group-buy package list query: callback stores ``GiftPackage`` (a list).
    Package definitions are client assets -> empty Lua-safe list."""
    return {"GiftPackage": []}


def action_13209(payload: dict, player, session, store) -> dict:
    """Group-buy package buy info; callback reads ``NowBuyNum`` and
    ``NextStageNum`` from lazy state keyed by ``PackageId``."""
    pid = _as_int(payload.get("PackageId") or payload.get("packageId") or 0)
    pkg = (_groupbuy(player).get("Packages") or {}).get(str(pid)) or {}
    return {
        "NextStageNum": _as_int(pkg.get("NextStageNum") or 0),
        "NowBuyNum": _as_int(pkg.get("NowBuyNum") or 0),
    }


# ---------------------------------------------------------------------------
# 14301 -- settings/misc
# ---------------------------------------------------------------------------
def action_14301(payload: dict, player, session, store) -> dict:
    """``_settingMod.lua`` request sends ``{UId = RoleBase.UserId}``; the
    response callback forwards the envelope to its caller and reads no field
    (extract_fields: []).  An empty Lua-safe map is therefore correct; the
    payload's meaning is server-authoritative."""
    return {}


MISC_EVENTS_HANDLERS = {
    "13001": action_13001,
    "13051": action_13051,
    "13052": action_13052,
    "13053": action_13053,
    "13054": action_13054,
    "13055": action_13055,
    "13056": action_13056,
    "13057": action_13057,
    "13058": action_13058,
    "13059": action_13059,
    "13060": action_13060,
    "13061": action_13061,
    "13063": action_13063,
    "13064": action_13064,
    "13101": action_13101,
    "13102": action_13102,
    "13103": action_13103,
    "13104": action_13104,
    "13105": action_13105,
    "13200": action_13200,
    "13201": action_13201,
    "13202": action_13202,
    "13203": action_13203,
    "13204": action_13204,
    "13206": action_13206,
    "13207": action_13207,
    "13208": action_13208,
    "13209": action_13209,
    "14301": action_14301,
}
