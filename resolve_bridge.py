from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable
from ctypes import wintypes


APP_DIR = Path(__file__).resolve().parent
BRIDGE_REPO = APP_DIR / "tools" / "resolve-console-bridge"
BRIDGE_CONFIG = Path.home() / ".config" / "davinci-resolve-mcp" / "bridge.json"
BRIDGE_LUA = Path.home() / ".config" / "davinci-resolve-mcp" / "console-bridge" / "resolve_bridge.lua"
RESOLVE_USER_SCRIPTS = Path(os.environ.get("APPDATA", Path.home())) / "Blackmagic Design" / "DaVinci Resolve" / "Support" / "Fusion" / "Scripts" / "Utility"
RESOLVE_MENU_SCRIPT = RESOLVE_USER_SCRIPTS / "降噪助手.lua"


class ResolveBridgeError(RuntimeError):
    pass


def _find_resolve_window() -> tuple[int, str] | None:
    if os.name != "nt":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    matches: list[tuple[int, str, int]] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    def collect(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        size = user32.GetWindowTextLengthW(hwnd)
        if not size:
            return True
        title_buffer = ctypes.create_unicode_buffer(size + 1)
        user32.GetWindowTextW(hwnd, title_buffer, size + 1)
        title = title_buffer.value
        if "davinci resolve" in title.casefold():
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
            matches.append((int(hwnd or 0), title, area))
        return True

    callback = enum_proc(collect)
    user32.EnumWindows(callback, 0)
    if not matches:
        return None
    matches.sort(key=lambda item: (item[2], len(item[1])), reverse=True)
    return matches[0][0], matches[0][1]


def resolve_window_available() -> bool:
    try:
        return _find_resolve_window() is not None
    except Exception:
        return False


def _hide_f6_console(main_hwnd: int) -> bool:
    """Hide Resolve's separate floating F6 console without toggling the main window."""
    if os.name != "nt":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL

    main_pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(main_hwnd), ctypes.byref(main_pid))
    matches: list[tuple[int, int]] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    def collect(hwnd, _lparam):
        if int(hwnd or 0) == main_hwnd or not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != main_pid.value:
            return True
        title_len = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(title_len + 1)
        user32.GetWindowTextW(hwnd, title, title_len + 1)
        cls = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, cls, 128)
        if title.value.strip() != "Resolve" or "Qt" not in cls.value:
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if 300 <= width <= 1400 and 200 <= height <= 1000:
            matches.append((int(hwnd or 0), width * height))
        return True

    callback = enum_proc(collect)
    user32.EnumWindows(callback, 0)
    if not matches:
        return False
    # Resolve can retain several hidden Qt helper windows with the same title;
    # only the visible, normal-sized floating console is considered above.
    console_hwnd = max(matches, key=lambda item: item[1])[0]
    user32.ShowWindow(wintypes.HWND(console_hwnd), 0)  # SW_HIDE
    return True


def activate_resolve_and_run_console(code: str) -> tuple[bool, str]:
    """Focus Resolve, open its F6 console, paste one Lua line, submit it and hide the console."""
    if os.name != "nt":
        return False, "自动输入仅支持 Windows；启动代码仍已复制。"
    try:
        match = _find_resolve_window()
        if match is None:
            return False, "没有找到已打开的 DaVinci Resolve 窗口；启动代码仍已复制，请先打开 Resolve。"
        hwnd, _title = match
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if int(user32.GetForegroundWindow() or 0) == hwnd:
                break
            time.sleep(0.05)
        if int(user32.GetForegroundWindow() or 0) != hwnd:
            return False, "Windows 未能将 Resolve 切换到前台；启动代码仍已复制，请在 Resolve 按 F6 后粘贴。"

        def key(vk: int, key_up: bool = False) -> None:
            user32.keybd_event(vk, 0, 0x0002 if key_up else 0, 0)

        def press(vk: int) -> None:
            key(vk)
            key(vk, True)

        press(0x75)  # VK_F6
        time.sleep(0.45)
        key(0x11)  # VK_CONTROL down
        try:
            press(0x56)  # VK_V
        finally:
            key(0x11, True)  # VK_CONTROL up
        time.sleep(0.15)
        press(0x0D)  # VK_RETURN
        # Wait for the listener started by the Lua command, then hide F6.
        # This is more reliable than a fixed delay on slower Resolve sessions.
        bridge_ready = False
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                if ResolveConsoleBridge(timeout=0.35).health().get("connected"):
                    bridge_ready = True
                    break
            except ResolveBridgeError:
                pass
            time.sleep(0.2)
        time.sleep(0.25)
        user32.SetForegroundWindow(hwnd)
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            if int(user32.GetForegroundWindow() or 0) == hwnd:
                break
            time.sleep(0.05)
        if not _hide_f6_console(hwnd):
            # Some Resolve builds dock F6 inside the main window instead of
            # creating a separate floating console.
            press(0x75)
        detail = "已连接桥接并收起 F6 控制台。" if bridge_ready else "已提交桥接代码并收起 F6 控制台，正在确认连接。"
        return True, detail
    except Exception as exc:
        return False, f"自动输入启动代码失败：{exc}。启动代码仍已复制，可在 Resolve 按 F6 后手动粘贴。"


