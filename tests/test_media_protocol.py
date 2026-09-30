import io
from types import SimpleNamespace

import pytest

from hikvision_console.media_source import rtsp_input
from hikvision_console.rtsp_proxy import pop_message, with_scale


def test_rtsp_fragmentation_and_interleaved_rtp_are_preserved():
    request = b"PLAY rtsp://localhost/track RTSP/1.0\r\nCSeq: 4\r\nAuthorization: Digest unchanged\r\n\r\n"
    assert pop_message(request[:30]) == (None, request[:30])
    rtp = b"$\x00\x00\x04abcd"
    one, rest = pop_message(request+rtp)
    assert one == request
    assert pop_message(rest) == (rtp, b"")
    scaled = with_scale(request, 4)
    assert b"Scale: 4\r\n" in scaled
    assert b"Authorization: Digest unchanged" in scaled
    assert with_scale(rtp, 4) == rtp


def test_body_and_duplicate_scale_handling():
    message = b"PLAY rtsp://host/t RTSP/1.0\r\nCSeq: 2\r\nScale: 1\r\nContent-Length: 4\r\n\r\ndata"
    transformed = with_scale(message, 2)
    assert transformed.count(b"Scale:") == 1
    assert transformed.endswith(b"\r\n\r\ndata")
    assert pop_message(transformed) == (transformed, b"")
    with pytest.raises(ValueError):
        pop_message(b"x"*70000)


def test_credentials_never_enter_ffmpeg_arguments():
    url = "rtsp://admin:secret%40pass@192.168.1.100/Streaming/tracks/101?name=a'b"
    arguments, payload = rtsp_input(url)
    assert "secret" not in " ".join(arguments)
    assert b"secret%40pass" in payload
    assert b"a'\\''b" in payload
    with pytest.raises(ValueError):
        rtsp_input("rtsp://nvr/a\nfile '/etc/passwd'")


def test_ingest_capacity_error_is_actionable_without_retaining_credentials():
    from hikvision_console.media_bridge import StreamBridge
    bridge = StreamBridge.__new__(StreamBridge)
    bridge.failure_reason = ""
    bridge.process = SimpleNamespace(stderr=io.BytesIO(
        b"[rtsp] method DESCRIBE failed: 453 Not Enough Bandwidth\n"
        b"Impossible to open rtsp://admin:secret@nvr/Streaming/tracks/201\n"))
    bridge.read_errors()
    assert "453" in bridge.failure_reason
    assert "资源" in bridge.failure_reason
    assert "secret" not in bridge.failure_reason
    assert "rtsp://" not in bridge.failure_reason
