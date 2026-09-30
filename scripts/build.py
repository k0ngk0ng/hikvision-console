"""Run PyInstaller with every build cache confined to the current repository."""
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
os.chdir(root)
for name in (".cache/pyinstaller", ".tmp", "build", "dist"):
    (root/name).mkdir(parents=True, exist_ok=True)
environment = dict(os.environ)
environment.update(PYINSTALLER_CONFIG_DIR=str(root/".cache/pyinstaller"), TMPDIR=str(root/".tmp"),
                   XDG_CACHE_HOME=str(root/".cache"))
subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(root/"dist"),
                "--workpath", str(root/"build"), str(root/"packaging/hikvision-console.spec")],
               env=environment, check=True)
