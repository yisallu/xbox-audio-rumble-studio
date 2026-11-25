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


APP_TITLE = "Xbox Audio Rumble Studio"
APP_PATH = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
CONFIG_FILE = APP_PATH.with_suffix(".settings.json")

XINPUT_SUCCESS = 0
XINPUT_GAMEPAD_LEFT_SHOULDER = 0x0100
XINPUT_GAMEPAD_RIGHT_SHOULDER = 0x0200
LB_RB_COMBO_MASK = XINPUT_GAMEPAD_LEFT_SHOULDER | XINPUT_GAMEPAD_RIGHT_SHOULDER

LOG_LINE_LIMIT = 200
MAX_RMS_DISPLAY = 5500.0

# ---------------------------------------------------------------------------
# 主题配色（现代沉稳深色卡片风）
# ---------------------------------------------------------------------------

BG_WINDOW = "#0e1117"
BG_CARD = "#161b24"
BG_CARD_HI = "#1d2430"
BG_INSET = "#0b0e14"
BG_BTN = "#1f2735"
BG_BTN_HOVER = "#2a3548"

BORDER = "#242d3d"
BORDER_HI = "#334158"

TEXT_MAIN = "#f1f5f9"
TEXT_SUB = "#94a3b8"
TEXT_MUTED = "#64748b"

GREEN = "#10b981"
GREEN_HI = "#34d399"
GREEN_BG = "#102a22"

BLUE = "#3b82f6"
BLUE_HI = "#60a5fa"
BLUE_BG = "#142642"

AMBER = "#f59e0b"
PURPLE = "#a855f7"
CYAN = "#06b6d4"
RED = "#ef4444"

# ---------------------------------------------------------------------------
# 高 DPI 与字体缩放
# ---------------------------------------------------------------------------

UI_SCALE = 1.0
FONT_UI = "Microsoft YaHei UI"
FONT_MONO = "Consolas"

F_H1 = F_H2 = F_BODY = F_BODY_B = F_SMALL = F_SMALL_B = F_TINY = None
F_MONO = F_MONO_SM = None


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
        max_scale = (screen_h * 0.88) / 670.0
        scale = min(scale, max(0.9, max_scale))
    return round(scale, 2)


def apply_dark_titlebar(widget):
    try:
        hwnd = get_toplevel_hwnd(widget)
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), 4)
        corner_round = ctypes.c_int(2)  # DWMWCP_ROUND on Win11
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner_round), 4)
    except Exception:
        pass


def init_fonts(root):
    global FONT_UI, FONT_MONO
    global F_H1, F_H2, F_BODY, F_BODY_B, F_SMALL, F_SMALL_B, F_TINY, F_MONO, F_MONO_SM
    families = set(tkfont.families(root))
    FONT_UI = next(
        (f for f in ("Microsoft YaHei UI", "Segoe UI Variable Display", "Segoe UI") if f in families),
        "TkDefaultFont",
    )
    FONT_MONO = next((f for f in ("Cascadia Mono", "Consolas") if f in families), "TkFixedFont")

    F_H1 = (FONT_UI, -S(18), "bold")
    F_H2 = (FONT_UI, -S(13), "bold")
    F_BODY = (FONT_UI, -S(12))
    F_BODY_B = (FONT_UI, -S(12), "bold")
    F_SMALL = (FONT_UI, -S(11))
    F_SMALL_B = (FONT_UI, -S(11), "bold")
    F_TINY = (FONT_UI, -S(10))
    F_MONO = (FONT_MONO, -S(12), "bold")
    F_MONO_SM = (FONT_MONO, -S(11))


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
        "vibration_threshold": 260.0,
        "map_in_max": 4200.0,
        "gamma_correction": 1.28,
        "attack_rate": 0.38,
        "release_rate": 0.12,
        "dead_zone": 0.045,
        "vocal_low": 650,
        "vocal_high": 3600,
        "bass_high": 520,
    },
    "低频沉浸": {
        "dual_motor_mode": True,
        "vocal_only_mode": False,
        "adaptive_threshold": True,
        "intensity": 0.86,
        "vibration_threshold": 220.0,
        "map_in_max": 5200.0,
        "gamma_correction": 1.05,
        "attack_rate": 0.46,
        "release_rate": 0.16,
        "dead_zone": 0.04,
        "vocal_low": 600,
        "vocal_high": 4000,
        "bass_high": 640,
    },
    "轻柔细腻": {
        "dual_motor_mode": False,
        "vocal_only_mode": True,
        "adaptive_threshold": True,
        "intensity": 0.52,
        "vibration_threshold": 340.0,
        "map_in_max": 4800.0,
        "gamma_correction": 1.55,
        "attack_rate": 0.24,
        "release_rate": 0.08,
        "dead_zone": 0.06,
        "vocal_low": 700,
        "vocal_high": 3300,
        "bass_high": 480,
    },
    "全频强劲": {
        "dual_motor_mode": False,
        "vocal_only_mode": False,
        "adaptive_threshold": False,
        "intensity": 1.0,
        "vibration_threshold": 180.0,
        "map_in_max": 6000.0,
        "gamma_correction": 0.9,
        "attack_rate": 0.58,
        "release_rate": 0.22,
        "dead_zone": 0.035,
        "vocal_low": 600,
        "vocal_high": 4200,
        "bass_high": 700,
    },
}