def install_console_bridge() -> str:
    installer = BRIDGE_REPO / "install.ps1"
    if not installer.is_file():
        raise ResolveBridgeError("没有找到随助手附带的 Resolve Console Bridge 文件。")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer)],
            cwd=str(BRIDGE_REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise ResolveBridgeError("找不到 PowerShell，无法安装 Resolve Lua 桥接。") from exc
    except subprocess.TimeoutExpired as exc:
        raise ResolveBridgeError("安装 Resolve Lua 桥接超时。") from exc
    except OSError as exc:
        raise ResolveBridgeError(f"无法运行桥接安装脚本：{exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "安装脚本未提供错误详情").strip()
        raise ResolveBridgeError(f"安装 Resolve Lua 桥接失败：\n{detail[-1800:]}")
    if not BRIDGE_CONFIG.is_file() or not BRIDGE_LUA.is_file():
        raise ResolveBridgeError("桥接安装未完成，请打开项目文件夹检查工具目录。")
    return 'dofile(os.getenv("USERPROFILE") .. "/.config/davinci-resolve-mcp/console-bridge/resolve_bridge.lua")'


def install_workspace_menu_script() -> bool:
    source = APP_DIR / "resolve_workspace_script.lua"
    if not source.is_file():
        raise ResolveBridgeError("没有找到工作区启动脚本模板。")
    try:
        RESOLVE_USER_SCRIPTS.mkdir(parents=True, exist_ok=True)
        content = source.read_text(encoding="utf-8").replace(
            "__LAUNCHER_VBS__", json.dumps(str(APP_DIR / "start_assistant.vbs"), ensure_ascii=False)
        )
        if RESOLVE_MENU_SCRIPT.is_file() and RESOLVE_MENU_SCRIPT.read_text(encoding="utf-8") == content:
            return False
        RESOLVE_MENU_SCRIPT.write_text(content, encoding="utf-8")
        return True
    except OSError as exc:
        raise ResolveBridgeError(f"无法写入 Resolve 工作区启动脚本：{exc}") from exc


class ResolveConsoleBridge:
    """Small authenticated client for the bundled MIT Resolve Console bridge."""

    def __init__(self, timeout: float = 4.0):
        try:
            config = json.loads(BRIDGE_CONFIG.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ResolveBridgeError("尚未安装 F6 脚本桥接。请通过降噪助手启动一次连接。") from exc
        self.host = str(config.get("host", ""))
        try:
            self.port = int(config.get("port", 0))
        except (TypeError, ValueError) as exc:
            raise ResolveBridgeError("桥接配置中的端口无效。请重新安装 F6 脚本桥接。") from exc
        self.token = str(config.get("token", ""))
        self.timeout = timeout
        if self.host != "127.0.0.1" or not 1 <= self.port <= 65535 or not self.token:
            raise ResolveBridgeError("桥接配置无效或不是本机回环地址。请重新安装 F6 脚本桥接。")

    def request(self, operation: str, arguments: dict | None = None) -> dict:
        body = {
            "protocol": "1.0",
            "id": uuid.uuid4().hex,
            "timestamp": int(time.time()),
            "nonce": secrets.token_urlsafe(24),
            "operation": operation,
            "arguments": arguments or {},
        }
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        body["signature"] = hmac.new(self.token.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
        wire = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as connection:
                connection.settimeout(self.timeout)
                connection.sendall(wire)
                chunks = bytearray()
                while len(chunks) <= 1_048_576:
                    block = connection.recv(65536)
                    if not block:
                        break
                    chunks.extend(block)
                    if b"\n" in block:
                        break
        except (OSError, TimeoutError) as exc:
            if operation != "health":
                message = (
                    "Resolve 的 F6 桥接在执行操作时失去响应或连接中断。"
                    "请先检查并关闭 Resolve 中的渲染提示，再点“刷新达芬奇”重试。"
                )
            else:
                message = "无法连接 Resolve。请打开项目，在 DaVinci Resolve 按 F6，粘贴已复制的启动代码并回车。"
            raise ResolveBridgeError(message) from exc
        if not chunks or len(chunks) > 1_048_576:
            raise ResolveBridgeError("Resolve 桥接没有返回有效响应。请在 F6 控制台重新运行启动代码。")
        try:
            reply = json.loads(bytes(chunks).split(b"\n", 1)[0].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResolveBridgeError("Resolve 桥接返回的数据格式无效。") from exc
        if not reply.get("ok"):
            error = reply.get("error") or {}
            message = str(error.get("message") or error.get("code") or "Resolve 脚本调用失败")
            raise ResolveBridgeError(message)
        return reply.get("result") or {}

    def health(self) -> dict:
        return self.request("health")

    def call(self, target: str | dict, method: str, args: list | None = None):
        if isinstance(target, dict):
            target = target.get("__handle__")
        if not isinstance(target, str) or not target:
            raise ResolveBridgeError("Resolve 返回了无效的媒体句柄，无法调用该对象。")
        result = self.request("call", {"target": target, "method": method, "args": args or []})
        return result.get("value")


def _describe_audio_items(client: ResolveConsoleBridge, items: list) -> list[dict]:
    clips: list[dict] = []
    for handle in items:
        if not isinstance(handle, dict) or not handle.get("__handle__"):
            continue
        kind = client.call(handle, "GetType")
        if str(kind).lower() != "audio":
            continue
        name = str(client.call(handle, "GetName") or "音频片段")
        record_frame = client.call(handle, "GetStart")
        duration_frames = client.call(handle, "GetDuration")
        track = client.call(handle, "GetTrackTypeAndIndex") or []
        if not isinstance(track, list) or len(track) < 2:
            raise ResolveBridgeError(f"无法读取“{name}”所在的音轨。")
        try:
            record_frame = float(record_frame)
            duration_frames = float(duration_frames)
            track_index = int(track[1])
        except (TypeError, ValueError) as exc:
            raise ResolveBridgeError(f"无法读取“{name}”的时间线位置或长度。") from exc
        if duration_frames <= 0:
            raise ResolveBridgeError(f"“{name}”的时间线长度无效。")
        clips.append({
            "name": name,
            "record_frame": record_frame,
            "record_end_frame": record_frame + duration_frames,
            "source_track": track_index,
        })
    if not clips:
        raise ResolveBridgeError("所选音轨没有可处理的音频片段。")
    clips.sort(key=lambda clip: (clip["record_frame"], clip["name"]))
    return clips


def audio_track_items(client: ResolveConsoleBridge, track_index: int) -> tuple[str, list[dict]]:
    timeline_name = client.call("current_timeline", "GetName")
    items = client.call("current_timeline", "GetItemListInTrack", ["audio", int(track_index)]) or []
    if not isinstance(items, list) or not items:
        raise ResolveBridgeError(f"音轨 A{track_index} 里没有音频片段。")
    clips = _describe_audio_items(client, items)
    return str(timeline_name or "当前时间线"), clips


def _render_job(client: ResolveConsoleBridge, job_id: str) -> dict | None:
    jobs = client.call("project", "GetRenderJobList") or []
    if not isinstance(jobs, list):
        return None
    return next((item for item in jobs if isinstance(item, dict) and item.get("JobId") == job_id), None)


def _resolve_render_staging_dir() -> Path:
    """Pick a writable Resolve bridge output root; the WAV is moved into the app cache afterward."""
    try:
        config = json.loads(BRIDGE_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        config = {}
    roots = config.get("allowed_output_roots") or []
    if not isinstance(roots, list):
        roots = []
    if not roots:
        roots = [str(Path.home() / "Videos")]

    failures: list[str] = []
    for value in roots:
        root_text = os.path.expandvars(os.path.expanduser(str(value))).strip()
        if not root_text:
            continue
        root = Path(root_text)
        if not root.is_absolute():
            continue
        staging = root / "ResolveDenoiseStaging"
        try:
            staging.mkdir(parents=True, exist_ok=True)
            probe = staging / f".write_check_{uuid.uuid4().hex}"
            with probe.open("xb"):
                pass
            probe.unlink()
            return staging.resolve()
        except OSError as exc:
            failures.append(f"{staging}: {exc}")
    detail = "\n".join(failures[:3])
    suffix = f"\n{detail}" if detail else ""
    raise ResolveBridgeError(
        "助手无法在 Resolve 桥接配置的输出目录中建立临时渲染位置。"
        f"请检查本机 Videos 输出目录权限。{suffix}"
    )


def _snapshot_render_settings(client: ResolveConsoleBridge) -> tuple[str | None, dict | None]:
    """Read current Deliver settings through a temporary, immediately deleted queue row."""
    job_id = client.call("project", "AddRenderJob")
    if not isinstance(job_id, str) or not job_id:
        return None, None
    try:
        return job_id, _render_job(client, job_id)
    finally:
        try:
            client.call("project", "DeleteRenderJob", [job_id])
        except ResolveBridgeError:
            pass


def _restore_render_settings(client: ResolveConsoleBridge, snapshot: dict | None) -> None:
    if not snapshot:
        return
    settings: dict = {}
    for source, target in (
        ("TargetDir", "TargetDir"),
        ("IsExportVideo", "ExportVideo"),
        ("IsExportAudio", "ExportAudio"),
        ("AudioBitDepth", "AudioBitDepth"),
        ("AudioSampleRate", "AudioSampleRate"),
        ("AudioCodec", "AudioCodec"),
        ("MarkIn", "MarkIn"),
        ("MarkOut", "MarkOut"),
    ):
        value = snapshot.get(source)
        if value is not None and value != "":
            settings[target] = value
    output_name = str(snapshot.get("OutputFilename") or "")
    if output_name:
        output_path = Path(output_name)
        settings["CustomName"] = output_path.stem
    if settings:
        client.call("project", "SetRenderSettings", [settings])
    video_codec = str(snapshot.get("VideoCodec") or "")
    video_format = str(snapshot.get("VideoFormat") or "")
    if video_codec and video_format:
        formats = client.call("project", "GetRenderFormats") or {}
        format_id = formats.get(video_format) if isinstance(formats, dict) else None
        if format_id:
            client.call("project", "SetCurrentRenderFormatAndCodec", [format_id, video_codec])


def render_audio_track_to_wav(
    client: ResolveConsoleBridge,
    timeline_name: str,
    track_index: int,
    destination: Path,
    progress: Callable[[str, int, bool], None] | None = None,
) -> tuple[dict, list[dict]]:
    """Render one enabled timeline audio track as a time-aligned WAV file."""
    timeline = client.call("project", "GetCurrentTimeline")
    if not isinstance(timeline, dict) or not timeline.get("__handle__"):
        raise ResolveBridgeError("Resolve 没有返回当前时间线句柄。")
    active_timeline = str(client.call(timeline, "GetName") or "")
    if active_timeline != timeline_name:
        raise ResolveBridgeError("处理期间 Resolve 当前时间线已改变。")
    timeline_id = str(client.call(timeline, "GetUniqueId") or "")

    def ensure_same_timeline() -> None:
        current = client.call("project", "GetCurrentTimeline")
        current_id = str(client.call(current, "GetUniqueId") or "") if isinstance(current, dict) else ""
        if not timeline_id or current_id != timeline_id:
            raise ResolveBridgeError("处理期间 Resolve 当前时间线已改变。")

    items = client.call(timeline, "GetItemListInTrack", ["audio", int(track_index)]) or []
    if not isinstance(items, list) or not items:
        raise ResolveBridgeError(f"音轨 A{track_index} 里没有音频片段。")
    clips = _describe_audio_items(client, items)
    mark_in = int(min(item["record_frame"] for item in clips))
    mark_out = int(max(item["record_end_frame"] for item in clips)) - 1
    if mark_out < mark_in:
        raise ResolveBridgeError("选中音轨的时间线范围无效，无法导出 WAV。")
    if client.call("project", "IsRenderingInProgress"):
        raise ResolveBridgeError("Resolve 正在执行其他渲染任务。请等当前渲染结束后再试。")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = _resolve_render_staging_dir()
    staging_destination = staging_dir / f"ResolveDenoise_{uuid.uuid4().hex}.wav"
    page_before = str(client.call("resolve", "GetCurrentPage") or "")
    try:
        render_mode_before = client.call("project", "GetCurrentRenderMode")
    except ResolveBridgeError:
        render_mode_before = None

    snapshot: dict | None = None
    current_format_codec: dict = {}
    track_states: dict[int, bool] = {}
    job_id: str | None = None
    try:
        _snapshot_id, snapshot = _snapshot_render_settings(client)
        wav_formats = client.call("project", "GetAudioRenderFormats") or {}
        if not isinstance(wav_formats, dict) or "wav" not in {str(ext).lower() for ext in wav_formats.values()}:
            raise ResolveBridgeError("此 Resolve 版本没有提供可脚本调用的 WAV 音频导出格式。")
        audio_format = next(
            (str(value) for label, value in wav_formats.items() if str(value).lower() == "wav"),
            None,
        )
        wav_codecs = client.call("project", "GetAudioRenderCodecs", ["wav"]) or {}
        audio_codec = next(
            (str(value) for label, value in wav_codecs.items()
             if "linear pcm" in str(label).lower()),
            None,
        ) if isinstance(wav_codecs, dict) else None
        if not audio_format or not audio_codec:
            raise ResolveBridgeError("Resolve 没有提供 WAV / Linear PCM 编码器。")

        current_format_codec = client.call("project", "GetCurrentRenderFormatAndCodec") or {}

        track_count = int(client.call(timeline, "GetTrackCount", ["audio"]) or 0)
        for index in range(1, track_count + 1):
            track_states[index] = bool(client.call(timeline, "GetIsTrackEnabled", ["audio", index]))
        for index, enabled in track_states.items():
            wanted = index == int(track_index)
            if enabled != wanted and not client.call(timeline, "SetTrackEnable", ["audio", index, wanted]):
                raise ResolveBridgeError(f"Resolve 无法临时隔离音轨 A{track_index} 进行纯音频导出。")

        if render_mode_before is None or int(render_mode_before) != 1:
            if not client.call("project", "SetCurrentRenderMode", [1]):
                raise ResolveBridgeError("Resolve 无法切换到单文件渲染模式。")

        ensure_same_timeline()
        if not client.call("project", "LoadRenderPreset", ["Audio Only"]):
            raise ResolveBridgeError("Resolve 无法加载内置“Audio Only”预设，未开始渲染。")
        if not client.call("project", "SetCurrentRenderFormatAndCodec", [audio_format, audio_codec]):
            raise ResolveBridgeError("Resolve 无法切换到 WAV / Linear PCM 输出格式。")
        staging_target = str(staging_dir).replace("\\", "/").rstrip("/") + "/"
        render_settings = {
            "SelectAllFrames": False,
            "MarkIn": mark_in,
            "MarkOut": mark_out,
            "TargetDir": staging_target,
            "CustomName": staging_destination.stem,
            "ExportVideo": False,
            "ExportAudio": True,
            "AudioCodec": audio_codec,
            "AudioBitDepth": 16,
            "AudioSampleRate": 48000,
        }
        if not client.call("project", "SetRenderSettings", [render_settings]):
            raise ResolveBridgeError("Resolve 拒绝了 WAV 导出设置，未开始降噪。")
        job_id = client.call("project", "AddRenderJob")
        if not isinstance(job_id, str) or not job_id:
            raise ResolveBridgeError("Resolve 没有创建 WAV 渲染任务。")
        job = _render_job(client, job_id)
        if not job:
            raise ResolveBridgeError("Resolve 已创建渲染任务，但没有返回任务信息。")
        if job.get("TimelineName") and str(job.get("TimelineName")) != timeline_name:
            raise ResolveBridgeError("Resolve 将当前渲染任务绑定到了其他时间线，已取消渲染。")
        if job.get("IsExportVideo") or job.get("IsExportAudio") is not True:
            raise ResolveBridgeError("Resolve 没有接受“纯音频 WAV”设置，已取消本次渲染。")
        job_target = str(job.get("TargetDir") or "")
        if not job_target:
            raise ResolveBridgeError(f"Resolve 没有接受自动选择的 WAV 输出目录：\n{staging_dir}")
        if os.path.normcase(os.path.normpath(job_target)) != os.path.normcase(
            os.path.normpath(str(staging_dir))
        ):
            raise ResolveBridgeError("Resolve 渲染任务没有使用助手自动选择的临时输出目录，已取消渲染。")
        if Path(str(job.get("OutputFilename") or "")).suffix.lower() != ".wav":
            raise ResolveBridgeError("Resolve 没有把本次音频导出设置成 WAV，已取消渲染。")

        if progress:
            progress("正在由 Resolve 导出 WAV", 0, False)
        ensure_same_timeline()
        if not client.call("project", "StartRendering", [[job_id]]):
            raise ResolveBridgeError(f"Resolve 无法启动 WAV 导出任务。自动选择的输出目录：\n{staging_dir}")

        started = time.monotonic()
        while True:
            status = client.call("project", "GetRenderJobStatus", [job_id]) or {}
            status_name = str(status.get("JobStatus") or "").lower()
            completion = max(0, min(100, int(status.get("CompletionPercentage") or 0)))
            if progress:
                progress("正在由 Resolve 导出 WAV", completion, False)
            if "complete" in status_name:
                break
            if any(word in status_name for word in ("fail", "cancel")):
                detail = str(status.get("Error") or status.get("JobStatus") or "未知渲染错误")
                raise ResolveBridgeError(f"Resolve 导出 WAV 失败：{detail}")
            if time.monotonic() - started > 6 * 60 * 60:
                raise ResolveBridgeError("Resolve 导出 WAV 超过六小时仍未完成，已停止等待。")
            time.sleep(0.4)

        output_name = str(job.get("OutputFilename") or "")
        rendered = Path(output_name)
        if not rendered.is_absolute():
            rendered = staging_dir / rendered
        if rendered.suffix.lower() != ".wav":
            rendered = rendered.with_suffix(".wav")
        if not rendered.is_file():
            rendered = staging_destination
        for _ in range(80):
            if rendered.is_file() and rendered.stat().st_size > 44:
                break
            time.sleep(0.25)
        if not rendered.is_file() or rendered.stat().st_size <= 44:
            raise ResolveBridgeError(f"Resolve 报告渲染完成，但没有生成有效 WAV：\n{rendered}")

        try:
            shutil.move(str(rendered), str(destination))
        except OSError as exc:
            raise ResolveBridgeError(
                f"WAV 已由 Resolve 导出，但无法移动到助手缓存目录：\n{destination}\n{exc}"
            ) from exc
        if not destination.is_file() or destination.stat().st_size <= 44:
            raise ResolveBridgeError(f"WAV 已导出，但助手缓存中的文件无效：\n{destination}")
        return {"path": destination, "mark_in": mark_in, "mark_out": mark_out,
                "timeline_id": timeline_id}, clips
    finally:
        if job_id:
            try:
                client.call("project", "DeleteRenderJob", [job_id])
            except ResolveBridgeError:
                pass
        for index, enabled in track_states.items():
            try:
                current = bool(client.call(timeline, "GetIsTrackEnabled", ["audio", index]))
                if current != enabled:
                    client.call(timeline, "SetTrackEnable", ["audio", index, enabled])
            except ResolveBridgeError:
                pass
        try:
            _restore_render_settings(client, snapshot)
        except ResolveBridgeError:
            pass
        if isinstance(current_format_codec, dict) and current_format_codec.get("format"):
            try:
                client.call("project", "SetCurrentRenderFormatAndCodec", [
                    str(current_format_codec.get("format")),
                    str(current_format_codec.get("codec") or ""),
                ])
            except ResolveBridgeError:
                pass
        if render_mode_before is not None:
            try:
                client.call("project", "SetCurrentRenderMode", [int(render_mode_before)])
            except ResolveBridgeError:
                pass
        if page_before:
            try:
                page_now = str(client.call("resolve", "GetCurrentPage") or "")
                if page_now != page_before:
                    client.call("resolve", "OpenPage", [page_before])
            except ResolveBridgeError:
                pass


def current_resolve_state(client: ResolveConsoleBridge) -> dict:
    health = client.health()
    timeline = client.request("get_timeline")
    timelines = client.request("list_timelines").get("timelines") or []
    tracks = timeline.get("tracks") or []
    audio_tracks = [
        {"index": int(track["index"]), "name": str(track.get("name") or f"Audio {track['index']}")}
        for track in tracks if track.get("type") == "audio"
    ]
    return {
        "health": health,
        "timeline_name": str(timeline.get("name") or ""),
        "timelines": [str(item.get("name") or "") for item in timelines if item.get("name")],
        "audio_tracks": audio_tracks,
    }


def append_outputs_to_new_tracks(
    client: ResolveConsoleBridge,
    timeline_name: str,
    clips: list[dict],
    output_paths: list[Path],
    expected_timeline_id: str | None = None,
) -> list[str]:
    if len(clips) != len(output_paths):
        raise ResolveBridgeError("降噪结果数量与时间线片段数量不一致，未回填。")
    active_name = str(client.call("current_timeline", "GetName") or "")
    if active_name != timeline_name:
        raise ResolveBridgeError("处理期间 Resolve 当前时间线已改变。为避免加错时间线，结果留在输出文件夹中，没有回填。")
    if expected_timeline_id:
        timeline = client.call("project", "GetCurrentTimeline")
        active_id = str(client.call(timeline, "GetUniqueId") or "") if isinstance(timeline, dict) else ""
        if active_id != expected_timeline_id:
            raise ResolveBridgeError("处理期间 Resolve 当前时间线已改变。降噪 WAV 保留在缓存中，没有回填。")

    def find_imported(path: Path) -> dict | None:
        try:
            folder = client.call("media_pool", "GetCurrentFolder")
            if not isinstance(folder, dict) or not folder.get("__handle__"):
                return None
            items = client.call(folder, "GetClipList") or []
            wanted = os.path.normcase(os.path.normpath(str(path.resolve())))
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict) or not item.get("__handle__"):
                    continue
                actual = client.call(item, "GetClipProperty", ["File Path"])
                if os.path.normcase(os.path.normpath(str(actual or ""))) == wanted:
                    return item
        except (OSError, ResolveBridgeError):
            return None
        return None

    imported: list[dict] = []
    for path in output_paths:
        path = Path(path).resolve()
        item = find_imported(path)
        if item is None:
            result = client.call("media_pool", "ImportMedia", [[str(path)]])
            if isinstance(result, list):
                wanted = os.path.normcase(os.path.normpath(str(path)))
                for entry in result:
                    if not isinstance(entry, dict) or not entry.get("__handle__"):
                        continue
                    actual = client.call(entry, "GetClipProperty", ["File Path"])
                    if os.path.normcase(os.path.normpath(str(actual or ""))) == wanted:
                        item = entry
                        break
        if item is None:
            for _ in range(20):
                time.sleep(0.25)
                item = find_imported(path)
                if item is not None:
                    break
        if item is None:
            raise ResolveBridgeError(
                f"Resolve 没有把降噪 WAV 导入当前 Media Pool 文件夹：\n{path}\n"
                "WAV 已保存在缓存中；请确认 Media Pool 可用后再重试回填。"
            )
        imported.append(item)

    source_tracks = sorted({int(item["source_track"]) for item in clips})
    track_count = int(client.call("current_timeline", "GetTrackCount", ["audio"]) or 0)
    track_map: dict[int, int] = {}
    for source_track in source_tracks:
        if not client.call("current_timeline", "AddTrack", ["audio"]):
            raise ResolveBridgeError("Resolve 无法创建新的音轨。降噪 WAV 已导入 Media Pool。")
        track_count += 1
        track_map[source_track] = track_count
        client.call("current_timeline", "SetTrackName", ["audio", track_count, f"降噪 A{source_track}"])

    placements = []
    for clip, media_pool_item in zip(clips, imported):
        if not isinstance(media_pool_item, dict) or not media_pool_item.get("__handle__"):
            raise ResolveBridgeError("Resolve 返回了无效的媒体池句柄，无法安全回填。")
        placements.append({
            "mediaPoolItem": media_pool_item,
            "mediaType": 2,
            "trackIndex": track_map[int(clip["source_track"])],
            "recordFrame": clip["record_frame"],
        })
    result = client.call("media_pool", "AppendToTimeline", [placements])
    if not isinstance(result, list) or len(result) != len(placements):
        raise ResolveBridgeError("Resolve 没有确认所有片段已回填。请检查新建的降噪音轨和 Media Pool。")
    return [f"A{track_map[index]}" for index in source_tracks]
