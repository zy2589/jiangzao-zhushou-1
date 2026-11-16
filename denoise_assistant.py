from __future__ import annotations

import base64
import json
import mimetypes
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zlib
from pathlib import Path
from typing import Callable

APP_DIR = Path(__file__).resolve().parent
VENDOR_DIR = APP_DIR / "vendor_new"
if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk
from tkinterdnd2 import DND_FILES, TkinterDnD

APP_NAME = "降噪助手"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "DenoiseAssistant"
CONFIG_FILE = CONFIG_DIR / "settings.json"
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mts", ".m2ts", ".ts", ".mxf", ".mpeg", ".mpg", ".wmv", ".3gp"}
AUDIO_EXTS = {".wav", ".wave", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff", ".aif", ".mka"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS


class RoundedButton(tk.Canvas):
    """A compact, keyboard accessible rounded button rendered by Tk Canvas."""

    _image_cache: dict[tuple[int, int, int, str], tk.PhotoImage] = {}

    def __init__(self, parent: tk.Widget, text: str, command: Callable,
                 button_bg: str, primary_bg: str, primary: bool = False, compact: bool = False):
        self._text = text
        self._command = command
        self._base_color = primary_bg if primary else button_bg
        self._primary = primary
        self._base_bg = parent.cget("bg")
        self._font = ("Microsoft YaHei UI", 9, "bold" if primary else "normal")
        self._font_measure = tkfont.Font(parent, font=self._font)
        self._width = max(30 if compact else 70,
                          self._font_measure.measure(text) + (12 if compact else 18))
        self._height = 24 if compact else 30
        self._enabled = True
        self._hovered = False
        self._pressed = False
        self._active_tab = False
        super().__init__(parent, width=self._width, height=self._height, bg=self._base_bg,
                         highlightthickness=0, bd=0, relief="flat",
                         cursor="hand2", takefocus=True)
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", self._on_key_activate)
        self.bind("<space>", self._on_key_activate)
        self.bind("<FocusIn>", lambda _event: self._draw())
        self.bind("<FocusOut>", lambda _event: self._draw())
        self._draw()

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
            cnf = None
        state = kwargs.pop("state", None)
        if state is not None:
            self._enabled = str(state).lower() != "disabled"
            self.configure(cursor="hand2" if self._enabled else "arrow")
        result = super().configure(cnf, **kwargs)
        self._draw()
        return result

    config = configure

    def set_active(self, active: bool) -> None:
        self._active_tab = active
        self._draw()

    def _draw(self) -> None:
        if not self.winfo_exists():
            return
        self.delete("all")
        width = max(self.winfo_width(), self._width, 1)
        height = self._height
        # Keep a restrained radius so compact buttons stay rounded rectangles.
        radius = min(10, width // 2, height // 2)
        color = "#414141" if not self._enabled else self._base_color
        if self._enabled and self._pressed:
            color = "#111111"
        elif self._enabled and self._hovered:
            color = "#4a4a4a" if self._primary else "#353535"
        cache_key = (width, height, radius, color)
        image = self._image_cache.get(cache_key)
        if image is None:
            png_data = self._rounded_button_png(width, height, radius, color)
            image = tk.PhotoImage(master=self, data=png_data, format="png")
            self._image_cache[cache_key] = image
        self._image = image
        self.create_image(0, 0, image=image, anchor="nw")
        text_color = "#ffffff" if self._enabled else "#d0d0d0"
        self.create_text(width / 2, height / 2 - (1 if self._active_tab else 0),
                         text=self._text, fill=text_color, font=self._font)
        if self._active_tab:
            underline_half = min(width / 4, max(18, len(self._text) * 5.2))
            self.create_line(width / 2 - underline_half, height - 5,
                             width / 2 + underline_half, height - 5,
                             fill="#ffffff", width=2, capstyle="round")

    @staticmethod
    def _rounded_button_png(width: int, height: int, radius: int, color: str) -> str:
        """Render an antialiased transparent rounded rectangle using supersampling."""
        scale = 8
        rgb = bytes.fromhex(color.lstrip("#"))
        raw = bytearray()
        offsets = tuple((index + 0.5) / scale for index in range(scale))
        for y in range(height):
            row = bytearray((0,))  # PNG filter: None
            for x in range(width):
                coverage = 0
                for oy in offsets:
                    py = y + oy
                    cy = min(max(py, radius), height - radius)
                    for ox in offsets:
                        px = x + ox
                        cx = min(max(px, radius), width - radius)
                        if (px - cx) ** 2 + (py - cy) ** 2 <= radius ** 2:
                            coverage += 1
                alpha = (coverage * 255 + (scale * scale // 2)) // (scale * scale)
                row.extend((*rgb, alpha))
            raw.extend(row)

        def chunk(kind: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + kind + data
                    + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

        header = struct.pack(">2I5B", width, height, 8, 6, 0, 0, 0)
        png_data = (b"\x89PNG\r\n\x1a\n"
                    + chunk(b"IHDR", header)
                    + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
                    + chunk(b"IEND", b""))
        return base64.b64encode(png_data).decode("ascii")

    @staticmethod
    def _strength_thumb_png(size: int = 24) -> str:
        """Antialiased white slider thumb with a restrained gray rim and shadow."""
        scale = 8
        center = size / 2
        outer_radius = 8.5
        inner_radius = 6.6
        shadow_radius = 9.0
        offsets = tuple((index + 0.5) / scale for index in range(scale))
        raw = bytearray()
        for y in range(size):
            row = bytearray((0,))
            for x in range(size):
                red = green = blue = alpha = 0
                for oy in offsets:
                    py = y + oy
                    for ox in offsets:
                        px = x + ox
                        distance = (px - center) ** 2 + (py - center) ** 2
                        shadow_distance = (px - center) ** 2 + (py - center - 1.2) ** 2
                        if distance <= inner_radius ** 2:
                            sample = (255, 255, 255, 255)
                        elif distance <= outer_radius ** 2:
                            sample = (224, 229, 234, 255)
                        elif shadow_distance <= shadow_radius ** 2:
                            sample = (0, 0, 0, 64)
                        else:
                            sample = (0, 0, 0, 0)
                        sr, sg, sb, sa = sample
                        red += sr * sa
                        green += sg * sa
                        blue += sb * sa
                        alpha += sa
                samples = scale * scale
                out_alpha = (alpha + samples // 2) // samples
                if alpha:
                    row.extend(((red + alpha // 2) // alpha,
                                (green + alpha // 2) // alpha,
                                (blue + alpha // 2) // alpha,
                                out_alpha))
                else:
                    row.extend((0, 0, 0, 0))
            raw.extend(row)

        def chunk(kind: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + kind + data
                    + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

        header = struct.pack(">2I5B", size, size, 8, 6, 0, 0, 0)
        png_data = (b"\x89PNG\r\n\x1a\n"
                    + chunk(b"IHDR", header)
                    + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
                    + chunk(b"IEND", b""))
        return base64.b64encode(png_data).decode("ascii")

    def _on_enter(self, _event) -> None:
        self._hovered = self._enabled
        self._draw()

    def _on_leave(self, _event) -> None:
        self._hovered = False
        self._draw()

    def _on_press(self, _event) -> str:
        if self._enabled:
            self._pressed = True
            self.focus_set()
            self._draw()
        return "break"

    def _on_release(self, event) -> str:
        activate = self._enabled and self._pressed and 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self._height
        self._pressed = False
        self._draw()
        if activate:
            self._command()
        return "break"

    def _on_key_activate(self, _event) -> str:
        if self._enabled:
            self._command()
        return "break"


def find_program(names: tuple[str, ...], bundled_name: str) -> str:
    bundled = APP_DIR / "tools" / bundled_name
    if getattr(sys, "frozen", False) and bundled.is_file():
        return str(bundled)
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return str(bundled) if bundled.is_file() else ""


def default_settings() -> dict:
    return {
        "ffmpeg": find_program(("ffmpeg",), "ffmpeg.exe"),
        "deep_filter": find_program(("deep-filter", "deep_filter"), "deep-filter.exe"),
        "engine": "DeepFilterNet 人声降噪",
        "strength": 75,
    }


def load_settings() -> dict:
    settings = default_settings()
    try:
        saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            settings.update(saved)
    except (OSError, json.JSONDecodeError):
        pass
    if getattr(sys, "frozen", False):
        # One-file bundles unpack to a new temporary directory each launch.
        # Ignore any saved paths from a previous app installation and use the
        # copies shipped inside this executable.
        settings["ffmpeg"] = str(APP_DIR / "tools" / "ffmpeg.exe")
        settings["deep_filter"] = str(APP_DIR / "tools" / "deep-filter.exe")
    settings.pop("output_dir", None)
    return settings


def save_settings(settings: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


class ProcessError(RuntimeError):
    pass


def run_command(args: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise ProcessError(f"找不到程序：{args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProcessError(f"处理超时：{args[0]}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "未知错误").strip()
        raise ProcessError(f"命令执行失败（{result.returncode}）：\n{detail[-2200:]}")
    return result


def ffmpeg_path(settings: dict) -> str:
    bundled = APP_DIR / "tools" / "ffmpeg.exe"
    if getattr(sys, "frozen", False) and bundled.is_file():
        return str(bundled)
    path = str(settings.get("ffmpeg", "")).strip() or find_program(("ffmpeg",), "ffmpeg.exe")
    if not path or not Path(path).is_file():
        raise ProcessError("没有找到 ffmpeg.exe。请在“引擎设置”中选择 ffmpeg.exe。")
    return path


def deep_filter_path(settings: dict) -> str:
    bundled = APP_DIR / "tools" / "deep-filter.exe"
    if getattr(sys, "frozen", False) and bundled.is_file():
        return str(bundled)
    path = str(settings.get("deep_filter", "")).strip() or find_program(("deep-filter", "deep_filter"), "deep-filter.exe")
    if not path or not Path(path).is_file():
        raise ProcessError("没有找到 DeepFilterNet 命令行程序。请在“引擎设置”中选择 deep-filter.exe。")
    return path


def yt_dlp_path() -> str:
    for path in (APP_DIR / "tools" / "yt-dlp.exe", APP_DIR / "build" / "dependencies" / "yt-dlp.exe"):
        if path.is_file():
            return str(path)
    found = shutil.which("yt-dlp.exe") or shutil.which("yt-dlp")
    if found:
        return found
    raise ProcessError("没有找到内置网页下载器 yt-dlp.exe。")


def deno_path() -> str:
    for path in (APP_DIR / "tools" / "deno.exe", APP_DIR / "build" / "dependencies" / "deno.exe"):
        if path.is_file():
            return str(path)
    found = shutil.which("deno.exe") or shutil.which("deno")
    return found or ""


def unique_output_path(source: Path, output_dir: Path, suffix: str, extension: str | None = None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ext = extension or source.suffix
    candidate = output_dir / f"{source.stem}{suffix}{ext}"
    index = 2
    while candidate.exists():
        candidate = output_dir / f"{source.stem}{suffix}_{index}{ext}"
        index += 1
    return candidate


def _download_folder() -> Path:
    return Path.home() / "Downloads" / "降噪助手下载"


def _url_is_direct_media(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    suffix = Path(urllib.parse.unquote(parsed.path)).suffix.lower()
    if suffix in MEDIA_EXTS:
        return True
    try:
        request = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 DenoiseAssistant/1.0"}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.headers.get_content_type().lower().startswith(("audio/", "video/"))
    except Exception:
        return False


def _download_direct_media(url: str, report: Callable[[int | None, str], None]) -> Path:
    folder = _download_folder()
    folder.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 DenoiseAssistant/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        headers = response.headers
        raw_name = headers.get_filename()
        if not raw_name:
            raw_name = urllib.parse.unquote(Path(urllib.parse.urlsplit(response.geturl()).path).name)
        raw_name = raw_name.replace("\\", "/").rsplit("/", 1)[-1]
        raw_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name).strip(" .")[:160]
        content_type = headers.get_content_type().lower()
        suffix = Path(raw_name).suffix.lower()
        if suffix not in MEDIA_EXTS:
            guessed = mimetypes.guess_extension(content_type) or ""
            suffix = guessed.lower()
            raw_name = f"{Path(raw_name).stem or '下载素材'}{suffix}"
        if suffix not in MEDIA_EXTS:
            raise ProcessError(f"这个链接没有返回支持的音视频文件（{content_type}）。")
        source = unique_output_path(Path(raw_name), folder, "_下载")
        total = int(headers.get("Content-Length") or 0)
        received = 0
        last_report = 0.0
        try:
            with source.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    received += len(chunk)
                    now = time.monotonic()
                    if now - last_report >= 0.4:
                        percent = round(received * 100 / total) if total > 0 else None
                        report(percent, "直链下载")
                        last_report = now
        except Exception:
            source.unlink(missing_ok=True)
            raise
    report(100, "下载完成")
    return source


def _download_platform_media(
    url: str, settings: dict, report: Callable[[int | None, str], None]
) -> Path:
    downloader = yt_dlp_path()
    ffmpeg = ffmpeg_path(settings)
    deno = deno_path()
    folder = _download_folder()
    folder.mkdir(parents=True, exist_ok=True)
    output_template = str(folder / "%(title).140B [%(id)s] %(epoch)s.%(ext)s")
    args = [
        downloader, "--no-playlist", "--no-colors", "--newline",
        "--format", "bestvideo*+bestaudio/best",
        "--merge-output-format", "mkv",
        "--ffmpeg-location", ffmpeg,
        "--output", output_template,
        "--print", "after_move:filepath",
        "--remote-components", "ejs:github",
    ]
    if deno:
        args.extend(("--js-runtimes", f"deno:{deno}"))
    args.append(url)

    download_started_at = time.time()
    try:
        process = subprocess.Popen(
            args, cwd=str(APP_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise ProcessError(f"无法启动网页下载器：{exc}") from exc

    output_lines: list[str] = []
    printed_paths: list[Path] = []
    if process.stdout is not None:
        for line in process.stdout:
            text_line = line.strip()
            if not text_line:
                continue
            output_lines.append(text_line)
            if len(output_lines) > 80:
                output_lines.pop(0)
            possible_path = Path(text_line.strip('"'))
            if possible_path.is_file():
                printed_paths.append(possible_path)
            match = re.search(r"(\d{1,3}(?:\.\d+)?)%", text_line)
            if match:
                report(max(0, min(100, round(float(match.group(1))))), "网页下载")
            elif any(stage in text_line.lower() for stage in ("merging", "extracting", "post-process")):
                report(None, "合并下载素材")
    result = process.wait()
    if result != 0:
        detail = "\n".join(output_lines[-12:]) or f"下载器退出代码 {result}"
        raise ProcessError(f"网页素材下载失败：\n{detail[-1800:]}")

    for candidate in reversed(printed_paths):
        if candidate.is_file() and candidate.suffix.lower() in MEDIA_EXTS:
            report(100, "下载完成")
            return candidate
    candidates = sorted(
        (path for path in folder.iterdir()
         if path.is_file() and path.suffix.lower() in MEDIA_EXTS
         and path.stat().st_mtime >= download_started_at - 1),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        report(100, "下载完成")
        return candidates[0]
    raise ProcessError("网页下载器结束了，但没有找到下载好的音视频文件。")


def download_media_link(
    url: str, settings: dict, report: Callable[[int | None, str], None]
) -> Path:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ProcessError("请粘贴以 http:// 或 https:// 开头的音视频链接。")
    if _url_is_direct_media(url):
        try:
            return _download_direct_media(url, report)
        except (OSError, ValueError, urllib.error.URLError, ProcessError) as exc:
            report(None, "直链下载失败，尝试网页解析")
            try:
                return _download_platform_media(url, settings, report)
            except Exception as fallback_error:
                raise ProcessError(f"直链下载失败：{exc}\n网页解析也失败：{fallback_error}") from fallback_error
    return _download_platform_media(url, settings, report)


def _prepare_wav(source: Path, destination: Path, ffmpeg: str) -> None:
    # DeepFilterNet expects 48 kHz WAV. The app uses mono/stereo for voice work.
    run_command([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-map", "0:a:0", "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(destination),
    ])


def _run_deepfilter(source_wav: Path, output_dir: Path, settings: dict) -> Path:
    binary = deep_filter_path(settings)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_command([binary, "-D", "-o", str(output_dir), str(source_wav)])
    outputs = sorted(output_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not outputs:
        raise ProcessError("DeepFilterNet 已运行，但没有生成 WAV 文件。")
    return outputs[0]


def _blend_audio(original_wav: Path, filtered_wav: Path, output_wav: Path, strength: int, ffmpeg: str) -> Path:
    wet = max(0.0, min(1.0, strength / 100.0))
    if wet <= 0.001:
        shutil.copy2(original_wav, output_wav)
        return output_wav
    if wet >= 0.999:
        shutil.copy2(filtered_wav, output_wav)
        return output_wav
    dry = 1.0 - wet
    run_command([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(original_wav), "-i", str(filtered_wav),
        "-filter_complex", f"[0:a][1:a]amix=inputs=2:weights='{dry:.3f} {wet:.3f}':normalize=0[a]",
        "-map", "[a]", "-ar", "48000", "-c:a", "pcm_s16le", str(output_wav),
    ])
    return output_wav


def process_audio_file(source: Path, destination: Path, settings: dict) -> Path:
    ffmpeg = ffmpeg_path(settings)
    strength = int(settings.get("strength", 75))
    with tempfile.TemporaryDirectory(prefix="denoise-") as work:
        workdir = Path(work)
        prepared = workdir / "source_48k.wav"
        _prepare_wav(source, prepared, ffmpeg)
        filtered = _run_deepfilter(prepared, workdir / "deepfilter_out", settings)
        blended = workdir / "denoised_mix.wav"
        _blend_audio(prepared, filtered, blended, strength, ffmpeg)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(blended, destination)
    return destination


def process_video_file(source: Path, destination: Path, settings: dict) -> Path:
    ffmpeg = ffmpeg_path(settings)
    strength = int(settings.get("strength", 75))
    with tempfile.TemporaryDirectory(prefix="denoise-video-") as work:
        workdir = Path(work)
        prepared = workdir / "source_48k.wav"
        _prepare_wav(source, prepared, ffmpeg)
        filtered = _run_deepfilter(prepared, workdir / "deepfilter_out", settings)
        mixed = workdir / "denoised_mix.wav"
        _blend_audio(prepared, filtered, mixed, strength, ffmpeg)

        destination.parent.mkdir(parents=True, exist_ok=True)
        audio_codec = "libopus" if destination.suffix.lower() == ".webm" else "aac"
        mux = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-i", str(mixed),
            "-map", "0:v?", "-map", "1:a:0", "-map_metadata", "0", "-map_chapters", "0",
            "-c:v", "copy", "-c:a", audio_codec, "-b:a", "192k", str(destination),
        ]
        try:
            run_command(mux)
        except ProcessError:
            # Some codecs cannot be muxed into the source container. MKV can keep
            # the original video stream without re-encoding it.
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            fallback = unique_output_path(source, destination.parent, "_降噪", ".mkv")
            fallback_mux = mux[:-1] + [str(fallback)]
            run_command(fallback_mux)
            destination = fallback
    return destination


def process_media(source: Path, settings: dict) -> Path:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise ProcessError(f"找不到素材：{source}")
    if source.suffix.lower() not in MEDIA_EXTS:
        raise ProcessError(f"暂不支持这种文件格式：{source.suffix or '无扩展名'}")
    out_dir = source.parent
    if source.suffix.lower() in VIDEO_EXTS:
        destination = unique_output_path(source, out_dir, "_降噪")
        return process_video_file(source, destination, settings)
    destination = unique_output_path(source, out_dir, "_降噪", ".wav")
    return process_audio_file(source, destination, settings)


class DenoiseAssistant:
    BG = "#0c0c0c"
    PANEL = "#161616"
    PANEL_ALT = "#101010"
    BORDER = "#353535"
    TEXT = "#f4f4f4"
    MUTED = "#c5c5c5"
    ACCENT = "#eeeeee"
    BUTTON_BG = "#222222"
    BUTTON_PRIMARY = "#3c3c3c"
    GREEN = "#f4f4f4"
    RED = "#ff7979"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = load_settings()
        self.files: list[Path] = []
        self.output_queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.is_processing = False
        self._configure_window()
        self.root.protocol("WM_DELETE_WINDOW", self.exit_assistant)
        self._build_ui()
        self._refresh_engine_status()
        self._fit_window_to_content()
        self.root.after(150, self._drain_queue)

    def _configure_window(self) -> None:
        self.root.title("降噪助手")
        self.root.geometry("390x600")
        self.root.minsize(370, 560)
        self.root.resizable(False, False)
        self.root.configure(bg=self.BG)
        self.root.attributes("-topmost", True)
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TCombobox", fieldbackground=self.PANEL_ALT, background=self.PANEL_ALT,
                        foreground=self.TEXT, arrowcolor=self.TEXT,
                        bordercolor=self.BORDER, lightcolor=self.BORDER, darkcolor=self.BORDER,
                        padding=(8, 5), font=("Microsoft YaHei UI", 10))
        style.map("TCombobox", fieldbackground=[("readonly", self.PANEL_ALT), ("disabled", self.PANEL)],
                  foreground=[("readonly", self.TEXT), ("disabled", self.MUTED)],
                  selectbackground=[("readonly", self.PANEL_ALT)],
                  selectforeground=[("readonly", self.TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", self.PANEL_ALT)
        self.root.option_add("*TCombobox*Listbox.foreground", self.TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#505050")
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        style.configure("Horizontal.TScale", background=self.PANEL, troughcolor="#454545")
        style.configure("Denoise.Horizontal.TProgressbar", troughcolor="#454545",
                        background="#eeeeee", bordercolor="#454545",
                        lightcolor="#eeeeee", darkcolor="#eeeeee", thickness=7)

    def _fit_window_to_content(self) -> None:
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(max(390, self.root.winfo_reqwidth()), max(370, screen_w - 40))
        height = min(max(560, self.root.winfo_reqheight()), max(560, screen_h - 80))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(width, 370), min(height, 560))

    def _card(self, parent: tk.Widget, title: str | None = None) -> tk.Frame:
        frame = tk.Frame(parent, bg=self.PANEL, highlightbackground=self.BORDER, highlightthickness=1)
        if title:
            tk.Label(frame, text=title, bg=self.PANEL, fg=self.TEXT, font=("Microsoft YaHei UI", 11, "bold"))\
                .pack(anchor="w", padx=9, pady=(7, 4))
        return frame

    def _button(self, parent: tk.Widget, text: str, command: Callable, primary: bool = False,
                compact: bool = False) -> RoundedButton:
        return RoundedButton(parent, text, command, self.BUTTON_BG, self.BUTTON_PRIMARY, primary, compact)

    def _build_ui(self) -> None:
        body = tk.Frame(self.root, bg=self.BG, padx=9, pady=6)
        body.pack(fill="both", expand=True)

        top = tk.Frame(body, bg=self.BG)
        top.pack(fill="x", pady=(0, 3))
        tk.Label(top, text="✂ 降噪助手", bg=self.BG, fg=self.TEXT,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")

        engine_card = self._card(body, "专业降噪 · 全部离线")
        engine_card.pack(fill="x", pady=(0, 5))
        engine_row = tk.Frame(engine_card, bg=self.PANEL)
        engine_row.pack(fill="x", padx=8)
        tk.Label(engine_row, text="降噪模块", bg=self.PANEL, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(0, 2))
        self.engine_var = tk.StringVar(value="现有降噪 · DeepFilterNet")
        self.engine_combo = ttk.Combobox(engine_row, textvariable=self.engine_var, state="readonly",
                                         values=("现有降噪 · DeepFilterNet",))
        self.engine_combo.pack(fill="x", ipady=1)
        self.engine_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_engine_status())
        self.engine_status = tk.Label(engine_card, text="", bg=self.PANEL, fg=self.MUTED, justify="left",
                                      wraplength=345, font=("Microsoft YaHei UI", 9))
        self.engine_status.pack(anchor="w", padx=8, pady=(4, 3))

        strength_row = tk.Frame(engine_card, bg=self.PANEL)
        strength_row.pack(fill="x", padx=8, pady=(0, 2))
        self.strength_var = tk.IntVar(value=max(0, min(100, int(self.settings.get("strength", 75)))))
        self.strength_label = tk.Label(strength_row, text="净化强度", bg=self.PANEL, fg=self.TEXT,
                                       font=("Microsoft YaHei UI", 9, "bold"))
        self.strength_label.pack(side="left")
        strength_controls = tk.Frame(engine_card, bg=self.PANEL)
        strength_controls.pack(fill="x", padx=8, pady=(0, 2))
        self.strength_canvas = tk.Canvas(strength_controls, height=32, bg=self.PANEL,
                                        highlightthickness=0, bd=0, cursor="hand2", takefocus=True)
        self.strength_canvas.pack(side="left", fill="x", expand=True)
        self.strength_canvas.bind("<Configure>", lambda _event: self._draw_strength_slider())
        self.strength_canvas.bind("<Button-1>", self._set_strength_from_event)
        self.strength_canvas.bind("<B1-Motion>", self._set_strength_from_event)
        strength_stepper = tk.Frame(strength_controls, bg=self.PANEL_ALT,
                                    highlightbackground=self.BORDER, highlightthickness=1)
        strength_stepper.pack(side="right", padx=(6, 0), pady=1)
        self._button(strength_stepper, "−", lambda: self._adjust_strength(-1), compact=True)\
            .pack(side="left", padx=(2, 1), pady=2)
        self.strength_value_label = tk.Label(strength_stepper, text="35%", width=4,
                                             bg=self.PANEL_ALT, fg=self.TEXT,
                                             font=("Microsoft YaHei UI", 9, "bold"))
        self.strength_value_label.pack(side="left")
        self._button(strength_stepper, "+", lambda: self._adjust_strength(1), compact=True)\
            .pack(side="left", padx=(1, 2), pady=2)
        tk.Label(engine_card, text="数值表示降噪音频的混入比例；越高降噪越明显，过高可能削弱人声细节。",
                 bg=self.PANEL, fg=self.MUTED, wraplength=345, justify="left",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=8, pady=(0, 3))
        self._update_strength()

        self.auto_close_var = tk.BooleanVar(value=True)
        tk.Checkbutton(engine_card, text="处理完成后收起助手", variable=self.auto_close_var,
                       bg=self.PANEL, fg=self.TEXT, activebackground=self.PANEL, activeforeground=self.TEXT,
                       selectcolor=self.PANEL_ALT, font=("Microsoft YaHei UI", 9),
                       padx=0, pady=0).pack(anchor="w", padx=6, pady=(0, 5))

        media_card = self._card(body, "本地音频降噪")
        self.local_card = media_card
        media_card.pack(fill="x", expand=False)
        tk.Label(media_card, text="拖入或添加音视频素材；视频保留画面并替换音轨。",
                 bg=self.PANEL, fg=self.MUTED, justify="left", wraplength=345,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=8, pady=(0, 4))

        link_row = tk.Frame(media_card, bg=self.PANEL)
        link_row.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(link_row, text="链接", bg=self.PANEL, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(0, 6))
        self.link_entry = tk.Entry(link_row, bg=self.PANEL_ALT, fg=self.TEXT,
                                   insertbackground=self.TEXT, relief="flat",
                                   font=("Microsoft YaHei UI", 9))
        self.link_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.link_entry.bind("<Return>", lambda _event: self.start_link_processing())
        self.link_button = self._button(link_row, "下载并降噪", self.start_link_processing, primary=True)
        self.link_button.pack(side="right", padx=(6, 0))

        list_holder = tk.Frame(media_card, bg=self.PANEL_ALT, highlightbackground=self.BORDER, highlightthickness=1)
        list_holder.pack(fill="x", padx=8)
        self.file_list = tk.Listbox(list_holder, bg=self.PANEL_ALT, fg=self.TEXT, selectbackground="#505050",
                                    selectforeground="white", highlightthickness=0, relief="flat",
                                    font=("Microsoft YaHei UI", 9), activestyle="none", height=2)
        self.file_list.pack(fill="x", padx=5, pady=4)
        self.file_list.insert("end", "还没有添加素材")
        self._enable_media_drop(list_holder)

        file_buttons = tk.Frame(media_card, bg=self.PANEL)
        file_buttons.pack(fill="x", padx=8, pady=2)
        self._button(file_buttons, "＋ 添加素材", self.add_files).pack(side="left", fill="x", expand=True)
        self._button(file_buttons, "移除选中", self.remove_selected).pack(side="left", fill="x", expand=True, padx=(6, 0))
        out_row = tk.Frame(media_card, bg=self.PANEL)
        out_row.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(out_row, text="输出位置", bg=self.PANEL, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        tk.Label(out_row, text="素材所在文件夹", bg=self.PANEL_ALT, fg=self.TEXT,
                 anchor="w", font=("Microsoft YaHei UI", 9)).pack(side="left", fill="x", expand=True, padx=(8, 0), ipady=2)
        self.process_button = self._button(media_card, "生成本地降噪素材", self.start_processing, primary=True)
        self.process_button.pack(fill="x", padx=8, pady=(0, 3))
        local_progress_row = tk.Frame(media_card, bg=self.PANEL)
        local_progress_row.pack(fill="x", padx=8, pady=(0, 4))
        self.local_progress_label = tk.Label(local_progress_row, text="等待素材", width=11,
                                             anchor="w", bg=self.PANEL, fg=self.MUTED,
                                             font=("Microsoft YaHei UI", 8))
        self.local_progress_label.pack(side="left")
        self.local_progressbar = ttk.Progressbar(local_progress_row, mode="determinate",
                                                 maximum=1, value=0,
                                                 style="Denoise.Horizontal.TProgressbar")
        self.local_progressbar.pack(side="left", fill="x", expand=True)

        self.status_var = tk.StringVar(value="准备就绪。")

    def exit_assistant(self) -> None:
        self.root.destroy()

    def _set_strength_from_event(self, event: tk.Event) -> None:
        width = max(1, self.strength_canvas.winfo_width())
        left, right = 12, max(13, width - 12)
        fraction = max(0.0, min(1.0, (event.x - left) / (right - left)))
        self.strength_var.set(round(fraction * 100))
        self._update_strength()

    def _draw_strength_slider(self) -> None:
        canvas = getattr(self, "strength_canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width <= 1 or height <= 1:
            return
        canvas.delete("all")
        left, right = 12, width - 12
        center_y = height // 2
        ratio = max(0.0, min(1.0, float(self.strength_var.get()) / 100.0))
        thumb_x = left + ratio * max(1, right - left)
        canvas.create_line(left, center_y, right, center_y,
                           fill="#464646", width=6, capstyle="round")
        if thumb_x > left:
            canvas.create_line(left, center_y, thumb_x, center_y,
                               fill=self.ACCENT, width=6, capstyle="round")
        if not hasattr(self, "_strength_thumb_image"):
            self._strength_thumb_image = tk.PhotoImage(
                master=canvas, data=RoundedButton._strength_thumb_png(), format="png")
        canvas.create_image(thumb_x, center_y, image=self._strength_thumb_image)

    def _adjust_strength(self, amount: int) -> None:
        self.strength_var.set(max(0, min(100, int(self.strength_var.get()) + amount)))
        self._update_strength()

    def _set_job_progress(self, current: int, total: int, state: str = "running") -> None:
        bar, label = self.local_progressbar, self.local_progress_label
        if total <= 0:
            bar.configure(maximum=1, value=0)
            label.configure(text="读取中" if state == "reading" else "无项目")
            return
        current = max(0, min(current, total))
        percent = round(current * 100 / total)
        bar.configure(maximum=total, value=current)
        if state == "reading":
            label.configure(text="读取中")
        elif state == "error":
            label.configure(text=f"失败 · {current}/{total}")
        else:
            label.configure(text=f"{current}/{total} · {percent}%")

    def _update_strength(self, _value: str = "") -> None:
        value = int(float(self.strength_var.get()))
        self.strength_value_label.configure(text=f"{value}%")
        self.settings["strength"] = value
        self._save()
        self._draw_strength_slider()

    def _refresh_engine_status(self) -> None:
        self.settings["engine"] = self.engine_var.get()
        ffmpeg = str(self.settings.get("ffmpeg", "")).strip() or find_program(("ffmpeg",), "ffmpeg.exe")
        if not ffmpeg or not Path(ffmpeg).is_file():
            self.engine_status.configure(text="请配置 ffmpeg.exe 后开始处理。", fg=self.RED)
        elif "DeepFilterNet" in self.engine_var.get():
            binary = str(self.settings.get("deep_filter", "")).strip() or find_program(("deep-filter", "deep_filter"), "deep-filter.exe")
            if binary and Path(binary).is_file():
                self.engine_status.configure(text="人声优先，适合持续环境底噪。DeepFilterNet 已就绪。", fg=self.GREEN)
            else:
                self.engine_status.configure(text="人声优先，适合持续环境底噪。请在“引擎设置”中选择 deep-filter.exe。", fg=self.MUTED)
        self._save()

    def _save(self) -> None:
        self.settings["engine"] = self.engine_var.get() if hasattr(self, "engine_var") else self.settings.get("engine")
        self.settings["strength"] = int(self.strength_var.get()) if hasattr(self, "strength_var") else self.settings.get("strength", 75)
        try:
            save_settings(self.settings)
        except OSError:
            pass

    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(title="选择音频或视频素材",
                                            filetypes=[("支持的媒体", "*.mp4 *.mov *.m4v *.mkv *.avi *.webm *.mts *.m2ts *.ts *.mxf *.wav *.wave *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.wma *.aiff *.aif"),
                                                       ("所有文件", "*.*")])
        self._add_media_paths(paths)

    def _enable_media_drop(self, list_holder: tk.Frame) -> None:
        """Accept Explorer file drops on both the list and its surrounding area."""
        self._media_list_holder = list_holder
        if DND_FILES is None or not hasattr(list_holder, "drop_target_register"):
            self._drop_target_ready = False
            return
        try:
            for widget in (list_holder, self.file_list):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<DropEnter>>", lambda _event: self._set_drop_highlight(list_holder, True))
                widget.dnd_bind("<<DropLeave>>", lambda _event: self._set_drop_highlight(list_holder, False))
                widget.dnd_bind("<<Drop>>", self._on_media_drop)
            self._drop_target_ready = True
        except (AttributeError, tk.TclError):
            self._drop_target_ready = False

    def _set_drop_highlight(self, list_holder: tk.Frame, active: bool) -> str:
        list_holder.configure(highlightbackground=self.ACCENT if active else self.BORDER)
        return "copy"

    def _on_media_drop(self, event) -> str:
        try:
            paths = self.root.tk.splitlist(event.data)
        except (tk.TclError, TypeError):
            paths = (str(event.data),)
        self._add_media_paths(paths)
        self._set_drop_highlight(self._media_list_holder, False)
        return "copy"

    def _add_media_paths(self, paths) -> None:
        existing = {os.path.normcase(os.path.abspath(str(item))) for item in self.files}
        added = 0
        skipped = 0
        for raw_path in paths:
            item = Path(str(raw_path)).expanduser()
            if item.suffix.lower() not in MEDIA_EXTS or not item.is_file():
                skipped += 1
                continue
            key = os.path.normcase(os.path.abspath(str(item)))
            if key in existing:
                continue
            self.files.append(item)
            existing.add(key)
            added += 1
        self._redraw_files()
        if added:
            self.status_var.set(f"已添加 {added} 个素材。")
        elif skipped:
            self.status_var.set("未添加文件：请拖入支持的音频或视频素材。")

    def remove_selected(self) -> None:
        indices = list(self.file_list.curselection())
        for index in reversed(indices):
            if 0 <= index < len(self.files):
                self.files.pop(index)
        self._redraw_files()

    def _redraw_files(self) -> None:
        self.file_list.delete(0, "end")
        if not self.files:
            self.file_list.insert("end", "还没有添加素材")
            return
        for item in self.files:
            kind = "视频" if item.suffix.lower() in VIDEO_EXTS else "音频"
            self.file_list.insert("end", f"{kind}   {item.name}")

    def configure_engines(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("降噪引擎设置")
        dialog.configure(bg=self.BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        panel = tk.Frame(dialog, bg=self.PANEL, highlightbackground=self.BORDER, highlightthickness=1)
        panel.pack(fill="both", expand=True, padx=12, pady=12)
        tk.Label(panel, text="本地程序路径", bg=self.PANEL, fg=self.TEXT,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 8))
        self.ffmpeg_var = tk.StringVar(value=self.settings.get("ffmpeg", ""))
        self.deep_var = tk.StringVar(value=self.settings.get("deep_filter", ""))
        self._path_row(panel, "ffmpeg.exe", self.ffmpeg_var, self._browse_ffmpeg)
        self._path_row(panel, "deep-filter.exe", self.deep_var, self._browse_deepfilter)
        tk.Label(panel, text="降噪核心固定使用 DeepFilterNet；FFmpeg 仅负责读取/转换媒体。",
                 bg=self.PANEL, fg=self.MUTED, justify="left", font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=12, pady=(8, 10))
        download_row = tk.Frame(panel, bg=self.PANEL)
        download_row.pack(fill="x", padx=12, pady=(0, 10))
        self._button(download_row, "FFmpeg 下载页", lambda: webbrowser.open("https://ffmpeg.org/download.html#build-windows"))\
            .pack(side="left", fill="x", expand=True)
        self._button(download_row, "DeepFilterNet 发布页", lambda: webbrowser.open("https://github.com/Rikorose/DeepFilterNet/releases"))\
            .pack(side="left", fill="x", expand=True, padx=(6, 0))
        self._button(panel, "保存设置", lambda: self._save_engine_settings(dialog), primary=True).pack(fill="x", padx=12, pady=(0, 12))
        dialog.update_idletasks()
        dialog.geometry(f"{dialog.winfo_reqwidth()}x{dialog.winfo_reqheight()}")

    def _path_row(self, parent: tk.Widget, label: str, var: tk.StringVar, command: Callable) -> None:
        row = tk.Frame(parent, bg=self.PANEL)
        row.pack(fill="x", padx=12, pady=4)
        tk.Label(row, text=label, width=15, anchor="w", bg=self.PANEL, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(side="left")
        entry = tk.Entry(row, textvariable=var, bg=self.PANEL_ALT, fg=self.TEXT, insertbackground=self.TEXT,
                         relief="flat", font=("Microsoft YaHei UI", 8))
        entry.pack(side="left", fill="x", expand=True, padx=6, ipady=5)
        self._button(row, "浏览", command).pack(side="right")

    def _browse_ffmpeg(self) -> None:
        path = filedialog.askopenfilename(title="选择 ffmpeg.exe", filetypes=[("ffmpeg.exe", "ffmpeg.exe"), ("程序", "*.exe")])
        if path:
            self.ffmpeg_var.set(path)

    def _browse_deepfilter(self) -> None:
        path = filedialog.askopenfilename(title="选择 DeepFilterNet 命令行程序", filetypes=[("Windows 程序", "*.exe"), ("所有文件", "*.*")])
        if path:
            self.deep_var.set(path)

    def _save_engine_settings(self, dialog: tk.Toplevel) -> None:
        self.settings["ffmpeg"] = self.ffmpeg_var.get().strip()
        self.settings["deep_filter"] = self.deep_var.get().strip()
        self._save()
        self._refresh_engine_status()
        dialog.destroy()
        self.status_var.set("引擎路径已保存。")

    def start_processing(self) -> None:
        if self.is_processing:
            return
        if not self.files:
            messagebox.showinfo("添加素材", "请先添加要处理的音频或视频素材。", parent=self.root)
            return
        self._save()
        try:
            ffmpeg_path(self.settings)
            deep_filter_path(self.settings)
        except ProcessError as exc:
            messagebox.showerror("引擎未就绪", str(exc), parent=self.root)
            return
        files = list(self.files)
        self.is_processing = True
        self.process_button.configure(state="disabled")
        self._set_job_progress(0, len(files))
        self.status_var.set(f"正在处理 0/{len(files)}…")
        self.worker = threading.Thread(target=self._process_batch, args=(files,), daemon=True)
        self.worker.start()

    def start_link_processing(self) -> None:
        if self.is_processing:
            return
        url = self.link_entry.get().strip()
        if not url:
            messagebox.showinfo("添加链接", "请粘贴音视频文件直链或公开视频页面链接。", parent=self.root)
            self.link_entry.focus_set()
            return
        self._save()
        try:
            ffmpeg_path(self.settings)
            deep_filter_path(self.settings)
        except ProcessError as exc:
            messagebox.showerror("引擎未就绪", str(exc), parent=self.root)
            return
        self.is_processing = True
        self.process_button.configure(state="disabled")
        self.link_button.configure(state="disabled")
        self._set_link_progress(None, "准备下载")
        self.status_var.set("正在下载链接素材…")
        self.worker = threading.Thread(
            target=self._download_and_process_link,
            args=(url, dict(self.settings)),
            daemon=True,
        )
        self.worker.start()

    def _download_and_process_link(self, url: str, settings: dict) -> None:
        successes: list[Path] = []
        failures: list[tuple[Path, str]] = []

        def report(percent: int | None, stage: str) -> None:
            self.output_queue.put(("link_progress", (percent, stage)))

        try:
            source = download_media_link(url, settings, report)
            self.output_queue.put(("link_downloaded", source))
            report(None, "下载完成，正在降噪")
            successes.append(process_media(source, settings))
        except Exception as exc:
            failures.append((Path("链接素材"), str(exc)))
        self.output_queue.put(("done", (successes, failures)))

    def _process_batch(self, files: list[Path]) -> None:
        successes: list[Path] = []
        failures: list[tuple[Path, str]] = []
        for index, source in enumerate(files, start=1):
            self.output_queue.put(("status", f"正在处理 {index}/{len(files)}：{source.name}"))
            try:
                output = process_media(source, self.settings)
                successes.append(output)
            except Exception as exc:
                failures.append((source, str(exc)))
            self.output_queue.put(("job_progress", (index, len(files), "running")))
        self.output_queue.put(("done", (successes, failures)))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, value = self.output_queue.get_nowait()
                if kind == "status":
                    self.status_var.set(value)
                    visible_status = value.split("：", 1)[0]
                    self.local_progress_label.configure(text=visible_status)
                elif kind == "job_progress":
                    self._set_job_progress(*value)
                elif kind == "link_progress":
                    self._set_link_progress(*value)
                elif kind == "link_downloaded":
                    self._add_media_paths((str(value),))
                elif kind == "done":
                    successes, failures = value
                    self.is_processing = False
                    self.process_button.configure(state="normal")
                    self.link_button.configure(state="normal")
                    if self.local_progressbar.cget("mode") == "indeterminate":
                        self.local_progressbar.stop()
                        self.local_progressbar.configure(mode="determinate", maximum=100, value=0)
                    total = len(successes) + len(failures)
                    self._set_job_progress(total, total)
                    self.local_progress_label.configure(text=f"完成 {len(successes)}/{total}")
                    self.status_var.set(f"处理完成：{len(successes)} 个成功，{len(failures)} 个失败。")
                    if self.auto_close_var.get() and successes and not failures:
                        self.root.iconify()
                    elif failures:
                        messagebox.showwarning("处理完成，但有失败项",
                                               "\n".join(f"{p.name}: {e}" for p, e in failures[:4]), parent=self.root)
                    elif successes:
                        if messagebox.askyesno("处理完成", f"已生成 {len(successes)} 个降噪素材。\n要打开输出文件夹吗？", parent=self.root):
                            try:
                                os.startfile(str(successes[0].parent))
                            except OSError:
                                pass
        except queue.Empty:
            pass
        self.root.after(150, self._drain_queue)

    def _set_link_progress(self, percent: int | None, stage: str) -> None:
        bar = self.local_progressbar
        if percent is None:
            if bar.cget("mode") != "indeterminate":
                bar.stop()
                bar.configure(mode="indeterminate", maximum=100, value=0)
                bar.start(12)
            self.local_progress_label.configure(text=stage)
            return
        if bar.cget("mode") == "indeterminate":
            bar.stop()
            bar.configure(mode="determinate", maximum=100, value=0)
        percent = max(0, min(100, int(percent)))
        bar.configure(maximum=100, value=percent)
        self.local_progress_label.configure(text=f"{stage} {percent}%")

def main() -> int:
    try:
        root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    except (RuntimeError, tk.TclError):
        # A broken native tkdnd binary must not prevent the app from opening.
        # The Add Files button remains available if drag-and-drop cannot load.
        root = tk.Tk()
    DenoiseAssistant(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
