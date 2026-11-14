#!/usr/bin/env python3
"""DS4 Presenter UI — PyQt6 graphical interface"""

import sys, time, glob, threading
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QProgressBar, QSystemTrayIcon,
    QMenu, QGroupBox, QGridLayout,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QRect, QPointF
from PyQt6.QtGui import (
    QIcon, QColor, QPainter, QFont, QPixmap, QPen, QBrush,
    QLinearGradient, QPainterPath, QAction,
)

try:
    import evdev
    from evdev import UInput, ecodes as e
    EVDEV_OK = True
except ImportError:
    EVDEV_OK = False

# ── Paleta (Breeze Dark — tema vermelho) ──────────────────────────────────────
C = {
    'bg':        '#251616',   # Window/BackgroundNormal
    'bg2':       '#281414',   # View/BackgroundNormal
    'bg_alt':    '#39191a',   # BackgroundAlternate
    'card':      '#251515',   # Unfocused bg
    'button':    '#380000',   # Button background
    'border':    '#511212',   # borders_breeze
    'sel_bg':    '#6c1919',   # Selection background
    'sel_focus': '#6c0000',   # DecorationFocus
    'text':      '#d3dae3',   # theme_text_color
    'text_dim':  '#9a9099',   # mid between normal and insensitive
    'text_btn':  '#c3c7d1',   # button fg normal
    'positive':  '#71f79f',   # ForegroundPositive
    'warning':   '#ff6a00',   # warning_color
    'negative':  '#ed254e',   # ForegroundNegative / error
    'link':      '#cb4400',   # ForegroundLink
    # DS4 face buttons — classic PS colors sobrepondo o tema
    'cross':     '#4fc3f7',
    'circle':    '#f48fb1',
    'square':    '#ce93d8',
    'tri':       '#80cbc4',
    'dpad':      '#9a6060',
    'btn_off':   '#3a1010',
}

FONT_UI   = 'Fira Sans'
FONT_MONO = 'FiraCode Nerd Font Mono'

SS = f"""
* {{ box-sizing: border-box; }}
QMainWindow, QWidget {{
    background: {C['bg']};
    color: {C['text']};
    font-family: '{FONT_UI}', 'Segoe UI', sans-serif;
    font-size: 10pt;
}}
QGroupBox {{
    background: {C['card']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    margin-top: 14px;
    padding: 10px 12px 12px 12px;
    font-size: 8pt;
    font-weight: 600;
    color: {C['text_dim']};
    letter-spacing: 1px;
    text-transform: uppercase;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
}}
QPushButton {{
    background: {C['button']};
    color: {C['text_btn']};
    border: 1px solid {C['border']};
    border-radius: 6px;
    padding: 7px 16px;
    font-size: 10pt;
}}
QPushButton:hover  {{ background: {C['sel_bg']};    border-color: {C['sel_focus']}; }}
QPushButton:pressed {{ background: {C['sel_focus']}; }}
QProgressBar {{
    background: {C['bg2']};
    border: 1px solid {C['border']};
    border-radius: 4px;
    max-height: 7px;
    min-height: 7px;
    font-size: 0px;
}}
QProgressBar::chunk {{ background: {C['positive']}; border-radius: 4px; }}
QMenu {{
    background: {C['card']};
    border: 1px solid {C['border']};
    border-radius: 6px;
    padding: 4px;
    font-size: 10pt;
}}
QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {C['sel_bg']}; }}
QMenu::separator {{ background: {C['border']}; height: 1px; margin: 4px 8px; }}
"""


