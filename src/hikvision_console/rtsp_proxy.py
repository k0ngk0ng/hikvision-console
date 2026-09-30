"""Loopback-only RTSP Scale adapter for FFmpeg's recording input.

FFmpeg does not expose RTSP PLAY's Scale header. Forward the authenticated
control connection and interleaved RTP unchanged, adding only that header.
Digest authentication remains end-to-end; we do not rewrite request targets.
"""
from __future__ import annotations

import select
import socket
import threading
from urllib.parse import urlsplit, urlunsplit


def pop_message(buffer: bytes):
    if not buffer:
        return None, buffer
    if buffer[0] == 36:  # interleaved RTP/RTCP: '$', channel, 16-bit length
        if len(buffer) < 4:
            return None, buffer
        length = 4 + int.from_bytes(buffer[2:4], "big")
        return (buffer[:length], buffer[length:]) if len(buffer) >= length else (None, buffer)
    end = buffer.find(b"\r\n\r\n")
    if end < 0:
        if len(buffer) > 65536:
            raise ValueError("RTSP header exceeds limit")
        return None, buffer
    length = 0
    for line in buffer[:end].split(b"\r\n")[1:]:
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
    if not 0 <= length <= 1024*1024:
        raise ValueError("RTSP body exceeds limit")
    total = end+4+length
    return (buffer[:total], buffer[total:]) if len(buffer) >= total else (None, buffer)


def with_scale(message: bytes, rate: float):
    if not message.startswith(b"PLAY "):
        return message
    head, body = message.split(b"\r\n\r\n", 1)
    lines = [line for line in head.split(b"\r\n") if not line.lower().startswith(b"scale:")]
    lines.append(f"Scale: {rate:g}".encode("ascii"))
    return b"\r\n".join(lines)+b"\r\n\r\n"+body


class RtspScaleProxy:
    def __init__(self, source: str, rate: float):
        if rate not in (0.5, 1, 2, 4, 8):
            raise ValueError("Unsupported playback rate")
        self.rate = rate
        parsed = urlsplit(source)
        self.target = (parsed.hostname, parsed.port or 554)
        self.cancel = threading.Event()
        self.sockets = []
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.2)
        auth = parsed.netloc.rsplit("@", 1)[0]+"@" if "@" in parsed.netloc else ""
        self.url = urlunsplit(("rtsp", f"{auth}127.0.0.1:{self.listener.getsockname()[1]}",
                              parsed.path, parsed.query, ""))
        self.play_status = None
        self.accepted_scale = None
        threading.Thread(target=self.run, daemon=True, name="rtsp-scale").start()

    def run(self):
        try:
            while not self.cancel.is_set():
                try:
                    client, _ = self.listener.accept()
                    break
                except socket.timeout:
                    continue
            else:
                return
            upstream = socket.create_connection(self.target, timeout=5)
            self.sockets = [client, upstream]
            client.settimeout(5)
            upstream.settimeout(5)
            buffers = {client: b"", upstream: b""}
            play_cseqs = set()
            while not self.cancel.is_set():
                ready, _, _ = select.select(self.sockets, [], [], 0.2)
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    buffers[source] += data
                    while True:
                        message, buffers[source] = pop_message(buffers[source])
                        if message is None:
                            break
                        if source is client:
                            if message.startswith(b"PLAY "):
                                for line in message.split(b"\r\n"):
                                    if line.lower().startswith(b"cseq:"):
                                        play_cseqs.add(line.split(b":", 1)[1].strip())
                                message = with_scale(message, self.rate)
                            upstream.sendall(message)
                        else:
                            if message.startswith(b"RTSP/"):
                                lines = message.split(b"\r\n")
                                headers = {line.split(b":", 1)[0].lower(): line.split(b":", 1)[1].strip()
                                           for line in lines[1:] if b":" in line}
                                if headers.get(b"cseq") in play_cseqs:
                                    self.play_status = int(lines[0].split()[1])
                                    if b"scale" in headers:
                                        self.accepted_scale = float(headers[b"scale"])
                            client.sendall(message)
        except (OSError, ValueError):
            pass
        finally:
            self.close()

    def close(self):
        self.cancel.set()
        for sock in self.sockets + [self.listener]:
            try:
                sock.close()
            except OSError:
                pass
