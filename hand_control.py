"""
Hand Control para macOS
-----------------------
Controlar el cursor del Mac con gestos de la mano usando la cámara web.

Gestos:
  - Mover índice + pulgar  -> mueve el cursor (punto medio entre ambos)
  - Pellizco índice+pulgar -> click (corto) / drag (sostenido)
  - Doble pellizco rápido  -> doble click
  - Pellizco anular+pulgar -> click derecho

Atajos (click en la miniatura de debug para activar):
  ESC / Cmd+Q -> salir
  D           -> mostrar / ocultar miniatura de debug
  M           -> activar / desactivar control del mouse
"""

import math
import os
import sys
import threading
import time

import cv2
import mediapipe as mp
import numpy as np
import pyautogui
from PyQt5.QtCore import Qt, QPoint, QTimer
from PyQt5.QtGui import QBrush, QColor, QIcon, QImage, QKeySequence, QPainter, QPixmap, QRadialGradient
from PyQt5.QtWidgets import QApplication, QLabel, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget

# ----- Configuración general -----
PINCH_ON_THRESHOLD   = 0.055  # distancia normalizada para activar el pellizco
PINCH_OFF_THRESHOLD  = 0.085  # histeresis: distancia para soltar (evita parpadeo)
SMOOTHING            = 0.15   # 0 = sin suavizado, 1 = sin movimiento
DEADZONE_PX          = 5      # movimientos < N px pantalla se ignoran (anti-jitter)
EDGE_MARGIN          = 0.12   # recorte de bordes de la cámara
DOUBLE_CLICK_INTERVAL = 0.35  # segundos entre pellizcos para doble click
CAMERA_INDEX = 0
CAMERA_WIDTH = 960
CAMERA_HEIGHT = 540

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

SCREEN_W, SCREEN_H = pyautogui.size()


# ----- Helpers macOS (sin deps extra, usa ctypes sobre Obj-C runtime) -----
def _macos_background_app():
    """Sin ícono en el Dock; evita que las ventanas se oculten al cambiar de app."""
    try:
        import ctypes, ctypes.util
        _objc = ctypes.CDLL(ctypes.util.find_library('objc'))
        _objc.objc_getClass.restype = ctypes.c_void_p
        _objc.sel_registerName.restype = ctypes.c_void_p
        _objc.objc_msgSend.restype = ctypes.c_void_p
        _objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        ns_app = _objc.objc_msgSend(
            _objc.objc_getClass(b'NSApplication'),
            _objc.sel_registerName(b'sharedApplication'),
        )
        _objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        _objc.objc_msgSend(ns_app, _objc.sel_registerName(b'setActivationPolicy:'),
                           ctypes.c_long(1))  # NSApplicationActivationPolicyAccessory
    except Exception:
        pass


def _macos_float_window(widget, ignore_mouse=False):
    """Flota sobre todas las apps, nunca se oculta al perder foco."""
    try:
        import ctypes, ctypes.util
        _objc = ctypes.CDLL(ctypes.util.find_library('objc'))
        _objc.sel_registerName.restype = ctypes.c_void_p
        _objc.objc_msgSend.restype = ctypes.c_void_p
        _objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        ns_view = ctypes.c_void_p(int(widget.winId()))
        ns_win  = _objc.objc_msgSend(ns_view, _objc.sel_registerName(b'window'))
        if not ns_win:
            return

        def _send_long(sel, val):
            _objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
            _objc.objc_msgSend(ns_win, _objc.sel_registerName(sel), ctypes.c_long(val))

        def _send_bool(sel, val):
            _objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
            _objc.objc_msgSend(ns_win, _objc.sel_registerName(sel), val)

        _send_long(b'setLevel:', 3)                  # NSFloatingWindowLevel
        _send_bool(b'setHidesOnDeactivate:', False)  # nunca se oculta al cambiar app
        # NSWindowCollectionBehaviorCanJoinAllSpaces|Stationary|IgnoresCycle
        _objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64]
        _objc.objc_msgSend(ns_win, _objc.sel_registerName(b'setCollectionBehavior:'),
                           ctypes.c_uint64(1 | 16 | 64))
        if ignore_mouse:
            _send_bool(b'setIgnoresMouseEvents:', True)
    except Exception:
        pass


