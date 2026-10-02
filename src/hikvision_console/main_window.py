from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .api import NvrClient
from .events import EventsPage
from .exports import ExportDialog, ExportJob, ExportManager, ExportsPage
from .live import LivePage
from .playback import PlaybackPage
from .settings_ui import SettingsDialog
from .tasks import TaskPool
from .widgets import ConnectionDialog, button, label


class MainWindow(QMainWindow):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.client = None
        self.device = None
        self.clients = []
        self.generation = 0
        self.polling = False
        self.closed = False
        self.fullscreen_was_maximized = False
        self.setWindowTitle(f"Hikvision Console v{__version__} · 监控工作台")
        self.resize(*settings.get("window_size", [1440, 940]))
        self.setMinimumSize(960, 600)
        self.tasks = TaskPool(self)
        self.exports = ExportManager(self)
        root = QWidget()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        sidebar = QWidget()
        self.sidebar = sidebar
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(240)
        left = QVBoxLayout(sidebar)
        left.setContentsMargins(18, 25, 18, 18)
        left.setSpacing(12)
        left.addWidget(label("HIK / CONSOLE", "Brand"))
        left.addWidget(label("DIRECT • PRIVATE • DESKTOP", "Subtitle"))
        left.addWidget(label(f"v{__version__}", "Subtitle"))
        left.addSpacing(15)
        self.nav = []
        self.pages = QStackedWidget()
        self.live = LivePage(settings)
        self.live.toggle.setEnabled(False)
        self.playback = PlaybackPage(settings, self.tasks)
        self.events = EventsPage(settings)
        self.export_page = ExportsPage(self.exports, settings)
        for i, (name, page) in enumerate((("▦  实时监控", self.live), ("◷  录像回放", self.playback),
                                         ("≋  设备事件", self.events), ("↓  导出任务", self.export_page))):
            b = button(name, lambda checked=False, index=i: self.navigate(index))
            b.setObjectName("Nav")
            b.setCheckable(True)
            self.nav.append(b)
            left.addWidget(b)
            self.pages.addWidget(page)
        left.addSpacing(15)
        channel_header = QHBoxLayout()
        channel_header.addWidget(label("设备通道", "Muted"))
        channel_header.addStretch()
        channel_header.addWidget(button("刷新", self.refresh_device))
        left.addLayout(channel_header)
        self.channel_tree = QTreeWidget()
        self.channel_tree.setHeaderHidden(True)
        self.channel_tree.setRootIsDecorated(False)
        self.channel_tree.itemChanged.connect(self.selection_changed)
        left.addWidget(self.channel_tree, 1)
        self.count_label = label("尚未连接设备", "Subtitle")
        left.addWidget(self.count_label)
        left.addWidget(button("云台控制", self.ptz_dialog))
        left.addWidget(button("客户端设置", self.open_settings))
        self.update_button = button("软件更新", lambda: self.updater.show())
        left.addWidget(self.update_button)
        left.addWidget(button("导出诊断摘要", self.export_diagnostics))
        shell.addWidget(sidebar)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        header = QWidget()
        self.header = header
        header.setObjectName("Header")
        row = QHBoxLayout(header)
        row.setContentsMargins(24, 12, 24, 12)
        self.device_label = label("●  未连接 NVR", "Accent")
        row.addWidget(self.device_label, 1)
        self.connection_button = button("连接设备", self.connect_dialog, True)
        row.addWidget(self.connection_button)
        row.addWidget(button("全屏", self.toggle_fullscreen))
        right_layout.addWidget(header)
        right_layout.addWidget(self.pages, 1)
        self.message = label("就绪 · 不依赖现场额外服务", "Muted")
        self.message.setWordWrap(True)
        self.message.setContentsMargins(24, 5, 24, 12)
        right_layout.addWidget(self.message)
        shell.addWidget(right, 1)
        self.playback.export_requested.connect(self.queue_export)
        self.health_timer = QTimer(self)
        self.health_timer.setInterval(15000)
        self.health_timer.timeout.connect(self.poll_health)
        full = QAction(self)
        full.setShortcut(QKeySequence("F11"))
        full.triggered.connect(self.toggle_fullscreen)
        self.addAction(full)
        escape = QAction(self)
        escape.setShortcut(QKeySequence("Escape"))
        escape.triggered.connect(self.leave_fullscreen)
        self.addAction(escape)
        self.navigate(0)
        from .update_ui import UpdateController
        self.updater = UpdateController(self)
        from .windows_input import VideoMouseInput
        self.video_mouse = VideoMouseInput(self)

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        for i, item in enumerate(self.nav):
            item.setChecked(i == index)
        self.update_fullscreen_layout()

    def connect_dialog(self):
        dialog = ConnectionDialog(self.settings, self)
        if dialog.exec():
            self.connect_device(dialog.connection())

    def connect_device(self, connection, resume_live=False):
        self.generation += 1
        generation = self.generation
        self.health_timer.stop()
        self.live.stop_all()
        self.live.connection = None
        self.live.channels = []
        self.live.enabled = False
        self.live.preview_feedback.stop()
        self.live.toggle.setChecked(False)
        self.live.toggle.setText("开始预览")
        self.live.toggle.setEnabled(False)
        self.live.rebuild()
        self.playback.clear_search()
        self.playback.client = self.playback.device = None
        self.playback.channel.clear()
        self.playback.build_tiles()
        self.events.stop()
        previous = self.client
        self.client = self.device = None
        if previous:
            self.tasks.submit(previous.close)
        self.channel_tree.clear()
        self.count_label.setText("正在读取设备…")
        self.connection_button.setEnabled(False)
        self.device_label.setText("●  正在读取设备…")
        client = NvrClient(connection, recording_time_mode=self.settings.get("recording_time_mode", "auto"))
        self.clients.append(client)

        def loaded(device):
            if generation != self.generation or self.closed:
                return
            self.client, self.device = client, device
            self.settings.update(host=connection.host, username=connection.username,
                                 http_port=connection.http_port, rtsp_port=connection.rtsp_port,
                                 https=connection.https, verify_tls=connection.verify_tls)
            self.live.set_device(connection, device)
            self.live.toggle.setEnabled(True)
            if resume_live:
                self.live.toggle_preview()
            self.playback.set_device(client, device)
            self.events.set_connection(connection)
            self.populate_channels()
            self.device_label.setText(f"●  {device.model}  ·  {connection.host}")
            self.connection_button.setText("切换设备")
            self.connection_button.setEnabled(True)
            disk = "；".join(f"硬盘 {d['id']}：{d['status']}" for d in device.disks)
            self.message.setText(f"{device.firmware} · {disk or '未获取存储状态'} · "
                                 f"设备时区 {device.time:%z} · 录像时间 {'本地兼容' if device.recording_time_mode == 'local' else 'UTC'}" +
                                 (" · 部分可选接口不可用" if device.warnings else ""))
            self.health_timer.start()

        def failed(message):
            if generation != self.generation or self.closed:
                return
            self.device_label.setText("●  设备连接失败")
            self.connection_button.setEnabled(True)
            self.message.setText(message)
        self.tasks.submit(client.discover, loaded, failed)

    def populate_channels(self):
        self.channel_tree.blockSignals(True)
        self.channel_tree.clear()
        for c in self.device.channels:
            item = QTreeWidgetItem([f"{'●' if c.online else '○'}  {c.id:02d}  {c.name}"])
            item.setData(0, Qt.ItemDataRole.UserRole, c.id)
            item.setToolTip(0, c.status)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if c.id in self.live.requested else Qt.CheckState.Unchecked)
            self.channel_tree.addTopLevelItem(item)
        self.channel_tree.blockSignals(False)
        online = sum(c.online for c in self.device.channels)
        self.count_label.setText(f"{online} 在线 / {len(self.device.channels)} 通道")

    def selection_changed(self, *_):
        selected = [self.channel_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                    for i in range(self.channel_tree.topLevelItemCount())
                    if self.channel_tree.topLevelItem(i).checkState(0) == Qt.CheckState.Checked]
        self.live.set_selected(selected)

    def refresh_device(self):
        if self.client:
            self.connect_device(self.client.connection, resume_live=self.live.enabled)

    def poll_health(self):
        if self.polling or not self.client:
            return
        self.polling = True
        generation = self.generation

        def loaded(status):
            self.polling = False
            if generation != self.generation or self.closed:
                return
            changed = False
            need_discovery = False
            for c in self.device.channels:
                online = status.get(c.id, False)
                if online != c.online:
                    c.online, c.status = online, "在线" if online else "离线"
                    changed = True
                    need_discovery = need_discovery or (online and not c.streams)
            if need_discovery or set(status) - {c.id for c in self.device.channels}:
                self.refresh_device()
            elif changed:
                self.populate_channels()
                self.live.reconcile()

        def failed(message):
            self.polling = False
            if generation == self.generation and not self.closed:
                self.message.setText(f"设备状态查询失败 · {message} · 将自动重试")
        self.tasks.submit(self.client.channel_status, loaded, failed)

    def queue_export(self, records, start, bounds):
        dialog = ExportDialog(start, bounds, self)
        if not dialog.exec():
            return
        begin, end = dialog.range()
        jobs = []
        for r in records:
            a, b = max(begin, r.start), min(end, r.end)
            if a >= b:
                continue
            name = f"ch{r.channel_id:02d}_{a.astimezone(self.device.time.tzinfo):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}.mp4"
            # Let FFmpeg delimit the clip. A tightly bounded RTSP endtime can leave
            # this NVR sending only keepalives before the final output timestamp.
            jobs.append(ExportJob(r.channel_id, a, b, self.client.playback_url(r, a, r.end),
                                  self.settings.output_dir("clips") / name, dialog.pace.currentData()))
        if jobs:
            self.start_exports(jobs)
        else:
            QMessageBox.information(self, "导出录像", "所选范围没有可导出的录像")

    def start_exports(self, jobs):
        bridges = [tile.player.bridge for page in (self.live, self.playback) for tile in page.tiles.values()
                   if tile.player.bridge is not None]
        self.navigate(3)
        self.live.stop_all()
        self.playback.stop_all()
        deadline = time.monotonic() + 8
        self.message.setText("正在释放播放连接，随后开始导出…")

        def ready():
            if self.closed:
                return
            pending = any(bridge.process is not None and bridge.process.poll() is None for bridge in bridges)
            if pending and time.monotonic() < deadline:
                QTimer.singleShot(100, ready)
                return
            if pending:
                for job in jobs:
                    job.state, job.error = "失败", "原播放连接尚未释放，请稍后重试"
            self.exports.add(jobs)
            self.message.setText("导出任务已加入队列" if not pending else "等待原播放连接释放超时")
        ready()

    def open_settings(self):
        previous_time_mode = self.settings.get("recording_time_mode", "auto")
        if SettingsDialog(self.settings, self).exec():
            if self.client and previous_time_mode != self.settings.get("recording_time_mode", "auto"):
                self.refresh_device()
            else:
                self.live.stop_all()
                self.live.reconcile()

    def ptz_dialog(self):
        if not self.device:
            self.message.setText("请先连接 NVR")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("云台控制")
        layout = QVBoxLayout(dialog)
        selector = QComboBox()
        for c in self.device.channels:
            if c.online:
                selector.addItem(f"{c.id:02d} · {c.name}", c.id)
        layout.addWidget(selector)
        status = label("请检测通道的云台能力。每次点击只短暂移动，随后自动停止。", "Muted")
        status.setWordWrap(True)
        layout.addWidget(status)
        grid = QGridLayout()
        moves = []
        client = self.client
        cancel = threading.Event()
        busy = [False]

        def move(pan, tilt, zoom):
            if busy[0]:
                return
            busy[0] = True
            cid = selector.currentData()
            for b in moves:
                b.setEnabled(False)

            def work():
                try:
                    client.ptz(cid, pan, tilt, zoom)
                    cancel.wait(0.25)
                finally:
                    client.ptz(cid, 0, 0, 0)

            def finished(_=None):
                busy[0] = False
                if not cancel.is_set():
                    for b in moves:
                        b.setEnabled(True)
            self.tasks.submit(work, finished, lambda message: (status.setText(message), finished()))

        for text, row, col, params in (("↑", 0, 1, (0, 30, 0)), ("←", 1, 0, (-30, 0, 0)),
                                       ("停止", 1, 1, (0, 0, 0)), ("→", 1, 2, (30, 0, 0)),
                                       ("↓", 2, 1, (0, -30, 0)), ("变焦 +", 3, 0, (0, 0, 25)),
                                       ("变焦 −", 3, 2, (0, 0, -25))):
            b = button(text, lambda checked=False, p=params: move(*p))
            b.setEnabled(False)
            moves.append(b)
            grid.addWidget(b, row, col)
        layout.addLayout(grid)

        def check():
            cid = selector.currentData()
            if cid is None:
                return
            for b in moves:
                b.setEnabled(False)
            status.setText("正在检测…")

            def result(supported):
                if cancel.is_set() or selector.currentData() != cid:
                    return
                status.setText("此通道支持云台控制" if supported else "此通道未报告云台能力，控制已禁用")
                for b in moves:
                    b.setEnabled(supported)
            self.tasks.submit(lambda: client.ptz_supported(cid), result, status.setText)
        layout.addWidget(button("检测云台能力", check))
        selector.currentIndexChanged.connect(lambda: [b.setEnabled(False) for b in moves])
        dialog.finished.connect(lambda _: cancel.set())
        dialog.exec()

    def export_diagnostics(self):
        data = {"version": "0.1.3", "device": None, "profile": self.live.profile.currentData(),
                "transport": self.settings.get("transport", "tcp"), "metrics": self.live.last_metrics}
        if self.device:
            data["device"] = {"model": self.device.model, "firmware": self.device.firmware,
                              "channel_count": len(self.device.channels),
                              "channels": [{"id": c.id, "online": c.online,
                                            "streams": {k: vars(s) for k, s in c.streams.items()}}
                                           for c in self.device.channels], "disks": self.device.disks}
        path = self.settings.output_dir("diagnostics") / f"diagnostics_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.message.setText(f"诊断摘要已保存（不含密码、录像地址和监控画面）：{path}")

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.leave_fullscreen()
        else:
            self.showFullScreen()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "message"):
            if self.isFullScreen() and not event.oldState() & Qt.WindowState.WindowFullScreen:
                self.fullscreen_was_maximized = bool(event.oldState() & Qt.WindowState.WindowMaximized)
            self.update_fullscreen_layout()

    def update_fullscreen_layout(self):
        immersive = self.isFullScreen() and self.pages.currentWidget() in (self.live, self.playback)
        for widget in (self.sidebar, self.header, self.message):
            widget.setVisible(not immersive)
        self.setMinimumSize(0, 0) if immersive else self.setMinimumSize(960, 600)
        self.live.set_immersive(immersive)
        self.playback.set_immersive(immersive)

    def leave_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized() if self.fullscreen_was_maximized else self.showNormal()
        elif self.live.focused is not None:
            self.live.focus(None)

    def closeEvent(self, event):
        if self.exports.process:
            answer = QMessageBox.question(self, "关闭应用", "仍有录像正在导出。关闭将取消未完成的导出，是否继续？")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.closed = True
        self.video_mouse.close()
        self.updater.cancel.set()
        self.generation += 1
        self.health_timer.stop()
        self.live.stop_all()
        self.live.rotation.stop()
        self.playback.cancel.set()
        self.playback.stop_all()
        self.events.stop()
        self.exports.shutdown()
        for client in self.clients:
            client.close()
        if not self.isFullScreen():
            self.settings.update(window_size=[self.width(), self.height()])
        event.accept()
