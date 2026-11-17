from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import config
from .crypto import ServerKeyPair, decode_request_data
from .defaults import default_player


def _sk_variants(sk: str) -> list[str]:
    """Return the plausible wire spellings of a stored RC4 session key.

    OpenSSL EVP_EncodeFinal (used by the client's liblua.shareKey) terminates
    its base64 output with a newline.  Sessions derived by our own crypto.py
    are stored without it, so both variants must be tried when matching an
    incoming encrypted request.
    """
    base = sk or ""
    out: list[str] = []
    for key in (base, base + "\n", base.rstrip("\n")):
        if key and key not in out:
            out.append(key)
    return out


@dataclass
class Session:
    jwt: str
    user_id: str
    sk: str
    keypair: ServerKeyPair | None
    player_id: str
    server_id: int
    app_id: int
    created: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


@dataclass
class Player:
    uid: str
    data: dict
    path: Path

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._players: dict[str, Player] = {}
        self._sessions: dict[str, Session] = {}
        self._by_user: dict[str, list[Session]] = {}
        self._session_file = config.DATA_DIR / "sessions.json"
        self._load_sessions()

    def _load_sessions(self) -> None:
        if not self._session_file.exists():
            return
        try:
            raw = json.loads(self._session_file.read_text(encoding="utf-8"))
        except Exception:
            return
        for item in raw:
            try:
                session = Session(
                    jwt=item["jwt"],
                    user_id=item["user_id"],
                    sk=item["sk"],
                    keypair=None,
                    player_id=item.get("player_id", ""),
                    server_id=int(item.get("server_id", 0)),
                    app_id=int(item.get("app_id", 0)),
                    created=float(item.get("created", 0)),
                    last_seen=float(item.get("last_seen", 0)),
                )
            except Exception:
                continue
            self._sessions[session.jwt] = session
            self._by_user.setdefault(session.user_id, []).append(session)

    def _save_sessions(self) -> None:
        # called while holding self._lock
        rows = [
            {
                "jwt": s.jwt,
                "user_id": s.user_id,
                "sk": s.sk,
                "player_id": s.player_id,
                "server_id": s.server_id,
                "app_id": s.app_id,
                "created": s.created,
                "last_seen": s.last_seen,
            }
            for s in self._sessions.values()
        ]
        tmp = self._session_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._session_file)

    # ---------------- players ----------------
    def get_or_create(self, uid: str, nickname: str = "Sultan") -> Player:
        uid = str(uid or "").strip() or "dev"
        with self._lock:
            if uid in self._players:
                return self._players[uid]
            path = config.PLAYER_DIR / f"{uid}.json"
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    data = default_player(uid, nickname)
            else:
                data = default_player(uid, nickname)
            now = int(time.time())
            data["SysTime"] = now
            data.setdefault("LastLoginTime", now)
            player = Player(uid=uid, data=data, path=path)
            player.save()
            self._players[uid] = player
            return player

    def save(self, player: Player) -> None:
        with self._lock:
            player.save()

    # ---------------- sessions ----------------
    def add_session(self, session: Session) -> None:
        with self._lock:
            self._sessions[session.jwt] = session
            self._by_user.setdefault(session.user_id, []).append(session)
            self._save_sessions()

    def get_session(self, jwt: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(jwt)

    def find_session_for_data(self, jwt: str, data_b64: str) -> Optional[Session]:
        """Resolve a session by JWT, falling back to trying every known SK.

        This makes development forgiving: if the client still carries an older
        JWT after a restart we can still decode its payload.

        The client derives SK with liblua.shareKey() -> OpenSSL.getSharedKey(),
        which base64-encodes the ECDH secret with EVP_EncodeFinal.  OpenSSL's
        EVP_EncodeFinal appends a trailing newline, so the real wire RC4 key is
        ``base64(secret) + "\\n"`` (confirmed against the device's encrypted
        action-10000 request).  Our own key derivation stores the bare base64,
        so try both spellings (plus a stripped spelling for legacy rows) and
        remember which one actually decoded the request: handle_forward must
        encrypt the response with that same key or the client cannot read it.
        """
        with self._lock:
            candidates: list[Session] = []
            if jwt:
                s = self._sessions.get(jwt)
                if s:
                    candidates.append(s)
            for session in self._sessions.values():
                if session not in candidates:
                    candidates.append(session)
        for session in candidates:
            for key in _sk_variants(session.sk):
                try:
                    decode_request_data(key, data_b64)
                    # Record the exact wire key so handle_forward encrypts the
                    # response with the same SK the client used for the request.
                    session.wire_sk = key
                    session.last_seen = time.time()
                    return session
                except Exception:
                    continue
        # Development fallback for build-dev21+: the client's ECDH/OpenSSL
        # bridge produces an explicit-parameter key that does not match the
        # RC4 key derived by PyCA.  That build pins runtime.SK to the literal
        # "sPub"; accept it as a local-only fixed key.
        try:
            decode_request_data("sPub", data_b64)
        except Exception:
            return None
        base = self._sessions.get(jwt)
        return Session(
            jwt=jwt or "dev-fixed",
            user_id=base.user_id if base else "devtest",
            sk="sPub",
            keypair=None,
            player_id=base.player_id if base else "devtest",
            server_id=base.server_id if base else config.SERVER_ID,
            app_id=base.app_id if base else 0,
        )

    def touch(self, session: Session) -> None:
        session.last_seen = time.time()
        self._sessions[session.jwt] = session


def new_jwt() -> str:
    return "dev-" + secrets.token_urlsafe(24)
