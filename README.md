# Xbox Audio Rumble Studio

A Windows Python/Tkinter app that maps system audio to Xbox controller vibration through XInput.

## Features

- Live audio loopback monitoring
- Xbox controller vibration through XInput
- Music, ASMR, and quiet-scene auto profiling
- Tunable intensity, rhythm, texture, threshold, attack, and release controls
- Local JSON settings persistence

## Requirements

- Windows
- Python 3.10+
- An Xbox-compatible XInput controller

Install Python dependencies:

```powershell
pip install numpy pyaudiowpatch
```

Run:

```powershell
python .\xbox6.py
```
