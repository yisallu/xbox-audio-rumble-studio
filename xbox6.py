import ctypes
import json
import math
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tkinter import scrolledtext, ttk
import tkinter as tk
from ctypes import wintypes

import numpy as np
import pyaudiowpatch as pyaudio


APP_TITLE = "Xbox Audio Rumble Studio"
CONFIG_FILE = Path(__file__).with_suffix(".settings.json")
XINPUT_SUCCESS = 0
XINPUT_ERROR_DEVICE_NOT_CONNECTED = 1167


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
    xinput = ctypes.WinDLL("xinput9_1_0")

XInputSetState = xinput.XInputSetState
XInputSetState.argtypes = [wintypes.DWORD, ctypes.POINTER(XInputVibration)]
XInputSetState.restype = wintypes.DWORD

XInputGetState = xinput.XInputGetState
XInputGetState.argtypes = [wintypes.DWORD, ctypes.POINTER(XInputState)]
XInputGetState.restype = wintypes.DWORD

XINPUT_GAMEPAD_LEFT_SHOULDER = 0x0100
XINPUT_GAMEPAD_RIGHT_SHOULDER = 0x0200


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


@dataclass
class RuntimeStats:
    total_rms: float = 0.0
    vocal_rms: float = 0.0
    bass_rms: float = 0.0
    left_output: float = 0.0
    right_output: float = 0.0
    noise_floor: float = 0.0
    effective_threshold: float = 0.0
    device_name: str = "初始化中..."
    controller_connected: bool = False
    peak_left: float = 0.0
    peak_right: float = 0.0
    detected_mode: str = "识别中"
    beat_strength: float = 0.0
    texture_level: float = 0.0


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
    stats: RuntimeStats = field(default_factory=RuntimeStats)

    def snapshot(self):
        with self.lock:
            return {
                "enabled": self.enabled,
                "controller_index": self.controller_index,
                "auto_profile": self.auto_profile,
                "rhythm_punch": self.rhythm_punch,
                "texture_amount": self.texture_amount,
                "dual_motor_mode": self.dual_motor_mode,
                "vocal_only_mode": self.vocal_only_mode,
                "adaptive_threshold": self.adaptive_threshold,
                "auto_calibrating": self.auto_calibrating,
                "intensity": self.intensity,
                "vibration_threshold": self.vibration_threshold,
                "dead_zone": self.dead_zone,
                "attack_rate": self.attack_rate,
                "release_rate": self.release_rate,
                "map_in_max": self.map_in_max,
                "gamma_correction": self.gamma_correction,
                "vocal_low": self.vocal_low,
                "vocal_high": self.vocal_high,
                "bass_high": self.bass_high,
                "stats": asdict(self.stats),
            }

    def apply(self, values):
        with self.lock:
            for key, value in values.items():
                if hasattr(self, key) and key not in {"lock", "stats"}:
                    setattr(self, key, value)


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def set_vibration(user_index, left_motor, right_motor):
    vibration = XInputVibration(
        int(clamp(left_motor) * 65535),
        int(clamp(right_motor) * 65535),
    )
    return XInputSetState(int(user_index), ctypes.byref(vibration))


def is_controller_connected(index):
    state = XInputState()
    return XInputGetState(int(index), ctypes.byref(state)) == XINPUT_SUCCESS


def smooth_signal(current, target, attack, release):
    rate = attack if target > current else release
    return current + (target - current) * clamp(rate)


def map_value_gamma(value, threshold, in_max, gamma):
    if value <= threshold:
        return 0.0
    span = max(1.0, in_max - threshold)
    normalized = clamp((value - threshold) / span)
    return normalized ** max(0.1, gamma)


def band_rms(fft_data, freqs, low, high, n):
    mask = (freqs >= low) & (freqs <= high)
    if not np.any(mask):
        return 0.0
    band_fft = np.zeros_like(fft_data)
    band_fft[mask] = fft_data[mask]
    return float(np.sqrt(np.mean(np.square(np.fft.irfft(band_fft, n=n))) + 1e-10))