MODE_CARDS = [
    ("智能自动", "自动适配场景"),
    ("人声清晰", "对白 · 播客"),
    ("低频沉浸", "音乐 · 鼓点"),
    ("轻柔细腻", "ASMR · 助眠"),
    ("全频强劲", "游戏 · 影院"),
]

FEEL_DEFAULTS = {
    "intensity": 0.72,
    "rhythm_punch": 0.62,
    "texture_amount": 0.58,
}

ADVANCED_DEFAULTS = {
    "dual_motor_mode": False,
    "vocal_only_mode": True,
    "adaptive_threshold": True,
    "vibration_threshold": 260.0,
    "dead_zone": 0.045,
    "attack_rate": 0.38,
    "release_rate": 0.12,
    "map_in_max": 4200.0,
    "gamma_correction": 1.28,
    "vocal_low": 650,
    "vocal_high": 3600,
    "bass_high": 520,
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
    for key in ("enabled", "auto_profile", "dual_motor_mode", "vocal_only_mode", "adaptive_threshold"):
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


@dataclass
class SharedVibrationParams:
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    enabled: bool = True
    controller_index: int = 0
    auto_profile: bool = True
    rhythm_punch: float = 0.62
    texture_amount: float = 0.58
    dual_motor_mode: bool = False
    vocal_only_mode: bool = True
    adaptive_threshold: bool = True
    auto_calibrating: bool = False
    reconnect_requested: bool = False
    intensity: float = 0.72
    vibration_threshold: float = 260.0
    dead_zone: float = 0.045
    attack_rate: float = 0.38
    release_rate: float = 0.12
    map_in_max: float = 4200.0
    gamma_correction: float = 1.28
    vocal_low: int = 650
    vocal_high: int = 3600
    bass_high: int = 520

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

    def snapshot(self) -> dict:
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
                stats=asdict(self.stats),
            )
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


def classify_content(total_rms: float, vocal_rms: float, bass_rms: float, transient: float, prev: str) -> str:
    if total_rms < 55.0:
        return "安静"
    total = max(total_rms, 1.0)
    bass_ratio = bass_rms / total
    vocal_ratio = vocal_rms / total

    music_score = bass_ratio * 1.25 + transient * 1.65
    asmr_score = vocal_ratio * 1.15 + (1.0 - min(transient * 2.4, 1.0)) * 0.55 - bass_ratio * 0.45

    if prev == "音乐":
        music_score += 0.12
    elif prev == "ASMR":
        asmr_score += 0.12

    if music_score > asmr_score + 0.16:
        return "音乐"
    if asmr_score > music_score + 0.10:
        return "ASMR"
    return prev if prev in {"音乐", "ASMR"} else "ASMR"


def controller_io_thread(shared: SharedVibrationParams, log_queue: queue.Queue, stop_event: threading.Event):
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
                keepalive = (key[1] > 0 or key[2] > 0) and (now - last_sent_at > 0.25)
                if key != last_sent or keepalive:
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


def safe_close_stream(stream):
    if stream is None:
        return
    for fn in (stream.stop_stream, stream.close):
        try:
            fn()
        except Exception:
            pass


