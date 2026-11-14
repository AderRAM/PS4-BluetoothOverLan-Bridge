import socket, struct, threading, time, sys, re, ctypes, os
import keyboard
import vgamepad as vg

# ── Parse port from environment or argument ─────────────────────────────
_parser = __import__('argparse').ArgumentParser(description='PS4 Bluetooth Over Lan Bridge Receiver')
_parser.add_argument('--port', type=int, default=int(os.environ.get('RECEIVER_PORT', '9999')),
                     help='UDP port to listen on (default: 9999 or RECEIVER_PORT env)')
_parser_args = _parser.parse_args()
PORT = _parser_args.port
# ────────────────────────────────────────────────────────────────────────

# ── Crash log path (Option A: env-variable-friendly) ──────────────────
# On Windows uses %APPDATA%\ds4bridge\, on other OS uses ~/local/share/ds4bridge.
# Directory is created automatically if it does not exist.
if sys.platform == 'win32':
    _crash_base = os.environ.get('APPDATA', os.path.expanduser('~'))
else:
    _crash_base = os.path.expanduser('~/.local/share')
_CRASH_LOG = os.path.join(_crash_base, 'ds4bridge', 'crash.log')
os.makedirs(os.path.dirname(_CRASH_LOG), exist_ok=True)
CRASH_LOG = _CRASH_LOG  # export for use throughout the file
# ────────────────────────────────────────────────────────────────────────

# ── SendInput for mouse buttons (works in focused game windows) ─
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx',          ctypes.c_long),
                ('dy',          ctypes.c_long),
                ('mouseData',   ctypes.c_ulong),
                ('dwFlags',     ctypes.c_ulong),
                ('time',        ctypes.c_ulong),
                ('dwExtraInfo', ctypes.c_size_t)]   # ULONG_PTR — 8 bytes on 64-bit

class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [('mi', _MOUSEINPUT)]
    _anonymous_ = ('_u',)
    _fields_    = [('type', ctypes.c_ulong), ('_u', _U)]

_LDOWN  = 0x0002;  _LUP  = 0x0004
_RDOWN  = 0x0008;  _RUP  = 0x0010

def _mouse_btn(flags):
    i = _INPUT(type=0)
    i.mi.dwFlags = flags
    ctypes.windll.user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(_INPUT))

def _enable_vt100():
    kernel32 = ctypes.windll.kernel32
    handle   = kernel32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
    mode     = ctypes.c_ulong()
    kernel32.GetConsoleMode(handle, ctypes.byref(mode))
    kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING

_enable_vt100()
sys.stdout.reconfigure(encoding='utf-8')

PACKET_FMT  = '<HBBBBBbbBBiiiiiiB'
PACKET_SIZE = struct.calcsize(PACKET_FMT)

BTN_CROSS    = 0x0001;  BTN_CIRCLE   = 0x0002
BTN_SQUARE   = 0x0004;  BTN_TRIANGLE = 0x0008
BTN_L1       = 0x0010;  BTN_R1       = 0x0020
BTN_L3       = 0x0040;  BTN_R3       = 0x0080
BTN_SHARE    = 0x0100;  BTN_OPTIONS  = 0x0200

B  = vg.DS4_BUTTONS
SB = vg.DS4_SPECIAL_BUTTONS
DD = vg.DS4_DPAD_DIRECTIONS

BUTTON_MAP = [
    (BTN_CROSS,    B.DS4_BUTTON_CROSS),
    (BTN_CIRCLE,   B.DS4_BUTTON_CIRCLE),
    (BTN_SQUARE,   B.DS4_BUTTON_SQUARE),
    (BTN_TRIANGLE, B.DS4_BUTTON_TRIANGLE),
    (BTN_L1,       B.DS4_BUTTON_SHOULDER_LEFT),
    (BTN_R1,       B.DS4_BUTTON_SHOULDER_RIGHT),
    (BTN_L3,       B.DS4_BUTTON_THUMB_LEFT),
    (BTN_R3,       B.DS4_BUTTON_THUMB_RIGHT),
    (BTN_SHARE,    B.DS4_BUTTON_SHARE),
    (BTN_OPTIONS,  B.DS4_BUTTON_OPTIONS),
]

