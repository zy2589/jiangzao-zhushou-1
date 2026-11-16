-- Resolve menu bridge for the portable local copy of 降噪助手.
-- If LuaJIT FFI is available, launch the local VBS without a console window.
-- Sandboxed Lua signals the already-running hidden assistant via Resolve's log.
local launcher = __LAUNCHER_VBS__
local marker = "RAA_AUDIO_START_REQUEST_V1"

local function launch_with_ffi(runtime)
    pcall(runtime.cdef, "typedef unsigned long long RAA_UINTPTR;")
    pcall(runtime.cdef, [[
        int MultiByteToWideChar(unsigned int codePage, unsigned long flags,
            const char* input, int inputLength, unsigned short* output, int outputLength);
        void* ShellExecuteW(void* hwnd, const unsigned short* operation,
            const unsigned short* file, const unsigned short* parameters,
            const unsigned short* directory, int showCommand);
    ]])

    local function wide(value)
        local count = runtime.C.MultiByteToWideChar(65001, 0, value, -1, nil, 0)
        if count <= 0 then error("无法转换启动路径。") end
        local buffer = runtime.new("unsigned short[?]", count)
        if runtime.C.MultiByteToWideChar(65001, 0, value, -1, buffer, count) <= 0 then
            error("无法转换启动路径。")
        end
        return buffer
    end

    local shell32 = runtime.load("shell32")
    local params = '//B //NoLogo "' .. launcher .. '"'
    local result = shell32.ShellExecuteW(nil, nil, wide("wscript.exe"), wide(params), nil, 0)
    if tonumber(runtime.cast("RAA_UINTPTR", result)) <= 32 then
        error("Windows 没有启动降噪助手。")
    end
end

local runtime = rawget(_G, "ffi")
if not runtime and type(require) == "function" then
    local ok, loaded = pcall(require, "ffi")
    if ok then runtime = loaded end
end

local ffi_error = nil
if runtime then
    local ok, result = pcall(launch_with_ffi, runtime)
    if ok then return end
    ffi_error = result
end

-- The local helper watches only this fixed marker in ResolveDebug.txt.
if ffi_error and type(print) == "function" then
    pcall(print, "[降噪助手] direct launch failed: " .. tostring(ffi_error))
end
error(marker, 0)
