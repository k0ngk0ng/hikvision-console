"""Opt-in end-to-end GUI/real-device validation, with a configurable live soak.

Only reads the NVR. All generated media stays in ignored artifacts/private.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from hikvision_console.api import redact
from hikvision_console.exports import ExportJob
from hikvision_console.main_window import MainWindow
from hikvision_console.models import Connection
from hikvision_console.player import shutdown_vlc
from hikvision_console.storage import Settings
from hikvision_console.ui_style import STYLE

parser = argparse.ArgumentParser()
parser.add_argument("--live-seconds", type=int, default=600)
parser.add_argument("--playback-channels", default="1,2", help="Comma-separated channels; default tests linked replay")
args = parser.parse_args()
playback_channels = list(dict.fromkeys(int(value) for value in args.playback_channels.split(",")))
root = Path.cwd()
directory = root / "artifacts/private/workflow"
directory.mkdir(parents=True, exist_ok=True)
app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet(STYLE)
settings = Settings(root / ".hikvision-console/workflow")
settings.update(hardware=False, output_dir=str(directory), grid_size=9, channel_selection={})
window = MainWindow(settings)
window.show()
connection = Connection("192.168.1.100", password=(root / ".nvrpass").read_text().strip())
report = {"checks": {}, "live_samples": [], "playback_samples": [], "errors": [],
          "started_at": datetime.now().isoformat(),
          "requested_playback_channels": playback_channels}
phase = "connect"
phase_start = time.monotonic()
last_sample = 0
target = None
job = None
pause_at = 0
finished = False


def change_phase(value):
    global phase, phase_start, last_sample
    phase, phase_start = value, time.monotonic()
    last_sample = 0
    print(json.dumps({"phase": value}), flush=True)


def finish(error=None):
    global finished
    if finished:
        return
    finished = True
    if error:
        report["errors"].append(error)
    report["passed"] = bool(not report["errors"] and report["checks"].get("export"))
    (directory / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    (directory / f"report-{datetime.now():%Y%m%d-%H%M%S}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps({"checks": report["checks"], "errors": report["errors"], "passed": report["passed"]}, ensure_ascii=False), flush=True)
    window.exports.shutdown()
    window.close()


def poll():
    global last_sample, target, job, pause_at
    elapsed = time.monotonic()-phase_start
    if phase == "connect":
        if window.device:
            report["checks"]["discovery"] = len(window.device.channels) == 8
            report["checks"]["legacy_time_mode"] = window.device.recording_time_mode == "local"
            window.live.toggle_preview()
            change_phase("live")
        elif elapsed > 30:
            finish("Device discovery timeout")
    elif phase == "live":
        if elapsed-last_sample >= 15:
            sample = {"seconds": round(elapsed), "channels": dict(window.live.last_metrics),
                      "visible": window.live.isVisible(), "active": dict(window.live.active),
                      "states": {cid: t.state.text() for cid, t in window.live.tiles.items()}}
            report["live_samples"].append(sample)
            print(json.dumps({"phase": phase, "seconds": sample["seconds"], "visible": sample["visible"],
                              "states": sample["states"]}, ensure_ascii=False), flush=True)
            last_sample = elapsed
        if elapsed >= args.live_seconds:
            online = [c.id for c in window.device.channels if c.online]
            report["checks"]["all_online_rendered"] = all(
                any(s["channels"].get(cid, {}).get("frames", 0) > 0 for s in report["live_samples"])
                for cid in online)
            report["checks"]["live_connections_kept_active"] = all(
                cid in window.live.active and window.live.tiles[cid].player.want_play for cid in online)
            report["checks"]["recent_frames_on_each_channel"] = all(
                any(s["seconds"] >= elapsed-60 and s["channels"].get(cid, {}).get("frames", 0) > 0
                    and s["channels"][cid].get("stale_seconds", 60) < 3 for s in report["live_samples"])
                for cid in online)
            report["checks"]["offline_slots"] = all(cid in window.live.tiles for cid in (7, 8))
            old = window.live.tiles[1]
            window.live.swap_channels(1, 2)
            report["checks"]["drag_reuses_player"] = window.live.tiles[1] is old
            window.grab().save(str(directory / "live-ui.png"))
            window.navigate(1)
            report["checks"]["hidden_live_stopped"] = not window.live.active
            window.playback.channel.setCurrentIndex(window.playback.channel.findData(playback_channels[0]))
            window.playback.linked = playback_channels[1:]
            window.playback.search()
            change_phase("search")
    elif phase == "search":
        if window.playback.records:
            report["checks"]["recording_search"] = all(window.playback.records.get(cid) for cid in playback_channels)
            target = datetime.now(timezone.utc)-timedelta(minutes=45)
            window.playback.seek(target)
            change_phase("playback")
        elif elapsed > 30:
            finish("Recording search timeout")
    elif phase == "playback":
        if elapsed-last_sample >= 10:
            sample = {"seconds": round(elapsed), "visible": window.playback.isVisible(),
                      "states": {cid: {"status": t.state.text(), "frames": t.player.last_frame,
                                       "want_play": t.player.want_play, "paused": t.player.paused}
                                 for cid, t in window.playback.tiles.items()}}
            report["playback_samples"].append(sample)
            print(json.dumps({"phase": phase, **sample}, ensure_ascii=False), flush=True)
            last_sample = elapsed
        if all(tile.player.has_played for tile in window.playback.tiles.values()):
            report["checks"]["requested_playback_rendered"] = len(window.playback.tiles) == len(playback_channels)
            if len(playback_channels) > 1:
                report["checks"]["linked_playback"] = True
            report["checks"]["playback_clock"] = bool(window.playback.clock and window.playback.clock > target)
            window.playback.toggle_pause()
            change_phase("pause")
        elif elapsed > 70:
            finish("Playback did not render every requested channel")
    elif phase == "pause":
        if elapsed > 2:
            report["checks"]["pause_all"] = all(tile.player.paused for tile in window.playback.tiles.values())
            window.playback.toggle_pause()
            window.playback.speed.setCurrentIndex(window.playback.speed.findData(2))
            change_phase("speed")
    elif phase == "speed":
        if all(tile.player.has_played for tile in window.playback.tiles.values()):
            report["checks"]["speed_request"] = all(tile.player.bridge.proxy.play_status == 200
                                                    for tile in window.playback.tiles.values())
            window.grab().save(str(directory / "playback-ui.png"))
            cid = playback_channels[0]
            record = next(r for r in window.playback.records[cid] if r.contains(target))
            path = directory / f"clip-{datetime.now():%H%M%S}.mp4"
            job = ExportJob(cid, target, target+timedelta(seconds=6), window.client.playback_url(record, target, record.end), path)
            window.start_exports([job])
            report["checks"]["hidden_playback_stopped"] = all(not t.player.want_play for t in window.playback.tiles.values())
            change_phase("export")
        elif elapsed > 70:
            finish("2x playback did not render")
    elif phase == "export":
        if job.state in ("完成", "失败", "已取消"):
            report["export_error"] = job.error
            report["export_diagnostic"] = redact(window.exports.error_buffer, connection)
            report["checks"]["export"] = job.state == "完成" and job.path.stat().st_size > 0
            if not all(report["checks"].values()):
                finish("One or more workflow checks failed")
            else:
                finish()
        elif elapsed > 90:
            finish("Export timeout")


timer = QTimer()
timer.timeout.connect(poll)
timer.start(500)
window.connect_device(connection)
app.exec()
shutdown_vlc()
window.tasks.pool.waitForDone(12000)
raise SystemExit(0 if report.get("passed") else 1)
