"""Regression: the real v2 client derives its RC4 session key with
liblua.shareKey() -> OpenSSL.getSharedKey(), whose EVP_EncodeFinal appends a
trailing newline to the base64 ECDH secret.  Our crypto.py stores the bare
base64, so handle_forward must match *and reply with* the newline spelling.

Without this the device's action-10000 request is answered with
{"stat":0,"info":"unknown session"} and "Tap to start" never appears.
"""
from __future__ import annotations

import base64
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gos import config, handlers
from gos.crypto import b64e, decode_response_data, rc4
from gos.defaults import default_player
from gos.store import Player, Session, Store

OK = []


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="gos_sk_newline_"))
    old_data = config.DATA_DIR
    config.DATA_DIR = tmp
    try:
        store = Store()
        uid = "sk_newline_test"
        bare_sk = base64.b64encode(b"x" * 32).decode()  # what crypto.py stores
        jwt = "dev-sk-newline"
        session = Session(jwt=jwt, user_id=uid, sk=bare_sk, keypair=None,
                          player_id=uid, server_id=1, app_id=1020)
        store.add_session(session)

        # Client encrypts with base64(secret) + "\n" (EVP_EncodeFinal newline).
        wire_sk = bare_sk + "\n"
        payload = {"actionid": 10000, "ActId": 10000, "UId": uid,
                   "Token": jwt, "Lang": "en"}
        enc = b64e(rc4(wire_sk, b'{"actionid":10000,"ActId":10000,"UId":"sk_newline_test"}'))
        resp = handlers.handle_forward(
            {"jwt": jwt, "server_id": 1, "app_id": 1020, "data": enc}, store)

        check("newline SK is accepted (not 'unknown session')",
              resp.get("stat") == 1, resp)
        if resp.get("stat") == 1:
            inner = decode_response_data(wire_sk, resp["data"])
            check("response decrypts with the client's newline SK",
                  inner.get("Code") == 0 and isinstance(inner.get("Data"), dict),
                  inner)
            check("action 10000 reached the handler",
                  store.find_session_for_data(jwt, enc) is not None)
        # The bare (test/sim-client) spelling must keep working too.
        enc_bare = b64e(rc4(bare_sk, b'{"actionid":10000}'))
        resp_bare = handlers.handle_forward(
            {"jwt": jwt, "server_id": 1, "app_id": 1020, "data": enc_bare}, store)
        check("bare SK still accepted (test harness compatibility)",
              resp_bare.get("stat") == 1, resp_bare)
    finally:
        config.DATA_DIR = old_data

    print(f"\n{sum(OK)}/{len(OK)} passed")
    if not all(OK):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
