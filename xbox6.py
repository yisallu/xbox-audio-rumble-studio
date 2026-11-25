"""Xbox Audio Rumble Studio

把 Windows 系统声音实时转换为 Xbox 手柄震动反馈。
特性：
- WASAPI Loopback 非阻塞实时音频捕获与断线自动重连
- 独立高频手柄轮询：同时按下手柄 LB + RB 随时开启/关闭震动（带触觉确认反馈）
- 简洁人性化的现代深色卡片 UI，高 DPI 自适应缩放
"""

import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

import numpy as np
import pyaudiowpatch as pyaudio
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk


APP_TITLE = "Xbox Audio Rumble Studio"
APP_PATH = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ICON_FILE = RESOURCE_DIR / "app.ico"
CONFIG_FILE = APP_PATH.with_suffix(".settings.json")

XINPUT_SUCCESS = 0
XINPUT_GAMEPAD_LEFT_SHOULDER = 0x0100
XINPUT_GAMEPAD_RIGHT_SHOULDER = 0x0200
LB_RB_COMBO_MASK = XINPUT_GAMEPAD_LEFT_SHOULDER | XINPUT_GAMEPAD_RIGHT_SHOULDER

LOG_LINE_LIMIT = 200
MAX_RMS_DISPLAY = 5500.0

# ---------------------------------------------------------------------------
# 主题配色（午夜丝绒声学暗夜 · Acoustic Halo Studio）
# ---------------------------------------------------------------------------

BG_WINDOW = "#090b12"
BG_CARD = "#111420"
BG_CARD_HI = "#191e30"
BG_INSET = "#0a0d16"
BG_BTN = "#171d2e"
BG_BTN_HOVER = "#212a42"

BORDER = "#1e253a"
BORDER_HI = "#334061"
RIM_TOP = "#2b3552"

TEXT_MAIN = "#f4f6fc"
TEXT_SUB = "#9aa6c2"
TEXT_MUTED = "#606d8a"

GREEN = "#10b981"
GREEN_HI = "#34d399"
GREEN_BG = "#0d2620"

BLUE = "#3b82f6"
BLUE_HI = "#60a5fa"
BLUE_BG = "#122242"

CYAN = "#06b6d4"
CYAN_HI = "#38bdf8"
TEAL = "#2dd4bf"
AMBER = "#f59e0b"
ORANGE = "#fb923c"
PURPLE = "#a855f7"
PURPLE_HI = "#c084fc"
PINK = "#f472b6"
RED = "#ef4444"

# ---------------------------------------------------------------------------
# 高 DPI 与字体缩放
# ---------------------------------------------------------------------------

UI_SCALE = 1.0
FONT_UI = "Microsoft YaHei UI"
FONT_MONO = "Consolas"
FONT_NUM = "Bahnschrift"

F_H1 = F_H2 = F_BODY = F_BODY_B = F_SMALL = F_SMALL_B = F_TINY = None
F_MONO = F_MONO_SM = F_NUM_LG = F_NUM_MD = None


def S(px: float) -> int:
    return max(1, int(round(px * UI_SCALE)))


def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def get_toplevel_hwnd(widget) -> int:
    widget.update_idletasks()
    hwnd = ctypes.windll.user32.GetParent(widget.winfo_id())
    return hwnd or widget.winfo_id()


def detect_scale(root) -> float:
    scale = 1.0
    try:
        dpi = ctypes.windll.user32.GetDpiForWindow(get_toplevel_hwnd(root))
        if dpi > 0:
            scale = max(1.0, dpi / 96.0)
    except Exception:
        pass
    screen_h = root.winfo_screenheight()
    if screen_h > 0:
        max_scale = (screen_h * 0.88) / 735.0
        scale = min(scale, max(0.85, max_scale))
    return round(scale, 2)


def apply_dark_titlebar(widget):
    try:
        hwnd = get_toplevel_hwnd(widget)
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), 4)
        dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(dark), 4)
        # Win11 原生指定标题栏底色与窗口背景一致 (#080b10 -> COLORREF 0x00100b08)
        caption_color = ctypes.c_uint(0x00120B09)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption_color), 4)
        text_color = ctypes.c_uint(0x00F0E8E2)
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text_color), 4)
        corner_round = ctypes.c_int(2)  # DWMWCP_ROUND on Win11
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner_round), 4)
    except Exception:
        pass


def init_fonts(root):
    global FONT_UI, FONT_MONO, FONT_NUM
    global F_H1, F_H2, F_BODY, F_BODY_B, F_SMALL, F_SMALL_B, F_TINY, F_MONO, F_MONO_SM, F_NUM_LG, F_NUM_MD
    families = set(tkfont.families(root))
    FONT_UI = next(
        (f for f in ("Microsoft YaHei UI", "Segoe UI Variable Display", "Segoe UI") if f in families),
        "TkDefaultFont",
    )
    FONT_MONO = next((f for f in ("Cascadia Mono", "Consolas") if f in families), "TkFixedFont")
    FONT_NUM = next((f for f in ("Bahnschrift", "DIN Alternate", FONT_MONO) if f in families), FONT_MONO)

    F_H1 = (FONT_UI, -S(18), "bold")
    F_H2 = (FONT_UI, -S(13), "bold")
    F_BODY = (FONT_UI, -S(12))
    F_BODY_B = (FONT_UI, -S(12), "bold")
    F_SMALL = (FONT_UI, -S(11))
    F_SMALL_B = (FONT_UI, -S(11), "bold")
    F_TINY = (FONT_UI, -S(10))
    F_MONO = (FONT_MONO, -S(12), "bold")
    F_MONO_SM = (FONT_MONO, -S(11))
    F_NUM_LG = (FONT_NUM, -S(15), "bold")
    F_NUM_MD = (FONT_NUM, -S(13), "bold")


_PIL_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def get_pil_font(kind: str, size_px: int):
    key = (kind, max(8, int(size_px)))
    cached = _PIL_FONT_CACHE.get(key)
    if cached is not None:
        return cached
    fonts_dir = Path(r"C:\Windows\Fonts")
    candidates = {
        "num": ("bahnschrift.ttf", "consolab.ttf", "seguisb.ttf"),
        "cn": ("msyhbd.ttc", "msyh.ttc", "seguisb.ttf"),
        "ui": ("seguisb.ttf", "bahnschrift.ttf", "segoeui.ttf"),
    }.get(kind, ("bahnschrift.ttf",))
    for fname in candidates:
        fp = fonts_dir / fname
        if fp.exists():
            try:
                f = ImageFont.truetype(str(fp), key[1])
                _PIL_FONT_CACHE[key] = f
                return f
            except Exception:
                pass
    f = ImageFont.load_default()
    _PIL_FONT_CACHE[key] = f
    return f


# ---------------------------------------------------------------------------
# 几何与颜色辅助
# ---------------------------------------------------------------------------

def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def blend_hex(c1: str, c2: str, t: float) -> str:
    t = clamp(t)
    v1 = int(c1[1:], 16)
    v2 = int(c2[1:], 16)
    r = int(round(((v1 >> 16) & 0xFF) * (1 - t) + ((v2 >> 16) & 0xFF) * t))
    g = int(round(((v1 >> 8) & 0xFF) * (1 - t) + ((v2 >> 8) & 0xFF) * t))
    b = int(round((v1 & 0xFF) * (1 - t) + (v2 & 0xFF) * t))
    return f"#{r:02x}{g:02x}{b:02x}"


