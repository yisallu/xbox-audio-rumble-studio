# Xbox Audio Rumble Studio

[中文说明](README.zh-CN.md)

Xbox Audio Rumble Studio is a clean, lightweight Windows desktop utility that turns live system audio into Xbox controller vibration. It listens to the default WASAPI loopback device, analyzes volume and frequency bands in real time, and drives an XInput-compatible controller with natural left/right motor feedback.

It is designed for music, ASMR, videos, games, and any other audio source playing on your PC.

## Features

- **Minimalist Dark Studio UI**: Clean, distraction-free card layout with `4x LANCZOS` anti-aliased Apple-style switches (`动效` / `震动`), high-DPI scaling, one-click scene presets, and precision feel sliders
- **ProMotion Adaptive VSync Visualizer**: Hardware `DwmFlush()` VBlank-synced 3-disc radial visualizer + clockwise motor fans that automatically match your monitor's refresh rate (`60Hz` / `120Hz` / `130Hz` / `144Hz`, `< 2.5%` CPU at full `130 FPS`, `0 FPS` / `~0.06%` CPU when paused or toggled off)
- **Double-Click Per-Pixel Transparent Floating HUD**: Double-click the center visualizer sphere to hide all window chrome and backgrounds, leaving only the glowing 3-ring visualizer floating transparently on top of any screen content (freely draggable, click-through gaps, double-click again to restore seamlessly)
- **LB + RB Controller Shortcut**: Press **LB + RB** simultaneously on your Xbox controller at any time to toggle vibration on or off (works even when audio is paused or minimized, with subtle haptic confirmation)
- **Live Bluetooth & Headphone Hot-Switch**: Queries Windows CoreAudio active endpoints directly so switching Bluetooth headphones (Bose / AirPods / FreeBuds) or speakers reconnects automatically within ~1s, with non-compressing low-volume auto-gain and `< 55ms` instant pause brake
- **True Stereo Directional & Pure Onset Haptics**: Independent Left/Right channel Hann-windowed FFT + adaptive baseline cancellation (`fast_env - bg_env`) — vibrates sharply on drum hits, plucks, spoken syllables, and binaural triggers with true left/right ear spatial directionality, settling to `0.0%` stillness within `~100ms` on sustained pads, chords, or white noise
- **Session-Scale Smart Auto & 5 Independent Presets**: `2048`-point (`23.4 Hz` bin) long-window session classifier calmly locks onto *Music*, *Vocal / Podcast*, *ASMR / Quiet*, or *Game / Cinema* with subtle top-pill status indication and zero mid-session jitter; every mode independently remembers your custom slider tweaks with one-click per-mode **Restore Default**
- **One-Tap Ambient Calibration & Persistence**: Samples 3 seconds of background noise to set an optimal trigger threshold and saves per-mode profiles automatically

## Requirements

- Windows 10 / 11
- Python 3.10+
- An Xbox-compatible XInput controller
- Audio playing through a Windows playback device

## Download Standalone EXE

You can download the pre-built standalone Windows executable directly (no Python environment or extraction needed):

- [Download XboxAudioRumbleStudio.exe (Latest Release)](https://github.com/yisallu/xbox-audio-rumble-studio/releases/latest/download/XboxAudioRumbleStudio.exe)

## Install from Source

Clone the repository:

```powershell
git clone https://github.com/yisallu/xbox-audio-rumble-studio.git
cd xbox-audio-rumble-studio
```

Install the Python dependencies:

```powershell
pip install numpy pyaudiowpatch pillow
```

## Run

```powershell
python .\xbox6.py
```

## Usage

1. Connect an Xbox-compatible controller.
2. Start audio playback on Windows.
3. Launch the app with `python .\xbox6.py`.
4. Keep **Smart Auto** active or pick a preset card (*Vocal Clarity*, *Bass Immersion*, *Soft Texture*, *Full-Band Punch*).
5. Adjust **Intensity**, **Rhythm Punch**, and **Subtle Texture** sliders to taste.
6. Press **LB + RB** together on the controller (or click the top-right switch / press `Space` in the window) whenever you want to toggle vibration on or off.

The app stores your latest settings next to the script as `xbox6.settings.json`.

## Notes

- Uses Windows XInput and WASAPI APIs (Windows-only).
- If the controller is not detected, reconnect it or check whether another controller mapper is taking exclusive control.
- Vibration is automatically zeroed when toggled off or when the app closes.

## License

No license has been added yet. All rights reserved unless a license file is added later.
