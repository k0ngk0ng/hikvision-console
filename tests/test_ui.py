from datetime import timedelta
from types import SimpleNamespace

from PySide6.QtCore import QPoint, Qt

from hikvision_console.exports import ExportDialog, ExportJob, ExportManager
from hikvision_console.main_window import MainWindow
from hikvision_console.models import Connection, plan_streams
from hikvision_console.settings_ui import SettingsDialog
from hikvision_console.storage import Settings
from hikvision_console.widgets import Timeline


def test_bandwidth_budget_prioritizes_focused_channel(device):
    chosen, deferred = plan_streams(device.channels, {i: "main" for i in range(1, 9)}, 2600, 8, 5)
    assert chosen[5] == "main"
    assert len(chosen) == 2
    assert set(deferred) == {2, 3, 4, 6}
    assert chosen[1] == "sub"


def test_window_offline_channels_navigation_and_settings(qtbot, tmp_path, device):
    settings = Settings(tmp_path)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.device = device
    window.live.set_device(Connection("nvr"), device)
    window.playback.set_device(None, device)
    window.populate_channels()
    window.show()
    assert window.channel_tree.topLevelItemCount() == 8
    assert len(window.live.tiles) == 8
    assert window.live.tiles[8].state.text() == "离线"
    original_tile = window.live.tiles[1]
    window.live.swap_channels(1, 2)
    assert window.live.channels[0].id == 2
    assert window.live.tiles[1] is original_tile  # dragging reuses the live player
    assert settings.get("channel_order")["nvr"][:2] == [2, 1]
    window.navigate(1)
    assert window.pages.currentWidget() is window.playback
    window.live.layout_combo.setCurrentIndex(1)
    assert len(window.live.tiles) == 4
    window.live.next_page()
    assert set(window.live.tiles) == {5, 6, 7, 8}
    window.open_settings = lambda: None
    dialog = SettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog.budget.setValue(3000)
    dialog.save()
    assert settings.get("budget_kbps") == 3000
    window.close()
    assert "password" not in settings.path.read_text()


def test_timeline_maps_time_and_emits_seek(qtbot, device):
    timeline = Timeline()
    qtbot.addWidget(timeline)
    timeline.resize(1008, 112)
    start = device.time.replace(hour=0)
    timeline.set_day(start, start+timedelta(days=1), [])
    assert timeline.time_at(504) == start+timedelta(hours=12)
    with qtbot.waitSignal(timeline.seek_requested) as result:
        qtbot.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=QPoint(24, 50))
    assert result.args[0] == start


def test_fullscreen_double_click_focus_and_return_to_same_page(qtbot, tmp_path, device):
    settings = Settings(tmp_path)
    settings.update(grid_size=4)
    window = MainWindow(settings)
    qtbot.addWidget(window)
    window.live.set_device(Connection("nvr"), device)
    window.show()
    window.live.next_page()
    window.toggle_fullscreen()
    surface = window.live.tiles[5].surface
    qtbot.mouseDClick(surface, Qt.MouseButton.RightButton)
    assert window.live.focused is None
    qtbot.mouseDClick(surface, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.live.focused == 5)
    assert window.isFullScreen()
    assert list(window.live.tiles) == [5]
    assert window.live.requested[5] == "main"
    assert window.live.tiles[5].header.isHidden()
    qtbot.mouseDClick(window.live.tiles[5].surface, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.live.focused is None)
    assert window.isFullScreen()
    assert set(window.live.tiles) == {5, 6, 7, 8}
    assert window.live.page == 1
    assert window.live.requested[5] == "sub"
    window.close()


def test_nine_tiles_do_not_clip_controls_on_small_window(qtbot, tmp_path, device):
    from hikvision_console.ui_style import STYLE
    window = MainWindow(Settings(tmp_path))
    window.setStyleSheet(STYLE)
    qtbot.addWidget(window)
    window.live.set_device(Connection("nvr"), device)
    window.resize(1100, 700)
    window.show()
    qtbot.wait(30)
    for tile in window.live.tiles.values():
        assert tile.footer.height() >= tile.footer.minimumSizeHint().height()
        assert tile.rect().contains(tile.footer.geometry())
        for control in (tile.snap, tile.mute, tile.zoom):
            assert tile.footer.rect().contains(control.geometry())
    assert window.live.scroll.verticalScrollBar().maximum() > 0
    window.toggle_fullscreen()
    qtbot.wait(30)
    assert window.live.scroll.verticalScrollBar().isVisible() is False
    assert all(t.surface.geometry() == t.rect() for t in window.live.tiles.values())
    window.leave_fullscreen()
    qtbot.wait(30)
    assert all(t.footer.height() >= t.footer.minimumSizeHint().height() for t in window.live.tiles.values())
    window.close()