def classify_content(total_rms, vocal_rms, bass_rms, transient, previous_mode):
    if total_rms < 55:
        return "安静"

    total = max(total_rms, 1.0)
    bass_ratio = bass_rms / total
    vocal_ratio = vocal_rms / total

    music_score = bass_ratio * 1.25 + transient * 1.65
    asmr_score = vocal_ratio * 1.15 + (1.0 - min(transient * 2.4, 1.0)) * 0.55 - bass_ratio * 0.45

    if previous_mode == "音乐":
        music_score += 0.12
    elif previous_mode == "ASMR":
        asmr_score += 0.12

    if music_score > asmr_score + 0.16:
        return "音乐"
    if asmr_score > music_score + 0.10:
        return "ASMR"
    return previous_mode if previous_mode in {"音乐", "ASMR"} else "ASMR"


def find_loopback_device(audio, log_queue):
    loopback_devices = []
    for i in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(i)
        if info.get("isLoopbackDevice", False):
            loopback_devices.append(info)

    try:
        wasapi_info = audio.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_index = wasapi_info["defaultOutputDevice"]
        default_output = audio.get_device_info_by_index(default_index)
        log_queue.put(f"默认输出: {default_output['name']}")
        try:
            return audio.get_loopback_device_info_by_output_device(default_index)
        except Exception:
            for dev in loopback_devices:
                if default_output["name"] in dev["name"]:
                    return dev
    except Exception as exc:
        log_queue.put(f"读取 WASAPI 默认输出失败: {exc}")

    if loopback_devices:
        log_queue.put("未匹配默认扬声器，已使用第一个 Loopback 设备。")
        return loopback_devices[0]
    return None


