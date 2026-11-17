import json, urllib.request

BASE = "http://127.0.0.1:808/frontend/distribute"


def post(body):
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


for action, nested in [(9002, {"ActionId": 9002}), (1105, {"ActionId": 1105, "Account": "dev", "Platform": "guest"}),
                       (1011, {"ActionId": 1011}), (1006, {"ActionId": 1006})]:
    body = {"isSend": True, "version": "1.8", "app_id": 1020,
            "data": json.dumps(nested), "act_id": f"hwsj_route_1020", "Lang": "en"}
    r = post(body)
    inner = json.loads(r["data"])
    print(action, "Stat" in inner, inner.get("Stat"), inner.get("stat"), sorted(inner.keys())[:12])
