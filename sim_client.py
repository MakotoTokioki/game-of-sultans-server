"""A tiny client that speaks the same wire protocol as the APK.

Useful for validating the server without installing the 32-bit ARM APK.
It intentionally follows the decompiled netSendMod/dataNetMod pipeline.
"""

from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.request

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from gos.crypto import b64e, rc4, decode_response_data


def post(url: str, body: dict) -> dict:
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://127.0.0.1:8080")
    ap.add_argument("--user", default="devtest")
    ap.add_argument("--name", default="Sultan")
    args = ap.parse_args()
    base = args.host.rstrip("/") + "/frontend/"

    client_key = ec.generate_private_key(ec.SECP256R1())
    c_pub = client_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    print("== 9999 create session ==")
    r = post(base + "jwt_action", {
        "version": "1.8",
        "c_pub": c_pub,
        "server_id": 1,
        "act_id": "9999",
        "jwt": "",
        "user_id": args.user,
        "app_id": 1021,
    })
    print(json.dumps(r, indent=2)[:1200])
    inner = json.loads(r["data"])
    jwt = inner["jwt"]
    server_pub = serialization.load_pem_public_key(inner["sPub"].encode())
    sk = base64.b64encode(client_key.exchange(ec.ECDH(), server_pub)).decode()
    print("jwt:", jwt, "sk:", sk)

    print("\n== 9004 server list (distribute) ==")
    nested = {
        "UId": args.user,
        "Version": "2.0.05",
        "RetailId": 1021,
        "DeviceId": "dev-device",
        "DeviceType": "android",
        "LoginType": 1,
        "IsAllServers": 0,
        "HotVersion": "2.0.05",
    }
    r = post(base + "distribute", {
        "isSend": True,
        "version": "1.8",
        "app_id": 1021,
        "data": json.dumps(nested),
        "act_id": "hwsj_route_1021",
        "actionid": 9004,
        "Lang": "en",
    })
    print(json.dumps(r, indent=2)[:1600])

    def forward(action: int, payload: dict | None = None):
        p = {
            "actionid": action,
            "ActId": action,
            "Ver": "2.0.05",
            "serverId": 1,
            "appId": 1021,
            "Sess": jwt,
            "Token": jwt,
            "Lang": "en",
            "T": int(time.time()),
            "HotVersion": "2.0.05",
            "RetailId": 1021,
        }
        if payload:
            p.update(payload)
        raw = json.dumps(p, ensure_ascii=False, separators=(",", ":")).encode()
        enc = b64e(rc4(sk, raw))
        body = {"version": "1.8", "jwt": jwt, "server_id": 1, "app_id": 1021, "data": enc}
        resp = post(base + "forward", body)
        outer = decode_response_data(sk, resp["data"])  # /forward data is the encrypted envelope
        if outer.get("Code") != 0:
            print("error:", outer)
            return None
        data = outer["Data"]
        print(f"\n== {action} ==")
        print(json.dumps(data, indent=2)[:2200])
        return data

    forward(10000)
    forward(10202)
    forward(10015)


if __name__ == "__main__":
    main()
