# -*- coding: utf-8 -*-
"""QR 공유 모듈 - 로컬 HTTP 서버 + QR 코드 생성"""
import socket
import subprocess
import threading
import http.server
import socketserver
import re
import qrcode
import numpy as np
import logging

import config as cfg

log = logging.getLogger(__name__)

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def get_local_ip() -> str:
    """현재 WiFi/이더넷 IP 주소 반환"""
    candidates = []

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("1.1.1.1", 80))
        candidates.append(s.getsockname()[0])
    except Exception:
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass

    try:
        result = subprocess.run(
            ["hostname", "-I"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            candidates.extend(result.stdout.split())
    except Exception:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.append(info[4][0])
    except Exception:
        pass

    for ip in candidates:
        if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
            return ip
    return "127.0.0.1"


class _PhotoHandler(http.server.BaseHTTPRequestHandler):
    """/photo/<session_id> 요청에 JPEG 파일 응답"""

    def do_GET(self):
        parts = self.path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "photo":
            sid  = parts[1].replace(".jpg", "")
            if not _SESSION_ID_RE.fullmatch(sid):
                self.send_error(404)
                return
            path = cfg.PHOTOS_DIR / f"{sid}.jpg"
            if path.exists():
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition",
                                 f'attachment; filename="photobooth_{sid}.jpg"')
                self.end_headers()
                self.wfile.write(data)
                log.info(f"사진 다운로드: {sid}")
                return
        self.send_error(404)

    def log_message(self, fmt, *args):
        pass   # 콘솔 HTTP 로그 억제


class _ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class QRServer:
    def __init__(self):
        self._server = None
        self._thread = None
        self._running = False

    def start(self):
        try:
            self._server = _ThreadingTCPServer(("", cfg.QR_SERVER_PORT), _PhotoHandler)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                daemon=True, name="qr-http-server"
            )
            self._thread.start()
            self._running = True
            log.info(f"QR 서버 시작: http://{get_local_ip()}:{cfg.QR_SERVER_PORT}")
        except OSError as e:
            self._running = False
            log.warning(f"QR 서버 시작 실패 (포트 {cfg.QR_SERVER_PORT} 사용 중?): {e}")

    @property
    def is_running(self) -> bool:
        return self._running

    def url_for(self, session_id: str) -> str:
        ip = get_local_ip()
        return f"http://{ip}:{cfg.QR_SERVER_PORT}/photo/{session_id}"

    def make_qr_surface(self, session_id: str):
        """(pygame.Surface, url_str) 반환"""
        import pygame
        url = self.url_for(session_id)
        log.info(f"QR URL: {url}")

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=9,
            border=3,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color=(45, 25, 35), back_color=(255, 245, 250))
        img = img.convert("RGB")
        arr = np.array(img)
        surface = pygame.surfarray.make_surface(arr.swapaxes(0, 1))
        return surface, url

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._running = False
