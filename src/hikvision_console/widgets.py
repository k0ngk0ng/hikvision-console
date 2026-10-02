from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QMimeData, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .models import Connection
from .player import Player


def label(text, name=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, slot=None, primary=False):
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("Primary")
    if slot:
        widget.clicked.connect(slot)
    return widget


class ConnectionDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("连接 NVR")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.addWidget(label("连接你的录像机", "PageTitle"))
        note = label("通过已有的局域网或 WireGuard 访问。\n地址和账号会保存，密码仅用于本次会话。", "Muted")
        layout.addWidget(note)
        form = QFormLayout()
        self.host = QLineEdit(settings.get("host", "192.168.1.100"))
        self.user = QLineEdit(settings.get("username", "admin"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.http = QSpinBox()
        self.http.setRange(1, 65535)
        self.http.setValue(settings.get("http_port", 80))
        self.rtsp = QSpinBox()
        self.rtsp.setRange(1, 65535)
        self.rtsp.setValue(settings.get("rtsp_port", 554))
        self.https = QCheckBox("HTTPS")
        self.https.setChecked(settings.get("https", False))
        self.https.toggled.connect(lambda checked: self.http.setValue(443 if checked else 80)
                                    if self.http.value() in (80, 443) else None)
        self.verify = QCheckBox("验证 TLS 证书")
        self.verify.setChecked(settings.get("verify_tls", True))
        for name, widget in (("NVR 地址", self.host), ("账号", self.user), ("密码", self.password),
                             ("设备接口端口", self.http), ("RTSP 端口", self.rtsp),
                             ("安全连接", self.https), ("", self.verify)):
            form.addRow(name, widget)
        layout.addLayout(form)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Ok).setText("连接")
        controls.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        controls.accepted.connect(self._accept)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)

    def _accept(self):
        try:
            self.connection()
            if not self.password.text():
                raise ValueError("请输入密码")
            self.accept()
        except ValueError as exc:
            QMessageBox.warning(self, "连接设置", str(exc))

    def connection(self):
        return Connection(self.host.text().strip(), self.user.text().strip(), self.password.text(),
                          self.http.value(), self.rtsp.value(), self.https.isChecked(), self.verify.isChecked())


class DragTitle(QLabel):
    double_clicked = Signal()

    def __init__(self, text, channel_id):
        super().__init__(text)
        self.channel_id = channel_id
        self.origin = None
        self.setToolTip("拖动标题交换画面位置；双击放大")

    def mousePressEvent(self, event):
        self.origin = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()

    def mouseMoveEvent(self, event):
        if self.origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.position().toPoint()-self.origin).manhattanLength() > 10:
                mime = QMimeData()
                mime.setData("application/x-hikvision-channel", str(self.channel_id).encode())
                drag = QDrag(self)
                drag.setMimeData(mime)
                drag.exec(Qt.DropAction.MoveAction)
                self.origin = None