def audio_vibration_thread(shared: SharedVibrationParams, log_queue: queue.Queue, stop_event: threading.Event):
    """WASAPI Loopback 回调式非阻塞音频分析线程。"""
    chunk = 512
    audio = pyaudio.PyAudio()
    stream = None
    audio_queue: queue.Queue = queue.Queue(maxsize=12)

    channels = 2
    rate = 48000
    current_device_name = ""

    left_vib = 0.0
    right_vib = 0.0
    noise_floor = 0.0
    calibration_until = 0.0
    energy_avg = 0.0
    transient_avg = 0.0
    detected_mode = "识别中"

    fft_cache_key = None
    fft_freqs = None
    fft_weights = None

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
            snap = shared.snapshot()

            if snap["reconnect_requested"]:
                with shared.lock:
                    shared.reconnect_requested = False
                safe_close_stream(stream)
                stream = None
                next_connect_at = 0.0

            # 定期检查默认音频设备是否发生切换（例如从扬声器切换到耳机）
            if stream is not None and now >= next_default_check_at:
                next_default_check_at = now + 3.5
                try:
                    latest_dev = find_loopback_device(audio, log_queue, log_info=False)
                    if latest_dev and latest_dev["name"] != current_device_name:
                        log_queue.put(f"检测到默认播放设备切换: {latest_dev['name']}")
                        safe_close_stream(stream)
                        stream = None
                        next_connect_at = 0.0
                except Exception:
                    pass

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
                    dev = find_loopback_device(audio, log_queue, log_info=True)
                    if dev is None:
                        raise RuntimeError("未找到可用的系统回环音频设备")
                    channels = max(1, int(dev["maxInputChannels"]))
                    rate = int(dev["defaultSampleRate"])
                    current_device_name = dev["name"]
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
                    next_default_check_at = now + 3.5
                    with shared.lock:
                        shared.stats.device_name = current_device_name
                        shared.stats.effective_threshold = shared.vibration_threshold
                    log_queue.put(f"音频监听就绪: {current_device_name} ({rate} Hz)")
                except Exception as exc:
                    stream = None
                    next_connect_at = now + 1.5
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

                left_vib = smooth_signal(left_vib, 0.0, snap["attack_rate"], max(0.15, snap["release_rate"]))
                right_vib = smooth_signal(right_vib, 0.0, snap["attack_rate"], max(0.15, snap["release_rate"]))
                if left_vib < snap["dead_zone"]:
                    left_vib = 0.0
                if right_vib < snap["dead_zone"]:
                    right_vib = 0.0

                with shared.lock:
                    shared.audio_left = left_vib if shared.enabled else 0.0
                    shared.audio_right = right_vib if shared.enabled else 0.0
                    shared.stats.total_rms *= 0.72
                    shared.stats.vocal_rms *= 0.72
                    shared.stats.bass_rms *= 0.72
                    shared.stats.peak_rms *= 0.95
                    if shared.stats.total_rms < 5.0:
                        shared.stats.total_rms = 0.0
                        shared.stats.detected_mode = "安静"
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

            fft_data = np.fft.rfft(samples)
            power = (fft_data.real ** 2 + fft_data.imag ** 2) * fft_weights

            bass_rms = band_rms_power(power, fft_freqs, 20.0, snap["bass_high"], n)
            vocal_rms = band_rms_power(power, fft_freqs, snap["vocal_low"], snap["vocal_high"], n)
            total_rms = float(np.sqrt(np.mean(np.square(samples)) + 1e-10))

            energy_avg = energy_avg * 0.965 + total_rms * 0.035
            transient = clamp((total_rms - energy_avg) / max(energy_avg, 80.0), 0.0, 1.0)
            transient_avg = transient_avg * 0.82 + transient * 0.18

            now = time.monotonic()
            if snap["auto_calibrating"]:
                noise_floor = max(noise_floor * 0.97, total_rms, vocal_rms)
                left_sec = max(0.0, calibration_until - now)
                if now >= calibration_until:
                    calibrated = clamp(max(120.0, noise_floor * 1.8), 40.0, 8000.0)
                    with shared.lock:
                        shared.vibration_threshold = calibrated
                        shared.auto_calibrating = False
                        shared.stats.calibrating_left = 0.0
                    calibration_until = 0.0
                    log_queue.put(f"环境校准完成: 触发门限已设为 {calibrated:.0f}")
            else:
                calibration_until = 0.0
                level = min(total_rms, vocal_rms)
                if level < noise_floor:
                    noise_floor += (level - noise_floor) * 0.06
                else:
                    noise_floor += (level - noise_floor) * 0.0015

            detected_mode = classify_content(total_rms, vocal_rms, bass_rms, transient_avg, detected_mode)

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
                    effective.update(
                        dual_motor_mode=True,
                        vocal_only_mode=False,
                        adaptive_threshold=True,
                        vibration_threshold=max(170.0, noise_floor * 1.7),
                        map_in_max=5600.0,
                        gamma_correction=0.92,
                        attack_rate=0.58,
                        release_rate=0.17,
                        dead_zone=0.035,
                        intensity=clamp(snap["intensity"] * 0.92, 0.1, 1.0),
                    )
                elif detected_mode == "ASMR":
                    effective.update(
                        dual_motor_mode=False,
                        vocal_only_mode=True,
                        adaptive_threshold=True,
                        vibration_threshold=max(75.0, noise_floor * 1.18),
                        map_in_max=2300.0,
                        gamma_correction=1.75,
                        attack_rate=0.20,
                        release_rate=0.045,
                        dead_zone=0.012,
                        intensity=clamp(snap["intensity"] * 0.50, 0.05, 0.75),
                    )
                else:
                    effective.update(
                        vibration_threshold=max(260.0, noise_floor * 2.0),
                        attack_rate=0.16,
                        release_rate=0.09,
                        dead_zone=0.05,
                        intensity=0.0,
                    )

            adaptive_floor = noise_floor * 1.55 if effective["adaptive_threshold"] else 0.0
            threshold = max(effective["vibration_threshold"], adaptive_floor)

            def calc_vib(rms_val: float) -> float:
                mapped = map_value_gamma(rms_val, threshold, effective["map_in_max"], effective["gamma_correction"])
                return clamp(mapped * effective["intensity"])

            if effective["dual_motor_mode"]:
                # 左马达为低频重型配重，右马达为高频轻型配重
                target_left = calc_vib(bass_rms)
                target_right = calc_vib(vocal_rms)
            else:
                src = vocal_rms if effective["vocal_only_mode"] else total_rms
                target_left = target_right = calc_vib(src)

            if snap["auto_profile"] and detected_mode == "音乐":
                beat_pulse = transient_avg * snap["rhythm_punch"] * snap["intensity"]
                target_left = clamp(target_left + beat_pulse * 0.55)
                target_right = clamp(target_right + beat_pulse * 0.22)
            elif snap["auto_profile"] and detected_mode == "ASMR":
                breath = clamp((vocal_rms - noise_floor * 0.85) / max(900.0, threshold * 4.0))
                wave = 0.55 + 0.45 * math.sin(now * 2.0 * math.pi * 34.0)
                texture = breath * snap["texture_amount"] * 0.075 * wave
                target_left = clamp(target_left + texture * 0.82)
                target_right = clamp(target_right + texture)
            elif not snap["auto_profile"]:
                # 手动预设下同样让节奏冲击和细腻微颤滑块直观生效
                if snap["rhythm_punch"] > 0.01:
                    beat_pulse = transient_avg * snap["rhythm_punch"] * snap["intensity"] * 0.42
                    target_left = clamp(target_left + beat_pulse)
                if snap["texture_amount"] > 0.01 and vocal_rms > threshold * 0.75:
                    breath = clamp((vocal_rms - threshold * 0.75) / max(1200.0, threshold * 3.5))
                    wave = 0.55 + 0.45 * math.sin(now * 2.0 * math.pi * 34.0)
                    texture = breath * snap["texture_amount"] * 0.055 * wave
                    target_right = clamp(target_right + texture)

            left_vib = smooth_signal(left_vib, target_left, effective["attack_rate"], effective["release_rate"])
            right_vib = smooth_signal(right_vib, target_right, effective["attack_rate"], effective["release_rate"])

            if left_vib < effective["dead_zone"]:
                left_vib = 0.0
            if right_vib < effective["dead_zone"]:
                right_vib = 0.0

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


