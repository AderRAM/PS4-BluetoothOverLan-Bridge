#!/usr/bin/env python3
#
# Usage:
#   python3 receiver_linux.py              # key-learn on first run, saved for next runs
#   python3 receiver_linux.py --no-hotkeys # skip key-learn entirely (good for notebooks)
#   python3 receiver_linux.py --reverse    # invert gyro→mouse direction
#
# During key-learn, press ESC at any point to skip and run without hotkeys.
# F-keys (F1–F12) work on any keyboard.
#
import socket, struct, threading, time, sys, re, os, subprocess, glob
import evdev
from evdev import UInput, AbsInfo, ecodes as e

# ── Parse port from environment or argument ───────────────────────────────
_parser = __import__('argparse').ArgumentParser(description='PS4 Bluetooth Over Lan Bridge Receiver (Linux)')
_parser.add_argument('--port', type=int, default=int(os.environ.get('RECEIVER_PORT', '9999')),
                     help='UDP port to listen on (default: 9999 or RECEIVER_PORT env)')
_parser.add_argument('--no-hotkeys', action='store_true',
                     help='Skip key-learn entirely')
_parser.add_argument('--reverse', action='store_true',
                     help='Invert gyro→mouse direction')
parser_args = _parser.parse_args()
PORT = parser_args.port
NO_HOTKEYS = parser_args.no_hotkeys
REVERSE = parser_args.reverse
# ────────────────────────────────────────────────────────────────────────

PACKET_FMT  = '<HBBBBBbbBBiiiiiiB'
PACKET_SIZE = struct.calcsize(PACKET_FMT)

BTN_CROSS    = 0x0001;  BTN_CIRCLE   = 0x0002
BTN_SQUARE   = 0x0004;  BTN_TRIANGLE = 0x0008
BTN_L1       = 0x0010;  BTN_R1       = 0x0020
BTN_L3       = 0x0040;  BTN_R3       = 0x0080
BTN_SHARE    = 0x0100;  BTN_OPTIONS  = 0x0200

BUTTON_MAP = [
    (BTN_CROSS,    e.BTN_SOUTH),
    (BTN_CIRCLE,   e.BTN_EAST),
    (BTN_SQUARE,   e.BTN_WEST),
    (BTN_TRIANGLE, e.BTN_NORTH),
    (BTN_L1,       e.BTN_TL),
    (BTN_R1,       e.BTN_TR),
    (BTN_L3,       e.BTN_THUMBL),
    (BTN_R3,       e.BTN_THUMBR),
    (BTN_SHARE,    e.BTN_SELECT),
    (BTN_OPTIONS,  e.BTN_START),
]

state = dict(
    buttons=0, special=0,
    lx=128, ly=128, rx=128, ry=128,
    dx=0, dy=0, l2=0, r2=0,
    gx=0, gy=0, gz=0,
    ax=0, ay=0, az=0,
    battery=0,
    pps=0, addr='---', connected=False, usb=False,
)

# ── Gyro modes ────────────────────────────────────────────────────────────
GYRO_JOYSTICK    = False
GYRO_MOUSE       = False
GYRO_REVERSE     = '--reverse' in sys.argv or REVERSE
GYRO_STICK_SENS  = 1 / 45000
GYRO_MOUSE_SENS  = 1 / 4000
GYRO_DEAD        = 1200
STICK_MOUSE_SENS = 0.15
STICK_DEAD       = 12

# ── Key-learn ─────────────────────────────────────────────────────────────
# Scan codes for keys that shouldn't be hotkeys (letters, numbers,
# enter, backspace, space, modifiers, etc.). F-keys are NOT blocked.
_KEY_BLOCKLIST = {1, 14, 15, 28, 29, 42, 54, 56, 57, 58, 284, 285, 312,
                  *range(2, 14), *range(16, 26), *range(30, 39), *range(44, 51)}

_KEY_G6      = None   # gyro→stick toggle
_KEY_G5      = None   # mouse mode toggle
_KEY_G4      = None   # center mouse
_LEARN_STEP  = 0      # 0=G6 first  1=G6 confirm  2=G5  3=G4  4=done
_LEARN_CAND  = None
_LEARN_TIME  = 0.0
_LAST_KEY    = ''
_LAST_TOGGLE = 0.0
_COOLDOWN    = 0.4

