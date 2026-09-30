from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QDateTime, QObject, QProcess, QTimer, QTimeZone, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .api import redact
from .media_source import rtsp_input
from .widgets import button, label


def ffmpeg_binary():
    bundled = Path(getattr(sys, "_MEIPASS", ".")) / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if bundled.is_file():
        return str(bundled.resolve())
    if getattr(sys, "frozen", False):
        directory = Path(sys._MEIPASS) / "imageio_ffmpeg/binaries"
        candidates = sorted(p for p in directory.glob("ffmpeg*") if p.is_file() and os.access(p, os.X_OK))
        if len(candidates) == 1:
            return str(candidates[0])
    supplied = os.environ.get("HIKVISION_FFMPEG")
    if supplied:
        return supplied
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


@dataclass
class ExportJob:
    channel: int
    start: datetime
    end: datetime
    url: str = field(repr=False)
    path: Path
    pace: float = 1.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    state: str = "等待"
    progress: int = 0
    error: str = ""
    cancelled: bool = False
    received_seconds: float = 0

    @property
    def duration(self):
        return (self.end-self.start).total_seconds()

    @property
    def temporary(self):
        return self.path.with_name(self.path.stem + f".{self.id}.partial.mp4")


class ExportManager(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs: list[ExportJob] = []
        self.process = None
        self.current = None
        self.paused = False
        self.buffer = ""
        self.error_buffer = ""
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(90000)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(self.timeout)

    def add(self, jobs):
        self.jobs.extend(jobs)
        self.changed.emit()
        self.next()

    def next(self):
        if self.process or self.paused:
            return
        job = next((j for j in self.jobs if j.state == "等待"), None)
        if not job:
            return
        self.current = job
        job.path.parent.mkdir(parents=True, exist_ok=True)
        if job.path.exists():
            job.state, job.error = "失败", "目标文件已存在，不会覆盖"
            self.changed.emit()
            QTimer.singleShot(0, self.next)
            return
        try:
            binary = ffmpeg_binary()
        except Exception:
            job.state, job.error = "失败", "未找到 FFmpeg 导出组件"
            self.changed.emit()
            return
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.read_progress)
        self.process.readyReadStandardError.connect(self.read_error)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.buffer = self.error_buffer = ""
        job.state = "导出中"
        input_args, payload = rtsp_input(job.url)
        process = self.process
        process.started.connect(lambda: (process.write(payload), process.closeWriteChannel()))
        args = ["-hide_banner", "-nostdin", "-loglevel", "error", "-readrate", str(job.pace)] + input_args + [
                "-t", str(job.duration), "-map", "0:v:0", "-map", "0:a?", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-progress", "pipe:1",
                "-n", "-f", "mp4", str(job.temporary)]
        self.process.start(binary, args)
        self.watchdog.start()
        self.changed.emit()

    def read_progress(self):
        if not self.process or not self.current:
            return
        self.buffer += bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.startswith("out_time_us="):
                try:
                    seconds = int(line.split("=", 1)[1]) / 1_000_000
                    if seconds > self.current.received_seconds:
                        self.current.received_seconds = seconds
                        self.current.progress = max(0, min(99, int(seconds/self.current.duration*100)))
                        self.watchdog.start()
                        self.changed.emit()
                except ValueError:
                    pass

    def read_error(self):
        if self.process:
            # Store only a bounded, credential-redacted diagnostic in memory.
            text = bytes(self.process.readAllStandardError()).decode(errors="replace")
            self.error_buffer = (self.error_buffer + redact(text))[-3000:]

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            if self.current:
                self.current.error = "FFmpeg 无法启动，请检查播放组件和可执行权限"
            self.finished(-1, QProcess.ExitStatus.CrashExit)

    def timeout(self):
        if self.current:
            self.current.error = "90 秒没有导出进度，连接已停止；可以重试"
        if self.process:
            self.process.kill()

    def finished(self, exit_code, exit_status):
        if not self.process:
            return
        self.watchdog.stop()
        self.read_error()
        job = self.current
        if job.cancelled:
            job.state = "已取消"
        elif (exit_code == 0 and job.temporary.exists() and job.temporary.stat().st_size > 0
              and job.received_seconds >= max(0.1, job.duration-1)):
            try:
                if job.path.exists():
                    raise FileExistsError()
                job.temporary.rename(job.path)
                job.progress, job.state = 100, "完成"
            except OSError:
                job.state, job.error = "失败", "无法保存导出文件（文件已存在或目录不可写）"
        else:
            job.state = "失败"
            job.error = job.error or ("NVR 当前资源不足（RTSP 453），请停止其他回放或稍后重试"
                                      if "453 Not Enough Bandwidth" in self.error_buffer else
                                      "视频读取或封装失败，请检查网络后重试")
        if job.temporary.exists():
            job.temporary.unlink(missing_ok=True)
        self.process.deleteLater()
        self.process = self.current = None
        self.changed.emit()
        QTimer.singleShot(1000, self.next)

    def cancel(self, job):
        job.cancelled = True
        if job is self.current and self.process:
            self.process.kill()
        elif job.state == "等待":
            job.state = "已取消"
            self.changed.emit()

    def retry(self, job):
        if job.state not in ("失败", "已取消"):
            return
        job.cancelled, job.error, job.progress, job.state = False, "", 0, "等待"
        job.received_seconds = 0
        self.changed.emit()
        self.next()

    def shutdown(self):
        self.paused = True
        if self.process:
            self.current.cancelled = True
            self.process.kill()
            self.process.waitForFinished(3000)


