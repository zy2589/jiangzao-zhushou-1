from __future__ import annotations

import ctypes
import os
import runpy
import traceback
from pathlib import Path


_INSTANCE_MUTEX = None


def bring_existing_assistant_forward() -> None:
    if os.name != "nt":
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        enum_windows = user32.EnumWindows
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t)
        enum_windows.argtypes = [callback_type, ctypes.c_ssize_t]
        enum_windows.restype = ctypes.c_int
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = ctypes.c_int
        user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]

        def visit(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            if title.value == "降噪助手":
                user32.ShowWindow(hwnd, 9)
                user32.SetForegroundWindow(hwnd)
                return 0
            return 1

        callback = callback_type(visit)
        enum_windows(callback, 0)
    except Exception:
        pass


def acquire_single_instance() -> bool:
    global _INSTANCE_MUTEX
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex = kernel32.CreateMutexW(None, False, "Local\\DenoiseAssistant.SingleInstance")
    if not mutex:
        return True
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(mutex)
        bring_existing_assistant_forward()
        return False
    _INSTANCE_MUTEX = mutex
    return True


def main() -> None:
    if not acquire_single_instance():
        return
    app_script = Path(__file__).resolve().with_name("denoise_assistant.py")
    try:
        runpy.run_path(str(app_script), run_name="__main__")
    except Exception:
        details = traceback.format_exc()
        log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DenoiseAssistant"
        log_path = log_dir / "startup.log"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\n[{datetime.now().isoformat(timespec='seconds')}]\n{details}\n")
        except OSError:
            pass
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"降噪助手没有启动成功。\n\n错误日志：{log_path}\n\n{details[-2200:]}",
                "降噪助手 - 启动失败",
                0x10,
            )
        except Exception:
            pass


if __name__ == "__main__":
    main()
