"""Pass authenticated media locations over a pipe, never in child-process arguments."""
from urllib.parse import urlsplit


def rtsp_input(url: str, timeout_us=15000000, transport="tcp", include_audio=True):
    if urlsplit(url).scheme != "rtsp" or any(c in url for c in ("\r", "\n", "\x00")):
        raise ValueError("Invalid RTSP source")
    if transport not in ("tcp", "udp"):
        raise ValueError("Invalid RTSP transport")
    escaped = url.replace("'", "'\\''")
    playlist = (f"ffconcat version 1.0\nfile '{escaped}'\noption rtsp_transport {transport}\n"
                f"option timeout {timeout_us}\noption analyzeduration 1500000\noption probesize 1000000\n")
    if not include_audio:
        playlist += "option allowed_media_types video\n"
    arguments = ["-protocol_whitelist", "file,pipe,rtsp,tcp,udp,rtp", "-f", "concat", "-safe", "0", "-i", "pipe:0"]
    return arguments, playlist.encode("utf-8")
