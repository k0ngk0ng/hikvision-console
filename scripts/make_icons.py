"""Create native icon containers from the repository's vector icon."""
import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

app = QApplication([])
renderer = QSvgRenderer("assets/icon.svg")


def png(size):
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


chunks = []
for size, tag in ((16, b"icp4"), (32, b"icp5"), (64, b"icp6"), (128, b"ic07"),
                  (256, b"ic08"), (512, b"ic09"), (1024, b"ic10")):
    data = png(size)
    chunks.append(tag + struct.pack(">I", len(data)+8) + data)
body = b"".join(chunks)
Path("assets/icon.icns").write_bytes(b"icns" + struct.pack(">I", len(body)+8) + body)
data = png(256)
Path("assets/icon.ico").write_bytes(struct.pack("<HHH", 0, 1, 1) +
                                  struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(data), 22) + data)
