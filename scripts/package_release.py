"""Archive tested desktop builds without losing executable bits or macOS symlinks."""
import hashlib
import json
import platform
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
dist = root / "dist"
system, arch = platform.system(), platform.machine().lower()
name = f"HikvisionConsole-0.1.0-{system}-{arch}"
smoke = json.loads((root / ".tmp/native-ci/self-test/native-smoke.json").read_text())
if not smoke.get("passed"):
    raise SystemExit("Run scripts/native_ci.py --frozen successfully before packaging")
manifest = {"version": "0.1.0", "system": system, "architecture": arch,
            "build_os": platform.platform(), "python": platform.python_version(), "native_test": smoke}
if sys.platform == "darwin":
    bundle = dist / "Hikvision Console.app"
    archive = dist / f"{name}.zip"
    subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(bundle), str(archive)], check=True)
elif sys.platform == "win32":
    bundle = dist / "HikvisionConsole"
    archive = dist / f"{name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in bundle.rglob("*"):
            if path.is_file():
                output.write(path, path.relative_to(dist))
else:
    bundle = dist / "HikvisionConsole"
    archive = dist / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(bundle, arcname=bundle.name)
digest = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
manifest["archive"] = archive.name
manifest["sha256"] = digest
(dist / f"{name}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
(dist / f"{name}.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
print(archive.name)
