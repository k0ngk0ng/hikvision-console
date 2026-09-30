from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from .widgets import button, label


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("客户端设置")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.addWidget(label("播放与网络", "PageTitle"))
        form = QFormLayout()
        self.hardware = QCheckBox("优先硬件解码，连续失败后回退软件解码")
        self.hardware.setChecked(settings.get("hardware", True))
        form.addRow("解码", self.hardware)
        self.transport = QComboBox()
        self.transport.addItem("TCP · 优先完整传输", "tcp")
        self.transport.addItem("UDP · 对照测试时使用", "udp")
        self.transport.setCurrentIndex(max(0, self.transport.findData(settings.get("transport", "tcp"))))
        form.addRow("RTSP 传输", self.transport)
        self.budget = QSpinBox()
        self.budget.setRange(0, 1000000)
        self.budget.setSingleStep(512)
        self.budget.setSpecialValueText("使用网络模式默认值")
        self.budget.setSuffix(" kbps")
        self.budget.setValue(settings.get("budget_kbps", 0))
        form.addRow("预览带宽预算", self.budget)
        self.max_live = QSpinBox()
        self.max_live.setRange(0, 4096)
        self.max_live.setSpecialValueText("使用网络模式默认值")
        self.max_live.setValue(settings.get("max_live", 0))
        form.addRow("同时预览上限", self.max_live)
        self.time_mode = QComboBox()
        self.time_mode.addItem("自动识别", "auto")
        self.time_mode.addItem("标准 UTC", "utc")
        self.time_mode.addItem("旧固件：本地时间标记为 Z", "local")
        self.time_mode.setCurrentIndex(max(0, self.time_mode.findData(settings.get("recording_time_mode", "auto"))))
        form.addRow("录像时间协议", self.time_mode)
        self.output = QLineEdit(settings.get("output_dir", str(settings.directory / "exports")))
        form.addRow("截图与导出目录", self.output)
        form.addRow("", button("选择目录…", self.choose_output))
        layout.addLayout(form)
        note = label("带宽预算按设备配置码率估算，通过切换或暂停码流控制。\n"
                     "它不是线路测速结果，也不会修改 NVR 的编码或录像配置。\n"
                     "密码仅保存在当前会话中。设置生效时会重新连接预览。", "Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(self.save)
        controls.rejected.connect(self.reject)
        layout.addWidget(controls)

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "选择截图与录像保存目录", self.output.text())
        if path:
            self.output.setText(path)

    def save(self):
        self.settings.update(hardware=self.hardware.isChecked(), transport=self.transport.currentData(),
                             budget_kbps=self.budget.value(), max_live=self.max_live.value(),
                             recording_time_mode=self.time_mode.currentData(),
                             output_dir=self.output.text().strip() or str(self.settings.directory / "exports"))
        self.accept()