def rounded_polygon_points(x1: float, y1: float, x2: float, y2: float, radius: float, steps: int = 7):
    r = max(0.0, min(radius, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    if r < 1.0:
        return [x1, y1, x2, y1, x2, y2, x1, y2]
    pts = []
    corners = [
        (x2 - r, y1 + r, -math.pi / 2, 0.0),
        (x2 - r, y2 - r, 0.0, math.pi / 2),
        (x1 + r, y2 - r, math.pi / 2, math.pi),
        (x1 + r, y1 + r, math.pi, 3 * math.pi / 2),
    ]
    for cx, cy, a0, a1 in corners:
        for i in range(steps + 1):
            ang = a0 + (a1 - a0) * (i / steps)
            pts.append(cx + math.cos(ang) * r)
            pts.append(cy + math.sin(ang) * r)
    return pts


def draw_rounded_box(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, radius: float, **kwargs):
    pts = rounded_polygon_points(x1, y1, x2, y2, radius)
    return canvas.create_polygon(pts, smooth=False, **kwargs)


# ---------------------------------------------------------------------------
# XInput 手柄底层接口
# ---------------------------------------------------------------------------

class XInputGamepad(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XInputState(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", wintypes.DWORD),
        ("Gamepad", XInputGamepad),
    ]


class XInputVibration(ctypes.Structure):
    _fields_ = [
        ("wLeftMotorSpeed", wintypes.WORD),
        ("wRightMotorSpeed", wintypes.WORD),
    ]


try:
    xinput = ctypes.WinDLL("xinput1_4")
except OSError:
    try:
        xinput = ctypes.WinDLL("xinput1_3")
    except OSError:
        xinput = ctypes.WinDLL("xinput9_1_0")

XInputSetState = xinput.XInputSetState
XInputSetState.argtypes = [wintypes.DWORD, ctypes.POINTER(XInputVibration)]
XInputSetState.restype = wintypes.DWORD

XInputGetState = xinput.XInputGetState
XInputGetState.argtypes = [wintypes.DWORD, ctypes.POINTER(XInputState)]
XInputGetState.restype = wintypes.DWORD

try:
    _XInputEnable = xinput.XInputEnable
    _XInputEnable.argtypes = [wintypes.BOOL]
    _XInputEnable.restype = None
    _XInputEnable(1)
except Exception:
    pass


def set_vibration(user_index: int, left_motor: float, right_motor: float) -> int:
    vibration = XInputVibration(
        int(clamp(left_motor) * 65535),
        int(clamp(right_motor) * 65535),
    )
    return XInputSetState(int(user_index), ctypes.byref(vibration))


def is_controller_connected(index: int) -> bool:
    state = XInputState()
    return XInputGetState(int(index), ctypes.byref(state)) == XINPUT_SUCCESS


# ---------------------------------------------------------------------------
# 预设与共享状态模型
# ---------------------------------------------------------------------------

PRESETS = {
    "人声清晰": {
        "dual_motor_mode": False,
        "vocal_only_mode": True,
        "adaptive_threshold": True,
        "intensity": 0.72,
        "rhythm_punch": 0.12,
        "texture_amount": 0.42,
        "vibration_threshold": 185.0,
        "map_in_max": 3600.0,
        "gamma_correction": 1.34,
        "attack_rate": 0.56,
        "release_rate": 0.34,
        "dead_zone": 0.012,
        "vocal_low": 280,
        "vocal_high": 4500,
        "bass_high": 200,
    },
    "低频沉浸": {
        "dual_motor_mode": True,
        "vocal_only_mode": False,
        "adaptive_threshold": True,
        "intensity": 0.88,
        "rhythm_punch": 0.92,
        "texture_amount": 0.15,
        "vibration_threshold": 185.0,
        "map_in_max": 4800.0,
        "gamma_correction": 1.62,
        "attack_rate": 0.78,
        "release_rate": 0.36,
        "dead_zone": 0.014,
        "vocal_low": 650,
        "vocal_high": 5200,
        "bass_high": 215,
    },
    "轻柔细腻": {
        "dual_motor_mode": False,
        "vocal_only_mode": True,
        "adaptive_threshold": True,
        "intensity": 0.36,
        "rhythm_punch": 0.12,
        "texture_amount": 0.82,
        "vibration_threshold": 68.0,
        "map_in_max": 2400.0,
        "gamma_correction": 0.84,
        "attack_rate": 0.42,
        "release_rate": 0.26,
        "dead_zone": 0.012,
        "vocal_low": 280,
        "vocal_high": 7800,
        "bass_high": 180,
    },
    "全频强劲": {
        "dual_motor_mode": True,
        "vocal_only_mode": False,
        "adaptive_threshold": True,
        "intensity": 1.0,
        "rhythm_punch": 0.96,
        "texture_amount": 0.58,
        "vibration_threshold": 185.0,
        "map_in_max": 4400.0,
        "gamma_correction": 1.18,
        "attack_rate": 0.84,
        "release_rate": 0.30,
        "dead_zone": 0.015,
        "vocal_low": 420,
        "vocal_high": 6800,
        "bass_high": 300,
    },
}

MODE_CARDS = [
    ("智能自动", "自动适配场景", CYAN_HI),
    ("人声清晰", "对白 · 播客", TEAL),
    ("低频沉浸", "音乐 · 鼓点", GREEN_HI),
    ("轻柔细腻", "ASMR · 助眠", PURPLE_HI),
    ("全频强劲", "游戏 · 影院", ORANGE),
]

FEEL_DEFAULTS = {
    "intensity": 0.76,
    "rhythm_punch": 0.72,
    "texture_amount": 0.56,
}

ADVANCED_DEFAULTS = {
    "dual_motor_mode": False,
    "vocal_only_mode": True,
    "adaptive_threshold": True,
    "vibration_threshold": 185.0,
    "dead_zone": 0.012,
    "attack_rate": 0.58,
    "release_rate": 0.34,
    "map_in_max": 3800.0,
    "gamma_correction": 1.34,
    "vocal_low": 280,
    "vocal_high": 5200,
    "bass_high": 220,
}

PERSISTED_KEYS = (
    "enabled",
    "controller_index",
    "auto_profile",
    "rhythm_punch",
    "texture_amount",
    "dual_motor_mode",
    "vocal_only_mode",
    "adaptive_threshold",
    "intensity",
    "vibration_threshold",
    "dead_zone",
    "attack_rate",
    "release_rate",
    "map_in_max",
    "gamma_correction",
    "vocal_low",
    "vocal_high",
    "bass_high",
    "visualizer_enabled",
)

SETTING_BOUNDS = {
    "controller_index": (0, 3, int),
    "rhythm_punch": (0.0, 1.0, float),
    "texture_amount": (0.0, 1.0, float),
    "intensity": (0.10, 1.0, float),
    "vibration_threshold": (40.0, 8000.0, float),
    "dead_zone": (0.0, 0.30, float),
    "attack_rate": (0.02, 1.0, float),
    "release_rate": (0.02, 1.0, float),
    "map_in_max": (600.0, 14000.0, float),
    "gamma_correction": (0.3, 2.8, float),
    "vocal_low": (80, 4800, int),
    "vocal_high": (300, 9600, int),
    "bass_high": (60, 1800, int),
}


def sanitize_settings(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for key in ("enabled", "visualizer_enabled", "auto_profile", "dual_motor_mode", "vocal_only_mode", "adaptive_threshold"):
        if key in raw and isinstance(raw[key], (bool, int)):
            cleaned[key] = bool(raw[key])
    for key, (lo, hi, cast) in SETTING_BOUNDS.items():
        if key in raw:
            try:
                val = cast(float(raw[key]))
                cleaned[key] = max(lo, min(hi, val))
            except (TypeError, ValueError):
                pass
    if "vocal_low" in cleaned and "vocal_high" in cleaned:
        if cleaned["vocal_low"] >= cleaned["vocal_high"]:
            cleaned["vocal_high"] = min(9600, cleaned["vocal_low"] + 200)
    return cleaned


@dataclass
class RuntimeStats:
    total_rms: float = 0.0
    vocal_rms: float = 0.0
    bass_rms: float = 0.0
    left_output: float = 0.0
    right_output: float = 0.0
    peak_left: float = 0.0
    peak_right: float = 0.0
    peak_rms: float = 0.0
    noise_floor: float = 0.0
    effective_threshold: float = 260.0
    device_name: str = "正在连接音频设备..."
    controller_connected: bool = False
    detected_mode: str = "识别中"
    calibrating_left: float = 0.0
    beat_pulse: float = 0.0
    spectrum: tuple = (0.0,) * 24


@dataclass
class SharedVibrationParams:
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    enabled: bool = True
    visualizer_enabled: bool = True
    controller_index: int = 0
    auto_profile: bool = True
    rhythm_punch: float = 0.72
    texture_amount: float = 0.56
    dual_motor_mode: bool = False
    vocal_only_mode: bool = True
    adaptive_threshold: bool = True
    auto_calibrating: bool = False
    reconnect_requested: bool = False
    intensity: float = 0.76
    vibration_threshold: float = 185.0
    dead_zone: float = 0.012
    attack_rate: float = 0.58
    release_rate: float = 0.34
    map_in_max: float = 3800.0
    gamma_correction: float = 1.34
    vocal_low: int = 280
    vocal_high: int = 5200
    bass_high: int = 220

    # 运行时协调字段（不写入配置文件）
    audio_left: float = 0.0
    audio_right: float = 0.0
    override_until: float = 0.0
    override_left: float = 0.0
    override_right: float = 0.0
    hotkey_notice_at: float = 0.0
    hotkey_notice_enabled: bool = True
    hotkey_seq: int = 0

    stats: RuntimeStats = field(default_factory=RuntimeStats)

    def snapshot(self, include_stats: bool = True) -> dict:
        with self.lock:
            data = {k: getattr(self, k) for k in PERSISTED_KEYS}
            data.update(
                auto_calibrating=self.auto_calibrating,
                reconnect_requested=self.reconnect_requested,
                audio_left=self.audio_left,
                audio_right=self.audio_right,
                override_until=self.override_until,
                override_left=self.override_left,
                override_right=self.override_right,
                hotkey_notice_at=self.hotkey_notice_at,
                hotkey_notice_enabled=self.hotkey_notice_enabled,
                hotkey_seq=self.hotkey_seq,
            )
            if include_stats:
                st = self.stats
                data["stats"] = {
                    "total_rms": st.total_rms,
                    "vocal_rms": st.vocal_rms,
                    "bass_rms": st.bass_rms,
                    "left_output": st.left_output,
                    "right_output": st.right_output,
                    "peak_left": st.peak_left,
                    "peak_right": st.peak_right,
                    "peak_rms": st.peak_rms,
                    "noise_floor": st.noise_floor,
                    "effective_threshold": st.effective_threshold,
                    "device_name": st.device_name,
                    "controller_connected": st.controller_connected,
                    "detected_mode": st.detected_mode,
                    "calibrating_left": st.calibrating_left,
                    "beat_pulse": st.beat_pulse,
                    "spectrum": st.spectrum,
                }
            return data

    def apply(self, values: dict):
        with self.lock:
            for key, value in values.items():
                if key in PERSISTED_KEYS:
                    setattr(self, key, value)


# ---------------------------------------------------------------------------
# 信号处理与双线程引擎（手柄轮询线程 + WASAPI 非阻塞音频线程）
# ---------------------------------------------------------------------------

def smooth_signal(current: float, target: float, attack: float, release: float) -> float:
    rate = attack if target > current else release
    return current + (target - current) * clamp(rate)


def map_value_gamma(value: float, threshold: float, in_max: float, gamma: float) -> float:
    if value <= threshold:
        return 0.0
    span = max(1.0, in_max - threshold)
    normalized = clamp((value - threshold) / span)
    return normalized ** max(0.1, gamma)


def band_rms_power(power: np.ndarray, freqs: np.ndarray, low: float, high: float, n: int) -> float:
    idx_lo = int(np.searchsorted(freqs, low, side="left"))
    idx_hi = int(np.searchsorted(freqs, high, side="right"))
    if idx_hi <= idx_lo:
        return 0.0
    energy = float(power[idx_lo:idx_hi].sum()) / (n * n)
    return math.sqrt(energy + 1e-12)


def apply_motor_deadzone(value: float, dead_zone: float) -> float:
    """纯净死区裁剪（彻底移除强制 5.2% 起跳台阶，完整保留 1%~6% 细腻微震梯度，零底震！）。"""
    if value <= dead_zone:
        return 0.0
    return clamp(value)


def classify_content(
    total_rms: float,
    vocal_rms: float,
    kick_rms: float,
    rhythm_env: float,
    bass_ratio_avg: float,
    crisp_rms: float,
    prev: str,
) -> str:
    if total_rms < 50.0 and rhythm_env < 0.12:
        return "安静"
    total = max(total_rms, 1.0)
    vocal_crisp_ratio = min(1.3, (vocal_rms + crisp_rms * 0.75) / total)

    # 采用 1.5s 鼓点节拍包络(rhythm_env)与平均低频占比，避免鼓点间隙(400ms)反复跳回 ASMR！
    music_score = bass_ratio_avg * 1.45 + rhythm_env * 1.75
    asmr_score = vocal_crisp_ratio * 0.92 + (1.0 - min(rhythm_env * 1.9, 1.0)) * 0.55 - bass_ratio_avg * 0.65

    if prev == "音乐":
        music_score += 0.14
    elif prev == "ASMR":
        asmr_score += 0.12

    if music_score > asmr_score + 0.10:
        return "音乐"
    if asmr_score > music_score + 0.08:
        return "ASMR"
    return prev if prev in {"音乐", "ASMR"} else "ASMR"


def controller_io_thread(
    shared: SharedVibrationParams,
    log_queue: queue.Queue,
    stop_event: threading.Event,
    vis_wake_event: threading.Event | None = None,
):
    """独立 60Hz 手柄轮询与震动输出线程。

    即使电脑完全静音或没有音频输出设备，手柄连接状态检测与
    LB + RB 同时按下开关震动也能毫秒级响应。
    """
    combo_latched = False
    last_toggle_at = 0.0
    watched_index = -1
    connected = False
    next_disconnected_poll = 0.0
    last_sent = None
    last_sent_at = 0.0

    try:
        while not stop_event.is_set():
            now = time.monotonic()
            with shared.lock:
                controller_index = shared.controller_index

            if watched_index != controller_index:
                if watched_index >= 0:
                    try:
                        set_vibration(watched_index, 0.0, 0.0)
                    except Exception:
                        pass
                watched_index = controller_index
                connected = False
                combo_latched = False
                next_disconnected_poll = 0.0
                last_sent = None

            if connected or now >= next_disconnected_poll:
                state = XInputState()
                is_conn = XInputGetState(controller_index, ctypes.byref(state)) == XINPUT_SUCCESS
                if is_conn:
                    connected = True
                    buttons = state.Gamepad.wButtons
                    both_pressed = (buttons & LB_RB_COMBO_MASK) == LB_RB_COMBO_MASK

                    if both_pressed:
                        if not combo_latched and (now - last_toggle_at) >= 0.25:
                            combo_latched = True
                            last_toggle_at = now
                            with shared.lock:
                                new_enabled = not shared.enabled
                                shared.enabled = new_enabled
                                shared.hotkey_notice_at = now
                                shared.hotkey_notice_enabled = new_enabled
                                shared.hotkey_seq += 1
                                if new_enabled:
                                    # 开启时给一次轻巧短促的震动确认（90ms）
                                    shared.override_left = 0.28
                                    shared.override_right = 0.28
                                    shared.override_until = now + 0.09
                                else:
                                    # 关闭时立即清空所有输出
                                    shared.override_left = 0.0
                                    shared.override_right = 0.0
                                    shared.override_until = 0.0
                                    shared.audio_left = 0.0
                                    shared.audio_right = 0.0
                            log_queue.put(f"手柄 LB + RB 快捷键: 震动已{'开启' if new_enabled else '关闭'}")
                    elif (buttons & LB_RB_COMBO_MASK) == 0:
                        # 两个肩键全部松开后复位，防止弹跳或单键长按误触
                        combo_latched = False
                else:
                    connected = False
                    combo_latched = False
                    next_disconnected_poll = now + 0.45
                    last_sent = None

            with shared.lock:
                if now < shared.override_until:
                    out_left = shared.override_left
                    out_right = shared.override_right
                elif shared.enabled:
                    out_left = shared.audio_left
                    out_right = shared.audio_right
                else:
                    out_left = 0.0
                    out_right = 0.0

                shared.stats.controller_connected = connected
                shared.stats.left_output = out_left if connected else 0.0
                shared.stats.right_output = out_right if connected else 0.0
                shared.stats.peak_left = max(shared.stats.peak_left * 0.96, shared.stats.left_output)
                shared.stats.peak_right = max(shared.stats.peak_right * 0.96, shared.stats.right_output)

            if connected:
                key = (
                    controller_index,
                    int(clamp(out_left) * 65535),
                    int(clamp(out_right) * 65535),
                )
                # 震动值变化时以 60Hz 毫秒级立即下发；震动值恒定时每 48ms 保活刷新防被后台手柄程序覆盖；空闲(0,0)不占总线。
                active_rumble = key[1] > 0 or key[2] > 0
                if active_rumble and vis_wake_event is not None and shared.visualizer_enabled:
                    vis_wake_event.set()
                if key != last_sent or (active_rumble and (now - last_sent_at) >= 0.048):
                    set_vibration(controller_index, out_left, out_right)
                    last_sent = key
                    last_sent_at = now

            time.sleep(0.016)
    finally:
        if watched_index >= 0:
            try:
                set_vibration(watched_index, 0.0, 0.0)
            except Exception:
                pass


def find_loopback_device(audio: pyaudio.PyAudio, log_queue: queue.Queue, log_info: bool = True):
    try:
        dev = audio.get_default_wasapi_loopback()
        if dev is not None:
            if log_info:
                log_queue.put(f"已匹配默认输出回环: {dev['name']}")
            return dev
    except Exception:
        pass

    loopbacks = []
    for i in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(i)
        if info.get("isLoopbackDevice", False):
            loopbacks.append(info)

    try:
        wasapi_info = audio.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_idx = wasapi_info["defaultOutputDevice"]
        default_out = audio.get_device_info_by_index(default_idx)
        if log_info:
            log_queue.put(f"默认播放设备: {default_out['name']}")
        for dev in loopbacks:
            if default_out["name"] in dev["name"]:
                return dev
    except Exception as exc:
        if log_info:
            log_queue.put(f"查询默认 WASAPI 设备失败: {exc}")

    if loopbacks:
        return loopbacks[0]
    return None


class _COMGuid(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_str(cls, text: str) -> "_COMGuid":
        g = cls()
        ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
        return g


_CLSID_MM_DEV_ENUM = _COMGuid.from_str("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
_IID_IMM_DEV_ENUM = _COMGuid.from_str("{A95664D2-9614-4F35-A746-DE8DB63617E6}")


def get_default_render_endpoint_id() -> str | None:
    """绕过 PortAudio 静态设备缓存，直接从 Windows CoreAudio 查询当前活跃默认播放端点 ID。"""
    try:
        ole32 = ctypes.windll.ole32
        enum_ptr = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(_CLSID_MM_DEV_ENUM),
            None,
            23,  # CLSCTX_ALL
            ctypes.byref(_IID_IMM_DEV_ENUM),
            ctypes.byref(enum_ptr),
        )
        if hr != 0 or not enum_ptr.value:
            return None
        vtbl = ctypes.cast(
            ctypes.cast(enum_ptr, ctypes.POINTER(ctypes.c_void_p)).contents.value,
            ctypes.POINTER(ctypes.c_void_p),
        )
        release_enum = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
        get_default = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
        )(vtbl[4])
        try:
            dev_ptr = ctypes.c_void_p()
            # eRender = 0, eMultimedia = 1
            if get_default(enum_ptr, 0, 1, ctypes.byref(dev_ptr)) != 0 or not dev_ptr.value:
                return None
            dvtbl = ctypes.cast(
                ctypes.cast(dev_ptr, ctypes.POINTER(ctypes.c_void_p)).contents.value,
                ctypes.POINTER(ctypes.c_void_p),
            )
            release_dev = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(dvtbl[2])
            get_id = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
            )(dvtbl[5])
            get_state = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)
            )(dvtbl[6])
            try:
                state = wintypes.DWORD(0)
                if get_state(dev_ptr, ctypes.byref(state)) == 0 and state.value != 1:
                    return None
                id_ptr = ctypes.c_void_p()
                if get_id(dev_ptr, ctypes.byref(id_ptr)) == 0 and id_ptr.value:
                    ep_id = ctypes.wstring_at(id_ptr.value)
                    ole32.CoTaskMemFree(id_ptr)
                    return ep_id
                return None
            finally:
                release_dev(dev_ptr)
        finally:
            release_enum(enum_ptr)
    except Exception:
        return None


def safe_close_stream(stream):
    if stream is None:
        return
    for fn in (stream.stop_stream, stream.close):
        try:
            fn()
        except Exception:
            pass


def audio_vibration_thread(
    shared: SharedVibrationParams,
    log_queue: queue.Queue,
    stop_event: threading.Event,
    vis_wake_event: threading.Event | None = None,
):
    """WASAPI Loopback 回调式非阻塞音频分析线程（支持蓝牙/耳机热切换实时自动重连）。"""
    com_inited = False
    try:
        hr = ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
        com_inited = hr in (0, 1)
    except Exception:
        pass

    chunk = 512
    audio = pyaudio.PyAudio()
    stream = None
    audio_queue: queue.Queue = queue.Queue(maxsize=12)

    channels = 2
    rate = 48000
    current_device_name = ""
    current_endpoint_id: str | None = None

    left_vib = 0.0
    right_vib = 0.0
    noise_floor = 0.0
    calibration_until = 0.0
    energy_avg = 0.0
    transient_avg = 0.0
    kick_avg = 0.0
    crisp_avg = 0.0
    rhythm_env = 0.0
    bass_ratio_avg = 0.0
    session_peak = 950.0
    detected_mode = "ASMR"
    candidate_mode = "ASMR"
    candidate_chunks = 0

    fft_cache_key = None
    fft_freqs = None
    fft_weights = None
    fft_hann = None
    vis_spec_smooth = [0.0] * 24
    spec_slices: list[tuple[int, int]] = []
    band_idx_cache_key = None
    band_idx_slices = (0, 0, 0, 0, 0, 0, 0, 0)
    spec_ref = 160.0

    next_connect_at = 0.0
    next_default_check_at = 0.0
    last_err_log_at = 0.0

    def stream_callback(in_data, _frame_count, _time_info, _status):
        try:
            audio_queue.put_nowait(in_data)
        except queue.Full:
            try:
                audio_queue.get_nowait()
                audio_queue.put_nowait(in_data)
            except queue.Empty:
                pass
        return (None, pyaudio.paContinue)

    try:
        while not stop_event.is_set():
            now = time.monotonic()
            snap = shared.snapshot(include_stats=False)

            if snap["reconnect_requested"]:
                with shared.lock:
                    shared.reconnect_requested = False
                safe_close_stream(stream)
                stream = None
                current_endpoint_id = None
                next_connect_at = 0.0

            # 通过 Windows CoreAudio 实时检测默认播放设备热切换（解决 PortAudio 缓存过期导致切换蓝牙耳机后失声/不震！）
            if stream is not None and now >= next_default_check_at:
                next_default_check_at = now + 1.2
                live_ep = get_default_render_endpoint_id()
                if live_ep != current_endpoint_id:
                    log_queue.put("检测到默认播放设备切换或蓝牙重连，正在刷新音频回环...")
                    safe_close_stream(stream)
                    stream = None
                    current_endpoint_id = None
                    next_connect_at = 0.0
                    with shared.lock:
                        shared.audio_left = 0.0
                        shared.audio_right = 0.0
                        shared.stats.device_name = "正在切换默认音频设备..."

            if stream is None:
                if now < next_connect_at:
                    time.sleep(0.08)
                    continue
                try:
                    safe_close_stream(stream)
                    audio.terminate()
                except Exception:
                    pass
                audio = pyaudio.PyAudio()
                try:
                    live_ep = get_default_render_endpoint_id()
                    dev = find_loopback_device(audio, log_queue, log_info=True)
                    if dev is None:
                        raise RuntimeError("未找到可用的系统回环音频设备")
                    channels = max(1, int(dev["maxInputChannels"]))
                    rate = int(dev["defaultSampleRate"])
                    current_device_name = dev["name"]
                    current_endpoint_id = live_ep
                    while not audio_queue.empty():
                        try:
                            audio_queue.get_nowait()
                        except queue.Empty:
                            break
                    stream = audio.open(
                        format=pyaudio.paInt16,
                        channels=channels,
                        rate=rate,
                        input=True,
                        input_device_index=dev["index"],
                        frames_per_buffer=chunk,
                        stream_callback=stream_callback,
                    )
                    stream.start_stream()
                    next_default_check_at = now + 1.2
                    with shared.lock:
                        shared.stats.device_name = current_device_name
                        shared.stats.effective_threshold = shared.vibration_threshold
                    log_queue.put(f"音频监听就绪: {current_device_name} ({rate} Hz)")
                except Exception as exc:
                    stream = None
                    current_endpoint_id = None
                    next_connect_at = now + 1.0
                    if now - last_err_log_at > 4.0:
                        log_queue.put(f"等待系统音频设备就绪: {exc}")
                        last_err_log_at = now
                    with shared.lock:
                        shared.stats.device_name = "未检测到播放设备，等待重连..."
                        shared.audio_left = 0.0
                        shared.audio_right = 0.0
                    time.sleep(0.1)
                    continue

            # 自动校准计时管理
            if snap["auto_calibrating"] and calibration_until == 0.0:
                calibration_until = now + 3.0
                noise_floor = 0.0
                log_queue.put("环境校准开始: 正在采样 3 秒环境底噪...")

            try:
                raw = audio_queue.get(timeout=0.04)
            except queue.Empty:
                # 系统当前静音无音频帧推送：平滑回落马达输出与电平显示
                now = time.monotonic()
                if snap["auto_calibrating"]:
                    left_sec = max(0.0, calibration_until - now)
                    if now >= calibration_until:
                        calibrated = clamp(max(120.0, noise_floor * 1.8), 40.0, 8000.0)
                        with shared.lock:
                            shared.vibration_threshold = calibrated
                            shared.auto_calibrating = False
                            shared.stats.calibrating_left = 0.0
                            shared.stats.effective_threshold = calibrated
                        calibration_until = 0.0
                        log_queue.put(f"环境校准完成: 触发门限已设为 {calibrated:.0f}")
                    else:
                        with shared.lock:
                            shared.stats.calibrating_left = left_sec

                # 系统当前静音无音频帧推送：立即急刹归零马达输出，绝不拖尾续转！
                left_vib = smooth_signal(left_vib, 0.0, 0.60, 0.58)
                right_vib = smooth_signal(right_vib, 0.0, 0.60, 0.62)
                if left_vib < 0.012:
                    left_vib = 0.0
                if right_vib < 0.012:
                    right_vib = 0.0

                with shared.lock:
                    shared.audio_left = left_vib if shared.enabled else 0.0
                    shared.audio_right = right_vib if shared.enabled else 0.0
                    shared.stats.total_rms *= 0.72
                    shared.stats.vocal_rms *= 0.72
                    shared.stats.bass_rms *= 0.72
                    shared.stats.peak_rms *= 0.95
                    shared.stats.beat_pulse *= 0.65
                    shared.stats.spectrum = tuple(v * 0.65 for v in shared.stats.spectrum)
                    if shared.stats.total_rms < 5.0:
                        shared.stats.total_rms = 0.0
                        shared.stats.detected_mode = "安静"
                noise_floor *= 0.90
                if noise_floor < 1.0:
                    noise_floor = 0.0
                # 静音/暂停期间保持长时音量基准 session_peak，绝不拉高增益制造底震！
                if stream is not None and not stream.is_active():
                    safe_close_stream(stream)
                    stream = None
                    next_connect_at = now + 0.5
                continue

            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            if channels > 1:
                usable = (len(samples) // channels) * channels
                if usable == 0:
                    continue
                samples = samples[:usable].reshape(-1, channels).mean(axis=1)

            n = len(samples)
            if n == 0:
                continue

            if fft_cache_key != (n, rate):
                fft_cache_key = (n, rate)
                fft_freqs = np.fft.rfftfreq(n, 1.0 / rate)
                fft_weights = np.full(len(fft_freqs), 2.0)
                fft_weights[0] = 1.0
                if n % 2 == 0:
                    fft_weights[-1] = 1.0
                fft_hann = (np.hanning(n) * 1.633).astype(np.float32)
                hi_f = max(400.0, min(14000.0, rate * 0.44))
                edges = np.geomspace(80.0, hi_f, 25)
                idxs = np.searchsorted(fft_freqs, edges)
                spec_slices = []
                for bi in range(24):
                    a = int(idxs[bi])
                    b = max(int(idxs[bi + 1]), a + 1)
                    spec_slices.append((a, b))
                spec_ref = 160.0

            fft_data = np.fft.rfft(samples)
            power = (fft_data.real ** 2 + fft_data.imag ** 2) * fft_weights

            # 单次前缀和替代每帧切片求和；视觉频谱单独加 Hann 窗消除矩形窗旁瓣闪烁抖动，使高刷动画如丝绸顺滑
            csum = [0.0] + np.cumsum(power, dtype=np.float64).tolist()
            inv_n2 = 1.0 / (n * n)

            if snap["visualizer_enabled"] and snap["enabled"]:
                vis_fft = np.fft.rfft(samples * fft_hann)
                vis_power = (vis_fft.real ** 2 + vis_fft.imag ** 2) * fft_weights
                vis_csum = [0.0] + np.cumsum(vis_power, dtype=np.float64).tolist()
                band_vals = [math.sqrt(max(0.0, vis_csum[b] - vis_csum[a]) * inv_n2 + 1e-12) for a, b in spec_slices]
                cur_peak = max(band_vals) if band_vals else 0.0
                spec_ref = max(spec_ref * 0.988, cur_peak * 0.92, 75.0)
                for bi, lv in enumerate(band_vals):
                    raw_sv = clamp(((lv / spec_ref) - 0.08) / 0.88) ** 1.28
                    prev_sv = vis_spec_smooth[bi]
                    vis_spec_smooth[bi] = prev_sv + (raw_sv - prev_sv) * (0.72 if raw_sv > prev_sv else 0.44)
                spectrum = tuple(vis_spec_smooth)
            else:
                spectrum = (0.0,) * 24

            # 4 个独立声学感知频段（严格排除 0Hz 直流分量，彻底杜绝暂停音乐后直流残余让左马达空转！）
            b_key = (n, rate, snap["bass_high"], snap["vocal_low"], snap["vocal_high"])
            if band_idx_cache_key != b_key:
                band_idx_cache_key = b_key
                k_lo = int(np.searchsorted(fft_freqs, 20.0, side="left"))
                k_hi = int(np.searchsorted(fft_freqs, 220.0, side="right"))
                b_hi = int(np.searchsorted(fft_freqs, snap["bass_high"], side="right"))
                v_lo = int(np.searchsorted(fft_freqs, snap["vocal_low"], side="left"))
                v_hi = int(np.searchsorted(fft_freqs, snap["vocal_high"], side="right"))
                c_lo = int(np.searchsorted(fft_freqs, 2400.0, side="left"))
                c_hi = int(np.searchsorted(fft_freqs, 8200.0, side="right"))
                band_idx_slices = (k_lo, k_hi, k_lo, b_hi, v_lo, v_hi, c_lo, c_hi)

            kl0, kh0, bl0, bh0, vl0, vh0, cl0, ch0 = band_idx_slices
            raw_kick_rms = (math.sqrt(max(0.0, csum[kh0] - csum[kl0]) * inv_n2 + 1e-12) * 1.18) if kh0 > kl0 else 0.0
            raw_bass_rms = (math.sqrt(max(0.0, csum[bh0] - csum[bl0]) * inv_n2 + 1e-12) * 1.12) if bh0 > bl0 else 0.0
            raw_vocal_rms = math.sqrt(max(0.0, csum[vh0] - csum[vl0]) * inv_n2 + 1e-12) if vh0 > vl0 else 0.0
            raw_crisp_rms = math.sqrt(max(0.0, csum[ch0] - csum[cl0]) * inv_n2 + 1e-12) if ch0 > cl0 else 0.0
            raw_total_rms = float(np.sqrt(np.mean(np.square(samples)) + 1e-10))

            # 暂停/静音急刹门限：当蓝牙或浏览器暂停播放但仍推送零值/底噪空包时，毫秒级立即刹停！
            silence_gate = 14.0
            is_paused_silence = (not snap["auto_calibrating"]) and (raw_total_rms < silence_gate)
            if not is_paused_silence and vis_wake_event is not None and snap["visualizer_enabled"] and snap["enabled"]:
                vis_wake_event.set()

            # 长时会话峰值自适应增益（仅补偿系统耳机总音量偏低，绝不在小声音/轻音段落拉高增益制造底震！）
            if not snap["auto_calibrating"] and not is_paused_silence:
                if raw_total_rms > session_peak:
                    session_peak = session_peak * 0.30 + raw_total_rms * 0.70
                elif raw_total_rms > 130.0:
                    # 仅在正常中等以上音量持续播放时慢速自适应；微弱小声音时冻结峰值绝不放大成大底震！
                    session_peak = max(340.0, session_peak * 0.9996)
                base_gain = clamp(1120.0 / max(session_peak, 340.0), 1.0, 3.0)
                quiet_taper = clamp((raw_total_rms - 18.0) / 80.0)
                vol_gain = (1.0 + (base_gain - 1.0) * quiet_taper) * clamp((raw_total_rms - 14.0) / 28.0)
            else:
                vol_gain = 0.0 if is_paused_silence else 1.0

            kick_rms = raw_kick_rms * vol_gain
            bass_rms = raw_bass_rms * vol_gain
            vocal_rms = raw_vocal_rms * vol_gain
            crisp_rms = raw_crisp_rms * vol_gain
            total_rms = raw_total_rms * vol_gain

            # 独立频段瞬态节拍检测（严格抑制微弱底噪误触发，保留真实鼓点干脆爆发！）
            energy_avg = energy_avg * 0.955 + total_rms * 0.045
            transient = clamp((total_rms - energy_avg * 1.05) / max(energy_avg * 0.90, 190.0), 0.0, 1.0)
            transient_avg = transient_avg * 0.72 + transient * 0.28

            kick_avg = kick_avg * 0.90 + kick_rms * 0.10
            kick_flux = clamp((kick_rms - kick_avg * 1.06) / max(kick_avg * 0.84, 185.0), 0.0, 1.0)
            rhythm_env = max(rhythm_env * 0.982, kick_flux)
            if total_rms > 65.0:
                bass_ratio_avg = bass_ratio_avg * 0.94 + (kick_rms / max(total_rms, 1.0)) * 0.06
            else:
                bass_ratio_avg *= 0.92

            crisp_avg = crisp_avg * 0.88 + crisp_rms * 0.12
            crisp_flux = clamp((crisp_rms - crisp_avg * 1.06) / max(crisp_avg * 0.82, 130.0), 0.0, 1.0)

            now = time.monotonic()
            if snap["auto_calibrating"]:
                noise_floor = max(noise_floor * 0.97, raw_total_rms, raw_vocal_rms)
                left_sec = max(0.0, calibration_until - now)
                if now >= calibration_until:
                    calibrated = clamp(max(115.0, noise_floor * 1.8), 40.0, 8000.0)
                    with shared.lock:
                        shared.vibration_threshold = calibrated
                        shared.auto_calibrating = False
                        shared.stats.calibrating_left = 0.0
                    calibration_until = 0.0
                    log_queue.put(f"环境校准完成: 触发门限已设为 {calibrated:.0f}")
            else:
                calibration_until = 0.0
                if raw_total_rms < 16.0:
                    level = min(raw_total_rms, raw_vocal_rms)
                    noise_floor += (level - noise_floor) * 0.05
                else:
                    noise_floor *= 0.995

            raw_mode = classify_content(total_rms, vocal_rms, kick_rms, rhythm_env, bass_ratio_avg, crisp_rms, detected_mode)
            if detected_mode == "安静" and raw_mode != "安静":
                detected_mode = raw_mode
                candidate_mode = raw_mode
                candidate_chunks = 0
            elif raw_mode != candidate_mode:
                candidate_mode = raw_mode
                candidate_chunks = 1
            elif candidate_mode != detected_mode:
                candidate_chunks += 1
                need_chunks = 18 if (candidate_mode == "音乐" and kick_flux > 0.35) else 38
                if candidate_chunks >= need_chunks:
                    detected_mode = candidate_mode
                    candidate_chunks = 0

            effective = {
                "dual_motor_mode": snap["dual_motor_mode"],
                "vocal_only_mode": snap["vocal_only_mode"],
                "adaptive_threshold": snap["adaptive_threshold"],
                "intensity": snap["intensity"],
                "vibration_threshold": snap["vibration_threshold"],
                "dead_zone": snap["dead_zone"],
                "attack_rate": snap["attack_rate"],
                "release_rate": snap["release_rate"],
                "map_in_max": snap["map_in_max"],
                "gamma_correction": snap["gamma_correction"],
            }
            if snap["auto_profile"]:
                if detected_mode == "音乐":
                    # 智能音乐档：彻底压掉持续伴奏底震(steady仅作轻托底)，全靠鼓点瞬态(kick_flux/crisp_flux)跟手爆发！
                    effective.update(
                        dual_motor_mode=True,
                        vocal_only_mode=False,
                        adaptive_threshold=True,
                        vibration_threshold=max(185.0, noise_floor * 1.65),
                        map_in_max=4800.0,
                        gamma_correction=1.58,
                        attack_rate=0.76,
                        release_rate=0.38,
                        dead_zone=0.012,
                        intensity=clamp(snap["intensity"] * 0.96, 0.10, 1.0),
                    )
                elif detected_mode == "ASMR":
                    # 智能轻柔/人声档：保留 1.5%~22% 细腻微震梯度，右小马达轻柔酥麻，左重锤绝不轰鸣抢戏！
                    effective.update(
                        dual_motor_mode=False,
                        vocal_only_mode=True,
                        adaptive_threshold=True,
                        vibration_threshold=max(72.0, noise_floor * 1.30),
                        map_in_max=2600.0,
                        gamma_correction=0.88,
                        attack_rate=0.44,
                        release_rate=0.28,
                        dead_zone=0.012,
                        intensity=clamp(snap["intensity"] * 0.44, 0.08, 0.52),
                    )
                else:
                    effective.update(
                        vibration_threshold=max(240.0, noise_floor * 2.0),
                        attack_rate=0.20,
                        release_rate=0.35,
                        dead_zone=0.02,
                        intensity=0.0,
                    )

            adaptive_floor = noise_floor * 1.55 if effective["adaptive_threshold"] else 0.0
            threshold = max(effective["vibration_threshold"], adaptive_floor)

            def calc_vib(rms_val: float, gamma_override: float | None = None, thr_override: float | None = None) -> float:
                gm = gamma_override if gamma_override is not None else effective["gamma_correction"]
                th = thr_override if thr_override is not None else threshold
                mapped = map_value_gamma(rms_val, th, effective["map_in_max"], gm)
                return clamp(mapped * effective["intensity"])

            is_asmr_preset = (not snap["auto_profile"]) and effective["vocal_only_mode"] and effective["gamma_correction"] < 1.20
            is_auto_asmr = snap["auto_profile"] and detected_mode == "ASMR"
            is_asmr_like = is_asmr_preset or is_auto_asmr
            is_bass_immersion = (not snap["auto_profile"]) and effective["dual_motor_mode"] and effective["gamma_correction"] > 1.45
            is_full_power = (not snap["auto_profile"]) and effective["dual_motor_mode"] and effective["gamma_correction"] <= 1.45

            # 响度平滑门控：声音极小时绝不误触发重击底震，保留丝滑微小震动；真鼓点来临时 100% 满额跟手爆发！
            punch_gate = clamp((max(total_rms, kick_rms) - threshold * 0.82) / max(threshold * 2.3, 380.0)) ** 1.15

            if snap["auto_profile"] and detected_mode == "音乐":
                # 智能音乐：去底震留节拍！持续伴奏压至极低防嗡嗡底震，鼓点每一下干净起跳；轻音细部由右小马达细腻还原
                steady_l = calc_vib(bass_rms, 1.72, threshold * 1.25) * 0.22
                vocal_detail = vocal_rms * 0.78 + crisp_rms * 1.15
                steady_r = calc_vib(vocal_detail, 1.05, threshold * 0.62) * 0.26
                punch_l = (kick_flux * 0.92 + transient_avg * 0.30) * snap["rhythm_punch"] * snap["intensity"] * punch_gate
                punch_r = (crisp_flux * 0.54 + transient_avg * 0.16) * snap["rhythm_punch"] * snap["intensity"] * punch_gate
                target_left = clamp(steady_l + punch_l * 0.85)
                target_right = clamp(steady_r + punch_r)
            elif is_bass_immersion:
                # 低频沉浸（音乐·鼓点）：左大马达纯粹重锤鼓点，彻底砍除持续嗡嗡底震与人声干扰！
                steady_l = calc_vib(bass_rms, 1.75, threshold * 1.22) * 0.20
                punch_l = (kick_flux * 0.96 + transient_avg * 0.32) * snap["rhythm_punch"] * snap["intensity"] * punch_gate
                punch_r = crisp_flux * 0.26 * snap["rhythm_punch"] * snap["intensity"] * punch_gate
                target_left = clamp(steady_l + punch_l * 0.92)
                target_right = clamp(punch_r)
            elif is_full_power:
                # 全频强劲（游戏·影院）：安静环境零底震，轻脚步声微震，枪炮爆炸与重击双马达全功率齐震！
                steady_l = calc_vib(max(bass_rms, total_rms * 0.85), effective["gamma_correction"], threshold * 1.05) * 0.52
                steady_r = calc_vib(max(vocal_rms, crisp_rms * 1.25, total_rms * 0.75), 1.02, threshold * 0.72) * 0.50
                punch_l = (kick_flux * 0.86 + transient_avg * 0.44) * snap["rhythm_punch"] * snap["intensity"] * punch_gate
                punch_r = (crisp_flux * 0.64 + transient_avg * 0.36) * snap["rhythm_punch"] * snap["intensity"] * punch_gate
                target_left = clamp(steady_l + punch_l * 0.88)
                target_right = clamp(steady_r + punch_r * 0.88)
            elif is_asmr_like:
                # 轻柔细腻（ASMR·助眠）：左重锤静默防厚重底震，右轻马达在 1.5%~22% 丝滑呈现耳语刷麦微小震动！
                asmr_detail = vocal_rms * 0.88 + crisp_rms * 1.25
                base_r = calc_vib(asmr_detail, effective["gamma_correction"])
                texture = 0.0
                if asmr_detail > threshold:
                    breath = clamp((asmr_detail - threshold) / max(950.0, threshold * 5.0))
                    wave = 0.65 + 0.35 * math.sin(now * 2.0 * math.pi * 31.0)
                    texture = breath * snap["texture_amount"] * 0.045 * wave
                tap_spark = crisp_flux * (0.08 + 0.14 * snap["rhythm_punch"]) * snap["intensity"] * punch_gate
                target_right = clamp(base_r + texture + tap_spark, 0.0, 0.26 if is_asmr_preset else 0.32)
                target_left = clamp(max(0.0, target_right - 0.15) * 0.15)
            elif effective["vocal_only_mode"]:
                # 人声清晰（对白·播客）：右小马达干净利落跟随字句起伏，句间零底震，左重锤不抢戏！
                v_sig = vocal_rms + crisp_rms * 0.45
                v_out = calc_vib(v_sig, 1.05, threshold * 0.75)
                texture = 0.0
                if snap["texture_amount"] > 0.01 and v_sig > threshold:
                    breath = clamp((v_sig - threshold) / max(1100.0, threshold * 4.0))
                    wave = 0.65 + 0.35 * math.sin(now * 2.0 * math.pi * 32.0)
                    texture = breath * snap["texture_amount"] * 0.038 * wave
                target_right = clamp(v_out * 0.86 + texture + crisp_flux * 0.12 * snap["rhythm_punch"] * punch_gate)
                target_left = clamp(max(0.0, v_out - 0.18) * 0.20)
            else:
                # 自定义手动模式
                if effective["dual_motor_mode"]:
                    steady_l = calc_vib(bass_rms) * 0.38
                    steady_r = calc_vib(max(vocal_rms, crisp_rms * 1.15)) * 0.38
                else:
                    steady_l = steady_r = calc_vib(total_rms) * 0.45
                punch_l = (kick_flux * 0.80 + transient_avg * 0.30) * snap["rhythm_punch"] * snap["intensity"] * punch_gate
                punch_r = crisp_flux * 0.45 * snap["rhythm_punch"] * snap["intensity"] * punch_gate
                target_left = clamp(steady_l + punch_l * 0.75)
                target_right = clamp(steady_r + punch_r)

            # 非对称包络跟踪：鼓点瞬间爆发(attack)，拍间与暂停毫秒级干净收住绝不拖泥带水！
            if is_paused_silence:
                target_left = 0.0
                target_right = 0.0
                left_rel = 0.62
                right_rel = 0.65
            else:
                left_rel = max(effective["release_rate"], 0.32)
                right_rel = min(0.58, max(effective["release_rate"] * 1.18, 0.28))
            left_vib = smooth_signal(left_vib, target_left, effective["attack_rate"], left_rel)
            right_vib = smooth_signal(right_vib, target_right, effective["attack_rate"], right_rel)

            left_vib = apply_motor_deadzone(left_vib, effective["dead_zone"])
            right_vib = apply_motor_deadzone(right_vib, effective["dead_zone"])

            with shared.lock:
                if shared.enabled:
                    shared.audio_left = left_vib
                    shared.audio_right = right_vib
                else:
                    shared.audio_left = left_vib = 0.0
                    shared.audio_right = right_vib = 0.0
                shared.stats.total_rms = total_rms
                shared.stats.vocal_rms = vocal_rms
                shared.stats.bass_rms = bass_rms
                shared.stats.peak_rms = max(shared.stats.peak_rms * 0.97, total_rms)
                shared.stats.noise_floor = noise_floor
                shared.stats.effective_threshold = threshold
                shared.stats.detected_mode = detected_mode
                shared.stats.beat_pulse = max(transient, kick_flux * 0.92)
                shared.stats.spectrum = spectrum
                if snap["auto_calibrating"]:
                    shared.stats.calibrating_left = max(0.0, calibration_until - now)
    except Exception as exc:
        log_queue.put(f"音频引擎异常: {exc}")
    finally:
        with shared.lock:
            shared.audio_left = 0.0
            shared.audio_right = 0.0
        safe_close_stream(stream)
        try:
            audio.terminate()
        except Exception:
            pass
        if com_inited:
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 高弹力环形律动 UI 组件库（Rhythm Pulse Studio）
# ---------------------------------------------------------------------------

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    v = int(hex_color[1:], 16)
    return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


def _lerp_rgb(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = clamp(t)
    return (
        int(round(c1[0] + (c2[0] - c1[0]) * t)),
        int(round(c1[1] + (c2[1] - c1[1]) * t)),
        int(round(c1[2] + (c2[2] - c1[2]) * t)),
    )


def _palette_sample(stops: list[tuple[float, tuple[int, int, int]]], t: float) -> tuple[int, int, int]:
    t = clamp(t)
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if t <= p1:
            local = 0.0 if p1 <= p0 else (t - p0) / (p1 - p0)
            return _lerp_rgb(c0, c1, local)
    return stops[-1][1]


# 通透利落的高对比度声波流光色板
PALETTE_BASS_PULSE = [
    (0.0, _hex_to_rgb("#10b981")),
    (0.50, _hex_to_rgb("#34d399")),
    (0.82, _hex_to_rgb("#a3e635")),
    (1.0, _hex_to_rgb("#fde047")),
]

PALETTE_MAIN_SPECTRUM = [
    (0.0, _hex_to_rgb("#2dd4bf")),
    (0.25, _hex_to_rgb("#38bdf8")),
    (0.52, _hex_to_rgb("#818cf8")),
    (0.76, _hex_to_rgb("#e879f9")),
    (1.0, _hex_to_rgb("#fb7185")),
]

PALETTE_HIGH_DETAIL = [
    (0.0, _hex_to_rgb("#38bdf8")),
    (0.45, _hex_to_rgb("#a855f7")),
    (0.82, _hex_to_rgb("#f472b6")),
    (1.0, _hex_to_rgb("#fda4af")),
]


class CardFrame(tk.Canvas):
    """干净精致的深色圆角卡片（顶部微光细线）。"""

    def __init__(
        self,
        parent,
        pad_x=16,
        pad_y=13,
        radius=16,
        bg_fill=BG_CARD,
        border_color=BORDER,
        top_highlight=RIM_TOP,
    ):
        super().__init__(parent, height=S(32), bg=parent.cget("bg"), bd=0, highlightthickness=0)
        self.pad_x = S(pad_x)
        self.pad_y = S(pad_y)
        self.radius = S(radius)
        self.bg_fill = bg_fill
        self.border_color = border_color
        self.top_highlight = top_highlight

        self.inner = tk.Frame(self, bg=bg_fill)
        self._win_id = self.create_window(self.pad_x, self.pad_y, window=self.inner, anchor="nw")
        self.bind("<Configure>", self._on_resize)
        self.inner.bind("<Configure>", self._on_inner_resize)

    def sync_height(self):
        self.inner.update_idletasks()
        req_h = self.inner.winfo_reqheight() + self.pad_y * 2
        self.configure(height=req_h)

    def _on_inner_resize(self, _event=None):
        req_h = self.inner.winfo_reqheight() + self.pad_y * 2
        if abs(self.winfo_reqheight() - req_h) > 1:
            self.configure(height=req_h)

    def _on_resize(self, event):
        w = max(event.width, 10)
        h = max(event.height, 10)
        self.delete("card_bg")
        box = draw_rounded_box(
            self,
            1,
            1,
            w - 2,
            h - 2,
            self.radius,
            fill=self.bg_fill,
            outline=self.border_color,
            width=1,
            tags="card_bg",
        )
        r_off = max(self.radius, S(14))
        if w > r_off * 2 + 4:
            self.create_line(r_off, 2, w - r_off, 2, fill=self.top_highlight, width=1, tags="card_bg")
        self.tag_lower("card_bg")
        self.tag_lower(box)
        self.coords(self._win_id, self.pad_x, self.pad_y)
        self.itemconfigure(self._win_id, width=max(1, w - self.pad_x * 2))


class PulseLogoBadge(tk.Label):
    """抗锯齿律动声波圆形小图标。"""

    def __init__(self, parent, size_px=38):
        bg = parent.cget("bg")
        px = S(size_px)
        self._photo = ImageTk.PhotoImage(self._render(px, bg))
        super().__init__(parent, image=self._photo, bg=bg, bd=0, highlightthickness=0)

    @staticmethod
    def _render(size: int, bg_hex: str) -> Image.Image:
        ss = 4
        n = size * ss
        base = Image.new("RGBA", (n, n), (*_hex_to_rgb(bg_hex), 255))
        d = ImageDraw.Draw(base, "RGBA")
        cx = cy = n / 2.0
        r = n * 0.46
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(15, 22, 38, 255), outline=(56, 189, 248, 130), width=max(2, ss))
        bars = [0.22, 0.48, 0.64, 0.38, 0.24]
        cols = [(45, 212, 191, 255), (56, 189, 248, 255), (129, 140, 248, 255), (232, 121, 249, 255), (251, 113, 133, 255)]
        bw = max(3, int(n * 0.068))
        gap = int(n * 0.128)
        sx = cx - 2 * gap
        for i, (hf, col) in enumerate(zip(bars, cols)):
            bx = sx + i * gap
            bh = n * hf
            d.rounded_rectangle((bx - bw / 2, cy - bh / 2, bx + bw / 2, cy + bh / 2), radius=bw / 2, fill=col)
        return base.resize((size, size), Image.Resampling.LANCZOS)


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class _DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", wintypes.LONG),
        ("dmPositionY", wintypes.LONG),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
    ]


_user32_gdi = ctypes.windll.user32
_gdi32_blit = ctypes.windll.gdi32
_dwmapi = ctypes.windll.dwmapi

_user32_gdi.GetDC.argtypes = [wintypes.HWND]
_user32_gdi.GetDC.restype = wintypes.HDC
_user32_gdi.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32_gdi.ReleaseDC.restype = ctypes.c_int
_user32_gdi.IsWindow.argtypes = [wintypes.HWND]
_user32_gdi.IsWindow.restype = wintypes.BOOL
_user32_gdi.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32_gdi.MonitorFromWindow.restype = wintypes.HMONITOR
_user32_gdi.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
_user32_gdi.GetMonitorInfoW.restype = wintypes.BOOL
_user32_gdi.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
_user32_gdi.EnumDisplaySettingsW.restype = wintypes.BOOL

_gdi32_blit.SetDIBitsToDevice.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.UINT,
]
_gdi32_blit.SetDIBitsToDevice.restype = ctypes.c_int


