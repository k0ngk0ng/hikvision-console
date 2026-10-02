"""Observe application-local Cocoa mouse events before VLC's NSView consumes them."""
import ctypes
import ctypes.util

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer
from PySide6.QtGui import QCursor


class VideoMouseInput(QAbstractNativeEventFilter):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.pending = None
        self.registered = True
        objc = ctypes.CDLL(ctypes.util.find_library("objc"))
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        self.kind = objc.sel_registerName(b"type")
        self.clicks = objc.sel_registerName(b"clickCount")
        self.native_window = objc.sel_registerName(b"window")
        self.key_code = objc.sel_registerName(b"keyCode")
        self.send = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(
            ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value)

    def close(self):
        self.registered = False

    def nativeEventFilter(self, event_type, message):
        if not self.registered or bytes(event_type) != b"mac_generic_NSEvent":
            return False, 0
        event = int(message)
        kind = self.send(event, self.kind)
        if kind not in (1, 10):  # NSEventTypeLeftMouseDown / NSEventTypeKeyDown
            return False, 0
        # A VLC NSView may own Cocoa first-responder status without Qt marking
        # MainWindow active. Match the actual NSEvent/NSView window instead.
        if self.send(event, self.native_window) != self.send(int(self.window.winId()), self.native_window):
            return False, 0
        if kind == 10:
            code = self.send(event, self.key_code)
            if code == 53 and self.window.isFullScreen():  # Escape
                QTimer.singleShot(0, self.window.leave_fullscreen)
                return True, 0
            if code == 103:  # F11
                QTimer.singleShot(0, self.window.toggle_fullscreen)
                return True, 0
            return False, 0
        if self.send(event, self.clicks) != 2:
            return False, 0
        page = self.window.live
        if not page.isVisible():
            return False, 0
        cursor = QCursor.pos()
        if not page.scroll.viewport().rect().contains(page.scroll.viewport().mapFromGlobal(cursor)):
            return False, 0
        for tile in page.tiles.values():
            if tile.surface.isVisible() and tile.surface.rect().contains(tile.surface.mapFromGlobal(cursor)):
                self.request_focus(tile)
                # This gesture belongs to the grid, not VLC's NSView. Delivering
                # it again can move native focus onto a view being retired.
                return True, 0
        return False, 0

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
                self.window.activateWindow()
                self.window.setFocus()
        QTimer.singleShot(0, apply)
