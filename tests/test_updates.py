import hashlib
import io
import os
import platform
import subprocess
import sys
import threading
import time
import zipfile

import httpx
import pytest

from hikvision_console import updates
from hikvision_console.desktop import subprocess_options


def release_data():
    stem = "HikvisionConsole-0.2.0-Windows-amd64"
    base = f"https://github.com/{updates.REPOSITORY}/releases/download/v0.2.0/"
    return {"tag_name": "v0.2.0", "draft": False, "prerelease": False,
            "assets": [{"name": stem + suffix, "browser_download_url": base + stem + suffix, "size": 7}
                       for suffix in (".zip", ".sha256")]}


def test_release_selects_current_platform_and_rejects_foreign_download(monkeypatch):
    monkeypatch.setattr(updates, "platform_name", lambda: ("Windows", "amd64", "zip"))
    data = release_data()
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))) as client:
        release = updates.check_release("0.1.9", client)
        assert release.version == "0.2.0"
        assert release.archive.endswith("Windows-amd64.zip")
        assert updates.check_release("0.2.0", client) is None
        assert updates.check_release("0.3.0", client) is None
        data["assets"][0]["browser_download_url"] = "https://other.invalid/app.zip"
        with pytest.raises(ValueError):
            updates.check_release("0.1.9", client)


def test_download_checksum_failure_and_cancel_do_not_install(tmp_path):
    content = b"payload"
    release = updates.Release("0.2.0", "app.zip", "https://test/app.zip", "https://test/app.sha256", len(content))
    checksum = [hashlib.sha256(content).hexdigest()]

    def handler(request):
        return httpx.Response(200, content=(checksum[0] + "  app.zip\n").encode()
                              if request.url.path.endswith("sha256") else content)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        cancel = threading.Event()
        path = updates.download_release(release, tmp_path / "good", cancel, lambda *args: None, client)
        assert path.read_bytes() == content
        checksum[0] = "0" * 64
        with pytest.raises(ValueError, match="校验失败"):
            updates.download_release(release, tmp_path / "bad", cancel, lambda *args: None, client)
        assert not list((tmp_path / "bad").iterdir())
        checksum[0] = hashlib.sha256(content).hexdigest()
        with pytest.raises(ValueError, match="取消"):
            updates.download_release(release, tmp_path / "cancel", cancel, lambda *args: cancel.set(), client)
        assert not list((tmp_path / "cancel").iterdir())


def make_archive(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)


def test_update_extraction_validates_paths_and_preserves_bundle(tmp_path):
    archive = tmp_path / "app.zip"
    make_archive(archive, [("HikvisionConsole/HikvisionConsole.exe", b"test executable")])
    bundle = updates.extract_release(archive, tmp_path / "safe", "Windows")
    assert (bundle / "HikvisionConsole.exe").read_bytes() == b"test executable"
    make_archive(archive, [("../outside", b"unsafe")])
    with pytest.raises(ValueError, match="路径"):
        updates.extract_release(archive, tmp_path / "unsafe", "Windows")
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize("rollback", [False, True])
def test_installer_waits_for_old_process_and_preserves_previous_version(tmp_path, rollback):
    target = tmp_path / "installed app"
    target.mkdir()
    (target / "version").write_text("old")
    stage = tmp_path / "staged app"
    if not rollback:
        stage.mkdir()
        (stage / "version").write_text("new")
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], **subprocess_options())
    command, ready, log, backup = updates.installer_script(
        target, stage, tmp_path / "installer", child.pid, platform.system(), restart=False)
    worker = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **subprocess_options())
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and worker.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), log.read_text() if log.exists() else "installer did not start"
        assert (target / "version").read_text() == "old"
        child.terminate()
        child.wait(timeout=10)
        output = worker.communicate(timeout=15)
        if rollback:
            assert worker.returncode != 0
            assert (target / "version").read_text() == "old"
        else:
            assert worker.returncode == 0, output
            assert (target / "version").read_text() == "new"
            assert (backup / "version").read_text() == "old"
    finally:
        for process in (child, worker):
            if process.poll() is None:
                process.kill()
            process.wait()


@pytest.mark.skipif(os.name == "nt", reason="macOS archive symlinks")
def test_macos_archive_retains_framework_links(tmp_path):
    path = tmp_path / "app.zip"
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("Hikvision Console.app/Contents/MacOS/HikvisionConsole", b"program")
        archive.writestr("Hikvision Console.app/Contents/Frameworks/Versions/A/lib", b"lib")
        link = zipfile.ZipInfo("Hikvision Console.app/Contents/Frameworks/Versions/Current")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        archive.writestr(link, b"A")
    path.write_bytes(data.getvalue())
    bundle = updates.extract_release(path, tmp_path / "unpacked", "Darwin")
    assert (bundle / "Contents/Frameworks/Versions/Current/lib").read_bytes() == b"lib"
