from datetime import timedelta
from types import SimpleNamespace

from PySide6.QtCore import QByteArray
from PySide6.QtWidgets import QWidget

from hikvision_console.exports import ExportJob, ExportManager
from hikvision_console.player import Player


def test_user_stop_cancels_pending_reconnect(qtbot, monkeypatch):
    import hikvision_console.player as module
    surface = QWidget()
    qtbot.addWidget(surface)
    player = Player(surface)
    player.want_play = True
    player.url = "rtsp://example/source"
    callbacks = []
    monkeypatch.setattr(module.QTimer, "singleShot", lambda delay, callback: callbacks.append(callback))
    opened = []
    monkeypatch.setattr(player, "_open", lambda: opened.append(True))
    player._retry("network interrupted")
    assert len(callbacks) == 1
    player.stop()
    callbacks[0]()
    assert opened == []
    assert player.want_play is False


def test_hardware_failure_fallback_is_local_to_failed_player(qtbot, monkeypatch):
    import hikvision_console.player as module
    surface = QWidget()
    qtbot.addWidget(surface)
    one, two = Player(surface), Player(surface)
    one.want_play = two.want_play = True
    monkeypatch.setattr(module.QTimer, "singleShot", lambda *args: None)
    one._retry("failure")
    assert one.hardware is True
    one._retry("failure")
    assert one.hardware is False
    assert two.hardware is True
    assert one.retry_count == 2


def test_paused_player_does_not_trigger_stall_reconnect(qtbot, monkeypatch):
    import hikvision_console.player as module
    surface = QWidget()
    qtbot.addWidget(surface)
    p = Player(surface)
    p.player = SimpleNamespace(get_state=lambda: "playing", get_time=lambda: 5000)
    p.media = SimpleNamespace(get_stats=lambda stats: None)
    p.last_activity = 0
    p.paused = True
    stats = SimpleNamespace(displayed_pictures=0, read_bytes=0, demux_read_bytes=0, lost_pictures=0)
    monkeypatch.setattr(module, "load_vlc", lambda: (SimpleNamespace(
        State=SimpleNamespace(Error="error", Ended="ended"), MediaStats=lambda: stats), None))
    failures = []
    monkeypatch.setattr(p, "_retry", failures.append)
    p._tick()
    assert failures == []
    p.player = p.media = None


def test_incomplete_export_is_not_presented_as_success(qtbot, tmp_path, device):
    manager = ExportManager()
    job = ExportJob(1, device.time, device.time+timedelta(seconds=30), "rtsp://nvr/a", tmp_path/"clip.mp4")
    job.temporary.write_bytes(b"truncated media")
    job.received_seconds = 2
    manager.current = job
    manager.jobs = [job]
    manager.process = SimpleNamespace(readAllStandardError=lambda: QByteArray(), deleteLater=lambda: None)
    manager.finished(0, None)
    assert job.state == "失败"
    assert not job.path.exists()
    assert not job.temporary.exists()


def test_duplicate_progress_does_not_reset_export_stall_watchdog(qtbot, tmp_path, device):
    manager = ExportManager()
    manager.current = ExportJob(1, device.time, device.time+timedelta(seconds=30), "rtsp://nvr/a", tmp_path/"clip.mp4")
    manager.process = SimpleNamespace(readAllStandardOutput=lambda: QByteArray(b"out_time_us=1000000\n"))
    resets = []
    manager.watchdog = SimpleNamespace(start=lambda: resets.append(True))
    manager.read_progress()
    manager.read_progress()
    assert len(resets) == 1


def test_continuously_moving_but_falling_behind_live_stream_is_reopened(qtbot, monkeypatch):
    import hikvision_console.player as module
    surface = QWidget()
    qtbot.addWidget(surface)
    p = Player(surface)
    p.player = SimpleNamespace(get_state=lambda: "playing", get_time=lambda: 10000)
    p.media = SimpleNamespace(get_stats=lambda stats: None)
    p.has_played = True
    p.clock_anchor = (0, 1000)
    p.last_frame = 10
    p.last_tick = 99
    stats = SimpleNamespace(displayed_pictures=20, read_bytes=1000, demux_read_bytes=1000, lost_pictures=0)
    monkeypatch.setattr(module.time, "monotonic", lambda: 100)
    monkeypatch.setattr(module, "load_vlc", lambda: (SimpleNamespace(
        State=SimpleNamespace(Error="error", Ended="ended"), MediaStats=lambda: stats), None))
    failures = []
    monkeypatch.setattr(p, "_retry", lambda reason, **options: failures.append((reason, options)))
    p._tick()
    assert len(failures) == 1
    assert "持续落后" in failures[0][0]
    assert failures[0][1] == {"refresh": True}
    p.player = p.media = None


def test_refreshing_a_lagging_stream_does_not_accumulate_connection_failures(qtbot, monkeypatch):
    import hikvision_console.player as module
    surface = QWidget()
    qtbot.addWidget(surface)
    player = Player(surface)
    player.want_play = True
    delays = []
    monkeypatch.setattr(module.QTimer, "singleShot", lambda delay, callback: delays.append(delay))
    for _ in range(8):
        player._retry("持续落后", refresh=True)
    assert max(delays) <= 2000
    assert player.failure_streak == 0
    assert player.hardware is True
