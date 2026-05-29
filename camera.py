# -*- coding: utf-8 -*-
"""카메라 모듈 - Logitech C920e (V4L2)"""
import cv2
import math
import threading
import time
import logging
import numpy as np

import config as cfg

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
        self._last_frame_at = 0.0
        self._last_reconnect_at = 0.0
        self._failure_count = 0
        self._reconnect_count = 0
        self._status = "대기"

    def _open_capture(self) -> bool:
        # V4L2 백엔드 우선, 실패 시 기본 백엔드
        cap = cv2.VideoCapture(self.source, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            self._status = f"열기 실패: {self.source}"
            return False

        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)   # 자동 노출

        self._cap = cap
        self._status = "연결됨"
        return True

    def start(self):
        if not self._open_capture():
            raise RuntimeError(f"카메라를 열 수 없습니다 (source={self.source})")

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

        cap = self._cap
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) if cap else 0
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) if cap else 0
        actual_fps = cap.get(cv2.CAP_PROP_FPS) if cap else 0.0
        log.info(
            "카메라 시작 완료 "
            f"(source={self.source}, requested={self.width}x{self.height}@{self.fps}, "
            f"actual={actual_w}x{actual_h}@{actual_fps:.1f})"
        )

    def _loop(self):
        while self._running:
            if not self._cap or not self._cap.isOpened():
                self._try_reconnect("캡처 장치 닫힘")
                continue

            ret, frame = self._cap.read()
            now = time.time()
            if ret and frame is not None:
                with self._lock:
                    self._frame = frame
                    self._last_frame_at = now
                    self._failure_count = 0
                    self._status = "정상"
            else:
                self._failure_count += 1
                stale_for = now - self._last_frame_at if self._last_frame_at else math.inf
                if (
                    self._failure_count >= cfg.CAM_MAX_READ_FAILURES
                    or stale_for >= cfg.CAM_STALE_SECS
                ):
                    self._try_reconnect(f"프레임 읽기 실패 {self._failure_count}회")
                else:
                    time.sleep(0.02)

    def _try_reconnect(self, reason: str):
        now = time.time()
        if now - self._last_reconnect_at < cfg.CAM_RECONNECT_SECS:
            time.sleep(0.05)
            return

        self._last_reconnect_at = now
        self._status = f"재연결 중: {reason}"
        log.warning(f"카메라 재연결 시도 ({reason})")

        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

        if self._open_capture():
            self._failure_count = 0
            self._reconnect_count += 1
            log.info(f"카메라 재연결 성공 (source={self.source}, count={self._reconnect_count})")
        else:
            log.warning(f"카메라 재연결 실패 (source={self.source})")
            time.sleep(0.2)

    def get_frame(self, max_age: float | None = None):
        """최신 프레임(BGR numpy array) 반환. 없으면 None."""
        with self._lock:
            if max_age is not None and self._last_frame_at:
                if time.time() - self._last_frame_at > max_age:
                    return None
            return self._frame.copy() if self._frame is not None else None

    def health(self) -> dict:
        now = time.time()
        with self._lock:
            age = now - self._last_frame_at if self._last_frame_at else math.inf
            ok = self._frame is not None and age <= cfg.CAM_STALE_SECS
            return {
                "ok": ok,
                "source": str(self.source),
                "status": self._status,
                "age": age,
                "failures": self._failure_count,
                "reconnects": self._reconnect_count,
            }

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

    def get_frame(self, max_age: float | None = None):
        self._t += 0.02
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        col = int(abs(math.sin(self._t)) * 200 + 30)
        frame[:, :, 0] = col    # B
        frame[:, :, 1] = 80     # G
        frame[:, :, 2] = 150    # R
        return frame

    def health(self) -> dict:
        return {
            "ok": True,
            "source": "mock",
            "status": "테스트 카메라",
            "age": 0.0,
            "failures": 0,
            "reconnects": 0,
        }

    def stop(self):
        pass
