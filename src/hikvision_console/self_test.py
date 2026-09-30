"""Native rendering smoke test used for source and packaged builds, with synthetic video only."""
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget

from .player import Player, shutdown_vlc


def run(app, path: Path, directory: Path):
    surface = QWidget()
    surface.setWindowTitle("Hikvision Console · native smoke test")
    surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
    surface.resize(640, 360)
    surface.show()
    player = Player(surface)
    result = {"frames": 0, "pause": False, "rate": False, "snapshot": False, "native_vout": False}
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
    player.start(path.resolve().as_uri(), hardware=False)
    QTimer.singleShot(15000, finish)
    app.exec()
    shutdown_vlc()
    return 0 if result.get("passed") else 1