def test_preview_button_has_immediate_feedback(qtbot, tmp_path, device):
    window = MainWindow(Settings(tmp_path))
    qtbot.addWidget(window)
    window.live.set_device(Connection("nvr"), device)
    # Keep this behavioral check independent of any NVR/network.
    window.live.reconcile = lambda: None
    window.live.toggle_preview()
    assert window.live.toggle.isChecked()
    assert not window.live.toggle.isEnabled()
    assert "启动" in window.live.toggle.text()
    qtbot.waitUntil(window.live.toggle.isEnabled)
    window.live.toggle_preview()
    assert not window.live.toggle.isChecked()
    assert not window.live.toggle.isEnabled()
    qtbot.waitUntil(window.live.toggle.isEnabled)
    assert window.live.toggle.text() == "开始预览"
    window.close()


def test_export_dialog_preserves_device_timezone(qtbot, device):
    start = device.time
    bounds = (start.replace(hour=0), start.replace(hour=0)+timedelta(days=1))
    dialog = ExportDialog(start, bounds)
    qtbot.addWidget(dialog)
    begin, end = dialog.range()
    assert begin == start
    assert end-begin == timedelta(minutes=5)


def test_cancel_waiting_export_and_no_overwrite(qtbot, tmp_path, device):
    manager = ExportManager()
    manager.paused = True
    job = ExportJob(1, device.time, device.time+timedelta(seconds=10), "rtsp://secret@nvr/a", tmp_path/"clip.mp4")
    assert "secret" not in repr(job)
    manager.add([job])
    manager.cancel(job)
    assert job.state == "已取消"
    job.path.write_bytes(b"original")
    manager.retry(job)
    manager.paused = False
    manager.next()
    assert job.state == "失败"
    assert job.path.read_bytes() == b"original"


def test_late_discovery_response_cannot_replace_new_device(qtbot, tmp_path, monkeypatch, device):
    window = MainWindow(Settings(tmp_path))
    qtbot.addWidget(window)
    callbacks = []
    monkeypatch.setattr(window.tasks, "submit", lambda work, result, error: callbacks.append(result))
    window.connect_device(Connection("first"))
    window.connect_device(Connection("second"))
    callbacks[0](device)
    assert window.client is None
    callbacks[1](device)
    assert window.client.connection.host == "second"
    window.close()


def test_budget_never_silently_exceeds_limit(device):
    chosen, deferred = plan_streams(device.channels, {1: "main"}, 100, 8, 1)
    assert chosen == {}
    assert deferred == [1]


def test_linked_playback_waits_for_ready_channels_without_overriding_user_pause(qtbot, tmp_path, device):
    window = MainWindow(Settings(tmp_path))
    qtbot.addWidget(window)
    page = window.playback
    page.set_device(None, device)
    page.linked = [2]
    page.build_tiles()
    page.sync_pending = {1, 2}
    page.ready(1)
    assert page.tiles[1].player.paused is True
    page.paused = True
    page.ready(2)
    assert page.sync_pending == set()
    assert all(t.player.paused for t in page.tiles.values())
    page.paused = False
    page.sync_pending = {1, 2}
    page.ready(1)
    page.ready(2)
    assert not any(t.player.paused for t in page.tiles.values())
    window.close()


def test_export_waits_until_previous_upstream_session_exits(qtbot, tmp_path, device, monkeypatch):
    window = MainWindow(Settings(tmp_path))
    qtbot.addWidget(window)
    window.live.set_device(Connection("nvr"), device)
    running = [True]
    bridge = SimpleNamespace(process=SimpleNamespace(poll=lambda: None if running[0] else 0), stop=lambda: None)
    window.live.tiles[1].player.bridge = bridge
    queued = []
    monkeypatch.setattr(window.exports, "add", queued.extend)
    job = ExportJob(1, device.time, device.time+timedelta(seconds=6), "rtsp://nvr/a", tmp_path/"clip.mp4")
    window.start_exports([job])
    assert queued == []
    running[0] = False
    qtbot.waitUntil(lambda: queued == [job])
    window.close()
