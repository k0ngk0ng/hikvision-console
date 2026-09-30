from __future__ import annotations

import json
import os
from pathlib import Path


class Settings:
    """Development defaults are workspace-local; a packaged app has an explicit user data directory."""

    def __init__(self, directory: Path | None = None):
        self.directory = directory or Path(os.environ.get("HIKVISION_HOME", ".hikvision-console")).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "settings.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def update(self, **values):
        if "password" in values:
            raise ValueError("Passwords must not be persisted")
        self.data.update(values)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def output_dir(self, category):
        path = Path(self.get("output_dir", str(self.directory / "exports"))) / category
        path.mkdir(parents=True, exist_ok=True)
        return path