_NO_HOTKEYS  = '--no-hotkeys' in sys.argv or NO_HOTKEYS

# Config file path: use XDG_CONFIG_HOME if available, otherwise ~/config
import pathlib
_XDG_CONFIG = os.environ.get('XDG_CONFIG_HOME')
_CONFIG_FILE = pathlib.Path(_XDG_CONFIG or os.path.expanduser('~/.config')) / 'ds4bridge' / 'hotkeys.conf'
CRASH_LOG    = pathlib.Path(os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.local/share/ds4bridge')) / 'crash.log'
pathlib.Path.mkdir(_CONFIG_FILE.parent, parents=True, exist_ok=True)
pathlib.Path.mkdir(CRASH_LOG.parent, parents=True, exist_ok=True)

# ── Hotkey config persistence ─────────────────────────────────────
def _save_hotkeys():
    try:
        with open(_CONFIG_FILE, 'w') as f:
            f.write(f'g6={_KEY_G6}\ng5={_KEY_G5}\ng4={_KEY_G4}\n')
    except Exception:
        pass

def _load_hotkeys():
    global _KEY_G6, _KEY_G5, _KEY_G4, _LEARN_STEP
    try:
        with open(_CONFIG_FILE) as f:
            cfg = dict(line.strip().split('=', 1) for line in f if '=' in line)
        _KEY_G6 = int(cfg['g6'])
        _KEY_G5 = int(cfg['g5'])
        _KEY_G4 = int(cfg['g4'])
        _LEARN_STEP = 4
        return True
    except Exception:
        return False

# ── Virtual devices ───────────────────────────────────────────────
def _make_gamepad():
    caps = {
        e.EV_KEY: [
            e.BTN_SOUTH, e.BTN_EAST, e.BTN_NORTH, e.BTN_WEST,
            e.BTN_TL, e.BTN_TR,
            e.BTN_THUMBL, e.BTN_THUMBR,
            e.BTN_SELECT, e.BTN_START, e.BTN_MODE,
        ],
        e.EV_ABS: [
            (e.ABS_X,     AbsInfo(128, 0, 255, 0, 15, 0)),
            (e.ABS_Y,     AbsInfo(128, 0, 255, 0, 15, 0)),
            (e.ABS_RX,    AbsInfo(128, 0, 255, 0, 15, 0)),
            (e.ABS_RY,    AbsInfo(128, 0, 255, 0, 15, 0)),
            (e.ABS_Z,     AbsInfo(0,   0, 255, 0,  0, 0)),
            (e.ABS_RZ,    AbsInfo(0,   0, 255, 0,  0, 0)),
            (e.ABS_HAT0X, AbsInfo(0,  -1,   1, 0,  0, 0)),
            (e.ABS_HAT0Y, AbsInfo(0,  -1,   1, 0,  0, 0)),
        ],
    }
    return UInput(caps, name='DS4Bridge Virtual DS4', vendor=0x054c, product=0x09cc)

def _make_mouse():
    caps = {
        e.EV_REL: [e.REL_X, e.REL_Y],
        e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT],
    }
    return UInput(caps, name='DS4Bridge Mouse')

_mouse_dev = None   # assigned in main()

def _mouse_btn_send(btn, pressed):
    if _mouse_dev:
        _mouse_dev.write(e.EV_KEY, btn, 1 if pressed else 0)
        _mouse_dev.syn()

