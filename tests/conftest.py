import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hikvision_console.models import Channel, Device, Stream


def pytest_configure(config):
    # pytest creates basetemp itself, but not its parent on a clean checkout.
    (config.rootpath / ".tmp").mkdir(exist_ok=True)


@pytest.fixture
def device():
    return Device("Test NVR", "V1", [
        Channel(i, f"Camera {i}", i <= 6, True, "在线" if i <= 6 else "离线", {
            "main": Stream(str(i*100+1), "H.265", 1920, 1080, 2048, 25),
            "sub": Stream(str(i*100+2), "H.265", 640, 360, 512, 25),
        }) for i in range(1, 9)
    ], datetime(2026, 9, 30, 12, tzinfo=timezone(timedelta(hours=8))))
