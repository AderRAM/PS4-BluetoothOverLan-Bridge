# PS4-BluetoothOverLan-Bridge

A bridge application that connects a PlayStation 4 (PS4) DualShock 4 controller to a PC, enabling it to function as a gamepad, presentation remote, or mouse/keyboard input device.

## Quick commands

| Task | Command |
|---|---|
| Start receiver (Linux notebook) | `python3 receiver_linux.py` [--no-hotkeys] [--reverse] |
| Start receiver (Windows) | `python3 receiver.py` — must run as admin for SendInput to work |
| Start sender via SSH | `python3 start_sender.py` |
| Presentation remote | `python3 presenter.py` |

## Architecture

Two main executables:

- **receiver.py** (Windows) / **receiver_linux.py** (Linux). Listens on `0.0.0.0:9999` for UDP packets, translates to gamepad/mouse/keyboard input. Windows uses `vgamepad` + `SendInput`; Linux uses `evdev` UInput virtual devices.

- **presenter.py** — Standalone presentation remote for LibreOffice Impress (no network required). Two modes: Mode 0 = slide navigation, Mode 1 = gyroscope mouse control. Mode toggled via L3.

### Packet format

UDP packets: `<HBBBBBbbBBiiiiiiB` (27 bytes)

| Bytes | Field |
|---|---|
| 2 | buttons |
| 1 | special |
| 3 | lx, ly, rx, ry (analog sticks) |
| 2 | dx, dy (touchpad/dpad) |
| 1 | l2, r2 (triggers) |
| 3 | gx, gy, gz (gyroscope) |
| 3 | ax, ay, az (accelerometer) |
| 1 | battery (0-100%) |

- `special` bit 0 = PS button, bit 2 = touchpad
- `gx` = ABS_RX (pitch → mouse Y), `gz` = ABS_RZ (yaw → mouse X)

**Note**: The bind address (`0.0.0.0:9999`) and broadcast address are configured in the Python source. To use different ports or addresses, edit the respective `PACKET_FMT` bind/binding in the receiver/sender code.

## Key-learn hotkey setup

All receivers use a three-key hotkey configuration via physical DS4 buttons. On first run, the key-learn sequence starts automatically:

1. Press **G6** once → device waits
2. Press the **same key twice within 2 seconds** → assigns as G6 (gyro→stick toggle)
3. Press a **different key once** → assigns as G5 (mouse mode toggle)
4. Press a **different key once** → assigns as G4 (center mouse)

- G6: Toggle gyro→stick (G5 determines source: stick or gyro)
- G5: Toggle mouse mode on/off (master switch)
- G4: Center mouse cursor

**Note**: Hotkey configuration path is configured in the Python source (default: `~/.config/ds4bridge/hotkeys.conf` on Linux). To use a different path or reset, edit the config file or run the receiver with `--no-hotkeys` to skip registration and start fresh.

To reconfigure or reset: run receiver with `--no-hotkeys` to skip, then re-run to start key-learn again, or press **ESC** at any point during key-learn to skip entirely.

## Platform-specific requirements

### Windows (receiver.py)
- **Must run as administrator** — `SendInput` is blocked by UIPI for non-elevated processes focused on elevated windows
- Requires `vgamepad` package
- Registers global hotkeys via `RegisterHotKey` (WM_HOTKEY)

### Linux (receiver_linux.py)
- Must have write access to `/dev/uinput` — add user to `input` group:
  ```bash
  sudo usermod -aG input $USER
  # then log out and back in
  # Or add a udev rule:
  echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | sudo tee /etc/udev/rules.d/99-uinput.rules
  sudo udevadm control --reload-rules && sudo udevadm trigger
  ```
- Optional: `xdotool`, `ydotool` for mouse center (`--no-hotkeys` skips key-learn entirely)
- F-keys work without registration

**Note**: The config file path, broadcast IP, and SSH target are all set in the Python source. To adapt to different networks, edit the respective constants in the sender/receiver code.

## Operational gotchas

- **Packet timeout**: If UDP packets stop for >2.5 seconds, the receiver resets the virtual controller (`PACKET_TIMEOUT = 2.5`)
- **USB DS4 detection**: If a real DS4 is plugged in via USB, the bridge pauses (both Linux and Windows receivers check for this)
- **Battery polling**: Polls `/sys/class/power_supply/ps-controller-battery-*/capacity` every 10s; if the path doesn't exist, battery shows `--`
- **Gyro deadzone**: `GYRO_DEAD = 1200` (raw units); rest noise peaks at ~900 — values below this are treated as zero
- **Key-blocklist**: During key-learn, certain scan codes (letters, numbers, modifiers) are blocked; F-keys always work. ESC skips key-learn entirely.
- **Gyro reverse**: Pass `--reverse` to invert gyro→mouse direction (point up → cursor down)
- **L1/R1 suppression**: When mouse mode is active, L1/R1 are suppressed from gamepad output but trigger mouse buttons
- **Heartbeat**: Sender streams state at 20Hz; receiver processes at up to 120Hz
- **Thread safety**: Shared `state` dict uses a `threading.Lock()` in sender; receiver reads atomically (GIL + individual int assignments)

**Note**: All constants (port numbers, deadzone values, paths) are defined in the Python source files. To use this repository on different computers, edit the relevant constants in `sender.py`, `receiver.py`, `receiver_linux.py`, or `presenter.py` rather than relying on documented values.

## Directory ownership

- `receiver.py` — Windows side (owns vgamepad, SendInput, hotkey registration)
- `receiver_linux.py` — Linux side (owns UInput devices, keyboard monitor, gyro loop)
- `presenter.py` — Standalone presentation remote (no network, LibreOffice Impress control)
- `start_sender.py` — SSH orchestration (connects via paramiko, starts sender on remote host)
- `ds4presenter_26-06-22/` — Alternative standalone presenter implementation (separate UI + logic)
- `sender.py` — DS4 input broadcaster (runs `evdev` + UDP broadcast; configurable bind address and port)