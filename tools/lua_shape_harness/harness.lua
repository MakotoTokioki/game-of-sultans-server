-- lua_shape_harness: load a decompiled GoS module in a sandbox, capture the
-- response callback passed to netProtocol.action, and execute it against a
-- server response under pcall with strict nil-read logging.
--
-- Usage: luajit harness.lua <module.lua> <action_id> <response.json> <module_name>
--        luajit harness.lua <module.lua> <action_id> - <module_name>  (reads stdin)
--
-- The module name is inferred from the filename if omitted.

local json_ok, json = pcall(require, "dkjson")
if not json_ok then
  -- minimal JSON decoder sufficient for server response objects
  json = {}
  function json.decode(s)
    local function esc(c) return "\\" .. c end
    -- handle via loadstring with JSON->Lua translation
    local body = s:gsub('"([^"\\]-)"', function(x)
      return '"' .. x:gsub('\\', '\\\\'):gsub('"', '\\"') .. '"'
    end)
    -- fallback: use a tiny recursive parser
    local i = 1
    local function ws() while s:sub(i,i):match('%s') do i=i+1 end end
    local function val()
      ws()
      local c = s:sub(i,i)
      if c == '{' then
        i=i+1; local t={}; ws()
        if s:sub(i,i)=='}' then i=i+1; return t end
        while true do
          ws(); local k=s:match('"([^"]+)"',i); i=i+#k+2; ws(); i=i+1 -- skip :
          t[k]=val(); ws()
          local d=s:sub(i,i); i=i+1
          if d=='}' then break end
        end
        return t
      elseif c=='[' then
        i=i+1; local t={}; ws()
        if s:sub(i,i)==']' then i=i+1; return t end
        while true do
          t[#t+1]=val(); ws()
          local d=s:sub(i,i); i=i+1
          if d==']' then break end
        end
        return t
      elseif c=='"' then
        i=i+1; local out={}
        while i<=#s do
          local ch=s:sub(i,i); i=i+1
          if ch=='"' then break end
          if ch=='\\' then
            local n=s:sub(i,i); i=i+1
            out[#out+1] = n=='n' and '\n' or n=='t' and '\t' or n
          else out[#out+1]=ch end
        end
        return table.concat(out)
      elseif c=='t' then i=i+4; return true
      elseif c=='f' then i=i+5; return false
      elseif c=='n' then i=i+4; return nil end
      local num=s:match('^-?%d+%.?%d*',i); i=i+#num
      return tonumber(num)
    end
    return val()
  end
end

local modfile, action_id, response_file, modname, reqargs_file = ...
assert(modfile and action_id, "usage: harness.lua <module.lua> <action_id> <response.json> [module_name] [request_args.json]")
action_id = tonumber(action_id)

local request_args = nil
if reqargs_file and reqargs_file ~= '-' then
  local f = assert(io.open(reqargs_file, 'r'))
  request_args = json.decode(f:read('*a')); f:close()
end

local response_json
if response_file == '-' then
  response_json = io.read('*a')
else
  local f = assert(io.open(response_file, 'r')); response_json = f:read('*a'); f:close()
end
local response = json.decode(response_json)
if not modname then
  modname = modfile:match('([^/]+)%.lua$') or 'mod'
end

-- ---- config data from disk (real original tables) ----
local CONFIG_DIR = '/tmp/opencode/gos/harness_configs'
local function load_config(name)
  local path = CONFIG_DIR .. '/' .. name .. '.json'
  local f = io.open(path, 'r')
  if not f then return {} end
  local s = f:read('*a'); f:close()
  return json.decode(s) or {}
end

-- ---- sandbox environment ----
local captured = nil
-- permissive object: unknown globals are harmless no-op tables/functions
local permissive
permissive = setmetatable({}, {
  __index = function() return permissive end,
  __call = function() return permissive end,
})
local SAFE = {
  pairs = pairs, ipairs = ipairs, type = type, tostring = tostring,
  tonumber = tonumber, select = select, unpack = unpack, pcall = pcall,
  error = error, assert = assert, next = next, rawget = rawget, rawset = rawset,
  setmetatable = setmetatable, getmetatable = getmetatable,
  math = math, string = string, table = table, os = os, io = io,
}
local env = setmetatable({}, {
  __index = function(_, k) if SAFE[k] ~= nil then return SAFE[k] end return permissive end,
  __newindex = function(t, k, v) rawset(t, k, v) end,
})

local function stubfn(...) return nil end
local function passthrough(...) return ... end
local function first(...) return (...) end

env.json = json
env.netProtocol = {
  action = function(id, payload, callback)
    if tonumber(id) == action_id then
      captured = { id = tonumber(id), payload = payload, callback = callback }
    end
  end,
  actionForDistribute = stubfn,
}
env.tableUtils = setmetatable({}, {__index = function() return function(...) return {...} end end})
env.stringUtils = setmetatable({}, {__index = function() return stubfn end})
env.modManager = {
  dataMod = {
    getTcData = load_config,
    getTbData = function() return load_config('RoleData') end,
    getCfg = function(name) return load_config(name) end,
  },
  roleMod = setmetatable({}, {__index = function() return stubfn end}),
  _wivesMod = setmetatable({}, {__index = function() return stubfn end}),
}
-- generic Corona / engine stubs
env.display = setmetatable({}, {__index = function() return function() return setmetatable({}, {__index=function() return stubfn end}) end end})
env.transition = setmetatable({}, {__index = function() return stubfn end})
env.network = setmetatable({}, {__index = function() return stubfn end})
env.timer = setmetatable({}, {__index = function() return stubfn end})
env.runtime = {}
env.scheduler = setmetatable({}, {__index = function() return stubfn end})
env.audio = setmetatable({}, {__index = function() return stubfn end})
env.system = setmetatable({}, {__index = function() return stubfn end})
env.globalGroup = setmetatable({}, {__index = function() return stubfn end})
env.userData = {}
env.event = setmetatable({}, {__index = function() return stubfn end})
env.gameSetting = setmetatable({}, {__index = function() return stubfn end})
env.platform = setmetatable({}, {__index = function() return stubfn end})
env.DEBUG = false

env.module = function(name, ...)
  local M = {}
  setmetatable(M, {__index = env})
  env[name] = M
  setfenv(2, M)
end

-- ---- load module ----
local ok, err = pcall(function()
  local f, e = loadfile(modfile, 't', env)
  if not f then error(e) end
  f(modname)
end)
if not ok then
  print("MODULE_LOAD_FAIL: " .. tostring(err))
  os.exit(2)
end

local M = env[modname] or {}
local req = M['action' .. action_id]
if type(req) ~= 'function' then
  print("NO_REQUEST_FUNCTION: action" .. action_id .. " (type=" .. type(req) .. ")")
  os.exit(3)
end

-- ---- strict nil-read logging ----
-- NOTE: LuaJIT is Lua 5.1 — pairs()/ipairs() IGNORE __pairs, so we must attach
-- the logging metatable to the REAL response tables (so loops see real data)
-- rather than returning a proxy table.
local nil_reads = {}
local function strict(t, label)
  if type(t) ~= 'table' then return t end
  local mt = getmetatable(t)
  if mt and mt.__gos_strict then return t end
  mt = mt or {}
  mt.__gos_strict = true
  mt.__index = function(_, k)
    local v = rawget(t, k)
    if v == nil then
      nil_reads[#nil_reads + 1] = label .. '.' .. tostring(k)
      return nil
    end
    if type(v) == 'table' then return strict(v, label .. '.' .. tostring(k)) end
    return v
  end
  setmetatable(t, mt)
  -- wrap nested tables now so loops over them get logging too
  for k, v in pairs(t) do
    if type(v) == 'table' then t[k] = strict(v, label .. '.' .. tostring(k)) end
  end
  return t
end

-- ---- call request fn with mock args, capture callback ----
-- Request functions read fields from A0_2 (a table) and pass a response callback
-- to netProtocol.action. If request_args.json is supplied, use those values;
-- otherwise a permissive table (0 for any field) satisfies the reads.
local reqok, reqerr
local function make_arg(v)
  if type(v) == 'table' then
    return setmetatable(v, {__index = function() return 0 end})
  end
  return v
end
if request_args then
  local call_args = {}
  for i, v in ipairs(request_args) do call_args[i] = make_arg(v) end
  call_args[#call_args + 1] = function(...) end
  reqok, reqerr = pcall(req, unpack(call_args))
else
  local dryargs = setmetatable({}, {__index = function() return 0 end})
  reqok, reqerr = pcall(req, dryargs, function(...) end)
end
if not reqok then
  print("REQUEST_ERROR: " .. tostring(reqerr))
end
local cb = captured and captured.callback

if not cb then
  print("NO_CALLBACK_CAPTURED for action " .. action_id)
  os.exit(4)
end

-- ---- execute response callback under pcall ----
if os.getenv('HARNESS_DEBUG') == '1' then
  print('DEBUG response type=' .. type(response))
  if type(response) == 'table' then
    for k, v in pairs(response) do
      print('DEBUG resp.' .. tostring(k) .. ' = ' .. type(v) .. (type(v)=='table' and ' #'..#v or ''))
    end
  end
  local i = 1
  while true do
    local n, v = debug.getupvalue(cb, i)
    if not n then break end
    print('DEBUG upvalue ' .. i .. ' ' .. n .. ' = ' .. type(v))
    i = i + 1
  end
  local n5, l5 = debug.getupvalue(cb, 5)
  local n4, l4 = debug.getupvalue(cb, 6)
  local gain = response.GainActivityItem
  local ok5, r5 = pcall(l5, gain)
  local ok4, r4 = pcall(l4, gain)
  print('DEBUG l5(firstkey) on gain: ok=' .. tostring(ok5) .. ' result=' .. tostring(r5))
  print('DEBUG l4(pairsofpairs) on gain: ok=' .. tostring(ok4) .. ' err=' .. tostring(r4))
  -- instrument: wrap upvalues to log if the callback reaches them
  local orig4 = l4
  debug.setupvalue(cb, 6, function(x)
    print('DEBUG callback reached L4_1 with ' .. type(x))
    return orig4(x)
  end)
end
local sr = strict(response, 'resp')
local cbok, cberr = pcall(cb, sr)

print("ACTION=" .. action_id .. " MODULE=" .. modname)
if not cbok then
  print("CALLBACK_ERROR: " .. tostring(cberr))
else
  print("CALLBACK_OK")
end
if #nil_reads > 0 then
  print("NIL_READS(" .. #nil_reads .. "): " .. table.concat(nil_reads, ", "))
else
  print("NIL_READS: none")
end
