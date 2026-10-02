"""Native rendering smoke test used for source and packaged builds, with synthetic video only."""
import json
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget

from .desktop import APP_ID, subprocess_options
from .player import Player, load_vlc, shutdown_vlc
from .widgets import VideoSurface


def windows_desktop_checks(surface):
    """Check the actual window icon and Explorer identity in a packaged process."""
    import ctypes
    from ctypes import wintypes

    shell = ctypes.windll.shell32
    app_id = ctypes.c_void_p()
    status = shell.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(app_id))
    identity = ctypes.wstring_at(app_id) if status == 0 and app_id.value else None
    if app_id.value:
        ctypes.windll.ole32.CoTaskMemFree(app_id)
    send = ctypes.windll.user32.SendMessageW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    send.restype = ctypes.c_ssize_t
    image = surface.windowIcon().pixmap(32, 32).toImage()
    colors = {image.pixel(x, y) for x in range(image.width()) for y in range(image.height())}
    return {"taskbar_identity": identity == APP_ID,
            "window_icon": len(colors) > 2 and bool(send(int(surface.winId()), 0x007F, 1, 0))}


def windows_double_click(surface):
    """Use OS mouse input to hit the native VLC child, rather than bypass it with QtTest."""
    import ctypes

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QCursor

    user = ctypes.windll.user32
    user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user.SetForegroundWindow(int(surface.window().winId()))
    point = surface.mapToGlobal(QPoint(surface.width() // 2, surface.height() // 2))
    QCursor.setPos(point)

    def click():
        user.mouse_event(0x0002, 0, 0, 0, 0)
        user.mouse_event(0x0004, 0, 0, 0, 0)
    click()
    QTimer.singleShot(80, click)


def windows_ffmpeg_checks(path):
    """Use the shipped FFmpeg itself, without suppression flags, from the GUI EXE."""
    import ctypes
    import struct

    from PySide6.QtCore import QProcess

    from .exports import ffmpeg_binary
    binary = ffmpeg_binary()
    data = Path(binary).read_bytes()
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    gui = struct.unpack_from("<H", data, pe + 24 + 68)[0] == 2
    args = ["-nostdin", "-loglevel", "error", "-re", "-stream_loop", "-1", "-i", str(path), "-f", "null", "-"]
    kernel = ctypes.windll.kernel32
    results = {"ffmpeg_gui_subsystem": gui}
    assert not kernel.GetConsoleWindow(), "Run console verification from the frozen GUI app"
    # Default subprocess and QProcess launches previously were not covered by tests.
    with subprocess.Popen([binary, *args], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
        time.sleep(0.3)
        attached = bool(kernel.AttachConsole(child.pid))
        if attached:
            kernel.FreeConsole()
        results["ffmpeg_popen_no_console"] = not attached and child.poll() is None
        child.terminate()
        child.communicate(timeout=5)
    child = QProcess()
    child.start(binary, args)
    started = child.waitForStarted(5000)
    child.waitForReadyRead(300)
    attached = bool(kernel.AttachConsole(int(child.processId()))) if started else False
    if attached:
        kernel.FreeConsole()
    results["ffmpeg_qprocess_no_console"] = started and not attached
    child.kill()
    child.waitForFinished(5000)
    return results


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
                        "-frames:v", "1", "-y", str(frame)], check=True, timeout=20, **subprocess_options())
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
    surface = VideoSurface()
    surface.setWindowTitle("Hikvision Console · native smoke test")
    surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
    surface.resize(640, 360)
    surface.show()
    player = Player(surface)
    result = {"test_kind": "native", "native_rendering_tested": True,
              "frames": 0, "pause": False, "rate": False, "snapshot": False, "native_vout": False}
    if sys.platform == "win32":
        result.update(windows_desktop_checks(surface), native_double_click=False)
        if getattr(sys, "frozen", False):
            result.update(windows_ffmpeg_checks(path))
        surface.double_clicked.connect(lambda: result.update(native_double_click=True))
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
        if sys.platform == "win32":
            QTimer.singleShot(2200, lambda: windows_double_click(surface))
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
        if sys.platform == "win32":
            result["passed"] &= all(result[key] for key in ("taskbar_identity", "window_icon", "native_double_click"))
            result["passed"] &= all(value for key, value in result.items() if key.startswith("ffmpeg_"))
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


def run_interaction(app, path: Path, directory: Path):
    """Exercise the actual fullscreen grid and native children using Windows mouse input."""
    from datetime import datetime, timezone

    from .main_window import MainWindow
    from .models import Channel, Connection, Device
    from .storage import Settings

    settings = Settings(directory / "settings")
    settings.update(grid_size=4)
    window = MainWindow(settings)
    window.show()
    device = Device("Synthetic", "CI", [Channel(i, f"Camera {i}", True, True, "在线") for i in range(1, 5)],
                    datetime.now(timezone.utc))
    window.live.set_device(Connection("demo.invalid"), device)
    window.live.enabled = True

    def reconcile():
        for tile in window.live.tiles.values():
            if not tile.player.want_play:
                tile.player.start(path.resolve().as_uri(), hardware=False)
    window.live.reconcile = reconcile
    result = {"passed": False, "raw_input_registered": window.video_mouse.registered}
    phase = [0]
    started = time.monotonic()

    def finish():
        timer.stop()
        result["passed"] = all(result.get(key) for key in ("raw_input_registered", "focused", "returned", "normal_controls"))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "interaction-smoke.json").write_text(json.dumps(result, indent=2))
        window.close()

    def poll():
        if time.monotonic() - started > 25:
            result["phase"] = phase[0]
            finish()
            return
        if phase[0] == 0 and all(tile.player.has_played for tile in window.live.tiles.values()):
            window.toggle_fullscreen()
            phase[0] = 1
            QTimer.singleShot(800, lambda: windows_double_click(window.live.tiles[2].surface))
        elif phase[0] == 1 and window.live.focused == 2 and window.live.tiles[2].player.has_played:
            result["focused"] = window.isFullScreen() and len(window.live.tiles) == 1
            phase[0] = 2
            QTimer.singleShot(800, lambda: windows_double_click(window.live.tiles[2].surface))
        elif phase[0] == 2 and window.live.focused is None and len(window.live.tiles) == 4:
            result["returned"] = window.isFullScreen()
            window.leave_fullscreen()
            phase[0] = 3
        elif phase[0] == 3:
            result["normal_controls"] = all(tile.footer.isVisible()
                                              and tile.footer.height() >= tile.footer.minimumSizeHint().height()
                                              for tile in window.live.tiles.values())
            finish()
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(200)
    QTimer.singleShot(300, reconcile)
    app.exec()
    shutdown_vlc()
    return 0 if result["passed"] else 1
