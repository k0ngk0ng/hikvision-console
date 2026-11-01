from datetime import timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt

from hikvision_console.main_window import MainWindow
from hikvision_console.models import Connection, Recording
from hikvision_console.player import Player
from hikvision_console.rtsp_proxy import RtspScaleProxy
from hikvision_console.storage import Settings


@pytest.fixture
def replay(qtbot, tmp_path, device, monkeypatch):
    window = MainWindow(Settings(tmp_path))
    qtbot.addWidget(window)
    page = window.playback
    page.set_device(SimpleNamespace(playback_url=lambda r, a, b: f"file:///clip-{a:%H%M%S}.mp4"), device)
    start = device.time
    page.records = {1: [Recording(1, "101", start, start+timedelta(seconds=20), "rtsp://nvr/one"),
                        Recording(1, "101", start+timedelta(seconds=30), start+timedelta(seconds=60), "rtsp://nvr/two")]}
    starts = []
    for tile in page.tiles.values():
        monkeypatch.setattr(tile.player, "start", lambda url, **kw: starts.append((url, kw)))
    page.timeline.set_day(*page.day_bounds(), page.records[1])
    window.navigate(1)
    window.show()
    yield window, page, start, starts
    window.close()


def test_timeline_chooses_record_and_keyboard_skips_gaps(replay, qtbot):
    window, page, start, starts = replay
    page.seek(start+timedelta(seconds=25))
    assert page.clock == start+timedelta(seconds=30)
    qtbot.keyClick(page, Qt.Key.Key_Right)
    assert page.clock == start+timedelta(seconds=35)
    qtbot.keyClick(page, Qt.Key.Key_Left, Qt.KeyboardModifier.ControlModifier)
    assert page.clock == start+timedelta(seconds=5)
    qtbot.keyClick(page, Qt.Key.Key_Space)
    assert page.paused
    qtbot.keyClick(page, Qt.Key.Key_Right)
    assert page.clock == start+timedelta(seconds=10)
    assert page.paused
    page.time.setFocus()
    qtbot.keyClick(page.time, Qt.Key.Key_Right)
    assert page.clock == start+timedelta(seconds=10)
    assert len(starts) == 4


def test_wide_video_and_fullscreen_click_reveals_bottom_timeline(replay, qtbot):
    window, page, start, _ = replay
    window.resize(1440, 900)
    qtbot.wait(30)
    assert page.video_area.width() >= page.width()-60
    assert page.timeline.mapTo(page, QPoint()).y() > page.pause_button.mapTo(page, QPoint()).y()
    page.seek(start)
    window.toggle_fullscreen()
    qtbot.wait(30)
    assert page.footer.isHidden()
    qtbot.mouseClick(page.tiles[1].surface, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(page.footer.isVisible)
    assert page.timeline.isVisible()
    qtbot.mouseClick(page.tiles[1].surface, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(page.footer.isHidden)
    window.leave_fullscreen()
    assert not page.footer.isHidden()


def test_16x_speed_and_paused_segment_transition(replay):
    _, page, start, _ = replay
    assert page.speed.findData(16) >= 0
    page.seek(start)
    page.toggle_pause()
    page.speed.setCurrentIndex(page.speed.findData(16))
    assert page.paused
    assert page.tiles[1].player.speed == 16
    page.on_ended(1)
    assert page.clock == start+timedelta(seconds=30)
    assert page.paused
    assert page.tiles[1].player.speed == 16
    proxy = RtspScaleProxy("rtsp://127.0.0.1:1/track", 16)
    proxy.close()


def test_zoom_keeps_selected_region_in_video_bounds(qtbot):
    from PySide6.QtWidgets import QWidget
    surface = QWidget()
    qtbot.addWidget(surface)
    player = Player(surface)
    crops = []
    player.player = SimpleNamespace(video_get_size=lambda _: (1920, 1080),
                                    video_set_crop_geometry=crops.append)
    player.set_zoom(4, (0.95, 0.05))
    assert crops[-1] == "480x270+1440+0"
    player.set_zoom(1)
    assert crops[-1] is None
    player.player = None


def test_face_scan_cancel_and_late_result_cannot_seek_new_channel(replay, monkeypatch):
    _, page, start, _ = replay
    page.seek(start)
    page.tiles[1].player.has_played = True
    monkeypatch.setattr(page.tiles[1].player, "screenshot", lambda path: True)
    callbacks = []
    monkeypatch.setattr(page.tasks, "submit", lambda work, ok, fail: callbacks.append(ok))
    page.toggle_face_scan()
    assert page.face_scanning
    assert page.speed.currentData() == 16
    page.scan_face_frame()
    assert callbacks
    page.toggle_face_scan()
    assert not page.face_scanning
    assert page.speed.currentData() == 1
    callbacks[0](True)
    assert not page.face_marks
    assert page.clock == start


def test_face_hit_pauses_at_sample_time_and_marks_timeline(replay, monkeypatch):
    _, page, start, _ = replay
    page.seek(start)
    page.tiles[1].player.has_played = True
    monkeypatch.setattr(page.tiles[1].player, "screenshot", lambda path: True)
    callbacks = []
    monkeypatch.setattr(page.tasks, "submit", lambda work, ok, fail: callbacks.append(ok))
    page.toggle_face_scan()
    page.tiles[1].player.has_played = True
    page.scan_face_frame()
    page.clock = start+timedelta(seconds=8)
    callbacks[0](True)
    assert not page.face_scanning
    assert page.clock == start
    assert page.paused
    assert page.face_marks == [start]
    assert page.timeline.markers == [start]


def test_transient_hide_does_not_stop_live_and_guard_revives_unexpected_stop(qtbot, tmp_path, device, monkeypatch):
    window = MainWindow(Settings(tmp_path))
    qtbot.addWidget(window)
    page = window.live
    page.set_device(Connection("nvr"), device)
    starts = []
    for tile in page.tiles.values():
        def start(url, *, target=tile.player, **kwargs):
            target.want_play = True
            starts.append(url)
        monkeypatch.setattr(tile.player, "start", start)
    window.show()
    page.enabled = True
    page.reconcile()
    qtbot.waitUntil(lambda: bool(starts))
    tile = page.tiles[1]
    page.hide()
    page.show()
    qtbot.wait(250)
    assert tile.player.want_play
    tile.player.want_play = False
    before = len(starts)
    page.ensure_preview()
    qtbot.waitUntil(lambda: len(starts) > before)
    assert tile.player.want_play
    page.enabled = False
    page.stop_all()
    before = len(starts)
    page.ensure_preview()
    qtbot.wait(500)
    assert len(starts) == before
    window.close()
