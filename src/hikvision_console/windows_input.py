"""Receive foreground mouse clicks even when VLC owns the native child window."""
import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer
from PySide6.QtGui import QCursor


class VideoMouseInput(QAbstractNativeEventFilter):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.registered = False
        self.previous = None
        self.pending = None
        self.click_count = self.hit_count = self.focus_count = self.apply_count = 0
        if sys.platform != "win32":
            return
        self.user = ctypes.windll.user32

        class Device(ctypes.Structure):
            _fields_ = [("page", wintypes.USHORT), ("usage", wintypes.USHORT),
                        ("flags", wintypes.DWORD), ("target", wintypes.HWND)]

        class Header(ctypes.Structure):
            _fields_ = [("kind", wintypes.DWORD), ("size", wintypes.DWORD),
                        ("device", wintypes.HANDLE), ("param", wintypes.WPARAM)]

        class Mouse(ctypes.Structure):
            _fields_ = [("flags", wintypes.USHORT), ("buttons", wintypes.ULONG),
                        ("raw_buttons", wintypes.ULONG), ("x", wintypes.LONG),
                        ("y", wintypes.LONG), ("extra", wintypes.ULONG)]
        self.Device, self.Header, self.Mouse = Device, Header, Mouse
        self.user.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p,
                                              ctypes.POINTER(wintypes.UINT), wintypes.UINT]
        self.user.GetForegroundWindow.restype = wintypes.HWND
        # No INPUTSINK: input is delivered only while this application is foreground.
        device = Device(1, 2, 0, int(window.winId()))
        self.registered = bool(self.user.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device)))

    def close(self):
        if self.registered:
            device = self.Device(1, 2, 1, None)  # RIDEV_REMOVE
            self.user.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device))
            self.registered = False

    def nativeEventFilter(self, event_type, message):
        if not self.registered:
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        if msg.message != 0x00FF or self.user.GetForegroundWindow() != int(self.window.winId()):
            return False, 0
        size = wintypes.UINT()
        self.user.GetRawInputData(msg.lParam, 0x10000003, None, ctypes.byref(size), ctypes.sizeof(self.Header))
        if not 0 < size.value < 4096:
            return False, 0
        buffer = ctypes.create_string_buffer(size.value)
        count = self.user.GetRawInputData(msg.lParam, 0x10000003, buffer, ctypes.byref(size), ctypes.sizeof(self.Header))
        if count != size.value or size.value < ctypes.sizeof(self.Header) + ctypes.sizeof(self.Mouse):
            return False, 0
        if self.Header.from_buffer(buffer).kind != 0:
            return False, 0
        mouse = self.Mouse.from_buffer(buffer, ctypes.sizeof(self.Header))
        if mouse.buttons & 1:  # RI_MOUSE_LEFT_BUTTON_DOWN
            self.left_down(msg.time)
        return False, 0

    def left_down(self, timestamp):
        self.click_count += 1
        page = self.window.live
        if not page.isVisible():
            self.previous = None
            return
        # Qt global coordinates account for per-monitor DPI; Win32 cursor pixels
        # do not match widget geometry at 125%/150% display scaling.
        cursor = QCursor.pos()
        tile = next((tile for tile in page.tiles.values()
                     if tile.surface.isVisible() and tile.surface.rect().contains(tile.surface.mapFromGlobal(cursor))), None)
        # Respect scroll clipping: a native surface may extend outside the viewport.
        if tile is None or not page.scroll.viewport().rect().contains(page.scroll.viewport().mapFromGlobal(cursor)):
            self.previous = None
            return
        self.hit_count += 1
        prior = self.previous
        self.previous = (tile, timestamp, cursor.x(), cursor.y())
        if prior and prior[0] is tile and (timestamp-prior[1]) & 0xffffffff <= self.user.GetDoubleClickTime():
            if (abs(cursor.x()-prior[2]) <= self.user.GetSystemMetrics(36) // 2
                    and abs(cursor.y()-prior[3]) <= self.user.GetSystemMetrics(37) // 2):
                self.previous = None
                self.request_focus(tile)

    def request_focus(self, tile):
        self.focus_count += 1
        if self.pending is tile:
            return
        self.pending = tile

        def apply():
            if self.pending is tile:
                self.pending = None
            page = self.window.live
            if page.tiles.get(tile.channel.id) is tile:
                self.apply_count += 1
                page.focus(tile.channel.id)
        QTimer.singleShot(0, apply)
