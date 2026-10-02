"""Desktop integration shared by source and packaged launches."""
import ctypes
import subprocess
import sys
from pathlib import Path

APP_ID = "org.hikvisionconsole.desktop"


def prepare_desktop():
    # Set this before creating any windows so Explorer groups them with our icon,
    # rather than the Python host or a generic executable identity.
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def icon_path():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / "assets" / ("icon.ico" if sys.platform == "win32" else "icon.svg")


def subprocess_options():
    # A windowed PyInstaller parent does not suppress console windows created by
    # its console-subsystem children. Apply this on every start, including retries.
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
