"""Verify a real native renderer, optionally in the target-platform packaged application."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from hikvision_console.exports import ffmpeg_binary

parser = argparse.ArgumentParser()
parser.add_argument("--frozen", action="store_true")
parser.add_argument("--components-only", action="store_true", help="Check components without claiming native video rendering")
parser.add_argument("--verbose", action="store_true", help="Include VLC diagnostics for this synthetic test")
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
directory = root / ".tmp/native-ci"
directory.mkdir(parents=True, exist_ok=True)
video = directory / "synthetic.mp4"
subprocess.run([ffmpeg_binary(), "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "12",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-y", str(video)], check=True)
environment = dict(os.environ)
environment.update(HIKVISION_HOME=str(directory), TMPDIR=str(root/".tmp"), XDG_CACHE_HOME=str(root/".cache"))
if args.verbose:
    environment["HIKVISION_VLC_DEBUG"] = "1"
if args.frozen:
    for key in ("HIKVISION_VLC_DIR", "HIKVISION_FFMPEG", "IMAGEIO_FFMPEG_EXE",
                "PYTHON_VLC_LIB_PATH", "VLC_PLUGIN_PATH"):
        environment.pop(key, None)
    if sys.platform == "darwin":
        program = root / "dist/Hikvision Console.app/Contents/MacOS/HikvisionConsole"
    else:
        program = root / "dist/HikvisionConsole" / ("HikvisionConsole.exe" if sys.platform == "win32" else "HikvisionConsole")
    command = [str(program)]
else:
    command = [sys.executable, "-m", "hikvision_console"]
test_option = "--component-test" if args.components_only else "--self-test"
report_name = "component-smoke.json" if args.components_only else "native-smoke.json"
subprocess.run(command + [test_option, str(video)], env=environment, check=True, timeout=30)
result = json.loads((directory / "self-test" / report_name).read_text())
assert result["passed"], result
if sys.platform == "win32" and args.frozen and not args.components_only:
    subprocess.run(command + ["--interaction-test", str(video)], env=dict(environment, QT_SCALE_FACTOR="1.25"),
                   check=True, timeout=40)
    interaction = json.loads((directory / "self-test/interaction-smoke.json").read_text())
    assert interaction["passed"], interaction
    result["fullscreen_interaction"] = interaction
    (directory / "self-test" / report_name).write_text(json.dumps(result, indent=2))
    print("Windows native fullscreen double-click and restored controls: PASS")
print("Components: PASS; native rendering NOT TESTED" if args.components_only
      else "Native rendering, pause, speed, crop and snapshot: PASS")