def _center_mouse():
    try:
        r = subprocess.run(['xdotool', 'getdisplaygeometry'],
                           capture_output=True, text=True, timeout=2)
        if r.returncode == 0:
            w, h = map(int, r.stdout.strip().split())
            subprocess.run(['xdotool', 'mousemove', str(w // 2), str(h // 2)], timeout=2)
            return
    except Exception:
        pass
    try:
        subprocess.run(['ydotool', 'mousemove', '--absolute', '-x', '50%', '-y', '50%'],
                       timeout=2)
    except Exception:
        pass

# ── Keyboard monitoring (evdev, no extra deps) ────────────────────
def _find_keyboards():
    devs = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except Exception:
            continue
        caps = dev.capabilities()
        if e.EV_KEY in caps:
            keys = caps[e.EV_KEY]
            if e.KEY_A in keys and e.KEY_SPACE in keys:
                devs.append(dev)
                continue
        dev.close()
    return devs

def _on_key(code, is_down):
    global GYRO_JOYSTICK, GYRO_MOUSE
    global _KEY_G6, _KEY_G5, _KEY_G4, _LEARN_STEP
    global _LEARN_CAND, _LEARN_TIME, _LAST_KEY, _LAST_TOGGLE

    # ESC at any point during learn skips hotkey registration entirely
    if is_down and code == e.KEY_ESC and _LEARN_STEP < 4:
        _KEY_G6 = _KEY_G5 = _KEY_G4 = None
        _LEARN_STEP = 4
        return

    if code < 1 or code > 250:
        return
    if _LEARN_STEP < 4 and code in _KEY_BLOCKLIST:
        return

    if is_down:
        name = e.KEY.get(code, f'key:{code}')
        _LAST_KEY = f'{name} ({code})'

    now = time.time()

    if _LEARN_STEP == 0:                            # G6: press once
        if is_down:
            _LEARN_CAND = code; _LEARN_TIME = now; _LEARN_STEP = 1

    elif _LEARN_STEP == 1:                          # G6: confirm within 2s
        if is_down:
            if code == _LEARN_CAND and now - _LEARN_TIME < 2.0:
                _KEY_G6 = _LEARN_CAND; _LEARN_STEP = 2
            else:
                _LEARN_CAND = code; _LEARN_TIME = now

    elif _LEARN_STEP == 2:                          # G5: single press
        if is_down and code != _KEY_G6:
            _KEY_G5 = code; _LEARN_STEP = 3

    elif _LEARN_STEP == 3:                          # G4: single press
        if is_down and code not in (_KEY_G6, _KEY_G5):
            _KEY_G4 = code; _LEARN_STEP = 4
            _save_hotkeys()

    elif _LEARN_STEP == 4 and not _NO_HOTKEYS:
        if is_down and now - _LAST_TOGGLE >= _COOLDOWN:
            if code == _KEY_G6 and _KEY_G6 is not None:
                GYRO_JOYSTICK = not GYRO_JOYSTICK
                _LAST_TOGGLE = now
            elif code == _KEY_G5 and _KEY_G5 is not None:
                GYRO_MOUSE = not GYRO_MOUSE
                _LAST_TOGGLE = now
            elif code == _KEY_G4 and _KEY_G4 is not None:
                threading.Thread(target=_center_mouse, daemon=True).start()
                _LAST_TOGGLE = now

def _kb_reader(dev):
    try:
        for event in dev.read_loop():
            if event.type == e.EV_KEY and event.value in (0, 1):
                _on_key(event.code, event.value == 1)
    except Exception:
        pass

def _start_keyboard_monitor():
    if _NO_HOTKEYS:
        return
    devs = _find_keyboards()
    if not devs:
        global _LEARN_STEP
        _LEARN_STEP = 4  # no keyboards detected, skip silently
    for dev in devs:
        threading.Thread(target=_kb_reader, args=(dev,), daemon=True).start()

# ── Gyro → mouse loop ─────────────────────────────────────────────
def _mouse_gyro_loop():
    acc_x = acc_y = 0.0
    while True:
        s = dict(state)
        if GYRO_MOUSE:
            if GYRO_JOYSTICK:
                ox = s['rx'] - 128
                oy = s['ry'] - 128
                fx = 0.0 if abs(ox) < STICK_DEAD else ox * STICK_MOUSE_SENS
                fy = 0.0 if abs(oy) < STICK_DEAD else oy * STICK_MOUSE_SENS
            else:
                rev  = -1.0 if GYRO_REVERSE else 1.0
                gy_v = s['gy']
                gx_v = s['gx']
                fx = 0.0 if abs(gy_v) < GYRO_DEAD else -gy_v * GYRO_MOUSE_SENS * rev
                fy = 0.0 if abs(gx_v) < GYRO_DEAD else -gx_v * GYRO_MOUSE_SENS * rev
        else:
            fx = fy = 0.0

        acc_x += fx
        acc_y += fy
        dx, dy = int(acc_x), int(acc_y)
        acc_x -= dx
        acc_y -= dy

        if (dx or dy) and _mouse_dev:
            if dx: _mouse_dev.write(e.EV_REL, e.REL_X, dx)
            if dy: _mouse_dev.write(e.EV_REL, e.REL_Y, dy)
            _mouse_dev.syn()

        time.sleep(1 / 120)

# ── Gamepad output ────────────────────────────────────────────────
def _clamp8(v):  return max(0, min(255, int(v)))

def push_gamepad(gamepad, s):
    for pb, db in BUTTON_MAP:
        gamepad.write(e.EV_KEY, db, 1 if s['buttons'] & pb else 0)
    gamepad.write(e.EV_KEY, e.BTN_MODE, 1 if s['special'] & 0x01 else 0)

    gamepad.write(e.EV_ABS, e.ABS_HAT0X, s['dx'])
    gamepad.write(e.EV_ABS, e.ABS_HAT0Y, s['dy'])
    gamepad.write(e.EV_ABS, e.ABS_Z,     s['l2'])
    gamepad.write(e.EV_ABS, e.ABS_RZ,    s['r2'])
    gamepad.write(e.EV_ABS, e.ABS_X,     s['lx'])
    gamepad.write(e.EV_ABS, e.ABS_Y,     s['ly'])

    if GYRO_MOUSE and GYRO_JOYSTICK:
        rx = _clamp8(128 + s['gy'] * GYRO_STICK_SENS * 127)
        ry = _clamp8(128 - s['gx'] * GYRO_STICK_SENS * 127)
    else:
        rx, ry = s['rx'], s['ry']
    gamepad.write(e.EV_ABS, e.ABS_RX, rx)
    gamepad.write(e.EV_ABS, e.ABS_RY, ry)

    gamepad.syn()

def reset_gamepad(gamepad):
    for _, db in BUTTON_MAP:
        gamepad.write(e.EV_KEY, db, 0)
    gamepad.write(e.EV_KEY, e.BTN_MODE, 0)
    gamepad.write(e.EV_ABS, e.ABS_HAT0X, 0)
    gamepad.write(e.EV_ABS, e.ABS_HAT0Y, 0)
    gamepad.write(e.EV_ABS, e.ABS_Z,     0)
    gamepad.write(e.EV_ABS, e.ABS_RZ,    0)
    gamepad.write(e.EV_ABS, e.ABS_X,     128)
    gamepad.write(e.EV_ABS, e.ABS_Y,     128)
    gamepad.write(e.EV_ABS, e.ABS_RX,    128)
    gamepad.write(e.EV_ABS, e.ABS_RY,    128)
    gamepad.syn()

def open_steam_overlay():
    try:
        kb_caps = {e.EV_KEY: [e.KEY_LEFTSHIFT, e.KEY_TAB]}
        with UInput(kb_caps, name='DS4Bridge Keyboard') as kb:
            kb.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
            kb.write(e.EV_KEY, e.KEY_TAB, 1)
            kb.syn()
            time.sleep(0.05)
            kb.write(e.EV_KEY, e.KEY_TAB, 0)
            kb.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
            kb.syn()
    except Exception:
        pass

# ── USB DS4 detection (pause bridge if real DS4 is plugged in here) ─
def _ds4_usb_connected():
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except Exception:
            continue
        info = dev.info
        is_ds4 = info.vendor == 0x054c and info.product in (0x09cc, 0x05c4)
        phys   = dev.phys or ''
        dev.close()
        if is_ds4 and 'usb' in phys.lower():
            return True
    return False

def _usb_watch_loop(gamepad):
    was_usb = False
    while True:
        is_usb = _ds4_usb_connected()
        if is_usb and not was_usb:
            state['usb'] = True
            state['connected'] = False
            reset_gamepad(gamepad)
        elif not is_usb and was_usb:
            state['usb'] = False
        was_usb = is_usb
        time.sleep(2)

# ── Receive loop ──────────────────────────────────────────────────
PACKET_TIMEOUT = 2.5

def recv_loop(gamepad, sock):
    count        = 0
    last_pps     = time.time()
    last_packet  = time.time()
    last_gamepad = 0.0
    ps_prev      = False
    l1_prev      = False
    r1_prev      = False
    timed_out    = False

    sock.settimeout(0.5)

    while True:
        try:
            data, addr = sock.recvfrom(64)
            if len(data) < PACKET_SIZE:
                continue

            last_packet = time.time()
            if timed_out:
                timed_out = False

            if state.get('usb'):
                continue

            buttons, special, lx, ly, rx, ry, dx, dy, l2, r2, gx, gy, gz, ax, ay, az, bat = \
                struct.unpack(PACKET_FMT, data[:PACKET_SIZE])

            mouse_active    = GYRO_MOUSE
            gamepad_buttons = buttons
            if mouse_active:
                l1_now = bool(buttons & BTN_L1)
                r1_now = bool(buttons & BTN_R1)
                if l1_now and not l1_prev: _mouse_btn_send(e.BTN_LEFT,  True)
                if not l1_now and l1_prev: _mouse_btn_send(e.BTN_LEFT,  False)
                if r1_now and not r1_prev: _mouse_btn_send(e.BTN_RIGHT, True)
                if not r1_now and r1_prev: _mouse_btn_send(e.BTN_RIGHT, False)
                l1_prev, r1_prev = l1_now, r1_now
                gamepad_buttons &= ~(BTN_L1 | BTN_R1)
            else:
                # Release held mouse buttons immediately when mode turns off
                if l1_prev: _mouse_btn_send(e.BTN_LEFT,  False)
                if r1_prev: _mouse_btn_send(e.BTN_RIGHT, False)
                l1_prev = r1_prev = False

            state.update(
                buttons=buttons, special=special,
                lx=lx, ly=ly, rx=rx, ry=ry,
                dx=int(dx), dy=int(dy), l2=l2, r2=r2,
                gx=gx, gy=gy, gz=gz, ax=ax, ay=ay, az=az,
                battery=bat,
                addr=addr[0], connected=True,
            )

            ps_now = bool(special & 0x01)
            if ps_now and not ps_prev:
                threading.Thread(target=open_steam_overlay, daemon=True).start()
            ps_prev = ps_now

            now = time.time()
            if now - last_gamepad >= 1 / 120:
                gs = dict(state)
                gs['buttons'] = gamepad_buttons
                push_gamepad(gamepad, gs)
                last_gamepad = now

            count += 1
            if now - last_pps >= 1.0:
                state['pps'] = count
                count    = 0
                last_pps = now

        except OSError:
            pass
        except Exception:
            import traceback
            try:
                with open(CRASH_LOG, 'a') as f:
                    f.write(traceback.format_exc() + '\n')
            except Exception:
                pass

        if not timed_out and time.time() - last_packet > PACKET_TIMEOUT:
            timed_out = True
            state['connected'] = False
            state['pps'] = 0
            reset_gamepad(gamepad)

# ── ANSI TUI ──────────────────────────────────────────────────────
_STRIP = re.compile(r'\033\[[0-9;?]*[a-zA-Z]')
def vlen(s):  return len(_STRIP.sub('', s))
def C(c):     return f'\033[{c}m'
RST  = C(0);    BOLD = C(1)
GRN  = C('1;32');  CYN  = C('1;36')
YLW  = C('1;33');  GRY  = C('2;37')

W = 72

def row(content):
    p = (W - 2) - vlen(content)
    return f'║{content}{" " * max(0, p)}║'

def sep(l='╠', r='╣'):
    return l + '═' * (W - 2) + r

def b(label, on):
    return f'{GRN}[{label}]{RST}' if on else f'{GRY}[{label}]{RST}'

def hbar(val, w=12, maxv=255):
    f = min(w, round((val / maxv) * w))
    return CYN + '█' * f + GRY + '░' * (w - f) + RST

def cbar(val, w=12, scale=32767):
    half = w // 2
    n    = max(-1.0, min(1.0, val / scale))
    if n >= 0:
        f = round(n * half)
        return GRY + '░'*half + CYN + '█'*f + GRY + '░'*(half - f) + RST
    else:
        f = round(-n * half)
        return GRY + '░'*(half - f) + CYN + '█'*f + GRY + '░'*half + RST

def stick_grid(x, y):
    IW, IH = 9, 3
    cx = round((x / 255) * (IW - 1))
    cy = round((y / 255) * (IH - 1))
    if abs(x - 128) <= 6: cx = (IW - 1) // 2
    if abs(y - 128) <= 6: cy = (IH - 1) // 2
    top = '┌' + '─' * IW + '┐'
    bot = '└' + '─' * IW + '┘'
    rows = [top]
    for r in range(IH):
        line = '│'
        for c in range(IW):
            line += (CYN + '●' + RST) if (c == cx and r == cy) else ' '
        line += '│'
        rows.append(line)
    rows.append(bot)
    return rows

def render():
    s  = dict(state)
    bt = s['buttons']
    sp = s['special']
    dx, dy = s['dx'], s['dy']

    cross    = bool(bt & BTN_CROSS);   circle   = bool(bt & BTN_CIRCLE)
    square   = bool(bt & BTN_SQUARE);  triangle = bool(bt & BTN_TRIANGLE)
    l1  = bool(bt & BTN_L1);   r1  = bool(bt & BTN_R1)
    l3  = bool(bt & BTN_L3);   r3  = bool(bt & BTN_R3)
    shr = bool(bt & BTN_SHARE); opt = bool(bt & BTN_OPTIONS)
    ps    = bool(sp & 0x01)
    touch = bool(sp & 0x02)

    bat = s['battery']
    bcol = GRN if bat > 50 else (YLW if bat > 20 else C('1;31'))
    bstr = f'{bcol}[ {bat}% ]{RST}' if s['connected'] else f'{GRY}[ --% ]{RST}'

    out = []
    out.append('╔' + '═' * (W - 2) + '╗')
    out.append(row(f'  {BOLD}DS4 Bridge{RST}  ◆  Live Input Monitor   {bstr}'))
    out.append(sep())

    out.append(row(
        f'  {b("△",triangle)}{b("□",square)}{b("✕",cross)}{b("○",circle)}'
        f'      {b("↑", dy==-1)}'
        f'       {b("L1",l1)}{b("R1",r1)}'
        f'   {b("L3",l3)}{b("R3",r3)}'
    ))
    out.append(row(
        f'                  '
        f'{b("←",dx==-1)}{GRY}·{RST}{b("→",dx==1)}'
        f'       {b("L2",s["l2"]>10)}{b("R2",s["r2"]>10)}'
        f'   {b("SHR",shr)}{b("OPT",opt)}'
    ))
    out.append(row(
        f'                    {b("↓",dy==1)}'
        f'                        {b("PS",ps)}{b("TP",touch)}'
    ))
    out.append(sep())

    lg = stick_grid(s['lx'], s['ly'])
    rg = stick_grid(s['rx'], s['ry'])
    out.append(row(f'  LEFT STICK              RIGHT STICK'))
    for i in range(len(lg)):
        out.append(row(f'  {lg[i]}              {rg[i]}'))
    out.append(row(f'  X:{s["lx"]:3d}  Y:{s["ly"]:3d}             X:{s["rx"]:3d}  Y:{s["ry"]:3d}'))
    out.append(row(
        f'  L2 {hbar(s["l2"])} {s["l2"]:3d}'
        f'          '
        f'R2 {hbar(s["r2"])} {s["r2"]:3d}'
    ))
    out.append(sep())

    out.append(row(f'  {YLW}GYROSCOPE{RST}                         {YLW}ACCELEROMETER{RST}'))
    gyro  = [s['gy'], s['gx'], s['gz']]
    accel = [s['ax'], s['ay'], s['az']]
    for axis, gv, av in zip(('X', 'Y', 'Z'), gyro, accel):
        out.append(row(
            f'  {axis} {cbar(gv, scale=200000)} {gv:+9d}'
            f'       '
            f'{axis} {cbar(av, scale=32768)} {av:+7d}'
        ))
    out.append(sep())

    if s.get('usb'):
        dot   = YLW + '⬡' + RST
        label = YLW + 'USB DIRECT  (bridge paused)' + RST
    elif s['connected']:
        dot   = GRN + '●' + RST
        label = GRN + 'BRIDGE ACTIVE' + RST
    else:
        dot   = GRY + '○' + RST
        label = GRY + 'WAITING...' + RST
    out.append(row(f'  {dot} {label}   {s["addr"]}   {s["pps"]} pkt/s'))

    # Hotkey status line
    if _NO_HOTKEYS or (_LEARN_STEP == 4 and _KEY_G6 is None):
        learn = f'  {GRY}Hotkeys disabled  (run without --no-hotkeys to enable){RST}'
    elif _LEARN_STEP == 0:
        learn = YLW + '  Register G6: press any key twice  (F-keys work, ESC to skip)' + RST
    elif _LEARN_STEP == 1:
        learn = YLW + f'  G6: press the same key again to confirm  [{_LAST_KEY}]' + RST
    elif _LEARN_STEP == 2:
        learn = YLW + '  G6 registered. Now press G5 key...  (ESC to skip)' + RST
    elif _LEARN_STEP == 3:
        learn = YLW + '  G5 registered. Now press G4 key (centers mouse)...  (ESC to skip)' + RST
    else:
        g6str  = (GRN + 'G6:STICK' + RST) if GYRO_JOYSTICK else (GRY + 'G6:GYRO' + RST)
        g5str  = (GRN + 'G5:ON'    + RST) if GYRO_MOUSE    else (GRY + 'G5:OFF'  + RST)
        revstr = (YLW + ' REV' + RST) if GYRO_REVERSE else ''
        learn  = f'  {g6str}   {g5str}{revstr}   G4:center   last: {_LAST_KEY}'
    out.append(row(learn))
    out.append('╚' + '═' * (W - 2) + '╝')

    return '\n'.join(out)

# ── Main ──────────────────────────────────────────────────────────
def main():
    global _mouse_dev, _LEARN_STEP

    if not os.access('/dev/uinput', os.W_OK):
        print("Cannot write to /dev/uinput. Make sure you are in the 'input' group:")
        print("  sudo usermod -aG input $USER  (then log out and back in)")
        print("Or add a udev rule:")
        print("  echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\"' | sudo tee /etc/udev/rules.d/99-uinput.rules")
        print("  sudo udevadm control --reload-rules && sudo udevadm trigger")
        sys.exit(1)

    if _NO_HOTKEYS:
        _LEARN_STEP = 4
    elif not _load_hotkeys():
        _LEARN_STEP = 0  # start key-learn

    gamepad    = _make_gamepad()
    _mouse_dev = _make_mouse()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', PORT))

    threading.Thread(target=recv_loop,       args=(gamepad, sock), daemon=True).start()
    threading.Thread(target=_mouse_gyro_loop,                      daemon=True).start()
    threading.Thread(target=_usb_watch_loop, args=(gamepad,),      daemon=True).start()
    _start_keyboard_monitor()

    sys.stdout.write('\033[?1049h\033[?25l\033[2J\033[H')
    sys.stdout.flush()

    try:
        while True:
            try:
                sys.stdout.write('\033[H' + render())
                sys.stdout.flush()
            except KeyboardInterrupt:
                raise
            except Exception:
                import traceback
                try:
                    with open(CRASH_LOG, 'a') as f:
                        f.write(traceback.format_exc())
                except Exception:
                    pass
                raise
            time.sleep(1 / 30)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write('\033[?25h\033[0m\033[?1049l')
        sys.stdout.flush()
        try: gamepad.close()
        except Exception: pass
        try: _mouse_dev.close()
        except Exception: pass

if __name__ == '__main__':
    import traceback, signal, faulthandler
    _fh = open(CRASH_LOG, 'a')
    faulthandler.enable(file=_fh)

    def _sig_handler(sig, frame):
        with open(CRASH_LOG, 'a') as f:
            f.write(f'Signal {sig} received\n')
            traceback.print_stack(frame, file=f)
        sys.exit(0)

    for _s in (signal.SIGTERM, signal.SIGINT):
        try: signal.signal(_s, _sig_handler)
        except Exception: pass

    try:
        main()
    except Exception:
        try:
            sys.stdout.write('\033[?25h\033[0m\033[?1049l')
            sys.stdout.flush()
        except Exception:
            pass
        tb = traceback.format_exc()
        with open(CRASH_LOG, 'a') as f:
            f.write(tb)
        print(tb)