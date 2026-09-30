from __future__ import annotations

import json
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from .api import NvrClient
from .widgets import button, label


class EventSignals(QObject):
    event = Signal(int, object)
    status = Signal(int, str)


class EventsPage(QWidget):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.connection = None
        self.cancel = threading.Event()
        self.generation = 0
        self.events = []
        self.last_states = {}
        self.signals = EventSignals(self)
        self.signals.event.connect(self.receive)
        self.signals.status.connect(self.status)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        top = QHBoxLayout()
        top.addWidget(label("设备事件", "PageTitle"))
        top.addStretch()
        top.addWidget(button("导出事件列表", self.export_events))
        self.toggle = button("开始订阅", self.toggle_subscription, True)
        self.toggle.setCheckable(True)
        top.addWidget(self.toggle)
        layout.addLayout(top)
        self.notice = label("从 NVR 订阅实时事件。事件能力以设备实际返回为准；不会发送外部通知。", "Muted")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["时间", "通道", "事件", "状态", "说明"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

    def set_connection(self, connection):
        self.stop()
        self.connection = connection

    def toggle_subscription(self):
        if not self.toggle.isChecked():
            self.stop()
            return
        if not self.connection:
            self.toggle.setChecked(False)
            self.notice.setText("请先连接 NVR")
            return
        self.cancel = threading.Event()
        self.last_states.clear()
        self.generation += 1
        generation, cancel, connection = self.generation, self.cancel, self.connection
        self.toggle.setText("停止订阅")

        def run():
            delay = 2
            client = NvrClient(connection)
            try:
                while not cancel.is_set():
                    self.signals.status.emit(generation, "正在订阅设备事件…")
                    try:
                        for event in client.iter_events(cancel):
                            self.signals.event.emit(generation, event)
                            delay = 2
                    except Exception:
                        if cancel.is_set():
                            break
                        self.signals.status.emit(generation, f"订阅连接中断，{delay} 秒后重试；可随时停止")
                    cancel.wait(delay)
                    delay = min(30, delay * 2)
            finally:
                client.close()

        threading.Thread(target=run, daemon=True, name="nvr-events").start()

    def receive(self, generation, event):
        if generation != self.generation:
            return
        key = (event["channelID"], event["eventType"])
        if self.last_states.get(key) == event["eventState"]:
            return
        self.last_states[key] = event["eventState"]
        self.events.insert(0, event)
        self.events = self.events[:1000]
        self.table.insertRow(0)
        self.table.setRowCount(min(self.table.rowCount(), 1000))
        names = {"VMD": "移动侦测", "videoloss": "视频丢失", "linedetection": "越界侦测",
                 "fielddetection": "区域入侵", "diskfull": "磁盘已满", "diskerror": "磁盘异常"}
        values = [event["dateTime"], event["channelID"], names.get(event["eventType"], event["eventType"]),
                  event["eventState"], event["eventDescription"]]
        for col, text in enumerate(values):
            self.table.setItem(0, col, QTableWidgetItem(text))
        self.notice.setText(f"订阅中 · 当前会话保留最近 {len(self.events)} 条事件")

    def status(self, generation, text):
        if generation == self.generation:
            self.notice.setText(text)

    def export_events(self):
        path = self.settings.output_dir("events") / f"events_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
        path.write_text(json.dumps(self.events, ensure_ascii=False, indent=2), encoding="utf-8")
        self.notice.setText(f"事件列表已保存：{path}")

    def stop(self):
        self.generation += 1
        self.cancel.set()
        self.toggle.setChecked(False)
        self.toggle.setText("开始订阅")
        self.notice.setText("事件订阅已停止")
