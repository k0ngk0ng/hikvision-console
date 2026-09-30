"""Opt-in device integration checks; never changes device configuration.

Run from the repository root. Outputs only status/counters. Images, if explicitly
requested, go under artifacts/private and must not be shipped.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QGridLayout, QWidget

from hikvision_console.api import NvrClient
from hikvision_console.models import Connection
from hikvision_console.player import Player, shutdown_vlc


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--host", default="192.168.1.100")
    args.add_argument("--channels", default="1")
    args.add_argument("--quality", choices=["main", "sub"], default="sub")
    args.add_argument("--seconds", type=int, default=35)
    args.add_argument("--playback-minutes-ago", type=int)
    args.add_argument("--hardware", action="store_true")
    args.add_argument("--speed", type=float, default=1, choices=[0.5, 1, 2, 4, 8])
    args.add_argument("--snapshot", action="store_true")
    parsed = args.parse_args()
    secret = os.environ.get("NVR_PASSWORD") or Path(".nvrpass").read_text().strip()
    connection = Connection(parsed.host, password=secret)
    client = NvrClient(connection)
    device = client.discover()
    print(json.dumps({"model": device.model, "ports": len(device.channels),
                      "online": sum(c.online for c in device.channels)}, ensure_ascii=False), flush=True)
    ids = [int(x) for x in parsed.channels.split(",")]
    app = QApplication([])
    window = QWidget()
    grid = QGridLayout(window)
    window.resize(1000, 650)
    surfaces, players, results = [], [], {}
    for index, cid in enumerate(ids):
        channel = next(c for c in device.channels if c.id == cid)
        surface = QWidget()
        surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        surface.setMinimumSize(240, 160)
        grid.addWidget(surface, index//3, index%3)
        player = Player(surface)
        player.speed = parsed.speed
        player.metrics.connect(lambda data, cid=cid: results.update({cid: data}))
        if parsed.playback_minutes_ago is not None:
            when = datetime.now(timezone.utc)-timedelta(minutes=parsed.playback_minutes_ago)
            records = client.search(cid, when, when+timedelta(minutes=1))
            if not records:
                raise RuntimeError("No recording in test interval")
            url = client.playback_url(records[0], when, when+timedelta(minutes=2))
        else:
            url = connection.rtsp_url(channel.stream(parsed.quality).id)
        surfaces.append(surface)
        players.append(player)
        QTimer.singleShot(index*450, lambda p=player, u=url: p.start(
            u, live=parsed.playback_minutes_ago is None, hardware=parsed.hardware))
    window.show()

    def finish():
        if parsed.snapshot:
            directory = Path("artifacts/private")
            directory.mkdir(parents=True, exist_ok=True)
            for cid, player in zip(ids, players):
                player.screenshot((directory/f"verify-ch{cid}.png").resolve())
        print(json.dumps(results, ensure_ascii=False), flush=True)
        for player in players:
            if player.bridge and player.bridge.proxy:
                print(json.dumps({"scale_status": player.bridge.proxy.play_status,
                                  "accepted_scale": player.bridge.proxy.accepted_scale}), flush=True)
        QTimer.singleShot(700 if parsed.snapshot else 0, stop)

    def stop():
        for player in players:
            player.stop()
        app.quit()

    QTimer.singleShot(parsed.seconds*1000, finish)
    app.exec()
    shutdown_vlc()
    client.close()
    return 0 if len(results) == len(ids) and all(r["frames"] > 0 for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
