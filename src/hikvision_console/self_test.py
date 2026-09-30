"""Native rendering smoke test used for source and packaged builds, with synthetic video only."""
import json
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget

from .player import Player, load_vlc, shutdown_vlc


def run_components(app, path: Path, directory: Path):
    """Limited hosted-macOS check: explicitly does NOT verify native video output."""
    from .exports import ffmpeg_binary

    directory.mkdir(parents=True, exist_ok=True)
    result = {"test_kind": "components", "native_rendering_tested": False, "passed": False}
    surface = QWidget()
    surface.resize(640, 360)
    surface.show()
    try:
        app.processEvents()
        result["qt_surface"] = bool(surface.winId()) and not surface.grab().isNull()
        vlc, instance = load_vlc()
        result["vlc_version"] = vlc.libvlc_get_version().decode(errors="replace")
        media_player = instance.media_player_new()
        result["vlc_player"] = media_player is not None
        media_player.release()
        frame = directory / "decoded.png"
        subprocess.run([ffmpeg_binary(), "-v", "error", "-i", str(path),
                        "-frames:v", "1", "-y", str(frame)], check=True, timeout=20)
        decoded = QImage(str(frame))
        result["ffmpeg_decode"] = not decoded.isNull() and decoded.width() == 640
        result["passed"] = all(result[key] for key in ("qt_surface", "vlc_player", "ffmpeg_decode"))
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        surface.close()
        shutdown_vlc()
    (directory / "component-smoke.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)
    return 0 if result["passed"] else 1


def run(app, path: Path, directory: Path):
    surface = QWidget()
    surface.setWindowTitle("Hikvision Console · native smoke test")
    surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
    surface.resize(640, 360)
    surface.show()
    player = Player(surface)
    result = {"test_kind": "native", "native_rendering_tested": True,
              "frames": 0, "pause": False, "rate": False, "snapshot": False, "native_vout": False}
    player.status.connect(lambda status: result.update(last_status=status))
    player.metrics.connect(lambda data: result.update(frames=max(result["frames"], data["frames"])))
    started = [False]
    screenshot = directory / "native-smoke.png"

    def resume():
        result["pause"] = bool(player.player and player.paused)
        player.pause(False)
        result["rate"] = player.set_rate(2)

    def capture():
        player.set_zoom(2)
        QTimer.singleShot(300, lambda: player.screenshot(screenshot))

    def ready():
        if started[0]:
            return
        started[0] = True
        result["native_vout"] = bool(player.player.has_vout())
        QTimer.singleShot(200, lambda: player.pause(True))
        QTimer.singleShot(900, resume)
        QTimer.singleShot(1500, capture)
        QTimer.singleShot(4000, finish)

    finished = [False]

    def finish():
        if finished[0]:
            return
        finished[0] = True
        image = QImage(str(screenshot))
        result["snapshot"] = not image.isNull() and image.width() > 0
        result["passed"] = bool(result["frames"] > 30 and result["pause"] and result["rate"]
                                and result["snapshot"] and result["native_vout"])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "native-smoke.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
        player.stop()
        app.quit()

    directory.mkdir(parents=True, exist_ok=True)
    player.playing.connect(ready)
    try:
        vlc, _ = load_vlc()
        result["vlc_version"] = vlc.libvlc_get_version().decode(errors="replace")
    except Exception as exc:
        result["component_error"] = f"{type(exc).__name__}: {exc}"
    player.start(path.resolve().as_uri(), hardware=False)
    QTimer.singleShot(15000, finish)
    app.exec()
    shutdown_vlc()
    return 0 if result.get("passed") else 1
