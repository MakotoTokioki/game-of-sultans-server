"""Regression test: SDK route responses must be accepted by sdks/netModule.lua
so that setRoute() populates ROUTEINFO for loginScene.getServerInfoById.

Failure being fixed (phone logcat, every login-scene entry):
  attempt to index a nil value
    at getServerInfoById -> setServerText -> dispatchEvent -> gotoScene

Root cause: the SDK route actions carry a top-level `actionid`, so the old
handle_distribute envelope was {"Code","Data","Sess","SysTime"} -- no Stat/stat
and Root nested under Data.  sdks/netModule.lua 128-139 treats any response
without Stat==0/stat==1 as a FAILURE and skips successListener, so setRoute was
never called; getRoute() returned nil and getServerInfoById crashed indexing
getRoute().Root.servers.

Run: python3 test_sdk_route.py
"""
from __future__ import annotations

import json
import sys

from gos import handlers, config
from gos.store import Store

ok: list[bool] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    ok.append(bool(cond))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def _route_inner(act: int, app_id: int = 1020) -> dict:
    body = {
        "isSend": True, "version": "1.8", "actionid": act,
        "data": json.dumps({"ActionId": act}), "app_id": app_id,
        "act_id": f"hwsj_route_{app_id}", "Lang": "en",
    }
    outer = handlers.handle_distribute(body, Store())
    return json.loads(outer["data"])


def test_sdk_netmodule_success_check() -> None:
    """Replicate sdks/netModule.lua 128-139 acceptance condition."""
    for act in (1011, 9002, 9004, 9005):
        inner = _route_inner(act)
        Stat, stat = inner.get("Stat"), inner.get("stat")
        sdk_failure = (Stat != 0 and stat != 1)
        check(f"{act} inner has Stat==0 or stat==1 (SDK successListener runs)",
              not sdk_failure, (Stat, stat))


def test_route_shape() -> None:
    """route successListener (routeAction.lua 453-486) reads Root.servers."""
    for act in (1011, 9002, 9004, 9005):
        inner = _route_inner(act)
        check(f"{act} Root at top level of decoded inner", "Root" in inner,
              list(inner.keys()))
        servers = inner.get("Root", {}).get("servers")
        check(f"{act} Root.servers is a non-empty array",
              isinstance(servers, list) and len(servers) >= 1 and
              isinstance(servers[0], dict), servers)
        s0 = servers[0] if servers else {}
        # Fields official v7 9004 server row carries that the client reads.
        for field in ("id", "state", "priority", "name", "mergeIds"):
            check(f"{act} server row has {field}", field in s0, s0)
        check(f"{act} server state=1 (survives getIsHideUnknowServer filter)",
              int(s0.get("state") or 0) == 1, s0)


def test_v7_top_level_fields() -> None:
    """Official 9004 Data has these fields at the object's top level."""
    inner = _route_inner(9004)
    for field in ("Root", "LastRegionId", "SysTime"):
        check(f"9004 top-level {field}", field in inner, field)
    check("9004 has Info string (SDK fail path reads it)",
          isinstance(inner.get("Info"), str), inner.get("Info"))


def main() -> None:
    test_sdk_netmodule_success_check()
    test_route_shape()
    test_v7_top_level_fields()
    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    sys.exit(0 if all(ok) else 1)


if __name__ == "__main__":
    main()
