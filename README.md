# Control Cursor with Hand 🖐️

Control your Mac cursor with hand gestures using your webcam — no hardware required.

Built with [MediaPipe](https://mediapipe.dev/) for hand tracking, [PyQt5](https://pypi.org/project/PyQt5/) for the glow overlay, and native [Quartz](https://developer.apple.com/documentation/coregraphics) events for pixel-perfect mouse input.

https://github.com/user-attachments/assets/control-cursor-with-hand.mp4

---

## Features

- **Full cursor control** — move your hand, the cursor follows the midpoint between index and thumb
- **Left click & drag** — pinch index + thumb; hold to drag, release to drop
- **Double click** — two quick pinches within 350 ms
- **Right click (hold)** — pinch ring finger + thumb; hold for context menus
- **Right click (instant)** — tap pinky + thumb for a quick right-click tap
- **Middle click / scroll** — pinch middle finger + thumb
- **Glow cursor overlay** — a floating translucent circle that follows your hand, color-coded by gesture
- **Debug preview** — a small camera thumbnail in the bottom-left corner with skeleton overlay, distances and FPS
- **Menu bar icon** — control the app from the macOS status bar without switching windows
- **Always-on-top, never steals focus** — works over any app without blocking keyboard input or textarea focus

---

## Gesture Map

| Gesture | Action | Glow color |
|---|---|---|
| Move hand | Move cursor | 🔵 Blue |
| Index + thumb pinch | Left click / drag | 🔴 Red |
| Index + thumb (double pinch) | Double click | 🔴 Red |
| Middle finger + thumb | Middle click (scroll) | 🟢 Green |
| Ring finger + thumb (hold) | Right click (sustained) | 🟣 Purple |
| Pinky + thumb (tap) | Right click (instant) | 🟣 Purple |
| Mouse control OFF | Paused | ⚪ Gray |

---

## Requirements

- **macOS** — tested on Apple Silicon (M1/M2/M3) and Intel, Sonoma 14+
- **Python 3.9 – 3.11** (MediaPipe does not yet support 3.12+)
- Webcam

---

## Installation

```bash
git clone https://github.com/tecnomanu/control-cursor-with-hand.git
cd control-cursor-with-hand

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> The first run will automatically download `hand_landmarker.task` (~8 MB) from Google's MediaPipe model registry.

---

## macOS Permissions (required)

The first time you run the app, macOS will request two permissions. If the dialogs don't appear, grant them manually at **System Settings → Privacy & Security**:

| Permission | Why |
|---|---|
| **Camera** | Capture video for hand detection |
| **Accessibility** | Move the cursor and send mouse events |

After granting Accessibility, you may need to restart Terminal.

---

## Running

```bash
source .venv/bin/activate
python hand_control.py
```

A glow circle will appear on screen following your hand. A small debug preview appears in the bottom-left corner.

### Menu bar controls

Click the **blue dot** in the macOS menu bar:

- **Mouse: ON / OFF** — pause cursor control without closing the app
- **Debug: ON / OFF** — show or hide the camera preview
- **Quit** — close everything

### Keyboard shortcuts (click the debug preview first to focus it)

| Key | Action |
|---|---|
| `D` | Toggle debug preview |
| `M` | Toggle mouse control |
| `ESC` / `Cmd+Q` | Quit |

---

## Tuning

All constants are at the top of `hand_control.py`:

| Constant | Default | Effect |
|---|---|---|
| `PINCH_ON_THRESHOLD` | `0.055` | How close fingers must be to trigger a click. Lower = more sensitive |
| `PINCH_OFF_THRESHOLD` | `0.085` | How far fingers must separate to release. Higher = more hysteresis |
| `SMOOTHING` | `0.15` | Cursor smoothing factor. Lower = smoother but slower to react |
| `DEADZONE_PX` | `5` | Minimum movement in screen pixels before cursor moves. Kills micro-jitter |
| `EDGE_MARGIN` | `0.12` | Camera edge crop. Higher = easier to reach screen corners |
| `DOUBLE_CLICK_INTERVAL` | `0.35` | Max seconds between two pinches to count as double click |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Cursor doesn't move | Grant **Accessibility** permission to Terminal |
| Camera doesn't open | Grant **Camera** permission; check no other app is using it |
| Too much jitter | Raise `DEADZONE_PX` to 8–10 or lower `SMOOTHING` to 0.10 |
| Accidental clicks | Raise `PINCH_ON_THRESHOLD` (e.g. `0.055` → `0.07`) |
| Can't reach screen corners | Raise `EDGE_MARGIN` (e.g. `0.12` → `0.18`) |
| Overlay hides on click | Already fixed via `orderFrontRegardless` + `setHidesOnDeactivate: NO` |
| Can't focus text fields | The glow overlay is fully click-through (`ignoresMouseEvents: YES`) |

---

## How it works

```
Webcam → OpenCV → MediaPipe HandLandmarker (21 landmarks)
       → midpoint(index, thumb) → smoothing + deadzone → Quartz CGEvent
       → kCGEventMouseMoved / kCGEventLeftMouseDragged / kCGEventLeftMouseDown …
```

- **Tracking**: `RunningMode.VIDEO` for frame-by-frame tracking with temporal continuity
- **Cursor position**: midpoint between index and thumb tip — stays stable when pinching because both fingers move symmetrically toward center
- **Click events**: sent via Quartz `CGEventPost(kCGHIDEventTap)` so macOS treats them as real hardware events, enabling proper drag, text selection, and double-click
- **Overlay**: PyQt5 `QWidget` with `WA_TranslucentBackground`, `ignoresMouseEvents: YES`, and `NSFloatingWindowLevel` — floats above all apps without blocking input or stealing keyboard focus

---

## Stack

- [MediaPipe Tasks](https://developers.google.com/mediapipe/solutions/vision/hand_landmarker) — hand landmark detection
- [OpenCV](https://opencv.org/) — camera capture and debug drawing
- [PyQt5](https://pypi.org/project/PyQt5/) — glow cursor overlay and debug preview
- [Quartz / CoreGraphics](https://developer.apple.com/documentation/coregraphics) — native macOS mouse events

---

## License

MIT
