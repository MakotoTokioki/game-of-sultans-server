"""Deterministic 4-week event rotation for the single-machine server.

Source: community spreadsheet (recon/community-research/spreadsheet/tabs/20_events.csv),
tab "Etkinlikler" -> "ETKİNLİKLERİN SIRALAMASI" (ORDER OF EVENTS). The sheet
documents a 4-cycle ("Devir") rotation:

  Devir 1 — Birlik Katkısı (Union Contribution)
  Devir 2 — Devlet Gücü (Empire Power)
  Devir 3 — Er Meydanı (Arena)
  Devir 4 — Samimiyet (Intimacy)

Each cycle has a cross-server event, a ranking-rush reward type, a set of
limited-time ("Sınırlı Süreli Ödüller") tasks, and mini-games ("Diğer").
The official event windows are 3 days and server-pushed; the sheet notes some
events have a 3-day registration delay and some do NOT repeat weekly (red bg).

This module turns that into a **deterministic, date-driven rotation** so the
server always shows a coherent, rotating set of events rather than a static
"everything always on". It is a documented simulation of the official pattern,
not a claim of exact parity (the real server picks events dynamically).

Ground rule: cycle membership and the ranking-reward type come from the
community sheet (directional); all *numeric* rewards stay in original configs.
"""
from __future__ import annotations

import datetime
import time

_GAME_TZ = datetime.timezone(datetime.timedelta(hours=3))
_GAME_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def _now() -> int:
    return int(time.time())

# Anchor: the first Monday used as week 1 of cycle 1. Week boundaries follow
# the client's game reset (UTC+3, same timezone as the rest of the server).
_ROTATION_ANCHOR = datetime.date(2026, 1, 5)   # a Monday
_WEEK_SECONDS = 7 * 24 * 3600
_CYCLE_WEEKS = 4
_EVENT_DAYS = 3                                # official events last 3 days

# The four cycles. Names are the Turkish sheet names; ranking_type is the
# original TcRankUpActivityCfg ActivityType for that cycle's "Sıralama Ödülü".
# limited_tasks are the sheet's "Sınırlı Süreli Ödüller" task keys (used only
# as labels; reward numbers remain in the configs).
CYCLES: list[dict] = [
    {
        "devir": 1,
        "name": "Birlik Katkısı",            # Union Contribution
        "cross_server": "10 Sunuculu Cariye Samimiyeti",
        "ranking_type": 4,                   # Union XP
        "limited_tasks": ["Elmas Harca", "Altın Harca", "Paye Kullan",
                          "Samimiyeti Artır", "Tebdile Çık", "Enderunda Eğit",
                          "İttifak Evliliği Yap", "Devlet İşlerini Hallet"],
        "mini_games": ["Rumeli Hisarı", "Karagöz ve Hacivat", "Nar-ı Aşk",
                       "Süvari Alayı", "Saray Atölyesi", "Yenilmez Beşli",
                       "Mutfak", "Promosyon (Ramazan Topu)"],
    },
    {
        "devir": 2,
        "name": "Devlet Gücü",                # Empire Power
        "cross_server": "10 Sunuculu Devlet Gücü",
        "ranking_type": 1,                   # Empire Power
        "limited_tasks": ["Elmas Harca", "Altın Harca", "Paye Kullan",
                          "Kuvvet Küresi Tüket", "Canlılık Küresi Tüket",
                          "Devlet Gücünü Artır", "İttifak Evliliği Yap",
                          "Girit'e Sefer Düzenle", "Girit'i Fethet"],
        "mini_games": ["Korsan Avı", "Bizans Hazinesi", "Nar-ı Aşk",
                       "Muhteşem Dörtlü", "Yenilmez Beşli"],
    },
    {
        "devir": 3,
        "name": "Er Meydanı",                 # Arena
        "cross_server": "Mısır Kalıntıları / 10 Sunuculu Devlet Gücü",
        "ranking_type": 3,                   # Arena
        "limited_tasks": ["Elmas Harca", "Altın Harca", "Paye Kullan",
                          "İntikam Mektubu Kullan", "Devlet Gücünü Artır",
                          "Er Meydanında Dövüş", "Girit'e Sefer Düzenle",
                          "Girit'i Fethet"],
        "mini_games": ["Barbar", "Yenilmez Beşli"],
    },
    {
        "devir": 4,
        "name": "Samimiyet",                  # Intimacy
        "cross_server": "10 Sunuculu Cariye Samimiyeti",
        "ranking_type": 5,                   # Intimacy
        "limited_tasks": ["Elmas Harca", "Yeniçeri Kullan",
                          "Enerji Küresi Tüket", "Samimiyeti Artır",
                          "Cazibeyi Artır", "Tebdile Çık", "Rastgele Ziyaret Et",
                          "Ziyafet Ver", "Ziyafete Katıl"],
        "mini_games": ["Sihirli Lamba", "Süvari Alayı", "Muhteşem Dörtlü",
                       "Yenilmez Beşli", "Saray Atölyesi"],
    },
]


def current_cycle_index(ts: int | None = None) -> int:
    """Return 0..3 for the current week's cycle (deterministic from date)."""
    ts = _now() if ts is None else ts
    today = datetime.datetime.fromtimestamp(ts, _GAME_TZ).date()
    days = (today - _ROTATION_ANCHOR).days
    week = max(0, days // 7)
    return week % _CYCLE_WEEKS


def current_cycle(ts: int | None = None) -> dict:
    return CYCLES[current_cycle_index(ts)]


def cycle_window(ts: int | None = None) -> tuple[str, str]:
    """The active window for the current cycle = the whole current UTC+3 week
    (Monday 00:00 -> Sunday 23:59:59). The official events are 3-day and the
    sheet notes a 3-day cross-server registration delay, but the *featured*
    cycle must stay open all week so there is always an active rotation entry;
    this is a documented adaptation of the 3-day pattern to a weekly rotation.
    """
    ts = _now() if ts is None else ts
    today = datetime.datetime.fromtimestamp(ts, _GAME_TZ).date()
    days = (today - _ROTATION_ANCHOR).days
    week = max(0, days // 7)
    monday = _ROTATION_ANCHOR + datetime.timedelta(weeks=week)
    start = datetime.datetime.combine(monday, datetime.time(0, 0, 0), _GAME_TZ)
    end = datetime.datetime.combine(monday + datetime.timedelta(days=6),
                                    datetime.time(23, 59, 59), _GAME_TZ)
    return start.strftime(_GAME_DATE_FMT), end.strftime(_GAME_DATE_FMT)


def active_ranking_types(ts: int | None = None) -> set[int]:
    """Only the current cycle's ranking rush is 'active'; the others remain
    advertised but with closed windows so they show as ended (faithful to a
    3-day rotation rather than everything-always-on)."""
    return {current_cycle(ts)["ranking_type"]}


def summary(ts: int | None = None) -> dict:
    c = current_cycle(ts)
    start, end = cycle_window(ts)
    return {
        "devir": c["devir"], "name": c["name"], "ranking_type": c["ranking_type"],
        "window": [start, end], "limited_tasks": c["limited_tasks"],
        "mini_games": c["mini_games"], "cross_server": c["cross_server"],
    }
