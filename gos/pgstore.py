"""PostgreSQL-backed storage for players and sessions.

Schema (gos_dev):
  players(uid text PK, data jsonb, updated_at timestamptz)
  sessions(jwt text PK, user_id text, sk text, player_id text,
           server_id int, app_id int, created float8, last_seen float8,
           updated_at timestamptz)

The DB mirrors the JSON-file layout 1:1 (data/players/<uid>.json and
data/sessions.json stay the fallback).  Migration is automatic on first
open: file rows that do not exist in the DB are upserted; DB rows always
win on later reads.  Every DB failure falls back to the file store so the
dev server keeps running without PostgreSQL.
"""
from __future__ import annotations

import json
import threading
import time

from . import config
from .store import Player, Session, Store
from .defaults import default_player

try:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG = True
except ImportError:  # pragma: no cover
    _PSYCOPG = False

_DSN = None  # set by init_pg_store()


def _connect():
    return psycopg2.connect(_DSN, connect_timeout=3)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    uid        text PRIMARY KEY,
    data       jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sessions (
    jwt        text PRIMARY KEY,
    user_id    text NOT NULL,
    sk         text NOT NULL,
    player_id  text NOT NULL DEFAULT '',
    server_id  int  NOT NULL DEFAULT 0,
    app_id     int  NOT NULL DEFAULT 0,
    created    float8 NOT NULL DEFAULT 0,
    last_seen  float8 NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);
