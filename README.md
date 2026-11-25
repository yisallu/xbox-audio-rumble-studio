# Xbox Audio Rumble Studio

[中文说明](README.zh-CN.md)

Xbox Audio Rumble Studio is a clean, lightweight Windows desktop utility that turns live system audio into Xbox controller vibration. It listens to the default WASAPI loopback device, analyzes volume and frequency bands in real time, and drives an XInput-compatible controller with natural left/right motor feedback.

It is designed for music, ASMR, videos, games, and any other audio source playing on your PC.

## Features

- **Clean, Human-Friendly Dark UI**: Streamlined card-based interface with high-DPI scaling, one-click scene modes, percentage feel sliders, and real-time response meters with a visual threshold marker
- **LB + RB Controller Shortcut**: Press **LB + RB** simultaneously on your Xbox controller at any time to toggle vibration on or off (works even when audio is paused, with subtle haptic confirmation when enabled)
- **Non-Blocking WASAPI Loopback Capture**: Smoothly tracks system audio and automatically reconnects if you switch playback devices (e.g., speakers to headphones)
- **Smart Auto Mode & Scene Presets**: Automatically adapts to Music, ASMR, or Quiet scenes, or switch with one click between *Vocal Clarity*, *Bass Immersion*, *Soft Texture*, and *Full-Band Punch*
- **Intuitive Feel Controls**: Adjust overall intensity, rhythm punch, and subtle texture right from the main window, while advanced curve/frequency settings and runtime logs are neatly tucked into an optional *Advanced & Logs* dialog
- **One-Tap Ambient Calibration**: Samples 3 seconds of background noise to set an optimal trigger threshold automatically
- **Settings Persistence**: Automatically saves your preferences to `xbox6.settings.json`

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
pip install numpy pyaudiowpatch
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