# ── DS4 Visualizer ────────────────────────────────────────────────────────────
class DS4View(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(172)
        self.btns: dict[str, bool] = {}
        self.lx = self.ly = self.rx = self.ry = 128

    def set_state(self, btns, lx=128, ly=128, rx=128, ry=128):
        self.btns = btns
        self.lx, self.ly, self.rx, self.ry = lx, ly, rx, ry
        self.update()

    def _b(self, name):
        return self.btns.get(name, False)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QColor(C['card']))

        bw = min(W - 24, 440)
        bh = 132
        bx = (W - bw) // 2
        by = (H - bh) // 2 + 10   # extra top room for L2/R2 pills

        # Helpers for fractional positions inside the body
        def px(f): return bx + bw * f
        def py(f): return by + bh * f

        # ── Controller body ───────────────────────────────────────────────────
        body = QPainterPath()
        body.addRoundedRect(bx, by, bw, bh, 40, 40)
        grad = QLinearGradient(bx, by, bx, by + bh)
        grad.setColorAt(0, QColor('#3a1818'))
        grad.setColorAt(1, QColor('#1e0c0c'))
        p.fillPath(body, QBrush(grad))
        p.setPen(QPen(QColor(C['border']), 1.5))
        p.drawPath(body)

        def circ(x, y, r, fill, stroke=None, lbl='', ls=10):
            p.setBrush(QBrush(QColor(fill)))
            p.setPen(QPen(QColor(stroke or fill), 1.2))
            p.drawEllipse(QPointF(x, y), r, r)
            if lbl:
                p.setPen(QColor('#ffffff' if fill != C['btn_off'] else C['text_dim']))
                f = QFont(FONT_UI); f.setPixelSize(ls); f.setBold(True); p.setFont(f)
                p.drawText(QRect(int(x-r), int(y-r), int(r*2), int(r*2)),
                           Qt.AlignmentFlag.AlignCenter, lbl)

        def pill(x, y, w, h, fill, lbl='', ls=9):
            p.setBrush(QBrush(QColor(fill)))
            p.setPen(QPen(QColor(fill).darker(140), 1))
            p.drawRoundedRect(QRect(int(x-w//2), int(y-h//2), w, h), 4, 4)
            if lbl:
                p.setPen(QColor(C['text'] if fill != C['btn_off'] else C['text_dim']))
                f = QFont(FONT_UI); f.setPixelSize(ls); p.setFont(f)
                p.drawText(QRect(int(x-w//2), int(y-h//2), w, h),
                           Qt.AlignmentFlag.AlignCenter, lbl)

        # ── L2 / R2  (top edge, above body) ──────────────────────────────────
        pill(px(0.22), by - 14, 52, 10,
             C['warning'] if self._b('L2') else C['btn_off'], 'L2', 8)
        pill(px(0.78), by - 14, 52, 10,
             C['warning'] if self._b('R2') else C['btn_off'], 'R2', 8)

        # ── L1 / R1  (just above body) ────────────────────────────────────────
        pill(px(0.22), by - 2, 52, 13,
             C['sel_bg'] if self._b('L1') else C['btn_off'], 'L1')
        pill(px(0.78), by - 2, 52, 13,
             C['sel_bg'] if self._b('R1') else C['btn_off'], 'R1')

        # ── D-Pad  (23% from left, 46% down) ─────────────────────────────────
        dpx, dpy, ds = px(0.23), py(0.46), 13
        for ddx, ddy, nm, lbl in [(0,-ds,'D↑','▲'),(0,ds,'D↓','▼'),
                                   (-ds,0,'D←','◀'),(ds,0,'D→','▶')]:
            circ(dpx+ddx, dpy+ddy, 11,
                 C['sel_bg'] if self._b(nm) else C['btn_off'], None, lbl, 9)

        # ── Face buttons  (77% from left, 46% down) ──────────────────────────
        fbx, fby, fd = px(0.77), py(0.46), 16
        for ddx, ddy, nm, on_col in [
            ( 0, -fd, '△', C['tri']),
            ( 0,  fd, '✕', C['cross']),
            (-fd,  0, '○', C['circle']),
            ( fd,  0, '□', C['square']),
        ]:
            circ(fbx+ddx, fby+ddy, 12,
                 on_col if self._b(nm) else C['btn_off'], on_col, nm, 10)

        # ── Share / Options  (small, above touchpad) ──────────────────────────
        circ(px(0.40), py(0.26), 8,
             C['sel_bg'] if self._b('Share')   else C['btn_off'], None, 'SHR', 7)
        circ(px(0.60), py(0.26), 8,
             C['sel_bg'] if self._b('Options') else C['btn_off'], None, 'OPT', 7)

        # ── PS button  (tiny, above touchpad center) ──────────────────────────
        ps_col = C['warning'] if self._b('PS') else '#2c1010'
        ps_str = C['warning'] if self._b('PS') else '#5a3535'
        p.setBrush(QBrush(QColor(ps_col)))
        p.setPen(QPen(QColor(ps_str), 1))
        p.drawEllipse(QPointF(px(0.50), py(0.24)), 7, 7)
        p.setPen(QColor(ps_str))
        f = QFont(FONT_UI); f.setPixelSize(7); p.setFont(f)
        p.drawText(QRect(int(px(0.50)-7), int(py(0.24)-7), 14, 14),
                   Qt.AlignmentFlag.AlignCenter, 'PS')

        # ── Touchpad ──────────────────────────────────────────────────────────
        tp_w, tp_h = int(bw * 0.22), 28
        tp_x = int(px(0.50) - tp_w/2)
        tp_y = int(py(0.42) - tp_h/2)
        p.setBrush(QBrush(QColor(C['sel_bg'] if self._b('TP') else '#2a1212')))
        p.setPen(QPen(QColor(C['border']), 1))
        p.drawRoundedRect(tp_x, tp_y, tp_w, tp_h, 5, 5)

        # ── Sticks  (left: 36%/75%, right: 64%/75%) ──────────────────────────
        def stick(sx, sy, vx, vy, pressed):
            p.setBrush(QBrush(QColor('#180808')))
            p.setPen(QPen(QColor(C['border']), 1))
            p.drawEllipse(QPointF(sx, sy), 21, 21)
            dx2 = sx + ((vx-128)/128)*12
            dy2 = sy + ((vy-128)/128)*12
            col = C['sel_focus'] if pressed else C['sel_bg']
            p.setBrush(QBrush(QColor(col)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(dx2, dy2), 9, 9)

        stick(px(0.36), py(0.75), self.lx, self.ly, self._b('L3'))
        stick(px(0.64), py(0.75), self.rx, self.ry, self._b('R3'))

        p.end()


# ── Status dot ────────────────────────────────────────────────────────────────
class Dot(QWidget):
    def __init__(self, size=11):
        super().__init__()
        self._c = QColor(C['text_dim'])
        self._s = size
        self.setFixedSize(size+4, size+4)

    def set(self, hex_col):
        self._c = QColor(hex_col); self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        glow = QColor(self._c); glow.setAlpha(55)
        p.setBrush(QBrush(glow)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, self._s+4, self._s+4)
        p.setBrush(QBrush(self._c))
        p.drawEllipse(2, 2, self._s, self._s)


# ── Worker thread ─────────────────────────────────────────────────────────────
class DS4Worker(QThread):
    found   = pyqtSignal(str)
    lost    = pyqtSignal()
    battery = pyqtSignal(int)
    buttons = pyqtSignal(dict)
    sticks  = pyqtSignal(int, int, int, int)
    action  = pyqtSignal(str)

    DEAD      = 15
    PAN       = 0.06
    ZOOM      = 0.025
    GYRO_DEAD = 1200
    GYRO_SENS = 1 / 4000   # same as receiver_linux.py

    def __init__(self):
        super().__init__()
        self._run = self._active = self._gyro_mouse = False
        self._dev = self._mot = self._kb = self._mouse = None
        self._lx = self._ly = self._rx = self._ry = 128
        self._gx = self._gy = 0          # gyro X/Y from motion device
        self._st: dict[str, bool] = {}

    def set_active(self, v):      self._active = v
    def set_gyro_mouse(self, v):  self._gyro_mouse = v

    def _make_kb(self):
        return UInput({e.EV_KEY: [
            e.KEY_RIGHT, e.KEY_LEFT, e.KEY_F5, e.KEY_ESC, e.KEY_B,
            e.KEY_LEFTALT, e.KEY_TAB, e.KEY_LEFTMETA, e.KEY_LEFTCTRL,
        ]}, name='DS4 Presenter Keyboard')

    def _make_mouse(self):
        return UInput({
            e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL],
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT],
        }, name='DS4 Presenter Mouse')

    def _tap(self, key, lbl):
        if self._kb and self._active:
            self._kb.write(e.EV_KEY, key, 1); self._kb.syn()
            self._kb.write(e.EV_KEY, key, 0); self._kb.syn()
        self.action.emit(lbl)

    def _hold(self, dev, code, val, lbl):
        if dev and self._active:
            dev.write(e.EV_KEY, code, val); dev.syn()
        if val: self.action.emit(lbl)

    def _combo(self, keys, lbl):
        if self._kb and self._active:
            for k in keys: self._kb.write(e.EV_KEY, k, 1)
            self._kb.syn(); time.sleep(0.05)
            for k in reversed(keys): self._kb.write(e.EV_KEY, k, 0)
            self._kb.syn()
        self.action.emit(lbl)

    def _analog(self):
        va = ha = za = mx = my = 0.0
        while self._run and self._dev:
            # ── Scroll (left stick) ───────────────────────────────────────────
            lx = self._lx-128; ly = self._ly-128
            if abs(lx)<self.DEAD: lx=0
            if abs(ly)<self.DEAD: ly=0
            va += -(ly/127)*self.PAN; ha += (lx/127)*self.PAN
            vt=int(va); va-=vt; ht=int(ha); ha-=ht
            if self._mouse and self._active and not self._gyro_mouse:
                if vt: self._mouse.write(e.EV_REL, e.REL_WHEEL, vt)
                if ht: self._mouse.write(e.EV_REL, e.REL_HWHEEL, ht)
                if vt or ht: self._mouse.syn()

            # ── Zoom (right stick Y) ──────────────────────────────────────────
            ry=self._ry-128
            if abs(ry)<self.DEAD: ry=0
            za+=-(ry/127)*self.ZOOM; zt=int(za); za-=zt
            if zt and self._kb and self._mouse and self._active:
                self._kb.write(e.EV_KEY, e.KEY_LEFTCTRL,1); self._kb.syn()
                self._mouse.write(e.EV_REL, e.REL_WHEEL, zt); self._mouse.syn()
                self._kb.write(e.EV_KEY, e.KEY_LEFTCTRL,0); self._kb.syn()

            # ── Gyro mouse ────────────────────────────────────────────────────
            if self._gyro_mouse and self._mouse:
                gx = self._gx; gy = self._gy
                fx = 0.0 if abs(gy) < self.GYRO_DEAD else -gy * self.GYRO_SENS
                fy = 0.0 if abs(gx) < self.GYRO_DEAD else -gx * self.GYRO_SENS
                mx += fx; my += fy
                dx2=int(mx); mx-=dx2
                dy2=int(my); my-=dy2
                if (dx2 or dy2) and self._active:
                    if dx2: self._mouse.write(e.EV_REL, e.REL_X, dx2)
                    if dy2: self._mouse.write(e.EV_REL, e.REL_Y, dy2)
                    self._mouse.syn()

            time.sleep(1/120)

    def _bat(self):
        while self._run:
            for path in glob.glob('/sys/class/power_supply/ps-controller-battery-*/capacity'):
                try:
                    with open(path) as f:
                        self.battery.emit(int(f.read().strip()))
                    break
                except Exception: pass
            time.sleep(10)

    def _find(self):
        if not EVDEV_OK: return None
        for path in evdev.list_devices():
            try: dev = evdev.InputDevice(path)
            except Exception: continue
            caps = dev.capabilities()
            if (e.EV_ABS in caps and e.EV_KEY in caps
                    and e.ABS_X in [a[0] for a in caps[e.EV_ABS]]
                    and e.BTN_SOUTH in caps[e.EV_KEY]):
                return dev
            dev.close()
        return None

    def _find_motion(self):
        if not EVDEV_OK: return None
        for path in evdev.list_devices():
            try: dev = evdev.InputDevice(path)
            except Exception: continue
            caps = dev.capabilities()
            abs_codes = [a[0] for a in caps.get(e.EV_ABS, [])]
            if (e.EV_ABS in caps and e.EV_KEY not in caps
                    and e.ABS_X in abs_codes and e.ABS_RZ in abs_codes):
                return dev
            dev.close()
        return None

    def _motion_loop(self):
        while self._run:
            mot = self._find_motion()
            if not mot:
                time.sleep(2); continue
            self._mot = mot
            try:
                for ev in mot.read_loop():
                    if not self._run: break
                    if ev.type == e.EV_ABS:
                        # ABS_RX=gyro X (pitch), ABS_RY=gyro Y (yaw)
                        if   ev.code == e.ABS_RX: self._gx = ev.value
                        elif ev.code == e.ABS_RY: self._gy = ev.value
            except OSError:
                self._mot = None; time.sleep(2)

    def _upd(self, name, val):
        if self._st.get(name) != val:
            self._st[name] = val
            self.buttons.emit(dict(self._st))

    def stop(self):
        self._run = False
        if self._dev:
            try: self._dev.ungrab()
            except Exception: pass
        if self._kb:    self._kb.close()
        if self._mouse: self._mouse.close()

    def run(self):
        self._run = True
        if not EVDEV_OK: return
        self._kb    = self._make_kb()
        self._mouse = self._make_mouse()
        threading.Thread(target=self._bat,         daemon=True).start()
        threading.Thread(target=self._motion_loop, daemon=True).start()
        dx = dy = 0

        while self._run:
            self._dev = self._find()
            if not self._dev:
                self.lost.emit(); time.sleep(2); continue
            try: self._dev.grab()
            except Exception: pass
            self.found.emit(self._dev.name)
            threading.Thread(target=self._analog, daemon=True).start()

            try:
                for ev in self._dev.read_loop():
                    if not self._run: break
                    if ev.type == e.EV_KEY:
                        c, v = ev.code, ev.value
                        if v == 2: continue
                        nm = {e.BTN_SOUTH:'✕', e.BTN_EAST:'○', e.BTN_NORTH:'△',
                              e.BTN_WEST:'□', e.BTN_TL:'L1', e.BTN_TR:'R1',
                              e.BTN_TL2:'L2', e.BTN_TR2:'R2',
                              e.BTN_THUMBL:'L3', e.BTN_THUMBR:'R3',
                              314:'Share', 315:'Options', 316:'PS'}.get(c)
                        if nm: self._upd(nm, bool(v))
                        if   c==e.BTN_SOUTH and v: self._tap(e.KEY_RIGHT,   '→  Próximo slide')
                        elif c==e.BTN_EAST  and v: self._tap(e.KEY_LEFT,    '←  Slide anterior')
                        elif c==e.BTN_NORTH and v: self._tap(e.KEY_ESC,     'ESC  Sair slideshow')
                        elif c==e.BTN_WEST  and v: self._tap(e.KEY_B,       'B  Tela preta')
                        elif c==e.BTN_TL:          self._hold(self._mouse, e.BTN_LEFT,  v, 'L1  Click esquerdo')
                        elif c==e.BTN_TR:          self._hold(self._mouse, e.BTN_RIGHT, v, 'R1  Click direito')
                        elif c==314 and v:         self._combo([e.KEY_LEFTALT,e.KEY_TAB], 'Share  Alt+Tab')
                        elif c==315 and v:         self._tap(e.KEY_F5,       'Options  Iniciar slideshow')
                        elif c==316 and v:         self._tap(e.KEY_LEFTMETA, 'PS  Super / Menu')
                    elif ev.type == e.EV_ABS:
                        c, v = ev.code, ev.value
                        if   c==e.ABS_X:  self._lx=v
                        elif c==e.ABS_Y:  self._ly=v
                        elif c==e.ABS_RX: self._rx=v
                        elif c==e.ABS_RY: self._ry=v
                        elif c==e.ABS_Z:  self._upd('L2', v>30)
                        elif c==e.ABS_RZ: self._upd('R2', v>30)
                        elif c==e.ABS_HAT0X and v!=dx:
                            self._upd('D←',v==-1); self._upd('D→',v==1)
                            if v==-1: self._tap(e.KEY_LEFT,  '←  Slide anterior')
                            elif v==1: self._tap(e.KEY_RIGHT,'→  Próximo slide')
                            dx=v
                        elif c==e.ABS_HAT0Y and v!=dy:
                            self._upd('D↑',v==-1); self._upd('D↓',v==1)
                            if v==-1: self._tap(e.KEY_F5, 'D↑  Iniciar slideshow')
                            elif v==1: self._tap(e.KEY_ESC,'D↓  ESC')
                            dy=v
                        self.sticks.emit(self._lx, self._ly, self._rx, self._ry)
            except OSError:
                self.lost.emit()
                try: self._dev.ungrab()
                except Exception: pass
                self._dev=None; time.sleep(2)


# ── Main window ───────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('DS4 Presenter')
        self.setMinimumWidth(400)
        self.setMaximumWidth(500)
        self._active = self._conn = self._blink_state = self._gyro = False

        self._worker = DS4Worker()
        self._worker.found.connect(self._on_found)
        self._worker.lost.connect(self._on_lost)
        self._worker.battery.connect(self._on_battery)
        self._worker.buttons.connect(self._on_buttons)
        self._worker.sticks.connect(self._on_sticks)
        self._worker.action.connect(self._on_action)

        self._build_ui()
        self._build_tray()

        self._blink_timer = QTimer()
        self._blink_timer.timeout.connect(self._blink_tick)
        self._worker.start()

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.setStyleSheet(SS)
        root = QWidget(); self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        # Header
        hdr = QHBoxLayout()
        icon_lbl = QLabel('⬡')
        icon_lbl.setStyleSheet(f'color: {C["negative"]}; font-size: 18px;')
        title = QLabel('DS4 Presenter')
        title.setStyleSheet('font-size: 13pt; font-weight: 700;')
        hdr.addWidget(icon_lbl)
        hdr.addSpacing(6)
        hdr.addWidget(title)
        hdr.addStretch()
        lay.addLayout(hdr)

        lay.addWidget(self._card_status())
        lay.addWidget(self._card_controller())
        lay.addWidget(self._card_action())
        lay.addWidget(self._card_map())
        lay.addSpacing(2)
        lay.addWidget(self._build_toggle())
        lay.addWidget(self._build_modes())

    def _grp(self, title):
        g = QGroupBox(title)
        return g

    def _card_status(self):
        g = self._grp('STATUS')
        lay = QVBoxLayout(g)
        lay.setSpacing(8)

        row1 = QHBoxLayout()
        self._dot = Dot(11)
        self._conn_lbl = QLabel('Aguardando controle…')
        self._conn_lbl.setStyleSheet(f'color: {C["text_dim"]}; font-size: 10pt;')
        self._dev_lbl = QLabel('')
        self._dev_lbl.setStyleSheet(f'color: {C["text_dim"]}; font-size: 9pt;')
        row1.addWidget(self._dot)
        row1.addSpacing(6)
        row1.addWidget(self._conn_lbl)
        row1.addStretch()
        row1.addWidget(self._dev_lbl)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        bat_ico = QLabel('🔋')
        bat_ico.setStyleSheet('font-size: 13px;')
        self._bat_bar = QProgressBar()
        self._bat_bar.setRange(0, 100)
        self._bat_bar.setValue(0)
        self._bat_pct = QLabel('—')
        self._bat_pct.setStyleSheet(f'color: {C["text_dim"]}; font-size: 9pt; min-width: 32px;')
        self._bat_pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row2.addWidget(bat_ico)
        row2.addSpacing(6)
        row2.addWidget(self._bat_bar)
        row2.addSpacing(8)
        row2.addWidget(self._bat_pct)
        lay.addLayout(row2)
        return g

    def _card_controller(self):
        g = self._grp('CONTROLE')
        lay = QVBoxLayout(g)
        lay.setContentsMargins(0, 0, 0, 0)
        self._ds4 = DS4View()
        lay.addWidget(self._ds4)
        return g

    def _card_action(self):
        g = self._grp('ÚLTIMA AÇÃO')
        lay = QHBoxLayout(g)
        self._act_dot = Dot(8)
        self._act_lbl = QLabel('—')
        self._act_lbl.setStyleSheet(f'font-family: {FONT_MONO}; font-size: 10pt;')
        lay.addWidget(self._act_dot)
        lay.addSpacing(8)
        lay.addWidget(self._act_lbl)
        lay.addStretch()
        return g

    def _card_map(self):
        g = self._grp('MAPEAMENTO')
        grid = QGridLayout(g)
        grid.setSpacing(3)
        grid.setHorizontalSpacing(14)
        rows = [
            ('✕  /  D→',     'Próximo slide',       '○  /  D←',   'Slide anterior'),
            ('Options / D↑', 'Iniciar slideshow',    '△  /  D↓',   'ESC'),
            ('□',            'Tela preta',           'L1  /  R1',  'Click esq / dir'),
            ('Stick Esq',    'Pan (rolar)',           'Stick Dir',  'Zoom in/out'),
            ('Share',        'Alt+Tab',              'PS',          'Super / Menu'),
        ]
        for r, (k1, v1, k2, v2) in enumerate(rows):
            for ci, (k, v) in enumerate([(k1,v1),(k2,v2)]):
                kl = QLabel(k)
                kl.setStyleSheet(f'color: {C["link"]}; font-size: 9pt; font-weight: 600;')
                vl = QLabel(v)
                vl.setStyleSheet(f'color: {C["text"]}; font-size: 9pt;')
                grid.addWidget(kl, r, ci*2)
                grid.addWidget(vl, r, ci*2+1)
        return g

    def _build_toggle(self):
        self._toggle = QPushButton('▶   ATIVAR PRESENTER')
        self._toggle.setFixedHeight(44)
        self._style_toggle_off()
        self._toggle.clicked.connect(self._do_toggle)
        return self._toggle

    def _style_toggle_off(self):
        self._toggle.setStyleSheet(f'''
            QPushButton {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #1a3a22, stop:1 #152d1a);
                color: {C['positive']};
                border: 1px solid {C['positive']}44;
                border-radius: 7px;
                font-size: 11pt; font-weight: 700; letter-spacing: 2px;
            }}
            QPushButton:hover {{
                border-color: {C['positive']};
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #22502d, stop:1 #1a3a22);
            }}
            QPushButton:pressed {{ background: #1c4828; }}
        ''')

    def _style_toggle_on(self):
        self._toggle.setStyleSheet(f'''
            QPushButton {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #4d1e1e, stop:1 #3a1010);
                color: {C['negative']};
                border: 1px solid {C['negative']}44;
                border-radius: 7px;
                font-size: 11pt; font-weight: 700; letter-spacing: 2px;
            }}
            QPushButton:hover {{
                border-color: {C['negative']};
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #612525, stop:1 #4d1e1e);
            }}
            QPushButton:pressed {{ background: #5c1a1a; }}
        ''')

    def _build_modes(self):
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self._gyro_btn = QPushButton('🖱  Gyro Mouse  OFF')
        self._gyro_btn.setCheckable(True)
        self._gyro_btn.setFixedHeight(34)
        self._gyro_btn.setStyleSheet(self._gyro_btn_style(False))
        self._gyro_btn.clicked.connect(self._toggle_gyro)

        lay.addStretch()
        lay.addWidget(self._gyro_btn)
        lay.addStretch()
        return row

    def _gyro_btn_style(self, on: bool):
        if on:
            return f'''QPushButton {{
                background: #1a2a3e; color: {C["link"]};
                border: 1px solid {C["link"]}66; border-radius: 6px;
                font-size: 10pt; font-weight: 600;
            }}
            QPushButton:hover {{ border-color: {C["link"]}; background: #1e3350; }}'''
        return f'''QPushButton {{
                background: {C["button"]}; color: {C["text_dim"]};
                border: 1px solid {C["border"]}; border-radius: 6px;
                font-size: 10pt;
            }}
            QPushButton:hover {{ background: {C["sel_bg"]}; color: {C["text"]}; }}'''

    def _toggle_gyro(self):
        self._gyro = not self._gyro
        self._worker.set_gyro_mouse(self._gyro)
        self._gyro_btn.setText(f'🖱  Gyro Mouse  {"ON" if self._gyro else "OFF"}')
        self._gyro_btn.setStyleSheet(self._gyro_btn_style(self._gyro))

    # ── Tray ──────────────────────────────────────────────────────────────────
    def _build_tray(self):
        pix = QPixmap(16, 16)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(C['negative'])))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(2, 2, 12, 12)
        p.end()

        self._tray = QSystemTrayIcon(QIcon(pix), self)
        m = QMenu()
        self._tray_act = QAction('Ativar Presenter', self)
        self._tray_act.triggered.connect(self._do_toggle)
        m.addAction(self._tray_act)
        m.addSeparator()
        m.addAction(QAction('Mostrar janela', self, triggered=self.show))
        m.addAction(QAction('Sair', self, triggered=self._quit))
        self._tray.setContextMenu(m)
        self._tray.activated.connect(
            lambda r: self.show() if r==QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self._tray.setToolTip('DS4 Presenter')
        self._tray.show()

    # ── Slots ─────────────────────────────────────────────────────────────────
    def _on_found(self, name):
        self._conn = True
        self._dot.set(C['positive'])
        self._conn_lbl.setText('Conectado')
        self._conn_lbl.setStyleSheet(f'color: {C["positive"]}; font-size: 10pt; font-weight: 600;')
        self._dev_lbl.setText(name)
        self._tray.setToolTip(f'DS4 Presenter — {name}')

    def _on_lost(self):
        self._conn = False
        self._dot.set(C['text_dim'])
        self._conn_lbl.setText('Aguardando controle…')
        self._conn_lbl.setStyleSheet(f'color: {C["text_dim"]}; font-size: 10pt;')
        self._dev_lbl.setText('')
        self._ds4.set_state({})
        self._tray.setToolTip('DS4 Presenter — desconectado')
        if self._active: self._set_active(False)

    def _on_battery(self, val):
        self._bat_bar.setValue(val)
        self._bat_pct.setText(f'{val}%')
        col = C['positive'] if val>50 else C['warning'] if val>20 else C['negative']
        self._bat_bar.setStyleSheet(
            f'QProgressBar::chunk {{ background: {col}; border-radius: 4px; }}')
        self._bat_pct.setStyleSheet(f'color: {col}; font-size: 9pt; min-width: 32px;')

    def _on_buttons(self, state):
        self._ds4.set_state(state, self._ds4.lx, self._ds4.ly, self._ds4.rx, self._ds4.ry)

    def _on_sticks(self, lx, ly, rx, ry):
        self._ds4.set_state(self._ds4.btns, lx, ly, rx, ry)

    def _on_action(self, lbl):
        self._act_lbl.setText(lbl)
        self._act_dot.set(C['sel_bg'])
        QTimer.singleShot(500, lambda: self._act_dot.set(C['text_dim']))

    def _blink_tick(self):
        self._blink_state = not self._blink_state
        self._dot.set(C['negative'] if self._blink_state else C['sel_bg'])

    # ── Toggle ────────────────────────────────────────────────────────────────
    def _do_toggle(self):
        self._set_active(not self._active)

    def _set_active(self, val):
        self._active = val
        self._worker.set_active(val)
        if val:
            self._toggle.setText('DESATIVAR PRESENTER')
            self._style_toggle_on()
            self._blink_timer.start(650)
            self._tray_act.setText('Desativar Presenter')
        else:
            self._toggle.setText('ATIVAR PRESENTER')
            self._style_toggle_off()
            self._blink_timer.stop()
            if self._conn: self._dot.set(C['positive'])
            self._tray_act.setText('Ativar Presenter')

    def _quit(self):
        self._worker.stop()
        self._worker.wait(2000)
        QApplication.quit()

    def closeEvent(self, ev):
        ev.ignore()
        self.hide()
        self._tray.showMessage('DS4 Presenter',
            'Rodando em segundo plano. Clique duplo para reabrir.',
            QSystemTrayIcon.MessageIcon.Information, 2000)


# ── Entry ─────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setApplicationName('DS4 Presenter')
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
