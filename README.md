# Game of Sultans dev server

**Built for client version 2.005 of Game of Sultans** (`com.dc.hwsj`). The
version comes from the decompiled client's `macro.lua`, which sets
`VERSION = 2.005`.

## What this project is, in plain words

This is a private server for the mobile game *Game of Sultans*. The real game
normally talks to the publisher's servers. This server takes their place, so
you can install the game on a phone or emulator, point it here, and play it as
a small private world.

You can create a sultan, collect taxes, upgrade buildings and advisers, take
care of your court, join an alliance, fight battles, take part in events and
buy diamonds. Buying is only simulated: the server gives you the diamonds and
VIP levels from the game's own data, and no real money is charged.

The server has no graphics of its own. It only answers the requests the game
sends, using the same network format as the real server. The shape of each
answer was taken from the decompiled 2.005 client, and the numbers, items and
rewards come from the game's original config files wherever possible. It is a
development and research project, not a service for public use.

## What is implemented

- `/frontend/jwt_action` — action 9999/9998
  - P-256 ECDH with the client's `c_pub` (same as `libopenssl.so`)
  - `SK = base64(ECDH shared secret)`
  - issues a dev JWT, returns `sPub`, `Sess`, `SysTime`
- `/frontend/distribute` — actions 9004/9005/9006
  - action 9004 returns a one-server `Root.servers` list
- `/frontend/forward` — encrypted actions
  - request: `base64 -> RC4(SK) -> JSON`
  - response: `JSON -> gzip -> base64 -> RC4(SK) -> base64`
  - envelope: `{"stat":1,"data":"{\"Code\":0,\"Data\":\"...\",\"Sess\":\"...\",\"SysTime\":...}"}`
- action 10000 — full login payload (`RoleBase`, `RoleData`, `RoleSection`,
  `ExtraData`, `RoleItem`, activity placeholders, server time)
- action 10001/10002/10003 — idle levy/recover/war buff timestamps
- action 10010/10011/10013/10015/10018 — level, rename, create role, main task,
  status
- action 10201/10202 — stage info and a permissive battle result
- unknown actions return `{}` and are logged

Player state is stored under `data/players/<uid>.json`.
Sessions (JWT + RC4 key) are stored in `data/sessions.json` so the server can
survive a restart while the client is still running.

Request logs:
- `logs/requests.jsonl` — outer HTTP request/response
- `logs/actions.jsonl` — decoded action + payload for `/forward`

## Run

```sh
cd server
python3 -m pip install -r requirements.txt   # cryptography
GOS_PORT=8088 GOS_PUBLIC_BASE_URL=http://gos-dev-server.local:8088/frontend/ \
  python3 -m gos.app
```

or `GOS_PORT=8088 ./run.sh`. `GOS_PUBLIC_BASE_URL` is what action 9004
advertises back to the client; set it to the same address the APK was patched
to use.

Environment variables (see `gos/config.py`): `GOS_PORT`,
`GOS_PUBLIC_BASE_URL`, `GOS_MUSIC_BASE_URL`, `GOS_SERVER_ID`,
`GOS_SERVER_NAME`, `GOS_DATA_DIR`, `GOS_LOG_DIR`, and `GOS_PG_DSN`.
PostgreSQL credentials are supplied only through `GOS_PG_DSN` in the service
environment — the committed default contains no password. The service here
uses a systemd drop-in at
`/etc/systemd/system/gos-server.service.d/override.conf` (not part of this
repo). Set `GOS_PG_DSN=""` to disable the PostgreSQL mirror and use JSON files
in `data/players/` only.

Runtime data (`data/players/`, `logs/`) and Python caches are intentionally
not committed; see `.gitignore`.

Health check: `curl http://127.0.0.1:8088/health`.

## Test without the APK

`sim_client.py` performs the real 9999 -> 9004 -> 10000 sequence and prints the
decoded responses:

```sh
python3 sim_client.py --host http://127.0.0.1:8088 --user devtest
```

It is the reference implementation of the wire format if the server needs to be
ported or debugged.

## APK tools

- `../apk_patch/patch_urls.py` — rewrite production frontend URLs + sign APK.
- `../apk_patch/rebuild_car.py` — parse/build `resource.car`, replace chunks.
- `../apk_patch/lua_chunk.py` — Lua 5.1 chunk parser/instruction patcher.
- A 32-bit `luac` is needed to recompile modified chunks:
  `curl -O https://www.lua.org/ftp/lua-5.1.5.tar.gz`, then
  `make -C src luac CC='gcc -m32' MYCFLAGS='-m32'`.

## Current gaps / next work

The client is a full live-service RPG (~500 action ids). A useful private shard
does not need all of them. The next milestones are:

1. Bring the real APK up against this server (patch base URLs + SDK login).
2. Capture the real client's first 50-100 action payloads and implement the
   response handlers it reaches.
3. Fill `__login` module state: `RoleCounsellor`, `RoleBeauty`,
   `OChildren`, `Activity`, `RedPoint`, `MainTask`.
4. Implement the idle/city economy (levy, building output, offline accrual).
5. Implement stage battle math and task rewards from `TcPhaseCfg` /
   `TcSectionCfg` / `TcMainTask` rather than the permissive stubs.

## APK notes

- The client talks to the base URL `.../frontend/`; `netTestMod.lu` contains the
  region list and `urlTools.lu` the update/notice URLs.
- `apk_patch/patch_urls.py` rewrites the three 42-byte production URLs in
  `resource.car` to a 42-byte dev URL and re-signs the APK. Default target:
  `http://gos-dev-server.local:8088/frontend/` (add that name to DNS/hosts).
  Tested byte-for-byte: it patches 3 occurrences and produces a signed APK.
- The SDK login flow (`longe9API.init` -> `sdks.scene.cutscene`) still needs to
  be bypassed or emulated before the patched APK can reach this server. The
  client-side options are: pass `true` to `loginMod.accountInit` (its direct
  login branch), or patch the `L4_2` callback in `loginMod` to call the login
  callback instead of `longe9API.init`.
- `resource.car` stores standard Lua 5.1 bytecode (32-bit `size_t`). Modified
  chunks can be recompiled with a 32-bit `luac`; `apk_patch/rebuild_car.py`
  round-trips the archive and can replace individual chunks.
- `apk_patch/lua_chunk.py` is a small Lua 5.1 chunk parser used to locate and
  patch individual instructions (e.g. `ISUPDATE` or the `accountInit` branch).