class VideoSurface(QFrame):
    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class VideoTile(QFrame):
    quality_changed = Signal(int, str)
    focus_requested = Signal(int)
    mute_requested = Signal(int, bool)
    metrics_changed = Signal(int, dict)
    swapped = Signal(int, int)

    def __init__(self, channel, settings, parent=None, playback=False):
        super().__init__(parent)
        self.channel, self.settings = channel, settings
        self.immersive = False
        self.setObjectName("Tile")
        self.setAcceptDrops(not playback)
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(5)
        self.header = QWidget()
        self.header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        header = QHBoxLayout(self.header)
        header.setContentsMargins(0, 0, 0, 0)
        self.title = DragTitle(f"{channel.id:02d}  {channel.name}", channel.id)
        self.title.setObjectName("TileTitle")
        self.title.double_clicked.connect(lambda: self.focus_requested.emit(channel.id))
        header.addWidget(self.title, 1)
        self.quality = QComboBox()
        self.quality.addItem("流畅", "sub")
        self.quality.addItem("高清", "main")
        self.quality.setFixedWidth(78)
        self.quality.setVisible(not playback)
        self.quality.currentIndexChanged.connect(
            lambda: self.quality_changed.emit(channel.id, self.quality.currentData()))
        header.addWidget(self.quality)
        expand = button("⛶", lambda: self.focus_requested.emit(channel.id))
        expand.setToolTip("放大 / 返回网格")
        expand.setVisible(not playback)
        expand.setFixedWidth(38)
        header.addWidget(expand)
        layout.addWidget(self.header)
        self.surface = VideoSurface()
        self.surface.setObjectName("VideoSurface")
        self.surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.surface.setMinimumSize(180, 64)
        if not playback:
            # Rebuilding the grid can retire this native surface; wait until the
            # mouse event has returned before switching to the selected channel.
            self.surface.double_clicked.connect(self.focus_from_surface, Qt.ConnectionType.QueuedConnection)
        layout.addWidget(self.surface, 1)
        self.state = label("等待连接" if channel.online else channel.status, "Muted")
        self.state.setWordWrap(True)
        self.state.setMaximumHeight(38)
        self.status_row = QWidget()
        self.status_row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        status_layout = QHBoxLayout(self.status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.addWidget(self.state, 1)
        self.stats = label("— fps  ·  — kbps", "Subtitle")
        status_layout.addWidget(self.stats)
        layout.addWidget(self.status_row)
        self.footer = QWidget()
        self.footer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addStretch()
        self.mute = button("静音")
        self.mute.setCheckable(True)
        self.mute.setToolTip("仅选中的一路播放声音")
        self.mute.toggled.connect(lambda enabled: self.mute_requested.emit(channel.id, not enabled))
        footer.addWidget(self.mute)
        self.snap = button("截图", self.screenshot)
        footer.addWidget(self.snap)
        self.zoom = QComboBox()
        for text, value in (("1×", 1), ("1.5×", 1.5), ("2×", 2), ("3×", 3), ("4×", 4)):
            self.zoom.addItem(text, value)
        self.zoom.setToolTip("数字放大（画面中心）")
        self.zoom.setFixedWidth(66)
        footer.addWidget(self.zoom)
        layout.addWidget(self.footer)
        self.player = Player(self.surface, self)
        self.player.status.connect(self.set_state)
        self.player.metrics.connect(self._metrics)
        self.zoom.currentIndexChanged.connect(lambda: self.player.set_zoom(self.zoom.currentData()))

    def focus_from_surface(self):
        # Windows native video children may consume Qt double-click events. Native
        # input owns the gesture there, avoiding duplicate toggles when both arrive.
        window = self.window()
        if getattr(window, "video_mouse", None) and window.video_mouse.registered:
            window.video_mouse.request_focus(self)
            return
        self.focus_requested.emit(self.channel.id)

    def set_immersive(self, enabled):
        self.immersive = enabled
        for widget in (self.header, self.status_row, self.footer):
            widget.setVisible(not enabled)
        self.layout().setContentsMargins(*(0, 0, 0, 0) if enabled else (8, 7, 8, 7))
        self.layout().setSpacing(0 if enabled else 5)
        self.surface.setMinimumSize(0, 0) if enabled else self.surface.setMinimumSize(180, 64)
        self.setProperty("immersive", enabled)
        # Reapply the stylesheet so QFrame recalculates its content rectangle,
        # including the former one-pixel border inset.
        self.setStyleSheet("QFrame#Tile { border: 0px; border-radius: 0px; padding: 0px; margin: 0px; }"
                           if enabled else "")
        self.player.set_fill_surface(enabled)

    def set_state(self, text):
        self.state.setText(text)
        self.state.setToolTip(text)
        warning = any(word in text for word in ("未更新", "中断", "失败", "不可用", "落后", "拒绝", "仅接受"))
        color = "#ffad83" if warning else "#55ddbf" if text in ("实时播放", "录像回放") else "#8191a6"
        self.state.setStyleSheet(f"color: {color};")
        if bool(self.property("warning")) != warning:
            self.setProperty("warning", warning)
            self.style().unpolish(self)
            self.style().polish(self)

    def _metrics(self, data):
        self.stats.setText(f"{data['fps']:.0f} fps  ·  {data['kbps']:.0f} kbps")
        self.stats.setToolTip(f"重连 {data['retries']} 次 · 最后画面距今 {data['stale_seconds']:.1f} 秒\n"
                             f"相对首帧的额外滞后估计 {data.get('estimated_backlog_s', 0):.1f} 秒（非端到端延迟）")
        self.metrics_changed.emit(self.channel.id, data)

    def set_muted(self, value):
        self.mute.blockSignals(True)
        self.mute.setChecked(not value)
        self.mute.setText("静音" if value else "声音开")
        self.mute.blockSignals(False)
        self.player.set_muted(value)

    def screenshot(self):
        path = self.settings.output_dir("snapshots") / (
            f"ch{self.channel.id:02d}_{datetime.now():%Y%m%d_%H%M%S_%f}.png")
        if self.player.screenshot(path):
            self.state.setText("正在保存截图…")

            def verify(attempt=0):
                if path.exists() and path.stat().st_size:
                    self.state.setText(f"截图已保存：{path.name}")
                elif attempt < 10:
                    QTimer.singleShot(200, lambda: verify(attempt+1))
                else:
                    self.state.setText("截图未成功写入，请检查导出目录")
            QTimer.singleShot(200, verify)
        else:
            self.state.setText("截图失败：请等待画面开始播放")

    def stop(self):
        self.player.stop()

    def dispose(self):
        """Keep the native render surface alive until VLC's asynchronous stop completes."""
        self.stop()
        self.hide()
        self.setParent(None)

        def release():
            if any(t.is_alive() for t in self.player.retiring):
                QTimer.singleShot(100, release)
            else:
                self.deleteLater()
        release()

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-hikvision-channel"):
            event.acceptProposedAction()

    def dropEvent(self, event):
        try:
            source = int(bytes(event.mimeData().data("application/x-hikvision-channel")))
        except ValueError:
            return
        self.swapped.emit(source, self.channel.id)
        event.acceptProposedAction()


class Timeline(QWidget):
    seek_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(112)
        self.setMouseTracking(True)
        self.start = None
        self.end = None
        self.records = []
        self.cursor = None
        self.zoom = 1

    def set_day(self, start, end, records):
        self.start, self.end, self.records = start, end, records
        self.cursor = None
        self.update()

    def position(self, when):
        if not self.start or not self.end:
            return 24
        return 24 + (when - self.start).total_seconds() / (self.end - self.start).total_seconds() * (self.width()-48)

    def time_at(self, x):
        portion = max(0, min(1, (x - 24) / max(1, self.width()-48)))
        return self.start + (self.end - self.start) * portion

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#111b28"))
        if not self.start:
            painter.setPen(QColor("#7f92a9"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "选择日期并检索录像")
            return
        painter.fillRect(QRectF(24, 43, self.width()-48, 25), QColor("#233044"))
        for r in self.records:
            a, b = max(self.start, r.start), min(self.end, r.end)
            if a < b:
                painter.fillRect(QRectF(self.position(a), 43, max(2, self.position(b)-self.position(a)), 25),
                                 QColor("#2cbf9a"))
        painter.setPen(QPen(QColor("#8598b0"), 1))
        ticks = max(4, min(12, self.width() // 90))
        for i in range(ticks + 1):
            at = self.start + (self.end-self.start) * i / ticks
            x = self.position(at)
            painter.drawLine(int(x), 71, int(x), 77)
            painter.drawText(QRectF(x-25, 80, 50, 22), Qt.AlignmentFlag.AlignCenter, at.strftime("%H:%M"))
        if self.cursor:
            x = self.position(self.cursor)
            painter.setPen(QPen(QColor("#f0bc67"), 2))
            painter.drawLine(int(x), 28, int(x), 75)
            painter.drawText(QRectF(max(0, min(x-50, self.width()-105)), 3, 105, 23),
                             Qt.AlignmentFlag.AlignCenter, self.cursor.astimezone(self.start.tzinfo).strftime("%H:%M:%S"))

    def mousePressEvent(self, event):
        if self.start and event.button() == Qt.MouseButton.LeftButton:
            at = self.time_at(event.position().x())
            self.cursor = at
            self.update()
            self.seek_requested.emit(at)

    def mouseMoveEvent(self, event):
        if self.start:
            at = self.time_at(event.position().x())
            present = any(r.contains(at) for r in self.records)
            self.setToolTip(f"{at:%H:%M:%S} · {'有录像' if present else '无录像'}")

    def wheelEvent(self, event):
        if not self.start:
            return
        center = self.time_at(event.position().x())
        seconds = (self.end-self.start).total_seconds()
        new_seconds = max(60, min(86400, seconds * (0.7 if event.angleDelta().y() > 0 else 1/0.7)))
        fraction = (center-self.start).total_seconds()/seconds
        self.start = center - timedelta(seconds=new_seconds*fraction)
        self.end = self.start + timedelta(seconds=new_seconds)
        self.update()
