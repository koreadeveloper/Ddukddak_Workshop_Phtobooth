# -*- coding: utf-8 -*-
"""뚝딱 포토부스 - 설정 파일"""
import os
import re
from pathlib import Path


BASE_DIR = Path(__file__).parent
_ENV_KEY_RE = re.compile(r"^PHOTOBOOTH_[A-Z0-9_]+$")


def _parse_env_value(raw_value: str) -> str:
    value = raw_value.strip().rstrip("\r")
    if not value:
        return ""

    if value[0] in {"'", '"'}:
        quote = value[0]
        chars = []
        escaped = False
        for ch in value[1:]:
            if quote == '"' and escaped:
                chars.append(ch)
                escaped = False
                continue
            if quote == '"' and ch == "\\":
                escaped = True
                continue
            if ch == quote:
                return "".join(chars)
            chars.append(ch)
        return "".join(chars)

    comment_at = value.find(" #")
    if comment_at >= 0:
        value = value[:comment_at].rstrip()
    return value


def _load_env_file(path: Path):
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not _ENV_KEY_RE.fullmatch(key):
            continue
        os.environ.setdefault(key, _parse_env_value(raw_value))


_load_env_file(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_text(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


# ─── 브랜딩/행사명 ────────────────────────────────────
BRAND_NAME = _env_text("PHOTOBOOTH_BRAND_NAME", "뚝딱 포토부스")
EVENT_TITLE = _env_text("PHOTOBOOTH_EVENT_TITLE", "오늘의 네컷")
BOOTH_SUBTITLE = _env_text("PHOTOBOOTH_BOOTH_SUBTITLE", "뚝딱 공방 포토부스")
FOOTER_TEXT = _env_text("PHOTOBOOTH_FOOTER_TEXT", "어린이 포토부스  |  뚝딱 공방")
PRINT_BRAND_TEXT = _env_text("PHOTOBOOTH_PRINT_BRAND_TEXT", BRAND_NAME)
PRINT_MARK_TEXT = _env_text("PHOTOBOOTH_PRINT_MARK_TEXT", "FOUR CUT")
DOWNLOAD_FILENAME_PREFIX = _env_text("PHOTOBOOTH_DOWNLOAD_PREFIX", "photobooth")


# ─── 화면 ────────────────────────────────────────────
SCREEN_W   = _env_int("PHOTOBOOTH_SCREEN_W", 1920)
SCREEN_H   = _env_int("PHOTOBOOTH_SCREEN_H", 1080)
WINDOW_W   = _env_int("PHOTOBOOTH_WINDOW_W", 1280)
WINDOW_H   = _env_int("PHOTOBOOTH_WINDOW_H", 720)
FPS        = _env_int("PHOTOBOOTH_FPS", 60)
FULLSCREEN = _env_bool("PHOTOBOOTH_FULLSCREEN", True)

# 실제 디스플레이 해상도는 Pi/모니터마다 다르므로, 기본 UI는 1920x1080으로
# 그리고 출력 단계에서 화면에 맞게 스케일링합니다.
DISPLAY_AUTO_SIZE = _env_bool("PHOTOBOOTH_DISPLAY_AUTO_SIZE", True)
MOUSE_VISIBLE     = _env_bool("PHOTOBOOTH_MOUSE_VISIBLE", True)
AUDIO_ENABLED     = _env_bool("PHOTOBOOTH_AUDIO_ENABLED", True)

# ─── 카메라 ──────────────────────────────────────────
CAM_INDEX  = _env_int("PHOTOBOOTH_CAM_INDEX", 0)
CAM_DEVICE = _env_text("PHOTOBOOTH_CAM_DEVICE", "")
CAM_W      = _env_int("PHOTOBOOTH_CAM_W", 1280)
CAM_H      = _env_int("PHOTOBOOTH_CAM_H", 720)
CAM_FPS    = _env_int("PHOTOBOOTH_CAM_FPS", 30)
CAM_STALE_SECS = _env_int("PHOTOBOOTH_CAM_STALE_SECS", 3)
CAM_RECONNECT_SECS = _env_int("PHOTOBOOTH_CAM_RECONNECT_SECS", 2)
CAM_MAX_READ_FAILURES = _env_int("PHOTOBOOTH_CAM_MAX_READ_FAILURES", 30)

# 화면 미리보기는 거울처럼 보여 주되, 저장/인쇄 이미지는 실제 카메라 방향을 유지합니다.
PREVIEW_MIRROR = _env_bool("PHOTOBOOTH_PREVIEW_MIRROR", True)
SHOW_CROP_GUIDE = _env_bool("PHOTOBOOTH_SHOW_CROP_GUIDE", True)
CAPTURE_ORIENTATION = _env_text("PHOTOBOOTH_CAPTURE_ORIENTATION", "portrait").lower()
PORTRAIT_ROTATION = _env_text("PHOTOBOOTH_PORTRAIT_ROTATION", "clockwise").lower()
DEFAULT_FILTER = _env_text("PHOTOBOOTH_DEFAULT_FILTER", "bright").lower()
DEFAULT_FRAME_THEME = _env_text("PHOTOBOOTH_DEFAULT_FRAME_THEME", "soft_pink").lower()
DEFAULT_PRINT_LAYOUT = _env_text("PHOTOBOOTH_DEFAULT_PRINT_LAYOUT", "auto").lower()

# ─── 촬영 흐름 ───────────────────────────────────────
PHOTO_COUNT      = 4     # 한 세션에 찍을 장수
COUNTDOWN_SECS   = 3     # 카운트다운 초
SHOT_DELAY       = 1.2   # 찰칵 후 다음 카운트 전 대기(초)
QR_SHOW_TIMEOUT  = 30    # QR 화면 자동 복귀(초)
REVIEW_TIMEOUT   = _env_int("PHOTOBOOTH_REVIEW_TIMEOUT", 120)
PRINT_RESULT_TIMEOUT = _env_int("PHOTOBOOTH_PRINT_RESULT_TIMEOUT", 5)

# ─── 인쇄 ────────────────────────────────────────────
PRINTER_NAME = _env_text("PHOTOBOOTH_PRINTER_NAME", "Canon_CP1500")
DEFAULT_PRINT_COPIES = _env_int("PHOTOBOOTH_DEFAULT_PRINT_COPIES", 1)
MAX_PRINT_COPIES = _env_int("PHOTOBOOTH_MAX_PRINT_COPIES", 3)
PRINT_JOB_WAIT_SECS = _env_int("PHOTOBOOTH_PRINT_JOB_WAIT_SECS", 120)
PRINT_JOB_POLL_SECS = _env_int("PHOTOBOOTH_PRINT_JOB_POLL_SECS", 2)

# ─── 사진 보관/저장공간 관리 ──────────────────────────
PHOTO_RETENTION_DAYS = _env_int("PHOTOBOOTH_PHOTO_RETENTION_DAYS", 14)
MAX_STORED_PHOTOS = _env_int("PHOTOBOOTH_MAX_STORED_PHOTOS", 800)
MIN_FREE_GB = _env_int("PHOTOBOOTH_MIN_FREE_GB", 1)
CLEANUP_INTERVAL_SECS = _env_int("PHOTOBOOTH_CLEANUP_INTERVAL_SECS", 3600)

# ─── QR 공유 서버 ─────────────────────────────────────
QR_SERVER_PORT = _env_int("PHOTOBOOTH_QR_PORT", 8080)

# ─── 경로 ────────────────────────────────────────────
PHOTOS_DIR = BASE_DIR / "photos"
ASSETS_DIR = BASE_DIR / "assets"

# ─── 색상 팔레트 ──────────────────────────────────────
C_BG      = (255, 245, 250)   # 연핑크 배경
C_PINK    = (255,  75, 115)   # 메인 핑크
C_LPINK   = (255, 190, 210)   # 연핑크 강조
C_BLUE    = ( 65, 155, 255)   # 하늘색
C_YELLOW  = (255, 210,   0)   # 노랑
C_WHITE   = (255, 255, 255)
C_DARK    = ( 50,  30,  40)   # 다크 텍스트
C_GRAY    = (130, 130, 140)   # 회색
C_LGRAY   = (210, 210, 215)   # 연회색
C_GREEN   = ( 50, 195,  95)   # 초록

# ─── 한국어 폰트 후보 (라즈베리파이 OS 기준) ─────────────
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/fonts-nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/fonts-nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto/NotoSansCJK-Regular.ttc",
]
