# Xbox Audio Rumble Studio

Xbox Audio Rumble Studio is a Windows desktop app that turns live system audio into Xbox controller vibration. It listens to the default WASAPI loopback device, analyzes volume and frequency bands in real time, then drives an XInput-compatible controller with tunable left/right motor feedback.

It is designed for music, ASMR, videos, games, and any other audio source playing on the PC.

## Features

- Live Windows system-audio capture through WASAPI loopback
- XInput vibration output for Xbox-compatible controllers
- Automatic content profiling for music, ASMR, and quiet scenes
- Presets for vocal clarity, low-frequency immersion, soft texture, and full-band punch
- Tunable overall intensity, rhythm punch, texture amount, threshold, attack, release, and gamma response
- Dual-motor mode plus vocal-focused mode
- Live visual meters and runtime status logs
- Local JSON settings persistence in `xbox6.settings.json`

## Requirements

- Windows
- Python 3.10+
- An Xbox-compatible XInput controller
- Audio playing through a Windows playback device

## Install

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
4. Choose a preset or keep auto profile enabled.
5. Adjust intensity, rhythm, texture, and response controls until the vibration feels right.

The app stores the latest settings next to the script as `xbox6.settings.json`.

## Notes

- The app uses Windows XInput libraries, so it is Windows-only.
- If the controller is not detected, reconnect it or check whether another controller tool is taking exclusive control.
- Use moderate intensity for long sessions.
- Stop vibration from the app before closing if a controller is actively rumbling.

## Project Status

This is a small standalone Python app. The current code is intentionally kept in one file so it is easy to run, inspect, and modify locally.

## License

No license has been added yet. All rights reserved unless a license file is added later.
