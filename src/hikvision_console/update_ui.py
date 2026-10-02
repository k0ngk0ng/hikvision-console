from __future__ import annotations

import shutil
import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QMessageBox, QProgressBar, QVBoxLayout

from . import __version__
from .updates import check_release, download_and_stage, installed_bundle, launch_installer
from .widgets import button, label


class UpdateController(QObject):
    changed = Signal()
    progress = Signal(int, int)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.settings = window.settings
        self.release = None
        self.stage = None
        self.target = None
        self.busy = False
        self.installing = False
        self.status = "从 GitHub Releases 获取最新稳定版本"
        self.cancel = threading.Event()
        self.received = self.total = 0
        self.progress.connect(self.receive_progress)
        self.periodic = QTimer(self)
        self.periodic.setInterval(6 * 60 * 60 * 1000)
        self.periodic.timeout.connect(self.automatic_check)
        self.dialog = UpdateDialog(self, window)

    def start(self):
        QTimer.singleShot(3000, self.automatic_check)
        self.periodic.start()

    def automatic_check(self):
        if self.settings.get("auto_check_updates", True) and not self.window.closed:
            self.check()

    def show(self):
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        if not self.busy and self.release is None:
            self.check()

    def check(self):
        if self.busy or self.stage:
            return
        self.busy = True
        self.status = "正在检查最新版本…"
        self.changed.emit()

        def done(release):
            self.busy = False
            self.release = release
            self.status = f"发现新版本 v{release.version}" if release else f"当前 v{__version__} 已是最新版本"
            self.window.update_button.setText(f"更新至 v{release.version}" if release else "软件更新")
            self.changed.emit()
        self.window.tasks.submit(lambda: check_release(__version__), done, self.failed)

    def failed(self, error):
        if self.installing:
            self.window.setEnabled(True)
            self.installing = False
        self.busy = False
        self.status = f"更新未完成：{error}。可以重试。"
        self.changed.emit()

    def receive_progress(self, received, total):
        self.received, self.total = received, total
        self.status = f"正在下载 v{self.release.version} · {received / 1048576:.1f} / {total / 1048576:.1f} MB"
        if received == total:
            self.status = "下载完成，正在校验并准备安装…"
        self.changed.emit()

    def download(self):
        if self.busy or not self.release:
            return
        try:
            self.target = installed_bundle()
        except ValueError as exc:
            self.failed(str(exc))
            return
        self.busy = True
        self.cancel.clear()
        self.received = self.total = 0
        self.status = "正在连接下载服务器…"
        self.changed.emit()
        directory = self.settings.directory / "updates" / self.release.version

        def done(stage):
            self.busy = False
            if self.cancel.is_set():
                shutil.rmtree(stage, ignore_errors=True)
                self.status = "已取消更新"
            else:
                self.stage = stage
                self.status = "校验通过，可以安装并重启。连接设置会保留，密码需重新输入。"
            self.changed.emit()
        self.window.tasks.submit(
            lambda: download_and_stage(self.release, directory, self.target, self.cancel, self.progress.emit),
            done, self.failed)

    def install(self):
        if self.busy or not self.stage:
            return
        if self.window.exports.process:
            QMessageBox.information(self.dialog, "正在导出", "请等待或取消当前录像导出后再安装更新。")
            return
        self.busy = True
        self.status = "正在准备重启…"
        self.changed.emit()
        self.installing = True
        self.window.setEnabled(False)

        def prepare():
            # Unusual portable setups may store their data within the app folder.
            # Preserve it in the replacement rather than leaving it only in backup.
            data = self.settings.directory.resolve()
            if data.is_relative_to(self.target):
                relative = data.relative_to(self.target)
                if not relative.parts:
                    raise ValueError("数据目录不能与应用根目录相同，请先迁移数据目录")
                shutil.copytree(data, self.stage / relative, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("updates"))
            launch_installer(self.target, self.stage)

        def ready(_):
            self.dialog.close()
            self.window.close()
        self.window.tasks.submit(prepare, ready, self.failed)


class UpdateDialog(QDialog):
    def __init__(self, controller, parent):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("软件更新")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        layout.addWidget(label(f"Hikvision Console · v{__version__}", "PageTitle"))
        self.status = label(controller.status)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.hide()
        layout.addWidget(self.bar)
        auto = QCheckBox("启动时自动检查新版本（之后每 6 小时检查一次）")
        auto.setChecked(controller.settings.get("auto_check_updates", True))
        auto.toggled.connect(lambda value: controller.settings.update(auto_check_updates=value))
        layout.addWidget(auto)
        note = label("下载经过 SHA-256 校验，安装时退出并重启应用。\n"
                     "旧版本保留在安装目录旁的 .previous 文件夹中。", "Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.check = button("检查更新", controller.check)
        self.download = button("下载更新", controller.download, True)
        self.install = button("安装并重启", controller.install, True)
        self.cancel = button("取消下载", controller.cancel.set)
        for item in (self.check, self.download, self.install, self.cancel):
            row.addWidget(item)
        layout.addLayout(row)
        controller.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        c = self.controller
        self.status.setText(c.status)
        self.check.setEnabled(not c.busy and not c.stage)
        self.download.setVisible(c.stage is None)
        self.download.setEnabled(c.release is not None and not c.busy)
        self.install.setVisible(c.stage is not None)
        self.install.setEnabled(not c.busy)
        self.cancel.setVisible(c.busy and c.release is not None and c.stage is None)
        self.bar.setVisible(c.busy or c.stage is not None)
        self.bar.setRange(0, 100 if c.total or not c.busy else 0)
        if c.total:
            self.bar.setValue(int(c.received * 100 / c.total))
