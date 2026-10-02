"""Local-only video-copy remux with isolated, restartable RTSP ingestion.

The NVR triggers live555 interleaved RTCP parser errors during long sessions.
FFmpeg handles RTSP in a separate process; libVLC renders a local MPEG-TS stream.
This also provides a playback clock without video transcoding or recording to disk.
"""
from __future__ import annotations

import re
import subprocess
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .desktop import subprocess_options
from .exports import ffmpeg_binary
from .media_source import rtsp_input


class StreamBridge:
    def __init__(self, source_url: str, rate=1, transport="tcp", include_audio=True):
        self.source_url = source_url
        self.transport = transport
        self.include_audio = include_audio
        self.binary = ffmpeg_binary()
        self.process = None
        self.cancelled = threading.Event()
        self.token = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.exit_code = None
        self.failure_reason = ""
        self.duration = None
        from .rtsp_proxy import RtspScaleProxy
        self.proxy = RtspScaleProxy(source_url, rate) if rate != 1 else None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.path != f"/{owner.token}.ts" or owner.cancelled.is_set():
                    self.send_error(404)
                    return
                with owner.lock:
                    if owner.cancelled.is_set():
                        self.send_error(410)
                        return
                    if owner.process is not None:
                        self.send_error(409)
                        return
                    try:
                        owner.process = subprocess.Popen(owner.command(), stdout=subprocess.PIPE,
                                                          stderr=subprocess.PIPE, stdin=subprocess.PIPE,
                                                          **subprocess_options())
                        threading.Thread(target=owner.read_errors, daemon=True, name="ingest-status").start()
                        _, payload = rtsp_input(owner.proxy.url if owner.proxy else owner.source_url,
                                                transport="tcp" if owner.proxy else owner.transport,
                                                include_audio=owner.include_audio)
                        owner.process.stdin.write(payload)
                        owner.process.stdin.close()
                    except OSError:
                        self.send_error(503)
                        return
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    while not owner.cancelled.is_set():
                        chunk = owner.process.stdout.read(188 * 32)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    owner.terminate_process()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.2), daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/{self.token}.ts"

    def read_errors(self):
        # Keep only a numeric RTSP status, never a raw line that may contain credentials.
        with self.process.stderr as stream:
            for line in iter(lambda: stream.readline(4096), b""):
                match = re.search(rb"method \w+ failed: (\d{3})", line)
                if match:
                    status = int(match[1])
                    if status == 453:
                        self.failure_reason = "设备拒绝新码流：资源或带宽不足（RTSP 453）"
                    elif status in (401, 403):
                        self.failure_reason = f"设备拒绝视频认证（RTSP {status}）"
                    else:
                        self.failure_reason = f"设备拒绝视频请求（RTSP {status}）"

    def command(self):
        input_args, _ = rtsp_input(self.proxy.url if self.proxy else self.source_url,
                                   transport="tcp" if self.proxy else self.transport,
                                   include_audio=self.include_audio)
        cmd = [self.binary, "-hide_banner", "-nostdin", "-loglevel", "error",
               "-fflags", "+genpts+discardcorrupt", "-analyzeduration", "500000", "-probesize", "500000"] + input_args
        query = parse_qs(urlsplit(self.source_url).query)
        try:
            start = datetime.strptime(query["starttime"][0], "%Y%m%dT%H%M%SZ")
            end = datetime.strptime(query["endtime"][0], "%Y%m%dT%H%M%SZ")
            duration = (end-start).total_seconds()
            if duration > 0:
                self.duration = duration
                cmd += ["-t", str(duration)]
        except (KeyError, ValueError):
            pass
        audio = ["-map", "0:a?", "-c:a", "aac", "-b:a", "96k", "-af", "aresample=async=1:first_pts=0"] if self.include_audio else ["-an"]
        return cmd + ["-map", "0:v:0", "-c:v", "copy"] + audio + ["-max_interleave_delta", "1000000",
                      "-avoid_negative_ts", "make_zero", "-muxdelay", "0", "-muxpreload", "0",
                      "-mpegts_flags", "+resend_headers", "-f", "mpegts", "pipe:1"]

    def terminate_process(self):
        with self.lock:
            if self.process:
                if self.process.poll() is None:
                    if not self.cancelled.is_set():
                        try:
                            self.process.wait(timeout=0.3)
                        except subprocess.TimeoutExpired:
                            self.process.kill()
                    else:
                        # FFmpeg handles SIGTERM by sending RTSP TEARDOWN. An immediate
                        # kill can leave scarce NVR sessions occupied until their timeout.
                        self.process.terminate()
                        try:
                            self.process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            self.process.kill()
                try:
                    self.exit_code = self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                if self.process.stdout:
                    self.process.stdout.close()

    def stop(self):
        self.cancelled.set()

        def cleanup():
            self.terminate_process()
            if self.proxy:
                self.proxy.close()
            self.server.shutdown()
            self.server.server_close()
        threading.Thread(target=cleanup, daemon=True, name="recording-stop").start()
