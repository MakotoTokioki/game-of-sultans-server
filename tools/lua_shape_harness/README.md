# Lua Shape Harness

Validates that a server response has the **exact shape the v2.005 client expects**,
without running the full game or a device. This is the foundation of the
"client-as-oracle" approach for implementing the remaining ~415 actions.

## Why

The client is brittle Lua: a missing key, a list where a table is expected, or
a number where a table is expected makes the response callback throw mid-way
(Corona catches it as `unhandledError`, so the UI silently fails). We can run
the *real decompiled response callback* in a sandbox and detect exactly that.

## PoC validation (2026-11-20)

Action `11302` (daily-task claim). The device bug was that `GainActivityItem`
was returned as a flat list `[[5,10]]`; the client callback's pairs-of-pairs
helper then indexed a bare number and aborted. The harness reproduces it:

```
$ luajit harness.lua <_dailyTaskMod.lua> 11302 resp_flat.json _dailyTaskMod reqargs.json
CALLBACK_ERROR: .../_dailyTaskMod.lua:106: attempt to index local 'L11_2' (a number value)
```

and passes with the fixed map shape `{"90000": [[5,10]]}`:

```
CALLBACK_OK
NIL_READS: none
```

A response with `GainActivityItem` missing entirely reports:
`NIL_READS(2): resp.GainActivityItem, resp.GainActivityItem`.

## Design

- `harness.lua` — loads a decompiled module in a sandbox with mocked globals
  (`netProtocol`, `modManager`, `tableUtils`, Corona stubs), hooks
  `netProtocol.action(NNNNN, ...)` to capture the response callback, then calls
  it under `pcall` with:
  - the server response, wrapped with a metatable that logs every nil field
    read (`NIL_READS`) — this catches *silent* missing fields, not just hard
    errors;
  - real config tables from `/tmp/opencode/gos/harness_configs/*.json`
    (generated from the original decompiled configs) fed to
    `modManager.dataMod.getTcData(name)`.

- **Lua 5.1 / LuaJIT note:** `pairs()` ignores `__pairs`, so the nil logger is
  attached to the real response tables rather than returned as a proxy table.

- `run.py` — driver: builds an action→module index from the decompiled tree,
  gets sample responses from our handlers (or a fixture), runs the harness,
  and emits reports.

## Usage

```bash
# regenerate config JSON (needed once, or after a decomp change)
python3 tools/lua_shape_harness/run.py --regen-configs

# one action, response from our server handler
python3 tools/lua_shape_harness/run.py --action 11302

# one action with a known-good fixture
python3 tools/lua_shape_harness/run.py --action 11302 \
    --response /tmp/opencode/gos/resp_11302_fixed.json --args '[2]'

# all implemented actions -> recon/protocol/shape-report-implemented.md
python3 tools/lua_shape_harness/run.py --all-implemented

# all unimplemented actions, empty response -> required fields
# -> recon/protocol/shape-required-fields.md
python3 tools/lua_shape_harness/run.py --scan-missing
```

## Interpreting results

- `OK` + no nil reads — response shape matches what the client reads.
- `ERROR` — the callback threw; check whether it's a real shape bug or a
  harness mock limitation (e.g. a helper function stubbed with `permissive`).
- `NIL_READS` — fields the client reads that our response didn't provide.
  For optional fields (`if X then ... end`) these are harmless; for required
  ones they are the exact TODO list.
- `NO_CALLBACK` / `NO_REQ_FN` / `MODULE_LOAD_FAIL` / `NO_MODULE` — harness
  coverage gaps, not necessarily client bugs. These are tracked for improvement.

## Current status

PoC validated on 11302. The bulk sweeps currently have false positives from
simplified mocks and unrealistic auto-generated responses; they are useful as
a field-discovery aid, not yet a strict CI gate. Improving mock fidelity and
feeding golden responses is ongoing work.