def audio_vibration_thread(shared, log_queue, stop_event):
    chunk = 512
    audio = pyaudio.PyAudio()
    stream = None
    left_vib = 0.0
    right_vib = 0.0
    last_hotkey_pressed = False
    noise_floor = 0.0
    calibration_until = 0.0
    energy_avg = 0.0
    transient_avg = 0.0
    detected_mode = "识别中"

    try:
        loopback_device = find_loopback_device(audio, log_queue)
        if loopback_device is None:
            log_queue.put("错误: 找不到 WASAPI Loopback 设备，请确认系统输出设备可用。")
            return

        channels = int(loopback_device["maxInputChannels"])
        rate = int(loopback_device["defaultSampleRate"])
        with shared.lock:
            shared.stats.device_name = loopback_device["name"]
            shared.stats.effective_threshold = shared.vibration_threshold

        stream = audio.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=loopback_device["index"],
            frames_per_buffer=chunk,
        )
        log_queue.put(f"音频捕获已启动: {loopback_device['name']} / {rate} Hz / {channels} 声道")

        while not stop_event.is_set():
            snap = shared.snapshot()
            controller_index = snap["controller_index"]
            state = XInputState()
            connected = XInputGetState(controller_index, ctypes.byref(state)) == XINPUT_SUCCESS

            if connected:
                hotkey_pressed = bool(
                    state.Gamepad.wButtons & XINPUT_GAMEPAD_LEFT_SHOULDER
                    and state.Gamepad.wButtons & XINPUT_GAMEPAD_RIGHT_SHOULDER
                )
                if hotkey_pressed and not last_hotkey_pressed:
                    with shared.lock:
                        shared.enabled = not shared.enabled
                        snap["enabled"] = shared.enabled
                    log_queue.put(f"手柄快捷键 LB+RB: 震动{'开启' if snap['enabled'] else '暂停'}")
                last_hotkey_pressed = hotkey_pressed
            else:
                last_hotkey_pressed = False

            if snap["auto_calibrating"] and calibration_until == 0:
                calibration_until = time.monotonic() + 3.0
                noise_floor = 0.0
                log_queue.put("自动校准开始: 请保持当前背景音量 3 秒。")

            try:
                raw = stream.read(chunk, exception_on_overflow=False)
            except Exception as exc:
                log_queue.put(f"音频读取失败，正在继续尝试: {exc}")
                time.sleep(0.05)
                continue

            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            if channels > 1:
                samples = samples.reshape(-1, channels).mean(axis=1)

            n = len(samples)
            fft_data = np.fft.rfft(samples)
            freqs = np.fft.rfftfreq(n, 1 / rate)

            bass_rms = band_rms(fft_data, freqs, 20, snap["bass_high"], n)
            vocal_rms = band_rms(fft_data, freqs, snap["vocal_low"], snap["vocal_high"], n)
            total_rms = float(np.sqrt(np.mean(np.square(samples)) + 1e-10))
            energy_avg = energy_avg * 0.965 + total_rms * 0.035
            transient = clamp((total_rms - energy_avg) / max(energy_avg, 80.0), 0.0, 1.0)
            transient_avg = transient_avg * 0.82 + transient * 0.18

            if snap["auto_calibrating"]:
                noise_floor = max(noise_floor * 0.97, total_rms, vocal_rms)
                if time.monotonic() >= calibration_until:
                    calibrated = max(120.0, noise_floor * 1.8)
                    with shared.lock:
                        shared.vibration_threshold = calibrated
                        shared.auto_calibrating = False
                    calibration_until = 0.0
                    log_queue.put(f"自动校准完成: 触发阈值 {calibrated:.1f}")
            else:
                calibration_until = 0.0
                noise_floor = noise_floor * 0.995 + min(total_rms, vocal_rms) * 0.005

            adaptive_floor = noise_floor * 1.55 if snap["adaptive_threshold"] else 0.0
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
                        intensity=clamp(snap["intensity"] * 0.9, 0.1, 1.0),
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
                        intensity=clamp(snap["intensity"] * 0.48, 0.05, 0.72),
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

            def calc_vibration(rms):
                value = map_value_gamma(rms, threshold, effective["map_in_max"], effective["gamma_correction"])
                return clamp(value * effective["intensity"])

            if effective["dual_motor_mode"]:
                target_left = calc_vibration(vocal_rms)
                target_right = calc_vibration(bass_rms)
            else:
                source = vocal_rms if effective["vocal_only_mode"] else total_rms
                target_left = target_right = calc_vibration(source)

            beat_pulse = 0.0
            texture_level = 0.0
            now = time.monotonic()
            if snap["auto_profile"] and detected_mode == "音乐":
                beat_pulse = transient_avg * snap["rhythm_punch"] * snap["intensity"]
                target_right = clamp(target_right + beat_pulse * 0.55)
                target_left = clamp(target_left + beat_pulse * 0.20)
            elif snap["auto_profile"] and detected_mode == "ASMR":
                breath_envelope = clamp((vocal_rms - noise_floor * 0.85) / max(900.0, threshold * 4.0))
                micro_wave = 0.55 + 0.45 * math.sin(now * 2 * math.pi * 34.0)
                texture_level = breath_envelope * snap["texture_amount"] * 0.075 * micro_wave
                target_left = clamp(target_left + texture_level)
                target_right = clamp(target_right + texture_level * 0.82)

            left_vib = smooth_signal(left_vib, target_left, effective["attack_rate"], effective["release_rate"])
            right_vib = smooth_signal(right_vib, target_right, effective["attack_rate"], effective["release_rate"])

            if left_vib < effective["dead_zone"]:
                left_vib = 0.0
            if right_vib < effective["dead_zone"]:
                right_vib = 0.0

            if snap["enabled"] and connected:
                set_vibration(controller_index, left_vib, right_vib)
            else:
                set_vibration(controller_index, 0, 0)
                if not snap["enabled"]:
                    left_vib = right_vib = 0.0

            with shared.lock:
                shared.stats.total_rms = total_rms
                shared.stats.vocal_rms = vocal_rms
                shared.stats.bass_rms = bass_rms
                shared.stats.left_output = left_vib
                shared.stats.right_output = right_vib
                shared.stats.noise_floor = noise_floor
                shared.stats.effective_threshold = threshold
                shared.stats.controller_connected = connected
                shared.stats.peak_left = max(shared.stats.peak_left * 0.985, left_vib)
                shared.stats.peak_right = max(shared.stats.peak_right * 0.985, right_vib)
                shared.stats.detected_mode = detected_mode
                shared.stats.beat_strength = beat_pulse
                shared.stats.texture_level = texture_level

            time.sleep(0.002)
    except Exception as exc:
        log_queue.put(f"运行时错误: {exc}")
    finally:
        snap = shared.snapshot()
        set_vibration(snap["controller_index"], 0, 0)
        if stream is not None:
            stream.close()
        audio.terminate()
        log_queue.put("音频线程已停止，手柄震动已清零。")