DPAD_MAP = {
    ( 0,  0): DD.DS4_BUTTON_DPAD_NONE,
    ( 0, -1): DD.DS4_BUTTON_DPAD_NORTH,
    ( 1, -1): DD.DS4_BUTTON_DPAD_NORTHEAST,
    ( 1,  0): DD.DS4_BUTTON_DPAD_EAST,
    ( 1,  1): DD.DS4_BUTTON_DPAD_SOUTHEAST,
    ( 0,  1): DD.DS4_BUTTON_DPAD_SOUTH,
    (-1,  1): DD.DS4_BUTTON_DPAD_SOUTHWEST,
    (-1,  0): DD.DS4_BUTTON_DPAD_WEST,
    (-1, -1): DD.DS4_BUTTON_DPAD_NORTHWEST,
}

state = dict(
    buttons=0, special=0,
    lx=128, ly=128, rx=128, ry=128,
    dx=0, dy=0, l2=0, r2=0,
    gx=0, gy=0, gz=0,
    ax=0, ay=0, az=0,
    battery=0,
    pps=0, addr='---', connected=False, usb=False,
)

# ── Gyro modes (independent) ───────────────────────────────────
# G6: gyro REPLACES right stick (swap joystick ↔ gyro for in-game aim)
# G5: gyro → system mouse cursor (toggle on/off, independent of G6)
GYRO_JOYSTICK    = False
GYRO_MOUSE       = False
GYRO_REVERSE     = '--reverse' in sys.argv   # invert gyro→mouse direction (point up → cursor down)
GYRO_STICK_SENS  = 1 / 45000    # gyro → right stick fraction (-1..1)
GYRO_MOUSE_SENS  = 1 / 4000     # gyro → pixels/update (accumulates sub-pixels)
GYRO_DEAD        = 1200          # gyro deadzone — rest noise peaks at ~900
STICK_MOUSE_SENS = 0.15          # stick offset (-128..127) → pixels/update
STICK_DEAD       = 12            # stick deadzone — rest jitter is ~3

# Key-learn: require pressing the same key TWICE within 2s to confirm
_KEY_G6       = None
_KEY_G5       = None
_KEY_G4       = None
_LEARN_STEP   = 0      # 0=G6  1=G6 confirm  2=G5 (single)  3=G4 (single)  4=done

_KEY_BLOCKLIST = {1, 14, 15, 28, 29, 42, 54, 56, 57, 58, 284, 285, 312,
                  *range(2, 14), *range(16, 26), *range(30, 39), *range(44, 51)}
_LEARN_CAND   = None   # candidate scan code waiting for confirmation
_LEARN_TIME   = 0.0    # time of first press
_LAST_KEY     = ''
_LAST_TOGGLE  = 0.0    # cooldown: ignore rapid re-triggers
_COOLDOWN     = 0.4    # seconds between allowed toggles

_HOTKEY_HWND = None

def _start_hotkeys():
    """Register G5/G6 as exclusive system hotkeys via RegisterHotKey — works over any focused window."""
    import ctypes.wintypes

    def _loop():
        global _HOTKEY_HWND
        u32 = ctypes.windll.user32
        # Create a message-only window to receive WM_HOTKEY
        hwnd = u32.CreateWindowExW(0, 'STATIC', None, 0, 0, 0, 0, 0,
                                   ctypes.cast(-3, ctypes.wintypes.HWND),  # HWND_MESSAGE
                                   None, None, None)
        _HOTKEY_HWND = hwnd
        # Convert scan codes to virtual keys
        sc_to_vk = lambda sc: u32.MapVirtualKeyW(sc, 1)  # MAPVK_VSC_TO_VK
        vk6 = sc_to_vk(_KEY_G6)
        vk5 = sc_to_vk(_KEY_G5)
        vk4 = sc_to_vk(_KEY_G4)
        u32.RegisterHotKey(hwnd, 6, 0, vk6)   # id=6 → G6
        u32.RegisterHotKey(hwnd, 5, 0, vk5)   # id=5 → G5
        u32.RegisterHotKey(hwnd, 4, 0, vk4)   # id=4 → G4 (center mouse)
        msg = ctypes.wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), hwnd, 0, 0) != 0:
            if msg.message == 0x0312:           # WM_HOTKEY
                global GYRO_JOYSTICK, GYRO_MOUSE, _LAST_TOGGLE
                now = time.time()
                if now - _LAST_TOGGLE >= _COOLDOWN:
                    if msg.wParam == 6:
                        GYRO_JOYSTICK = not GYRO_JOYSTICK
                    elif msg.wParam == 5:
                        GYRO_MOUSE = not GYRO_MOUSE
                    elif msg.wParam == 4:
                        w = u32.GetSystemMetrics(0)
                        h = u32.GetSystemMetrics(1)
                        u32.SetCursorPos(w // 2, h // 2)
                    _LAST_TOGGLE = now
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))

    threading.Thread(target=_loop, daemon=True).start()

