#!/usr/bin/env python3
"""
DS4 Presenter — presentation remote for LibreOffice Impress (standalone, no network)

  ✕  / D-pad →   next slide          ○  / D-pad ←   prev slide
  D-pad ↑         start slideshow     D-pad ↓  / △   escape
  □               black screen (B)
  Left stick      pan (scroll)        Right stick ↑/↓ zoom in / out
  L1 / R1         left / right click
  Options         F5 (slideshow)      Share           Alt+Tab
  PS              Super (activities)
"""
import evdev, glob, sys, threading, time
from evdev import UInput, ecodes as e

# ── Device discovery ──────────────────────────────────────────────
def find_ds4():
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except Exception:
            continue
        caps = dev.capabilities()
        if (e.EV_ABS in caps and e.EV_KEY in caps
                and e.ABS_X in [a[0] for a in caps[e.EV_ABS]]
                and e.BTN_SOUTH in caps[e.EV_KEY]):
            return dev
        dev.close()
    return None

# ── Virtual devices ───────────────────────────────────────────────
def make_kb():
    return UInput({e.EV_KEY: [
        e.KEY_RIGHT, e.KEY_LEFT, e.KEY_F5, e.KEY_ESC, e.KEY_B,
        e.KEY_LEFTALT, e.KEY_TAB, e.KEY_LEFTMETA, e.KEY_LEFTCTRL,
    ]}, name='DS4 Presenter Keyboard')

def make_mouse():
    return UInput({
        e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL],
        e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT],
    }, name='DS4 Presenter Mouse')

# ── Shared state (GIL makes individual int assignments atomic) ────
_lx = _ly = 128   # left stick  → pan
_ry = 128         # right stick Y → zoom (up = in, down = out)
_battery = 0
_last    = '—'

# ── Actions ───────────────────────────────────────────────────────
def tap(kb, key, label):
    global _last
    kb.write(e.EV_KEY, key, 1); kb.syn()
    kb.write(e.EV_KEY, key, 0); kb.syn()
    _last = label
    _print_status()

def hold(dev, code, val, label):
    global _last
    dev.write(e.EV_KEY, code, val); dev.syn()
    if val:
        _last = label
        _print_status()

def combo(kb, keys, label):
    global _last
    for k in keys: kb.write(e.EV_KEY, k, 1)
    kb.syn()
    time.sleep(0.05)
    for k in reversed(keys): kb.write(e.EV_KEY, k, 0)
    kb.syn()
    _last = label
    _print_status()

# ── Status line ───────────────────────────────────────────────────
def _print_status():
    bat = f'{_battery}%' if _battery else '--'
    print(f'\r  bat:{bat}  last: {_last}          ', end='', flush=True)

# ── Pan / zoom thread ─────────────────────────────────────────────
STICK_DEAD  = 15
PAN_SENS    = 0.06   # scroll ticks per frame at full deflection
ZOOM_SENS   = 0.025  # zoom steps per frame at full deflection

def analog_loop(kb, mouse):
    v_acc = h_acc = zoom_acc = 0.0
    while True:
        # Left stick → pan (vertical + horizontal scroll)
        lx = _lx - 128
        ly = _ly - 128
        if abs(lx) < STICK_DEAD: lx = 0
        if abs(ly) < STICK_DEAD: ly = 0

        v_acc += -(ly / 127) * PAN_SENS
        h_acc +=  (lx / 127) * PAN_SENS

        v_tick = int(v_acc);  v_acc -= v_tick
        h_tick = int(h_acc);  h_acc -= h_tick
        if v_tick: mouse.write(e.EV_REL, e.REL_WHEEL,  v_tick)
        if h_tick: mouse.write(e.EV_REL, e.REL_HWHEEL, h_tick)
        if v_tick or h_tick: mouse.syn()

        # Right stick Y → zoom (up = in, down = out) via Ctrl+scroll
        ry = _ry - 128
        if abs(ry) < STICK_DEAD: ry = 0

        zoom_acc += -(ry / 127) * ZOOM_SENS
        z_tick = int(zoom_acc);  zoom_acc -= z_tick
        if z_tick:
            kb.write(e.EV_KEY, e.KEY_LEFTCTRL, 1); kb.syn()
            mouse.write(e.EV_REL, e.REL_WHEEL, z_tick); mouse.syn()
            kb.write(e.EV_KEY, e.KEY_LEFTCTRL, 0); kb.syn()

        time.sleep(1 / 120)