def _macos_order_front(widget):
    """Trae la ventana al frente SIN activar la app ni robar foco del teclado."""
    try:
        import ctypes, ctypes.util
        _objc = ctypes.CDLL(ctypes.util.find_library('objc'))
        _objc.sel_registerName.restype = ctypes.c_void_p
        _objc.objc_msgSend.restype    = ctypes.c_void_p
        _objc.objc_msgSend.argtypes   = [ctypes.c_void_p, ctypes.c_void_p]
        ns_view = ctypes.c_void_p(int(widget.winId()))
        ns_win  = _objc.objc_msgSend(ns_view, _objc.sel_registerName(b'window'))
        if ns_win:
            _objc.objc_msgSend(ns_win, _objc.sel_registerName(b'orderFrontRegardless'))
    except Exception:
        pass


# ----- Eventos de mouse vía Quartz (pyautogui usa kCGEventMouseMoved siempre,
#        lo que rompe el drag/selección de texto; necesitamos kCGEventLeftMouseDragged
#        cuando el botón está apretado) -----
import Quartz as _Q

def _mouse_move(x, y, dragging=False):
    ev_type = _Q.kCGEventLeftMouseDragged if dragging else _Q.kCGEventMouseMoved
    ev = _Q.CGEventCreateMouseEvent(None, ev_type, (x, y), _Q.kCGMouseButtonLeft)
    _Q.CGEventPost(_Q.kCGHIDEventTap, ev)

def _mouse_down(x, y, click_count=1):
    ev = _Q.CGEventCreateMouseEvent(None, _Q.kCGEventLeftMouseDown, (x, y), _Q.kCGMouseButtonLeft)
    _Q.CGEventSetIntegerValueField(ev, _Q.kCGMouseEventClickState, click_count)
    _Q.CGEventPost(_Q.kCGHIDEventTap, ev)

def _mouse_up(x, y, click_count=1):
    ev = _Q.CGEventCreateMouseEvent(None, _Q.kCGEventLeftMouseUp, (x, y), _Q.kCGMouseButtonLeft)
    _Q.CGEventSetIntegerValueField(ev, _Q.kCGMouseEventClickState, click_count)
    _Q.CGEventPost(_Q.kCGHIDEventTap, ev)

def _mouse_right_down(x, y):
    ev = _Q.CGEventCreateMouseEvent(None, _Q.kCGEventRightMouseDown, (x, y), _Q.kCGMouseButtonRight)
    _Q.CGEventPost(_Q.kCGHIDEventTap, ev)

def _mouse_right_up(x, y):
    ev = _Q.CGEventCreateMouseEvent(None, _Q.kCGEventRightMouseUp, (x, y), _Q.kCGMouseButtonRight)
    _Q.CGEventPost(_Q.kCGHIDEventTap, ev)

def _mouse_middle_down(x, y):
    ev = _Q.CGEventCreateMouseEvent(None, _Q.kCGEventOtherMouseDown, (x, y), _Q.kCGMouseButtonCenter)
    _Q.CGEventPost(_Q.kCGHIDEventTap, ev)

def _mouse_middle_up(x, y):
    ev = _Q.CGEventCreateMouseEvent(None, _Q.kCGEventOtherMouseUp, (x, y), _Q.kCGMouseButtonCenter)
    _Q.CGEventPost(_Q.kCGHIDEventTap, ev)


# Estado compartido entre el hilo de tracking y la UI de Qt.
class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.cursor_x = SCREEN_W / 2
        self.cursor_y = SCREEN_H / 2
        self.is_pinching = False
        self.is_right_clicking = False
        self.is_middle_clicking = False
        self.hand_visible = False
        self.mouse_control_enabled = True
        self._debug_frame = None

    def set_debug_frame(self, frame):
        with self.lock:
            self._debug_frame = frame.copy()

    def get_debug_frame(self):
        with self.lock:
            return self._debug_frame

    def update(self, **kwargs):
        with self.lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def snapshot(self):
        with self.lock:
            return (self.cursor_x, self.cursor_y, self.is_pinching,
                    self.hand_visible, self.mouse_control_enabled)