class VerticalMeter(tk.Frame):
    def __init__(
        self,
        parent,
        label,
        color,
        max_value,
        value_format="{:.0f}",
        width=78,
        height=260,
        **kwargs,
    ):
        super().__init__(parent, bg="#151b24", **kwargs)
        self.max_value = max_value
        self.color = color
        self.value_format = value_format
        self.height = height
        self.width = width

        self.value_label = tk.Label(
            self,
            text=value_format.format(0),
            bg="#151b24",
            fg="#f8fafc",
            font=("Consolas", 13, "bold"),
        )
        self.value_label.pack(fill="x", pady=(0, 8))

        self.canvas = tk.Canvas(
            self,
            width=width,
            height=height,
            bg="#0d1118",
            bd=0,
            highlightthickness=1,
            highlightbackground="#293241",
        )
        self.canvas.pack()
        self.track = self.canvas.create_rectangle(20, 12, width - 20, height - 12, fill="#202938", outline="")
        self.fill = self.canvas.create_rectangle(20, height - 12, width - 20, height - 12, fill=color, outline="")
        self.peak = self.canvas.create_line(15, height - 12, width - 15, height - 12, fill="#f8fafc", width=2)
        for i in range(1, 5):
            y = 12 + (height - 24) * i / 5
            self.canvas.create_line(20, y, width - 20, y, fill="#2c3748")

        tk.Label(
            self,
            text=label,
            bg="#151b24",
            fg="#cbd5e1",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(fill="x", pady=(9, 0))

    def set(self, value, peak=None):
        ratio = clamp(value / max(1e-6, self.max_value))
        top = 12 + (self.height - 24) * (1 - ratio)
        bottom = self.height - 12
        self.canvas.coords(self.fill, 20, top, self.width - 20, bottom)
        if peak is not None:
            peak_ratio = clamp(peak / max(1e-6, self.max_value))
            peak_y = 12 + (self.height - 24) * (1 - peak_ratio)
            self.canvas.coords(self.peak, 15, peak_y, self.width - 15, peak_y)
        self.value_label.config(text=self.value_format.format(value))


class VibrationApp:
    def __init__(self, master):
        self.master = master
        self.shared = SharedVibrationParams()
        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.audio_thread = None
        self.test_thread = None

        self.configure_window()
        self.load_settings()
        self.create_widgets()
        self.sync_controls_from_params()
        self.start_engine()
        self.update_gui()

    def configure_window(self):
        self.master.title(APP_TITLE)
        self.master.geometry("1080x760")
        self.master.minsize(960, 680)
        self.master.configure(bg="#0b0f16")
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=("Microsoft YaHei UI", 9), background="#111827", foreground="#dbe4ef")
        style.configure("Dark.TFrame", background="#0b0f16")
        style.configure("Panel.TFrame", background="#151b24")
        style.configure("TLabel", background="#151b24", foreground="#dbe4ef")
        style.configure("Muted.TLabel", background="#151b24", foreground="#8fa3b8")
        style.configure("Value.TLabel", background="#151b24", foreground="#f8fafc", font=("Consolas", 10, "bold"))
        style.configure("TCheckbutton", background="#151b24", foreground="#dbe4ef")
        style.map("TCheckbutton", background=[("active", "#151b24")], foreground=[("active", "#ffffff")])
        style.configure("TButton", background="#233044", foreground="#f8fafc", borderwidth=0, padding=(12, 7))
        style.map("TButton", background=[("active", "#314158"), ("pressed", "#1f2937")])
        style.configure("Accent.TButton", background="#2563eb", foreground="#ffffff", font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Accent.TButton", background=[("active", "#1d4ed8"), ("pressed", "#1e40af")])
        style.configure("TCombobox", fieldbackground="#0f1622", background="#233044", foreground="#f8fafc", arrowcolor="#f8fafc")
        style.configure("Horizontal.TScale", background="#151b24", troughcolor="#293241")

    def load_settings(self):
        if not CONFIG_FILE.exists():
            return
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            self.shared.apply(data)
        except Exception as exc:
            self.log_queue.put(f"设置读取失败，已使用默认值: {exc}")

    def save_settings(self):
        snap = self.shared.snapshot()
        data = {k: v for k, v in snap.items() if k != "stats"}
        try:
            CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            self.log_queue.put(f"设置保存失败: {exc}")

    def create_widgets(self):
        shell = tk.Frame(self.master, bg="#0b0f16")
        shell.pack(fill="both", expand=True, padx=16, pady=16)
        shell.columnconfigure(0, minsize=360)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(1, weight=1)

        header = tk.Frame(shell, bg="#0b0f16")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        tk.Label(
            header,
            text="Xbox 音频震动控制台",
            bg="#0b0f16",
            fg="#f8fafc",
            font=("Microsoft YaHei UI", 22, "bold"),
        ).pack(side="left")
        self.status_label = tk.Label(
            header,
            text="正在启动...",
            bg="#122037",
            fg="#c7d2fe",
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=14,
            pady=8,
        )
        self.status_label.pack(side="right")

        left = tk.Frame(shell, bg="#0b0f16")
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        left.columnconfigure(0, weight=1)

        right = tk.Frame(shell, bg="#151b24", highlightthickness=1, highlightbackground="#293241")
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        device_box, device_panel = self.make_panel(left, "设备")
        device_box.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        device_panel.columnconfigure(1, weight=1)

        ttk.Label(device_panel, text="手柄").grid(row=0, column=0, sticky="w")
        self.controller_var = tk.StringVar(value="0")
        self.controller_box = ttk.Combobox(
            device_panel,
            textvariable=self.controller_var,
            values=["0", "1", "2", "3"],
            width=8,
            state="readonly",
        )
        self.controller_box.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        self.controller_box.bind("<<ComboboxSelected>>", lambda _e: self.set_controller())

        ttk.Label(device_panel, text="策略").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.profile_label = ttk.Label(device_panel, text="全自动识别", style="Value.TLabel")
        self.profile_label.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(10, 0))

        button_row = tk.Frame(device_panel, bg="#151b24")
        button_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        button_row.columnconfigure((0, 1), weight=1)
        ttk.Button(button_row, text="自动校准", command=self.start_calibration, style="Accent.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(button_row, text="测试震动", command=self.test_vibration).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        self.device_label = ttk.Label(device_panel, text="音频源: 初始化中...", style="Muted.TLabel", wraplength=310)
        self.device_label.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))

        mode_box, mode = self.make_panel(left, "模式")
        mode_box.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        mode.columnconfigure(0, weight=1)

        self.enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(mode, text="启用震动", variable=self.enabled_var, command=self.set_enabled).grid(row=0, column=0, sticky="w")
        self.auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(mode, text="全自动模式", variable=self.auto_var, command=self.set_auto_profile).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.add_hint(mode, 2, "自动判断音乐 / ASMR / 安静段，并在后台调整阈值、曲线、左右马达分配。")

        controls_box, controls = self.make_panel(left, "手感")
        controls_box.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        controls.columnconfigure(1, weight=1)

        self.sliders = {}
        slider_specs = [
            ("整体强度", "intensity", 0.1, 1.0, "{:.2f}", False, "控制所有震动的总体上限。太弱就加，太顶手就降。"),
            ("节奏力度", "rhythm_punch", 0.0, 1.0, "{:.2f}", False, "听歌时跟鼓点、贝斯、重拍走。越高越有节奏冲击，太高会糊。"),
            ("细腻麻感", "texture_amount", 0.0, 1.0, "{:.2f}", False, "ASMR/呼吸/轻声时的微颤细节。越高越麻，太高会像电流。"),
        ]
        for row, spec in enumerate(slider_specs):
            self.add_slider(controls, row, *spec)

        explain_box, explain = self.make_panel(left, "现在怎么工作")
        explain_box.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        self.add_hint(explain, 0, "音乐: 右马达跟低频和重拍，左马达补人声和节奏边缘，避免整段糊成一坨。")
        self.add_hint(explain, 1, "ASMR: 自动压低强度和死区，用轻微连续微颤表现呼吸、摩擦、耳语尾音。")
        self.add_hint(explain, 2, "安静段: 自动收住输出，避免背景噪声也把手柄带起来。")

        monitor_header = tk.Frame(right, bg="#151b24")
        monitor_header.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 4))
        tk.Label(
            monitor_header,
            text="实时动态条",
            bg="#151b24",
            fg="#f8fafc",
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(side="left")
        self.noise_label = tk.Label(
            monitor_header,
            text="背景噪声 0.0",
            bg="#151b24",
            fg="#8fa3b8",
            font=("Consolas", 10, "bold"),
        )
        self.noise_label.pack(side="right")

        monitor = tk.Frame(right, bg="#151b24")
        monitor.grid(row=1, column=0, sticky="nsew", padx=18, pady=(8, 14))
        for column in range(7):
            monitor.columnconfigure(column, weight=1)
        monitor.rowconfigure(0, weight=1)

        self.meters = {
            "total": VerticalMeter(monitor, "总音量", "#94a3b8", 6000, width=86),
            "vocal": VerticalMeter(monitor, "人声", "#22c55e", 6000, width=86),
            "bass": VerticalMeter(monitor, "低频", "#38bdf8", 6000, width=86),
            "beat": VerticalMeter(monitor, "节奏", "#f59e0b", 1.0, "{:.3f}", width=86),
            "texture": VerticalMeter(monitor, "细节", "#e879f9", 0.12, "{:.3f}", width=86),
            "left": VerticalMeter(monitor, "左马达", "#34d399", 1.0, "{:.3f}", width=96, height=300),
            "right": VerticalMeter(monitor, "右马达", "#60a5fa", 1.0, "{:.3f}", width=96, height=300),
        }
        for col, meter in enumerate(self.meters.values()):
            meter.grid(row=0, column=col, sticky="nsew", padx=7)

        log_frame = tk.Frame(right, bg="#0d1118", highlightthickness=1, highlightbackground="#293241")
        log_frame.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 18))
        tk.Label(
            log_frame,
            text="运行日志",
            bg="#0d1118",
            fg="#cbd5e1",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=(8, 0))
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=6,
            state="disabled",
            bg="#0d1118",
            fg="#d8e0ea",
            insertbackground="#ffffff",
            relief="flat",
            font=("Consolas", 9),
        )
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(6, 10))

    def make_panel(self, parent, title):
        outer = tk.Frame(parent, bg="#151b24", highlightthickness=1, highlightbackground="#293241")
        tk.Label(
            outer,
            text=title,
            bg="#151b24",
            fg="#f8fafc",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 6))
        body = tk.Frame(outer, bg="#151b24")
        body.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)
        return outer, body

    def add_hint(self, parent, row, text):
        tk.Label(
            parent,
            text=text,
            bg="#151b24",
            fg="#8fa3b8",
            wraplength=315,
            justify="left",
            anchor="w",
            font=("Microsoft YaHei UI", 8),
        ).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(2, 4))

    def add_slider(self, parent, row, label, attr, from_, to, fmt, is_int=False, help_text=""):
        base_row = row * 2
        ttk.Label(parent, text=label, style="Muted.TLabel").grid(row=base_row, column=0, sticky="w", pady=(5, 0))
        value_label = ttk.Label(parent, width=8, style="Value.TLabel")
        value_label.grid(row=base_row, column=2, sticky="e", padx=(8, 0), pady=(5, 0))
        scale = ttk.Scale(
            parent,
            from_=from_,
            to=to,
            orient="horizontal",
            command=lambda value, a=attr, f=fmt, i=is_int, l=value_label: self.set_numeric(a, value, f, i, l),
        )
        scale.grid(row=base_row, column=1, sticky="ew", padx=(10, 8), pady=(5, 0))
        if help_text:
            self.add_hint(parent, base_row + 1, help_text)
        self.sliders[attr] = (scale, value_label, fmt, is_int)

    def sync_controls_from_params(self):
        snap = self.shared.snapshot()
        self.enabled_var.set(snap["enabled"])
        self.controller_var.set(str(snap["controller_index"]))
        if hasattr(self, "auto_var"):
            self.auto_var.set(snap["auto_profile"])
        for attr, (scale, label, fmt, _is_int) in self.sliders.items():
            scale.set(snap[attr])
            label.config(text=fmt.format(snap[attr]))

    def start_engine(self):
        if self.audio_thread and self.audio_thread.is_alive():
            return
        self.stop_event.clear()
        self.audio_thread = threading.Thread(
            target=audio_vibration_thread,
            args=(self.shared, self.log_queue, self.stop_event),
            daemon=True,
        )
        self.audio_thread.start()

    def set_numeric(self, attr, value, fmt, is_int, label):
        value = int(float(value)) if is_int else float(value)
        with self.shared.lock:
            setattr(self.shared, attr, value)
        label.config(text=fmt.format(value))
        if attr == "vocal_low" and value >= self.shared.snapshot()["vocal_high"]:
            self.shared.apply({"vocal_high": value + 100})
            self.sync_controls_from_params()

    def set_enabled(self):
        with self.shared.lock:
            self.shared.enabled = self.enabled_var.get()

    def set_auto_profile(self):
        with self.shared.lock:
            self.shared.auto_profile = self.auto_var.get()
        self.profile_label.config(text="全自动识别" if self.auto_var.get() else "固定当前手感")

    def set_modes(self):
        with self.shared.lock:
            if hasattr(self, "dual_var"):
                self.shared.dual_motor_mode = self.dual_var.get()
            if hasattr(self, "vocal_var"):
                self.shared.vocal_only_mode = self.vocal_var.get()
            if hasattr(self, "adaptive_var"):
                self.shared.adaptive_threshold = self.adaptive_var.get()
        if hasattr(self, "vocal_check") and hasattr(self, "dual_var"):
            self.vocal_check.config(state="disabled" if self.dual_var.get() else "normal")

    def set_controller(self):
        with self.shared.lock:
            old_index = self.shared.controller_index
            self.shared.controller_index = int(self.controller_var.get())
        set_vibration(old_index, 0, 0)
        self.log_queue.put(f"当前手柄切换为 {self.controller_var.get()} 号。")

    def apply_preset(self):
        preset = PRESETS[self.preset_var.get()]
        self.shared.apply(preset)
        self.sync_controls_from_params()
        self.log_queue.put(f"已应用预设: {self.preset_var.get()}")

    def start_calibration(self):
        with self.shared.lock:
            self.shared.auto_calibrating = True

    def test_vibration(self):
        if self.test_thread and self.test_thread.is_alive():
            return

        def run():
            snap = self.shared.snapshot()
            index = snap["controller_index"]
            if not is_controller_connected(index):
                self.log_queue.put("测试失败: 当前手柄未连接。")
                return
            pattern = [(0.25, 0.0, 0.16), (0.0, 0.25, 0.16), (0.55, 0.55, 0.22), (0.0, 0.0, 0.05)]
            for left, right, duration in pattern:
                if self.stop_event.is_set():
                    break
                set_vibration(index, left, right)
                time.sleep(duration)
            set_vibration(index, 0, 0)
            self.log_queue.put("测试震动完成。")

        self.test_thread = threading.Thread(target=run, daemon=True)
        self.test_thread.start()

    def update_gui(self):
        snap = self.shared.snapshot()
        stats = snap["stats"]
        connected = "已连接" if stats["controller_connected"] else "未连接"
        running = self.audio_thread is not None and self.audio_thread.is_alive()
        auto_text = stats["detected_mode"] if snap["auto_profile"] else "手动"
        self.status_label.config(text=f"手柄 {snap['controller_index']}: {connected} | {auto_text} | {'运行中' if running else '已停止'}")
        self.device_label.config(text=f"音频源: {stats['device_name']}")
        self.noise_label.config(text=f"识别 {auto_text}  |  背景噪声 {stats['noise_floor']:.1f}")
        self.profile_label.config(text="全自动识别" if snap["auto_profile"] else "固定当前手感")

        self.meters["total"].set(stats["total_rms"])
        self.meters["vocal"].set(stats["vocal_rms"])
        self.meters["bass"].set(stats["bass_rms"])
        self.meters["beat"].set(stats["beat_strength"])
        self.meters["texture"].set(stats["texture_level"])
        self.meters["left"].set(stats["left_output"], stats["peak_left"])
        self.meters["right"].set(stats["right_output"], stats["peak_right"])

        while True:
            try:
                msg = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.append_log(msg)

        if snap["auto_calibrating"]:
            self.status_label.config(text=f"手柄 {snap['controller_index']}: {connected} | 正在校准")

        self.master.after(50, self.update_gui)

    def append_log(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_text.see(tk.END)
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 300:
            self.log_text.delete("1.0", "80.0")
        self.log_text.config(state="disabled")

    def on_closing(self):
        self.save_settings()
        self.stop_event.set()
        snap = self.shared.snapshot()
        set_vibration(snap["controller_index"], 0, 0)
        self.master.after(120, self.master.destroy)


if __name__ == "__main__":
    root = tk.Tk()
    app = VibrationApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
