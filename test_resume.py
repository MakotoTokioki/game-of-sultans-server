import json, time, urllib.request
from gos.crypto import b64e, rc4, decode_response_data

sessions = json.load(open('data/sessions.json'))
s = sessions[-1]
jwt, sk = s['jwt'], s['sk']
payload = {
    "actionid": 10000, "ActId": 10000, "Ver": "2.0.05", "serverId": 1,
    "appId": 1021, "Sess": jwt, "Token": jwt, "Lang": "en",
    "T": int(time.time()), "HotVersion": "2.0.05", "RetailId": 1021,
}
raw = json.dumps(payload, separators=(",", ":")).encode()
body = {"version": "1.8", "jwt": jwt, "server_id": 1, "app_id": 1021,
        "data": b64e(rc4(sk, raw))}
req = urllib.request.Request("http://127.0.0.1:808/frontend/forward",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req) as r:
    outer = json.loads(r.read())
inner = decode_response_data(sk, outer["data"])  # /forward data is the encrypted envelope
print("Code", inner["Code"])
print("RoleBase", inner["Data"]["RoleBase"]["Nickname"])
