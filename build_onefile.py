from __future__ import annotations

import hashlib
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

from PyInstaller.__main__ import run


ROOT = Path(__file__).resolve().parent
FFMPEG = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
DEPENDENCY_DIR = ROOT / "build" / "dependencies"
YT_DLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.26/yt-dlp.exe"
YT_DLP_SHA256 = "f5389d3698ecde4ef06f4e6b1ce0294fe9ac1396228c8990b8d3416d6d177a7d"
DENO_URL = "https://github.com/denoland/deno/releases/download/v2.9.8/deno-x86_64-pc-windows-msvc.zip"
DENO_SHA256 = "cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_verified(url: str, destination: Path, expected_sha256: str) -> None:
    if destination.is_file() and sha256(destination) == expected_sha256:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    partial.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "DenoiseAssistant-builder"})
    print(f"下载并校验：{destination.name}", flush=True)
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output, 1024 * 1024)
        actual = sha256(partial)
        if actual != expected_sha256:
            raise RuntimeError(f"{destination.name} SHA-256 不匹配：{actual}")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def prepare_platform_tools() -> tuple[Path, Path]:
    DEPENDENCY_DIR.mkdir(parents=True, exist_ok=True)
    yt_dlp = DEPENDENCY_DIR / "yt-dlp.exe"
    deno = DEPENDENCY_DIR / "deno.exe"
    deno_zip = DEPENDENCY_DIR / "deno-windows-x64.zip"
    download_verified(YT_DLP_URL, yt_dlp, YT_DLP_SHA256)
    download_verified(DENO_URL, deno_zip, DENO_SHA256)
    if not deno.is_file():
        with zipfile.ZipFile(deno_zip) as archive:
            member = next((name for name in archive.namelist() if Path(name).name.lower() == "deno.exe"), None)
            if member is None:
                raise RuntimeError("Deno 官方压缩包中没有 deno.exe。")
            deno.write_bytes(archive.read(member))
    return yt_dlp, deno


if sys.platform != "win32":
    raise SystemExit("请在 Windows x64 上构建这个单文件 EXE。")
if not FFMPEG or not Path(FFMPEG).is_file():
    raise SystemExit("构建前请把 ffmpeg.exe 加入 PATH。")

YT_DLP, DENO = prepare_platform_tools()

DATA_FILES = [
    (ROOT / "denoise_assistant.py", "."),
    (ROOT / "vendor_new" / "tkinterdnd2", "vendor_new/tkinterdnd2"),
    (ROOT / "vendor_new" / "tkinterdnd2-0.6.4.dist-info" / "licenses" / "LICENSE",
     "licenses/TkinterDnD2-LICENSE.txt"),
    (ROOT / "tools" / "deep-filter.exe", "tools"),
    (Path(FFMPEG), "tools"),
    (YT_DLP, "tools"),
    (DENO, "tools"),
    (ROOT / "licenses" / "yt-dlp-LICENSE.txt", "licenses"),
    (ROOT / "licenses" / "yt-dlp-THIRD_PARTY_LICENSES.txt", "licenses"),
    (ROOT / "licenses" / "deno-LICENSE.txt", "licenses"),
    (ROOT / "THIRD_PARTY_NOTICES.md", "licenses"),
]

for source, _destination in DATA_FILES:
    if not source.exists():
        raise SystemExit(f"打包文件不存在：{source}")

args = [
    str(ROOT / "launch_assistant.pyw"),
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name",
    "降噪助手",
    "--distpath",
    str(ROOT / "dist"),
    "--workpath",
    str(ROOT / "build" / "pyinstaller"),
    "--specpath",
    str(ROOT / "build" / "pyinstaller"),
    "--hidden-import=tkinter",
    "--hidden-import=tkinter.ttk",
    "--hidden-import=tkinter.filedialog",
    "--hidden-import=tkinter.font",
    "--hidden-import=tkinter.messagebox",
]

for source, destination in DATA_FILES:
    # PyInstaller uses a semicolon as the source/destination separator on Windows.
    args.extend(("--add-data", f"{source};{destination}"))

run(args)
