from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Hikvision Console — 直连 NVR 的桌面监控客户端")
    parser.add_argument("--connect", action="store_true", help="使用环境变量和本地 .nvrpass 连接设备")
    parser.add_argument("--host", default=None)
    parser.add_argument("--demo", action="store_true", help="离线演示布局，不连接设备")
    parser.add_argument("--smoke-seconds", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--self-test", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    # Qt/libVLC use X11 handles on Linux; xcb also works through XWayland.
    if sys.platform.startswith("linux"):
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    from datetime import datetime, timedelta, timezone

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow
    from .models import Channel, Connection, Device, Stream
    from .player import shutdown_vlc
    from .storage import Settings
    from .ui_style import STYLE

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Hikvision Console")
    app.setOrganizationName("HikvisionConsole")
    asset_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    app.setWindowIcon(QIcon(str(asset_root / "assets/icon.svg")))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    if getattr(sys, "frozen", False) and not os.environ.get("HIKVISION_HOME"):
        from PySide6.QtCore import QStandardPaths
        os.environ["HIKVISION_HOME"] = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    settings = Settings()
    if args.self_test:
        from .self_test import run
        return run(app, args.self_test, settings.directory / "self-test")
    window = MainWindow(settings)
    window.show()
    if args.demo:
        channels = [Channel(i, f"摄像头 {i:02d}", i <= 6, i <= 6, "在线" if i <= 6 else "未接入",
                            {"main": Stream(str(i*100+1), "H.265", 1920, 1080, 2048, 25),
                             "sub": Stream(str(i*100+2), "H.265", 640, 360, 512, 25)}) for i in range(1, 9)]
        device = Device("离线演示 · 8 通道", "DEMO", channels,
                        datetime.now(timezone(timedelta(hours=8))))
        window.device = device
        window.live.set_device(Connection("demo.invalid"), device)
        window.playback.set_device(None, device)
        window.populate_channels()
        window.device_label.setText("●  离线演示 · 不连接真实设备")
        window.live.toggle.setEnabled(False)
    elif args.connect:
        password = os.environ.get("NVR_PASSWORD", "")
        password_file = Path(".nvrpass")
        if not password and password_file.exists():
            password = password_file.read_text().strip()
        if not password:
            QTimer.singleShot(0, window.connect_dialog)
        else:
            connection = Connection(args.host or os.environ.get("NVR_HOST") or settings.get("host", "192.168.1.100"),
                                    os.environ.get("NVR_USERNAME", "admin"), password,
                                    settings.get("http_port", 80), settings.get("rtsp_port", 554),
                                    settings.get("https", False), settings.get("verify_tls", True))
            QTimer.singleShot(0, lambda: window.connect_device(connection))
    elif not settings.get("host"):
        QTimer.singleShot(0, window.connect_dialog)
    if args.smoke_seconds:
        QTimer.singleShot(args.smoke_seconds*1000, window.close)
    result = app.exec()
    shutdown_vlc()
    window.tasks.pool.waitForDone(12000)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
