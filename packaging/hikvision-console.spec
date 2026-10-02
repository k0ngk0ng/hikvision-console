"""Build on the target OS. macOS/Windows bundles VLC; Linux uses system libVLC."""
import os
import shutil
import sys
from pathlib import Path

import imageio_ffmpeg

root = Path(SPECPATH).parent
ffmpeg = shutil.which(imageio_ffmpeg.get_ffmpeg_exe())
if not ffmpeg:
    raise RuntimeError("Install FFmpeg for the target architecture before packaging")
binaries = [(ffmpeg, "imageio_ffmpeg/binaries")]
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
                             "CFBundleShortVersionString": "0.1.1"})
