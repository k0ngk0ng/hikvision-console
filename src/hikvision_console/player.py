from __future__ import annotations

import ctypes
import os
import random
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer, Signal

from .models import PROFILES

_vlc = None
_instance = None
_retiring: list[threading.Thread] = []
_dll_directory_handles = []


def load_vlc():
    global _vlc, _instance
    if _instance is not None:
        return _vlc, _instance
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "vlc"
    supplied = os.environ.get("HIKVISION_VLC_DIR")
    candidates = [Path(supplied)] if supplied else []
    candidates += [bundle]
    if sys.platform == "darwin":
        candidates += [Path("/Applications/VLC.app/Contents/MacOS")]
        for root in candidates:
            lib = root / "lib" / "libvlc.dylib"
            if lib.exists():
                ctypes.CDLL(str(root / "lib" / "libvlccore.dylib"), mode=ctypes.RTLD_GLOBAL)
                os.environ["PYTHON_VLC_LIB_PATH"] = str(lib)
                os.environ["VLC_PLUGIN_PATH"] = str(root / "plugins")
                break
    elif sys.platform == "win32":
        candidates += [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "VideoLAN/VLC"]
        for root in candidates:
            if (root / "libvlc.dll").exists():
                os.environ["PYTHON_VLC_LIB_PATH"] = str(root / "libvlc.dll")
                os.environ["VLC_PLUGIN_PATH"] = str(root / "plugins")
                _dll_directory_handles.append(os.add_dll_directory(str(root)))
                break
    import vlc
    _vlc = vlc
    try:
        _instance = vlc.Instance("--ignore-config", "--no-media-library",
                                 "--no-plugins-cache", "--no-video-title-show", "--no-osd",
                                 "--no-snapshot-preview", "--keystore=memory",
                                 "--verbose=2" if os.environ.get("HIKVISION_VLC_DEBUG") == "1" else "--quiet",
                                 "--no-lua", "--stats", "--intf=dummy")
    except Exception:
        raise RuntimeError("未找到可用的 VLC 播放库，请安装与应用架构一致的 VLC 3.x") from None
    if _instance is None:
        raise RuntimeError("VLC 播放库初始化失败")
    return vlc, _instance


def retire(player, media):
    if not player:
        return

    def cleanup():
        try:
            player.stop()
            player.release()
            if media:
                media.release()
        except Exception:
            pass

    thread = threading.Thread(target=cleanup, daemon=True, name="vlc-stop")
    _retiring[:] = [t for t in _retiring if t.is_alive()]
    _retiring.append(thread)
    thread.start()
    return thread


