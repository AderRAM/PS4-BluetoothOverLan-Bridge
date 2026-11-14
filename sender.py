#!/usr/bin/env python3
import socket, struct, threading, glob, time, evdev
from evdev import ecodes as e

# Broadcast to the whole subnet so a DHCP IP change on the PC (e.g. after a
# reboot) never breaks the bridge. The receiver binds 0.0.0.0:9999 and catches it.
WINDOWS_IP   = "192.168.1.255"
WINDOWS_PORT = 9999
PACKET_FMT   = "<HBBBBBbbBBiiiiiiB"

BTN_MAP = {
    304: 0x0001, 305: 0x0002, 308: 0x0004, 307: 0x0008,
    310: 0x0010, 311: 0x0020, 317: 0x0040, 318: 0x0080,
    314: 0x0100, 315: 0x0200,
}

def find_ds4():
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except Exception:
            continue
        caps = dev.capabilities()
        if (e.EV_ABS in caps and e.EV_KEY in caps and
                e.ABS_X in [a[0] for a in caps[e.EV_ABS]] and
                e.BTN_SOUTH in caps[e.EV_KEY]):
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
        if (e.EV_ABS in caps and e.EV_KEY not in caps and
                e.ABS_X in abs_codes and e.ABS_RZ in abs_codes):
            return dev
        dev.close()
    return None

def find_touchpad():
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except Exception:
            continue
        caps = dev.capabilities()
        abs_codes = [a[0] for a in caps.get(e.EV_ABS, [])]
        keys = caps.get(e.EV_KEY, [])
        if (e.EV_ABS in caps and e.EV_KEY in caps and
                e.ABS_X in abs_codes and e.BTN_TOUCH in keys and e.BTN_SOUTH not in keys):
            return dev
        dev.close()
    return None

state = dict(
    buttons=0, special=0,
    lx=128, ly=128, rx=128, ry=128,
    dx=0, dy=0, l2=0, r2=0,
    gx=0, gy=0, gz=0,
    ax=0, ay=0, az=0,
    battery=100,
)
lock = threading.Lock()

def battery_thread():
    # Poll the DS4 battery capacity from sysfs (slow — value changes rarely)
    while True:
        cap = 0
        for p in glob.glob('/sys/class/power_supply/ps-controller-battery-*/capacity'):
            try:
                with open(p) as f:
                    cap = int(f.read().strip())
                break
            except Exception:
                pass
        with lock:
            state["battery"] = max(0, min(100, cap))
        time.sleep(10)

def motion_thread(dev):
    # ABS_X/Y/Z = accelerometer, ABS_RX/Y/Z = gyroscope
    try:
        for event in dev.read_loop():
            if event.type == e.EV_ABS:
                with lock:
                    if   event.code == e.ABS_X:  state["ax"] = event.value
                    elif event.code == e.ABS_Y:  state["ay"] = event.value
                    elif event.code == e.ABS_Z:  state["az"] = event.value
                    elif event.code == e.ABS_RX: state["gx"] = event.value
                    elif event.code == e.ABS_RY: state["gy"] = event.value
                    elif event.code == e.ABS_RZ: state["gz"] = event.value
    except OSError as err:
        print(f"Motion device disconnected: {err}", flush=True)

def touchpad_thread(dev):
    try:
        for event in dev.read_loop():
            if event.type == e.EV_KEY and event.code == e.BTN_TOUCH:
                with lock:
                    if event.value: state["special"] |= 0x02
                    else:           state["special"] &= ~0x02
    except OSError as err:
        print(f"Touchpad device disconnected: {err}", flush=True)

def pack_state(s):
    return struct.pack(PACKET_FMT,
        s["buttons"], s["special"],
        s["lx"], s["ly"], s["rx"], s["ry"],
        s["dx"], s["dy"],
        s["l2"], s["r2"],
        s["gx"], s["gy"], s["gz"],
        s["ax"], s["ay"], s["az"],
        s["battery"])

def heartbeat_thread(sock):
    # Keepalive: stream current state at 20Hz so the bridge stays "connected"
    # and battery stays visible even when the controller is idle (no events).
    while True:
        with lock:
            s = dict(state)
        try:
            sock.sendto(pack_state(s), (WINDOWS_IP, WINDOWS_PORT))
        except OSError:
            pass
        time.sleep(0.05)

def set_led(r, g, b):
    report = bytearray(79)
    report[0] = 0x11; report[1] = 0xC0; report[2] = 0x20; report[3] = 0xF7
    report[8] = r; report[9] = g; report[10] = b
    for path in glob.glob('/dev/hidraw*'):
        try:
            with open(path, 'wb') as f:
                f.write(bytes(report))
            return
        except OSError:
            continue

def main():
    dev = find_ds4()
    if not dev:
        print("DS4 not found."); return
    print(f"DS4:       {dev.name} at {dev.path}", flush=True)
    set_led(255, 0, 0)

    mot = find_motion()
    if mot:
        print(f"Motion:    {mot.name} at {mot.path}", flush=True)

    tp = find_touchpad()
    if tp:
        print(f"Touchpad:  {tp.name} at {tp.path}", flush=True)

    grabbed = []
    for d in filter(None, [dev, mot, tp]):
        try:
            d.grab()
            grabbed.append(d)
        except Exception as ex:
            print(f"Warning: could not grab {d.path}: {ex}", flush=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    threading.Thread(target=battery_thread, daemon=True).start()
    threading.Thread(target=heartbeat_thread, args=(sock,), daemon=True).start()
    if mot:
        threading.Thread(target=motion_thread, args=(mot,), daemon=True).start()
    if tp:
        threading.Thread(target=touchpad_thread, args=(tp,), daemon=True).start()

    print(f"Sending to {WINDOWS_IP}:{WINDOWS_PORT} (exclusive grab active)", flush=True)

    try:
        for event in dev.read_loop():
            if event.type == e.EV_KEY:
                code, val = event.code, event.value
                with lock:
                    if code in BTN_MAP:
                        if val: state["buttons"] |=  BTN_MAP[code]
                        else:   state["buttons"] &= ~BTN_MAP[code]
                    elif code == 316:
                        if val: state["special"] |= 0x01
                        else:   state["special"] &= ~0x01

            elif event.type == e.EV_ABS:
                code, val = event.code, event.value
                with lock:
                    if   code == e.ABS_X:     state["lx"] = val
                    elif code == e.ABS_Y:     state["ly"] = val
                    elif code == e.ABS_RX:    state["rx"] = val
                    elif code == e.ABS_RY:    state["ry"] = val
                    elif code == e.ABS_Z:     state["l2"] = val
                    elif code == e.ABS_RZ:    state["r2"] = val
                    elif code == e.ABS_HAT0X: state["dx"] = val
                    elif code == e.ABS_HAT0Y: state["dy"] = val
            elif event.type == e.EV_SYN:
                with lock:
                    s = dict(state)
                sock.sendto(pack_state(s), (WINDOWS_IP, WINDOWS_PORT))
    except OSError as err:
        print(f"DS4 disconnected: {err}", flush=True)
    finally:
        for d in grabbed:
            try: d.ungrab()
            except Exception: pass
        print("Sender stopped cleanly.", flush=True)

if __name__ == "__main__":
    main()
