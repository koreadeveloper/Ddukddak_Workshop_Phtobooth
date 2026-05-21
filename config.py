# -*- coding: utf-8 -*-
"""뚝딱 포토부스 - 설정 파일"""
from pathlib import Path

# ─── 화면 ────────────────────────────────────────────
SCREEN_W   = 1920
SCREEN_H   = 1080
FPS        = 60
FULLSCREEN = True

# ─── 카메라 ──────────────────────────────────────────
CAM_INDEX = 0
CAM_W     = 1280
CAM_H     = 720

# ─── 촬영 흐름 ───────────────────────────────────────
PHOTO_COUNT      = 4     # 한 세션에 찍을 장수
COUNTDOWN_SECS   = 3     # 카운트다운 초
SHOT_DELAY       = 1.2   # 찰칵 후 다음 카운트 전 대기(초)
QR_SHOW_TIMEOUT  = 30    # QR 화면 자동 복귀(초)

# ─── 인쇄 ────────────────────────────────────────────
PRINTER_NAME = "Canon_CP1500"   # CUPS 프린터 이름

# ─── QR 공유 서버 ─────────────────────────────────────
QR_SERVER_PORT = 8080

# ─── 경로 ────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
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
