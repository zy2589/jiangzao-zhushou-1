from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from PyInstaller.__main__ import run


ROOT = Path(__file__).resolve().parent
APP_NAME = "降噪助手"
FFMPEG = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
BUILD_DIR = ROOT / "build"
DEPENDENCY_DIR = BUILD_DIR / "dependencies"
ONEDIR_ROOT = BUILD_DIR / "portable"
PAYLOAD_ZIP = BUILD_DIR / "assistant-payload.zip"
BOOTSTRAP_EXE = BUILD_DIR / "assistant-bootstrap.exe"
FINAL_EXE = ROOT / "dist" / f"{APP_NAME}.exe"
LAUNCHER_SOURCE = ROOT / "single_file_launcher.cs"
YT_DLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.26/yt-dlp.exe"
YT_DLP_SHA256 = "f5389d3698ecde4ef06f4e6b1ce0294fe9ac1396228c8990b8d3416d6d177a7d"
DENO_URL = "https://github.com/denoland/deno/releases/download/v2.9.8/deno-x86_64-pc-windows-msvc.zip"
DENO_SHA256 = "cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf"
FOOTER_MAGIC = b"DENOISEZIP1"


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


def find_csharp_compiler() -> Path:
    windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidates = (
        windows / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
        windows / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("没有找到 Windows .NET Framework C# 编译器 csc.exe。")


def _write_payload_zip(payload_directory: Path) -> tuple[int, str]:
    PAYLOAD_ZIP.unlink(missing_ok=True)
    print("正在压缩应用文件，首次构建会较慢。", flush=True)
    with zipfile.ZipFile(PAYLOAD_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for source in sorted(payload_directory.rglob("*")):
            if source.is_file():
                archive.write(source, source.relative_to(payload_directory).as_posix())
    return PAYLOAD_ZIP.stat().st_size, sha256(PAYLOAD_ZIP)


def _compile_bootstrap(compiler: Path) -> None:
    BOOTSTRAP_EXE.parent.mkdir(parents=True, exist_ok=True)
    BOOTSTRAP_EXE.unlink(missing_ok=True)
    subprocess.run(
        [
            str(compiler), "/nologo", "/target:winexe", "/optimize+",
            "/reference:System.IO.Compression.dll",
            "/reference:System.IO.Compression.FileSystem.dll",
            "/reference:System.Windows.Forms.dll",
            f"/out:{BOOTSTRAP_EXE}", str(LAUNCHER_SOURCE),
        ],
        check=True,
    )


def _append_payload(payload_length: int, payload_hash: str) -> None:
    FINAL_EXE.parent.mkdir(parents=True, exist_ok=True)
    pending = FINAL_EXE.with_suffix(".new.exe")
    pending.unlink(missing_ok=True)
    with BOOTSTRAP_EXE.open("rb") as source, pending.open("wb") as output:
        shutil.copyfileobj(source, output, 1024 * 1024)
    with PAYLOAD_ZIP.open("rb") as source, pending.open("ab") as output:
        shutil.copyfileobj(source, output, 1024 * 1024)
        output.write(struct.pack("<q", payload_length))
        output.write(payload_hash.encode("ascii"))
        output.write(FOOTER_MAGIC)
    os.replace(pending, FINAL_EXE)


def _seed_local_cache(payload_directory: Path, payload_hash: str) -> None:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    cache = local_app_data / "DenoiseAssistant" / "Runtime" / payload_hash
    if (cache / ".ready").is_file() and (cache / f"{APP_NAME}.exe").is_file():
        return
    try:
        if cache.exists():
            shutil.rmtree(cache)
        cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(payload_directory, cache)
        (cache / ".ready").write_text(payload_hash, encoding="ascii")
        print("已为本机预热启动缓存。", flush=True)
    except OSError as exc:
        print(f"本机缓存未预热，首次运行时会自动生成：{exc}", flush=True)


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("请在 Windows x64 上构建这个单文件 EXE。")
    if not FFMPEG or not Path(FFMPEG).is_file():
        raise SystemExit("构建前请把 ffmpeg.exe 加入 PATH。")
    if not LAUNCHER_SOURCE.is_file():
        raise SystemExit(f"缺少启动器源码：{LAUNCHER_SOURCE}")

    yt_dlp, deno = prepare_platform_tools()
    data_files = [
        (ROOT / "vendor_new" / "tkinterdnd2", "vendor_new/tkinterdnd2"),
        (ROOT / "vendor_new" / "tkinterdnd2-0.6.4.dist-info" / "licenses" / "LICENSE",
         "licenses/TkinterDnD2-LICENSE.txt"),
        (ROOT / "tools" / "deep-filter.exe", "tools"),
        (Path(FFMPEG), "tools"),
        (yt_dlp, "tools"),
        (deno, "tools"),
        (ROOT / "licenses" / "yt-dlp-LICENSE.txt", "licenses"),
        (ROOT / "licenses" / "yt-dlp-THIRD_PARTY_LICENSES.txt", "licenses"),
        (ROOT / "licenses" / "deno-LICENSE.txt", "licenses"),
        (ROOT / "THIRD_PARTY_NOTICES.md", "licenses"),
    ]
    for source, _destination in data_files:
        if not source.exists():
            raise SystemExit(f"打包文件不存在：{source}")

    if ONEDIR_ROOT.exists():
        shutil.rmtree(ONEDIR_ROOT)
    args = [
        str(ROOT / "launch_assistant.pyw"),
        "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", APP_NAME,
        "--contents-directory", "_internal",
        "--distpath", str(ONEDIR_ROOT),
        "--workpath", str(BUILD_DIR / "pyinstaller"),
        "--specpath", str(BUILD_DIR / "pyinstaller"),
        "--paths", str(ROOT / "vendor_new"),
        "--hidden-import=tkinter", "--hidden-import=tkinter.ttk",
        "--hidden-import=tkinter.filedialog", "--hidden-import=tkinter.font",
        "--hidden-import=tkinter.messagebox", "--hidden-import=mimetypes",
    ]
    for source, destination in data_files:
        args.extend(("--add-data", f"{source};{destination}"))
    run(args)

    payload_directory = ONEDIR_ROOT / APP_NAME
    if not (payload_directory / f"{APP_NAME}.exe").is_file():
        raise RuntimeError("PyInstaller 没有生成预期的应用目录。")
    payload_length, payload_hash = _write_payload_zip(payload_directory)
    _compile_bootstrap(find_csharp_compiler())
    _append_payload(payload_length, payload_hash)
    _seed_local_cache(payload_directory, payload_hash)
    PAYLOAD_ZIP.unlink(missing_ok=True)
    shutil.rmtree(ONEDIR_ROOT, ignore_errors=True)
    BOOTSTRAP_EXE.unlink(missing_ok=True)
    print(f"单文件 EXE 已生成：{FINAL_EXE}", flush=True)
    print(f"大小：{FINAL_EXE.stat().st_size / (1024 * 1024):.1f} MB。后续启动从本机缓存打开。", flush=True)


if __name__ == "__main__":
    main()
