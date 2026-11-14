#!/usr/bin/env python3
"""
DS4 Presenter — presentation remote for LibreOffice Impress (standalone, no network)

MODE 0 — Presentation (default):
  ✕  / D-pad →   next slide          ○  / D-pad ←   prev slide
  D-pad ↑         start slideshow     D-pad ↓  / △   escape
  □               black screen (B)
  Left stick      pan (scroll)        Right stick ↑/↓ zoom in / out

MODE 1 — Mouse / Desktop:
  Gyroscope       mouse pointer       Left stick      mouse pointer (additive)
  Right stick ↑/↓ scroll
  ✕               left click (hold)   ○               right click (hold)
  △               middle click        □               Escape

  L1 / R1         left / right click  (both modes)
  L3              toggle mode
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

def find_motion():
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except Exception:
            continue
        caps = dev.capabilities()
        abs_codes = [a[0] for a in caps.get(e.EV_ABS, [])]
        if (e.EV_ABS in caps and e.EV_KEY not in caps
                and e.ABS_X in abs_codes and e.ABS_RZ in abs_codes):
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
        e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
    }, name='DS4 Presenter Mouse')

# ── Shared state (GIL makes individual int assignments atomic) ────
_lx = _ly = 128   # left stick
_ry = 128         # right stick Y
_gx = _gz = 0    # gyro: pitch (ABS_RX) and yaw (ABS_RZ)
_battery = 0
_last    = '—'
_mode    = 0      # 0 = presentation, 1 = mouse/desktop

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
    bat  = f'{_battery}%' if _battery else '--'
    mode = 'PRESENT' if _mode == 0 else 'MOUSE  '
    print(f'\r  bat:{bat}  mode:{mode}  last:{_last}          ', end='', flush=True)

# ── Pan / zoom / mouse thread ─────────────────────────────────────
STICK_DEAD  = 15
GYRO_DEAD   = 80      # ignore gyro noise below this threshold (raw units)
PAN_SENS    = 0.06    # scroll ticks per frame at full deflection
ZOOM_SENS   = 0.025   # zoom steps per frame at full deflection
MOUSE_SENS  = 8.0     # stick pixels per frame at full deflection
GYRO_SENS   = 0.004   # gyro pixels per frame per raw unit

def analog_loop(kb, mouse):
    v_acc = h_acc = zoom_acc = scroll_acc = 0.0
    mx_acc = my_acc = 0.0
    while True:
        lx = _lx - 128
        ly = _ly - 128
        if abs(lx) < STICK_DEAD: lx = 0
        if abs(ly) < STICK_DEAD: ly = 0

        ry = _ry - 128
        if abs(ry) < STICK_DEAD: ry = 0

        if _mode == 0:
            # Left stick → pan (vertical + horizontal scroll)
            v_acc += -(ly / 127) * PAN_SENS
            h_acc +=  (lx / 127) * PAN_SENS
            v_tick = int(v_acc);  v_acc -= v_tick
            h_tick = int(h_acc);  h_acc -= h_tick
            if v_tick: mouse.write(e.EV_REL, e.REL_WHEEL,  v_tick)
            if h_tick: mouse.write(e.EV_REL, e.REL_HWHEEL, h_tick)
            if v_tick or h_tick: mouse.syn()

            # Right stick Y → zoom via Ctrl+scroll
            zoom_acc += -(ry / 127) * ZOOM_SENS
            z_tick = int(zoom_acc);  zoom_acc -= z_tick
            if z_tick:
                kb.write(e.EV_KEY, e.KEY_LEFTCTRL, 1); kb.syn()
                mouse.write(e.EV_REL, e.REL_WHEEL, z_tick); mouse.syn()
                kb.write(e.EV_KEY, e.KEY_LEFTCTRL, 0); kb.syn()

        else:
            # Gyro (yaw→X, pitch→Y) + left stick → mouse pointer
            gz = _gz if abs(_gz) > GYRO_DEAD else 0
            gx = _gx if abs(_gx) > GYRO_DEAD else 0

            mx_acc += (lx / 127) * MOUSE_SENS +  gz * GYRO_SENS
            my_acc += (ly / 127) * MOUSE_SENS + -gx * GYRO_SENS
            mx = int(mx_acc);  mx_acc -= mx
            my = int(my_acc);  my_acc -= my
            if mx or my:
                mouse.write(e.EV_REL, e.REL_X, mx)
                mouse.write(e.EV_REL, e.REL_Y, my)
                mouse.syn()

            # Right stick Y → vertical scroll
            scroll_acc += -(ry / 127) * PAN_SENS
            s_tick = int(scroll_acc);  scroll_acc -= s_tick
            if s_tick:
                mouse.write(e.EV_REL, e.REL_WHEEL, s_tick)
                mouse.syn()

        time.sleep(1 / 120)

# ── Gyroscope thread ──────────────────────────────────────────────
def motion_thread(dev):
    global _gx, _gz
    try:
        for event in dev.read_loop():
            if event.type == e.EV_ABS:
                if   event.code == e.ABS_RX: _gx = event.value  # pitch → mouse Y
                elif event.code == e.ABS_RZ: _gz = event.value  # yaw   → mouse X
    except OSError as err:
        print(f"\nMotion device disconnected: {err}")

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
    global _lx, _ly, _ry, _mode

    dev = find_ds4()
    if not dev:
        print("DS4 not found. Make sure it is connected via Bluetooth or USB.")
        sys.exit(1)
    print(f"DS4 found: {dev.name} ({dev.path})")

    mot = find_motion()
    if mot:
        print(f"Motion:    {mot.name} ({mot.path})")
    else:
        print("Motion device not found — gyro mouse unavailable, stick only.")

    for d in filter(None, [dev, mot]):
        try:
            d.grab()
        except Exception as ex:
            print(f"Warning: could not grab {d.path}: {ex}")

    kb    = make_kb()
    mouse = make_mouse()

    threading.Thread(target=analog_loop,  args=(kb, mouse), daemon=True).start()
    threading.Thread(target=battery_loop,                  daemon=True).start()
    if mot:
        threading.Thread(target=motion_thread, args=(mot,), daemon=True).start()

    print(__doc__)
    _print_status()

    dpad_x = dpad_y = 0

    try:
        for ev in dev.read_loop():
            if ev.type == e.EV_KEY:
                code, val = ev.code, ev.value
                if val == 2: continue  # ignore repeat

                # ── L3: toggle mode ──────────────────────────────
                if code == e.BTN_THUMBL and val:
                    _mode = 1 - _mode
                    _last = 'PRESENT mode' if _mode == 0 else 'MOUSE mode'
                    _print_status()
                    continue

                # ── L1 / R1: clicks (both modes) ─────────────────
                elif code == e.BTN_TL: hold(mouse, e.BTN_LEFT,  val, 'click L')
                elif code == e.BTN_TR: hold(mouse, e.BTN_RIGHT, val, 'click R')

                # ── Global buttons ────────────────────────────────
                elif code == 314 and val: combo(kb, [e.KEY_LEFTALT, e.KEY_TAB], 'Alt+Tab')
                elif code == 315 and val: tap(kb, e.KEY_F5,       'F5 slideshow')
                elif code == 316 and val: tap(kb, e.KEY_LEFTMETA, 'Super')

                # ── Mode-dependent face buttons ───────────────────
                elif _mode == 0:
                    if   code == e.BTN_SOUTH and val: tap(kb, e.KEY_RIGHT, '→ next slide')
                    elif code == e.BTN_EAST  and val: tap(kb, e.KEY_LEFT,  '← prev slide')
                    elif code == e.BTN_NORTH and val: tap(kb, e.KEY_ESC,   'ESC')
                    elif code == e.BTN_WEST  and val: tap(kb, e.KEY_B,     'black screen')

                else:  # _mode == 1
                    if   code == e.BTN_SOUTH: hold(mouse, e.BTN_LEFT,   val, 'click L')
                    elif code == e.BTN_EAST:  hold(mouse, e.BTN_RIGHT,  val, 'click R')
                    elif code == e.BTN_NORTH: hold(mouse, e.BTN_MIDDLE, val, 'click M')
                    elif code == e.BTN_WEST and val: tap(kb, e.KEY_ESC, 'ESC')

            elif ev.type == e.EV_ABS:
                code, val = ev.code, ev.value

                if   code == e.ABS_X:  _lx = val
                elif code == e.ABS_Y:  _ly = val
                elif code == e.ABS_RY: _ry = val

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
        for d in filter(None, [dev, mot]):
            try: d.ungrab()
            except Exception: pass
        kb.close()
        mouse.close()
        print("Stopped.")

if __name__ == '__main__':
    main()