def get_window_refresh_hz(hwnd: int) -> int:
    """实时查询窗口所在显示器的物理刷新率（支持 60Hz/120Hz/130Hz/144Hz/240Hz 及多屏拖动热自适应）。"""
    try:
        dev_name = None
        if hwnd and _user32_gdi.IsWindow(hwnd):
            hmon = _user32_gdi.MonitorFromWindow(hwnd, 2)
            if hmon:
                mi = _MONITORINFOEXW()
                mi.cbSize = ctypes.sizeof(_MONITORINFOEXW)
                if _user32_gdi.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                    dev_name = mi.szDevice
        dm = _DEVMODEW()
        dm.dmSize = ctypes.sizeof(_DEVMODEW)
        if _user32_gdi.EnumDisplaySettingsW(dev_name, 0xFFFFFFFF, ctypes.byref(dm)):
            hz = int(dm.dmDisplayFrequency)
            if 24 <= hz <= 500:
                return hz
    except Exception:
        pass
    return 60


def _rgba_to_bgra(img: Image.Image) -> Image.Image:
    """预交换 R/B 通道存入 PIL RGBA，使底层缓冲区直接对应 Win32 32位 BGRA 位图。"""
    r, g, b, a = img.split()
    return Image.merge("RGBA", (b, g, r, a))