class ExportDialog(QDialog):
    def __init__(self, start, bounds, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出录像片段")
        self.setMinimumWidth(450)
        layout = QVBoxLayout(self)
        layout.addWidget(label("导出到本机", "PageTitle"))
        note = label("保留原视频编码，音频转换为 AAC。跨录像文件时分段保存；\n"
                     "起点以源录像关键帧为准。导出会额外占用一路主码流带宽。", "Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.start = QDateTimeEdit()
        self.end = QDateTimeEdit()
        tz = QTimeZone(int(start.utcoffset().total_seconds()))
        for widget in (self.start, self.end):
            widget.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            widget.setCalendarPopup(True)
            widget.setTimeZone(tz)
            widget.setMinimumDateTime(QDateTime.fromSecsSinceEpoch(int(bounds[0].timestamp()), tz))
            widget.setMaximumDateTime(QDateTime.fromSecsSinceEpoch(int(bounds[1].timestamp()), tz))
        self.start.setDateTime(QDateTime.fromSecsSinceEpoch(int(start.timestamp()), tz))
        self.end.setDateTime(QDateTime.fromSecsSinceEpoch(min(int(start.timestamp())+300, int(bounds[1].timestamp())), tz))
        self.pace = QComboBox()
        self.pace.addItem("正常节奏（1×）", 1.0)
        self.pace.addItem("降低读取节奏（0.5×）", 0.5)
        form.addRow("开始时间（设备时区）", self.start)
        form.addRow("结束时间", self.end)
        form.addRow("读取节奏", self.pace)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("加入导出队列")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def validate(self):
        if self.end.dateTime() <= self.start.dateTime():
            QMessageBox.warning(self, "时间范围", "结束时间必须晚于开始时间")
        else:
            self.accept()

    def range(self):
        return (datetime.fromtimestamp(self.start.dateTime().toSecsSinceEpoch(), timezone.utc),
                datetime.fromtimestamp(self.end.dateTime().toSecsSinceEpoch(), timezone.utc))


class ExportsPage(QWidget):
    def __init__(self, manager, settings, parent=None):
        super().__init__(parent)
        self.manager, self.settings = manager, settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        top = QHBoxLayout()
        top.addWidget(label("导出任务", "PageTitle"))
        top.addStretch()
        self.pause = button("暂停队列", self.toggle_pause)
        self.pause.setCheckable(True)
        top.addWidget(self.pause)
        top.addWidget(button("打开导出目录", self.open_folder))
        layout.addLayout(top)
        layout.addWidget(label("每次只导出一个片段，避免多任务挤占监控链路。关闭应用会取消未完成的任务。", "Muted"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["通道", "录像时间", "状态", "进度", "文件"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(button("取消选中任务", lambda: self.action("cancel")))
        row.addWidget(button("重试选中任务", lambda: self.action("retry")))
        row.addStretch()
        self.detail = label("暂无导出任务", "Muted")
        row.addWidget(self.detail)
        layout.addLayout(row)
        manager.changed.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self.show_detail)

    def refresh(self):
        self.table.setRowCount(len(self.manager.jobs))
        for row, j in enumerate(self.manager.jobs):
            for col, text in enumerate((str(j.channel), f"{j.start:%m-%d %H:%M:%S} UTC", j.state,
                                        f"{j.progress}%", j.path.name)):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.show_detail()

    def show_detail(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.manager.jobs):
            j = self.manager.jobs[row]
            self.detail.setText(j.error or str(j.path))

    def action(self, name):
        row = self.table.currentRow()
        if 0 <= row < len(self.manager.jobs):
            getattr(self.manager, name)(self.manager.jobs[row])

    def toggle_pause(self):
        self.manager.paused = self.pause.isChecked()
        self.pause.setText("继续队列" if self.manager.paused else "暂停队列")
        self.manager.next()

    def open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.settings.output_dir("clips"))))
