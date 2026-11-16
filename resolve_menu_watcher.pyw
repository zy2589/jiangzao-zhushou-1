from __future__ import annotations

import ctypes
import os
import subprocess
import time
from pathlib import Path


MARKER = "RAA_AUDIO_START_REQUEST_V1"
APP_DIR = Path(__file__).resolve().parent
LAUNCHER = APP_DIR / "start_assistant.vbs"
RESOLVE_LOG = (
    Path(os.environ.get("APPDATA", Path.home()))
    / "Blackmagic Design"
    / "DaVinci Resolve"
    / "Support"
    / "logs"
    / "ResolveDebug.txt"
)


class LogTail:
    def __init__(self, path: Path):
        self.path = path
        self.pending = b""
        try:
            self.offset = path.stat().st_size
        except OSError:
            self.offset = None

    def read_request(self) -> bool:
        try:
            stat = self.path.stat()
            if self.offset is None:
                self.offset = stat.st_size
                return False
            if stat.st_size < self.offset:
                self.offset = 0
                self.pending = b""
            if stat.st_size - self.offset > 1_048_576:
                self.offset = stat.st_size
                self.pending = b""
                return False
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                chunk = stream.read()
                self.offset = stream.tell()
            self.pending += chunk
            end = self.pending.rfind(b"\n")
            if end < 0:
                if len(self.pending) > 8192:
                    self.pending = b""
                return False
            complete = self.pending[: end + 1]
            self.pending = self.pending[end + 1 :]
            marker = MARKER.encode("ascii")
            return any(line.rstrip(b"\r").endswith(marker) for line in complete.split(b"\n"))
        except OSError:
            return False


def acquire_watcher_mutex():
    if os.name != "nt":
        return None, True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex = kernel32.CreateMutexW(None, True, "Local\\DenoiseAssistant.ResolveMenuWatcher")
    if not mutex:
        return None, True
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(mutex)
        return None, False
    return (kernel32, mutex), True


def open_assistant() -> None:
    try:
        subprocess.Popen(
            ["wscript.exe", "//B", "//NoLogo", str(LAUNCHER)],
            cwd=str(APP_DIR), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        pass


def main() -> None:
    handle, acquired = acquire_watcher_mutex()
    if not acquired:
        return
    tail = LogTail(RESOLVE_LOG)
    try:
        while True:
            if tail.read_request():
                open_assistant()
            time.sleep(0.3)
    finally:
        if handle:
            kernel32, mutex = handle
            kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
            kernel32.ReleaseMutex(mutex)
            kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
