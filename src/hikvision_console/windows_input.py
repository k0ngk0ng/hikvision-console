"""Receive foreground mouse clicks even when VLC owns the native child window."""
import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QCursor


class VideoMouseInput(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.registered = False
        self.previous = None
        self.pending = None
        self.click_count = 0
        if sys.platform != "win32":
            return
        self.user = ctypes.windll.user32

        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD]
        self.user.SetWindowsHookExW.restype = wintypes.HHOOK
        self.user.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        self.user.CallNextHookEx.restype = ctypes.c_ssize_t
        self.user.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]

        class MouseEvent(ctypes.Structure):
            _fields_ = [("point", wintypes.POINT), ("data", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("extra", ctypes.c_size_t)]

        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        def receive(code, event, address):
            # VLC renders into a child owned by its own native thread. A low-level
            # callback reaches this Qt thread before that child consumes the click.
            # Ignore every event outside our foreground window and never suppress input.
            if code >= 0 and event == 0x0201 and self.user.GetForegroundWindow() == int(window.winId()):
                timestamp = MouseEvent.from_address(address).time
                self.left_down(timestamp)
            return self.user.CallNextHookEx(None, code, event, address)

        self.callback = callback_type(receive)
        kernel = ctypes.windll.kernel32
        kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel.GetModuleHandleW.restype = wintypes.HMODULE
        self.hook = self.user.SetWindowsHookExW(14, self.callback, kernel.GetModuleHandleW(None), 0)  # WH_MOUSE_LL
        self.registered = bool(self.hook)

    def close(self):
        if self.registered:
            self.user.UnhookWindowsHookEx(self.hook)
            self.registered = False

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
        prior = self.previous
        self.previous = (tile, timestamp, cursor.x(), cursor.y())
        if prior and prior[0] is tile and (timestamp-prior[1]) & 0xffffffff <= self.user.GetDoubleClickTime():
            if (abs(cursor.x()-prior[2]) <= self.user.GetSystemMetrics(36) // 2
                    and abs(cursor.y()-prior[3]) <= self.user.GetSystemMetrics(37) // 2):
                self.previous = None
                self.request_focus(tile)

    def request_focus(self, tile):
        if self.pending is tile:
            return
        self.pending = tile

        def apply():
            if self.pending is tile:
                self.pending = None
            page = self.window.live
            if page.tiles.get(tile.channel.id) is tile:
                page.focus(tile.channel.id)
        QTimer.singleShot(0, apply)
