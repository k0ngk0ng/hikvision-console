"""GitHub release discovery, verified downloads and staged desktop updates."""
from __future__ import annotations

import hashlib
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx

from .desktop import subprocess_options

REPOSITORY = "k0ngk0ng/hikvision-console"
API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_ARCHIVE = 1024 * 1024 * 1024


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise ValueError("发布版本号格式不正确")
    return tuple(map(int, match.groups()))


def platform_name():
    system, machine = platform.system(), platform.machine().lower()
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    if system == "Windows" and arch == "x86_64":
        return system, "amd64", "zip"
    if system == "Darwin" and arch == "arm64":
        return system, arch, "zip"
    if system == "Linux" and arch == "x86_64":
        return system, arch, "tar.gz"
    raise ValueError("此系统架构暂无自动更新安装包，请从 Releases 查看可用版本")


@dataclass(frozen=True)
class Release:
    version: str
    archive: str
    url: str
    checksum_url: str
    size: int


def check_release(current, client=None):
    if client is None:
        with httpx.Client(timeout=20, follow_redirects=True) as owned:
            return check_release(current, owned)
    response = client.get(API, headers={"Accept": "application/vnd.github+json"})
    response.raise_for_status()
    data = response.json()
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = data["tag_name"]
    if version_tuple(tag) <= version_tuple(current):
        return None
    version = tag.removeprefix("v")
    system, arch, extension = platform_name()
    stem = f"HikvisionConsole-{version}-{system}-{arch}"
    archive = f"{stem}.{extension}"
    assets = {item["name"]: item for item in data["assets"]}
    base = f"https://github.com/{REPOSITORY}/releases/download/{tag}/"
    for name in (archive, f"{stem}.sha256"):
        if name not in assets or assets[name].get("browser_download_url") != base + name:
            raise ValueError("新版本尚未提供完整的本平台安装包，请稍后重试")
    size = assets[archive]["size"]
    if not isinstance(size, int) or not 0 < size <= MAX_ARCHIVE:
        raise ValueError("安装包大小不正确")
    return Release(version, archive, base + archive, base + stem + ".sha256", size)


def download_release(release, directory, cancel, progress, client=None):
    if client is None:
        with httpx.Client(timeout=httpx.Timeout(30, connect=15), follow_redirects=True) as owned:
            return download_release(release, directory, cancel, progress, owned)
    directory.mkdir(parents=True, exist_ok=True)
    response = client.get(release.checksum_url)
    response.raise_for_status()
    if len(response.content) > 1024:
        raise ValueError("校验文件格式不正确")
    match = re.fullmatch(r"([a-fA-F0-9]{64})  " + re.escape(release.archive) + r"\s*", response.text)
    if not match:
        raise ValueError("校验文件与安装包不匹配")
    destination = directory / release.archive
    partial = destination.with_name(destination.name + ".partial")
    digest, count = hashlib.sha256(), 0
    try:
        with client.stream("GET", release.url) as response:
            response.raise_for_status()
            with partial.open("wb") as output:
                for chunk in response.iter_bytes(256 * 1024):
                    if cancel.is_set():
                        raise ValueError("已取消下载")
                    count += len(chunk)
                    if count > release.size:
                        raise ValueError("下载大小超过发布记录")
                    digest.update(chunk)
                    output.write(chunk)
                    progress(count, release.size)
        if cancel.is_set():
            raise ValueError("已取消下载")
        if count != release.size or digest.hexdigest() != match[1].lower():
            raise ValueError("安装包校验失败，未安装；请重新下载")
        partial.replace(destination)
        return destination
    finally:
        partial.unlink(missing_ok=True)


def extract_release(archive, destination, system=None):
    system = system or platform.system()
    destination.mkdir(parents=True, exist_ok=False)
    if archive.name.endswith(".zip"):
        links = []
        with zipfile.ZipFile(archive) as source:
            if sum(item.file_size for item in source.infolist()) > 3 * MAX_ARCHIVE:
                raise ValueError("安装包解压大小异常")
            for item in source.infolist():
                name = PurePosixPath(item.filename)
                if name.is_absolute() or ".." in name.parts or "\\" in item.filename or ":" in item.filename:
                    raise ValueError("安装包包含不安全的路径")
                target = destination.joinpath(*name.parts)
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode):
                    links.append((target, source.read(item).decode("utf-8")))
                elif item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(item) as src, target.open("xb") as dst:
                        shutil.copyfileobj(src, dst)
                    target.chmod((mode & 0o777) or 0o644)
        # Create links only after regular files, so extraction never writes through one.
        for target, link in links:
            if Path(link).is_absolute() or "\\" in link or ":" in link:
                raise ValueError("安装包包含不安全的链接")
            if not (target.parent / link).resolve().is_relative_to(destination.resolve()):
                raise ValueError("安装包链接越界")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(link)
    else:
        with tarfile.open(archive) as source:
            if sum(item.size for item in source.getmembers()) > 3 * MAX_ARCHIVE:
                raise ValueError("安装包解压大小异常")
            source.extractall(destination, filter="data")
    bundle = destination / ("Hikvision Console.app" if system == "Darwin" else "HikvisionConsole")
    program = bundle / ("Contents/MacOS/HikvisionConsole" if system == "Darwin" else
                        "HikvisionConsole.exe" if system == "Windows" else "HikvisionConsole")
    if not program.is_file():
        raise ValueError("安装包缺少应用程序")
    return bundle


