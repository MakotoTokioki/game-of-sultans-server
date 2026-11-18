"""Verify the newly implemented actions (10101/10901/10905/10906/10907/11801/11808/11810/16003/16004).

Speaks the same wire protocol as sim_client.py.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.request

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from gos.crypto import b64e, rc4, decode_response_data

HOST = "http://127.0.0.1:808"
USER = "verify%d" % int(time.time())  # fresh account each run (500k starting silver)


def post(url: str, body: dict) -> dict:
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> None:
    base = HOST.rstrip("/") + "/frontend/"
    client_key = ec.generate_private_key(ec.SECP256R1())
    c_pub = client_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    r = post(base + "jwt_action", {
        "version": "1.8", "c_pub": c_pub, "server_id": 1, "act_id": "9999",
        "jwt": "", "user_id": USER, "app_id": 1021,
    })
    inner = json.loads(r["data"])
    jwt, server_pub = inner["jwt"], serialization.load_pem_public_key(inner["sPub"].encode())
    sk = base64.b64encode(client_key.exchange(ec.ECDH(), server_pub)).decode()

    def forward(action: int, payload: dict | None = None):
        p = {"actionid": action, "ActId": action, "Ver": "2.005", "serverId": 1,
             "appId": 1020, "Sess": jwt, "Lang": "tr", "T": int(time.time()), "RetailId": 2}
        if payload:
            p.update(payload)
        enc = b64e(rc4(sk, json.dumps(p, ensure_ascii=False, separators=(",", ":")).encode()))
        resp = post(base + "forward", {"version": "1.8", "jwt": jwt, "server_id": 1,
                                       "app_id": 1020, "data": enc})
        # /forward returns the encrypted inner envelope directly in "data"
        outer = decode_response_data(sk, resp["data"])
        assert outer.get("Code") == 0, outer
        return outer["Data"]

    ok = []

    def check(name, cond, detail=""):
        ok.append(cond)
        print(("PASS" if cond else "FAIL"), name, detail if not cond else "")

    d = forward(10000)  # login
    before_silver = d["RoleData"]["Silver"]
    before_lv = next(c["Lv"] for c in d["RoleCounsellor"] if c["CounsellorId"] == 1)

    # 10901 assign minister 2 -> position 3
    d = forward(10901, {"PositionAndMinisterId": [{"MinisterId": 2, "Position": 3}]})
    check("10901 assigns", d.get("DataList") and d["DataList"][0]["CounsellorId"] == 2
          and d["DataList"][0]["StudyPosition"] == 3 and "STUnix" in d["DataList"][0], d)

    # 10905 list reflects persistence
    d = forward(10905)
    check("10905 list persisted", d.get("DataList") and d["DataList"][0]["CounsellorId"] == 2
          and d.get("Capacity") == 5, d)

    # 10906 study array
    d = forward(10906, {"StudyArray": [1, 2, 3]})
    check("10906 echoes", d.get("StudyArray") == [1, 2, 3], d)

    # 10907 is now the "Dispatch All" button: it returns only the counsellors
    # newly seated from the 10906 lineup (2 is already seated at position 3).
    d = forward(10907)
    check("10907 dispatch seats the pending lineup",
          {e["CounsellorId"] for e in d.get("DataList", [])} == {1, 3}, d)

    # 10101 upgrade counsellor 1
    d = forward(10101, {"CounsellorId": 1, "IsUpTen": False})
    new_lv = d.get("CounsellorLv")
    new_silver = d.get("CounsellorSilver")
    check("10101 shape", isinstance(new_lv, int) and isinstance(new_silver, int)
          and "MiracleTrigger" in d, d)
    # Sultan Boost (15%) can add +2 free bonus levels, so Lv rises 1..3.
    check("10101 leveled (1..3 with Sultan Boost)",
          before_lv < new_lv <= before_lv + 3, (before_lv, new_lv))
    check("10101 MiracleTrigger is a real boolean",
          isinstance(d.get("MiracleTrigger"), bool), d.get("MiracleTrigger"))

    # 10101 x10
    d = forward(10101, {"CounsellorId": 1, "IsUpTen": True})
    check("10101 up10", d["CounsellorLv"] >= new_lv + 1, d)

    # persistence: re-login (no further mutations in between)
    d = forward(10000)
    c1 = next(c for c in d["RoleCounsellor"] if c["CounsellorId"] == 1)
    d2 = forward(10000)
    c2 = next(c for c in d2["RoleCounsellor"] if c["CounsellorId"] == 1)
    check("10101 persisted", c2["Lv"] == c1["Lv"] and c2["NowSilver"] == c1["NowSilver"], (c1, c2))
    check("10101 silver deducted", d2["RoleData"]["Silver"] < before_silver,
          (before_silver, d2["RoleData"]["Silver"]))
    check("10906 persisted", d2["RoleData"]["StudyArray"] == [1, 2, 3], d2["RoleData"]["StudyArray"])
    slots = d2.get("School", {}).get("Slots", {})
    check("10901 persisted", slots.get("3", {}).get("MinisterId") == 2, slots)

    # instance actions
    d = forward(11801, {"updataTesting": 1})
    check("11801 shape", isinstance(d.get("BossSingleUTC"), list) and len(d["BossSingleUTC"]) == 2
          and isinstance(d.get("BossGlobalUTC"), list)
          and d.get("BossSingleComplete") == 0
          and isinstance(d.get("DamageRankAward"), dict) and "RemainList" in d["DamageRankAward"]
          and isinstance(d.get("BossGlobalDead"), dict), d)

    d = forward(11808)
    check("11808 shape", "ScoreRank" in d and "KillRank" in d and "RoleScore" in d
          and "RoleScoreRank" in d, d)

    d = forward(11810)
    check("11810 shape", "RoleScore" in d and "ItemList" in d, d)

    # binding actions
    d = forward(16003)
    check("16003 shape", d.get("IsBinding") == 0, d)
    d = forward(16004)
    check("16004 shape", d.get("AccountList") == [], d)

    print("\n%d/%d checks passed" % (sum(ok), len(ok)))


if __name__ == "__main__":
    main()
