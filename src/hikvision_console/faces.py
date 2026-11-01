"""Local frontal-face sampling. No biometric identity recognition or network service."""
import time
from pathlib import Path


def detect_face(path: Path):
    import cv2

    # VLC writes snapshots asynchronously. Wait in the worker, never on the GUI thread.
    try:
        for _ in range(20):
            if path.exists() and path.stat().st_size:
                frame = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                if frame is not None:
                    break
            time.sleep(0.05)
        else:
            raise RuntimeError("截图未生成，请等待画面恢复后重试")
        height, width = frame.shape
        scale = min(1, 800/width)
        if scale < 1:
            frame = cv2.resize(frame, (int(width*scale), int(height*scale)))
        frame = cv2.equalizeHist(frame)
        classifier = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
        if classifier.empty():
            raise RuntimeError("人脸检测组件不可用")
        return len(classifier.detectMultiScale(frame, scaleFactor=1.1, minNeighbors=6, minSize=(28, 28))) > 0
    finally:
        path.unlink(missing_ok=True)