def installed_bundle():
    if not getattr(sys, "frozen", False):
        raise ValueError("源码运行请使用 Git 更新；自动安装仅用于已打包应用")
    executable = Path(sys.executable).resolve()
    return executable.parents[2] if sys.platform == "darwin" else executable.parent


def stage_install(bundle, target):
    if not target.is_dir() or target.parent == target:
        raise ValueError("无法定位当前安装目录")
    stage = target.parent / f".{target.name}-update-{uuid.uuid4().hex}"
    try:
        shutil.copytree(bundle, stage, symlinks=True)
    except OSError:
        shutil.rmtree(stage, ignore_errors=True)
        raise ValueError("无法写入安装目录，请将应用移至有写入权限的文件夹后重试") from None
    return stage


def installer_script(target, stage, directory, parent_pid, system=None, restart=True):
    """Scripts live outside the installed directory and retain the previous bundle."""
    system = system or platform.system()
    directory.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(target.name + ".previous-" + uuid.uuid4().hex[:8])
    ready = directory / "installer.ready"
    ready.unlink(missing_ok=True)
    log = directory / "install.log"
    if system == "Windows":
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        script = directory / "install.ps1"
        body = f"""$ErrorActionPreference = 'Stop'
$target = {quote(target)}
$stage = {quote(stage)}
$backup = {quote(backup)}
Set-Content -LiteralPath {quote(ready)} -Value ready
try {{
    $old = Get-Process -Id {int(parent_pid)} -ErrorAction SilentlyContinue
    if ($old) {{ $old | Wait-Process -Timeout 90 }}
    Move-Item -LiteralPath $target -Destination $backup
    try {{ Move-Item -LiteralPath $stage -Destination $target }}
    catch {{ Move-Item -LiteralPath $backup -Destination $target; throw }}
    $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
"""
        if restart:
            body += "    Start-Process -FilePath (Join-Path $target 'HikvisionConsole.exe') -WorkingDirectory $target\n"
        body += f"}} catch {{ $_ | Out-File -LiteralPath {quote(log)}; exit 1 }}\n"
        script.write_text(body, encoding="utf-8-sig")
        binary = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        command = [str(binary), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-WindowStyle", "Hidden", "-File", str(script)]
    else:
        script = directory / "install.sh"
        q = shlex.quote
        body = f"""#!/bin/sh
set -eu
target={q(str(target))}
stage={q(str(stage))}
backup={q(str(backup))}
: > {q(str(ready))}
count=0
while kill -0 {int(parent_pid)} 2>/dev/null; do
    count=$((count + 1))
    [ "$count" -lt 90 ] || exit 1
    sleep 1
done
mv "$target" "$backup"
if ! mv "$stage" "$target"; then mv "$backup" "$target"; exit 1; fi
export PYINSTALLER_RESET_ENVIRONMENT=1
"""
        if restart:
            program = "Contents/MacOS/HikvisionConsole" if system == "Darwin" else "HikvisionConsole"
            body += f'"$target/{program}" </dev/null &\n'
        script.write_text(body, encoding="utf-8")
        command = ["/bin/sh", str(script)]
    return command, ready, log, backup


def launch_installer(target, stage):
    # Keep scripts outside target, including when the user configured data inside it.
    directory = target.parent / f".hikvision-installer-{uuid.uuid4().hex}"
    command, ready, log, _ = installer_script(target, stage, directory, os.getpid())
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    with log.open("ab") as output:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                   env=environment, **subprocess_options(),
                                   **({"start_new_session": True} if os.name != "nt" else {}))
    for _ in range(100):
        if ready.exists():
            return
        if process.poll() is not None:
            break
        time.sleep(0.1)
    if process.poll() is None:
        process.terminate()
    raise ValueError(f"无法启动更新安装，请查看 {log}")


def download_and_stage(release, directory, target, cancel: threading.Event, progress):
    archive = download_release(release, directory, cancel, progress)
    bundle = extract_release(archive, directory / ("unpacked-" + uuid.uuid4().hex))
    if cancel.is_set():
        raise ValueError("已取消更新")
    return stage_install(bundle, target)