# ── Battery poll ──────────────────────────────────────────────────
def battery_loop():
    global _battery
    while True:
        for p in glob.glob('/sys/class/power_supply/ps-controller-battery-*/capacity'):
            try:
                with open(p) as f:
                    _battery = int(f.read().strip())
                break
            except Exception:
                pass
        time.sleep(10)

# ── Main ──────────────────────────────────────────────────────────
def main():
    global _lx, _ly, _ry

    dev = find_ds4()
    if not dev:
        print("DS4 not found. Make sure it is connected via Bluetooth or USB.")
        sys.exit(1)

    print(f"DS4 found: {dev.name} ({dev.path})")
    try:
        dev.grab()
    except Exception as ex:
        print(f"Warning: could not grab DS4 exclusively: {ex}")

    kb    = make_kb()
    mouse = make_mouse()

    threading.Thread(target=analog_loop,  args=(kb, mouse), daemon=True).start()
    threading.Thread(target=battery_loop,                  daemon=True).start()

    print(__doc__)
    _print_status()

    dpad_x = dpad_y = 0

    try:
        for ev in dev.read_loop():
            if ev.type == e.EV_KEY:
                code, val = ev.code, ev.value
                if val == 2: continue  # ignore repeat

                if   code == e.BTN_SOUTH: tap(kb, e.KEY_RIGHT,    '→ next slide') if val else None
                elif code == e.BTN_EAST:  tap(kb, e.KEY_LEFT,     '← prev slide') if val else None
                elif code == e.BTN_NORTH: tap(kb, e.KEY_ESC,      'ESC') if val else None
                elif code == e.BTN_WEST:  tap(kb, e.KEY_B,        'black screen') if val else None
                elif code == e.BTN_TL:    hold(mouse, e.BTN_LEFT,  val, 'click L')
                elif code == e.BTN_TR:    hold(mouse, e.BTN_RIGHT, val, 'click R')
                elif code == 314 and val: combo(kb, [e.KEY_LEFTALT, e.KEY_TAB], 'Alt+Tab')
                elif code == 315 and val: tap(kb, e.KEY_F5,        'F5 slideshow')
                elif code == 316 and val: tap(kb, e.KEY_LEFTMETA,  'Super')

            elif ev.type == e.EV_ABS:
                code, val = ev.code, ev.value

                if   code == e.ABS_X:      _lx = val
                elif code == e.ABS_Y:      _ly = val
                elif code == e.ABS_RY:     _ry = val

                elif code == e.ABS_HAT0X and val != dpad_x:
                    if   val == -1: tap(kb, e.KEY_LEFT,  '← prev slide')
                    elif val ==  1: tap(kb, e.KEY_RIGHT, '→ next slide')
                    dpad_x = val

                elif code == e.ABS_HAT0Y and val != dpad_y:
                    if   val == -1: tap(kb, e.KEY_F5,  'F5 slideshow')
                    elif val ==  1: tap(kb, e.KEY_ESC, 'ESC')
                    dpad_y = val

    except OSError as err:
        print(f"\nDS4 disconnected: {err}")
    except KeyboardInterrupt:
        print()
    finally:
        try: dev.ungrab()
        except Exception: pass
        kb.close()
        mouse.close()
        print("Stopped.")

if __name__ == '__main__':
    main()