# ---------------------------------------------------------------------------
# 简洁现代 UI 组件库
# ---------------------------------------------------------------------------

class CardFrame(tk.Canvas):
    """自适应圆角卡片容器。"""

    def __init__(self, parent, pad_x=16, pad_y=13, radius=14, bg_fill=BG_CARD, border_color=BORDER):
        super().__init__(parent, height=S(32), bg=parent.cget("bg"), bd=0, highlightthickness=0)
        self.pad_x = S(pad_x)
        self.pad_y = S(pad_y)
        self.radius = S(radius)
        self.bg_fill = bg_fill
        self.border_color = border_color

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
        self.tag_lower(box)
        self.coords(self._win_id, self.pad_x, self.pad_y)
        self.itemconfigure(self._win_id, width=max(1, w - self.pad_x * 2))


class StatusPill(tk.Canvas):
    """圆角状态胶囊徽章，可选点击交互。"""

    def __init__(self, parent, text="", dot_color=GREEN, command=None):
        self._parent_bg = parent.cget("bg")
        super().__init__(
            parent,
            height=S(26),
            width=S(120),
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

        if command:
            self.bind("<Enter>", lambda _e: self._set_hover(True))
            self.bind("<Leave>", lambda _e: self._set_hover(False))
            self.bind("<ButtonRelease-1>", lambda _e: command())
        self._redraw()

    def _set_hover(self, state: bool):
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
        w = text_w + S(28)
        h = S(26)
        if abs(self.winfo_reqwidth() - w) > 1:
            self.configure(width=w)
        fill = BG_CARD_HI if self._hover else BG_INSET
        stroke = BORDER_HI if self._hover else BORDER
        draw_rounded_box(self, 1, 1, w - 2, h - 2, h / 2, fill=fill, outline=stroke, width=1)
        r = S(3.5)
        cx, cy = S(12), h / 2
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=self._dot, outline="")
        self.create_text(S(20), cy, text=self._text, anchor="w", fill=TEXT_SUB, font=F_TINY)