class Player(QObject):
    status = Signal(str)
    metrics = Signal(dict)
    ended = Signal()
    playing = Signal()
    failed = Signal(str)

    def __init__(self, surface, parent=None):
        super().__init__(parent)
        self.surface = surface
        self.player = None
        self.media = None
        self.bridge = None
        self.url = ""
        self.profile = "balanced"
        self.hardware = True
        self.transport = "tcp"
        self.live = True
        self.want_play = False
        self.paused = False
        self.retry_count = 0
        self.failure_streak = 0
        self.generation = 0
        self.last_frame = 0
        self.last_bytes = 0
        self.last_activity = time.monotonic()
        self.started_at = 0.0
        self.last_tick = time.monotonic()
        self.clock_anchor = None
        self.has_played = False
        self.muted = True
        self.speed = 1.0
        self.zoom = 1.0
        self.fill_surface = False
        self.retiring = []
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.surface.installEventFilter(self)

    def set_fill_surface(self, enabled):
        self.fill_surface = enabled
        self.apply_surface_aspect()

    def apply_surface_aspect(self):
        if self.player:
            ratio = f"{self.surface.width()}:{self.surface.height()}" if self.fill_surface else None
            self.player.video_set_aspect_ratio(ratio)

    def eventFilter(self, watched, event):
        if watched is self.surface and event.type() == QEvent.Type.Resize and self.fill_surface:
            self.apply_surface_aspect()
        return super().eventFilter(watched, event)

    def start(self, url, *, live=True, profile="balanced", hardware=True, transport="tcp"):
        self.stop()
        self.url, self.live, self.profile = url, live, profile
        self.hardware, self.transport = hardware, transport
        self.want_play = True
        self.paused = False
        self.retry_count = 0
        self.failure_streak = 0
        try:
            if url.startswith("rtsp://"):
                from .media_bridge import StreamBridge
                self.bridge = StreamBridge(url, 1 if live else self.speed, transport,
                                           include_audio=not live or not self.muted)
            self._open()
        except (RuntimeError, OSError, ImportError, SystemExit):
            self.want_play = False
            if self.bridge:
                self.bridge.stop()
            self.status.emit("播放组件不可用 · 请检查 VLC 3.x、FFmpeg 及本地端口权限")

    def _open(self):
        if not self.want_play:
            return
        if self.bridge and self.bridge.cancelled.is_set():
            from .media_bridge import StreamBridge
            self.bridge = StreamBridge(self.url, 1 if self.live else self.speed, self.transport,
                                       include_audio=not self.live or not self.muted)
        vlc, instance = load_vlc()
        profile = PROFILES[self.profile]
        self.media = instance.media_new(self.bridge.url if self.bridge else self.url)
        for option in (f":network-caching={profile.cache_ms}", ":clock-jitter=0", ":no-video-title-show",
                       ":no-sout-all", ":rtsp-timeout=10", ":no-input-repeat", ":codec=avcodec",
                       f":avcodec-hw={'any' if self.hardware else 'none'}"):
            self.media.add_option(option)
        if self.transport == "tcp":
            self.media.add_option(":rtsp-tcp")
        self.player = instance.media_player_new()
        self.player.set_media(self.media)
        handle = int(self.surface.winId())
        if sys.platform == "darwin":
            self.player.set_nsobject(handle)
        elif sys.platform == "win32":
            self.player.set_hwnd(handle)
        else:
            self.player.set_xwindow(handle)
        self.player.video_set_mouse_input(False)
        self.player.video_set_key_input(False)
        self.apply_surface_aspect()
        self.player.audio_set_mute(self.muted)
        self.last_frame = self.last_bytes = 0
        self.last_tick = self.last_activity = self.started_at = time.monotonic()
        self.clock_anchor = None
        self.has_played = False
        if self.player.play() == -1:
            self._retry("无法启动播放")
            return
        self.status.emit("连接中 · 等待视频关键帧")
        self.timer.start()

    def stop(self):
        self.generation += 1
        self.want_play = False
        self.timer.stop()
        player, media = self.player, self.media
        self.player = self.media = None
        self.url = ""
        if self.bridge:
            self.bridge.stop()
            self.bridge = None
        thread = retire(player, media)
        if thread:
            self.retiring = [t for t in self.retiring if t.is_alive()] + [thread]
        self.status.emit("已停止")

    def _retry(self, reason, *, refresh=False):
        self.timer.stop()
        player, media = self.player, self.media
        self.player = self.media = None
        thread = retire(player, media)
        if thread:
            self.retiring = [t for t in self.retiring if t.is_alive()] + [thread]
        if not self.want_play:
            return
        if self.bridge:
            self.bridge.stop()
        if not self.live:
            self.status.emit(f"{reason} · 请重新定位或重试")
            self.want_play = False
            self.failed.emit(reason)
            return
        self.retry_count += 1
        if not refresh:
            self.failure_streak += 1
        if not refresh and self.hardware and self.failure_streak >= 2:
            self.hardware = False
            reason += " · 已回退软件解码"
        delay = (1 if refresh else min(30, 2 ** min(self.failure_streak, 5))) + random.uniform(0.1, 1)
        self.status.emit(f"{reason} · {delay:.0f} 秒后重连（{self.retry_count}）")
        generation = self.generation
        QTimer.singleShot(int(delay * 1000), lambda: self._open()
                          if self.want_play and generation == self.generation else None)

    def _tick(self):
        if not self.player or not self.media:
            return
        vlc, _ = load_vlc()
        state = self.player.get_state()
        now = time.monotonic()
        if self.bridge and self.bridge.failure_reason:
            self._retry(self.bridge.failure_reason)
            return
        if self.bridge and self.bridge.proxy and self.bridge.proxy.play_status:
            status = self.bridge.proxy.play_status
            if status >= 400 and status != 401:
                self._retry(f"设备拒绝此倍速（RTSP {status}），请选择 1×")
                return
            actual = self.bridge.proxy.accepted_scale
            if actual is not None and abs(actual-self.speed) > 0.01:
                self._retry(f"设备仅接受 {actual:g}×，请选择受支持的倍速")
                return
        if state == vlc.State.Error:
            self._retry("视频连接或解码失败")
            return
        if state == vlc.State.Ended:
            if self.live:
                self._retry("码流结束")
            else:
                if self.bridge and self.bridge.duration and self.player.get_time()/1000 < self.bridge.duration-2:
                    self._retry("录像传输提前结束")
                    return
                self.timer.stop()
                self.want_play = False
                self.status.emit("片段播放完成")
                self.ended.emit()
            return
        stats = vlc.MediaStats()
        self.media.get_stats(stats)
        frames, byte_count = stats.displayed_pictures, max(stats.read_bytes, stats.demux_read_bytes)
        if frames > self.last_frame:
            self.last_activity = now
            if now - self.started_at > 30:
                self.failure_streak = 0
            if not self.has_played:
                self.has_played = True
                self.playing.emit()
                self.player.audio_set_mute(self.muted)
                self.set_zoom(self.zoom)
                self.apply_surface_aspect()
                if self.speed != 1:
                    self.player.set_rate(self.speed)
            self.status.emit("实时播放" if self.live else ("已暂停" if self.paused else "录像回放"))
        elapsed = max(0.1, now - self.last_tick)
        position_ms = self.player.get_time()
        backlog = 0.0
        if self.live and self.has_played and position_ms > 0:
            if self.clock_anchor is None:
                self.clock_anchor = (now, position_ms)
            backlog = max(0, (now-self.clock_anchor[0])-(position_ms-self.clock_anchor[1])/1000)
        self.metrics.emit({"fps": max(0, frames - self.last_frame) / elapsed,
                           "kbps": max(0, byte_count - self.last_bytes) * 8 / elapsed / 1000,
                           "frames": frames, "lost": stats.lost_pictures,
                           "stale_seconds": now - self.last_activity,
                           "position_ms": position_ms, "retries": self.retry_count,
                           "estimated_backlog_s": backlog,
                           "hardware_requested": self.hardware})
        self.last_frame, self.last_bytes, self.last_tick = frames, byte_count, now
        if self.paused:
            self.last_activity = now
            return
        if self.live and backlog > PROFILES[self.profile].stall_seconds:
            self._retry("实时画面持续落后 · 重新获取当前画面", refresh=True)
            return
        threshold = PROFILES[self.profile].stall_seconds if self.has_played else 40
        if now - self.last_activity > threshold:
            self._retry("视频长时间没有更新")
        elif self.has_played and now - self.last_activity > 3:
            self.status.emit(f"画面未更新 {now - self.last_activity:.0f} 秒")

    def pause(self, value):
        self.paused = value
        if self.player:
            self.player.set_pause(int(value))
            self.status.emit("已暂停" if value else "录像回放")

    def set_muted(self, muted):
        self.muted = muted
        if self.live and self.want_play and self.bridge and not muted and not self.bridge.include_audio:
            self.start(self.url, live=True, profile=self.profile, hardware=self.hardware, transport=self.transport)
            self.status.emit("正在启用此通道音频…")
            return
        if self.player:
            self.player.audio_set_mute(muted)

    def set_rate(self, rate):
        self.speed = rate
        if self.player:
            return self.player.set_rate(rate) == 0
        return True

    def screenshot(self, path: Path):
        return bool(self.player and self.player.video_take_snapshot(0, str(path), 0, 0) == 0)

    def set_zoom(self, value):
        self.zoom = value
        if self.player:
            size = self.player.video_get_size(0)
            if not size or not size[0] or not size[1]:
                return
            w, h = size
            if value <= 1:
                self.player.video_set_crop_geometry(None)
            else:
                cw, ch = int(w / value), int(h / value)
                self.player.video_set_crop_geometry(f"{cw}x{ch}+{(w-cw)//2}+{(h-ch)//2}")


def shutdown_vlc():
    # All surfaces are stopped by their owners; avoid destroying a library in use by teardown threads.
    for thread in list(_retiring):
        thread.join(timeout=2)
