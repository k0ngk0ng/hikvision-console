from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


class StreamQuality(str, Enum):
    SUB = "sub"
    MAIN = "main"


@dataclass(frozen=True)
class Connection:
    host: str
    username: str = "admin"
    password: str = field(default="", repr=False)
    http_port: int = 80
    rtsp_port: int = 554
    https: bool = False
    verify_tls: bool = True

    def __post_init__(self):
        if not self.host or any(c in self.host for c in "/@?#\\ \r\n"):
            raise ValueError("请输入主机名或 IP，不包含协议、路径和账号")
        if ":" in self.host or self.host.startswith("["):
            try:
                ipaddress.IPv6Address(self.host.strip("[]"))
            except ValueError:
                raise ValueError("无效的 IPv6 地址；端口请填写在单独的端口栏") from None
        if not (1 <= self.http_port <= 65535 and 1 <= self.rtsp_port <= 65535):
            raise ValueError("端口必须在 1–65535 之间")

    @property
    def authority(self):
        return f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host

    @property
    def base_url(self):
        return f"{'https' if self.https else 'http'}://{self.authority}:{self.http_port}"

    def rtsp_url(self, stream_id: str):
        if not stream_id.isdigit():
            raise ValueError("无效的码流编号")
        auth = f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
        return f"rtsp://{auth}{self.authority}:{self.rtsp_port}/Streaming/Channels/{stream_id}"

    def playback_url(self, uri: str, start: datetime | None = None, end: datetime | None = None,
                     recording_timezone=None):
        parsed = urlsplit(uri)
        # Use the configured reachable endpoint, never blindly follow an NVR-supplied host.
        if parsed.scheme.lower() != "rtsp" or not parsed.path.startswith("/"):
            raise ValueError("NVR 返回了无效的录像地址")
        query = dict(parse_qsl(parsed.query))
        def encode(value):
            if recording_timezone is not None:
                return value.astimezone(recording_timezone).strftime("%Y%m%dT%H%M%SZ")
            return utc_compact(value)
        if start:
            query["starttime"] = encode(start)
        if end:
            query["endtime"] = encode(end)
        auth = f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
        return urlunsplit(("rtsp", f"{auth}{self.authority}:{self.rtsp_port}", parsed.path,
                           urlencode(query), ""))


def utc_compact(value: datetime):
    if value.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_iso(value: datetime):
    if value.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("设备时间缺少时区")
    return result


@dataclass(frozen=True)
class Stream:
    id: str
    codec: str = ""
    width: int = 0
    height: int = 0
    bitrate_kbps: int = 0
    fps: float = 0


@dataclass
class Channel:
    id: int
    name: str
    online: bool = False
    configured: bool = False
    status: str = "未配置"
    streams: dict[str, Stream] = field(default_factory=dict)
    ptz: bool = False

    def stream(self, quality: StreamQuality | str) -> Stream | None:
        key = quality.value if isinstance(quality, StreamQuality) else quality
        return self.streams.get(key) or self.streams.get("main") or next(iter(self.streams.values()), None)


@dataclass(frozen=True)
class Recording:
    channel_id: int
    track_id: str
    start: datetime
    end: datetime
    uri: str = field(repr=False)
    kind: str = "录像"
    size: int = 0

    def contains(self, when: datetime):
        return self.start <= when < self.end


@dataclass
class Device:
    model: str
    firmware: str
    channels: list[Channel]
    time: datetime
    capabilities: dict[str, str] = field(default_factory=dict)
    disks: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    recording_time_mode: str = "utc"


@dataclass(frozen=True)
class NetworkProfile:
    name: str
    cache_ms: int
    stall_seconds: int
    budget_kbps: int
    max_live: int


PROFILES = {
    "balanced": NetworkProfile("均衡", 900, 15, 8000, 16),
    "low_latency": NetworkProfile("低延迟", 300, 10, 12000, 16),
    "smooth": NetworkProfile("流畅优先", 1800, 22, 6000, 16),
    "economy": NetworkProfile("节省带宽", 1400, 20, 2500, 4),
}


def plan_streams(channels: list[Channel], requested: dict[int, str], budget: int, limit: int,
                 focused: int | None = None) -> tuple[dict[int, str], list[int]]:
    """Explicitly defer streams when the source's advertised bandwidth exceeds budget."""
    chosen: dict[int, str] = {}
    deferred = []
    used = 0
    candidates = [c for c in channels if c.online and c.id in requested]
    candidates.sort(key=lambda c: (c.id != focused, list(requested).index(c.id)))
    for c in candidates:
        quality = requested[c.id]
        if quality not in c.streams:
            quality = "main" if "main" in c.streams else next(iter(c.streams), quality)
        stream = c.stream(quality)
        if not stream:
            deferred.append(c.id)
            continue
        if used + (stream.bitrate_kbps or 1024) > budget and quality == "main" and "sub" in c.streams:
            quality = "sub"
            stream = c.stream(quality)
        cost = stream.bitrate_kbps or 1024
        if len(chosen) >= limit or used + cost > budget:
            deferred.append(c.id)
        else:
            chosen[c.id] = quality
            used += cost
    return chosen, deferred