state = SharedState()


# ----- Cursor con efecto glow (overlay click-through) -----
class GlowCursor(QWidget):
    SIZE = 90  # px del overlay

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(self.SIZE, self.SIZE)
        self.move(int(SCREEN_W / 2), int(SCREEN_H / 2))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)  # ~60 fps

    def showEvent(self, event):
        super().showEvent(event)
        _macos_float_window(self, ignore_mouse=True)

    def _tick(self):
        cx, cy, pinching, visible, _ = state.snapshot()
        if visible:
            self.move(int(cx - self.SIZE / 2), int(cy - self.SIZE / 2))
            if not self.isVisible():
                self.show()
            _macos_order_front(self)
            self.update()
        elif self.isVisible():
            self.hide()

    def paintEvent(self, _event):
        _, _, pinching, _, mouse_on = state.snapshot()
        with state.lock:
            right = state.is_right_clicking
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = QPoint(self.SIZE // 2, self.SIZE // 2)

        with state.lock:
            middle = state.is_middle_clicking
        if not mouse_on:
            color = QColor(180, 180, 180)   # gris: pausado
        elif right:
            color = QColor(180, 80, 255)    # violeta: click derecho
        elif middle:
            color = QColor(80, 220, 160)    # verde: click medio
        elif pinching:
            color = QColor(255, 80, 80)     # rojo: click izquierdo / drag
        else:
            color = QColor(80, 180, 255)    # azul: idle

        # Halo (glow) con gradiente radial
        gradient = QRadialGradient(center, self.SIZE / 2)
        gradient.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 200))
        gradient.setColorAt(0.35, QColor(color.red(), color.green(), color.blue(), 110))
        gradient.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, self.SIZE, self.SIZE)

        # Punto central blanco
        painter.setBrush(QBrush(QColor(255, 255, 255, 235)))
        painter.drawEllipse(self.SIZE // 2 - 6, self.SIZE // 2 - 6, 12, 12)


# ----- Miniatura de debug en la esquina (hilo principal de Qt) -----
class DebugOverlay(QWidget):
    PREVIEW_W = 320
    PREVIEW_H = 180
    MARGIN = 16

    def __init__(self, tracker):
        super().__init__()
        self._tracker = tracker
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._label = QLabel()
        self._label.setFixedSize(self.PREVIEW_W, self.PREVIEW_H)
        self._label.setStyleSheet(
            "border: 1px solid rgba(255,255,255,80);"
            "border-radius: 6px;"
            "background: black;"
        )
        layout.addWidget(self._label)
        self.adjustSize()

        # Posiciona en esquina inferior-izquierda
        self.move(
            self.MARGIN,
            SCREEN_H - self.PREVIEW_H - self.MARGIN * 3,
        )

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)  # ~30 fps es suficiente para preview

    def showEvent(self, event):
        super().showEvent(event)
        _macos_float_window(self)

    def _tick(self):
        if not self._tracker.debug:
            if self.isVisible():
                self.hide()
            return
        if not self.isVisible():
            self.show()
        _macos_order_front(self)

        frame = state.get_debug_frame()
        if frame is None:
            return

        h, w = frame.shape[:2]
        rgb = frame[:, :, ::-1].copy()  # BGR → RGB
        qimg = QImage(rgb.data, w, h, w * 3, QImage.Format_RGB888)
        self._label.setPixmap(
            QPixmap.fromImage(qimg).scaled(
                self.PREVIEW_W, self.PREVIEW_H,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            self._tracker.running = False
            QApplication.quit()
        elif key in (Qt.Key_D,):
            self._tracker.debug = not self._tracker.debug
        elif key in (Qt.Key_M,):
            _, _, _, _, mouse_on = state.snapshot()
            state.update(mouse_control_enabled=not mouse_on)


# ----- Hilo de detección de mano -----
# ----- Ícono en la barra de menú -----
class TrayIcon(QSystemTrayIcon):
    def __init__(self, tracker, parent=None):
        super().__init__(self._make_icon(), parent)
        self._tracker = tracker
        self.setToolTip("Hand Control activo")

        menu = QMenu()
        self._act_mouse = menu.addAction("Mouse: ON")
        self._act_mouse.triggered.connect(self._toggle_mouse)
        self._act_debug = menu.addAction("Debug: ON")
        self._act_debug.triggered.connect(self._toggle_debug)
        menu.addSeparator()
        menu.addAction("Salir").triggered.connect(self._quit)
        self.setContextMenu(menu)
        self.show()

        self._refresh = QTimer()
        self._refresh.timeout.connect(self._update_menu)
        self._refresh.start(400)

    @staticmethod
    def _make_icon(active=True):
        px = QPixmap(22, 22)
        px.fill(QColor(0, 0, 0, 0))
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor(80, 180, 255) if active else QColor(150, 150, 150)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(3, 3, 16, 16)
        p.end()
        return QIcon(px)

    def _toggle_mouse(self):
        _, _, _, _, mouse_on = state.snapshot()
        state.update(mouse_control_enabled=not mouse_on)

    def _toggle_debug(self):
        self._tracker.debug = not self._tracker.debug

    def _quit(self):
        self._tracker.running = False
        QApplication.quit()

    def _update_menu(self):
        _, _, _, _, mouse_on = state.snapshot()
        self._act_mouse.setText("Mouse: ON ✓" if mouse_on else "Mouse: OFF")
        self._act_debug.setText("Debug: ON ✓" if self._tracker.debug else "Debug: OFF")
        self.setIcon(self._make_icon(mouse_on))


# ----- Hilo de detección de mano -----
class HandTracker(threading.Thread):
    def __init__(self, debug=True):
        super().__init__(daemon=True)
        self.debug = debug
        self.running = True
        self._smooth_x = SCREEN_W / 2
        self._smooth_y = SCREEN_H / 2
        self._was_pinching       = False
        self._was_right_clicking = False
        self._was_middle_clicking = False
        self._was_pinky_near     = False
        self._last_pinch_end     = 0.0
        self._click_count        = 1

    def stop(self):
        self.running = False

    def run(self):
        import os
        import urllib.request
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "hand_landmarker.task")
        if not os.path.exists(MODEL_PATH):
            print("Descargando modelo de deteccion de mano (~8 MB)...")
            url = ("https://storage.googleapis.com/mediapipe-models/"
                   "hand_landmarker/hand_landmarker/float16/latest/"
                   "hand_landmarker.task")
            urllib.request.urlretrieve(url, MODEL_PATH)
            print("Modelo listo.")

        base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
        options = mp_vision.HandLandmarkerOptions(
            base_options=base_options,
            running_mode=mp_vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.7,
            min_tracking_confidence=0.5,
        )
        landmarker = mp_vision.HandLandmarker.create_from_options(options)

        HAND_CONNECTIONS = [
            (c.start, c.end)
            for c in mp_vision.HandLandmarksConnections.HAND_CONNECTIONS
        ]

        cap = cv2.VideoCapture(CAMERA_INDEX)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)

        if not cap.isOpened():
            print("ERROR: no se pudo abrir la camara. Revisa permisos en"
                  " Ajustes del Sistema -> Privacidad y seguridad -> Camara.")
            QApplication.quit()
            return

        last_fps_t = time.time()
        frame_count = 0
        fps = 0.0

        while self.running:
            ok, frame = cap.read()
            if not ok:
                continue

            frame = cv2.flip(frame, 1)  # espejo: mover a la derecha mueve a la derecha
            h, w, _ = frame.shape
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            ts_ms = int(time.time() * 1000)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            results = landmarker.detect_for_video(mp_image, ts_ms)

            hand_visible      = False
            is_pinching_now   = self._was_pinching
            is_right_now      = self._was_right_clicking
            is_middle_now     = self._was_middle_clicking
            distance          = 1.0

            if results.hand_landmarks:
                hand_visible = True
                lm = results.hand_landmarks[0]
                index_tip  = lm[8]
                thumb_tip  = lm[4]
                middle_tip = lm[12]
                ring_tip   = lm[16]
                pinky_tip  = lm[20]

                def _dist(a, b):
                    return math.hypot(a.x - b.x, a.y - b.y)

                distance        = _dist(index_tip,  thumb_tip)
                middle_distance = _dist(middle_tip, thumb_tip)
                ring_distance   = _dist(ring_tip,   thumb_tip)
                pinky_distance  = _dist(pinky_tip,  thumb_tip)

                # Punto de control: centro entre índice y pulgar.
                src_x = (index_tip.x + thumb_tip.x) / 2
                src_y = (index_tip.y + thumb_tip.y) / 2

                m = EDGE_MARGIN
                nx = (src_x - m) / (1 - 2 * m)
                ny = (src_y - m) / (1 - 2 * m)
                target_x = max(0, min(SCREEN_W - 1, nx * SCREEN_W))
                target_y = max(0, min(SCREEN_H - 1, ny * SCREEN_H))

                ddx = target_x - self._smooth_x
                ddy = target_y - self._smooth_y
                dist_px = math.hypot(ddx, ddy)
                if dist_px > DEADZONE_PX:
                    move_frac = (dist_px - DEADZONE_PX) / dist_px * SMOOTHING
                    self._smooth_x += ddx * move_frac
                    self._smooth_y += ddy * move_frac

                # Histeresis izquierdo (índice)
                if distance < PINCH_ON_THRESHOLD:
                    is_pinching_now = True
                elif distance > PINCH_OFF_THRESHOLD:
                    is_pinching_now = False

                # Histeresis medio (mayor) — middle click
                if middle_distance < PINCH_ON_THRESHOLD:
                    is_middle_now = True
                elif middle_distance > PINCH_OFF_THRESHOLD:
                    is_middle_now = False

                # Histeresis anular — right click (hold)
                if ring_distance < PINCH_ON_THRESHOLD:
                    is_right_now = True
                elif ring_distance > PINCH_OFF_THRESHOLD:
                    is_right_now = False

                _, _, _, _, mouse_on = state.snapshot()
                if mouse_on:
                    mx, my = int(self._smooth_x), int(self._smooth_y)

                    # Meñique: right click instantáneo (dispara al tocar, no hace falta mantener)
                    pinky_near = pinky_distance < PINCH_ON_THRESHOLD
                    if pinky_near and not self._was_pinky_near:
                        _mouse_right_down(mx, my)
                        _mouse_right_up(mx, my)
                    self._was_pinky_near = pinky_near

                    # Anular: right click sostenido
                    if is_right_now and not self._was_right_clicking:
                        _mouse_right_down(mx, my)
                    elif (not is_right_now) and self._was_right_clicking:
                        _mouse_right_up(mx, my)

                    # Medio/mayor: middle click
                    elif is_middle_now and not self._was_middle_clicking:
                        _mouse_middle_down(mx, my)
                    elif (not is_middle_now) and self._was_middle_clicking:
                        _mouse_middle_up(mx, my)

                    # Índice: left click / drag / doble click
                    elif is_pinching_now and not self._was_pinching:
                        now = time.time()
                        self._click_count = (2 if now - self._last_pinch_end < DOUBLE_CLICK_INTERVAL
                                             else 1)
                        _mouse_down(mx, my, self._click_count)
                    elif (not is_pinching_now) and self._was_pinching:
                        _mouse_up(mx, my, self._click_count)
                        self._last_pinch_end = time.time()
                    else:
                        _mouse_move(mx, my, dragging=is_pinching_now)

                self._was_pinching        = is_pinching_now
                self._was_right_clicking  = is_right_now
                self._was_middle_clicking = is_middle_now

                state.update(
                    cursor_x=self._smooth_x,
                    cursor_y=self._smooth_y,
                    is_pinching=is_pinching_now,
                    is_right_clicking=is_right_now,
                    is_middle_clicking=is_middle_now,
                    hand_visible=True,
                )

                if self.debug:
                    for a, b in HAND_CONNECTIONS:
                        ax, ay = int(lm[a].x * w), int(lm[a].y * h)
                        bx, by = int(lm[b].x * w), int(lm[b].y * h)
                        cv2.line(frame, (ax, ay), (bx, by), (200, 200, 200), 2)
                    for point in lm:
                        px, py = int(point.x * w), int(point.y * h)
                        cv2.circle(frame, (px, py), 4, (255, 255, 255), -1)
                    ix, iy = int(index_tip.x * w), int(index_tip.y * h)
                    tx, ty = int(thumb_tip.x * w), int(thumb_tip.y * h)
                    color = (60, 60, 255) if is_pinching_now else (60, 220, 60)
                    cv2.line(frame, (ix, iy), (tx, ty), color, 2)
                    cv2.circle(frame, (ix, iy), 9, color, -1)
                    cv2.circle(frame, (tx, ty), 9, color, -1)
            else:
                # Mano fuera de cuadro: soltar todos los botones activos.
                mx, my = int(self._smooth_x), int(self._smooth_y)
                if self._was_pinching:
                    _mouse_up(mx, my, self._click_count)
                    self._last_pinch_end = time.time()
                    self._was_pinching = False
                if self._was_right_clicking:
                    _mouse_right_up(mx, my)
                    self._was_right_clicking = False
                if self._was_middle_clicking:
                    _mouse_middle_up(mx, my)
                    self._was_middle_clicking = False
                self._was_pinky_near = False
                state.update(hand_visible=False, is_pinching=False,
                             is_right_clicking=False, is_middle_clicking=False)

            # Overlay de debug (texto).
            if self.debug:
                _, _, _, _, mouse_on = state.snapshot()
                if is_right_now:
                    status, status_color = "CLICK DERECHO", (180, 60, 255)
                elif is_middle_now:
                    status, status_color = "CLICK MEDIO", (60, 220, 140)
                elif is_pinching_now:
                    status, status_color = "CLICK / DRAG", (60, 60, 255)
                else:
                    status, status_color = "MOVER", (60, 220, 60)
                cv2.putText(frame, status, (12, 38),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.1, status_color, 2)
                cv2.putText(frame, f"dist: {distance:.3f}", (12, 70),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1)
                cv2.putText(frame, f"mouse: {'ON' if mouse_on else 'OFF (M)'}",
                            (12, 92), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                            (230, 230, 230) if mouse_on else (80, 80, 255), 1)

                frame_count += 1
                now = time.time()
                if now - last_fps_t > 0.5:
                    fps = frame_count / (now - last_fps_t)
                    frame_count = 0
                    last_fps_t = now
                cv2.putText(frame, f"{fps:4.1f} fps",
                            (w - 110, 28), cv2.FONT_HERSHEY_SIMPLEX,
                            0.6, (200, 200, 200), 1)
                cv2.putText(frame,
                            "ESC salir | D debug | M mouse on/off",
                            (12, h - 16), cv2.FONT_HERSHEY_SIMPLEX,
                            0.5, (200, 200, 200), 1)

                state.set_debug_frame(frame)

        cap.release()
        QApplication.quit()


