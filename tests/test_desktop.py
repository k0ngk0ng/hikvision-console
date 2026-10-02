import json
import sys
from urllib.request import urlopen

import pytest

from hikvision_console.media_bridge import StreamBridge


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console allocation")
def test_ingest_child_has_no_windows_console(monkeypatch):
    # Exercise the actual bridge launch (including its stdin/stdout pipes), not
    # merely the helper flags. A console-subsystem child must have no console.
    code = ("import ctypes,json,sys; sys.stdin.buffer.read(); "
            "print(json.dumps({'console':ctypes.windll.kernel32.GetConsoleWindow()}))")
    monkeypatch.setattr("hikvision_console.media_bridge.ffmpeg_binary", lambda: sys.executable)
    monkeypatch.setattr(StreamBridge, "command", lambda self: [sys.executable, "-c", code])
    bridge = StreamBridge("rtsp://test.invalid/Streaming/Channels/101")
    try:
        with urlopen(bridge.url, timeout=10) as response:
            assert json.loads(response.read())["console"] == 0
    finally:
        bridge.stop()
