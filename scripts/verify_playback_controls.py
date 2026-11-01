"""Repeatable native replay E2E with synthetic media; no NVR credentials or footage."""
import json
import os
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

os.environ.pop("QT_QPA_PLATFORM", None)

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from hikvision_console.exports import ffmpeg_binary
from hikvision_console.main_window import MainWindow
from hikvision_console.models import Channel, Device, Recording
from hikvision_console.player import shutdown_vlc
from hikvision_console.storage import Settings
from hikvision_console.ui_style import STYLE

root = Path.cwd()
out = root / "artifacts/private/playback-controls"
out.mkdir(parents=True, exist_ok=True)
video = out / "synthetic.mp4"
subprocess.run([ffmpeg_binary(), "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=25",
                "-t", "25", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(video)], check=True)
app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(STYLE)
window = MainWindow(Settings(out / "settings"))
page = window.playback
now = datetime.now(timezone.utc).replace(microsecond=0)
device = Device("Synthetic E2E", "test", [Channel(1, "合成测试视频", True)], now)
page.set_device(SimpleNamespace(playback_url=lambda *args: video.as_uri()), device)
record = Recording(1, "101", now, now+timedelta(seconds=90), "rtsp://synthetic.invalid/track")
page.records = {1: [record]}
page.timeline.set_day(now, record.end, [record])
window.navigate(1)
window.show()
checks = {}


def wait_for(condition, timeout=12000):
    for _ in range(timeout//100):
        QTest.qWait(100)
        if condition():
            return
    raise AssertionError("Timed out waiting for native playback")


try:
    page.seek(now)
    wait_for(lambda: page.tiles[1].player.has_played)
    checks["native_render"] = page.tiles[1].player.last_frame > 0
    checks["wide_viewport"] = page.video_area.width() >= page.width()-60
    QTest.keyClick(page, Qt.Key.Key_Space)
    checks["space_pauses"] = page.paused
    QTest.keyClick(page, Qt.Key.Key_Right)
    checks["right_seeks_5_seconds"] = page.clock >= now+timedelta(seconds=5) and page.paused
    QTest.keyClick(page, Qt.Key.Key_Right, Qt.KeyboardModifier.ControlModifier)
    checks["ctrl_right_seeks_30_seconds"] = page.clock >= now+timedelta(seconds=35) and page.paused
    wait_for(lambda: page.tiles[1].player.has_played)
    page.tiles[1].player.set_zoom(2, (0.75, 0.25))
    checks["region_zoom"] = page.tiles[1].player.zoom == 2
    checks["rate_16_available"] = page.speed.findData(16) >= 0
    window.grab().save(str(out / "playback-window.png"))
    window.toggle_fullscreen()
    QTest.qWait(1000)
    checks["fullscreen_controls_hidden"] = page.footer.isHidden()
    QTest.mouseClick(page.tiles[1].surface, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
    wait_for(page.footer.isVisible, 3000)
    checks["click_reveals_timeline"] = page.timeline.isVisible()
    window.grab().save(str(out / "playback-fullscreen.png"))
    window.leave_fullscreen()
    QTest.qWait(500)
    checks["controls_restored"] = page.footer.isVisible()
    from hikvision_console.faces import detect_face
    # Verify the bundled classifier against an actual rendered synthetic frame.
    frame = out / "frame.png"
    page.tiles[1].player.screenshot(frame)
    wait_for(frame.exists)
    checks["synthetic_frame_is_not_face"] = not detect_face(frame)
    # Public OpenCV test image is downloaded only into ignored local E2E artifacts.
    portrait = out / "opencv-lena.jpg"
    if not portrait.exists():
        with urllib.request.urlopen("https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/lena.jpg", timeout=20) as response:
            portrait.write_bytes(response.read())
    face_video = out / "face-synthetic.mp4"
    subprocess.run([ffmpeg_binary(), "-v", "error", "-loop", "1", "-i", str(portrait),
                    "-t", "120", "-r", "10", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-y", str(face_video)], check=True)
    page.client = SimpleNamespace(playback_url=lambda *args: face_video.as_uri())
    page.tiles[1].player.set_zoom(1)
    page.seek(now)
    page.toggle_face_scan()
    wait_for(lambda: bool(page.face_marks), 18000)
    checks["face_sample_pauses_and_marks_timeline"] = page.paused and not page.face_scanning and bool(page.timeline.markers)
    checks["face_scan_restores_speed"] = page.speed.currentData() == 1
finally:
    window.close()
    shutdown_vlc()
    window.tasks.pool.waitForDone(12000)
    report = {"checks": checks, "passed": bool(checks) and all(checks.values()),
              "scope": "Synthetic local video; NVR Scale acceptance is verified separately."}
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
raise SystemExit(0 if report["passed"] else 1)
