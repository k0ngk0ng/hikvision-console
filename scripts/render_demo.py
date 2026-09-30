"""Render a synthetic UI preview; no device or credential access."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from hikvision_console.main_window import MainWindow
from hikvision_console.models import Channel, Connection, Device, Stream
from hikvision_console.storage import Settings
from hikvision_console.ui_style import STYLE

app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(STYLE)
window = MainWindow(Settings(Path(".hikvision-console/render")))
names = ["前门入口", "车库", "院内东侧", "院内西侧", "走廊", "后门", "备用通道", "备用通道"]
channels = [Channel(i, name, i <= 6, i <= 6, "在线" if i <= 6 else "未接入",
                    {"main": Stream(str(i*100+1), "H.265", 1920, 1080, 2048, 25),
                     "sub": Stream(str(i*100+2), "H.265", 640, 360, 512, 25)}) for i, name in enumerate(names, 1)]
device = Device("DS-7108N · 演示设备", "DEMO", channels, datetime.now(timezone(timedelta(hours=8))))
window.device = device
window.live.set_device(Connection("demo.invalid"), device)
window.playback.set_device(None, device)
window.populate_channels()
window.device_label.setText("●  离线演示 · 6 在线 / 8 通道")
window.show()


def capture():
    Path("artifacts").mkdir(exist_ok=True)
    window.grab().save("artifacts/desktop-preview.png")
    window.close()


QTimer.singleShot(700, capture)
app.exec()
