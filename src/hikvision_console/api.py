from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import httpx
from defusedxml.ElementTree import fromstring

from .models import Channel, Connection, Device, Recording, Stream, parse_time, utc_iso


class DeviceError(Exception):
    pass


def redact(value: str, connection: Connection | None = None) -> str:
    value = re.sub(r"(rtsp[s]?://)[^\s/]*@", r"\1[credentials]@", value)
    if connection and connection.password:
        from urllib.parse import quote
        for secret in (connection.password, quote(connection.password, safe="")):
            value = value.replace(secret, "[redacted]")
    return value


def xml_root(content: bytes):
    root = fromstring(content)
    for e in root.iter():
        e.tag = e.tag.rsplit("}", 1)[-1]
    return root


def integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class NvrClient:
    def __init__(self, connection: Connection, transport=None, recording_time_mode="auto"):
        self.connection = connection
        self.time_policy = recording_time_mode
        self.recording_timezone = None
        self.http = httpx.Client(
            base_url=connection.base_url,
            auth=httpx.DigestAuth(connection.username, connection.password),
            timeout=httpx.Timeout(10, connect=5), verify=connection.verify_tls,
            trust_env=False, transport=transport,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )

    def close(self):
        self.http.close()

    def request(self, path: str, body: bytes | None = None, method=None):
        try:
            response = self.http.request(method or ("POST" if body is not None else "GET"), path,
                                         content=body, headers={"Content-Type": "application/xml"})
            if response.status_code in (401, 403):
                raise DeviceError(f"认证失败或设备不允许此接口（{response.status_code}）：{path}")
            response.raise_for_status()
            if len(response.content) > 16 * 1024 * 1024:
                raise DeviceError("设备响应过大")
            root = xml_root(response.content)
            if root.tag == "ResponseStatus" and root.findtext("statusCode") not in (None, "1"):
                raise DeviceError(f"设备拒绝请求：{root.findtext('subStatusCode', 'unknown')}")
            return root
        except DeviceError:
            raise
        except Exception as exc:
            raise DeviceError(redact(f"设备请求失败：{path} · {type(exc).__name__}", self.connection)) from None

    def discover(self) -> Device:
        info = self.request("/ISAPI/System/deviceInfo")
        warnings = []

        def optional(path):
            try:
                return self.request(path)
            except DeviceError as exc:
                warnings.append(str(exc))
                return ET.Element("Unavailable")

        caps = optional("/ISAPI/System/capabilities")
        configured = self.request("/ISAPI/ContentMgmt/InputProxy/channels")
        statuses = self.request("/ISAPI/ContentMgmt/InputProxy/channels/status")
        stream_list = self.request("/ISAPI/Streaming/channels")
        device_time = self.request("/ISAPI/System/time")
        storage = optional("/ISAPI/ContentMgmt/Storage")
        channels: dict[int, Channel] = {}
        count = integer(caps.findtext(".//inputProxyNums"))
        for i in range(1, count + 1):
            channels[i] = Channel(i, f"通道 {i:02d}")
        for item in configured:
            i = integer(item.findtext("id"))
            if i <= 0:
                continue
            c = channels.setdefault(i, Channel(i, f"通道 {i:02d}"))
            c.name = item.findtext("name") or c.name
            c.configured = True
            c.status = "离线"
        for item in statuses:
            i = integer(item.findtext("id"))
            if i <= 0:
                continue
            c = channels.setdefault(i, Channel(i, f"通道 {i:02d}"))
            c.online = item.findtext("online") == "true"
            detail = item.findtext("chanDetectResult", "")
            c.status = "在线" if c.online else ("未接入" if detail == "notExist" else detail or "离线")
        for item in stream_list:
            sid = item.findtext("id", "")
            if not sid.isdigit():
                continue
            i = integer(item.findtext(".//dynVideoInputChannelID")) or int(sid) // 100
            c = channels.setdefault(i, Channel(i, f"通道 {i:02d}"))
            suffix = int(sid) % 100
            quality = {1: "main", 2: "sub"}.get(suffix, f"stream{suffix}")
            c.streams[quality] = Stream(sid, item.findtext(".//videoCodecType", ""),
                                        integer(item.findtext(".//videoResolutionWidth")),
                                        integer(item.findtext(".//videoResolutionHeight")),
                                        integer(item.findtext(".//constantBitRate")),
                                        integer(item.findtext(".//maxFrameRate")) / 100)
        time_text = device_time.findtext("localTime") or device_time.findtext("UTCTime")
        if not time_text:
            raise DeviceError("设备未返回时区时间，无法可靠定位录像")
        disks = [{k: h.findtext(k, "") for k in ("id", "status", "capacity", "freeSpace")}
                 for h in storage.findall(".//hdd")]
        clock = parse_time(time_text)
        self.recording_timezone = clock.tzinfo if self.time_policy == "local" else None
        if self.time_policy == "auto" and clock.utcoffset() > timedelta(minutes=30):
            # Some older firmware labels local wall times with Z in BOTH search and RTSP.
            # Probe for a recording in the apparent future, ending near the device's wall clock.
            # A mere capability flag claiming UTC support is not sufficient on these devices.
            candidate = next((c.id for c in channels.values() if c.online), None)
            virtual_now = clock.replace(tzinfo=timezone.utc)
            if candidate:
                try:
                    samples = self.search(candidate, virtual_now-timedelta(minutes=15), virtual_now)
                    if any(r.start > clock.astimezone(timezone.utc)+timedelta(minutes=10)
                           and abs((virtual_now-r.end).total_seconds()) < 900 for r in samples):
                        self.recording_timezone = clock.tzinfo
                except DeviceError:
                    pass  # Unknown firmware: keep standards-compliant UTC; allow explicit override.
        return Device(info.findtext("model", "NVR"), info.findtext("firmwareVersion", ""),
                      sorted(channels.values(), key=lambda c: c.id), clock,
                      {e.tag: (e.text or "").strip() for e in caps.iter() if len(e) == 0},
                      disks, warnings, "local" if self.recording_timezone else "utc")

    def channel_status(self):
        root = self.request("/ISAPI/ContentMgmt/InputProxy/channels/status")
        return {integer(e.findtext("id")): e.findtext("online") == "true" for e in root}

    def search(self, channel_id: int, start: datetime, end: datetime, cancel=None) -> list[Recording]:
        if end <= start:
            raise ValueError("结束时间必须晚于开始时间")
        track = str(channel_id * 100 + 1)
        search_id = str(uuid.uuid4())
        position = 0
        found: dict[tuple, Recording] = {}
        for _ in range(2000):
            if cancel and cancel.is_set():
                return []
            root = ET.Element("CMSearchDescription")
            ET.SubElement(root, "searchID").text = search_id
            ET.SubElement(ET.SubElement(root, "trackList"), "trackID").text = track
            span = ET.SubElement(ET.SubElement(root, "timeSpanList"), "timeSpan")
            ET.SubElement(span, "startTime").text = self.encode_search_time(start)
            ET.SubElement(span, "endTime").text = self.encode_search_time(end)
            ET.SubElement(root, "maxResults").text = "100"
            # Device firmware uses this misspelled element in its public protocol.
            ET.SubElement(root, "searchResultPostion").text = str(position)
            ET.SubElement(ET.SubElement(root, "metadataList"), "metadataDescriptor").text = (
                "//recordType.meta.std-cgi.com")
            result = self.request("/ISAPI/ContentMgmt/search", ET.tostring(root))
            status = result.findtext("responseStatusStrg", "").upper()
            if result.findtext("responseStatus", "true").lower() == "false":
                if status in ("NO MATCHES", "NO_MATCHES"):
                    break
                raise DeviceError(f"录像检索失败：{status}")
            items = result.findall(".//searchMatchItem")
            before = len(found)
            for item in items:
                uri = item.findtext(".//playbackURI", "")
                s, e = item.findtext(".//startTime"), item.findtext(".//endTime")
                if not uri or not s or not e:
                    continue
                begin, finish = self.decode_recording_time(s), self.decode_recording_time(e)
                if finish <= start or begin >= end:
                    continue
                actual_track = item.findtext("trackID", track)
                # Reject mismatched records instead of playing an unexpected camera.
                if actual_track != track:
                    continue
                found[(begin, finish, uri)] = Recording(
                    channel_id, actual_track, begin, finish, uri,
                    item.findtext(".//metadataDescriptor", "录像").rsplit("/", 1)[-1],
                    integer(item.findtext(".//size")))
            if status != "MORE":
                break
            if not items or len(found) == before:
                raise DeviceError("设备录像分页未前进，请缩小查询时间范围")
            position += len(items)
        else:
            raise DeviceError("录像条目过多，请缩小查询范围")
        return sorted(found.values(), key=lambda r: r.start)

    def encode_search_time(self, value):
        if self.recording_timezone is not None:
            return value.astimezone(self.recording_timezone).strftime("%Y-%m-%dT%H:%M:%SZ")
        return utc_iso(value)

    def decode_recording_time(self, value):
        parsed = parse_time(value)
        return parsed.replace(tzinfo=self.recording_timezone) if self.recording_timezone is not None else parsed

    def playback_url(self, recording, start=None, end=None):
        return self.connection.playback_url(recording.uri, start, end, self.recording_timezone)

    def ptz_supported(self, channel: int):
        try:
            root = self.request(f"/ISAPI/PTZCtrl/channels/{channel}/capabilities")
            return root.tag not in ("ResponseStatus", "Unavailable") and any(
                "pan" in e.tag.lower() or "tilt" in e.tag.lower() for e in root.iter())
        except DeviceError:
            return False

    def ptz(self, channel: int, pan=0, tilt=0, zoom=0):
        root = ET.Element("PTZData")
        for name, value in (("pan", pan), ("tilt", tilt), ("zoom", zoom)):
            ET.SubElement(root, name).text = str(max(-100, min(100, int(value))))
        self.request(f"/ISAPI/PTZCtrl/channels/{channel}/continuous", ET.tostring(root), "PUT")

    def iter_events(self, cancel):
        """Parse XML blocks from Hikvision's multipart alert stream, with bounded buffering."""
        try:
            with self.http.stream("GET", "/ISAPI/Event/notification/alertStream",
                                  timeout=httpx.Timeout(35, connect=5)) as response:
                response.raise_for_status()
                buffer = b""
                for chunk in response.iter_bytes():
                    if cancel.is_set():
                        return
                    buffer += chunk
                    if len(buffer) > 2 * 1024 * 1024:
                        buffer = buffer[-65536:]
                    end_tag = b"</EventNotificationAlert>"
                    while end_tag in buffer:
                        end = buffer.index(end_tag) + len(end_tag)
                        begin = buffer.find(b"<EventNotificationAlert")
                        block, buffer = buffer[begin:end], buffer[end:]
                        if begin < 0:
                            continue
                        try:
                            root = xml_root(block)
                            data = {k: root.findtext(k, "") for k in
                                    ("eventType", "eventState", "channelID", "dateTime", "eventDescription")}
                            data["channelID"] = root.findtext("dynChannelID") or data["channelID"]
                            if data["eventType"] and data["eventType"].lower() != "heartbeat":
                                yield data
                        except ET.ParseError:
                            continue
        except Exception as exc:
            if not cancel.is_set():
                raise DeviceError(f"事件订阅中断：{type(exc).__name__}") from None


def clean_uri(uri):
    parsed = urlsplit(uri)
    host = parsed.hostname or ""
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, ""))