def _pil_bgra_to_u32(img_rgba: Image.Image) -> np.ndarray:
    """将标准 RGBA PIL 图像转换为预交换 BGRA 的 (H, W) uint32 数组。"""
    bgra = _rgba_to_bgra(img_rgba)
    return np.frombuffer(bgra.tobytes(), dtype=np.uint32).reshape((img_rgba.height, img_rgba.width)).copy()


class _DiscUnit:
    """单个圆盘的亚像素 SDF 物理与图集单元（直接写入统一 ProMotion 共享画布）。"""

    _BAR_STEPS = 72
    _ROTOR_ANGLES = 48
    _ROTOR_RPM_TIERS = (0.18, 0.40, 0.64, 0.86, 1.00)

    def __init__(
        self,
        cx: float,
        cy: float,
        diameter: int,
        bar_count: int,
        palette: list[tuple[float, tuple[int, int, int]]],
        glow_color: str,
        max_val: float,
        role: str,
        stage_w: int,
        stage_h: int,
        bg_rgb: tuple[int, int, int],
    ):
        self.cx = cx
        self.cy = cy
        self.diam = S(diameter)
        self.bar_count = bar_count
        self.palette = palette
        self.glow_rgb = _hex_to_rgb(glow_color)
        self.max_val = max(1e-6, float(max_val))
        self.role = role
        self._sw = stage_w
        self._sh = stage_h
        self._bg_rgb = bg_rgb

        self._R = self.diam * 0.48
        self._r_core = self._R * 0.376
        self._r_start = self._r_core + 4.5 * UI_SCALE
        self._max_len = (self._R * 0.95) - self._r_start
        circum = 2.0 * math.pi * self._r_start
        self._base_hw = max(1.4 * UI_SCALE, (circum / self.bar_count) * 0.30)
        self._cap_thick = max(2.2 * UI_SCALE, self._R * 0.022)

        self._dirs = []
        self._bar_colors = []
        for i in range(bar_count):
            ang = -math.pi / 2.0 + (2.0 * math.pi * i / bar_count)
            self._dirs.append((math.cos(ang), math.sin(ang)))
            sym_t = (i / (bar_count / 2.0)) if i < bar_count / 2.0 else ((bar_count - i) / (bar_count / 2.0))
            self._bar_colors.append(_palette_sample(palette, sym_t))

        self._bars = [0.0] * bar_count
        self._peaks = [0.0] * bar_count
        self._peak_vels = [0.0] * bar_count
        self._target_bars = [0.0] * bar_count
        self._center_val = 0.0
        self._disp_center_val = 0.0
        self._pump = 0.0
        self._target_pump = 0.0
        self._phase = 0.0

        self._rotor_angle = -math.pi / 2.0
        self._rpm_norm = 0.0
        self._visual_omega = 0.0
        self._shake_phase = 0.0
        self._shake_x = 0.0
        self._shake_y = 0.0
        self._motion_energy = 0.0

    @property
    def is_settled(self) -> bool:
        if self._rpm_norm > 0.001 or self._pump > 0.003:
            return False
        for v in self._bars:
            if v > 0.002:
                return False
        for p in self._peaks:
            if p > 0.004:
                return False
        if self.role == "audio" and self._disp_center_val > 10.0:
            return False
        return True

    @staticmethod
    def _classic_fan_blade(
        mx: float,
        my: float,
        base_angle: float,
        r_hub: float,
        r_tip: float,
        root_hw: float,
        tip_hw: float,
        tilt_rad: float = 0.08,
    ) -> list[tuple[float, float]]:
        r_shoulder = r_hub + (r_tip - r_hub) * 0.72
        pts = []
        for i in range(5):
            t = i / 4.0
            r = r_hub + (r_shoulder - r_hub) * t
            c_a = base_angle - tilt_rad * t
            hw = root_hw + (tip_hw - root_hw) * (t ** 0.72)
            pts.append((mx + r * math.cos(c_a + hw), my + r * math.sin(c_a + hw)))
        c_tip = base_angle - tilt_rad
        for i in range(1, 5):
            f = i / 4.0
            ang_k = f * (math.pi / 2.0)
            r = r_shoulder + (r_tip - r_shoulder) * math.sin(ang_k)
            hw = tip_hw * (0.76 + 0.24 * math.cos(ang_k))
            pts.append((mx + r * math.cos(c_tip + hw), my + r * math.sin(c_tip + hw)))
        arc_hw = tip_hw * 0.76
        for i in range(1, 7):
            f = i / 6.0
            a = c_tip + arc_hw * (1.0 - 2.0 * f)
            pts.append((mx + r_tip * math.cos(a), my + r_tip * math.sin(a)))
        for i in range(1, 5):
            f = i / 4.0
            ang_k = (1.0 - f) * (math.pi / 2.0)
            r = r_shoulder + (r_tip - r_shoulder) * math.sin(ang_k)
            hw = tip_hw * (0.76 + 0.24 * math.cos(ang_k))
            pts.append((mx + r * math.cos(c_tip - hw), my + r * math.sin(c_tip - hw)))
        for i in range(3, -1, -1):
            t = i / 4.0
            r = r_hub + (r_shoulder - r_hub) * t
            c_a = base_angle - tilt_rad * t
            hw = root_hw + (tip_hw - root_hw) * (t ** 0.72)
            pts.append((mx + r * math.cos(c_a - hw), my + r * math.sin(c_a - hw)))
        return pts

    def bake_into(self, base_rgb_f32: np.ndarray, idle_flat_u32: np.ndarray):
        """向量化亚像素解析 SDF 烘焙 80 级丝滑音柱 + 0.5px 亚像素小帽 + 72 细分角度顺时针风扇转子。"""
        ss = 2 if UI_SCALE >= 1.25 else 3
        w, h = self._sw, self._sh
        cx, cy, R = self.cx, self.cy, self._R
        r_core, r_start, max_len = self._r_core, self._r_start, self._max_len
        base_hw, cap_thick = self._base_hw, self._cap_thick
        pad_c = 4

        # 仅在当前圆盘局部外接矩形内构建坐标网格，极速完成 80 级 SDF 向量化烘焙（< 25ms/盘）
        margin = int(math.ceil(R + 10.0 * UI_SCALE))
        x0_box = max(0, int(math.floor(cx)) - margin)
        x1_box = min(w, int(math.ceil(cx)) + margin)
        y0_box = max(0, int(math.floor(cy)) - margin)
        y1_box = min(h, int(math.ceil(cy)) + margin)

        yy, xx = np.mgrid[y0_box:y1_box, x0_box:x1_box]
        dx = (xx + 0.5) - cx
        dy = (yy + 0.5) - cy
        rad = np.hypot(dx, dy)
        ang = np.arctan2(dy, dx)
        ring_mask = (rad >= (r_start - 2.5 * UI_SCALE)) & (rad <= (r_start + max_len + 4.5 * UI_SCALE))
        half_sec = (math.pi / self.bar_count) * 1.42
        global_idx_grid = yy * w + xx

        vals = np.linspace(0.0, 1.0, self._BAR_STEPS + 1, dtype=np.float32)
        lengths = (max_len * (0.035 + 0.965 * vals))[:, None]
        hw_outs = (base_hw * (1.0 + 0.45 * vals))[:, None]
        bar_alphas = ((125.0 + 130.0 * np.clip(vals * 1.4, 0.0, 1.0)) / 255.0)[:, None]
        glow_r = np.float32(2.3 * UI_SCALE)
        glow_ext = np.float32(1.8 * UI_SCALE)

        self._bar_u32_table: list[list[tuple[np.ndarray, np.ndarray] | None]] = [[None] for _ in range(self.bar_count)]

        for i in range(self.bar_count):
            ux, uy = self._dirs[i]
            b_ang = -math.pi / 2.0 + (2.0 * math.pi * i / self.bar_count)
            adiff = np.abs(((ang - b_ang + math.pi) % (2.0 * math.pi)) - math.pi)
            loc_y, loc_x = np.nonzero(ring_mask & (adiff <= half_sec))
            cand = global_idx_grid[loc_y, loc_x].astype(np.intp)

            px = (xx[loc_y, loc_x] + 0.5 - (cx + r_start * ux)).astype(np.float32)
            py = (yy[loc_y, loc_x] + 0.5 - (cy + r_start * uy)).astype(np.float32)
            along = px * np.float32(ux) + py * np.float32(uy)
            perp = np.abs(-px * np.float32(uy) + py * np.float32(ux))

            t_al = np.clip(along[None, :] / lengths, 0.0, 1.0)
            hw_at = np.float32(base_hw) + (hw_outs - np.float32(base_hw)) * t_al
            d_al = np.maximum(-along[None, :], along[None, :] - lengths)
            d_pe = perp[None, :] - hw_at
            sdf = np.hypot(np.maximum(d_al, 0.0), np.maximum(d_pe, 0.0)) + np.minimum(np.maximum(d_al, d_pe), 0.0)

            cov = np.clip(0.5 - sdf, 0.0, 1.0) * bar_alphas
            d_al_g = np.maximum(-along[None, :], along[None, :] - (lengths + glow_ext))
            sdf_g = np.hypot(np.maximum(d_al_g, 0.0), np.maximum(d_pe, 0.0))
            glow_a = np.where(
                vals[:, None] > 0.16,
                (np.clip(1.0 - sdf_g / glow_r, 0.0, 1.0) ** 1.6) * (np.float32(52.0 / 255.0) * vals[:, None]),
                np.float32(0.0),
            )

            base_col = np.array(self._bar_colors[i], dtype=np.float32)
            bright_t = np.clip((vals - 0.55) * 0.72, 0.0, 1.0)[:, None]
            bright_cols = base_col[None, :] + (255.0 - base_col[None, :]) * bright_t

            bg_cand = base_rgb_f32[cand]  # (M, 3)
            rgb1 = bg_cand[None, :, :] * (1.0 - glow_a[:, :, None]) + base_col[None, None, :] * glow_a[:, :, None]
            rgb_out = rgb1 * (1.0 - cov[:, :, None]) + bright_cols[:, None, :] * cov[:, :, None]

            r_u = np.rint(rgb_out[:, :, 0]).astype(np.uint32)
            g_u = np.rint(rgb_out[:, :, 1]).astype(np.uint32)
            b_u = np.rint(rgb_out[:, :, 2]).astype(np.uint32)
            packed = 0xFF000000 | (r_u << 16) | (g_u << 8) | b_u

            # step=0 直接写入全局默认底图
            s0_mask = cov[0] > 0.004
            idle_flat_u32[cand[s0_mask]] = packed[0, s0_mask]

            for step in range(1, self._BAR_STEPS + 1):
                vis_m = (cov[step] > 0.004) | (glow_a[step] > 0.004) | s0_mask
                self._bar_u32_table[i].append((cand[vis_m], packed[step, vis_m].copy()))

        # 2. 预烘焙 2x2 半像素 (0.5px) 亚像素抛飞小帽表（高刷慢速抛物线顶点零跳格！）
        cap_box = max(10, int(math.ceil((base_hw * 1.6 + cap_thick) * 2.0)) + 6)
        if cap_box % 2 == 1:
            cap_box += 1
        self._cap_half = cap_box // 2
        cy_grid, cx_grid = np.mgrid[0:cap_box, 0:cap_box].astype(np.float32)
        bg_cap_rgb = np.array(self._bg_rgb, dtype=np.float32)
        hl_cap = float(cap_thick * 0.5)
        cap_hw = float(base_hw * 1.38)
        self._cap_sub_sparse: list[list[tuple[np.ndarray, np.ndarray]]] = []
        for i in range(self.bar_count):
            ux, uy = self._dirs[i]
            cap_col = np.array(_lerp_rgb(self._bar_colors[i], (255, 255, 255), 0.72), dtype=np.float32)
            subs = []
            for qy in (0.0, 0.5):
                for qx in (0.0, 0.5):
                    mcx = self._cap_half + qx
                    mcy = self._cap_half + qy
                    px = (cx_grid + 0.5) - mcx
                    py = (cy_grid + 0.5) - mcy
                    al = px * ux + py * uy
                    pe = np.abs(-px * uy + py * ux)
                    d_a = np.maximum(np.abs(al) - hl_cap, 0.0)
                    d_p = np.maximum(pe - cap_hw, 0.0)
                    sdf_c = np.hypot(d_a, d_p) + np.minimum(np.maximum(np.abs(al) - hl_cap, pe - cap_hw), 0.0)
                    ca = np.clip(0.5 - sdf_c, 0.0, 1.0) * (235.0 / 255.0)
                    ys, xs = np.nonzero(ca > 0.02)
                    a_sel = ca[ys, xs][:, None]
                    rgb_c = bg_cap_rgb[None, :] * (1.0 - a_sel) + cap_col[None, :] * a_sel
                    cu32 = (
                        0xFF000000
                        | (np.rint(rgb_c[:, 0]).astype(np.uint32) << 16)
                        | (np.rint(rgb_c[:, 1]).astype(np.uint32) << 8)
                        | np.rint(rgb_c[:, 2]).astype(np.uint32)
                    )
                    rel_off = ((ys - self._cap_half) * w + (xs - self._cap_half)).astype(np.intp)
                    subs.append((rel_off, cu32))
            self._cap_sub_sparse.append(subs)

        # 3. 节拍冲击波光环精灵
        self._shock_stamps: list[tuple[int, int, Image.Image] | None] = [None]
        for sk in range(1, 9):
            cp = sk / 8.0
            r_sh = r_core + (4.0 + 5.0 * cp) * UI_SCALE
            sh_rad = int(math.ceil(r_sh)) + 4
            sh_sz = sh_rad * 2
            sh_hi = Image.new("RGBA", (sh_sz * ss, sh_sz * ss), (0, 0, 0, 0))
            ImageDraw.Draw(sh_hi, "RGBA").ellipse(
                ((sh_rad - r_sh) * ss, (sh_rad - r_sh) * ss, (sh_rad + r_sh) * ss, (sh_rad + r_sh) * ss),
                outline=(*self.glow_rgb, int(85 * cp)),
                width=max(2, int(round(1.6 * UI_SCALE * ss))),
            )
            sh_1x = _rgba_to_bgra(sh_hi.resize((sh_sz, sh_sz), Image.Resampling.LANCZOS))
            self._shock_stamps.append((int(round(cx)) - sh_rad, int(round(cy)) - sh_rad, sh_1x))

        # 4. 盘心腔体稀疏 uint32 直写表（零 PIL alpha_composite 开销！）+ 连续 SDF 声波 / 顺时针风扇转子
        shell_rad = int(math.ceil(r_core)) + 4
        shell_sz = shell_rad * 2
        sx0_sh = int(round(cx)) - shell_rad
        sy0_sh = int(round(cy)) - shell_rad
        sh_bg_crop = Image.fromarray(
            np.dstack([
                base_rgb_f32.reshape((h, w, 3))[sy0_sh : sy0_sh + shell_sz, sx0_sh : sx0_sh + shell_sz].astype(np.uint8),
                np.full((shell_sz, shell_sz), 255, dtype=np.uint8),
            ]),
            mode="RGBA",
        )
        sh_yy, sh_xx = np.mgrid[sy0_sh : sy0_sh + shell_sz, sx0_sh : sx0_sh + shell_sz]
        sh_global_idx = (sh_yy * w + sh_xx).astype(np.intp)
        self._core_shells_u32: list[tuple[np.ndarray, np.ndarray]] = []

        def bake_shell_sparse(hi_img: Image.Image):
            s1x = hi_img.resize((shell_sz, shell_sz), Image.Resampling.LANCZOS)
            a_ch = np.array(s1x.getchannel("A"), dtype=np.uint8)
            ym, xm = np.nonzero(a_ch > 4)
            comp = _pil_bgra_to_u32(Image.alpha_composite(sh_bg_crop, s1x))
            self._core_shells_u32.append((sh_global_idx[ym, xm].copy(), comp[ym, xm].copy()))

        if self.role == "audio":
            for pk_i in range(13):
                cp = pk_i / 12.0
                lit = max(0.35, cp)
                rim_col = _lerp_rgb(self.glow_rgb, (255, 255, 255), 0.35 * lit)
                rim_alpha = int(170 + 80 * clamp(lit))
                rim_w = max(2, int(round((1.3 + 1.1 * cp) * UI_SCALE * ss)))
                hi = Image.new("RGBA", (shell_sz * ss, shell_sz * ss), (0, 0, 0, 0))
                hd = ImageDraw.Draw(hi, "RGBA")
                c0, c1 = (shell_rad - r_core) * ss, (shell_rad + r_core) * ss
                hd.ellipse((c0, c0, c1, c1), fill=(10, 14, 24, 248))
                hd.ellipse((c0, c0, c1, c1), outline=(*rim_col, rim_alpha), width=rim_w)
                bake_shell_sparse(hi)

            # 实时连续亚像素 SDF 双层声波网格（零相位量化、零振幅阶梯）
            span = r_core * 0.80
            wx0 = int(math.floor(cx - span))
            wx1 = int(math.ceil(cx + span))
            wy0 = int(math.floor(cy - r_core * 0.72))
            wy1 = int(math.ceil(cy + r_core * 0.72))
            self._wave_box = (wx0, wy0, wx1, wy1)
            xs_pix = np.arange(wx0, wx1, dtype=np.float32) + 0.5
            self._wave_t = np.clip((xs_pix - (cx - span)) / max(1.0, 2.0 * span), 0.0, 1.0)
            self._wave_env = (np.maximum(np.float32(0.0), np.sin(self._wave_t * np.float32(math.pi))) ** 1.35).astype(np.float32)
            self._wave_ys = (np.arange(wy0, wy1, dtype=np.float32) + 0.5 - cy)[:, None]
            self._wave_b_idx = np.clip((self._wave_t * (self.bar_count // 2 - 1)).astype(np.int32), 0, self.bar_count // 2 - 1)
            self._wave_c_sub = np.array(_lerp_rgb(self.palette[0][1], self.palette[-1][1], 0.55), dtype=np.float32)
            self._wave_c_glow = np.array(self.glow_rgb, dtype=np.float32)
            self._wave_c_main = np.array(_lerp_rgb(self.glow_rgb, (255, 255, 255), 0.55), dtype=np.float32)
            self._wave_c_bg = np.array((10.0, 14.0, 24.0), dtype=np.float32)
        else:
            r_arc = r_core * 0.87
            aw = max(2, int(round(1.8 * UI_SCALE * ss)))
            for idx in range(17):
                rpm = 0.0 if idx == 0 else (idx / 16.0)
                hi = Image.new("RGBA", (shell_sz * ss, shell_sz * ss), (0, 0, 0, 0))
                hd = ImageDraw.Draw(hi, "RGBA")
                c0, c1 = (shell_rad - r_core) * ss, (shell_rad + r_core) * ss
                hd.ellipse((c0, c0, c1, c1), fill=(10, 14, 24, 248))
                a0, a1 = (shell_rad - r_arc) * ss, (shell_rad + r_arc) * ss
                hd.arc((a0, a0, a1, a1), start=135, end=405, fill=(32, 40, 58, 135), width=aw)
                if idx > 0:
                    end_a = 135.0 + 270.0 * rpm
                    acol = _lerp_rgb(self.glow_rgb, (255, 255, 255), 0.26 * rpm)
                    hd.arc((a0, a0, a1, a1), start=135, end=end_a, fill=(*acol, int(155 + 95 * rpm)), width=aw)
                    rim_col = _lerp_rgb(self.glow_rgb, (255, 255, 255), 0.35 * rpm)
                    rim_alpha = int(170 + 80 * rpm)
                else:
                    rim_col = (64, 76, 102)
                    rim_alpha = 145
                rim_w = max(2, int(round((1.3 + 1.1 * rpm) * UI_SCALE * ss)))
                hd.ellipse((c0, c0, c1, c1), outline=(*rim_col, rim_alpha), width=rim_w)
                bake_shell_sparse(hi)

            is_bass = self.role == "bass"
            n_blades = 3 if is_bass else 5
            r_hub = r_core * 0.22
            r_tip = r_core * 0.75
            root_hw = math.radians(16.0 if is_bass else 10.5)
            tip_hw = math.radians(36.0 if is_bass else 21.5)
            tilt = math.radians(7.0 if is_bass else 6.0)

            rot_rad = int(math.ceil(r_tip)) + 3
            rot_sz = rot_rad * 2
            self._rotor_base_pos = (int(round(cx)) - rot_rad, int(round(cy)) - rot_rad)

            period = (2.0 * math.pi) / n_blades
            self._rotor_period = period
            all_tiers = (0.0,) + self._ROTOR_RPM_TIERS
            self._rotor_tiers: list[list[Image.Image]] = []
            r_strip_w = (rot_sz + pad_c) * self._ROTOR_ANGLES

            for rpm_val in all_tiers:
                running = rpm_val > 0.008
                rh_s, rt_s = r_hub * ss, r_tip * ss
                l_bg = Image.new("RGBA", (r_strip_w * ss, rot_sz * ss), (0, 0, 0, 0))
                l_blade = Image.new("RGBA", (r_strip_w * ss, rot_sz * ss), (0, 0, 0, 0))
                l_hi = Image.new("RGBA", (r_strip_w * ss, rot_sz * ss), (0, 0, 0, 0))
                d_bg = ImageDraw.Draw(l_bg, "RGBA")
                d_blade = ImageDraw.Draw(l_blade, "RGBA")
                d_hi = ImageDraw.Draw(l_hi, "RGBA")

                idle_metal = (76, 88, 112)
                blade_col = _lerp_rgb(idle_metal, self.glow_rgb, 0.26 + 0.70 * rpm_val) if running else idle_metal
                soft_hi = _lerp_rgb(blade_col, (255, 255, 255), 0.44 if running else 0.16)
                dot_col = _lerp_rgb(self.glow_rgb, (255, 255, 255), 0.56 * rpm_val) if running else (82, 94, 118)
                hub_w = max(2, int(round(1.5 * UI_SCALE * ss)))

                for ai in range(self._ROTOR_ANGLES):
                    base_ang = (ai / self._ROTOR_ANGLES) * period
                    mx_s = (ai * (rot_sz + pad_c) + rot_rad) * ss
                    my_s = rot_rad * ss

                    if rpm_val > 0.22:
                        shutter_k = clamp((rpm_val - 0.22) / 0.78)
                        r_blur = (rh_s + rt_s) * 0.56
                        blur_w = max(2, int(round((rt_s - rh_s) * (0.44 if is_bass else 0.52))))
                        d_bg.ellipse(
                            (mx_s - r_blur, my_s - r_blur, mx_s + r_blur, my_s + r_blur),
                            outline=(*self.glow_rgb, int(shutter_k * (28 if is_bass else 42))),
                            width=blur_w,
                        )
                    if rpm_val > 0.08:
                        max_trail = (0.34 if is_bass else 0.30) * (0.30 + 0.70 * rpm_val)
                        t_col = _lerp_rgb(self.palette[0][1], self.glow_rgb, 0.55)
                        for tl in (2, 1):
                            frac = tl / 2.0
                            t_alpha = int((1.0 - frac * 0.55) * (32 + (58 if is_bass else 68) * rpm_val))
                            ang_off = -max_trail * frac
                            for b in range(n_blades):
                                ba = base_ang + (2.0 * math.pi * b / n_blades) + ang_off
                                poly = self._classic_fan_blade(mx_s, my_s, ba, rh_s, rt_s, root_hw * 0.92, tip_hw * 0.94, tilt)
                                d_bg.polygon(poly, fill=(*t_col, t_alpha))

                    if running and rpm_val > 0.06:
                        r_glow = rh_s * (1.55 + 0.30 * rpm_val)
                        d_bg.ellipse((mx_s - r_glow, my_s - r_glow, mx_s + r_glow, my_s + r_glow), fill=(*self.glow_rgb, int(48 * rpm_val)))

                    for b in range(n_blades):
                        ba = base_ang + (2.0 * math.pi * b / n_blades)
                        poly = self._classic_fan_blade(mx_s, my_s, ba, rh_s, rt_s, root_hw, tip_hw, tilt)
                        d_blade.polygon(poly, fill=(*blade_col, 228 if running else 178))
                        inner_poly = self._classic_fan_blade(
                            mx_s, my_s, ba + math.radians(2.5),
                            rh_s + (rt_s - rh_s) * 0.16,
                            rt_s - (rt_s - rh_s) * 0.14,
                            root_hw * 0.55, tip_hw * 0.58, tilt * 0.85,
                        )
                        d_hi.polygon(inner_poly, fill=(*soft_hi, 64 if running else 26))

                    d_hi.ellipse(
                        (mx_s - rh_s, my_s - rh_s, mx_s + rh_s, my_s + rh_s),
                        fill=(16, 22, 36, 252),
                        outline=(*soft_hi, 220 if running else 125),
                        width=hub_w,
                    )
                    r_dot = rh_s * 0.46
                    d_hi.ellipse((mx_s - r_dot, my_s - r_dot, mx_s + r_dot, my_s + r_dot), fill=(*dot_col, 240 if running else 150))

                r_comp = Image.alpha_composite(Image.alpha_composite(l_bg, l_blade), l_hi)
                r_1x = _rgba_to_bgra(r_comp.resize((r_strip_w, rot_sz), Image.Resampling.LANCZOS))
                self._rotor_tiers.append([
                    r_1x.crop((ai * (rot_sz + pad_c), 0, ai * (rot_sz + pad_c) + rot_sz, rot_sz))
                    for ai in range(self._ROTOR_ANGLES)
                ])

    def step_physics(self, dt: float, bar_targets: list[float], center_val: float, pump_tgt: float, active: bool, launch_prog: float | None):
        """连续时间微分（Apple ProMotion 阻尼弹簧包络），任意刷新率 (60Hz~144Hz) 运动速度 100% 物理恒定且如黄油顺滑。"""
        self._phase += dt * (2.6 + 5.2 * self._pump)
        n = self.bar_count
        # 连续时间重力加速度（单位：归一化高度 / s^2）
        grav_acc = 18.5
        up_k = 1.0 - math.exp(-dt / 0.020)
        dn_k = 1.0 - math.exp(-dt / 0.105)
        max_delta = 0.0

        if launch_prog is not None and active:
            p = clamp(launch_prog)
            burst = math.sin(p * math.pi) ** 0.75
            l_k = 1.0 - math.exp(-dt / 0.018)
            for i in range(n):
                ang = 2.0 * math.pi * i / n
                tgt = clamp(burst * (0.55 + 0.45 * abs(math.sin(ang * 2.0 + p * 4.0))))
                cur = self._bars[i]
                nxt = cur + (tgt - cur) * l_k
                if abs(nxt - cur) > max_delta:
                    max_delta = abs(nxt - cur)
                if nxt > self._peaks[i]:
                    self._peaks[i] = nxt
                    self._peak_vels[i] = max(0.0, (nxt - cur) / max(dt, 0.004) * 0.28)
                else:
                    self._peak_vels[i] -= grav_acc * dt
                    self._peaks[i] = max(nxt, min(1.06, self._peaks[i] + self._peak_vels[i] * dt))
                self._bars[i] = nxt
            self._pump = burst * 0.85
            max_delta = max(max_delta, 0.2)
        else:
            for i in range(n):
                tgt = clamp(bar_targets[i]) if (active and i < len(bar_targets)) else 0.0
                cur = self._bars[i]
                if tgt > cur:
                    nxt = cur + (tgt - cur) * up_k
                else:
                    nxt = cur + (tgt - cur) * dn_k
                    if nxt < 0.003:
                        nxt = 0.0
                diff_b = nxt - cur
                if abs(diff_b) > max_delta:
                    max_delta = abs(diff_b)
                self._bars[i] = nxt

                if nxt >= self._peaks[i]:
                    self._peaks[i] = nxt
                    if diff_b > 0.015:
                        self._peak_vels[i] = max(self._peak_vels[i], clamp((diff_b / max(dt, 0.004)) * 0.22, 0.0, 4.8))
                    else:
                        self._peak_vels[i] = max(0.0, self._peak_vels[i])
                else:
                    self._peak_vels[i] -= grav_acc * dt
                    self._peaks[i] += self._peak_vels[i] * dt
                    if self._peaks[i] <= nxt:
                        self._peaks[i] = nxt
                        self._peak_vels[i] = 0.0
                    elif self._peaks[i] > 1.07:
                        self._peaks[i] = 1.07
                        self._peak_vels[i] = min(0.0, self._peak_vels[i])

            pt = clamp(pump_tgt) if active else 0.0
            self._pump += (pt - self._pump) * ((1.0 - math.exp(-dt / 0.022)) if pt > self._pump else (1.0 - math.exp(-dt / 0.095)))
            if self._pump < 0.002:
                self._pump = 0.0

        cv = max(0.0, float(center_val)) if active else 0.0
        dc = cv - self._disp_center_val
        self._disp_center_val += dc * ((1.0 - math.exp(-dt / 0.025)) if dc > 0 else (1.0 - math.exp(-dt / 0.085)))
        if abs(dc) < self.max_val * 0.001:
            self._disp_center_val = cv

        if self.role != "audio":
            raw_duty = clamp(cv / self.max_val) if active else 0.0
            is_bass = self.role == "bass"
            n_blades = 3 if is_bass else 5
            target_rpm = (raw_duty ** 0.72) if raw_duty > 0.006 else 0.0
            tau = (0.055 if is_bass else 0.030) if target_rpm > self._rpm_norm else (0.072 if is_bass else 0.050)
            self._rpm_norm += (target_rpm - self._rpm_norm) * (1.0 - math.exp(-dt / tau))
            if target_rpm <= 0.0 and self._rpm_norm < 0.010:
                self._rpm_norm = 0.0

            max_vis_omega = 20.5 if is_bass else 16.2
            if self._rpm_norm > 0.0:
                self._visual_omega = 1.4 + (max_vis_omega - 1.4) * (self._rpm_norm ** 0.78)
                blade_pitch = (2.0 * math.pi) / n_blades
                d_theta = min(self._visual_omega * dt, blade_pitch * 0.22)
                self._rotor_angle = (self._rotor_angle + d_theta) % (2.0 * math.pi)
            else:
                self._visual_omega = 0.0

            if self._rpm_norm > 0.008:
                self._shake_phase += dt * (36.0 + 28.0 * self._rpm_norm if is_bass else 64.0 + 42.0 * self._rpm_norm)
                amp_s = (0.028 if is_bass else 0.018) * (self._rpm_norm ** 0.9)
                self._shake_x = math.sin(self._shake_phase * 1.13) * amp_s
                self._shake_y = math.cos(self._shake_phase * 1.37) * amp_s
            else:
                self._shake_x = self._shake_y = 0.0

        self._motion_energy = max(max_delta / max(dt, 0.004), self._rpm_norm, self._pump * 0.8)

    def draw_into(self, frame_u32: np.ndarray, frame_flat: np.ndarray, frame_pil: Image.Image):
        w, h = self._sw, self._sh
        cx, cy = self.cx, self.cy
        cap_half = self._cap_half
        r_cap_base = self._r_start + 2.8 * UI_SCALE + self._cap_thick * 0.5
        max_len = self._max_len
        b_steps = self._BAR_STEPS
        b_table = self._bar_u32_table
        c_subs = self._cap_sub_sparse
        dirs = self._dirs
        bars = self._bars
        peaks = self._peaks

        # 1. 80 级亚像素音柱 + 0.5px 亚像素抛飞小帽直写
        for i in range(self.bar_count):
            v = bars[i]
            if v > 0.003:
                s = min(b_steps, max(1, int(v * b_steps + 0.5)))
                idx_arr, val_arr = b_table[i][s]
                frame_flat[idx_arr] = val_arr
            pk = peaks[i]
            if pk > 0.045:
                rc = r_cap_base + max_len * (0.035 + 0.965 * pk)
                ux, uy = dirs[i]
                fx = cx + rc * ux
                fy = cy + rc * uy
                px = int(math.floor(fx))
                py = int(math.floor(fy))
                if cap_half <= px < w - cap_half - 1 and cap_half <= py < h - cap_half - 1:
                    sub_idx = (2 if (fy - py) >= 0.5 else 0) + (1 if (fx - px) >= 0.5 else 0)
                    rel_off, c_vals = c_subs[i][sub_idx]
                    frame_flat[py * w + px + rel_off] = c_vals

        # 2. 冲击波与盘心腔体
        core_pump = self._pump if self.role == "audio" else self._rpm_norm
        ac = frame_pil.alpha_composite
        if core_pump > 0.05:
            sk = min(8, max(1, int(core_pump * 8.5)))
            sx, sy, sh_img = self._shock_stamps[sk]
            ac(sh_img, (sx, sy))

        if self.role == "audio":
            shell_idx = min(12, int(self._pump * 12.4))
            sh_i, sh_v = self._core_shells_u32[shell_idx]
            frame_flat[sh_i] = sh_v

            # 实时连续向量化 SDF 声波线（紧致行裁剪：仅对声波实际扫过的窄带切片求值，提速 4 倍！）
            audio_norm = clamp(self._disp_center_val / self.max_val)
            audio_vis = max(audio_norm ** 0.62 if audio_norm > 0.002 else 0.0, self._pump * 0.55)
            lvl = max(audio_vis, self._pump * 0.82)
            wx0, wy0, wx1, wy1 = self._wave_box
            t = self._wave_t
            ys = self._wave_ys
            if lvl > 0.012:
                amp = np.float32(self._r_core * (0.03 + 0.55 * lvl))
                half_b = np.array(bars[: self.bar_count // 2], dtype=np.float32)
                b_mod = np.float32(0.65) + np.float32(0.55) * half_b[self._wave_b_idx]
                env = self._wave_env
                y1 = np.sin(t * np.float32(math.pi * 3.6) + np.float32(self._phase * 1.45)) * amp * env * b_mod
                y2 = np.sin(t * np.float32(math.pi * 5.0) - np.float32(self._phase * 1.15 - 1.1)) * (amp * np.float32(0.60)) * env

                pad_px = 4.2 * UI_SCALE
                y_lo = float(min(y1.min(), y2.min())) - pad_px
                y_hi = float(max(y1.max(), y2.max())) + pad_px
                r0 = max(0, int(math.floor(y_lo - float(ys[0, 0]))))
                r1 = min(ys.shape[0], int(math.ceil(y_hi - float(ys[0, 0]))) + 1)
                if r1 > r0:
                    ys_sub = ys[r0:r1]
                    dy1 = np.empty_like(y1)
                    dy1[1:-1] = (y1[2:] - y1[:-2]) * np.float32(0.5)
                    dy1[0] = y1[1] - y1[0]
                    dy1[-1] = y1[-1] - y1[-2]
                    dy2 = np.empty_like(y2)
                    dy2[1:-1] = (y2[2:] - y2[:-2]) * np.float32(0.5)
                    dy2[0] = y2[1] - y2[0]
                    dy2[-1] = y2[-1] - y2[-2]

                    inv_s1 = np.float32(1.0) / np.hypot(np.float32(1.0), dy1)
                    inv_s2 = np.float32(1.0) / np.hypot(np.float32(1.0), dy2)
                    d1 = np.abs(ys_sub - y1[None, :]) * inv_s1[None, :]
                    d2 = np.abs(ys_sub - y2[None, :]) * inv_s2[None, :]

                    a2 = np.clip(np.float32(0.8 * UI_SCALE + 0.5) - d2, 0.0, 1.0) * np.float32(140.0 / 255.0)
                    ag = (np.clip(np.float32(1.0) - d1 / np.float32(3.1 * UI_SCALE), 0.0, 1.0) ** 1.7) * np.float32(88.0 / 255.0)
                    am = np.clip(np.float32(1.0 * UI_SCALE + 0.5) - d1, 0.0, 1.0) * np.float32(246.0 / 255.0)

                    hit = (a2 > 0.006) | (ag > 0.006) | (am > 0.006)
                    hy, hx = np.nonzero(hit)
                    if hy.size:
                        va2 = a2[hy, hx][:, None]
                        vag = ag[hy, hx][:, None]
                        vam = am[hy, hx][:, None]
                        rgb = self._wave_c_bg[None, :] * (1.0 - va2) + self._wave_c_sub[None, :] * va2
                        rgb = rgb * (1.0 - vag) + self._wave_c_glow[None, :] * vag
                        rgb = rgb * (1.0 - vam) + self._wave_c_main[None, :] * vam
                        sub_slice = frame_u32[wy0 + r0 : wy0 + r1, wx0:wx1]
                        sub_slice[hy, hx] = (
                            0xFF000000
                            | (np.rint(rgb[:, 0]).astype(np.uint32) << 16)
                            | (np.rint(rgb[:, 1]).astype(np.uint32) << 8)
                            | np.rint(rgb[:, 2]).astype(np.uint32)
                        )
            else:
                d0 = np.abs(ys[:, 0])
                a0 = np.clip(np.float32(0.75 * UI_SCALE + 0.5) - d0, 0.0, 1.0) * np.float32(130.0 / 255.0)
                hy = np.flatnonzero(a0 > 0.01)
                if hy.size:
                    va0 = a0[hy][:, None]
                    idle_col = np.array((82.0, 95.0, 122.0), dtype=np.float32)
                    rgb = self._wave_c_bg[None, :] * (1.0 - va0) + idle_col[None, :] * va0
                    row_u32 = (
                        0xFF000000
                        | (np.rint(rgb[:, 0]).astype(np.uint32) << 16)
                        | (np.rint(rgb[:, 1]).astype(np.uint32) << 8)
                        | np.rint(rgb[:, 2]).astype(np.uint32)
                    )
                    frame_u32[wy0 + hy, wx0:wx1] = row_u32[:, None]
        else:
            rpm = self._rpm_norm
            if rpm < 0.008:
                shell_idx = 0
                tier_idx = 0
                sx_px = sy_px = 0
            else:
                shell_idx = min(16, max(1, int(round(rpm * 16.0))))
                tier_idx = min(len(self._ROTOR_RPM_TIERS), max(1, int(rpm * len(self._ROTOR_RPM_TIERS)) + 1))
                sx_px = int(round(self._shake_x * self._r_core))
                sy_px = int(round(self._shake_y * self._r_core))
            sh_i, sh_v = self._core_shells_u32[shell_idx]
            frame_flat[sh_i] = sh_v
            ang_mod = self._rotor_angle % self._rotor_period
            ang_idx = int((ang_mod / self._rotor_period) * self._ROTOR_ANGLES + 0.5) % self._ROTOR_ANGLES
            rx, ry = self._rotor_base_pos
            ac(self._rotor_tiers[tier_idx][ang_idx], (rx + sx_px, ry + sy_px))


class ProMotionCluster(tk.Frame):
    """
    Apple ProMotion 风格自适应高刷三联律动舞台：
    - 自动识别屏幕物理刷新率（60Hz / 120Hz / 130Hz / 144Hz / 240Hz 及跨屏拖动自适应）
    - 独立硬件垂直同步线程锁定 DwmFlush() VBlank，彻底消除软件定时器撕裂与顿挫
    - 动态可变刷新率：重拍爆发跑满原生高刷 -> 轻柔余韵自动 1/2 整数分频 -> 静音暂停 0Hz 零重绘
    - 三盘合一单次零拷贝 Win32 GDI 提交，130Hz 满刷下 CPU 依然稳压在 ~1.3%（远低于 3%）！
    """

    def __init__(self, parent, shared: SharedVibrationParams, stop_event: threading.Event, wake_event: threading.Event, bg_hex: str = BG_CARD):
        self._bg_hex = bg_hex
        self._bg_rgb = _hex_to_rgb(bg_hex)
        self._sw = S(646)
        self._sh = S(226)

        super().__init__(parent, width=self._sw, height=self._sh, bg=bg_hex, bd=0, highlightthickness=0)
        self.pack_propagate(False)

        self.shared = shared
        self.stop_event = stop_event
        self.wake_event = wake_event
        self._buf_lock = threading.Lock()

        self._stage = tk.Frame(self, width=self._sw, height=self._sh, bg=bg_hex, bd=0, highlightthickness=0)
        self._stage.pack(fill="both", expand=True)

        cx_l = self._sw * 0.172
        cx_m = self._sw * 0.500
        cx_r = self._sw * 0.828
        cy_s = self._sh * 0.500
        cy_m = self._sh * 0.500

        self._lbls = []
        self._last_lbl_bright = True

        self.disc_left = _DiscUnit(cx_l, cy_s, 176, 32, PALETTE_BASS_PULSE, GREEN_HI, 1.0, "bass", self._sw, self._sh, self._bg_rgb)
        self.disc_audio = _DiscUnit(cx_m, cy_m, 216, 48, PALETTE_MAIN_SPECTRUM, CYAN_HI, MAX_RMS_DISPLAY, "audio", self._sw, self._sh, self._bg_rgb)
        self.disc_right = _DiscUnit(cx_r, cy_s, 176, 32, PALETTE_HIGH_DETAIL, PURPLE_HI, 1.0, "detail", self._sw, self._sh, self._bg_rgb)
        self.discs = (self.disc_left, self.disc_audio, self.disc_right)

        self._bmi = _BITMAPINFOHEADER(
            biSize=ctypes.sizeof(_BITMAPINFOHEADER),
            biWidth=self._sw,
            biHeight=-self._sh,
            biPlanes=1,
            biBitCount=32,
            biCompression=0,
        )
        self._frame_u32 = np.empty((self._sh, self._sw), dtype=np.uint32)
        self._frame_flat = self._frame_u32.ravel()
        self._frame_ptr = self._frame_u32.ctypes.data_as(ctypes.c_void_p)
        self._frame_pil = Image.frombuffer("RGBA", (self._sw, self._sh), self._frame_u32, "raw", "RGBA", 0, 1)
        self._frame_pil.readonly = 0

        self._bake_unified_stage()

        self.hwnd: int = 0
        self.viewable: bool = True
        self.launch_start: float = time.monotonic()
        self.launch_duration: float = 0.55
        self.measured_fps: float = 0.0
        self.monitor_hz: int = 60
        self._has_frame = True
        np.copyto(self._frame_u32, self._base_idle_u32)
        for d in self.discs:
            d.draw_into(self._frame_u32, self._frame_flat, self._frame_pil)

        self._stage.bind("<Map>", self._on_map_or_expose)
        self._stage.bind("<Expose>", self._on_map_or_expose)
        self._stage.bind("<Configure>", self._on_map_or_expose)
        self._stage.bind("<Unmap>", self._on_unmap)

    def _bake_unified_stage(self):
        w, h = self._sw, self._sh
        # 1. 激活态与静默关闭态全局漫反射底图
        def make_stage_rgba(active: bool) -> Image.Image:
            img = Image.new("RGBA", (w * 2, h * 2), (*self._bg_rgb, 255))
            amb = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
            ad = ImageDraw.Draw(amb, "RGBA")
            for d in self.discs:
                r_amb = d._R * 0.68
                ad.ellipse(
                    ((d.cx - r_amb) * 2, (d.cy - r_amb) * 2, (d.cx + r_amb) * 2, (d.cy + r_amb) * 2),
                    fill=(*d.glow_rgb, 28 if active else 7),
                )
            amb = amb.filter(ImageFilter.GaussianBlur(max(1, int(self.disc_audio._R * 0.24 * 2))))
            img = Image.alpha_composite(img, amb)
            if not active:
                od = ImageDraw.Draw(img, "RGBA")
                for d in self.discs:
                    length = d._max_len * 0.035
                    bhw = d._base_hw * 2
                    for i in range(d.bar_count):
                        ux, uy = d._dirs[i]
                        x0, y0 = (d.cx + d._r_start * ux) * 2, (d.cy + d._r_start * uy) * 2
                        x1, y1 = (d.cx + (d._r_start + length) * ux) * 2, (d.cy + (d._r_start + length) * uy) * 2
                        nx, ny = -uy, ux
                        od.polygon(
                            [(x0 + bhw * nx, y0 + bhw * ny), (x1 + bhw * nx, y1 + bhw * ny),
                             (x1 - bhw * nx, y1 - bhw * ny), (x0 - bhw * nx, y0 - bhw * ny)],
                            fill=(72, 84, 112, 82),
                        )
                    rc = d._r_core
                    od.ellipse(
                        ((d.cx - rc) * 2, (d.cy - rc) * 2, (d.cx + rc) * 2, (d.cy + rc) * 2),
                        fill=(10, 14, 24, 248),
                        outline=(52, 62, 84, 95),
                        width=max(2, int(round(1.3 * UI_SCALE * 2))),
                    )
                    if d.role == "audio":
                        sp = rc * 0.80
                        od.line(((d.cx - sp) * 2, d.cy * 2, (d.cx + sp) * 2, d.cy * 2), fill=(68, 80, 104, 110), width=max(2, int(round(1.4 * UI_SCALE * 2))))
                    else:
                        is_bass = d.role == "bass"
                        nb = 3 if is_bass else 5
                        rh, rt = rc * 0.22 * 2, rc * 0.75 * 2
                        rhw = math.radians(16.0 if is_bass else 10.5)
                        thw = math.radians(36.0 if is_bass else 21.5)
                        tlt = math.radians(7.0 if is_bass else 6.0)
                        for b in range(nb):
                            ba = -math.pi / 2.0 + (2.0 * math.pi * b / nb)
                            poly = d._classic_fan_blade(d.cx * 2, d.cy * 2, ba, rh, rt, rhw, thw, tlt)
                            od.polygon(poly, fill=(64, 74, 96, 135))
                        od.ellipse((d.cx * 2 - rh, d.cy * 2 - rh, d.cx * 2 + rh, d.cy * 2 + rh), fill=(14, 19, 32, 245), outline=(74, 86, 110, 110), width=2)
            return img.resize((w, h), Image.Resampling.LANCZOS)

        active_rgba = make_stage_rgba(True)
        base_rgb_f32 = np.frombuffer(active_rgba.convert("RGB").tobytes(), dtype=np.uint8).reshape((-1, 3)).astype(np.float32)
        idle_flat = _pil_bgra_to_u32(active_rgba).ravel()

        for d in self.discs:
            d.bake_into(base_rgb_f32, idle_flat)

        self._base_idle_u32 = idle_flat.reshape((h, w)).copy()
        self._base_dim_u32 = _pil_bgra_to_u32(make_stage_rgba(False))

    def set_titles_bright(self, bright: bool):
        if bright != self._last_lbl_bright:
            self._last_lbl_bright = bright
            fg = "#8b9bb8" if bright else "#4e5a72"
            for lbl in self._lbls:
                lbl.config(fg=fg)

    def _on_unmap(self, _event=None):
        self.viewable = False

    def _on_map_or_expose(self, _event=None):
        try:
            if self._stage.winfo_ismapped():
                self.hwnd = int(self._stage.winfo_id())
                self.viewable = True
                self.blit_current()
                self.wake_event.set()
        except tk.TclError:
            pass

    def blit_current(self):
        hwnd = self.hwnd
        if not hwnd or not self._has_frame or not _user32_gdi.IsWindow(hwnd):
            return
        with self._buf_lock:
            hdc = _user32_gdi.GetDC(hwnd)
            if hdc:
                try:
                    _gdi32_blit.SetDIBitsToDevice(
                        hdc, 0, 0, self._sw, self._sh,
                        0, 0, 0, self._sh,
                        self._frame_ptr, ctypes.byref(self._bmi), 0,
                    )
                finally:
                    _user32_gdi.ReleaseDC(hwnd, hdc)

    @staticmethod
    def _build_punchy_ring(count: int, band_slice: tuple | list, motor_val: float, beat: float, audio_active: float) -> tuple[list[float], float]:
        half = count // 2
        n_b = max(1, len(band_slice))
        motor_boost = (clamp(motor_val) ** 0.52) if motor_val > 0.005 else 0.0
        bars_half = []
        for i in range(half):
            idx = int(round((i / max(1, half - 1)) * (n_b - 1)))
            raw_b = band_slice[idx] if band_slice else 0.0
            val = raw_b * audio_active * (0.78 + 0.55 * beat)
            if motor_boost > 0.02:
                ripple = 0.70 + 0.30 * abs(math.sin(i * 0.95 + motor_val * 6.0))
                val = max(val, motor_boost * ripple)
            bars_half.append(clamp(val))
        bars = bars_half + bars_half[::-1]
        peak_p = clamp(max(max(bars_half) if bars_half else 0.0, beat * 0.95, motor_boost))
        return bars, peak_p

    def run_promotion_loop(self):
        """独立 ProMotion 垂直同步渲染线程（DwmFlush 硬件级逐帧对齐 + 0Hz~144Hz 动态变频）。"""
        last_hz_check = 0.0
        mon_hz = 60
        fast_stride = 1
        dwm_ok = True
        short_flushes = 0
        was_dimmed = None
        last_t = time.perf_counter()
        fps_frames = 0
        fps_t0 = last_t

        while not self.stop_event.is_set():
            if not self.viewable or not self.hwnd:
                self.wake_event.wait(0.10)
                self.wake_event.clear()
                last_t = time.perf_counter()
                continue

            now_mono = time.monotonic()
            with self.shared.lock:
                enabled = self.shared.enabled
                vis_on = self.shared.visualizer_enabled
                st = self.shared.stats
                total_rms = st.total_rms
                beat = clamp(st.beat_pulse)
                l_out = st.left_output
                r_out = st.right_output
                spec = st.spectrum

            # 当用户关闭「丝滑律动」开关或关闭总震动时：仅绘制 1 次静默暗光态，随后进入 0% CPU 休眠！
            show_active = enabled and vis_on
            if not show_active:
                if was_dimmed is not True:
                    for d in self.discs:
                        d._bars = [0.0] * d.bar_count
                        d._peaks = [0.0] * d.bar_count
                        d._peak_vels = [0.0] * d.bar_count
                        d._pump = 0.0
                        d._rpm_norm = 0.0
                        d._disp_center_val = 0.0
                    with self._buf_lock:
                        np.copyto(self._frame_u32, self._base_dim_u32)
                    self.blit_current()
                    was_dimmed = True
                self.measured_fps = 0.0
                self.wake_event.wait(0.08)
                self.wake_event.clear()
                last_t = time.perf_counter()
                continue

            # 检测是否完全静止（静音暂停且余韵已落定）：ProMotion 自动降至 0Hz 零重绘，有声瞬间毫秒级满刷唤醒！
            elapsed_launch = now_mono - self.launch_start
            launch_prog = (elapsed_launch / self.launch_duration) if (0.0 <= elapsed_launch < self.launch_duration) else None
            audio_active = clamp((total_rms - 25.0) / 110.0)
            has_signal = (launch_prog is not None) or (audio_active > 0.005) or (l_out > 0.002) or (r_out > 0.002)

            if (not has_signal) and (was_dimmed is False) and all(d.is_settled for d in self.discs):
                self.measured_fps = 0.0
                self.wake_event.wait(0.05)
                self.wake_event.clear()
                last_t = time.perf_counter()
                continue

            was_dimmed = False

            # 每 2 秒自动探测窗口当前所在显示器的物理刷新率（多屏不同赫兹无缝自适应）
            if now_mono - last_hz_check >= 2.0:
                last_hz_check = now_mono
                mon_hz = get_window_refresh_hz(self.hwnd)
                self.monitor_hz = mon_hz
                # <=144Hz 屏幕直接 1:1 满刷同步（如 60/120/130/144Hz）；超高刷电竞屏(240/360Hz)自动整除至 ~120Hz 控温省电
                fast_stride = 1 if mon_hz <= 144 else max(2, int(round(mon_hz / 120.0)))

            # ProMotion 动态分频：爆发弹跳与高速旋转跑满原生帧率，轻柔余韵切至 1/2 整数分频（零顿挫且再省一半算力）
            peak_motion = max(d._motion_energy for d in self.discs)
            need_full_hz = (launch_prog is not None) or (beat > 0.12) or (peak_motion > 0.14) or (l_out > 0.08) or (r_out > 0.08)
            stride = fast_stride if need_full_hz else (fast_stride * 2 if mon_hz >= 90 else fast_stride)

            # 硬件 VBlank 垂直同步等待（0% CPU 内核阻塞，帧间隔精确对齐显示器每次扫描刷新）
            if dwm_ok:
                for _ in range(stride):
                    t_f0 = time.perf_counter()
                    hr = _dwmapi.DwmFlush()
                    if hr != 0 or (time.perf_counter() - t_f0) < 0.0008:
                        short_flushes += 1
                        if short_flushes > 6:
                            dwm_ok = False
                            break
                    else:
                        short_flushes = 0
            if not dwm_ok:
                time.sleep(stride / max(30, mon_hz))

            t_now = time.perf_counter()
            dt = clamp(t_now - last_t, 0.003, 0.050)
            last_t = t_now

            if len(spec) < 24:
                spec = (0.0,) * 24
            left_bars, left_pump = self._build_punchy_ring(32, spec[:8], l_out, beat * 1.15, audio_active)
            spec_list = list(spec)
            audio_bars = [clamp(v * audio_active * (0.92 + 0.35 * beat)) for v in (spec_list + spec_list[::-1])]
            audio_pump = clamp(max(beat * 0.95, (total_rms / MAX_RMS_DISPLAY) ** 0.7))
            right_bars, right_pump = self._build_punchy_ring(32, spec[8:], r_out, beat * 0.65, audio_active)

            self.disc_left.step_physics(dt, left_bars, l_out, left_pump, True, launch_prog)
            self.disc_audio.step_physics(dt, audio_bars, total_rms, audio_pump, True, launch_prog)
            self.disc_right.step_physics(dt, right_bars, r_out, right_pump, True, launch_prog)

            with self._buf_lock:
                np.copyto(self._frame_u32, self._base_idle_u32)
                for d in self.discs:
                    d.draw_into(self._frame_u32, self._frame_flat, self._frame_pil)

            self.blit_current()

            fps_frames += 1
            if t_now - fps_t0 >= 0.5:
                self.measured_fps = fps_frames / (t_now - fps_t0)
                fps_frames = 0
                fps_t0 = t_now


class AppleSwitch(tk.Label):
    """4x LANCZOS 超采样抗锯齿苹果风胶囊拨动开关（尺寸紧凑统一、边缘丝滑无锯齿、带平滑推钮过渡）。"""

    def __init__(self, parent, command, accent_hex: str = GREEN, bg_hex: str = None):
        self._bg_hex = bg_hex or parent.cget("bg")
        self._bg_rgb = _hex_to_rgb(self._bg_hex)
        self._accent_rgb = _hex_to_rgb(accent_hex)
        self._sw_px = S(40)
        self._sh_px = S(22)
        self._enabled = True
        self._hover = False
        self._pos = 1.0
        self._anim_job = None
        self._command = command
        self._cache: dict[tuple[int, bool], ImageTk.PhotoImage] = {}

        super().__init__(parent, bg=self._bg_hex, bd=0, highlightthickness=0, cursor="hand2")
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", lambda _e: self._command())
        self._update_image()

    def _get_frame(self, step: int, hover: bool) -> ImageTk.PhotoImage:
        key = (step, hover)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        ss = 4
        W, H = self._sw_px * ss, self._sh_px * ss
        t = step / 6.0
        img = Image.new("RGBA", (W, H), (*self._bg_rgb, 255))
        d = ImageDraw.Draw(img, "RGBA")

        off_col = (34, 43, 64) if hover else (25, 32, 48)
        on_col = _lerp_rgb(self._accent_rgb, (255, 255, 255), 0.10 if hover else 0.0)
        track_rgb = _lerp_rgb(off_col, on_col, t)
        border_rgb = _lerp_rgb((52, 65, 96) if hover else (38, 48, 72), _lerp_rgb(on_col, (255, 255, 255), 0.25), t)

        pad = int(round(1.0 * ss))
        rad = (H - pad * 2) // 2
        d.rounded_rectangle((pad, pad, W - pad - 1, H - pad - 1), radius=rad, fill=(*track_rgb, 255), outline=(*border_rgb, 220), width=max(1, int(round(1.0 * ss))))

        # 白色立体圆形滑块 + 柔和微投影
        margin = int(round(3.0 * ss))
        kr = (H - margin * 2) / 2.0
        x_left = margin + kr
        x_right = W - margin - kr
        kcx = x_left + (x_right - x_left) * t
        kcy = H / 2.0

        sh_off = max(1, int(round(0.9 * ss)))
        d.ellipse((kcx - kr, kcy - kr + sh_off, kcx + kr, kcy + kr + sh_off), fill=(6, 10, 18, 95))
        knob_rgb = _lerp_rgb((210, 218, 234), (255, 255, 255), 0.35 + 0.65 * t)
        d.ellipse((kcx - kr, kcy - kr, kcx + kr, kcy + kr), fill=(*knob_rgb, 255))

        photo = ImageTk.PhotoImage(img.resize((self._sw_px, self._sh_px), Image.Resampling.LANCZOS))
        self._cache[key] = photo
        return photo

    def _set_hover(self, val: bool):
        if self._hover != val:
            self._hover = val
            self._update_image()

    def set_state(self, enabled: bool):
        enabled = bool(enabled)
        if self._enabled == enabled:
            return
        self._enabled = enabled
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None
        self._animate_step()

    def _animate_step(self):
        target = 1.0 if self._enabled else 0.0
        diff = target - self._pos
        if abs(diff) <= 0.18:
            self._pos = target
            self._anim_job = None
            self._update_image()
            return
        self._pos += 0.34 if diff > 0 else -0.34
        self._pos = clamp(self._pos)
        self._update_image()
        self._anim_job = self.after(14, self._animate_step)

    def _update_image(self):
        step = int(round(clamp(self._pos) * 6.0))
        self._cur_photo = self._get_frame(step, self._hover)
        self.configure(image=self._cur_photo)


class StatusPill(tk.Canvas):
    """4x LANCZOS 超采样圆角硬件状态胶囊徽章（支持点击切换与丝滑抗锯齿边缘）。"""

    def __init__(self, parent, text="", dot_color=GREEN, command=None):
        self._parent_bg = parent.cget("bg")
        self._parent_rgb = _hex_to_rgb(self._parent_bg)
        super().__init__(
            parent,
            height=S(26),
            width=S(110),
            bg=self._parent_bg,
            bd=0,
            highlightthickness=0,
            cursor="hand2" if command else "",
        )
        self._text = text
        self._dot = dot_color
        self._command = command
        self._hover = False
        self._font = tkfont.Font(font=F_TINY)
        self._bg_photo = None

        if command:
            self.bind("<Enter>", lambda _e: self._set_hover(True))
            self.bind("<Leave>", lambda _e: self._set_hover(False))
            self.bind("<ButtonRelease-1>", lambda _e: command())
        self._redraw()

    def _set_hover(self, state: bool):
        if self._hover != state:
            self._hover = state
            self._redraw()

    def update_pill(self, text: str, dot_color: str):
        if text == self._text and dot_color == self._dot:
            return
        self._text = text
        self._dot = dot_color
        self._redraw()

    def _redraw(self):
        self.delete("all")
        text_w = self._font.measure(self._text)
        w = max(S(68), text_w + S(28))
        h = S(26)
        if abs(self.winfo_reqwidth() - w) > 1:
            self.configure(width=w)

        ss = 3
        W, H = w * ss, h * ss
        img = Image.new("RGBA", (W, H), (*self._parent_rgb, 255))
        d = ImageDraw.Draw(img, "RGBA")
        fill_rgb = _hex_to_rgb(BG_CARD_HI if self._hover else BG_INSET)
        stroke_rgb = _hex_to_rgb(BORDER_HI if self._hover else BORDER)
        pad = ss
        d.rounded_rectangle((pad, pad, W - pad - 1, H - pad - 1), radius=(H - pad * 2) // 2, fill=(*fill_rgb, 255), outline=(*stroke_rgb, 235), width=max(1, ss))

        dot_rgb = _hex_to_rgb(self._dot)
        dcx, dcy = S(12) * ss, H / 2.0
        rh = S(5.0) * ss
        rc = S(2.9) * ss
        d.ellipse((dcx - rh, dcy - rh, dcx + rh, dcy + rh), fill=(*dot_rgb, 58))
        d.ellipse((dcx - rc, dcy - rc, dcx + rc, dcy + rc), fill=(*dot_rgb, 255))

        self._bg_photo = ImageTk.PhotoImage(img.resize((w, h), Image.Resampling.LANCZOS))
        self.create_image(0, 0, image=self._bg_photo, anchor="nw")
        self.create_text(S(21), h / 2, text=self._text, anchor="w", fill=TEXT_MAIN if self._hover else TEXT_SUB, font=F_TINY)


class ModeSelector(tk.Frame):
    """5 选 1 场景模式卡片组。"""

    def __init__(self, parent, items, on_select):
        super().__init__(parent, bg=parent.cget("bg"))
        self._items = items
        self._on_select = on_select
        self._selected = items[0][0]
        self._cards = {}

        for idx, (name, desc, accent) in enumerate(items):
            self.columnconfigure(idx, weight=1, uniform="mode_col")
            card = _ModeOption(self, name, desc, accent, lambda n=name: self._handle_click(n))
            card.grid(row=0, column=idx, sticky="ew", padx=(0 if idx == 0 else S(7), 0))
            self._cards[name] = card
        self._sync()

    def _handle_click(self, name: str):
        self._selected = name
        self._sync()
        self._on_select(name)

    def set_selected(self, name: str):
        if name != self._selected:
            self._selected = name
            self._sync()

    def _sync(self):
        for name, card in self._cards.items():
            card.set_active(name == self._selected)


class _ModeOption(tk.Canvas):
    def __init__(self, parent, title: str, subtitle: str, accent: str, command):
        super().__init__(
            parent,
            height=S(58),
            bg=parent.cget("bg"),
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self.title = title
        self.subtitle = subtitle
        self.accent = accent
        self.command = command
        self.active = False
        self.hover = False

        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", lambda _e: self.command())

    def set_active(self, active: bool):
        if self.active != active:
            self.active = active
            self._redraw()

    def _set_hover(self, hover: bool):
        self.hover = hover
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w = max(self.winfo_width(), S(80))
        h = max(self.winfo_height(), S(58))
        if self.active:
            fill = blend_hex(BG_INSET, self.accent, 0.16 if self.hover else 0.11)
            stroke = blend_hex(self.accent, "#ffffff", 0.22) if self.hover else self.accent
            title_fg = TEXT_MAIN
            sub_fg = blend_hex(TEXT_SUB, self.accent, 0.35)
            bar_col = self.accent
        else:
            fill = BG_CARD_HI if self.hover else BG_INSET
            stroke = BORDER_HI if self.hover else BORDER
            title_fg = TEXT_MAIN if self.hover else TEXT_SUB
            sub_fg = TEXT_MUTED
            bar_col = blend_hex(BG_INSET, self.accent, 0.35) if self.hover else "#1e263b"

        draw_rounded_box(self, 1, 1, w - 2, h - 2, S(12), fill=fill, outline=stroke, width=1)
        bar_w = w * (0.42 if self.active else 0.22)
        bx1 = (w - bar_w) / 2
        draw_rounded_box(self, bx1, S(6), bx1 + bar_w, S(9), S(1.5), fill=bar_col, outline="")

        self.create_text(w / 2, S(25), text=self.title, fill=title_fg, font=F_SMALL_B)
        self.create_text(w / 2, S(43), text=self.subtitle, fill=sub_fg, font=F_TINY)


class ModernSlider(tk.Frame):
    """丝滑精致调音推子滑块。"""

    def __init__(
        self,
        parent,
        label: str,
        hint: str,
        low: float,
        high: float,
        initial: float,
        accent: str = BLUE,
        display_fn=None,
        command=None,
    ):
        bg = parent.cget("bg")
        super().__init__(parent, bg=bg)
        self.low = low
        self.high = high
        self.value = clamp(initial, low, high)
        self.accent = accent
        self.display_fn = display_fn or (lambda v: f"{int(round(v * 100))}%")
        self.command = command
        self._dragging = False
        self._hover = False

        top = tk.Frame(self, bg=bg)
        top.pack(fill="x")
        tk.Label(top, text=label, bg=bg, fg=TEXT_MAIN, font=F_BODY_B).pack(side="left")
        if hint:
            tk.Label(top, text=hint, bg=bg, fg=TEXT_MUTED, font=F_TINY).pack(side="left", padx=(S(8), 0))
        self.val_label = tk.Label(top, text=self.display_fn(self.value), bg=bg, fg=TEXT_MAIN, font=F_NUM_MD)
        self.val_label.pack(side="right")

        self.track = tk.Canvas(
            self,
            height=S(23),
            bg=bg,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self.track.pack(fill="x", pady=(S(1), 0))
        self.track.bind("<Configure>", lambda _e: self._redraw())
        self.track.bind("<Enter>", lambda _e: self._set_hover(True))
        self.track.bind("<Leave>", lambda _e: self._set_hover(False))
        self.track.bind("<ButtonPress-1>", self._on_press)
        self.track.bind("<B1-Motion>", self._on_drag)
        self.track.bind("<ButtonRelease-1>", self._on_release)
        self.track.bind("<MouseWheel>", self._on_wheel)

    def _set_hover(self, hover: bool):
        self._hover = hover
        self._redraw()

    def _x_to_val(self, x: float) -> float:
        pad = S(10)
        w = max(1, self.track.winfo_width() - pad * 2)
        ratio = clamp((x - pad) / w)
        return self.low + ratio * (self.high - self.low)

    def _on_press(self, event):
        self._dragging = True
        self._update_from_user(self._x_to_val(event.x))

    def _on_drag(self, event):
        if self._dragging:
            self._update_from_user(self._x_to_val(event.x))

    def _on_release(self, _event):
        self._dragging = False
        self._redraw()

    def _on_wheel(self, event):
        step = (self.high - self.low) * 0.02 * (1 if event.delta > 0 else -1)
        self._update_from_user(self.value + step)

    def _update_from_user(self, new_val: float):
        self.value = clamp(new_val, self.low, self.high)
        self.val_label.config(text=self.display_fn(self.value))
        self._redraw()
        if self.command:
            self.command(self.value)

    def set_value(self, val: float):
        self.value = clamp(val, self.low, self.high)
        self.val_label.config(text=self.display_fn(self.value))
        self._redraw()

    def _get_knob_photo(self, active: bool) -> ImageTk.PhotoImage:
        if not hasattr(self, "_knob_cache"):
            self._knob_cache = {}
        if active in self._knob_cache:
            return self._knob_cache[active]
        sz = S(22)
        if sz % 2 == 1:
            sz += 1
        ss = 4
        N = sz * ss
        img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
        d = ImageDraw.Draw(img, "RGBA")
        c = N / 2.0
        acc = _hex_to_rgb(self.accent)
        if active:
            rh = S(9.8) * ss
            d.ellipse((c - rh, c - rh, c + rh, c + rh), fill=(*acc, 68))
        r = S(6.8 if active else 6.0) * ss
        d.ellipse((c - r, c - r, c + r, c + r), fill=(248, 250, 252, 255), outline=(*acc, 255), width=max(2, int(round(S(2.0) * ss))))
        rc = S(2.1) * ss
        d.ellipse((c - rc, c - rc, c + rc, c + rc), fill=(*_hex_to_rgb(BG_INSET), 255))
        ph = ImageTk.PhotoImage(img.resize((sz, sz), Image.Resampling.LANCZOS))
        self._knob_cache[active] = ph
        return ph

    def _redraw(self):
        self.track.delete("all")
        w = max(self.track.winfo_width(), S(100))
        pad = S(10)
        cy = S(11)
        bar_h = S(5)
        span = w - pad * 2

        draw_rounded_box(
            self.track,
            pad,
            cy - bar_h / 2,
            w - pad,
            cy + bar_h / 2,
            bar_h / 2,
            fill=BG_INSET,
            outline=BORDER,
            width=1,
        )

        ratio = 0.0 if self.high <= self.low else (self.value - self.low) / (self.high - self.low)
        fill_x = pad + ratio * span
        if fill_x > pad + 2:
            draw_rounded_box(
                self.track,
                pad,
                cy - bar_h / 2,
                fill_x,
                cy + bar_h / 2,
                bar_h / 2,
                fill=self.accent,
                outline="",
            )

        knob_img = self._get_knob_photo(self._hover or self._dragging)
        self.track.create_image(int(round(fill_x)), cy, image=knob_img, anchor="center")


class ActionButton(tk.Canvas):
    """圆角柔和微光操作按钮。"""

    def __init__(self, parent, text: str, command, width=92, height=32, primary=False):
        super().__init__(
            parent,
            width=S(width),
            height=S(height),
            bg=parent.cget("bg"),
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self._text = text
        self._command = command
        self._btn_w = S(width)
        self._btn_h = S(height)
        self._primary = primary
        self._hover = False
        self._enabled = True

        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._on_click)
        self._redraw()

    def _set_hover(self, state: bool):
        self._hover = state
        self._redraw()

    def _on_click(self, _event):
        if self._enabled and self._command:
            self._command()

    def configure_btn(self, text: str = None, enabled: bool = None):
        changed = False
        if text is not None and text != self._text:
            self._text = text
            changed = True
        if enabled is not None and enabled != self._enabled:
            self._enabled = enabled
            self.configure(cursor="hand2" if enabled else "")
            changed = True
        if changed:
            self._redraw()

    def _redraw(self):
        self.delete("all")
        w, h = self._btn_w, self._btn_h
        if not self._enabled:
            fill, stroke, fg = BG_INSET, BORDER, TEXT_MUTED
        elif self._primary:
            fill = blend_hex(BLUE_BG, CYAN, 0.34 if self._hover else 0.20)
            stroke = CYAN_HI if self._hover else CYAN
            fg = TEXT_MAIN
        else:
            fill = BG_BTN_HOVER if self._hover else BG_BTN
            stroke = BORDER_HI if self._hover else BORDER
            fg = TEXT_MAIN

        draw_rounded_box(self, 1, 1, w - 2, h - 2, S(10), fill=fill, outline=stroke, width=1)
        if self._enabled:
            self.create_line(S(10), 2, w - S(10), 2, fill=blend_hex(stroke, "#ffffff", 0.20), width=1)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=F_SMALL_B)


class PadSlotSelector(tk.Canvas):
    """精致手柄槽位 [1][2][3][4] 分段选择器。"""

    def __init__(self, parent, on_change):
        self._seg_w = S(30)
        self._box_h = S(30)
        total_w = self._seg_w * 4 + S(6)
        super().__init__(
            parent,
            width=total_w,
            height=self._box_h,
            bg=parent.cget("bg"),
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self._index = 0
        self._hover_idx = -1
        self._on_change = on_change

        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonRelease-1>", self._on_click)
        self._redraw()

    def _hit(self, x: float) -> int:
        idx = int((x - S(3)) // self._seg_w)
        return max(0, min(3, idx))

    def _on_motion(self, event):
        idx = self._hit(event.x)
        if idx != self._hover_idx:
            self._hover_idx = idx
            self._redraw()

    def _on_leave(self, _event):
        self._hover_idx = -1
        self._redraw()

    def _on_click(self, event):
        idx = self._hit(event.x)
        if idx != self._index:
            self._index = idx
            self._redraw()
            self._on_change(idx)

    def set_index(self, idx: int):
        idx = max(0, min(3, int(idx)))
        if idx != self._index:
            self._index = idx
            self._redraw()

    def _redraw(self):
        self.delete("all")
        w = self._seg_w * 4 + S(6)
        h = self._box_h
        draw_rounded_box(self, 1, 1, w - 2, h - 2, S(10), fill=BG_INSET, outline=BORDER, width=1)
        for i in range(4):
            x1 = S(3) + i * self._seg_w
            x2 = x1 + self._seg_w
            if i == self._index:
                draw_rounded_box(self, x1 + 1, S(3), x2 - 1, h - S(3), S(7), fill=blend_hex(BG_BTN_HOVER, CYAN, 0.16), outline=CYAN_HI, width=1)
                fg = TEXT_MAIN
            elif i == self._hover_idx:
                draw_rounded_box(self, x1 + 1, S(3), x2 - 1, h - S(3), S(7), fill=BG_CARD_HI, outline="")
                fg = TEXT_MAIN
            else:
                fg = TEXT_MUTED
            self.create_text((x1 + x2) / 2, h / 2, text=str(i + 1), fill=fg, font=F_SMALL_B)


# ---------------------------------------------------------------------------
# 二级高级设置与运行日志弹窗
# ---------------------------------------------------------------------------

class AdvancedDialog(tk.Toplevel):
    """收纳专业频段参数、手动开关与运行日志的独立窗口。"""

    def __init__(self, app: "VibrationApp"):
        super().__init__(app.master)
        self.app = app
        self.withdraw()
        self.title("高级调音与运行日志")
        self.configure(bg=BG_WINDOW)
        self.transient(app.master)

        wrap = tk.Frame(self, bg=BG_WINDOW)
        wrap.pack(fill="both", expand=True, padx=S(16), pady=S(14))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(2, weight=1)

        sw_card = CardFrame(wrap, pad_x=14, pad_y=12)
        sw_card.grid(row=0, column=0, sticky="ew", pady=(0, S(10)))
        sw_body = sw_card.inner

        hdr = tk.Frame(sw_body, bg=BG_CARD)
        hdr.pack(fill="x", pady=(0, S(8)))
        tk.Label(hdr, text="手动模式选项", bg=BG_CARD, fg=TEXT_MAIN, font=F_H2).pack(side="left")
        self.auto_hint = tk.Label(hdr, text="", bg=BG_CARD, fg=AMBER, font=F_TINY)
        self.auto_hint.pack(side="right")

        chk_row = tk.Frame(sw_body, bg=BG_CARD)
        chk_row.pack(fill="x")
        self.chk_dual = tk.Checkbutton(
            chk_row,
            text="双马达分工（左低频 / 右人声）",
            variable=app.dual_var,
            command=app.on_advanced_toggle,
            bg=BG_CARD,
            fg=TEXT_SUB,
            selectcolor=BG_INSET,
            activebackground=BG_CARD,
            activeforeground=TEXT_MAIN,
            font=F_SMALL,
        )
        self.chk_dual.pack(side="left", padx=(0, S(12)))

        self.chk_vocal = tk.Checkbutton(
            chk_row,
            text="仅聚焦人声频段",
            variable=app.vocal_var,
            command=app.on_advanced_toggle,
            bg=BG_CARD,
            fg=TEXT_SUB,
            selectcolor=BG_INSET,
            activebackground=BG_CARD,
            activeforeground=TEXT_MAIN,
            font=F_SMALL,
        )
        self.chk_vocal.pack(side="left", padx=(0, S(12)))

        self.chk_adapt = tk.Checkbutton(
            chk_row,
            text="自适应底噪门限",
            variable=app.adapt_var,
            command=app.on_advanced_toggle,
            bg=BG_CARD,
            fg=TEXT_SUB,
            selectcolor=BG_INSET,
            activebackground=BG_CARD,
            activeforeground=TEXT_MAIN,
            font=F_SMALL,
        )
        self.chk_adapt.pack(side="left")

        param_card = CardFrame(wrap, pad_x=14, pad_y=12)
        param_card.grid(row=1, column=0, sticky="ew", pady=(0, S(10)))
        p_body = param_card.inner
        p_body.columnconfigure((0, 1), weight=1, uniform="adv_col")

        p_hdr = tk.Frame(p_body, bg=BG_CARD)
        p_hdr.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, S(6)))
        tk.Label(p_hdr, text="响应曲线与频段划分", bg=BG_CARD, fg=TEXT_MAIN, font=F_H2).pack(side="left")
        ActionButton(p_hdr, "恢复默认参数", app.reset_advanced_params, width=106, height=26).pack(side="right")

        specs = [
            ("触发门限", "vibration_threshold", 40.0, 3000.0, lambda v: f"{int(round(v))}", True),
            ("映射上限", "map_in_max", 1000.0, 10000.0, lambda v: f"{int(round(v))}", True),
            ("启动速度", "attack_rate", 0.05, 0.95, lambda v: f"{v:.2f}", False),
            ("释放速度", "release_rate", 0.02, 0.65, lambda v: f"{v:.2f}", False),
            ("曲线伽马", "gamma_correction", 0.50, 2.40, lambda v: f"{v:.2f}", False),
            ("马达死区", "dead_zone", 0.0, 0.15, lambda v: f"{int(round(v * 100))}%", False),
            ("低频上限", "bass_high", 80.0, 1200.0, lambda v: f"{int(round(v))} Hz", True),
            ("人声下限", "vocal_low", 150.0, 2000.0, lambda v: f"{int(round(v))} Hz", True),
            ("人声上限", "vocal_high", 1500.0, 8500.0, lambda v: f"{int(round(v))} Hz", True),
        ]
        self.sliders = {}
        snap = app.shared.snapshot()
        for idx, (label, attr, lo, hi, fmt_fn, is_int) in enumerate(specs):
            r = 1 + idx // 2
            c = idx % 2
            s = ModernSlider(
                p_body,
                label,
                "",
                lo,
                hi,
                float(snap[attr]),
                accent=CYAN,
                display_fn=fmt_fn,
                command=lambda val, a=attr, ii=is_int: app.on_advanced_slider(a, val, ii),
            )
            s.grid(row=r, column=c, sticky="ew", padx=(0, S(10)) if c == 0 else (S(10), 0), pady=S(2))
            self.sliders[attr] = s

        log_card = CardFrame(wrap, pad_x=14, pad_y=12)
        log_card.grid(row=2, column=0, sticky="nsew")
        l_body = log_card.inner
        tk.Label(l_body, text="运行日志", bg=BG_CARD, fg=TEXT_MAIN, font=F_H2).pack(anchor="w", pady=(0, S(6)))
        self.log_box = tk.Text(
            l_body,
            height=7,
            bg=BG_INSET,
            fg=TEXT_SUB,
            insertbackground=TEXT_MAIN,
            relief="flat",
            font=F_MONO_SM,
            padx=S(10),
            pady=S(8),
            wrap="word",
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        self.log_box.pack(fill="both", expand=True)
        self.refresh_logs(app.log_history)
        self.sync_from_snapshot(snap)

        for c in (sw_card, param_card, log_card):
            c.sync_height()
        self.update_idletasks()
        w = S(590)
        h = max(S(570), self.winfo_reqheight())
        self.minsize(S(530), S(510))
        px = app.master.winfo_rootx() + (app.master.winfo_width() - w) // 2
        py = app.master.winfo_rooty() + S(18)
        self.geometry(f"{w}x{h}+{max(0, px)}+{max(0, py)}")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.deiconify()
        apply_dark_titlebar(self)

    def sync_from_snapshot(self, snap: dict):
        for attr, slider in self.sliders.items():
            slider.set_value(float(snap[attr]))
        if snap["auto_profile"]:
            self.auto_hint.config(text="当前为「智能自动」，选择手动模式后以下选项生效")
        else:
            self.auto_hint.config(text="手动模式生效中")

    def refresh_logs(self, history: list[str]):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", tk.END)
        if history:
            self.log_box.insert(tk.END, "\n".join(history) + "\n")
            self.log_box.see(tk.END)
        self.log_box.configure(state="disabled")

    def append_line(self, line: str):
        self.log_box.configure(state="normal")
        self.log_box.insert(tk.END, line + "\n")
        self.log_box.see(tk.END)
        self.log_box.configure(state="disabled")


# ---------------------------------------------------------------------------
# 主应用程序窗口
# ---------------------------------------------------------------------------

class VibrationApp:
    def __init__(self, master: tk.Tk):
        self.master = master
        self.shared = SharedVibrationParams()
        self.log_queue: queue.Queue = queue.Queue()
        self.log_history: list[str] = []
        self.stop_event = threading.Event()
        self.vis_wake_event = threading.Event()
        self.audio_thread = None
        self.controller_thread = None
        self.vis_thread = None
        self.test_thread = None
        self.adv_dialog: AdvancedDialog | None = None
        self.closing = False
        self._save_timer = None
        self._seen_hotkey_seq = 0

        # 启动 / 开启震动时的弹性爆发波动画
        self._launch_start = time.monotonic()
        self._launch_duration = 0.55
        self._last_enabled_state = None
        self._last_hotkey_hint_state = None

        self.dual_var = tk.BooleanVar(value=False)
        self.vocal_var = tk.BooleanVar(value=True)
        self.adapt_var = tk.BooleanVar(value=True)

        self.load_settings()
        self._build_ui()
        self.sync_ui_from_shared()
        self._bind_shortcuts()
        self.start_threads()
        self.log_queue.put("就绪: 按手柄 LB + RB 两个键可随时开启或关闭震动。")
        self.update_gui()

    def trigger_launch_burst(self, duration: float = 0.45):
        now = time.monotonic()
        self._launch_start = now
        self._launch_duration = duration
        if hasattr(self, "disc_cluster"):
            self.disc_cluster.launch_start = now
            self.disc_cluster.launch_duration = duration
        self.vis_wake_event.set()

    # ----- 设置读写 -----

    def load_settings(self):
        if not CONFIG_FILE.exists():
            return
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            self.shared.apply(sanitize_settings(raw))
        except Exception as exc:
            self.log_queue.put(f"读取配置文件失败，已使用默认设置: {exc}")

    def save_settings(self):
        snap = self.shared.snapshot()
        payload = {k: snap[k] for k in PERSISTED_KEYS}
        try:
            CONFIG_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            self.log_queue.put(f"保存配置失败: {exc}")

    def schedule_save(self):
        if self.closing:
            return
        if self._save_timer is not None:
            try:
                self.master.after_cancel(self._save_timer)
            except Exception:
                pass
        self._save_timer = self.master.after(1200, self._commit_save)

    def _commit_save(self):
        self._save_timer = None
        if not self.closing:
            self.save_settings()

    # ----- 界面构建 -----

    def _build_ui(self):
        root_pad = tk.Frame(self.master, bg=BG_WINDOW)
        root_pad.pack(fill="both", expand=True, padx=S(16), pady=S(12))
        root_pad.columnconfigure(0, weight=1)

        # 1. 极简精密顶栏（左侧实时硬件胶囊，右侧等尺寸对称双拨杆：动效 | 震动，零废话标题）
        hero_card = CardFrame(root_pad, pad_x=14, pad_y=9, radius=14)
        hero_card.grid(row=0, column=0, sticky="ew", pady=(0, S(10)))
        hero = hero_card.inner
        hero.columnconfigure(0, weight=1)

        badges = tk.Frame(hero, bg=BG_CARD)
        badges.grid(row=0, column=0, sticky="w")
        self.pad_pill = StatusPill(badges, "手柄 1 · 检测中", TEXT_MUTED, command=self.cycle_controller)
        self.pad_pill.pack(side="left", padx=(0, S(8)))
        self.audio_pill = StatusPill(badges, "连接音频设备...", CYAN_HI, command=self.request_audio_reconnect)
        self.audio_pill.pack(side="left")

        right_ctrl = tk.Frame(hero, bg=BG_CARD)
        right_ctrl.grid(row=0, column=1, sticky="e")

        tk.Label(right_ctrl, text="动效", bg=BG_CARD, fg=TEXT_SUB, font=F_SMALL).pack(side="left", padx=(0, S(6)))
        self.vis_switch = AppleSwitch(right_ctrl, command=self.toggle_visualizer, accent_hex=CYAN, bg_hex=BG_CARD)
        self.vis_switch.pack(side="left")

        tk.Frame(right_ctrl, width=1, height=S(15), bg="#242e47").pack(side="left", padx=S(13))

        tk.Label(right_ctrl, text="震动", bg=BG_CARD, fg=TEXT_SUB, font=F_SMALL).pack(side="left", padx=(0, S(6)))
        self.power_btn = AppleSwitch(right_ctrl, command=self.toggle_enabled, accent_hex=GREEN, bg_hex=BG_CARD)
        self.power_btn.pack(side="left")

        # 2. 纯净对称三联圆盘舞台（无标题、无遮挡按钮，纯粹视觉中心）
        pulse_card = CardFrame(root_pad, pad_x=14, pad_y=10, radius=16)
        pulse_card.grid(row=1, column=0, sticky="ew", pady=(0, S(10)))
        p_body = pulse_card.inner

        self.disc_cluster = ProMotionCluster(
            p_body,
            shared=self.shared,
            stop_event=self.stop_event,
            wake_event=self.vis_wake_event,
            bg_hex=BG_CARD,
        )
        self.disc_cluster.pack()
        self.disc_left = self.disc_cluster.disc_left
        self.disc_audio = self.disc_cluster.disc_audio
        self.disc_right = self.disc_cluster.disc_right

        # 3. 五档模式选择（无多余标题，直观自明）
        mode_card = CardFrame(root_pad, pad_x=12, pad_y=10, radius=15)
        mode_card.grid(row=2, column=0, sticky="ew", pady=(0, S(10)))
        m_body = mode_card.inner
        self.mode_selector = ModeSelector(m_body, MODE_CARDS, self.on_mode_selected)
        self.mode_selector.pack(fill="x")

        # 4. 三路精密推子（无冗余说明字）
        feel_card = CardFrame(root_pad, pad_x=16, pad_y=11, radius=15)
        feel_card.grid(row=3, column=0, sticky="ew", pady=(0, S(10)))
        f_body = feel_card.inner

        self.feel_sliders = {}
        feel_specs = [
            ("震动强度", "", "intensity", 0.10, 1.0, GREEN_HI),
            ("节奏冲击", "", "rhythm_punch", 0.0, 1.0, AMBER),
            ("细节微颤", "", "texture_amount", 0.0, 1.0, PURPLE_HI),
        ]
        for idx, (label, hint, attr, lo, hi, color) in enumerate(feel_specs):
            slider = ModernSlider(
                f_body,
                label,
                hint,
                lo,
                hi,
                0.6,
                accent=color,
                command=lambda val, a=attr: self.on_feel_change(a, val),
            )
            slider.pack(fill="x", pady=(0 if idx == 0 else S(4), 0))
            self.feel_sliders[attr] = slider

        # 5. 底部工具栏
        footer = tk.Frame(root_pad, bg=BG_WINDOW)
        footer.grid(row=4, column=0, sticky="ew", pady=(S(1), 0))

        self.slot_selector = PadSlotSelector(footer, self.set_controller_index)
        self.slot_selector.pack(side="left")
        self.hotkey_hint = tk.Label(
            footer,
            text="LB+RB 快捷开关",
            bg=BG_WINDOW,
            fg=TEXT_MUTED,
            font=F_TINY,
        )
        self.hotkey_hint.pack(side="left", padx=(S(10), 0))

        ActionButton(footer, "高级设置", self.open_advanced_dialog, width=84, height=28).pack(side="right")
        self.cal_btn = ActionButton(footer, "环境校准", self.start_calibration, width=84, height=28, primary=True)
        self.cal_btn.pack(side="right", padx=(0, S(7)))
        self.test_btn = ActionButton(footer, "测试震动", self.run_test_vibration, width=84, height=28)
        self.test_btn.pack(side="right", padx=(0, S(7)))
        ActionButton(footer, "恢复默认", self.reset_feel, width=80, height=28).pack(side="right", padx=(0, S(7)))

        self._cards = [hero_card, pulse_card, mode_card, feel_card]

    def sync_layout(self):
        for card in self._cards:
            card.sync_height()
        self.master.update_idletasks()

    def _bind_shortcuts(self):
        self.master.bind("<space>", self._on_space_key)

    def _on_space_key(self, event):
        if isinstance(event.widget, tk.Text):
            return
        self.toggle_enabled()

    # ----- 交互逻辑 -----

    def sync_ui_from_shared(self):
        snap = self.shared.snapshot()
        self.power_btn.set_state(snap["enabled"])
        self.vis_switch.set_state(snap["visualizer_enabled"])
        self.disc_cluster.set_titles_bright(snap["enabled"] and snap["visualizer_enabled"])
        self._last_enabled_state = snap["enabled"]
        self.slot_selector.set_index(snap["controller_index"])
        self.dual_var.set(snap["dual_motor_mode"])
        self.vocal_var.set(snap["vocal_only_mode"])
        self.adapt_var.set(snap["adaptive_threshold"])

        for attr, slider in self.feel_sliders.items():
            slider.set_value(snap[attr])

        if snap["auto_profile"]:
            self.mode_selector.set_selected("智能自动")
        else:
            matched = self._match_preset_name(snap)
            if matched:
                self.mode_selector.set_selected(matched)

        if self.adv_dialog and self.adv_dialog.winfo_exists():
            self.adv_dialog.sync_from_snapshot(snap)

    @staticmethod
    def _match_preset_name(snap: dict) -> str | None:
        for name, cfg in PRESETS.items():
            if (
                snap["dual_motor_mode"] == cfg["dual_motor_mode"]
                and snap["vocal_only_mode"] == cfg["vocal_only_mode"]
                and abs(snap["gamma_correction"] - cfg["gamma_correction"]) < 0.03
            ):
                return name
        return None

    def toggle_enabled(self):
        now = time.monotonic()
        with self.shared.lock:
            new_state = not self.shared.enabled
            self.shared.enabled = new_state
            if new_state:
                self.shared.override_left = 0.25
                self.shared.override_right = 0.25
                self.shared.override_until = now + 0.08
            else:
                self.shared.override_until = 0.0
                self.shared.audio_left = 0.0
                self.shared.audio_right = 0.0
        self.power_btn.set_state(new_state)
        self.disc_cluster.set_titles_bright(new_state and self.shared.visualizer_enabled)
        self._last_enabled_state = new_state
        if new_state:
            self.trigger_launch_burst(0.42)
        else:
            self.vis_wake_event.set()
        self.log_queue.put(f"震动输出已{'开启' if new_state else '关闭'}。")
        self.schedule_save()

    def toggle_visualizer(self):
        with self.shared.lock:
            new_vis = not self.shared.visualizer_enabled
            self.shared.visualizer_enabled = new_vis
            en = self.shared.enabled
        self.vis_switch.set_state(new_vis)
        self.disc_cluster.set_titles_bright(en and new_vis)
        if new_vis and en:
            self.trigger_launch_burst(0.38)
        else:
            self.vis_wake_event.set()
        self.log_queue.put(f"实时丝滑律动画面已{'开启' if new_vis else '关闭'}。")
        self.schedule_save()

    def cycle_controller(self):
        snap = self.shared.snapshot()
        self.set_controller_index((snap["controller_index"] + 1) % 4)

    def set_controller_index(self, idx: int):
        idx = max(0, min(3, int(idx)))
        with self.shared.lock:
            old = self.shared.controller_index
            self.shared.controller_index = idx
            self.shared.override_until = 0.0
        self.slot_selector.set_index(idx)
        if old != idx:
            self.log_queue.put(f"已切换到手柄 {idx + 1} 号槽位。")
        self.schedule_save()

    def request_audio_reconnect(self):
        with self.shared.lock:
            self.shared.reconnect_requested = True
        self.log_queue.put("正在重新刷新默认音频输出设备...")

    def on_mode_selected(self, mode_name: str):
        if mode_name == "智能自动":
            auto_cfg = dict(ADVANCED_DEFAULTS)
            auto_cfg.update(FEEL_DEFAULTS)
            auto_cfg["auto_profile"] = True
            self.shared.apply(auto_cfg)
            self.sync_ui_from_shared()
            self.log_queue.put("已启用「智能自动」模式（自动识别音乐 / ASMR / 安静场景）。")
        else:
            preset = dict(PRESETS[mode_name])
            preset["auto_profile"] = False
            self.shared.apply(preset)
            self.sync_ui_from_shared()
            self.log_queue.put(f"已切换至「{mode_name}」预设。")
        if self.adv_dialog and self.adv_dialog.winfo_exists():
            self.adv_dialog.sync_from_snapshot(self.shared.snapshot())
        self.schedule_save()

    def on_feel_change(self, attr: str, value: float):
        with self.shared.lock:
            setattr(self.shared, attr, float(value))
        self.schedule_save()

    def reset_feel(self):
        selected = self.mode_selector._selected
        target = PRESETS[selected] if selected in PRESETS else FEEL_DEFAULTS
        feel_vals = {k: target[k] for k in ("intensity", "rhythm_punch", "texture_amount")}
        self.shared.apply(feel_vals)
        for attr, val in feel_vals.items():
            self.feel_sliders[attr].set_value(val)
        self.log_queue.put("手感滑块已恢复当前模式默认值。")
        self.schedule_save()

    def on_advanced_toggle(self):
        with self.shared.lock:
            self.shared.dual_motor_mode = self.dual_var.get()
            self.shared.vocal_only_mode = self.vocal_var.get()
            self.shared.adaptive_threshold = self.adapt_var.get()
        self.schedule_save()

    def on_advanced_slider(self, attr: str, value: float, is_int: bool):
        val = int(round(value)) if is_int else float(value)
        with self.shared.lock:
            setattr(self.shared, attr, val)
            if attr == "vocal_low" and val >= self.shared.vocal_high:
                self.shared.vocal_high = min(9600, val + 200)
            elif attr == "vocal_high" and val <= self.shared.vocal_low:
                self.shared.vocal_low = max(80, val - 200)
        if attr in ("vocal_low", "vocal_high") and self.adv_dialog and self.adv_dialog.winfo_exists():
            snap = self.shared.snapshot()
            self.adv_dialog.sliders["vocal_low"].set_value(snap["vocal_low"])
            self.adv_dialog.sliders["vocal_high"].set_value(snap["vocal_high"])
        self.schedule_save()

    def reset_advanced_params(self):
        self.shared.apply(ADVANCED_DEFAULTS)
        self.sync_ui_from_shared()
        self.log_queue.put("高级调音参数已恢复默认。")
        self.schedule_save()

    def start_calibration(self):
        with self.shared.lock:
            if self.shared.auto_calibrating:
                return
            self.shared.auto_calibrating = True

    def run_test_vibration(self):
        if self.test_thread and self.test_thread.is_alive():
            return

        def worker():
            snap = self.shared.snapshot()
            if not is_controller_connected(snap["controller_index"]):
                self.log_queue.put(f"测试未执行: 手柄 {snap['controller_index'] + 1} 当前未连接。")
                return
            self.log_queue.put("开始测试震动（左低频 → 右高频 → 双马达齐震）...")
            steps = [(0.55, 0.0, 0.22), (0.0, 0.55, 0.22), (0.85, 0.85, 0.28)]
            for left_m, right_m, dur in steps:
                if self.stop_event.is_set():
                    break
                with self.shared.lock:
                    self.shared.override_left = left_m
                    self.shared.override_right = right_m
                    self.shared.override_until = time.monotonic() + dur + 0.05
                time.sleep(dur)
            with self.shared.lock:
                self.shared.override_left = 0.0
                self.shared.override_right = 0.0
                self.shared.override_until = 0.0
            self.log_queue.put("测试震动完成。")

        self.test_thread = threading.Thread(target=worker, daemon=True)
        self.test_thread.start()

    def open_advanced_dialog(self):
        if self.adv_dialog and self.adv_dialog.winfo_exists():
            self.adv_dialog.lift()
            self.adv_dialog.focus_force()
            return
        self.master.after(10, self._create_advanced_dialog)

    def _create_advanced_dialog(self):
        if not self.closing and not (self.adv_dialog and self.adv_dialog.winfo_exists()):
            self.adv_dialog = AdvancedDialog(self)

    # ----- 后台线程启动与界面刷新（50 FPS 丝滑弹性） -----

    def start_threads(self):
        self.stop_event.clear()
        if not (self.controller_thread and self.controller_thread.is_alive()):
            self.controller_thread = threading.Thread(
                target=controller_io_thread,
                args=(self.shared, self.log_queue, self.stop_event, self.vis_wake_event),
                daemon=True,
            )
            self.controller_thread.start()
        if not (self.audio_thread and self.audio_thread.is_alive()):
            self.audio_thread = threading.Thread(
                target=audio_vibration_thread,
                args=(self.shared, self.log_queue, self.stop_event, self.vis_wake_event),
                daemon=True,
            )
            self.audio_thread.start()
        if not (self.vis_thread and self.vis_thread.is_alive()):
            self.vis_thread = threading.Thread(
                target=self.disc_cluster.run_promotion_loop,
                daemon=True,
            )
            self.vis_thread.start()

    def _drain_logs(self):
        while True:
            try:
                msg = self.log_queue.get_nowait()
            except queue.Empty:
                break
            line = f"[{time.strftime('%H:%M:%S')}] {msg}"
            self.log_history.append(line)
            if len(self.log_history) > LOG_LINE_LIMIT:
                self.log_history = self.log_history[-LOG_LINE_LIMIT:]
            if self.adv_dialog and self.adv_dialog.winfo_exists():
                self.adv_dialog.append_line(line)
            if "校准完成" in msg and self.adv_dialog and self.adv_dialog.winfo_exists():
                self.adv_dialog.sync_from_snapshot(self.shared.snapshot())

    def update_gui(self):
        if self.closing:
            return
        try:
            is_minimized = (self.master.state() == "iconic") or (not self.master.winfo_viewable())
        except tk.TclError:
            return

        if is_minimized:
            self.disc_cluster.viewable = False
            self._drain_logs()
            self.master.after(220, self.update_gui)
            return

        if not self.disc_cluster.viewable:
            self.disc_cluster.viewable = True
            self.vis_wake_event.set()

        try:
            self._refresh_ui()
        except tk.TclError:
            return
        except Exception:
            pass
        # ProMotion 独立垂直同步线程已接管全部高刷动画，Tk 主线程仅以极轻量 10Hz 更新状态胶囊与快捷键提示
        self.master.after(100, self.update_gui)

    def _refresh_ui(self):
        snap = self.shared.snapshot()
        stats = snap["stats"]
        now = time.monotonic()

        enabled = snap["enabled"]
        vis_on = snap["visualizer_enabled"]
        self.power_btn.set_state(enabled)
        self.vis_switch.set_state(vis_on)
        self.disc_cluster.set_titles_bright(enabled and vis_on)
        if self._last_enabled_state is False and enabled is True:
            self.trigger_launch_burst(0.42)
        elif self._last_enabled_state is True and enabled is False:
            self.vis_wake_event.set()
        self._last_enabled_state = enabled

        if snap["hotkey_seq"] != self._seen_hotkey_seq:
            self._seen_hotkey_seq = snap["hotkey_seq"]
            self.schedule_save()

        if snap["hotkey_notice_at"] and (now - snap["hotkey_notice_at"]) < 2.8:
            if snap["hotkey_notice_enabled"]:
                hint_state = ("✓ 已按 LB+RB 开启", GREEN_HI)
            else:
                hint_state = ("⏸ 已按 LB+RB 关闭", AMBER)
        else:
            hint_state = ("LB+RB 快捷开关", TEXT_MUTED)
        if hint_state != self._last_hotkey_hint_state:
            self._last_hotkey_hint_state = hint_state
            self.hotkey_hint.config(text=hint_state[0], fg=hint_state[1])

        pad_num = snap["controller_index"] + 1
        if stats["controller_connected"]:
            self.pad_pill.update_pill(f"手柄 {pad_num}", GREEN_HI)
        else:
            self.pad_pill.update_pill(f"手柄 {pad_num} · 未连接", RED)

        dev_name = stats["device_name"].replace(" [Loopback]", "").strip()
        short_dev = dev_name if len(dev_name) <= 28 else dev_name[:27] + "…"
        dev_ok = "未检测" not in dev_name and "重连" not in dev_name
        self.audio_pill.update_pill(short_dev, CYAN_HI if dev_ok else AMBER)

        if snap["auto_calibrating"]:
            sec = max(1, int(math.ceil(stats["calibrating_left"])))
            self.cal_btn.configure_btn(f"校准 {sec}s…", enabled=False)
        else:
            self.cal_btn.configure_btn("环境校准", enabled=True)

        testing = self.test_thread is not None and self.test_thread.is_alive()
        self.test_btn.configure_btn("测试中…" if testing else "测试震动", enabled=not testing)

        self._drain_logs()

    # ----- 安全退出 -----

    def on_closing(self):
        if self.closing:
            return
        self.closing = True
        if self._save_timer is not None:
            try:
                self.master.after_cancel(self._save_timer)
            except Exception:
                pass
        self.save_settings()
        self.stop_event.set()
        self.vis_wake_event.set()
        with self.shared.lock:
            self.shared.enabled = False
            self.shared.audio_left = 0.0
            self.shared.audio_right = 0.0
            self.shared.override_until = 0.0

        for t in (self.vis_thread, self.controller_thread, self.audio_thread):
            if t and t.is_alive():
                t.join(timeout=0.6)

        try:
            set_vibration(self.shared.snapshot()["controller_index"], 0.0, 0.0)
        except Exception:
            pass
        self.master.destroy()


def build_app_icon() -> tk.PhotoImage:
    size = 32
    img = tk.PhotoImage(width=size, height=size)
    cx, cy = 15.5, 15.5
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            dx, dy = x - cx, y - cy
            dist = math.hypot(dx, dy)
            if dist > 14.5:
                row.append(BG_WINDOW)
            else:
                wave_y = cy + math.sin((x - 4) / 24.0 * math.pi * 2.2) * 5.2
                if 5 <= x <= 26 and abs(y - wave_y) <= 1.7:
                    row.append(GREEN_HI)
                elif 13.0 <= dist <= 14.5:
                    row.append(GREEN)
                else:
                    row.append("#132620")
        rows.append("{" + " ".join(row) + "}")
    img.put(" ".join(rows))
    return img


def main():
    global UI_SCALE
    enable_dpi_awareness()
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("yisal.XboxAudioRumbleStudio.v6")
    except Exception:
        pass

    root = tk.Tk()
    root.withdraw()
    root.title(APP_TITLE)
    root.configure(bg=BG_WINDOW)

    UI_SCALE = detect_scale(root)
    init_fonts(root)

    try:
        if ICON_FILE.exists():
            root.iconbitmap(default=str(ICON_FILE))
        else:
            icon = build_app_icon()
            root.iconphoto(True, icon)
            root._icon_ref = icon
    except Exception:
        pass

    app = VibrationApp(root)
    app.sync_layout()

    width = S(716)
    height = root.winfo_reqheight()
    root.minsize(S(660), height)

    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    pos_x = max(0, (screen_w - width) // 2)
    pos_y = max(0, (screen_h - height) // 2 - S(14))
    root.geometry(f"{width}x{height}+{pos_x}+{pos_y}")

    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.deiconify()
    apply_dark_titlebar(root)
    root.mainloop()


if __name__ == "__main__":
    main()