def _on_key(ev):
    global GYRO_JOYSTICK, GYRO_MOUSE
    global _KEY_G6, _KEY_G5, _KEY_G4, _LEARN_STEP, _LEARN_CAND, _LEARN_TIME, _LAST_KEY, _LAST_TOGGLE

    sc      = ev.scan_code
    is_down = ev.event_type == keyboard.KEY_DOWN

    # Reject gamepad/virtual controller events (negative or out of range)
    if sc is None or sc < 1 or sc > 250:
        return

    if _LEARN_STEP < 4 and sc in _KEY_BLOCKLIST:
        return  # reject during registration only

    if is_down:
        _LAST_KEY = f'{ev.name} (scan:{sc})'
    now = time.time()

    if _LEARN_STEP == 0:                          # G6: press once
        if is_down:
            _LEARN_CAND = sc; _LEARN_TIME = now; _LEARN_STEP = 1

    elif _LEARN_STEP == 1:                        # G6: confirm (press again within 2s)
        if is_down:
            if sc == _LEARN_CAND and now - _LEARN_TIME < 2.0:
                _KEY_G6 = _LEARN_CAND; _LEARN_STEP = 2
            else:
                _LEARN_CAND = sc; _LEARN_TIME = now

    elif _LEARN_STEP == 2:                        # G5: single press
        if is_down and sc != _KEY_G6:
            _KEY_G5 = sc; _LEARN_STEP = 3

    elif _LEARN_STEP == 3:                        # G4: single press
        if is_down and sc not in (_KEY_G6, _KEY_G5):
            _KEY_G4 = sc; _LEARN_STEP = 4
            _start_hotkeys()
            keyboard.unhook_all()  # RegisterHotKey takes over — no more global hook needed

    else:                                         # _LEARN_STEP == 4: registered
        if sc in (_KEY_G6, _KEY_G5, _KEY_G4):
            return False  # suppress — handled by RegisterHotKey thread

    # All other keys pass through unchanged

def _mouse_gyro_loop():
    u32   = ctypes.windll.user32
    acc_x = 0.0   # sub-pixel accumulator
    acc_y = 0.0

    while True:
        s = dict(state)

        if GYRO_MOUSE:                        # G5 = master switch
            if GYRO_JOYSTICK:               # G6 = source: stick
                ox = s['rx'] - 128
                oy = s['ry'] - 128
                fx = 0.0 if abs(ox) < STICK_DEAD else ox * STICK_MOUSE_SENS
                fy = 0.0 if abs(oy) < STICK_DEAD else oy * STICK_MOUSE_SENS
            else:                           # G6 = source: gyro
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

        if dx or dy:
            i = _INPUT(type=0)
            i.mi.dwFlags = 0x0001   # MOUSEEVENTF_MOVE
            i.mi.dx = dx
            i.mi.dy = dy
            ctypes.windll.user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(_INPUT))

        time.sleep(1 / 120)

# ── ANSI ──────────────────────────────────────────────────────
_STRIP = re.compile(r'\033\[[0-9;?]*[a-zA-Z]')
def vlen(s):  return len(_STRIP.sub('', s))
def C(c):     return f'\033[{c}m'
RST  = C(0);    BOLD = C(1)
GRN  = C('1;32');  CYN  = C('1;36')
YLW  = C('1;33');  GRY  = C('2;37')

W = 72  # total width including ║ borders

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
    # Apply small deadzone so natural drift doesn't move dot off-center
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
    return rows   # 5 lines, 11 chars wide each

# ── Gamepad update ─────────────────────────────────────────────
from vgamepad.win.vigem_commons import DS4_REPORT_EX as _DS4_REPORT_EX
import struct as _struct
_ext_report = _DS4_REPORT_EX()