"""


class PgStore(Store):
    """Drop-in Store with a PostgreSQL mirror and file fallback."""

    def __init__(self, dsn: str) -> None:
        self._pg_ok = False
        self._pg_lock = threading.RLock()
        self._conn = None
        try:
            conn = _connect()
            with conn, conn.cursor() as cur:
                cur.execute(_SCHEMA)
            self._conn = conn
            self._pg_ok = True
        except Exception as exc:
            print(f"[pgstore] PostgreSQL unavailable ({exc!r}); file fallback", flush=True)
            self._conn = None
        super().__init__()
        if self._pg_ok:
            self._migrate_files()

    # ---------------- internals ----------------
    def _reconnect(self) -> bool:
        try:
            if self._conn is None or self._conn.closed:
                self._conn = _connect()
                with self._conn, self._conn.cursor() as cur:
                    cur.execute(_SCHEMA)
            self._pg_ok = True
            return True
        except Exception as exc:
            print(f"[pgstore] reconnect failed ({exc!r}); file fallback", flush=True)
            self._pg_ok = False
            self._conn = None
            return False

    def _migrate_files(self) -> int:
        """Upsert file-backed players/sessions that the DB does not know yet."""
        migrated = 0
        try:
            for path in sorted(config.PLAYER_DIR.glob("*.json")):
                uid = path.stem
                if uid.endswith(".tmp"):
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                with self._pg_lock, self._conn, self._conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO players (uid, data) VALUES (%s, %s)"
                        " ON CONFLICT (uid) DO NOTHING",
                        (uid, psycopg2.extras.Json(data)),
                    )
                    migrated += cur.rowcount
            rows = []
            if self._session_file.exists():
                try:
                    rows = json.loads(self._session_file.read_text(encoding="utf-8"))
                except Exception:
                    rows = []
            for item in rows:
                with self._pg_lock, self._conn, self._conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO sessions (jwt, user_id, sk, player_id, server_id,"
                        " app_id, created, last_seen)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (jwt) DO NOTHING",
                        (item.get("jwt", ""), item.get("user_id", ""), item.get("sk", ""),
                         item.get("player_id", ""), int(item.get("server_id", 0)),
                         int(item.get("app_id", 0)), float(item.get("created", 0)),
                         float(item.get("last_seen", 0))),
                    )
                    migrated += cur.rowcount
            if migrated:
                print(f"[pgstore] migrated {migrated} rows from JSON files", flush=True)
        except Exception as exc:
            print(f"[pgstore] migration failed ({exc!r}); file fallback", flush=True)
            self._pg_ok = False
        return migrated

    # ---------------- players ----------------
    def get_or_create(self, uid: str, nickname: str = "Sultan") -> Player:
        uid = str(uid or "").strip() or "dev"
        with self._lock:
            if uid in self._players:
                return self._players[uid]
            data = None
            path = config.PLAYER_DIR / f"{uid}.json"
            # DB is authoritative when it has the row
            if self._pg_ok or self._reconnect():
                try:
                    with self._pg_lock, self._conn, self._conn.cursor() as cur:
                        cur.execute("SELECT data FROM players WHERE uid = %s", (uid,))
                        row = cur.fetchone()
                        if row:
                            data = row[0]
                except Exception as exc:
                    print(f"[pgstore] player read failed ({exc!r}); file fallback", flush=True)
                    self._pg_ok = False
            if data is None and path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    data = None
            if data is None:
                data = default_player(uid, nickname)
                if self._pg_ok or self._reconnect():
                    try:
                        with self._pg_lock, self._conn, self._conn.cursor() as cur:
                            cur.execute(
                                "INSERT INTO players (uid, data) VALUES (%s, %s)"
                                " ON CONFLICT (uid) DO NOTHING",
                                (uid, psycopg2.extras.Json(data)),
                            )
                    except Exception:
                        pass
            now = int(time.time())
            data["SysTime"] = now
            data.setdefault("LastLoginTime", now)
            player = Player(uid=uid, data=data, path=path)
            player.save()  # keeps the file mirror in sync
            self._players[uid] = player
            return player

    def save(self, player: Player) -> None:
        with self._lock:
            player.save()  # file mirror (fallback + inspection)
            if self._pg_ok or self._reconnect():
                try:
                    with self._pg_lock, self._conn, self._conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO players (uid, data, updated_at)"
                            " VALUES (%s, %s, now())"
                            " ON CONFLICT (uid) DO UPDATE"
                            " SET data = EXCLUDED.data, updated_at = now()",
                            (player.uid, psycopg2.extras.Json(player.data)),
                        )
                except Exception as exc:
                    print(f"[pgstore] player save failed ({exc!r}); file fallback", flush=True)
                    self._pg_ok = False

    # ---------------- sessions ----------------
    def add_session(self, session: Session) -> None:
        with self._lock:
            super().add_session(session)  # in-memory + sessions.json
            if self._pg_ok or self._reconnect():
                try:
                    with self._pg_lock, self._conn, self._conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO sessions (jwt, user_id, sk, player_id,"
                            " server_id, app_id, created, last_seen, updated_at)"
                            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now())"
                            " ON CONFLICT (jwt) DO UPDATE SET last_seen = EXCLUDED.last_seen,"
                            " updated_at = now()",
                            (session.jwt, session.user_id, session.sk,
                             session.player_id, int(session.server_id),
                             int(session.app_id), float(session.created),
                             float(session.last_seen)),
                        )
                except Exception as exc:
                    print(f"[pgstore] session save failed ({exc!r}); file fallback", flush=True)
                    self._pg_ok = False

    def touch(self, session: Session) -> None:
        super().touch(session)
        if self._pg_ok:
            try:
                with self._pg_lock, self._conn, self._conn.cursor() as cur:
                    cur.execute(
                        "UPDATE sessions SET last_seen = %s, updated_at = now()"
                        " WHERE jwt = %s",
                        (float(session.last_seen), session.jwt),
                    )
            except Exception:
                self._pg_ok = False  # touch is best-effort


def init_pg_store(store: Store, dsn: str | None) -> Store:
    """Wrap `store` with a PostgreSQL mirror when configured/possible."""
    global _DSN
    if not dsn or not _PSYCOPG:
        if dsn and not _PSYCOPG:
            print("[pgstore] psycopg2 not installed; file fallback", flush=True)
        return store
    _DSN = dsn
    try:
        return PgStore(dsn)
    except Exception as exc:
        print(f"[pgstore] init failed ({exc!r}); file fallback", flush=True)
        return store
