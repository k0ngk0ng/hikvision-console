from __future__ import annotations

import math

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from .models import PROFILES, plan_streams
from .widgets import VideoTile, button, label


class LivePage(QWidget):
    summary = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.channels = []
        self.connection = None
        self.tiles = {}
        self.requested = {}
        self.active = {}
        self.enabled = False
        self.focused = None
        self.page = 0
        self.last_metrics = {}
        self.immersive = False
        self.rotation = QTimer(self)
        self.rotation.timeout.connect(self.next_page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        self.toolbar = QWidget()
        top = QHBoxLayout(self.toolbar)
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(label("实时监控", "PageTitle"))
        top.addStretch()
        self.rotate = button("轮巡", self.toggle_rotation)
        self.rotate.setCheckable(True)
        self.rotate.setToolTip("每 30 秒切换下一页；放大画面时暂停轮巡")
        top.addWidget(self.rotate)
        self.profile = QComboBox()
        for key, value in PROFILES.items():
            self.profile.addItem(value.name, key)
        self.profile.setCurrentIndex(max(0, self.profile.findData(settings.get("profile", "balanced"))))
        self.profile.currentIndexChanged.connect(self.profile_changed)
        top.addWidget(self.profile)
        self.layout_combo = QComboBox()
        for size in (1, 4, 9, 16):
            self.layout_combo.addItem(f"{size} 画面", size)
        self.layout_combo.setCurrentIndex(max(0, self.layout_combo.findData(settings.get("grid_size", 9))))
        self.layout_combo.currentIndexChanged.connect(self.layout_changed)
        top.addWidget(self.layout_combo)
        self.toggle = button("开始预览", self.toggle_preview, True)
        top.addWidget(self.toggle)
        layout.addWidget(self.toolbar)
        self.info = label("连接录像机后选择通道。预览默认使用子码流，放大时切换高清。", "Muted")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.grid_container = QWidget()
        self.grid = QGridLayout(self.grid_container)
        self.grid.setContentsMargins(0, 10, 0, 5)
        self.grid.setSpacing(10)
        layout.addWidget(self.grid_container, 1)
        self.footer = QWidget()
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        self.previous = button("← 上一页", self.previous_page)
        self.next = button("下一页 →", self.next_page)
        self.page_label = label("—", "Muted")
        footer.addWidget(self.previous)
        footer.addWidget(self.page_label)
        footer.addWidget(self.next)
        footer.addStretch()
        self.return_grid = button("返回网格", lambda: self.focus(None))
        self.return_grid.hide()
        footer.addWidget(self.return_grid)
        self.traffic = label("媒体接收 — Mbps", "Accent")
        footer.addWidget(self.traffic)
        layout.addWidget(self.footer)

    def set_immersive(self, enabled):
        self.immersive = enabled
        for widget in (self.toolbar, self.info, self.footer):
            widget.setVisible(not enabled)
        self.layout().setContentsMargins(*(0, 0, 0, 0) if enabled else (24, 20, 24, 16))
        self.layout().setSpacing(0 if enabled else -1)
        self.grid.setContentsMargins(*(0, 0, 0, 0) if enabled else (0, 10, 0, 5))
        self.grid.setSpacing(0 if enabled else 10)
        for tile in self.tiles.values():
            tile.set_immersive(enabled)

    def set_device(self, connection, device):
        self.stop_all()
        self.connection = connection
        self.channels = list(device.channels)
        order = self.settings.get("channel_order", {}).get(connection.host, [])
        self.channels.sort(key=lambda c: order.index(c.id) if c.id in order else len(order)+c.id)
        saved = self.settings.get("channel_selection", {}).get(connection.host)
        selected = set(saved) if saved is not None else {c.id for c in self.channels if c.online}
        self.requested = {c.id: "sub" for c in self.channels if c.id in selected}
        self.page = 0
        self.focused = None
        self.rebuild()

    def set_selected(self, selected):
        self.requested = {i: self.requested.get(i, "sub") for i in selected}
        if self.connection:
            saved = self.settings.get("channel_selection", {})
            saved[self.connection.host] = list(selected)
            self.settings.update(channel_selection=saved)
        self.reconcile()

    def toggle_preview(self):
        self.enabled = not self.enabled
        self.toggle.setText("停止预览" if self.enabled else "开始预览")
        self.reconcile()

    def profile_changed(self):
        self.settings.update(profile=self.profile.currentData())
        self.stop_all()
        self.reconcile()

    def layout_changed(self):
        self.settings.update(grid_size=self.layout_combo.currentData())
        self.page = 0
        self.rebuild()

    def visible_channels(self):
        if self.focused is not None:
            return [c for c in self.channels if c.id == self.focused]
        size = self.layout_combo.currentData()
        return self.channels[self.page*size:(self.page+1)*size]

    def rebuild(self):
        self.stop_all()
        for tile in self.tiles.values():
            self.grid.removeWidget(tile)
            tile.dispose()
        self.tiles.clear()
        count = 1 if self.focused is not None else self.layout_combo.currentData()
        columns = math.ceil(math.sqrt(count))
        for i in range(4):
            self.grid.setColumnStretch(i, 1 if i < columns else 0)
            self.grid.setRowStretch(i, 1 if i < columns else 0)
        for index, channel in enumerate(self.visible_channels()):
            tile = VideoTile(channel, self.settings)
            tile.set_immersive(self.immersive)
            tile.quality_changed.connect(self.quality_changed)
            tile.focus_requested.connect(self.focus)
            tile.mute_requested.connect(self.mute)
            tile.metrics_changed.connect(self.metrics)
            tile.swapped.connect(self.swap_channels)
            tile.quality.blockSignals(True)
            tile.quality.setCurrentIndex(tile.quality.findData(
                "main" if channel.id == self.focused else self.requested.get(channel.id, "sub")))
            tile.quality.blockSignals(False)
            self.tiles[channel.id] = tile
            self.grid.addWidget(tile, index // columns, index % columns)
        pages = max(1, math.ceil(len(self.channels) / self.layout_combo.currentData()))
        self.page_label.setText(f"{self.page+1} / {pages} 页 · 共 {len(self.channels)} 通道")
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page < pages-1)
        self.return_grid.setVisible(self.focused is not None)
        self.reconcile()

    def quality_changed(self, channel_id, quality):
        self.requested[channel_id] = quality
        self.reconcile()

    def focus(self, channel_id):
        self.focused = None if self.focused == channel_id else channel_id
        if self.focused is not None:
            self.requested[self.focused] = "main"
        else:
            self.requested = {k: "sub" for k in self.requested}
        self.rebuild()

    def reconcile(self):
        if not self.connection or not self.enabled or not self.isVisible():
            self.stop_all()
            return
        profile = PROFILES[self.profile.currentData()]
        budget = self.settings.get("budget_kbps", 0) or profile.budget_kbps
        limit = self.settings.get("max_live", 0) or profile.max_live
        chosen, deferred = plan_streams(self.visible_channels(), self.requested, budget, limit, self.focused)
        for cid in list(self.active):
            if cid not in chosen or chosen[cid] != self.active[cid]:
                self.tiles[cid].stop()
                self.active.pop(cid)
        delay = 0
        for c in self.visible_channels():
            tile = self.tiles[c.id]
            if c.id in chosen and c.id not in self.active:
                quality = chosen[c.id]
                tile.quality.blockSignals(True)
                tile.quality.setCurrentIndex(max(0, tile.quality.findData(quality)))
                tile.quality.blockSignals(False)
                self.active[c.id] = quality
                tile.state.setText("排队连接…")
                # Stagger connections so a reconnect does not flood the cellular/ISP uplink.
                def start(cid=c.id, q=quality, target=tile):
                    if (self.enabled and self.isVisible() and self.tiles.get(cid) is target
                            and self.active.get(cid) == q and not target.player.want_play):
                        stream = target.channel.stream(q)
                        target.player.start(self.connection.rtsp_url(stream.id), profile=self.profile.currentData(),
                                            hardware=self.settings.get("hardware", True),
                                            transport=self.settings.get("transport", "tcp"))
                QTimer.singleShot(delay, start)
                delay += 450
            elif c.id in deferred:
                tile.state.setText("带宽预算不足 · 已暂停此通道")
            elif not c.online:
                tile.state.setText(c.status)
            elif c.id not in self.requested:
                tile.state.setText("未选择预览")
        estimate = sum(next(c for c in self.channels if c.id == i).stream(q).bitrate_kbps
                       for i, q in chosen.items())
        self.info.setText(f"正在预览 {len(chosen)} 路 · 配置码率约 {estimate/1000:.1f} Mbps · "
                          f"预算 {budget/1000:.1f} Mbps" +
                          (f" · {len(deferred)} 路等待带宽" if deferred else ""))

    def metrics(self, channel_id, data):
        self.last_metrics[channel_id] = data
        total = sum(self.last_metrics.get(i, {}).get("kbps", 0) for i in self.active)
        self.traffic.setText(f"媒体接收 {total/1000:.2f} Mbps")

    def swap_channels(self, source, destination):
        if source == destination or source not in self.tiles or destination not in self.tiles:
            return
        ids = [c.id for c in self.channels]
        a, b = ids.index(source), ids.index(destination)
        self.channels[a], self.channels[b] = self.channels[b], self.channels[a]
        orders = self.settings.get("channel_order", {})
        orders[self.connection.host] = [c.id for c in self.channels]
        self.settings.update(channel_order=orders)
        columns = math.ceil(math.sqrt(self.layout_combo.currentData()))
        for tile in self.tiles.values():
            self.grid.removeWidget(tile)
        for index, channel in enumerate(self.visible_channels()):
            self.grid.addWidget(self.tiles[channel.id], index//columns, index%columns)

    def mute(self, channel_id, value):
        for cid, tile in self.tiles.items():
            tile.set_muted(value if cid == channel_id else True)

    def stop_all(self):
        for tile in self.tiles.values():
            tile.stop()
            if not tile.channel.online:
                tile.state.setText(tile.channel.status)
        self.active.clear()
        self.last_metrics.clear()

    def next_page(self):
        if self.focused is not None:
            return
        pages = max(1, math.ceil(len(self.channels)/self.layout_combo.currentData()))
        self.page = (self.page + 1) % pages
        self.rebuild()

    def previous_page(self):
        self.page = max(0, self.page-1)
        self.rebuild()

    def toggle_rotation(self):
        if self.rotate.isChecked():
            self.rotation.start(30000)
        else:
            self.rotation.stop()

    def hideEvent(self, event):
        self.stop_all()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.reconcile)