def _clamp8(v):  return max(0, min(255, int(v)))
def _clamp16(v): return max(-32768, min(32767, int(v)))

def _write_motion(gx, gy, gz, ax, ay, az):
    # Write gyro/accel directly at correct DS4 USB HID byte offsets
    # DS4 report: gyroX at byte 13, not 14 (struct has 1-byte padding before wGyroX)
    _struct.pack_into('<hhhhhh',
                      _ext_report.ReportBuffer, 13,
                      _clamp16(gx), _clamp16(gy), _clamp16(gz),
                      _clamp16(ax), _clamp16(ay), _clamp16(az))

MOTION_DEADZONE      = 20
MOTION_BURST         = 28000   # gyro peak on first push (±32767 max)
MOTION_HOLD          = 8000    # gyro value while stick held
MOTION_BURST_MS      = 0.25
_motion_burst_start  = 0.0

def push_gamepad(gamepad, s):
    global _motion_burst_start
    ds4_btns = 0
    for pb, db in BUTTON_MAP:
        if s['buttons'] & pb:
            ds4_btns |= int(db)
    dpad = int(DPAD_MAP.get((s['dx'], s['dy']), DD.DS4_BUTTON_DPAD_NONE))
    sp = 0
    if s['special'] & 0x01: sp |= int(SB.DS4_SPECIAL_BUTTON_PS)
    if s['special'] & 0x02: sp |= int(SB.DS4_SPECIAL_BUTTON_TOUCHPAD)

    r = _ext_report.Report
    r.wButtons  = dpad | ds4_btns
    r.bSpecial  = sp
    r.bTriggerL = s['l2']
    r.bTriggerR = s['r2']
    r.bThumbLX  = s['lx']
    r.bThumbLY  = s['ly']

    if GYRO_MOUSE and GYRO_JOYSTICK:
        r.bThumbRX = _clamp8(128 + s['gy'] * GYRO_STICK_SENS * 127)
        r.bThumbRY = _clamp8(128 - s['gx'] * GYRO_STICK_SENS * 127)
    else:
        r.bThumbRX = s['rx']
        r.bThumbRY = s['ry']

    # Motion: right stick → gyro/accel with burst on first push
    sx = s['rx'] - 128
    sy = s['ry'] - 128
    if abs(sx) < MOTION_DEADZONE: sx = 0
    if abs(sy) < MOTION_DEADZONE: sy = 0

    if sx != 0 or sy != 0:
        # Normalise direction (-1..1)
        nx = sx / 127.0
        ny = sy / 127.0
        # Burst on leading edge, then hold
        in_burst = (time.time() - _motion_burst_start) < MOTION_BURST_MS
        gyro_mag = MOTION_BURST if in_burst else MOTION_HOLD
        scale    = 8192
        _write_motion(
            int(-ny * gyro_mag), int(nx * gyro_mag), s['gz'] // 64,
            int(nx * scale), int(scale * (1.0 - abs(ny))), int(-ny * scale)
        )
    else:
        _motion_burst_start = time.time()
        _write_motion(
            s['gx'] // 64, s['gy'] // 64, s['gz'] // 64,
            s['ax'], s['ay'], s['az']
        )

    # Battery: write the real level into the DS4 report (raw HID offset 30) so Steam
    # shows the true charge instead of reading 0 and spamming "battery low 5%".
    pct = s.get('battery', 0)
    _ext_report.ReportBuffer[30] = max(1, min(10, (pct + 5) // 10)) if pct > 0 else 0

    try:
        gamepad.update_extended_report(_ext_report)
    except Exception:
        pass

def open_steam_overlay():
    u32 = ctypes.windll.user32
    SHIFT, TAB = 0x10, 0x09
    DOWN, UP   = 0x0000, 0x0002
    u32.keybd_event(SHIFT, 0, DOWN, 0)
    u32.keybd_event(TAB,   0, DOWN, 0)
    time.sleep(0.05)
    u32.keybd_event(TAB,   0, UP, 0)
    u32.keybd_event(SHIFT, 0, UP, 0)

def reset_gamepad(gamepad):
    r = gamepad.report
    r.wButtons = r.bLeftTrigger = r.bRightTrigger = 0
    r.sThumbLX = r.sThumbLY = r.sThumbRX = r.sThumbRY = 0
    gamepad.update()

def _ds4_usb_connected():
    """True if a real DS4 is physically plugged into this PC via USB."""
    import subprocess
    try:
        out = subprocess.check_output(
            ['powershell', '-Command',
             'Get-PnpDevice | Where-Object { $_.InstanceId -like "*VID_054C*PID_09CC*" '
             '-and $_.InstanceId -like "*USB*" -and $_.Status -eq "OK" } | Measure-Object | Select-Object -Expand Count'],
            text=True, timeout=3, creationflags=0x08000000
        )
        return int(out.strip() or 0) > 0
    except Exception:
        return False

def _usb_watch_loop(gamepad):
    """Monitors for DS4 USB connection; resets virtual controller when USB takes over."""
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

# ── Receive thread ─────────────────────────────────────────────
PACKET_TIMEOUT = 2.5   # seconds before virtual controller is reset

def recv_loop(gamepad, sock):
    count       = 0
    last_pps      = time.time()
    last_packet   = time.time()
    last_gamepad  = 0.0
    ps_prev       = False
    l1_prev       = False
    r1_prev       = False
    timed_out     = False

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

            mouse_active = GYRO_MOUSE   # G5 is the master switch

            # L1/R1 → mouse buttons when mouse mode is on
            gamepad_buttons = buttons   # separate copy for gamepad (may suppress L1/R1)
            if mouse_active:
                l1_now = bool(buttons & BTN_L1)
                r1_now = bool(buttons & BTN_R1)
                if l1_now and not l1_prev: _mouse_btn(_LDOWN)
                if not l1_now and l1_prev: _mouse_btn(_LUP)
                if r1_now and not r1_prev: _mouse_btn(_RDOWN)
                if not r1_now and r1_prev: _mouse_btn(_RUP)
                l1_prev, r1_prev = l1_now, r1_now
                gamepad_buttons &= ~(BTN_L1 | BTN_R1)  # suppress only from gamepad

            # state keeps raw buttons so monitor always shows real input
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


            now2 = time.time()
            if now2 - last_gamepad >= 1/120:   # cap at 120Hz
                gs = dict(state)
                gs['buttons'] = gamepad_buttons
                push_gamepad(gamepad, gs)
                last_gamepad = now2
            count += 1
            now = time.time()
            if now - last_pps >= 1.0:
                state['pps'] = count
                count    = 0
                last_pps = now

        except OSError:
            pass   # socket timeout — fall through to check packet age
        except Exception:
            import traceback
            with open(CRASH_LOG, 'a', encoding='utf-8') as f:
                f.write(traceback.format_exc() + '\n')

        # Reset virtual controller when packets stop arriving
        if not timed_out and time.time() - last_packet > PACKET_TIMEOUT:
            timed_out = True
            state['connected'] = False
            state['pps']       = 0
            reset_gamepad(gamepad)

# ── Render ─────────────────────────────────────────────────────
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

    # ── Buttons ───────────────────────────────────────────────
    #  face                dpad              shoulder / misc
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

    # ── Sticks ────────────────────────────────────────────────
    lg = stick_grid(s['lx'], s['ly'])
    rg = stick_grid(s['rx'], s['ry'])
    out.append(row(f'  LEFT STICK              RIGHT STICK'))
    for i in range(len(lg)):
        out.append(row(f'  {lg[i]}              {rg[i]}'))
    out.append(row(f'  X:{s["lx"]:3d}  Y:{s["ly"]:3d}             X:{s["rx"]:3d}  Y:{s["ry"]:3d}'))

    # triggers on their own line
    out.append(row(
        f'  L2 {hbar(s["l2"])} {s["l2"]:3d}'
        f'          '
        f'R2 {hbar(s["r2"])} {s["r2"]:3d}'
    ))
    out.append(sep())

    # ── Gyro / Accel ──────────────────────────────────────────
    out.append(row(f'  {YLW}GYROSCOPE{RST}                         {YLW}ACCELEROMETER{RST}'))
    # Gyro X/Y are swapped vs user's intuition — fix display only.
    # Accel axes are correct as-is (gravity lands on Y when controller is upright).
    gyro  = [s['gy'], s['gx'], s['gz']]   # X/Y swapped for gyro
    accel = [s['ax'], s['ay'], s['az']]   # accel unchanged
    for axis, gv, av in zip(('X', 'Y', 'Z'), gyro, accel):
        out.append(row(
            f'  {axis} {cbar(gv, scale=200000)} {gv:+9d}'
            f'       '
            f'{axis} {cbar(av, scale=32768)} {av:+7d}'
        ))
    out.append(sep())

    # ── Status ────────────────────────────────────────────────
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

    # Gyro mode + key-learn status
    if _LEARN_STEP == 0:
        learn = YLW + '  Press G6 once...' + RST
    elif _LEARN_STEP == 1:
        learn = YLW + f'  G6: press again to confirm  [{_LAST_KEY}]' + RST
    elif _LEARN_STEP == 2:
        learn = YLW + '  G6 registered. Press G5...' + RST
    elif _LEARN_STEP == 3:
        learn = YLW + '  G5 registered. Press G4... (centers mouse)' + RST
    else:
        g6str = (GRN + 'G6:STICK' + RST) if GYRO_JOYSTICK else (GRY + 'G6:GYRO' + RST)
        g5str = (GRN + 'G5:ON'    + RST) if GYRO_MOUSE    else (GRY + 'G5:OFF'  + RST)
        revstr = (YLW + ' REV' + RST) if GYRO_REVERSE else ''
        learn = f'  {g6str}   {g5str}{revstr}   G4:center   last: {_LAST_KEY}'
    out.append(row(learn))
    out.append('╚' + '═' * (W - 2) + '╝')

    return '\n'.join(out)

# ── Main ───────────────────────────────────────────────────────
def main():
    gamepad = vg.VDS4Gamepad()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', PORT))

    threading.Thread(target=recv_loop,       args=(gamepad, sock), daemon=True).start()
    threading.Thread(target=_mouse_gyro_loop,                      daemon=True).start()
    threading.Thread(target=_usb_watch_loop, args=(gamepad,),      daemon=True).start()
    keyboard.on_press(_on_key)

    # Enter alternate screen — cursor hidden, clean slate, no bleed into shell
    sys.stdout.write('\033[?1049h\033[?25l\033[2J\033[H')
    sys.stdout.flush()

    CRASH_LOG = _CRASH_LOG
    try:
        while True:
            try:
                sys.stdout.write('\033[H' + render())
                sys.stdout.flush()
            except KeyboardInterrupt:
                raise
            except Exception:
                import traceback
                with open(CRASH_LOG, 'a', encoding='utf-8') as f:
                    f.write(traceback.format_exc())
                raise
            time.sleep(1 / 30)  # outside inner try — safe, sleep never throws
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
    _ensure_admin()
    import traceback, signal, faulthandler
    CRASH_LOG = _CRASH_LOG

    # Catch C-level crashes (segfault, access violation)
    _fh = open(CRASH_LOG, 'a', encoding='utf-8')
    faulthandler.enable(file=_fh)

    def _sig_handler(sig, frame):
        with open(CRASH_LOG, 'a', encoding='utf-8') as f:
            f.write(f'Signal {sig} received\n')
            traceback.print_stack(frame, file=f)
        sys.exit(0)

    for _s in (signal.SIGTERM, signal.SIGBREAK, signal.SIGINT):
        try: signal.signal(_s, _sig_handler)
        except Exception: pass
    try:
        main()
    except Exception:
        # Restore terminal before writing so the log is readable
        try:
            sys.stdout.write('\033[?25h\033[0m\033[?1049l')
            sys.stdout.flush()
        except Exception:
            pass
        tb = traceback.format_exc()
        with open(CRASH_LOG, 'a', encoding='utf-8') as f:
            f.write(tb)
        print(traceback.format_exc())

def _ensure_admin():
    """Relaunch elevated so SendInput (mouse/buttons) reaches elevated foreground
    windows (games, admin apps). UIPI blocks synthetic input from a lower-integrity
    process into a higher-integrity focused window — running as admin fixes that."""
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return
    except Exception:
        return
    params = ' '.join(f'"{a}"' for a in sys.argv)
    try:
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    except Exception:
        return  # elevation declined/failed — fall through and run unelevated
    sys.exit(0)