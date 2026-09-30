from __future__ import annotations

import threading
from datetime import datetime, timedelta

from PySide6.QtCore import QDate, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
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
        layout.addLayout(head)
        self.notice = label("选择通道和日期，查看 NVR 中已有的录像。", "Muted")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.sync_label = label("", "Muted")
        self.sync_label.hide()
        layout.addWidget(self.sync_label)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.video_area = QWidget()
        self.grid = QGridLayout(self.video_area)
        self.grid.setContentsMargins(0, 0, 8, 0)
        splitter.addWidget(self.video_area)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["开始", "结束", "类型"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self.play_row)
        self.table.setMinimumWidth(245)
        splitter.addWidget(self.table)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.timeline = Timeline()
        self.timeline.seek_requested.connect(self.seek)
        layout.addWidget(self.timeline)
        controls = QHBoxLayout()
        self.pause_button = button("暂停", self.toggle_pause)
        controls.addWidget(self.pause_button)
        controls.addWidget(button("上一段", lambda: self.adjacent(-1)))
        controls.addWidget(button("下一段", lambda: self.adjacent(1)))
        controls.addWidget(button("重试", lambda: self.seek(self.clock) if self.clock else None))
        self.speed = QComboBox()
        for value in (0.5, 1, 2, 4, 8):
            self.speed.addItem(f"{value:g}×", value)
        self.speed.setCurrentIndex(1)
        self.speed.currentIndexChanged.connect(self.change_speed)
        controls.addWidget(self.speed)
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm:ss")
        controls.addWidget(self.time)
        controls.addWidget(button("定位", self.seek_clock))
        controls.addWidget(button("全天", self.reset_timeline))
        controls.addStretch()
        self.time_label = label("—", "Accent")
        controls.addWidget(self.time_label)
        controls.addWidget(button("导出片段", self.export))
        layout.addLayout(controls)

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
        self.table.setRowCount(0)
        self.timeline.set_day(None, None, [])
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
            tile.state.setText("等待录像检索")
            tile.metrics_changed.connect(self.metrics)
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
            self.table.setRowCount(len(primary))
            for row, record in enumerate(primary):
                begin = max(start, record.start).astimezone(start.tzinfo)
                finish = min(end, record.end).astimezone(start.tzinfo)
                for col, text in enumerate((begin.strftime("%H:%M:%S"),
                                             "24:00:00" if finish == end else finish.strftime("%H:%M:%S"), record.kind)):
                    item = QTableWidgetItem(text)
                    item.setToolTip(f"{begin:%Y-%m-%d %H:%M:%S} — {finish:%Y-%m-%d %H:%M:%S}")
                    self.table.setItem(row, col, item)
            self.timeline.set_day(start, end, primary)
            self.notice.setText(f"找到 {len(primary)} 段录像 · 设备时区 {start:%z} · "
                                "绿色表示有录像，滚轮缩放时间轴，点击定位；双击列表播放")
            if not primary:
                self.notice.setText("所选通道当天没有录像。离线通道也可以查询已有录像。")

        def failure(message):
            if generation == self.generation:
                self.search_button.setEnabled(True)
                self.notice.setText(message)
        self.tasks.submit(work, success, failure)

    def play_row(self, row, _column):
        records = self.records.get(self.channel.currentData(), [])
        if row < len(records):
            self.seek(max(records[row].start, self.day_bounds()[0]))

    def seek(self, when):
        if not self.client or not self.records:
            return
        primary = self.records.get(self.channel.currentData(), [])
        if not any(r.contains(when) for r in primary):
            self.notice.setText(f"{when.astimezone(self.device.time.tzinfo):%H:%M:%S} 没有录像，请选择绿色时间段")
            return
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
        self.paused = False
        self.pause_button.setText("暂停")
        for cid, tile in self.tiles.items():
            record = matching[cid]
            tile.stop()
            if not record:
                tile.state.setText("此时段没有录像")
                self.current.pop(cid, None)
                continue
            self.current[cid] = record
            self.anchors[cid] = when
            tile.player.speed = self.speed.currentData()
            tile.player.start(self.client.playback_url(record, when, record.end), live=False,
                              profile="smooth", hardware=self.settings.get("hardware", True),
                              transport=self.settings.get("transport", "tcp"))
        self.timeline.cursor = when
        self.timeline.update()
        self.notice.setText(f"定位到 {when.astimezone(self.device.time.tzinfo):%Y-%m-%d %H:%M:%S} · "
                            "跳转精度受录像关键帧影响")
        QTimer.singleShot(30000, lambda: self.release_barrier() if seek_generation == self.seek_generation else None)

    def recover(self, cid, reason):
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
            self.seek(following.start)
            if gap > 1:
                self.notice.setText(f"已跳过 {gap:.0f} 秒录像空缺，继续下一段")
        else:
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
        if self.clock and self.current:
            paused = self.paused
            self.seek(self.clock)
            self.paused = paused
            self.pause_button.setText("继续" if paused else "暂停")

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
        self.stop_all()
        super().hideEvent(event)
