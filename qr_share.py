# -*- coding: utf-8 -*-
"""QR 공유 모듈 - 로컬 HTTP 서버 + QR 코드 생성"""
import html
import socket
import subprocess
import threading
import http.server
import socketserver
import re
import urllib.parse
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


def _session_path(session_id: str):
    sid = session_id.replace(".jpg", "")
    if not _SESSION_ID_RE.fullmatch(sid):
        return None, None
    return sid, cfg.PHOTOS_DIR / f"{sid}.jpg"


class _PhotoHandler(http.server.BaseHTTPRequestHandler):
    """/s/<session_id> 모바일 페이지와 JPEG 다운로드 응답"""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 2:
            self.send_error(404)
            return

        kind, raw_sid = parts
        sid, path = _session_path(raw_sid)
        if not sid or not path or not path.exists():
            self.send_error(404)
            return

        if kind == "s":
            self._send_mobile_page(sid)
            return

        if kind == "photo":
            self._send_jpeg(sid, path, attachment=False)
            return

        if kind == "download":
            self._send_jpeg(sid, path, attachment=True)
            return

        self.send_error(404)

    def _send_mobile_page(self, sid: str):
        safe_sid = html.escape(sid, quote=True)
        body = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>뚝딱 포토부스 사진 받기</title>
  <style>
    body {{
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #fff5fa;
      color: #321e28;
    }}
    main {{
      max-width: 520px;
      margin: 0 auto;
      padding: 24px 18px 34px;
      text-align: center;
    }}
    h1 {{
      margin: 4px 0 8px;
      font-size: 26px;
      line-height: 1.25;
      color: #ff4b73;
    }}
    p {{
      margin: 8px 0 18px;
      color: #6f6470;
      font-size: 15px;
      line-height: 1.55;
    }}
    img {{
      width: 100%;
      max-width: 420px;
      border-radius: 14px;
      border: 8px solid #ffcfe0;
      box-sizing: border-box;
      background: white;
    }}
    a.button {{
      display: block;
      margin: 20px auto 0;
      max-width: 420px;
      padding: 17px 18px;
      border-radius: 14px;
      background: #ff4b73;
      color: white;
      text-decoration: none;
      font-weight: 800;
      font-size: 18px;
    }}
    .hint {{
      font-size: 13px;
      color: #8a818a;
    }}
  </style>
</head>
<body>
  <main>
    <h1>사진 저장하기</h1>
    <p>아래 버튼을 누르면 네컷 사진 JPG 파일을 저장할 수 있습니다.</p>
    <img src="/photo/{safe_sid}.jpg" alt="네컷 사진 미리보기">
    <a class="button" href="/download/{safe_sid}.jpg" download="photobooth_{safe_sid}.jpg">사진 다운로드</a>
    <p class="hint">같은 Wi-Fi에서만 열립니다. 저장이 안 되면 사진을 길게 눌러 저장하세요.</p>
  </main>
</body>
</html>""".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        log.info(f"QR 모바일 페이지 열림: {sid}")

    def _send_jpeg(self, sid: str, path, attachment: bool):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        disposition = "attachment" if attachment else "inline"
        self.send_header(
            "Content-Disposition",
            f'{disposition}; filename="photobooth_{sid}.jpg"',
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
        log.info(f"사진 {'다운로드' if attachment else '미리보기'}: {sid}")

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
        return f"http://{ip}:{cfg.QR_SERVER_PORT}/s/{session_id}"

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
