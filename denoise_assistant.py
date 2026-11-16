from __future__ import annotations

import base64
import json
import os
import queue
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
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

from resolve_bridge import (
    ResolveBridgeError,
    ResolveConsoleBridge,
    activate_resolve_and_run_console,
    append_outputs_to_new_tracks,
    current_resolve_state,
    install_console_bridge,
    install_workspace_menu_script,
    render_audio_track_to_wav,
    resolve_window_available,
)


APP_NAME = "降噪助手"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "DenoiseAssistant"
CONFIG_FILE = CONFIG_DIR / "settings.json"
SHOW_REQUEST_FILE = CONFIG_DIR / "show_assistant.request"
RESOLVE_CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DenoiseAssistant" / "ResolveCache"
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mts", ".m2ts", ".ts", ".mxf"}
AUDIO_EXTS = {".wav", ".wave", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff", ".aif"}
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
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    bundled = APP_DIR / "tools" / bundled_name
    return str(bundled) if bundled.is_file() else ""


def default_settings() -> dict:
    return {
        "ffmpeg": find_program(("ffmpeg",), "ffmpeg.exe"),
        "deep_filter": find_program(("deep-filter", "deep_filter"), "deep-filter.exe"),
        "engine": "DeepFilterNet 人声降噪",
        "strength": 35,
        "output_dir": str(Path.home() / "Downloads"),
    }


def load_settings() -> dict:
    settings = default_settings()
    try:
        saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            settings.update(saved)
    except (OSError, json.JSONDecodeError):
        pass
    if not str(settings.get("output_dir", "")).strip():
        settings["output_dir"] = str(Path.home() / "Downloads")
    return settings


def save_settings(settings: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def start_hidden_menu_watcher() -> None:
    if os.name != "nt":
        return
    try:
        subprocess.Popen(
            [sys.executable, str(APP_DIR / "resolve_menu_watcher.pyw")],
            cwd=str(APP_DIR), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        pass


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
    path = str(settings.get("ffmpeg", "")).strip() or find_program(("ffmpeg",), "ffmpeg.exe")
    if not path or not Path(path).is_file():
        raise ProcessError("没有找到 ffmpeg.exe。请在“引擎设置”中选择 ffmpeg.exe。")
    return path


def deep_filter_path(settings: dict) -> str:
    path = str(settings.get("deep_filter", "")).strip() or find_program(("deep-filter", "deep_filter"), "deep-filter.exe")
    if not path or not Path(path).is_file():
        raise ProcessError("没有找到 DeepFilterNet 命令行程序。请在“引擎设置”中选择 deep-filter.exe。")
    return path


def unique_output_path(source: Path, output_dir: Path, suffix: str, extension: str | None = None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ext = extension or source.suffix
    candidate = output_dir / f"{source.stem}{suffix}{ext}"
    index = 2
    while candidate.exists():
        candidate = output_dir / f"{source.stem}{suffix}_{index}{ext}"
        index += 1
    return candidate


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
    strength = int(settings.get("strength", 35))
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
    strength = int(settings.get("strength", 35))
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


def process_media(source: Path, output_dir: Path | None, settings: dict) -> Path:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise ProcessError(f"找不到素材：{source}")
    if source.suffix.lower() not in MEDIA_EXTS:
        raise ProcessError(f"暂不支持这种文件格式：{source.suffix or '无扩展名'}")
    out_dir = output_dir or Path(str(settings.get("output_dir", "")).strip()).expanduser()
    if not out_dir or str(out_dir) == ".":
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
        self.resolve_checking = False
        self.resolve_connected = False
        self.f6_autostart_attempted = False
        self._timeline_progress_indeterminate = False
        self.audio_track_count = 1
        self._configure_window()
        start_hidden_menu_watcher()
        self.root.protocol("WM_DELETE_WINDOW", self.exit_assistant)
        self._build_ui()
        self._refresh_engine_status()
        self.root.after(150, self._drain_queue)
        try:
            install_workspace_menu_script()
        except ResolveBridgeError as exc:
            self.resolve_status.configure(text=f"工作区启动脚本写入失败：{exc}", fg=self.RED)
        self.root.after(800, self._auto_connect_resolve)
        self.root.after(400, self._poll_launch_requests)
        self.root.after(1200, self._poll_resolve_window)

    def _configure_window(self) -> None:
        self.root.title("降噪助手")
        self.root.geometry("390x626")
        self.root.minsize(370, 586)
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
        self.refresh_button = self._button(top, "↻ 刷新达芬奇", self.manual_refresh_resolve_state)
        self.refresh_button.pack(side="right")

        self.conn_label = tk.Label(body, text="● 等待达芬奇连接", bg=self.BG, fg=self.MUTED,
                                   anchor="w", justify="left", wraplength=355,
                                   font=("Microsoft YaHei UI", 9))
        self.conn_label.pack(fill="x", pady=(0, 4))

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
        self.strength_var = tk.IntVar(value=max(0, min(100, int(self.settings.get("strength", 35)))))
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
        tk.Label(engine_card, text="听感未过度降噪可降低强度；百分比不代表相同的降噪量。",
                 bg=self.PANEL, fg=self.MUTED, wraplength=345, justify="left",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=8, pady=(0, 3))
        self._update_strength()

        self.auto_close_var = tk.BooleanVar(value=True)
        tk.Checkbutton(engine_card, text="处理完成后收起助手", variable=self.auto_close_var,
                       bg=self.PANEL, fg=self.TEXT, activebackground=self.PANEL, activeforeground=self.TEXT,
                       selectcolor=self.PANEL_ALT, font=("Microsoft YaHei UI", 9),
                       padx=0, pady=0).pack(anchor="w", padx=6, pady=(0, 5))

        tabs = tk.Frame(body, bg=self.BG)
        tabs.pack(fill="x", pady=(0, 4))
        self.timeline_tab = self._button(tabs, "达芬奇时间线", lambda: self.show_page("timeline"))
        self.local_tab = self._button(tabs, "本地降噪", lambda: self.show_page("local"))
        self.timeline_tab.pack(side="left", fill="x", expand=True, padx=(0, 3))
        self.local_tab.pack(side="left", fill="x", expand=True, padx=(3, 0))
        self.page_host = tk.Frame(body, bg=self.BG)
        self.page_host.pack(fill="both", expand=True)

        self.timeline_page = tk.Frame(self.page_host, bg=self.BG)
        self.local_page = tk.Frame(self.page_host, bg=self.BG)

        timeline_card = self._card(self.timeline_page, "当前时间线")
        timeline_card.pack(fill="x", pady=(0, 4))
        timeline_row = tk.Frame(timeline_card, bg=self.PANEL)
        timeline_row.pack(fill="x", padx=8, pady=(0, 6))
        self.timeline_var = tk.StringVar(value="等待达芬奇")
        tk.Label(timeline_row, textvariable=self.timeline_var, bg=self.PANEL, fg=self.TEXT,
                 anchor="w", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left", fill="x", expand=True)
        track_picker = tk.Frame(timeline_row, bg=self.PANEL)
        track_picker.pack(side="right", padx=(6, 0))
        tk.Label(track_picker, text="处理音轨", bg=self.PANEL, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(0, 1))
        self.audio_track_var = tk.StringVar(value="A1")
        self.audio_track_combo = ttk.Combobox(track_picker, textvariable=self.audio_track_var,
                                              state="readonly", width=13, values=("A1",))
        self.audio_track_combo.pack(fill="x", ipady=0)
        self.audio_track_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_output_track_label())
        self.output_track_var = tk.StringVar(value="自动新建 A2")
        self.resolve_process_button = self._button(self.timeline_page, "生成降噪并添加到达芬奇",
                                                   self.start_resolve_processing, primary=True)
        self.resolve_process_button.pack(fill="x", padx=8, pady=(0, 3))
        resolve_progress_row = tk.Frame(self.timeline_page, bg=self.BG)
        resolve_progress_row.pack(fill="x", padx=8, pady=(0, 3))
        self.resolve_progress_label = tk.Label(resolve_progress_row, text="等待处理", width=16,
                                               anchor="w", bg=self.BG, fg=self.MUTED,
                                               font=("Microsoft YaHei UI", 8))
        self.resolve_progress_label.pack(side="left")
        self.resolve_progressbar = ttk.Progressbar(resolve_progress_row, mode="determinate",
                                                   maximum=1, value=0,
                                                   style="Denoise.Horizontal.TProgressbar")
        self.resolve_progressbar.pack(side="left", fill="x", expand=True)
        self.resolve_status = tk.Label(self.timeline_page,
                                       text="Resolve 先导出所选音轨 WAV；降噪后按时间线位置回填新轨道。",
                                       bg=self.BG, fg=self.MUTED, justify="left", wraplength=355,
                                       font=("Microsoft YaHei UI", 9))
        self.resolve_status.pack(anchor="w", fill="x", pady=(0, 4))
        self.cache_card = tk.Frame(self.timeline_page, bg=self.BG)
        self.cache_card.pack(fill="x", pady=(0, 3))
        self._button(self.cache_card, "删除七天前的缓存", self.clear_cache).pack(fill="x", padx=8, pady=(3, 2))
        tk.Label(self.cache_card,
                 text="Resolve 降噪 WAV 单独缓存；只清除超过七天的文件。",
                 bg=self.BG, fg=self.MUTED, justify="left", wraplength=345,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=8, pady=(0, 3))

        media_card = self._card(self.local_page, "本地音频降噪")
        self.local_card = media_card
        media_card.pack(fill="both", expand=True)
        tk.Label(media_card, text="拖入或添加音视频素材；视频保留画面并替换音轨。",
                 bg=self.PANEL, fg=self.MUTED, justify="left", wraplength=345,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=8, pady=(0, 4))

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
        self.output_var = tk.StringVar(value=self.settings.get("output_dir", ""))
        out_row = tk.Frame(media_card, bg=self.PANEL)
        out_row.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(out_row, text="输出位置", bg=self.PANEL, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.output_entry = tk.Entry(out_row, textvariable=self.output_var, bg=self.PANEL_ALT, fg=self.TEXT,
                                     insertbackground=self.TEXT, relief="flat", font=("Microsoft YaHei UI", 9))
        self.output_entry.pack(side="left", fill="x", expand=True, padx=6, ipady=1)
        self._button(out_row, "浏览", self.choose_output_dir, compact=True).pack(side="right")
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
        self.current_page = "local"
        self.show_page("local")

    def exit_assistant(self) -> None:
        self.root.destroy()

    def show_page(self, page: str) -> None:
        self.current_page = page if page in {"timeline", "local"} else "local"
        self.timeline_page.pack_forget()
        self.local_page.pack_forget()
        timeline_active = self.current_page == "timeline"
        if timeline_active:
            self.timeline_page.pack(fill="both", expand=True)
        else:
            self.local_page.pack(fill="both", expand=True)
        for button, active in ((self.timeline_tab, timeline_active), (self.local_tab, not timeline_active)):
            button._font = ("Microsoft YaHei UI", 9, "bold" if active else "normal")
            button.set_active(active)

    def _update_output_track_label(self) -> None:
        self.output_track_var.set(f"自动新建 A{max(1, self.audio_track_count) + 1}")

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

    def _set_job_progress(self, mode: str, current: int, total: int, state: str = "running") -> None:
        if mode == "timeline":
            bar, label = self.resolve_progressbar, self.resolve_progress_label
        else:
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

    def _set_timeline_progress(self, stage: str, percent: int, indeterminate: bool = False) -> None:
        percent = max(0, min(100, int(percent)))
        bar = self.resolve_progressbar
        if indeterminate:
            if not self._timeline_progress_indeterminate:
                bar.stop()
                bar.configure(mode="indeterminate", maximum=100, value=0)
                bar.start(12)
                self._timeline_progress_indeterminate = True
            self.resolve_progress_label.configure(text=stage)
            return
        if self._timeline_progress_indeterminate:
            bar.stop()
            self._timeline_progress_indeterminate = False
        bar.configure(mode="determinate", maximum=100, value=percent)
        self.resolve_progress_label.configure(text=f"{stage} · {percent}%")

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
        self.settings["strength"] = int(self.strength_var.get()) if hasattr(self, "strength_var") else self.settings.get("strength", 35)
        self.settings["output_dir"] = self.output_var.get().strip() if hasattr(self, "output_var") else self.settings.get("output_dir", "")
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

    def choose_output_dir(self) -> None:
        folder = filedialog.askdirectory(title="选择输出文件夹", initialdir=self.output_var.get() or str(Path.home()))
        if folder:
            self.output_var.set(folder)
            self._save()

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

    def start_resolve_script(self) -> None:
        try:
            line = install_console_bridge()
        except ResolveBridgeError as exc:
            self.resolve_status.configure(text=f"安装 F6 桥接失败：{exc}", fg=self.RED)
            self.status_var.set("达芬奇连接失败。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(line)
        self.root.update()
        # Let Resolve rise above the assistant for F6 input. Restore the
        # requested always-on-top behavior immediately after the handoff.
        self.root.attributes("-topmost", False)
        try:
            started, detail = activate_resolve_and_run_console(line)
        finally:
            self.root.attributes("-topmost", True)
        if started:
            self.resolve_status.configure(text=f"{detail} 正在同步时间线和音轨…", fg=self.GREEN)
            self.status_var.set("F6 控制台已收起，正在连接。")
            self.root.after(1700, self.refresh_resolve_state)
        else:
            self.resolve_status.configure(text=detail, fg=self.MUTED)
            self.status_var.set("启动代码已复制，等待手动输入。")

    def _auto_connect_resolve(self) -> None:
        if resolve_window_available():
            self.refresh_resolve_state()
        else:
            self.resolve_status.configure(text="本地素材模式可直接使用。打开 Resolve 后点“刷新达芬奇”自动连接。")

    def _poll_launch_requests(self) -> None:
        show_assistant = False
        try:
            if SHOW_REQUEST_FILE.is_file():
                SHOW_REQUEST_FILE.unlink(missing_ok=True)
                show_assistant = True
        except OSError:
            pass
        if show_assistant:
            self._bring_assistant_forward()
            self.status_var.set("已打开降噪助手。")
        self.root.after(400, self._poll_launch_requests)

    def _poll_resolve_window(self) -> None:
        if resolve_window_available():
            if not self.resolve_connected and not self.resolve_checking and not self.f6_autostart_attempted:
                self.refresh_resolve_state()
        else:
            self.resolve_connected = False
            self.f6_autostart_attempted = False
        self.root.after(1500, self._poll_resolve_window)

    def refresh_resolve_state(self) -> None:
        if self.resolve_checking or self.is_processing:
            return
        self.resolve_checking = True
        self.refresh_button.configure(state="disabled")
        self.resolve_status.configure(text="正在连接 Resolve 并读取音轨…")
        threading.Thread(target=self._resolve_state_worker, daemon=True).start()

    def manual_refresh_resolve_state(self) -> None:
        # A manual retry is the user's signal to retry the F6 connection once.
        self.f6_autostart_attempted = False
        self.refresh_resolve_state()

    def _resolve_state_worker(self) -> None:
        try:
            state = current_resolve_state(ResolveConsoleBridge(timeout=1.2))
            self.output_queue.put(("resolve_state", state))
        except Exception as exc:
            if resolve_window_available():
                self.output_queue.put(("resolve_need_f6", str(exc)))
            else:
                self.output_queue.put(("resolve_offline", str(exc)))

    def start_resolve_processing(self) -> None:
        if self.is_processing or self.resolve_checking:
            return
        try:
            ffmpeg_path(self.settings)
            deep_filter_path(self.settings)
        except ProcessError as exc:
            messagebox.showerror("引擎未就绪", str(exc), parent=self.root)
            return
        self._save()
        output_dir = RESOLVE_CACHE_DIR
        track_index = 1
        try:
            track_index = int(self.audio_track_var.get().strip().lstrip("A"))
        except ValueError:
            pass
        self.is_processing = True
        self.process_button.configure(state="disabled")
        self.resolve_process_button.configure(state="disabled")
        self._set_timeline_progress("准备导出 WAV", 0)
        self.status_var.set(f"正在导出音轨 A{track_index}…")
        self.worker = threading.Thread(target=self._process_resolve_track,
                                       args=(dict(self.settings), output_dir, track_index), daemon=True)
        self.worker.start()

    def _process_resolve_track(self, settings: dict, output_dir: Path, track_index: int) -> None:
        outputs: list[Path] = []
        tracks: list[str] = []
        try:
            client = ResolveConsoleBridge(timeout=8.0)
            status = client.health()
            if not status.get("connected"):
                raise ResolveBridgeError("Resolve 脚本桥接尚未连接。")
            timeline_name = str(client.call("current_timeline", "GetName") or "当前时间线")
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_timeline = "".join(
                "_" if char in '<>:\"/\\|?*' else char for char in timeline_name
            ).strip(" .") or "Timeline"
            destination = unique_output_path(
                Path(safe_timeline), output_dir, f"_A{track_index}_降噪", ".wav"
            )
            with tempfile.TemporaryDirectory(prefix="denoise-resolve-export-") as work:
                render_source = Path(work) / f"A{track_index}_timeline.wav"

                def on_render_progress(stage: str, percent: int, indeterminate: bool) -> None:
                    self.output_queue.put((
                        "timeline_progress", (f"导出 A{track_index} WAV", round(percent * 0.35), indeterminate)
                    ))

                render_info, _clips = render_audio_track_to_wav(
                    client, timeline_name, track_index, render_source, on_render_progress
                )
                self.output_queue.put(("timeline_progress", ("DeepFilterNet 降噪", 38, True)))
                self.output_queue.put(("status", f"正在降噪 Resolve 导出的 A{track_index} WAV…"))
                output = process_audio_file(Path(render_info["path"]), destination, settings)
                outputs.append(output)
            self.output_queue.put(("timeline_progress", ("回填新音轨", 94, False)))
            placement = [{"record_frame": render_info["mark_in"], "source_track": track_index}]
            tracks = append_outputs_to_new_tracks(
                client, timeline_name, placement, outputs,
                expected_timeline_id=str(render_info.get("timeline_id") or ""),
            )
            self.output_queue.put(("resolve_done", (outputs, tracks, "")))
        except Exception as exc:
            self.output_queue.put(("resolve_done", (outputs, tracks, str(exc))))

    def start_processing(self) -> None:
        if self.is_processing or self.resolve_checking:
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
        output_dir = Path(self.output_var.get().strip()).expanduser() if self.output_var.get().strip() else None
        files = list(self.files)
        self.is_processing = True
        self.process_button.configure(state="disabled")
        self.resolve_process_button.configure(state="disabled")
        self._set_job_progress("local", 0, len(files))
        self.status_var.set(f"正在处理 0/{len(files)}…")
        self.worker = threading.Thread(target=self._process_batch, args=(files, output_dir), daemon=True)
        self.worker.start()

    def _process_batch(self, files: list[Path], output_dir: Path | None) -> None:
        successes: list[Path] = []
        failures: list[tuple[Path, str]] = []
        for index, source in enumerate(files, start=1):
            self.output_queue.put(("status", f"正在处理 {index}/{len(files)}：{source.name}"))
            try:
                output = process_media(source, output_dir, self.settings)
                successes.append(output)
            except Exception as exc:
                failures.append((source, str(exc)))
            self.output_queue.put(("job_progress", ("local", index, len(files), "running")))
        self.output_queue.put(("done", (successes, failures)))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, value = self.output_queue.get_nowait()
                if kind == "status":
                    self.status_var.set(value)
                elif kind == "job_progress":
                    self._set_job_progress(*value)
                elif kind == "timeline_progress":
                    self._set_timeline_progress(*value)
                elif kind == "resolve_state":
                    self.resolve_checking = False
                    self.resolve_connected = True
                    self.refresh_button.configure(state="normal")
                    health = value.get("health") or {}
                    tracks = value.get("audio_tracks") or []
                    timeline_name = value.get("timeline_name") or "当前时间线"
                    project = health.get("current_project") or "项目"
                    version = health.get("version") or "Resolve"
                    track_labels = [f"A{item['index']}" for item in tracks]
                    self.audio_track_count = len(track_labels)
                    self.audio_track_combo.configure(values=track_labels or ("A1",))
                    if self.audio_track_var.get() not in track_labels and track_labels:
                        self.audio_track_var.set(track_labels[0])
                    self.timeline_var.set(timeline_name)
                    self._update_output_track_label()
                    self.conn_label.configure(text=f"● 已连接：{project} / {timeline_name}", fg=self.GREEN)
                    self.resolve_status.configure(text=f"{version} 已连接。所选音轨会由 Resolve 导出为 WAV，降噪后自动回填到新音轨。", fg=self.MUTED)
                    self.status_var.set("达芬奇连接成功。")
                    self.root.after(80, self._bring_assistant_forward)
                elif kind == "resolve_need_f6":
                    self.resolve_checking = False
                    self.resolve_connected = False
                    self.refresh_button.configure(state="normal")
                    if not self.f6_autostart_attempted:
                        self.f6_autostart_attempted = True
                        self.start_resolve_script()
                    else:
                        self.resolve_status.configure(
                            text="自动连接未成功。确认 Resolve 已打开并加载项目后，再点“刷新达芬奇”重试。",
                            fg=self.RED,
                        )
                        self.status_var.set("F6 桥接未连接；可手动重试。")
                elif kind == "resolve_offline":
                    self.resolve_checking = False
                    self.resolve_connected = False
                    self.refresh_button.configure(state="normal")
                    self.conn_label.configure(text="● 等待达芬奇连接", fg=self.MUTED)
                    self.resolve_status.configure(text="打开 Resolve 项目后点“刷新达芬奇”；助手会自动启动并收起 F6 控制台。", fg=self.MUTED)
                    self.status_var.set("本地模式可独立使用。")
                elif kind == "resolve_done":
                    outputs, tracks, error = value
                    self.is_processing = False
                    self.process_button.configure(state="normal")
                    self.resolve_process_button.configure(state="normal")
                    if error:
                        completed = int(float(self.resolve_progressbar["value"]))
                        self._set_timeline_progress("失败", completed)
                        self.status_var.set(f"Resolve 降噪未完成：{error[:100]}")
                        messagebox.showerror("Resolve 降噪未完成", error, parent=self.root)
                    else:
                        self._set_timeline_progress("完成", 100)
                        track_text = "、".join(tracks)
                        source_track = self.audio_track_var.get().strip()
                        self.status_var.set(f"Resolve 处理完成：{source_track} WAV 已降噪并放入 {track_text}。")
                        self.resolve_status.configure(text=f"{source_track} 已导出 WAV 并完成降噪，结果已放入新音轨 {track_text}。原音轨保留。", fg=self.GREEN)
                        if self.auto_close_var.get():
                            self.root.iconify()
                        elif messagebox.askyesno("Resolve 降噪完成", f"{source_track} 已导出、降噪并添加到新音轨 {track_text}。\n要打开降噪文件夹吗？", parent=self.root):
                            try:
                                os.startfile(str(outputs[0].parent))
                            except (OSError, IndexError):
                                pass
                elif kind == "done":
                    successes, failures = value
                    self.is_processing = False
                    self.process_button.configure(state="normal")
                    self.resolve_process_button.configure(state="normal")
                    total = len(successes) + len(failures)
                    self._set_job_progress("local", total, total)
                    if failures:
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

    def _bring_assistant_forward(self) -> None:
        try:
            if self.root.state() == "iconic":
                self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def clear_cache(self) -> None:
        # Resolve audio outputs have a dedicated cache. Keep the latest seven days.
        removed_files = 0
        cutoff = time.time() - 7 * 24 * 3600
        try:
            RESOLVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            for path in RESOLVE_CACHE_DIR.iterdir():
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed_files += 1
                except OSError:
                    pass
        except OSError:
            pass

        # Also clean abandoned temporary work directories older than seven days.
        temp_root = Path(tempfile.gettempdir())
        removed_dirs = 0
        for pattern in ("denoise-*", "denoise-video-*", "denoise-resolve-*"):
            for path in temp_root.glob(pattern):
                try:
                    if path.is_dir() and path.stat().st_mtime < cutoff:
                        shutil.rmtree(path)
                        removed_dirs += 1
                except OSError:
                    pass
        self.status_var.set(f"已删除 {removed_files} 个超过七天的音轨缓存和 {removed_dirs} 个临时目录。")


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
