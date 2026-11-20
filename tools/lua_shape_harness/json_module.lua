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

