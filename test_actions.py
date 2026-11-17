import json
import time
import urllib.request
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from gos.crypto import b64e, rc4, decode_response_data

BASE = "http://127.0.0.1:808/frontend/"


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def main():
    key = ec.generate_private_key(ec.SECP256R1())
    c_pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    r = post(BASE + "jwt_action", {"version": "1.8", "c_pub": c_pub, "server_id": 1,
                                   "act_id": "9999", "jwt": "", "user_id": "flowtest", "app_id": 1021})
    inner = json.loads(r["data"])
    jwt = inner["jwt"]
    sk = base64.b64encode(key.exchange(ec.ECDH(), serialization.load_pem_public_key(inner["sPub"].encode()))).decode()

    def forward(action, payload=None):
        p = {"actionid": action, "ActId": action, "Ver": "2.0.05", "serverId": 1,
             "appId": 1021, "Sess": jwt, "Token": jwt, "Lang": "en",
             "T": int(time.time()), "HotVersion": "2.0.05", "RetailId": 1021}
        if payload:
            p.update(payload)
        enc = b64e(rc4(sk, json.dumps(p, separators=(",", ":")).encode()))
        resp = post(BASE + "forward", {"version": "1.8", "jwt": jwt, "server_id": 1,
                                       "app_id": 1021, "data": enc})
        outer = decode_response_data(sk, resp["data"])  # /forward data is the encrypted envelope
        assert outer.get("Code") == 0, outer
        return outer["Data"]

    data = forward(10000)
    print("10000 keys:", len(data), "RoleBase" in data, "LevyTimeUTC", data["RoleData"]["LevyTimeUTC"])
    data = forward(10000)
    print("10000 keys:", len(data), "RoleBase" in data, "LevyTimeUTC", data["RoleData"]["LevyTimeUTC"])
    for action, payload in [
        (10001, {"CollectType": "login_manageAssets"}),
        (10002, {"RecoverType": 1, "RecoverTimes": 1}),
        (10003, {}),
        (10015, {}),
        (10202, {}),
        (10010, {"Rid": "flowtest"}),
        (10011, {"NewName": "FlowSultan"}),
        (10013, {"Rid": "flowtest", "Sex": 1, "Avatar": 1}),
        (10301, {}),
        (10401, {}),
    ]:
        d = forward(action, payload)
        print(action, "->", sorted(d.keys())[:8])


if __name__ == "__main__":
    main()
