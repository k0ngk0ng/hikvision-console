"""Build on the target OS. macOS/Windows bundles VLC; Linux uses system libVLC."""
import os
import shutil
import struct
import sys
from pathlib import Path

import imageio_ffmpeg

root = Path(SPECPATH).parent
ffmpeg = shutil.which(imageio_ffmpeg.get_ffmpeg_exe())
if not ffmpeg:
    raise RuntimeError("Install FFmpeg for the target architecture before packaging")
if sys.platform == "win32":
    # Keep the upstream environment untouched. Our bundled copy is a GUI-subsystem
    # executable, so even QProcess or a launcher without flags cannot allocate a
    # console window. Its entry point and redirected stdin/stdout remain unchanged.
    target = root / "build/windows-media/ffmpeg.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ffmpeg, target)
    data = bytearray(target.read_bytes())
    assert data[:2] == b"MZ"
    pe = struct.unpack_from("<I", data, 0x3c)[0]
    assert data[pe:pe+4] == b"PE\0\0"
    optional = pe + 24
    assert struct.unpack_from("<H", data, optional)[0] in (0x10b, 0x20b)
    struct.pack_into("<H", data, optional + 68, 2)  # IMAGE_SUBSYSTEM_WINDOWS_GUI
    struct.pack_into("<I", data, optional + 64, 0)  # checksum not required for user executables
    target.write_bytes(data)
    ffmpeg = str(target)
binaries = [(ffmpeg, "." if sys.platform == "win32" else "imageio_ffmpeg/binaries")]
datas = [(str(root / "README.md"), "."), (str(root / "LICENSE"), "."),
         (str(root / "THIRD_PARTY.md"), "."), (str(root / "assets/icon.svg"), "assets"),
         (str(root / "assets/icon.ico"), "assets")]
if sys.platform == "darwin":
    vlc_root = Path(os.environ.get("HIKVISION_VLC_DIR", "/Applications/VLC.app/Contents/MacOS"))
    if not (vlc_root / "lib/libvlc.dylib").exists():
        raise RuntimeError("Install a VLC 3.x build matching the Python architecture before packaging")
    for folder in ("lib", "plugins"):
        for item in (vlc_root / folder).glob("*.dylib"):
            if not item.is_symlink() and item.name not in ("libmacosx_plugin.dylib", "libosx_notifications_plugin.dylib"):
                binaries.append((str(item), f"vlc/{folder}"))
    # Preserve the unversioned names loaded explicitly by python-vlc.
    for item in ("libvlc.dylib", "libvlccore.dylib"):
        binaries.append((str(vlc_root / "lib" / item), "vlc/lib"))
elif sys.platform == "win32":
    vlc_root = Path(os.environ.get("HIKVISION_VLC_DIR", "C:/Program Files/VideoLAN/VLC"))
    if not (vlc_root / "libvlc.dll").exists():
        raise RuntimeError("Install 64-bit VLC 3.x before packaging")
    for item in vlc_root.rglob("*.dll"):
        binaries.append((str(item), str(Path("vlc") / item.parent.relative_to(vlc_root))))

a = Analysis([str(root / "packaging/launcher.py")], pathex=[str(root / "src")], binaries=binaries,
             datas=datas, hiddenimports=["vlc", "imageio_ffmpeg"],
             excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
                       "PySide6.QtQuick", "PySide6.QtMultimedia", "numpy", "pytest"])
if sys.platform == "win32":
    # imageio's hook may also collect its console build. Ship only our explicit
    # root-level executable, which ffmpeg_binary() selects without probing.
    def original_ffmpeg(item):
        name = item[0].replace("\\", "/")
        return name.startswith("imageio_ffmpeg/binaries/") and name.lower().endswith(".exe")
    a.binaries = [item for item in a.binaries if not original_ffmpeg(item)]
    a.datas = [item for item in a.datas if not original_ffmpeg(item)]
if sys.platform.startswith("linux"):
    # python-vlc's ctypes hook collects the core libraries without their plugins.
    # Linux deliberately uses the installed VLC, so keep its core and plugins together.
    a.binaries = [item for item in a.binaries
                  if not Path(item[0]).name.startswith(("libvlc.so", "libvlccore.so"))]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="HikvisionConsole",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          icon=str(root / "assets/icon.ico") if sys.platform == "win32" else None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="HikvisionConsole")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="Hikvision Console.app", bundle_identifier="org.hikvisionconsole.desktop",
                 icon=str(root / "assets/icon.icns"),
                 info_plist={"NSHighResolutionCapable": True,
                             "NSLocalNetworkUsageDescription": "Connect to the NVR you configure for live monitoring and recording playback.",
                             "CFBundleShortVersionString": "0.1.3"})
