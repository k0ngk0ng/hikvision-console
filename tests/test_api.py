from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import httpx
import pytest

from hikvision_console.api import DeviceError, NvrClient, redact, xml_root
from hikvision_console.models import Connection, parse_time, utc_iso


def client_with(handler):
    return NvrClient(Connection("nvr.test", password="very-secret"), httpx.MockTransport(handler))


def test_digest_challenge_is_answered_without_clear_password():
    requests = []

    def handler(request):
        requests.append(request)
        if "Authorization" not in request.headers:
            return httpx.Response(401, headers={"WWW-Authenticate": 'Digest realm="NVR", nonce="1234", qop="auth"'})
        assert request.headers["Authorization"].startswith("Digest ")
        assert "very-secret" not in request.headers["Authorization"]
        return httpx.Response(200, content=b"<DeviceInfo><model>test</model></DeviceInfo>")

    client = client_with(handler)
    assert client.request("/ISAPI/System/deviceInfo").findtext("model") == "test"
    assert len(requests) == 2
    client.close()


def test_discovery_includes_all_ports_and_nonstandard_stream_ids():
    bodies = {
        "/ISAPI/System/deviceInfo": "<Info><model>24-port</model></Info>",
        "/ISAPI/System/capabilities": "<Cap><inputProxyNums>24</inputProxyNums></Cap>",
        "/ISAPI/ContentMgmt/InputProxy/channels": "<List><Channel><id>19</id><name>Gate</name></Channel></List>",
        "/ISAPI/ContentMgmt/InputProxy/channels/status": "<List><Status><id>19</id><online>true</online></Status></List>",
        "/ISAPI/Streaming/channels": """<List><StreamingChannel><id>1902</id><Video>
        <dynVideoInputChannelID>19</dynVideoInputChannelID><videoCodecType>H.265</videoCodecType>
        <constantBitRate>512</constantBitRate></Video></StreamingChannel></List>""",
        "/ISAPI/System/time": "<Time><localTime>2026-09-30T12:00:00+08:00</localTime></Time>",
        "/ISAPI/ContentMgmt/Storage": "<Storage/>",
    }
    client = client_with(lambda r: httpx.Response(200, content=bodies[r.url.path].encode()))
    device = client.discover()
    assert len(device.channels) == 24
    assert device.channels[18].name == "Gate"
    assert device.channels[18].stream("sub").id == "1902"
    assert device.channels[23].online is False
    assert device.time.utcoffset() == timedelta(hours=8)


def search_response(start, end, status="OK", track="101"):
    return f"""<CMSearchResult xmlns="http://www.hikvision.com/ver20/XMLSchema">
    <responseStatus>true</responseStatus><responseStatusStrg>{status}</responseStatusStrg>
    <numOfMatches>2</numOfMatches><matchList><searchMatchItem><trackID>{track}</trackID>
    <timeSpan><startTime>{start}</startTime><endTime>{end}</endTime></timeSpan>
    <mediaSegmentDescriptor><playbackURI>rtsp://192.168.1.100/Streaming/tracks/101?starttime=x&amp;endtime=y</playbackURI>
    </mediaSegmentDescriptor></searchMatchItem></matchList></CMSearchResult>""".encode()


def test_search_paginates_by_returned_items_and_normalizes_timezone():
    positions = []

    def handler(request):
        root = ET.fromstring(request.content)
        positions.append(int(root.findtext("searchResultPostion")))
        assert root.findtext(".//startTime") == "2026-09-29T16:00:00Z"
        n = len(positions)
        return httpx.Response(200, content=search_response(f"2026-09-30T0{n}:00:00Z", f"2026-09-30T0{n+1}:00:00Z",
                                                          "MORE" if n == 1 else "OK"))

    client = client_with(handler)
    start = parse_time("2026-09-30T00:00:00+08:00")
    records = client.search(1, start, start+timedelta(days=1))
    assert len(records) == 2
    assert positions == [0, 1]  # numOfMatches is NOT a reliable page length on this firmware.
    assert records[0].contains(records[0].start)
    assert not records[0].contains(records[0].end)


def test_search_detects_nonadvancing_pagination():
    body = search_response("2026-09-30T01:00:00Z", "2026-09-30T02:00:00Z", "MORE")
    client = client_with(lambda r: httpx.Response(200, content=body))
    start = parse_time("2026-09-30T00:00:00Z")
    with pytest.raises(DeviceError, match="分页未前进"):
        client.search(1, start, start+timedelta(days=1))


def test_playback_uri_uses_reachable_host_and_utc_without_double_credentials():
    c = Connection("10.0.0.1", "admin", "p@ss:/?#", rtsp_port=8554)
    uri = c.playback_url("rtsp://old:secret@192.168.1.100:554/Streaming/tracks/101?foo=bar&starttime=old",
                         parse_time("2026-09-30T12:00:00+08:00"))
    assert "10.0.0.1:8554" in uri
    assert "20260930T040000Z" in uri
    assert "old:secret" not in uri
    assert "foo=bar" in uri
    assert "p@ss" not in repr(c)


def test_redaction_and_untrusted_xml():
    c = Connection("nvr", password="s/ecret")
    assert "s/ecret" not in redact("bad rtsp://admin:s%2Fecret@nvr/path s/ecret", c)
    with pytest.raises(Exception):
        xml_root(b'<!DOCTYPE x [<!ENTITY y SYSTEM "file:///etc/passwd">]><x>&y;</x>')
    with pytest.raises(ValueError):
        utc_iso(datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        Connection("http://nvr")


def test_cancelled_search_makes_no_network_request():
    import threading
    cancel = threading.Event()
    cancel.set()
    client = client_with(lambda r: pytest.fail("cancelled query should not run"))
    start = datetime.now(timezone.utc)
    assert client.search(1, start, start+timedelta(days=1), cancel) == []


def test_legacy_local_z_time_roundtrip_does_not_shift_eight_hours():
    client = client_with(lambda r: httpx.Response(200, content=b"<unused/>"))
    client.recording_timezone = timezone(timedelta(hours=8))
    real = parse_time("2026-09-30T13:30:00+08:00")
    assert client.encode_search_time(real) == "2026-09-30T13:30:00Z"
    assert client.decode_recording_time("2026-09-30T13:30:00Z") == real
    from hikvision_console.models import Recording
    recording = Recording(1, "101", real, real+timedelta(hours=1),
                          "rtsp://nvr/Streaming/tracks/101?starttime=old")
    assert "20260930T133000Z" in client.playback_url(recording, real)
