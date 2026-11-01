from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta

from PySide6.QtCore import QDate, QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .widgets import Timeline, VideoTile, button, label


class PlaybackPage(QWidget):
    export_requested = Signal(object, object, object)

    def __init__(self, settings, tasks, parent=None):
        super().__init__(parent)
        self.settings, self.tasks = settings, tasks
        self.client = None
        self.device = None
        self.records = {}
        self.tiles = {}
        self.current = {}
        self.generation = 0
        self.cancel = threading.Event()
        self.linked = []
        self.target = None
        self.clock = None
        self.offsets = {}
        self.paused = False
        self.seek_generation = 0
        self.sync_pending = set()
        self.sync_hold = set()
        self.clocks = {}
        self.anchors = {}
        self.recovery_counts = {}
        self.immersive = False
        self.face_scanning = False
        self.face_generation = 0
        self.face_busy = False
        self.face_marks = []
        self.face_timer = QTimer(self)
        self.face_timer.setInterval(350)
        self.face_timer.timeout.connect(self.scan_face_frame)
        self.controls_timer = QTimer(self)
        self.controls_timer.setSingleShot(True)
        self.controls_timer.timeout.connect(self.hide_immersive_controls)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.stop_if_hidden)
        QApplication.instance().installEventFilter(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        self.toolbar = QWidget()
        toolbar_layout = QVBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(label("录像回放", "PageTitle"))
        head.addStretch()
        self.link_button = button("联动通道", self.choose_linked)
        head.addWidget(self.link_button)
        self.channel = QComboBox()
        self.channel.setMinimumWidth(160)
        self.channel.currentIndexChanged.connect(self.clear_search)
        head.addWidget(self.channel)
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.dateChanged.connect(self.clear_search)
        head.addWidget(self.date)
        self.search_button = button("检索录像", self.search, True)
        head.addWidget(self.search_button)
        toolbar_layout.addLayout(head)
        self.notice = label("选择通道和日期，查看 NVR 中已有的录像。", "Muted")
        self.notice.setWordWrap(True)
        toolbar_layout.addWidget(self.notice)
        self.sync_label = label("", "Muted")
        self.sync_label.hide()
        toolbar_layout.addWidget(self.sync_label)
        layout.addWidget(self.toolbar)
        self.video_area = QWidget()
        self.grid = QGridLayout(self.video_area)
        self.grid.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.video_area, 1)
        self.timeline = Timeline()
        self.timeline.seek_requested.connect(self.seek)
        self.footer = QWidget()
        footer_layout = QVBoxLayout(self.footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        self.pause_button = button("暂停", self.toggle_pause)
        controls.addWidget(self.pause_button)
        controls.addWidget(button("−5 秒", lambda: self.seek_relative(-5)))
        controls.addWidget(button("+5 秒", lambda: self.seek_relative(5)))
        controls.addWidget(button("重试", lambda: self.seek(self.clock) if self.clock else None))
        self.speed = QComboBox()
        for value in (0.5, 1, 2, 4, 8, 16):
            self.speed.addItem(f"{value:g}×", value)
        self.speed.setCurrentIndex(1)
        self.speed.currentIndexChanged.connect(self.change_speed)
        self.speed.setToolTip("播放速度；[ 减速，] 加速，最高 16×。设备可能拒绝倍速请求。")
        controls.addWidget(self.speed)
        self.faster_button = button("快进 ⏩", lambda: self.step_speed(1))
        controls.addWidget(self.faster_button)
        self.face_button = button("找人脸", self.toggle_face_scan)
        self.face_button.setToolTip("从当前位置加速回放，在本机抽样检测正面人脸，命中后暂停。可能漏检或误检，不识别身份。")
        controls.addWidget(self.face_button)
        footer_layout.addLayout(controls)
        navigation = QHBoxLayout()
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm:ss")
        navigation.addWidget(self.time)
        navigation.addWidget(button("定位", self.seek_clock))
        navigation.addWidget(button("全天", self.reset_timeline))
        help_button = button("快捷键", self.show_shortcuts)
        navigation.addWidget(help_button)
        navigation.addStretch()
        self.time_label = label("—", "Accent")
        navigation.addWidget(self.time_label)
        navigation.addWidget(button("导出片段", self.export))
        footer_layout.addLayout(navigation)
        footer_layout.addWidget(self.timeline)
        layout.addWidget(self.footer)

    def set_immersive(self, enabled):
        changed = self.immersive != enabled
        self.immersive = enabled
        self.toolbar.setVisible(not enabled)
        if changed or not enabled:
            self.controls_timer.stop()
            self.footer.setVisible(not enabled)
        self.layout().setContentsMargins(*(0, 0, 0, 0) if enabled else (24, 20, 24, 16))
        self.layout().setSpacing(0 if enabled else -1)
        self.grid.setSpacing(0 if enabled else -1)
        for tile in self.tiles.values():
            tile.set_immersive(enabled)

    def video_clicked(self):
        if self.immersive:
            self.footer.setVisible(self.footer.isHidden())
            if self.footer.isVisible():
                self.controls_timer.start(5000)

    def hide_immersive_controls(self):
        if self.immersive:
            if self.footer.underMouse() or self.footer.isAncestorOf(QApplication.focusWidget()):
                self.controls_timer.start(2000)
            else:
                self.footer.hide()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and self.isVisible():
            if self.handle_key(event.key(), event.modifiers()):
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def handle_key(self, key, modifiers=Qt.KeyboardModifier.NoModifier):
        if not self.isVisible() or QApplication.activeModalWidget():
            return False
        focus = QApplication.focusWidget()
        if isinstance(focus, (QAbstractSpinBox, QLineEdit, QComboBox)):
            return False
        if modifiers & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
            return False
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            seconds = 30 if modifiers & Qt.KeyboardModifier.ControlModifier else 5
            self.seek_relative(seconds if key == Qt.Key.Key_Right else -seconds)
        elif key == Qt.Key.Key_Space and not modifiers:
            self.toggle_pause()
        elif key in (Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight) and not modifiers:
            self.step_speed(1 if key == Qt.Key.Key_BracketRight else -1)
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus):
            tile = self.tiles.get(self.channel.currentData())
            if tile:
                tile.zoom.setCurrentIndex(max(0, min(tile.zoom.count()-1,
                                                    tile.zoom.currentIndex()+(-1 if key == Qt.Key.Key_Minus else 1))))
        else:
            return False
        if self.immersive:
            self.footer.show()
            self.controls_timer.start(5000)
        return True

    def show_shortcuts(self):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(self, "回放快捷键", "空格：暂停 / 继续\n← / →：后退 / 前进 5 秒\n"
                                "Ctrl + ← / →：后退 / 前进 30 秒\n[ / ]：减速 / 加速（最高 16×）\n"
                                "− / +：缩小 / 放大，单击画面调整放大中心\nF11：全屏；Esc：退出全屏\n"
                                "全屏单击画面：显示 / 隐藏时间轴")

    def step_speed(self, direction):
        self.speed.setCurrentIndex(max(0, min(self.speed.count()-1, self.speed.currentIndex()+direction)))

    def set_device(self, client, device):
        self.clear_search()
        self.client, self.device = client, device
        self.channel.blockSignals(True)
        self.channel.clear()
        for c in device.channels:
            self.channel.addItem(f"{c.id:02d} · {c.name}", c.id)
        self.channel.blockSignals(False)
        d = device.time.date()
        self.date.setDate(QDate(d.year, d.month, d.day))
        self.build_tiles()

    def day_bounds(self):
        d = self.date.date().toPython()
        start = datetime(d.year, d.month, d.day, tzinfo=self.device.time.tzinfo)
        return start, start + timedelta(days=1)

    def selected_ids(self):
        main = self.channel.currentData()
        return list(dict.fromkeys([main] + self.linked)) if main is not None else []

    def clear_search(self, *_):
        self.cancel_face_scan(restore=False)
        self.face_marks.clear()
        self.generation += 1
        self.seek_generation += 1
        self.sync_pending.clear()
        self.sync_hold.clear()
        self.clocks.clear()
        self.anchors.clear()
        self.recovery_counts.clear()
        self.cancel.set()
        self.cancel = threading.Event()
        self.records.clear()
        self.stop_all()
        self.current.clear()
        self.target = self.clock = None
        self.timeline.set_day(None, None, [])
        self.timeline.markers = []
        self.search_button.setEnabled(True)
        if self.device:
            self.build_tiles()

    def build_tiles(self):
        self.stop_all()
        for tile in self.tiles.values():
            self.grid.removeWidget(tile)
            tile.dispose()
        self.tiles.clear()
        if not self.device:
            return
        ids = self.selected_ids()
        columns = 1 if len(ids) == 1 else 2 if len(ids) <= 4 else 3
        for index, cid in enumerate(ids):
            c = next((c for c in self.device.channels if c.id == cid), None)
            if not c:
                continue
            tile = VideoTile(c, self.settings, playback=True)
            tile.set_immersive(self.immersive)
            tile.state.setText("等待录像检索")
            tile.metrics_changed.connect(self.metrics)
            tile.surface.point_clicked.connect(lambda *_: self.video_clicked())
            tile.player.ended.connect(lambda cid=cid: self.on_ended(cid))
            tile.player.playing.connect(lambda cid=cid: self.ready(cid))
            tile.player.failed.connect(lambda reason, cid=cid: self.recover(cid, reason))
            tile.mute_requested.connect(self.mute)
            self.tiles[cid] = tile
            self.grid.addWidget(tile, index // columns, index % columns)

    def choose_linked(self):
        if not self.device:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("联动回放通道")
        layout = QVBoxLayout(dialog)
        layout.addWidget(label("各路定位到同一录像时间；受源端和网络影响，\n不保证逐帧同步。多路回放会增加带宽占用。", "Muted"))
        listing = QListWidget()
        for c in self.device.channels:
            if c.id == self.channel.currentData():
                continue
            item = QListWidgetItem(f"{c.id:02d} · {c.name}")
            item.setData(Qt.ItemDataRole.UserRole, c.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if c.id in self.linked else Qt.CheckState.Unchecked)
            listing.addItem(item)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            self.linked = [listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count())
                           if listing.item(i).checkState() == Qt.CheckState.Checked]
            self.clear_search()

    def search(self):
        if not self.client:
            return
        self.clear_search()
        generation, cancel, client = self.generation, self.cancel, self.client
        ids = self.selected_ids()
        start, end = self.day_bounds()
        self.notice.setText(f"正在检索 {len(ids)} 路录像 · 设备时区 {start:%z} …")
        self.search_button.setEnabled(False)

        def work():
            results = {}
            for cid in ids:
                if cancel.is_set():
                    break
                results[cid] = client.search(cid, start, end, cancel)
            return results

        def success(data):
            if generation != self.generation:
                return
            self.records = data
            self.search_button.setEnabled(True)
            primary = data.get(self.channel.currentData(), [])
            self.timeline.set_day(start, end, primary)
            self.notice.setText(f"找到 {len(primary)} 段录像 · 设备时区 {start:%z} · "
                                "点击时间轴定位，滚轮缩放；录像空缺自动跳过")
            if primary:
                self.seek(max(start, primary[0].start), preserve_pause=False)
            if not primary:
                self.notice.setText("所选通道当天没有录像。离线通道也可以查询已有录像。")

        def failure(message):
            if generation == self.generation:
                self.search_button.setEnabled(True)
                self.notice.setText(message)
        self.tasks.submit(work, success, failure)

    def seek(self, when, *, preserve_pause=True, direction=1):
        if not self.client or not self.records or when is None:
            return
        if self.face_scanning:
            self.cancel_face_scan()
        primary = self.records.get(self.channel.currentData(), [])
        if not primary:
            return
        start, end = self.day_bounds()
        when = max(start, min(end-timedelta(milliseconds=1), when))
        if not any(r.contains(when) for r in primary):
            following = next((r for r in primary if r.end > when and r.start < end), None)
            previous = next((r for r in reversed(primary) if r.start < when and r.end > start), None)
            if direction >= 0 and following:
                when = max(start, following.start)
            elif previous:
                when = min(end, previous.end)-timedelta(milliseconds=1)
            elif following:
                when = max(start, following.start)
            else:
                return
        paused = self.paused if preserve_pause else False
        self.target = self.clock = when
        self.seek_generation += 1
        seek_generation = self.seek_generation
        self.clocks.clear()
        self.anchors.clear()
        self.recovery_counts.clear()
        matching = {cid: next((r for r in self.records.get(cid, []) if r.contains(when)), None)
                    for cid in self.tiles}
        self.sync_pending = {cid for cid, record in matching.items() if record is not None}
        if len(self.sync_pending) < 2:
            self.sync_pending.clear()
        self.sync_hold.clear()
        self.sync_label.setVisible(len(self.tiles) > 1)
        self.sync_label.setText("正在等待各路就绪后共同开始…")
        self.offsets.clear()
        self.paused = paused
        self.pause_button.setText("继续" if paused else "暂停")
        for cid, tile in self.tiles.items():
            record = matching[cid]
            previous_bridge = tile.player.bridge
            tile.stop()
            if not record:
                tile.state.setText("此时段没有录像")
                self.current.pop(cid, None)
                continue
            self.current[cid] = record
            self.anchors[cid] = when
            tile.player.speed = self.speed.currentData()

            self.begin_record(tile, record, when, seek_generation, previous_bridge)
        self.timeline.cursor = when
        self.timeline.update()
        self.time_label.setText(when.astimezone(self.device.time.tzinfo).strftime("%H:%M:%S"))
        self.notice.setText(f"定位到 {when.astimezone(self.device.time.tzinfo):%Y-%m-%d %H:%M:%S} · "
                            "跳转精度受录像关键帧影响")
        QTimer.singleShot(30000, lambda: self.release_barrier() if seek_generation == self.seek_generation else None)

    def begin_record(self, tile, record, when, generation, previous_bridge):
        if generation != self.seek_generation or tile.disposed or not self.client:
            return
        if previous_bridge and previous_bridge.process and previous_bridge.process.poll() is None:
            QTimer.singleShot(100, lambda: self.begin_record(tile, record, when, generation, previous_bridge))
            return
        tile.player.start(self.client.playback_url(record, when, record.end), live=False,
                          profile="smooth", hardware=self.settings.get("hardware", True),
                          transport=self.settings.get("transport", "tcp"))

    def seek_relative(self, seconds):
        records = self.records.get(self.channel.currentData(), [])
        if records:
            when = self.clock or max(records[0].start, self.day_bounds()[0])
            self.seek(when+timedelta(seconds=seconds), direction=1 if seconds > 0 else -1)

    def recover(self, cid, reason):
        if self.face_scanning:
            rate_index = self.speed.currentIndex()
            if rate_index > self.speed.findData(1):
                self.face_generation += 1
                self.face_busy = False
                self.face_scanning = False
                self.speed.setCurrentIndex(rate_index-1)
                self.face_scanning = True
                self.notice.setText(f"{reason} · 人脸定位降至 {self.speed.currentData():g}× 继续")
                return
            self.cancel_face_scan(restore=False)
            self.notice.setText(f"人脸定位已停止：{reason}。可降低倍速后重试。")
        if not self.isVisible() or reason.startswith(("设备拒绝此倍速", "设备仅接受", "设备拒绝视频认证")):
            return
        count = self.recovery_counts.get(cid, 0)+1
        self.recovery_counts[cid] = count
        if count > 5:
            self.tiles[cid].set_state(f"{reason} · 多次恢复失败，减少联动通道后重试")
            return
        generation = self.seek_generation
        delay = min(30, 2**count)
        self.tiles[cid].set_state(f"{reason} · {delay} 秒后从上次位置恢复")

        def resume():
            if generation != self.seek_generation or not self.isVisible() or not self.client:
                return
            record = self.current.get(cid)
            when = self.clocks.get(cid, self.anchors.get(cid, self.target))
            if not record or when is None or when >= record.end:
                return
            self.anchors[cid] = when
            tile = self.tiles[cid]
            tile.player.speed = self.speed.currentData()
            tile.player.start(self.client.playback_url(record, when, record.end), live=False,
                              profile="smooth", hardware=self.settings.get("hardware", True) and count < 2,
                              transport=self.settings.get("transport", "tcp"))
        QTimer.singleShot(delay*1000, resume)

    def ready(self, cid):
        if cid in self.sync_pending:
            self.sync_pending.remove(cid)
            self.sync_hold.add(cid)
            self.tiles[cid].player.pause(True)
            if not self.sync_pending:
                self.release_barrier()
        elif self.paused:
            self.tiles[cid].player.pause(True)

    def release_barrier(self):
        if not self.sync_hold and not self.sync_pending:
            return
        pending = bool(self.sync_pending)
        for cid in self.sync_hold:
            if cid in self.tiles:
                self.tiles[cid].player.pause(self.paused)
        self.sync_hold.clear()
        self.sync_pending.clear()
        self.sync_label.setText("部分通道未就绪，已继续可用画面；点击重试可重新共同定位" if pending else
                                "各路已共同开始；时间差受关键帧和网络影响")

    def metrics(self, cid, data):
        if not self.target or self.paused:
            return
        if data["frames"] <= 0 or data["position_ms"] < 0:
            return
        position = data["position_ms"]
        self.clocks[cid] = self.anchors.get(cid, self.target) + timedelta(milliseconds=max(0, position))
        if len(self.clocks) > 1 and not self.sync_pending:
            drift = (max(self.clocks.values())-min(self.clocks.values())).total_seconds()
            self.sync_label.setText(f"各路播放时钟差约 {drift:.1f} 秒 · 点击重试重新共同定位")
        if cid != self.channel.currentData():
            return
        # VLC reports the media-relative playback clock. It is not the wall clock or a latency estimate.
        self.clock = self.clocks[cid]
        self.timeline.cursor = self.clock
        self.timeline.update()
        self.time_label.setText(self.clock.astimezone(self.device.time.tzinfo).strftime("%H:%M:%S"))
        record = self.current.get(cid)
        if record and self.clock >= record.end:
            self.on_ended(cid)

    def on_ended(self, cid):
        if cid != self.channel.currentData():
            return
        current = self.current.get(cid)
        if not current:
            return
        following = next((r for r in self.records.get(cid, []) if r.start >= current.end), None)
        if following:
            gap = (following.start-current.end).total_seconds()
            scanning = self.face_scanning
            # Continue sampling across media segments without cancelling the scan.
            self.face_scanning = False
            self.seek(following.start)
            self.face_scanning = scanning
            if gap > 1:
                self.notice.setText(f"已跳过 {gap:.0f} 秒录像空缺，继续下一段")
        else:
            self.cancel_face_scan()
            self.notice.setText("当天录像已播放结束")
            self.stop_all()

    def adjacent(self, direction):
        records = self.records.get(self.channel.currentData(), [])
        if not records:
            return
        current = self.current.get(self.channel.currentData())
        index = records.index(current) if current in records else (-1 if direction > 0 else len(records))
        index = max(0, min(len(records)-1, index+direction))
        self.seek(max(records[index].start, self.day_bounds()[0]))

    def seek_clock(self):
        if self.device:
            start, _ = self.day_bounds()
            t = self.time.time()
            self.seek(start.replace(hour=t.hour(), minute=t.minute(), second=t.second()))

    def reset_timeline(self):
        if self.device:
            self.timeline.set_day(*self.day_bounds(), self.records.get(self.channel.currentData(), []))

    def toggle_pause(self):
        self.paused = not self.paused
        self.pause_button.setText("继续" if self.paused else "暂停")
        for tile in self.tiles.values():
            tile.player.pause(self.paused)

    def change_speed(self):
        if self.face_scanning:
            self.cancel_face_scan(restore=False)
        if self.clock and self.current:
            self.seek(self.clock)

    def toggle_face_scan(self):
        if self.face_scanning:
            self.cancel_face_scan()
            self.notice.setText("已停止人脸定位")
            return
        if self.linked:
            self.notice.setText("人脸定位使用单路回放，请先取消联动通道")
            return
        records = self.records.get(self.channel.currentData(), [])
        if not records:
            self.notice.setText("请先检索录像")
            return
        self.face_original_speed = self.speed.currentData()
        self.face_original_pause = self.paused
        if not self.clock:
            self.seek(max(records[0].start, self.day_bounds()[0]))
        self.speed.setCurrentIndex(self.speed.findData(16) if self.speed.currentData() == 1 else self.speed.currentIndex())
        if self.paused:
            self.toggle_pause()
        self.face_generation += 1
        self.face_scanning = True
        self.face_button.setText("停止找人脸")
        self.face_timer.start()
        self.notice.setText("正在本机抽样检测人脸，命中后暂停；可能漏检或误检")

    def cancel_face_scan(self, restore=True):
        was_scanning = self.face_scanning
        self.face_scanning = False
        self.face_generation += 1
        self.face_timer.stop()
        self.face_busy = False
        self.face_button.setText("找人脸")
        if was_scanning and restore:
            self.speed.setCurrentIndex(self.speed.findData(self.face_original_speed))
            if self.face_original_pause != self.paused:
                self.toggle_pause()

    def scan_face_frame(self):
        tile = self.tiles.get(self.channel.currentData())
        if not self.face_scanning or self.face_busy or not tile or not tile.player.has_played or not self.clock:
            return
        directory = self.settings.directory / "face-sampling"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{uuid.uuid4().hex}.png"
        if not tile.player.screenshot(path):
            return
        self.face_busy = True
        generation, when = self.face_generation, self.clock

        def success(found):
            if generation != self.face_generation or not self.face_scanning:
                return
            self.face_busy = False
            if found:
                self.cancel_face_scan()
                self.seek(when)
                if not self.paused:
                    self.toggle_pause()
                self.face_marks.append(when)
                self.timeline.markers = self.face_marks[:]
                self.timeline.update()
                self.notice.setText(f"{when.astimezone(self.device.time.tzinfo):%H:%M:%S} 检测到疑似人脸，已暂停")

        def failure(message):
            if generation == self.face_generation:
                self.cancel_face_scan()
                self.notice.setText(f"人脸定位失败：{message}")

        from .faces import detect_face
        self.tasks.submit(lambda: detect_face(path), success, failure)

    def mute(self, cid, value):
        for key, tile in self.tiles.items():
            tile.set_muted(value if key == cid else True)

    def export(self):
        records = self.records.get(self.channel.currentData(), [])
        if records:
            self.export_requested.emit(records, self.clock or max(records[0].start, self.day_bounds()[0]),
                                       self.day_bounds())
        else:
            self.notice.setText("请先检索要导出的录像")

    def stop_all(self):
        self.seek_generation += 1
        self.sync_pending.clear()
        self.sync_hold.clear()
        for tile in self.tiles.values():
            tile.stop()

    def hideEvent(self, event):
        self.hide_timer.start(200)
        super().hideEvent(event)

    def stop_if_hidden(self):
        if not self.isVisible():
            self.cancel_face_scan(restore=False)
            self.stop_all()
