"""Export a six-second real NVR clip into ignored artifacts/private and verify metadata."""
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer

from hikvision_console.api import NvrClient
from hikvision_console.exports import ExportJob, ExportManager
from hikvision_console.models import Connection

client = NvrClient(Connection("192.168.1.100", password=Path(".nvrpass").read_text().strip()))
device = client.discover()
target = datetime.now(timezone.utc)-timedelta(minutes=40)
record = client.search(1, target, target+timedelta(minutes=1))[0]
app = QCoreApplication([])
manager = ExportManager()
path = Path("artifacts/private") / f"export-test-{datetime.now():%H%M%S}.mp4"
job = ExportJob(1, target, target+timedelta(seconds=6), client.playback_url(record, target, record.end), path.resolve())


def changed():
    if job.state in ("完成", "失败", "已取消"):
        print(json.dumps({"state": job.state, "progress": job.progress, "error": job.error,
                          "file": str(path), "size": path.stat().st_size if path.exists() else 0}, ensure_ascii=False))
        app.quit()


manager.changed.connect(changed)
QTimer.singleShot(0, lambda: manager.add([job]))
QTimer.singleShot(65000, lambda: (manager.shutdown(), app.quit()))
app.exec()
client.close()
if job.state != "完成":
    raise SystemExit(1)
result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,width,height",
                         "-of", "json", str(path)], capture_output=True, text=True, check=True)
print(result.stdout)