class MasterPowerButton(tk.Canvas):
    """右上角醒目圆润的震动总开关。"""

    def __init__(self, parent, command):
        super().__init__(
            parent,
            width=S(162),
            height=S(44),
            bg=parent.cget("bg"),
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self._enabled = True
        self._hover = False
        self._command = command
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", lambda _e: self._command())
        self._redraw()

    def _set_hover(self, val: bool):
        self._hover = val
        self._redraw()

    def set_state(self, enabled: bool):
        if self._enabled != enabled:
            self._enabled = enabled
            self._redraw()

    def _redraw(self):
        self.delete("all")
        w, h = S(162), S(44)
        if self._enabled:
            fill = blend_hex(GREEN_BG, GREEN, 0.28 if self._hover else 0.16)
            stroke = GREEN_HI if self._hover else GREEN
            dot_fill = GREEN_HI
            label = "震动已开启"
            fg = TEXT_MAIN
        else:
            fill = BG_BTN_HOVER if self._hover else BG_INSET
            stroke = BORDER_HI if self._hover else BORDER
            dot_fill = TEXT_MUTED
            label = "震动已关闭"
            fg = TEXT_SUB

        draw_rounded_box(self, 1, 1, w - 2, h - 2, h / 2, fill=fill, outline=stroke, width=S(1.5))

        # 左侧拨动胶囊指示
        pill_x1, pill_y1 = S(12), S(12)
        pill_x2, pill_y2 = S(42), h - S(12)
        track_col = GREEN if self._enabled else "#2c3647"
        draw_rounded_box(self, pill_x1, pill_y1, pill_x2, pill_y2, (pill_y2 - pill_y1) / 2, fill=track_col, outline="")
        knob_r = (pill_y2 - pill_y1) / 2 - S(2.5)
        knob_cx = (pill_x2 - S(2.5) - knob_r) if self._enabled else (pill_x1 + S(2.5) + knob_r)
        knob_cy = h / 2
        self.create_oval(
            knob_cx - knob_r,
            knob_cy - knob_r,
            knob_cx + knob_r,
            knob_cy + knob_r,
            fill="#ffffff" if self._enabled else dot_fill,
            outline="",
        )

        self.create_text(S(52), h / 2, text=label, anchor="w", fill=fg, font=F_H2)


class ModeSelector(tk.Frame):
    """5 选 1 直观场景模式卡片组。"""

    def __init__(self, parent, items, on_select):
        super().__init__(parent, bg=parent.cget("bg"))
        self._items = items
        self._on_select = on_select
        self._selected = items[0][0]
        self._cards = {}

        for idx, (name, desc) in enumerate(items):
            self.columnconfigure(idx, weight=1, uniform="mode_col")
            card = _ModeOption(self, name, desc, lambda n=name: self._handle_click(n))
            card.grid(row=0, column=idx, sticky="ew", padx=(0 if idx == 0 else S(6), 0))
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
    def __init__(self, parent, title: str, subtitle: str, command):
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
            fill = blend_hex(BLUE_BG, BLUE, 0.22 if self.hover else 0.14)
            stroke = BLUE_HI if self.hover else BLUE
            title_fg = TEXT_MAIN
            sub_fg = "#93c5fd"
        else:
            fill = BG_CARD_HI if self.hover else BG_INSET
            stroke = BORDER_HI if self.hover else BORDER
            title_fg = TEXT_MAIN if self.hover else TEXT_SUB
            sub_fg = TEXT_MUTED

        draw_rounded_box(self, 1, 1, w - 2, h - 2, S(10), fill=fill, outline=stroke, width=1)
        self.create_text(w / 2, S(21), text=self.title, fill=title_fg, font=F_SMALL_B)
        self.create_text(w / 2, S(41), text=self.subtitle, fill=sub_fg, font=F_TINY)


class ModernSlider(tk.Frame):
    """简洁顺滑的百分比/数值滑块，支持点击直达、拖拽与滚轮微调。"""

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
        self.val_label = tk.Label(top, text=self.display_fn(self.value), bg=bg, fg=TEXT_MAIN, font=F_MONO)
        self.val_label.pack(side="right")

        self.track = tk.Canvas(
            self,
            height=S(22),
            bg=bg,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self.track.pack(fill="x", pady=(S(2), 0))
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
        pad = S(9)
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

    def _redraw(self):
        self.track.delete("all")
        w = max(self.track.winfo_width(), S(100))
        h = S(22)
        pad = S(9)
        cy = h / 2
        bar_h = S(6)

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
        fill_x = pad + ratio * (w - pad * 2)
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

        r = S(7.5 if (self._hover or self._dragging) else 6.5)
        self.track.create_oval(
            fill_x - r,
            cy - r,
            fill_x + r,
            cy + r,
            fill="#ffffff",
            outline=self.accent,
            width=S(2),
        )


class SmoothMeterRow(tk.Frame):
    """单行水平圆角实时能量条（可选显示触发阈值标记线）。"""

    def __init__(self, parent, title: str, color: str, max_val: float = 1.0, fmt_fn=None):
        bg = parent.cget("bg")
        super().__init__(parent, bg=bg)
        self.color = color
        self.max_val = max_val
        self.fmt_fn = fmt_fn or (lambda v: f"{int(round(clamp(v / max_val) * 100))}%")
        self._val = 0.0
        self._peak = 0.0
        self._marker = None

        self.columnconfigure(1, weight=1)
        tk.Label(self, text=title, bg=bg, fg=TEXT_SUB, font=F_SMALL, width=10, anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        self.bar = tk.Canvas(self, height=S(14), bg=bg, bd=0, highlightthickness=0)
        self.bar.grid(row=0, column=1, sticky="ew", padx=(S(6), S(6)))
        self.readout = tk.Label(self, text=self.fmt_fn(0.0), bg=bg, fg=TEXT_MAIN, font=F_MONO_SM, width=5, anchor="e")
        self.readout.grid(row=0, column=2, sticky="e")

        self.bar.bind("<Configure>", lambda _e: self._redraw())

    def set_level(self, value: float, peak: float = None, threshold_marker: float = None):
        self._val = max(0.0, value)
        self._peak = max(0.0, peak) if peak is not None else 0.0
        self._marker = threshold_marker
        self.readout.config(text=self.fmt_fn(self._val))
        self._redraw()

    def _redraw(self):
        self.bar.delete("all")
        w = max(self.bar.winfo_width(), S(60))
        h = S(14)
        y1, y2 = S(2), h - S(2)
        r = (y2 - y1) / 2

        draw_rounded_box(self.bar, 1, y1, w - 2, y2, r, fill=BG_INSET, outline=BORDER, width=1)

        ratio = clamp(self._val / max(1e-6, self.max_val))
        fill_w = ratio * (w - 4)
        if fill_w > 2:
            draw_rounded_box(self.bar, 2, y1 + 1, 2 + fill_w, y2 - 1, r - 1, fill=self.color, outline="")

        if self._peak > 0:
            p_ratio = clamp(self._peak / max(1e-6, self.max_val))
            px = 2 + p_ratio * (w - 4)
            if px > 4:
                self.bar.create_line(px, y1 + 1, px, y2 - 1, fill=blend_hex(self.color, "#ffffff", 0.55), width=S(1.5))

        if self._marker is not None and self._marker > 0:
            m_ratio = clamp(self._marker / max(1e-6, self.max_val))
            mx = 2 + m_ratio * (w - 4)
            self.bar.create_line(mx, 0, mx, h, fill=AMBER, width=S(2))


class ActionButton(tk.Canvas):
    """圆角扁平操作按钮。"""

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
            fill = blend_hex(BLUE_BG, BLUE, 0.35 if self._hover else 0.22)
            stroke = BLUE_HI if self._hover else BLUE
            fg = TEXT_MAIN
        else:
            fill = BG_BTN_HOVER if self._hover else BG_BTN
            stroke = BORDER_HI if self._hover else BORDER
            fg = TEXT_MAIN

        draw_rounded_box(self, 1, 1, w - 2, h - 2, S(8), fill=fill, outline=stroke, width=1)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=F_SMALL_B)


class PadSlotSelector(tk.Canvas):
    """紧凑的手柄槽位 [1][2][3][4] 分段选择器。"""

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
        draw_rounded_box(self, 1, 1, w - 2, h - 2, S(8), fill=BG_INSET, outline=BORDER, width=1)
        for i in range(4):
            x1 = S(3) + i * self._seg_w
            x2 = x1 + self._seg_w
            if i == self._index:
                draw_rounded_box(self, x1 + 1, S(3), x2 - 1, h - S(3), S(6), fill=BG_BTN_HOVER, outline=BLUE, width=1)
                fg = TEXT_MAIN
            elif i == self._hover_idx:
                draw_rounded_box(self, x1 + 1, S(3), x2 - 1, h - S(3), S(6), fill=BG_CARD_HI, outline="")
                fg = TEXT_MAIN
            else:
                fg = TEXT_MUTED
            self.create_text((x1 + x2) / 2, h / 2, text=str(i + 1), fill=fg, font=F_SMALL_B)


# ---------------------------------------------------------------------------
# 二级高级设置与运行日志弹窗（按需打开，保持主界面极致简洁）
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

        apply_dark_titlebar(self)

        wrap = tk.Frame(self, bg=BG_WINDOW)
        wrap.pack(fill="both", expand=True, padx=S(16), pady=S(14))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(2, weight=1)

        # 1. 手动模式策略开关
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

        # 2. 底层曲线与频段滑块
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
            ("释放速度", "release_rate", 0.02, 0.60, lambda v: f"{v:.2f}", False),
            ("曲线伽马", "gamma_correction", 0.50, 2.40, lambda v: f"{v:.2f}", False),
            ("马达死区", "dead_zone", 0.0, 0.15, lambda v: f"{int(round(v * 100))}%", False),
            ("低频上限", "bass_high", 120.0, 1200.0, lambda v: f"{int(round(v))} Hz", True),
            ("人声下限", "vocal_low", 200.0, 2000.0, lambda v: f"{int(round(v))} Hz", True),
            ("人声上限", "vocal_high", 1500.0, 7000.0, lambda v: f"{int(round(v))} Hz", True),
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
            s.grid(row=r, column=c, sticky="ew", padx=(0, S(10)) if c == 0 else (S(10), 0), pady=S(3))
            self.sliders[attr] = s

        # 3. 运行日志
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
        w = S(580)
        h = max(S(560), self.winfo_reqheight())
        self.minsize(S(520), S(500))
        px = app.master.winfo_rootx() + (app.master.winfo_width() - w) // 2
        py = app.master.winfo_rooty() + S(18)
        self.geometry(f"{w}x{h}+{max(0, px)}+{max(0, py)}")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.deiconify()

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
        self.audio_thread = None
        self.controller_thread = None
        self.test_thread = None
        self.adv_dialog: AdvancedDialog | None = None
        self.closing = False
        self._save_timer = None
        self._seen_hotkey_seq = 0

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
        root_pad.pack(fill="both", expand=True, padx=S(18), pady=S(16))
        root_pad.columnconfigure(0, weight=1)

        # 1. 顶部主控卡片（连接状态 + 大号震动总开关 + LB+RB 快捷提示）
        hero_card = CardFrame(root_pad, pad_x=18, pad_y=15, radius=16)
        hero_card.grid(row=0, column=0, sticky="ew", pady=(0, S(12)))
        hero = hero_card.inner
        hero.columnconfigure(0, weight=1)

        left_info = tk.Frame(hero, bg=BG_CARD)
        left_info.grid(row=0, column=0, sticky="w")
        title_row = tk.Frame(left_info, bg=BG_CARD)
        title_row.pack(anchor="w")
        tk.Label(title_row, text="Xbox 音频震动", bg=BG_CARD, fg=TEXT_MAIN, font=F_H1).pack(side="left")
        tk.Label(
            title_row,
            text="Audio Rumble Studio",
            bg=BG_CARD,
            fg=TEXT_MUTED,
            font=F_SMALL,
        ).pack(side="left", padx=(S(8), 0), pady=(S(3), 0))

        badges = tk.Frame(left_info, bg=BG_CARD)
        badges.pack(anchor="w", pady=(S(8), 0))
        self.pad_pill = StatusPill(badges, "手柄 1 · 检测中", TEXT_MUTED, command=self.cycle_controller)
        self.pad_pill.pack(side="left", padx=(0, S(8)))
        self.audio_pill = StatusPill(badges, "音频源连接中...", CYAN, command=self.request_audio_reconnect)
        self.audio_pill.pack(side="left")

        right_ctrl = tk.Frame(hero, bg=BG_CARD)
        right_ctrl.grid(row=0, column=1, sticky="e")
        self.power_btn = MasterPowerButton(right_ctrl, command=self.toggle_enabled)
        self.power_btn.pack(anchor="e")
        self.hotkey_hint = tk.Label(
            right_ctrl,
            text="快捷键：同按手柄 LB + RB 开关震动",
            bg=BG_CARD,
            fg=TEXT_SUB,
            font=F_TINY,
        )
        self.hotkey_hint.pack(anchor="e", pady=(S(5), 0))

        # 2. 场景模式卡片（智能自动 + 4 组精心调校预设）
        mode_card = CardFrame(root_pad, pad_x=16, pad_y=14)
        mode_card.grid(row=1, column=0, sticky="ew", pady=(0, S(12)))
        m_body = mode_card.inner

        m_hdr = tk.Frame(m_body, bg=BG_CARD)
        m_hdr.pack(fill="x", pady=(0, S(10)))
        tk.Label(m_hdr, text="场景模式", bg=BG_CARD, fg=TEXT_MAIN, font=F_H2).pack(side="left")
        self.scene_status = tk.Label(m_hdr, text="正在识别场景...", bg=BG_CARD, fg=BLUE_HI, font=F_SMALL)
        self.scene_status.pack(side="right")

        self.mode_selector = ModeSelector(m_body, MODE_CARDS, self.on_mode_selected)
        self.mode_selector.pack(fill="x")

        # 3. 核心手感调节卡片（3 项直观百分比滑块）
        feel_card = CardFrame(root_pad, pad_x=16, pad_y=14)
        feel_card.grid(row=2, column=0, sticky="ew", pady=(0, S(12)))
        f_body = feel_card.inner

        f_hdr = tk.Frame(f_body, bg=BG_CARD)
        f_hdr.pack(fill="x", pady=(0, S(6)))
        tk.Label(f_hdr, text="手感调节", bg=BG_CARD, fg=TEXT_MAIN, font=F_H2).pack(side="left")
        reset_lbl = tk.Label(f_hdr, text="恢复默认", bg=BG_CARD, fg=TEXT_MUTED, font=F_TINY, cursor="hand2")
        reset_lbl.pack(side="right")
        reset_lbl.bind("<Enter>", lambda _e: reset_lbl.config(fg=BLUE_HI))
        reset_lbl.bind("<Leave>", lambda _e: reset_lbl.config(fg=TEXT_MUTED))
        reset_lbl.bind("<ButtonRelease-1>", lambda _e: self.reset_feel())

        self.feel_sliders = {}
        feel_specs = [
            ("震动强度", "总体力度上限", "intensity", 0.10, 1.0, GREEN),
            ("节奏冲击", "鼓点与重低音冲击感", "rhythm_punch", 0.0, 1.0, AMBER),
            ("细节微颤", "人声呼吸与轻柔麻感", "texture_amount", 0.0, 1.0, PURPLE),
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
            slider.pack(fill="x", pady=(0 if idx == 0 else S(6), 0))
            self.feel_sliders[attr] = slider

        # 4. 实时动态监控卡片（输入电平带阈值线 + 左右马达输出条）
        mon_card = CardFrame(root_pad, pad_x=16, pad_y=14)
        mon_card.grid(row=3, column=0, sticky="ew", pady=(0, S(12)))
        mon_body = mon_card.inner

        mon_hdr = tk.Frame(mon_body, bg=BG_CARD)
        mon_hdr.pack(fill="x", pady=(0, S(8)))
        tk.Label(mon_hdr, text="实时响应", bg=BG_CARD, fg=TEXT_MAIN, font=F_H2).pack(side="left")
        self.threshold_info = tk.Label(mon_hdr, text="黄线 = 触发门限", bg=BG_CARD, fg=TEXT_MUTED, font=F_TINY)
        self.threshold_info.pack(side="right")

        self.meter_audio = SmoothMeterRow(
            mon_body,
            "系统音量",
            CYAN,
            max_val=MAX_RMS_DISPLAY,
            fmt_fn=lambda v: f"{int(round(v))}",
        )
        self.meter_audio.pack(fill="x", pady=(0, S(6)))

        self.meter_left = SmoothMeterRow(mon_body, "左马达·低频", GREEN, max_val=1.0)
        self.meter_left.pack(fill="x", pady=(0, S(6)))

        self.meter_right = SmoothMeterRow(mon_body, "右马达·细节", BLUE_HI, max_val=1.0)
        self.meter_right.pack(fill="x")

        # 5. 底部快捷工具栏
        footer = tk.Frame(root_pad, bg=BG_WINDOW)
        footer.grid(row=4, column=0, sticky="ew", pady=(S(2), 0))

        tk.Label(footer, text="手柄槽位", bg=BG_WINDOW, fg=TEXT_MUTED, font=F_SMALL).pack(side="left", padx=(0, S(8)))
        self.slot_selector = PadSlotSelector(footer, self.set_controller_index)
        self.slot_selector.pack(side="left")

        ActionButton(footer, "高级与日志", self.open_advanced_dialog, width=98, height=30).pack(side="right")
        self.cal_btn = ActionButton(footer, "环境校准", self.start_calibration, width=88, height=30, primary=True)
        self.cal_btn.pack(side="right", padx=(0, S(8)))
        self.test_btn = ActionButton(footer, "测试震动", self.run_test_vibration, width=88, height=30)
        self.test_btn.pack(side="right", padx=(0, S(8)))

        self._cards = [hero_card, mode_card, feel_card, mon_card]

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
        self.log_queue.put(f"震动输出已{'开启' if new_state else '关闭'}。")
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
            with self.shared.lock:
                self.shared.auto_profile = True
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
        self.shared.apply(FEEL_DEFAULTS)
        for attr, val in FEEL_DEFAULTS.items():
            self.feel_sliders[attr].set_value(val)
        self.log_queue.put("手感滑块已恢复默认值。")
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
            steps = [(0.38, 0.0, 0.18), (0.0, 0.38, 0.18), (0.60, 0.60, 0.24)]
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

    # ----- 后台线程启动与界面刷新 -----

    def start_threads(self):
        self.stop_event.clear()
        if not (self.controller_thread and self.controller_thread.is_alive()):
            self.controller_thread = threading.Thread(
                target=controller_io_thread,
                args=(self.shared, self.log_queue, self.stop_event),
                daemon=True,
            )
            self.controller_thread.start()
        if not (self.audio_thread and self.audio_thread.is_alive()):
            self.audio_thread = threading.Thread(
                target=audio_vibration_thread,
                args=(self.shared, self.log_queue, self.stop_event),
                daemon=True,
            )
            self.audio_thread.start()

    def update_gui(self):
        if self.closing:
            return
        try:
            self._refresh_ui()
        except tk.TclError:
            return
        except Exception:
            pass
        self.master.after(35, self.update_gui)

    def _refresh_ui(self):
        snap = self.shared.snapshot()
        stats = snap["stats"]
        now = time.monotonic()

        # 同步总开关状态（包括通过手柄 LB + RB 切换后的状态同步与自动保存）
        self.power_btn.set_state(snap["enabled"])
        if snap["hotkey_seq"] != self._seen_hotkey_seq:
            self._seen_hotkey_seq = snap["hotkey_seq"]
            self.schedule_save()

        if snap["hotkey_notice_at"] and (now - snap["hotkey_notice_at"]) < 2.8:
            if snap["hotkey_notice_enabled"]:
                self.hotkey_hint.config(text="✓ 已通过 LB + RB 开启震动", fg=GREEN_HI)
            else:
                self.hotkey_hint.config(text="⏸ 已通过 LB + RB 关闭震动", fg=AMBER)
        else:
            self.hotkey_hint.config(text="快捷键：同按手柄 LB + RB 开关震动", fg=TEXT_SUB)

        # 手柄与音频状态胶囊
        pad_num = snap["controller_index"] + 1
        if stats["controller_connected"]:
            self.pad_pill.update_pill(f"手柄 {pad_num} · 已连接", GREEN)
        else:
            self.pad_pill.update_pill(f"手柄 {pad_num} · 未连接", RED)

        dev_name = stats["device_name"]
        short_dev = dev_name if len(dev_name) <= 26 else dev_name[:25] + "…"
        dev_ok = "未检测" not in dev_name and "重连" not in dev_name
        self.audio_pill.update_pill(short_dev, CYAN if dev_ok else AMBER)

        # 场景状态与校准按钮反馈
        if snap["auto_calibrating"]:
            sec = max(1, int(math.ceil(stats["calibrating_left"])))
            self.cal_btn.configure_btn(f"校准 {sec}s…", enabled=False)
            self.scene_status.config(text="正在采样环境底噪...", fg=AMBER)
        else:
            self.cal_btn.configure_btn("环境校准", enabled=True)
            if not snap["enabled"]:
                self.scene_status.config(text="震动已暂停（LB + RB 可恢复）", fg=TEXT_MUTED)
            elif snap["auto_profile"]:
                mode_map = {
                    "音乐": ("当前识别：音乐律动", AMBER),
                    "ASMR": ("当前识别：人声 / 轻语", PURPLE),
                    "安静": ("当前识别：安静待机", TEXT_MUTED),
                }
                txt, col = mode_map.get(stats["detected_mode"], ("正在监听识别...", BLUE_HI))
                self.scene_status.config(text=txt, fg=col)
            else:
                self.scene_status.config(text="手动预设模式", fg=BLUE_HI)

        testing = self.test_thread is not None and self.test_thread.is_alive()
        self.test_btn.configure_btn("测试中…" if testing else "测试震动", enabled=not testing)

        # 实时动态条
        self.threshold_info.config(
            text=f"触发门限 {stats['effective_threshold']:.0f}  ·  底噪 {stats['noise_floor']:.0f}"
        )
        self.meter_audio.set_level(
            stats["total_rms"],
            peak=stats["peak_rms"],
            threshold_marker=stats["effective_threshold"],
        )
        self.meter_left.set_level(stats["left_output"], peak=stats["peak_left"])
        self.meter_right.set_level(stats["right_output"], peak=stats["peak_right"])

        # 排空日志队列
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
            # 当校准刚完成时同步刷新高级窗口里的触发门限滑块
            if "校准完成" in msg and self.adv_dialog and self.adv_dialog.winfo_exists():
                self.adv_dialog.sync_from_snapshot(self.shared.snapshot())

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
        with self.shared.lock:
            self.shared.enabled = False
            self.shared.audio_left = 0.0
            self.shared.audio_right = 0.0
            self.shared.override_until = 0.0

        for t in (self.controller_thread, self.audio_thread):
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
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("yisal.XboxAudioRumbleStudio")
    except Exception:
        pass

    root = tk.Tk()
    root.withdraw()
    root.title(APP_TITLE)
    root.configure(bg=BG_WINDOW)

    UI_SCALE = detect_scale(root)
    init_fonts(root)
    apply_dark_titlebar(root)

    try:
        icon = build_app_icon()
        root.iconphoto(True, icon)
        root._icon_ref = icon
    except Exception:
        pass

    app = VibrationApp(root)
    app.sync_layout()

    width = S(640)
    height = root.winfo_reqheight()
    root.minsize(S(580), height)

    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    pos_x = max(0, (screen_w - width) // 2)
    pos_y = max(0, (screen_h - height) // 2 - S(16))
    root.geometry(f"{width}x{height}+{pos_x}+{pos_y}")

    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    main()
