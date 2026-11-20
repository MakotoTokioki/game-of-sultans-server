# Handler implementation spec (read before writing any handler)

We are reimplementing Game of Sultans v2.005 server handlers. Follow this EXACTLY.

## Ground rules
- **NEVER invent numbers.** Every reward/cost/threshold MUST come from an original
  v2 config table (`gos/v2configs_data/settings.*.lua`, loaded via
  `v2configs.load("Name")`) or from the request payload echoed back. If a needed
  table is missing, copy it from `/tmp/opencode/gos/decomp_all/configs/settings.NAME.lua`
  into `gos/v2configs_data/` (only if `v2configs.parse_lua_table_file` parses it).
- **Client shape is law.** The decompiled client response callback reads exact
  field names/types. Discover them with `python3 tools/lua_shape_harness/extract_fields.py ACTION`
  and cross-check in the client module under `/tmp/opencode/gos/decomp_all/modules/`.
  Missing/mistyped fields make the client callback throw (silent UI failure).
- **Lua-safe shapes:** tables where the client `pairs()`/indexes them, never nil;
  arrays as lists, maps as dicts; real ints/bools. Empty `{}` is safe for optional.
- No client edits. Server-side only.

## Files to touch
1. `gos/handlers.py` — add `def action_xxx(payload, player, session, store) -> dict`
   near related handlers, and register the action id in the `HANDLERS` dict at bottom.
2. `gos/defaults.py` — only if a new persistent state slice is needed; seed it in
   `default_player()`. Prefer storing under an existing slice (`RoleData`, `ExtraData`,
   `RoleSection`, etc.) to avoid broad changes.
3. `test_<cluster>.py` — regression tests asserting exact response keys/types and
   config-derived values. Use the pattern in `test_campaign_102xx.py` (Player,
   default_player, SimpleNamespace store stub, check(), final `assert all(ok)`).

## Reusable helpers in handlers.py
- `v2configs.load("TableName")` -> list of dict rows (already imported)
- `_as_int(x)` — safe int conversion
- `attach_remain_list(data, player, items=..., resources=[...])` — for grant actions;
  `resources` = item ids 1-6 actually changed (avoid bogus +0 rows)
- `_grant_award_pairs(player, award_pairs)` -> (pairs, bag_ids)
- `action_full_state(payload, player, session, store)` — permissive fallback; use only
  when the client reads many unrelated login fields (prefer explicit shapes)
- `_now()` server clock, `_phase_cfg`, `_section_record` for campaign.

## Common pattern (query handler)
```python
def action_xxx(payload, player, session, store):
    # read any request fields (echo or validate)
    # build response from config rows / player state
    return {"FieldA": value, "FieldB": [...]}
```

## Common pattern (grant/consume handler)
```python
def action_xxx(payload, player, session, store):
    # validate cost/prereq from config
    # mutate player.data, persist via store.save(player)
    pairs, bag_ids = _grant_award_pairs(player, award)
    return attach_remain_list({"FieldA": value}, player, items=bag_ids,
                              resources=[int(p[0]) for p in pairs if int(p[0]) in _REMAIN_LIST_MAP])
```

## Validation before you finish
- `python3 -m py_compile gos/handlers.py` must pass.
- `python3 test_<your>.py` must print `N/N checks passed` with no FAIL.
- Run `python3 tools/lua_shape_harness/extract_fields.py ACTION` for each action and
  confirm every listed field is present in your response (optional fields whose
  absence is guarded by `if X then` may be omitted, but prefer including them).
- Do NOT run ADB/device tests. Do NOT restart the server. Do NOT commit — the
  coordinator reviews, runs the full suite, and commits.

## Reporting
Return: (1) actions implemented, (2) config tables used, (3) any field you could
not source from config (with what you did instead — must be documented), (4) test
results, (5) any server-authoritative data needing research/simulation.
