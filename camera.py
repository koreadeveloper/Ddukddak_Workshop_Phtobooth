# -*- coding: utf-8 -*-
"""카메라 모듈 - Logitech C920e (V4L2)"""
import cv2
import math
import threading
import time
import logging
import numpy as np

log = logging.getLogger(__name__)


class Camera:
    def __init__(
        self,
        index: int = 0,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        device: str = "",
    ):
        self.index  = index
        self.device = device.strip() if device else ""
        self.source = self.device or self.index
        self.width  = width
        self.height = height
        self.fps    = fps
        self._cap     = None
        self._frame   = None
        self._lock    = threading.Lock()
        self._running = False
        self._thread  = None

    def start(self):
        # V4L2 백엔드 우선, 실패 시 기본 백엔드
        self._cap = cv2.VideoCapture(self.source, cv2.CAP_V4L2)
        if not self._cap.isOpened():
            self._cap = cv2.VideoCapture(self.source)
        if not self._cap.isOpened():
            raise RuntimeError(f"카메라를 열 수 없습니다 (source={self.source})")

        self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._cap.set(cv2.CAP_PROP_FPS, self.fps)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
        self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)   # 자동 노출

        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="camera-reader")
        self._thread.start()

        # 첫 프레임 대기 (최대 5초)
        deadline = time.time() + 5.0
        while self._frame is None and time.time() < deadline:
            time.sleep(0.05)

        if self._frame is None:
            self.stop()
            raise RuntimeError(
                f"카메라 프레임을 받을 수 없습니다 (source={self.source}, "
                f"{self.width}x{self.height}@{self.fps})"
            )

        actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self._cap.get(cv2.CAP_PROP_FPS)
        log.info(
            "카메라 시작 완료 "
            f"(source={self.source}, requested={self.width}x{self.height}@{self.fps}, "
            f"actual={actual_w}x{actual_h}@{actual_fps:.1f})"
        )

    def _loop(self):
        while self._running:
            ret, frame = self._cap.read()
            if ret:
                with self._lock:
                    self._frame = frame
            else:
                time.sleep(0.01)

    def get_frame(self):
        """최신 프레임(BGR numpy array) 반환. 없으면 None."""
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._cap:
            self._cap.release()
        log.info("카메라 종료")


class MockCamera:
    """카메라 없이 테스트할 때 사용하는 더미 카메라"""

    def __init__(self, width=1280, height=720):
        self.width  = width
        self.height = height
        self._t     = 0.0

    def start(self):
        log.warning("MockCamera 사용 중 (실제 카메라 없음)")

    def get_frame(self):
        self._t += 0.02
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        col = int(abs(math.sin(self._t)) * 200 + 30)
        frame[:, :, 0] = col    # B
        frame[:, :, 1] = 80     # G
        frame[:, :, 2] = 150    # R
        return frame

    def stop(self):
        pass