def main():
    # Abre la cámara brevemente en el hilo principal para disparar el
    # diálogo de permisos de macOS (no se puede hacer desde un hilo secundario).
    _cap = cv2.VideoCapture(CAMERA_INDEX)
    if _cap.isOpened():
        _cap.release()
    else:
        print("Esperando permisos de camara... Acepta el dialogo de macOS.")
        time.sleep(2)
        _cap.release()
    # Con el permiso ya otorgado, el hilo de tracking puede abrir la camara
    # sin necesitar girar el run-loop principal.
    os.environ["OPENCV_AVFOUNDATION_SKIP_AUTH"] = "1"

    app = QApplication(sys.argv)
    _macos_background_app()  # sin ícono en Dock, ventanas no se ocultan al cambiar app

    tracker = HandTracker(debug=True)

    cursor = GlowCursor()
    cursor.show()

    debug_overlay = DebugOverlay(tracker)
    debug_overlay.show()

    tray = TrayIcon(tracker, parent=None)

    # Cmd+Q global
    from PyQt5.QtWidgets import QShortcut
    quit_sc = QShortcut(QKeySequence("Ctrl+Q"), debug_overlay)
    quit_sc.setContext(Qt.ApplicationShortcut)
    quit_sc.activated.connect(lambda: (tracker.stop.__call__() or QApplication.quit()))

    tracker.start()

    exit_code = app.exec_()
    tracker.stop()
    tracker.join(timeout=1.5)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
