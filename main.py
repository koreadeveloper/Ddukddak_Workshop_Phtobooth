#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
뚝딱 포토부스
────────────────────────────────────────────────
Raspberry Pi 5  /  Logitech C920e  /  Canon SELPHY CP1500
pygame 기반 4컷 스트립 포토부스 (어린이센터용)

실행:  python3 main.py
테스트 (카메라 없이):  python3 main.py --test
"""

import sys
import os
import time
from datetime import date
import uuid
import threading
import logging
import argparse
import math
import random
import shutil

import cv2
import pygame
import numpy as np

# ─── 내부 모듈 ───────────────────────────────────────
import config as cfg
from config import *
from camera import Camera, MockCamera
import composer
from qr_share import QRServer, get_local_ip
import printer
from effects import FILTERS, apply_filter
import booth_stats

# ─── 로깅 설정 ──────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "photobooth.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

AI_HEALTH_INTERVAL_SECS = 60   # AI 서버 연결 확인 주기 (이미지 생성 없음, 비용 없음)
INPUT_GUARD_SECS = 0.25   # 상태 전환 직후 입력 무시 시간 (터치 중복 클릭 방지)
CONCEPT_CATEGORY_NAMES = {
    "profile": "프로필", "job": "직업", "traditional": "전통",
    "stage": "무대", "sports": "스포츠", "fun": "재미",
}
DEFAULT_AI_CONSENT_TEXT = (
    "촬영한 사진은 AI 변환을 위해 외부 AI 서버로 전송됩니다.|"
    "변환이 끝난 사진은 이 부스에만 저장되며 {days}일 후 자동 삭제됩니다.|"
    "만 14세 미만은 보호자와 함께 동의해 주세요."
)


# ════════════════════════════════════════════════════
#  상태 상수
# ════════════════════════════════════════════════════
class St:
    IDLE      = "idle"
    ID_SETUP = "id_setup"
    ID_CAPTURE = "id_capture"
    ID_PROCESSING = "id_processing"
    ID_RESULT = "id_result"
    ID_REVIEW = "id_review"
    CONCEPT_SELECT = "concept_select"
    CONSENT   = "consent"
    COUNTDOWN = "countdown"
    FLASH     = "flash"
    AI_PROCESSING = "ai_processing"
    REVIEW    = "review"
    PRINTING  = "printing"
    QR_SHOW   = "qr_show"
    STATUS    = "status"


ID_STAGES = {St.ID_SETUP, St.ID_CAPTURE, St.ID_PROCESSING, St.ID_RESULT, St.ID_REVIEW}


# ════════════════════════════════════════════════════
#  헬퍼 함수
# ════════════════════════════════════════════════════
def find_korean_font() -> str | None:
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            log.info(f"폰트 발견: {p}")
            return p
    log.warning("한국어 폰트를 찾지 못했습니다.")
    return None


def bgr_to_surface(bgr: np.ndarray, size: tuple = None) -> pygame.Surface:
    """OpenCV BGR ndarray → pygame Surface"""
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if size:
        rgb = cv2.resize(rgb, size, interpolation=cv2.INTER_LINEAR)
    return pygame.surfarray.make_surface(rgb.swapaxes(0, 1))


def fit_bgr_to_surface(bgr: np.ndarray, max_w: int, max_h: int) -> pygame.Surface:
    """비율을 유지하며 지정 영역 안에 들어가는 pygame Surface 생성"""
    h, w = bgr.shape[:2]
    scale = min(max_w / w, max_h / h)
    target = (max(1, int(w * scale)), max(1, int(h * scale)))
    return bgr_to_surface(bgr, target)


def center_crop_bgr_to_aspect(bgr: np.ndarray, target_aspect: float) -> np.ndarray:
    """BGR frame을 지정한 가로/세로 비율로 중앙 크롭합니다."""
    h, w = bgr.shape[:2]
    if h <= 0 or w <= 0 or target_aspect <= 0:
        return bgr
    frame_aspect = w / h
    if frame_aspect > target_aspect:
        new_w = max(1, int(h * target_aspect))
        x = max(0, (w - new_w) // 2)
        return bgr[:, x:x + new_w]
    new_h = max(1, int(w / target_aspect))
    y = max(0, (h - new_h) // 2)
    return bgr[y:y + new_h, :]


def set_runtime_screen_size(width: int, height: int):
    """실제 디스플레이 크기에 맞춰 런타임 UI 해상도를 갱신합니다."""
    global SCREEN_W, SCREEN_H
    SCREEN_W = max(640, int(width))
    SCREEN_H = max(480, int(height))
    cfg.SCREEN_W = SCREEN_W
    cfg.SCREEN_H = SCREEN_H


def scaled_font_size(design_size: int, min_size: int) -> int:
    scale = min(SCREEN_W / 1920, SCREEN_H / 1080)
    return max(min_size, int(round(design_size * scale)))


def pil_to_surface(pil_img) -> pygame.Surface:
    """PIL Image → pygame Surface"""
    arr = np.array(pil_img.convert("RGB"))
    return pygame.surfarray.make_surface(arr.swapaxes(0, 1))


def fit_text(text, font, max_width: int | None):
    text = str(text)
    if max_width is None or font.size(text)[0] <= max_width:
        return text

    ellipsis = "..."
    if font.size(ellipsis)[0] > max_width:
        return ""

    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.size(text[:mid].rstrip() + ellipsis)[0] <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ellipsis


def draw_text(surface, text, font, color, x, y, anchor="topleft",
              shadow=False, shadow_color=(0, 0, 0), shadow_off=3,
              max_width: int | None = None):
    text = fit_text(text, font, max_width)
    if shadow:
        s = font.render(text, True, shadow_color)
        r = s.get_rect(**{anchor: (x + shadow_off, y + shadow_off)})
        surface.blit(s, r)
    rendered = font.render(text, True, color)
    rect = rendered.get_rect(**{anchor: (x, y)})
    surface.blit(rendered, rect)
    return rect


def draw_rrect(surface, color, rect, radius=20, border=0, bcol=None):
    pygame.draw.rect(surface, color, rect, border_radius=radius)
    if border and bcol:
        pygame.draw.rect(surface, bcol, rect, border, border_radius=radius)


def draw_card(surface, rect, radius=18):
    shadow = pygame.Surface((rect.width + 12, rect.height + 12), pygame.SRCALPHA)
    pygame.draw.rect(
        shadow, (40, 30, 50, 24), shadow.get_rect().inflate(-6, -6),
        border_radius=radius
    )
    surface.blit(shadow, (rect.x - 2, rect.y + 4))
    draw_rrect(surface, C_WHITE, rect, radius=radius, border=1, bcol=(224, 224, 230))


def make_beep(freq=440, duration=0.12, volume=0.55, sr=44100):
    """단순 사인파 사운드 생성"""
    n = int(sr * duration)
    t = np.linspace(0, duration, n, False)
    env = np.ones(n)
    fade = min(int(n * 0.1), 500)
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    wave = (np.sin(2 * math.pi * freq * t) * env * volume * 32767).astype(np.int16)
    stereo = np.column_stack([wave, wave])
    return pygame.sndarray.make_sound(stereo)


# ════════════════════════════════════════════════════
#  버튼 클래스
# ════════════════════════════════════════════════════
class Button:
    def __init__(self, x, y, w, h, label, color,
                 text_color=C_WHITE, radius=22, border_color=None, border_width=0):
        self.rect       = pygame.Rect(x, y, w, h)
        self.label      = label
        self.color      = color
        self.hover_col  = tuple(min(c + 25, 255) for c in color)
        self.press_col  = tuple(max(c - 30, 0) for c in color)
        self.text_color = text_color
        self.radius     = radius
        self.border_color = border_color
        self.border_width = border_width
        self._hov       = False
        self._pressed   = False

    def set_color(self, color):
        self.color     = color
        self.hover_col = tuple(min(c + 25, 255) for c in color)
        self.press_col = tuple(max(c - 30, 0) for c in color)

    def set_style(self, color, text_color=None, border_color=None, border_width=None):
        self.set_color(color)
        if text_color is not None:
            self.text_color = text_color
        if border_color is not None:
            self.border_color = border_color
        if border_width is not None:
            self.border_width = border_width

    def update(self, mpos, pressed=False):
        self._hov     = self.rect.collidepoint(mpos)
        self._pressed = pressed and self._hov

    def draw(self, surf, font):
        col = self.press_col if self._pressed else (
              self.hover_col if self._hov else self.color)
        draw_rrect(surf, col, self.rect, self.radius,
                   self.border_width, self.border_color)
        draw_text(surf, self.label, font, self.text_color,
                  self.rect.centerx, self.rect.centery, anchor="center",
                  max_width=self.rect.width - 24)

    def hit(self, pos) -> bool:
        return self.rect.collidepoint(pos)


# ════════════════════════════════════════════════════
#  파티클 시스템 (대기화면 장식)
# ════════════════════════════════════════════════════
class Particle:
    COLORS = [C_LPINK, C_YELLOW, (195, 220, 255), (255, 230, 160)]

    def __init__(self):
        self.reset()

    def reset(self):
        self.x     = random.randint(0, SCREEN_W)
        self.y     = random.uniform(SCREEN_H * 0.2, SCREEN_H * 1.1)
        self.r     = random.randint(7, 28)
        self.speed = random.uniform(0.4, 1.4)
        self.alpha = random.randint(55, 130)
        self.color = random.choice(self.COLORS)
        self.wobble_phase = random.uniform(0, math.pi * 2)
        self.wobble_amp   = random.uniform(0.3, 1.2)

    def update(self, dt):
        self.y -= self.speed * dt * 40
        self.wobble_phase += dt * 1.5
        self.x += math.sin(self.wobble_phase) * self.wobble_amp
        if self.y + self.r < 0:
            self.x = random.randint(0, SCREEN_W)
            self.y = SCREEN_H + self.r + random.randint(0, 200)

    def draw(self, surf):
        s = pygame.Surface((self.r * 2, self.r * 2), pygame.SRCALPHA)
        pygame.draw.circle(s, (*self.color, self.alpha),
                           (self.r, self.r), self.r)
        surf.blit(s, (int(self.x - self.r), int(self.y - self.r)))


# ════════════════════════════════════════════════════
#  메인 애플리케이션
# ════════════════════════════════════════════════════
class PhotoBooth:
    def _create_display(self):
        flags = pygame.FULLSCREEN if FULLSCREEN else pygame.RESIZABLE
        if FULLSCREEN and DISPLAY_AUTO_SIZE:
            display_size = (0, 0)
        elif FULLSCREEN:
            display_size = (SCREEN_W, SCREEN_H)
        else:
            display_size = (min(SCREEN_W, WINDOW_W), min(SCREEN_H, WINDOW_H))

        self.display = pygame.display.set_mode(display_size, flags)
        actual_w, actual_h = self.display.get_size()
        if DISPLAY_AUTO_SIZE and (actual_w != SCREEN_W or actual_h != SCREEN_H):
            log.info(
                f"UI 해상도 자동 조정: configured={SCREEN_W}x{SCREEN_H}, "
                f"actual={actual_w}x{actual_h}"
            )
            set_runtime_screen_size(actual_w, actual_h)
        self.screen = pygame.Surface((SCREEN_W, SCREEN_H)).convert()
        self._update_display_transform()
        pygame.display.set_caption(BRAND_NAME)

    def _update_display_transform(self):
        self.display_w, self.display_h = self.display.get_size()
        self.ui_scale = min(self.display_w / SCREEN_W, self.display_h / SCREEN_H)
        self.ui_scale = max(self.ui_scale, 0.01)
        self.ui_w = max(1, int(SCREEN_W * self.ui_scale))
        self.ui_h = max(1, int(SCREEN_H * self.ui_scale))
        self.ui_x = (self.display_w - self.ui_w) // 2
        self.ui_y = (self.display_h - self.ui_h) // 2
        log.info(
            f"디스플레이: actual={self.display_w}x{self.display_h}, "
            f"ui={SCREEN_W}x{SCREEN_H}, scale={self.ui_scale:.3f}"
        )

    def _event_pos_to_ui(self, pos):
        x = int((pos[0] - self.ui_x) / self.ui_scale)
        y = int((pos[1] - self.ui_y) / self.ui_scale)
        return x, y

    def _present(self):
        self.display.fill(C_BG)
        if self.ui_w == SCREEN_W and self.ui_h == SCREEN_H:
            scaled = self.screen
        else:
            scaled = pygame.transform.scale(self.screen, (self.ui_w, self.ui_h))
        self.display.blit(scaled, (self.ui_x, self.ui_y))
        pygame.display.flip()

    def _compact_review_layout(self):
        margin = max(24, int(SCREEN_W * 0.031))
        gap = 18
        action_h = min(58, max(42, int(SCREEN_H * 0.08)))
        action_y = SCREEN_H - action_h - 32
        card_top = 104 if SCREEN_H >= 720 else 88
        card_h = action_y - 28 - card_top
        cards_w = SCREEN_W - 2 * margin - gap
        left_w = min(400, int(SCREEN_W * 0.39), cards_w - min(520, int(cards_w * 0.66)))
        right_x = margin + left_w + gap
        right_w = cards_w - left_w

        left_card = pygame.Rect(margin, card_top, left_w, card_h)
        right_card = pygame.Rect(right_x, card_top, right_w, card_h)
        v = min(1.0, (card_h - 10) / 540)

        preview_h = min(
            card_h - 82,
            int((left_w - 44) * composer.PRINT_H / composer.PRINT_W),
        )
        padding = min(28, right_w // 18)
        inner_x = right_card.x + padding
        inner_right = right_card.right - padding
        inner_w = inner_right - inner_x
        thumb_gap = 14
        thumb_panel_w = min(252, int(inner_w * 0.48))
        thumb_grid_y = right_card.y + int(88 * v)
        retake_y = right_card.y + int(280 * v)
        # Leave room for both thumbnail borders and the separate retake button.
        max_thumb_h = (retake_y - thumb_grid_y - thumb_gap - 14) // 2
        max_thumb_w = (thumb_panel_w - thumb_gap - 12) // 2
        thumb_w = min(114, max_thumb_w, int(max_thumb_h / 0.68))
        thumb_h = int(thumb_w * 0.68)
        portrait_thumb_w = min(max_thumb_w, int(max_thumb_h / 1.45))
        portrait_thumb_h = int(portrait_thumb_w * 1.45)

        divider_x = inner_x + thumb_panel_w + 10
        control_x = divider_x + 16
        choice_gap = 10
        choice_w = (inner_right - control_x - choice_gap) // 2

        filter_gap = 10
        filter_w = (inner_w - filter_gap * 4) // 5
        action_gap = 30
        action_w = (SCREEN_W - margin * 2 - action_gap * 3) // 4
        action_x = margin

        return {
            "v": v,
            "left_card": left_card,
            "right_card": right_card,
            "preview_h": preview_h,
            "thumb_gap": thumb_gap,
            "thumb_grid_x": inner_x + 6,
            "thumb_grid_y": thumb_grid_y,
            "thumb_w": thumb_w,
            "thumb_h": thumb_h,
            "portrait_thumb_w": portrait_thumb_w,
            "portrait_thumb_h": portrait_thumb_h,
            "retake_x": inner_x,
            "retake_y": retake_y,
            "retake_w": thumb_panel_w,
            "control_right": inner_right,
            "divider_x": divider_x,
            "control_x": control_x,
            "choice_w": choice_w,
            "choice_gap": choice_gap,
            "filter_x": inner_x,
            "filter_w": filter_w,
            "filter_gap": filter_gap,
            "action_x": action_x,
            "action_y": action_y,
            "action_w": action_w,
            "action_h": action_h,
            "action_gap": action_gap,
        }

    def __init__(self, test_mode: bool = False):
        pygame.init()
        self.audio_enabled = False
        if AUDIO_ENABLED:
            try:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
                self.audio_enabled = True
            except Exception as e:
                log.warning(f"오디오 초기화 실패 - 무음으로 실행합니다: {e}")
        pygame.mouse.set_visible(MOUSE_VISIBLE)

        self._create_display()
        # The full-size controls use fixed positions sized for the FHD layout.
        self.compact_ui = SCREEN_W < 1840 or SCREEN_H < 1080
        self.clock = pygame.time.Clock()

        # ── 폰트 ──────────────────────────────────────
        fp = find_korean_font()
        self.f_huge   = pygame.font.Font(fp, scaled_font_size(160, 110))
        self.f_big    = pygame.font.Font(fp, scaled_font_size(78, 44))
        self.f_large  = pygame.font.Font(fp, scaled_font_size(52, 34))
        self.f_medium = pygame.font.Font(fp, scaled_font_size(36, 28))
        self.f_small  = pygame.font.Font(fp, scaled_font_size(24, 22))
        self.f_tiny   = pygame.font.Font(fp, scaled_font_size(18, 17))

        # ── 사운드 ────────────────────────────────────
        if self.audio_enabled:
            try:
                self.snd_beep    = make_beep(880, 0.10)   # 카운트다운 삑
                self.snd_shutter = make_beep(1200, 0.05)  # 찰칵
                self.snd_done    = make_beep(660, 0.20)   # 완료
            except Exception as e:
                log.warning(f"효과음 생성 실패 - 무음으로 실행합니다: {e}")
                self.snd_beep = self.snd_shutter = self.snd_done = None
        else:
            self.snd_beep = self.snd_shutter = self.snd_done = None

        # ── 카메라 ────────────────────────────────────
        if test_mode:
            import cv2 as _cv2
            globals()['cv2'] = _cv2
            self.camera = MockCamera(CAM_W, CAM_H)
        else:
            self.camera = Camera(CAM_INDEX, CAM_W, CAM_H, CAM_FPS, CAM_DEVICE)
        self.camera.start()
        self.id_demo_frame = None
        if test_mode and AI_PROVIDER == "mock":
            sample = ASSETS_DIR / "ai_concepts" / "suit_profile.jpg"
            if sample.is_file():
                self.id_demo_frame = cv2.imread(str(sample))

        # ── QR 서버 ───────────────────────────────────
        self.qr_server = QRServer()
        self.qr_server.start()

        # ── 세션 데이터 ───────────────────────────────
        self.capture_orientation = (
            CAPTURE_ORIENTATION if CAPTURE_ORIENTATION in {"portrait", "landscape"}
            else "portrait"
        )
        self.portrait_rotation = (
            PORTRAIT_ROTATION if PORTRAIT_ROTATION in {"clockwise", "counterclockwise"}
            else "clockwise"
        )
        self.selected_filter = (
            DEFAULT_FILTER if DEFAULT_FILTER in FILTERS else "bright"
        )
        self.selected_frame_theme = (
            DEFAULT_FRAME_THEME if DEFAULT_FRAME_THEME in composer.FRAME_THEMES
            else "studio_black"
        )
        self.selected_print_layout = "grid"
        self.print_copies = max(1, min(DEFAULT_PRINT_COPIES, MAX_PRINT_COPIES))
        self._compose_generation = 0
        # ── AI 변신 (AI_ENABLED=0이면 manager 없음) ──
        self.ai_manager = None
        self.ai_concepts = []
        self.ai_generation = 0
        self.session_mode = "normal"
        self._ai_saved_look = None
        self.concept_page = 0
        self.concept_selected = []
        self.ai_screen_input_at = 0.0
        self.idle_notice = ""
        self.idle_notice_until = 0.0
        self._concept_thumb_cache = {}
        self._ai_slot_cache = {}
        self.ai_provider = None
        self.ai_online = False              # health_check 결과 (첫 확인 전에는 회색)
        self.ai_health_text = "확인 전"
        self.ai_health_at = 0.0
        self._ai_health_stop = threading.Event()
        if AI_ENABLED:
            self._init_ai()
        self.status_lines = []
        self.status_checked_at = 0.0
        self.selected_retake_index = 0
        self.retake_index = None
        self.review_notice = ""
        self.review_notice_until = 0.0
        self.cleanup_summary = "정리 전"
        self.cleanup_checked_at = 0.0
        self.session_counted = False
        self._reset_session()

        # ── 상태 머신 ─────────────────────────────────
        self.state      = St.IDLE
        self.state_time = time.time()

        # ── COUNTDOWN 상태 변수 ────────────────────────
        self.cd_val    = COUNTDOWN_SECS   # 현재 카운트 숫자
        self.cd_tick   = 0.0              # 카운트 시작 시각
        self.last_beep = COUNTDOWN_SECS + 1  # 중복 비프 방지

        # ── FLASH 상태 변수 ───────────────────────────
        self.flash_alpha = 0

        # ── REVIEW 상태 변수 ──────────────────────────
        self.strip_surface  = None
        self.thumb_surfaces = []
        self.qr_surface     = None
        self.qr_url         = ""
        self._composing     = False
        self._print_thread  = None
        self._print_done    = False
        self._print_ok      = False
        self._print_done_at = 0.0

        # ── 파티클 ────────────────────────────────────
        self.particles = [Particle() for _ in range(22)]

        # ── 버튼 ──────────────────────────────────────
        if self.compact_ui:
            self.btn_status = Button(
                SCREEN_W - 170, 18, 150, 44, "운영 점검", (110, 110, 120), radius=14
            )
        else:
            self.btn_status = Button(
                1540, 38, 270, 58, "운영 점검", (110, 110, 120), radius=18
            )
        self._build_ai_buttons()
        self._build_id_buttons()
        self.btn_capture_home = Button(20, 18, 190 if self.compact_ui else 250, 54,
                                       "촬영 중단", C_WHITE, C_DARK, radius=14,
                                       border_color=C_LGRAY, border_width=2)
        self.review_frame_buttons = []
        self.review_filter_buttons = []
        if self.compact_ui:
            layout = self._compact_review_layout()
            panel_x = layout["control_x"]
            col_w = layout["choice_w"]
            col_gap = layout["choice_gap"]
            v = layout["v"]
            button_h = int(42 * v)
            for i, (theme_id, theme) in enumerate(composer.FRAME_THEMES.items()):
                x = panel_x + (i % 2) * (col_w + col_gap)
                y = layout["right_card"].y + int((88 + (i // 2) * 50) * layout["v"])
                self.review_frame_buttons.append(
                    (theme_id, Button(x, y, col_w, button_h, theme["name"], C_WHITE,
                                      C_DARK, radius=14, border_color=C_LGRAY, border_width=2))
                )
            orient_y = layout["right_card"].y + int(258 * layout["v"])
            self.review_orientation_buttons = [
                ("portrait", Button(panel_x, orient_y, col_w, button_h,
                                    "세로", C_WHITE, C_DARK, radius=14,
                                    border_color=C_LGRAY, border_width=2)),
                ("landscape", Button(panel_x + col_w + col_gap, orient_y,
                                     col_w, button_h, "가로", C_WHITE, C_DARK, radius=14,
                                     border_color=C_LGRAY, border_width=2)),
            ]
            for i, (filter_id, meta) in enumerate(FILTERS.items()):
                x = layout["filter_x"] + i * (layout["filter_w"] + layout["filter_gap"])
                y = layout["right_card"].y + int(384 * layout["v"])
                self.review_filter_buttons.append(
                    (filter_id, Button(x, y, layout["filter_w"], button_h, meta["name"],
                                       C_WHITE, C_DARK, radius=14,
                                       border_color=C_LGRAY, border_width=2))
                )

            bw = layout["action_w"]
            bh = layout["action_h"]
            gap = layout["action_gap"]
            bx = layout["action_x"]
            by = layout["action_y"]
            self.btn_review_home = Button(bx, by, bw, bh, "처음으로",
                                          C_WHITE, C_DARK, radius=18,
                                          border_color=C_LGRAY, border_width=2)
            self.btn_retake = Button(bx + bw + gap, by, bw, bh, "다시 찍기",
                                     C_WHITE, C_PINK, radius=18,
                                     border_color=C_PINK, border_width=2)
            self.btn_qr     = Button(bx + 2 * (bw + gap), by, bw, bh, "QR 저장",
                                     C_WHITE, C_DARK, radius=18,
                                     border_color=C_LGRAY, border_width=2)
            self.btn_print  = Button(bx + 3 * (bw + gap), by, bw, bh, "인쇄하기", C_PINK, radius=18)
            self.btn_retake_selected = Button(
                layout["retake_x"], layout["retake_y"], layout["retake_w"], int(42 * v),
                "선택 컷 다시 찍기", C_WHITE, C_DARK, radius=14,
                border_color=C_LGRAY, border_width=2
            )
            copies_y = layout["right_card"].y + int(492 * layout["v"])
            copies_mid = layout["right_card"].x + layout["right_card"].width // 2
            copies_h = int(46 * v)
            self.btn_copies_minus = Button(copies_mid - 86, copies_y, 46, copies_h, "-",
                                           C_WHITE, C_PINK, radius=12,
                                           border_color=C_PINK, border_width=2)
            self.btn_copies_plus = Button(copies_mid + 40, copies_y, 46, copies_h, "+",
                                          C_WHITE, C_PINK, radius=12,
                                          border_color=C_PINK, border_width=2)
            self.btn_status_close = Button(
                SCREEN_W // 2 - 140, SCREEN_H - 92, 280, 58, "돌아가기", C_BLUE, radius=18
            )
            ai_w = 2 * col_w + col_gap
            ai_y = layout["right_card"].y + int(222 * v)
            self.btn_ai_toggle = Button(panel_x, ai_y, ai_w, int(36 * v), "원본/AI 전환",
                                        C_WHITE, C_DARK, radius=12,
                                        border_color=C_LGRAY, border_width=2)
            self.btn_ai_regen = Button(panel_x, ai_y + int(40 * v), ai_w, int(36 * v), "선택 컷 다시 변환",
                                       C_WHITE, C_PINK, radius=12,
                                       border_color=C_PINK, border_width=2)
        else:
            panel_x = 1240
            for i, (theme_id, theme) in enumerate(composer.FRAME_THEMES.items()):
                x = panel_x + (i % 2) * 220
                y = 170 + (i // 2) * 68
                self.review_frame_buttons.append(
                    (theme_id, Button(x, y, 200, 52, theme["name"], (110, 110, 120), radius=16))
                )
            for i, (filter_id, meta) in enumerate(FILTERS.items()):
                x = panel_x + (i % 2) * 220
                y = 480 + (i // 2) * 64
                self.review_filter_buttons.append(
                    (filter_id, Button(x, y, 200, 50, meta["name"], (110, 110, 120), radius=16))
                )
            self.review_orientation_buttons = [
                ("portrait", Button(panel_x, 356, 200, 52, "세로", (110, 110, 120), radius=16)),
                ("landscape", Button(panel_x + 220, 356, 200, 52, "가로", (110, 110, 120), radius=16)),
            ]

            bw, bh, gap = 300, 80, 30
            total = 4 * bw + 3 * gap
            bx = (SCREEN_W - total) // 2
            by = SCREEN_H - 115
            self.btn_review_home = Button(bx, by, bw, bh, "처음으로",
                                          C_WHITE, C_DARK, border_color=C_LGRAY, border_width=2)
            self.btn_retake = Button(bx + bw + gap, by, bw, bh, "다시 찍기",
                                     C_WHITE, C_PINK, border_color=C_PINK, border_width=2)
            self.btn_qr     = Button(bx + 2 * (bw + gap), by, bw, bh, "QR 저장",
                                     C_WHITE, C_DARK, border_color=C_LGRAY, border_width=2)
            self.btn_print  = Button(bx + 3 * (bw + gap), by, bw, bh, "인쇄하기", C_PINK)
            self.btn_retake_selected = Button(
                700, 660, 316, 64, "선택 컷 다시 찍기", C_WHITE, C_DARK, radius=18,
                border_color=C_LGRAY, border_width=2
            )
            self.btn_copies_minus = Button(panel_x, 760, 70, 58, "-", C_WHITE, C_PINK,
                                           radius=16, border_color=C_PINK, border_width=2)
            self.btn_copies_plus = Button(panel_x + 370, 760, 70, 58, "+", C_WHITE, C_PINK,
                                          radius=16, border_color=C_PINK, border_width=2)
            self.btn_status_close = Button(
                SCREEN_W // 2 - 165, SCREEN_H - 140, 330, 72, "돌아가기", C_BLUE, radius=20
            )
            self.btn_ai_regen = Button(700, 744, 316, 64, "선택 컷 다시 변환", C_WHITE, C_PINK,
                                       radius=18, border_color=C_PINK, border_width=2)
            self.btn_ai_toggle = Button(700, 824, 316, 64, "원본/AI 전환", C_WHITE, C_DARK,
                                        radius=18, border_color=C_LGRAY, border_width=2)
        self._audit_button_layout()

        PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
        self._cleanup_old_photos(force=True)
        log.info(f"포토부스 초기화 완료 (test_mode={test_mode})")

    # ─────────────────────────────────────────────────
    #  세션 초기화
    # ─────────────────────────────────────────────────
    def _reset_session(self):
        self.session_id     = uuid.uuid4().hex[:12]
        self.capture_date: date | None = None
        self.captured_source_bgr = []   # 카메라 원본 BGR ndarray 목록
        self.captured_raw_bgr = []      # 방향만 보정된 원본 BGR ndarray 목록
        self.captured_bgr   = []        # BGR ndarray 목록
        self.strip_path     = None
        self.strip_surface  = None
        self.thumb_surfaces = []
        self.qr_surface     = None
        self.qr_url         = ""
        self._composing     = False
        self._compose_generation += 1
        self._print_done    = False
        self._print_ok      = False
        self._print_done_at = 0.0
        self.print_copies   = max(1, min(DEFAULT_PRINT_COPIES, MAX_PRINT_COPIES))
        self.selected_retake_index = 0
        self.retake_index = None
        self.review_notice = ""
        self.review_notice_until = 0.0
        self.session_counted = False
        # ── AI 변신 세션 필드 ──
        if self.session_mode == "ai" and self._ai_saved_look is not None:
            # AI 콘셉트 기본 프레임/필터는 그 세션에서만 사용
            self.selected_filter, self.selected_frame_theme = self._ai_saved_look
        self._ai_saved_look = None
        if self.ai_manager is not None:
            self.ai_manager.cancel_all()
        self.session_mode = "normal"            # "normal" | "ai" | "id"
        self.id_count = 1
        self.id_captured_count = 0
        self.id_portraits = []
        self.id_items = []
        self.id_print_choices = []
        self.id_picker_slot = None
        self.id_picker_page = 0
        self.id_picker_options = []
        self.id_review_index = 0
        self.id_prepared = None
        self.id_result = None
        self.id_portrait_surface = None
        self.id_notice = ""
        self.ai_concept_id = None
        self.ai_slot_concepts = []
        self.ai_generation += 1                 # 세션 식별 (늦게 온 결과 무시용)
        self.ai_results = [None] * PHOTO_COUNT  # 성공 시 AI BGR (크롭 완료본)
        self.ai_status = ["idle"] * PHOTO_COUNT  # idle|pending|done|failed|refused|timeout
        self.ai_use_original = [False] * PHOTO_COUNT
        self.ai_regen_count = 0
        self.ai_processing_started_at = 0.0
        self._ai_recompose_needed = False
        self._ai_slot_cache = {}

    # ─────────────────────────────────────────────────
    #  AI 변신 (session_mode == "ai"일 때만 사용)
    # ─────────────────────────────────────────────────
    def _init_ai(self):
        """AI 사용 준비. 실패해도 경고만 남기고 일반 네컷으로 계속 동작."""
        try:
            import ai_concepts
            import ai_transform
            provider = ai_transform.get_provider()
            if getattr(provider, "name", "") != "mock" and not getattr(provider, "has_key", True):
                log.warning("AI 비활성: %s가 비어 있습니다", provider.key_env)
                return
            concepts = ai_concepts.load_concepts()
            if not concepts:
                log.warning("AI 비활성: 사용할 수 있는 콘셉트가 없습니다")
                return
            self.ai_concepts = concepts
            self.ai_provider = provider
            self.ai_manager = ai_transform.AITransformManager(provider)
            log.info(f"AI 변신 준비 완료: provider={provider.name}, 콘셉트 {len(concepts)}개")
            threading.Thread(target=self._ai_health_loop, name="ai-health", daemon=True).start()
        except Exception as e:
            log.warning(f"AI 비활성: 초기화 실패 ({type(e).__name__}: {e})")
            self.ai_manager = None

    def _ai_available(self) -> bool:
        return self.ai_manager is not None and bool(self.ai_concepts) and self.ai_online

    def _ai_health_loop(self):
        """시작 시 + AI_HEALTH_INTERVAL_SECS마다 연결 확인 → ai_online 갱신 (백그라운드)."""
        while not self._ai_health_stop.is_set():
            try:
                ok, text = self.ai_provider.health_check(timeout=5.0)
            except Exception as e:
                ok, text = False, f"확인 오류: {type(e).__name__}"
            if ok != self.ai_online or not self.ai_health_at:
                log.info(f"AI 서버 상태: {'정상' if ok else '연결 안 됨'} ({text})")
            self.ai_online = bool(ok)
            self.ai_health_text = text
            self.ai_health_at = time.time()
            self._ai_health_stop.wait(AI_HEALTH_INTERVAL_SECS)

    def _ai_status_line(self):
        if not AI_ENABLED:
            return ("AI", "꺼짐 (PHOTOBOOTH_AI_ENABLED=0)", True)
        if self.ai_provider is None:
            return ("AI", "사용 불가 · 키/콘셉트/초기화 확인 필요 (로그 참고)", False)
        name = getattr(self.ai_provider, "name", "?")
        model = getattr(self.ai_provider, "model", "") or name
        if name == "mock":
            key_text = "키 불필요"
        else:
            key_text = "키 설정됨" if getattr(self.ai_provider, "has_key", False) else "키 없음"
        if self.ai_health_at:
            ago = int(time.time() - self.ai_health_at)
            health = f"{'연결 정상' if self.ai_online else '연결 안 됨'} ({self.ai_health_text}, {ago}초 전)"
        else:
            health = "연결 확인 중"
        today = booth_stats.summary()["today"]
        return (
            "AI",
            f"{name} · {model} · {key_text} · {health} · "
            f"오늘 성공 {today['ai_success']} / 실패 {today['ai_failure']}",
            self.ai_online,
        )

    def _ai_concept(self, index=None):
        if index is not None and 0 <= index < len(self.ai_slot_concepts):
            return self.ai_slot_concepts[index]
        return self._ai_concept_by_id(self.ai_concept_id)

    @staticmethod
    def _assign_costumes(concepts, count):
        repeats, extra = divmod(count, len(concepts))
        assignments = concepts * repeats + random.sample(concepts, extra)
        random.shuffle(assignments)
        return assignments

    def _begin_ai_session(self, concepts):
        self._begin_countdown()
        self.session_mode = "ai"
        self.ai_slot_concepts = self._assign_costumes(concepts, PHOTO_COUNT)
        concept = concepts[0]
        self.ai_concept_id = concept.id
        self._ai_saved_look = (self.selected_filter, self.selected_frame_theme)
        if concept.default_filter in FILTERS:
            self.selected_filter = concept.default_filter
        if concept.default_frame in composer.FRAME_THEMES:
            self.selected_frame_theme = concept.default_frame
        booth_stats.increment("ai_sessions")
        for selected in concepts:
            booth_stats.increment_concept(selected.id)
        log.info("AI 변신 컷별 복장: %s", ", ".join(c.id for c in self.ai_slot_concepts))

    def _ai_submit(self, idx: int, raw: np.ndarray):
        """촬영 직후 해당 컷만 바로 변환 요청 (파이프라이닝)."""
        import ai_transform
        concept = self._ai_concept(idx)
        self.ai_results[idx] = None
        self.ai_use_original[idx] = False
        if concept is None:
            self.ai_status[idx] = "failed"
            return
        try:
            upload = ai_transform.prepare_upload_image(raw)
            prompt = (f"{concept.prompt}\n\n"
                      "Preserve the exact person, facial features, expression, skin tone, hairstyle, "
                      "head position, body pose, hands, gesture, camera angle and framing. "
                      "Change clothing and the requested background only. Light the person and "
                      "the surroundings as one photograph, matching the scene's light direction, "
                      "exposure, contrast and white balance. Preserve each person's natural "
                      "complexion and skin texture, with soft highlight roll-off and visible detail "
                      "in light clothing. Avoid skin whitening, blown highlights, flat frontal flash "
                      "and glowing outlines. Use the scene's subtle ambient light and realistic soft "
                      "shadows at the hair, neck and clothing edges. Do not beautify, reshape, "
                      "replace or add people.")
            self.ai_manager.submit(self.ai_generation, idx, upload, prompt)
        except Exception as e:
            log.warning(f"AI 변환 요청 실패 ({idx + 1}번 컷): {type(e).__name__}")
            self.ai_status[idx] = "failed"
            booth_stats.increment("ai_failure")
            return
        self.ai_status[idx] = "pending"
        booth_stats.increment("ai_requests")

    def _poll_ai_results(self):
        import ai_transform
        for gen, idx, res in self.ai_manager.poll():
            if gen != self.ai_generation or not 0 <= idx < PHOTO_COUNT:
                continue  # 지난 세션 결과 버림
            if self.session_mode == "id":
                self._receive_id_portrait(idx, res)
                continue
            if self.ai_status[idx] != "pending":
                continue  # 타임아웃 확정된 컷의 늦은 응답은 버림
            image = None
            if res.ok and res.image_bgr is not None and idx < len(self.captured_raw_bgr):
                try:
                    aspect = ai_transform.slot_aspect_for(self.captured_raw_bgr[idx])
                    image = ai_transform.postprocess_result(res.image_bgr, aspect)
                except Exception as e:
                    log.warning(f"AI 결과 후처리 실패 ({idx + 1}번 컷): {type(e).__name__}")
            if image is not None:
                self.ai_results[idx] = image
                self.ai_status[idx] = "done"
                booth_stats.increment("ai_success")
            else:
                self.ai_status[idx] = "refused" if res.refused else "failed"
                booth_stats.increment("ai_failure")
                if res.refused:
                    booth_stats.increment("ai_refused")
            if self.state == St.REVIEW:
                self._ai_recompose_needed = True
        # REVIEW 상태에서 재변환 결과가 도착했다면 재합성
        if self._ai_recompose_needed and self.state == St.REVIEW and not self._composing:
            self._ai_recompose_needed = False
            self._recompose_current_session()

    def _enter_ai_processing(self):
        self.ai_processing_started_at = time.time()
        self._set_state(St.AI_PROCESSING)

    def _update_ai_processing(self):
        pending = [i for i, st in enumerate(self.ai_status) if st == "pending"]
        if pending and time.time() - self.ai_processing_started_at > AI_TOTAL_TIMEOUT:
            for i in pending:
                self.ai_status[i] = "timeout"
                booth_stats.increment("ai_failure")
            log.warning(f"AI 전체 시간 초과: {[i + 1 for i in pending]}번 컷은 원본 사용")
            pending = []
        if pending:
            return
        failed = [i + 1 for i, st in enumerate(self.ai_status) if st != "done"]
        if failed:
            self._set_review_notice(f"{', '.join(map(str, failed))}번 컷은 원본으로 넣었어요.", 6.0)
        self._refresh_processed_photos()
        self._start_compose()
        self._set_state(St.REVIEW)
        if self.snd_done:
            self.snd_done.play()

    def _ai_concept_by_id(self, concept_id):
        for concept in self.ai_concepts:
            if concept.id == concept_id:
                return concept
        return None

    def _set_idle_notice(self, text: str, seconds: float = 3.5):
        self.idle_notice = text
        self.idle_notice_until = time.time() + seconds

    def _open_concept_select(self):
        if not self._ai_available():
            self._set_idle_notice("AI 연결을 확인한 뒤 다시 시도해 주세요.")
            return
        self.concept_page = 0
        self.concept_selected = []
        self.ai_screen_input_at = time.time()
        self._set_state(St.CONCEPT_SELECT)

    def _concept_pages(self) -> int:
        per = self.concept_per_page
        return max(1, (len(self.ai_concepts) + per - 1) // per)

    def _ai_select_timed_out(self) -> bool:
        if self.state == St.CONSENT and self.session_mode == "id" and self.id_items:
            return False  # 촬영을 마친 참가자가 있고 다음 참가자를 기다리는 중
        last = max(self.ai_screen_input_at, self.state_time)
        if time.time() - last <= AI_SELECT_TIMEOUT:
            return False
        log.info(f"{self.state} 화면 {AI_SELECT_TIMEOUT}초 무입력 → 대기 화면")
        if self.session_mode == "id":
            self._reset_session()
        self._set_state(St.IDLE)
        return True

    def _selected_concepts(self):
        return [self._ai_concept_by_id(concept_id) for concept_id in self.concept_selected]

    def _handle_concept_click(self, pos):
        self.ai_screen_input_at = time.time()
        pages = self._concept_pages()
        if pages > 1 and self.btn_concept_prev.hit(pos):
            self.concept_page = (self.concept_page - 1) % pages
            return
        if pages > 1 and self.btn_concept_next.hit(pos):
            self.concept_page = (self.concept_page + 1) % pages
            return
        if self.btn_concept_back.hit(pos):
            self._set_state(St.ID_SETUP if self.session_mode == "id" else St.IDLE)
            return
        if self.btn_concept_confirm.hit(pos):
            concepts = self._selected_concepts()
            if not concepts:
                return
            if AI_REQUIRE_CONSENT or self.session_mode == "id":
                if self.session_mode == "id":
                    self.ai_concept_id = concepts[0].id
                    slots = PHOTO_COUNT if len(concepts) > 1 else self.id_count
                    self.ai_slot_concepts = self._assign_costumes(concepts, slots)
                self._set_state(St.CONSENT)
            else:
                self._begin_ai_session(concepts)
            return
        base = self.concept_page * self.concept_per_page
        for i, btn in enumerate(self.concept_card_buttons):
            idx = base + i
            if idx < len(self.ai_concepts) and btn.hit(pos):
                concept_id = self.ai_concepts[idx].id
                if concept_id in self.concept_selected:
                    self.concept_selected.remove(concept_id)
                elif len(self.concept_selected) < PHOTO_COUNT:
                    self.concept_selected.append(concept_id)
                return

    def _ai_toggle_selected(self):
        if self._composing:
            self._set_review_notice("사진 합성 중입니다. 잠시 후 다시 눌러 주세요.")
            return
        i = self.selected_retake_index
        if not 0 <= i < len(self.captured_raw_bgr):
            return
        if self.ai_results[i] is None:
            self._set_review_notice(f"{i + 1}번 컷은 AI 결과가 없어 원본만 쓸 수 있어요.")
            return
        self.ai_use_original[i] = not self.ai_use_original[i]
        source = "원본" if self.ai_use_original[i] else "AI"
        self._set_review_notice(f"{i + 1}번 컷을 {source} 사진으로 바꿨어요.")
        self._recompose_current_session()

    def _ai_regenerate_selected(self):
        if self._composing:
            self._set_review_notice("사진 합성 중입니다. 잠시 후 다시 눌러 주세요.")
            return
        i = self.selected_retake_index
        if not 0 <= i < len(self.captured_raw_bgr):
            self._set_review_notice("다시 변환할 컷을 먼저 선택해 주세요.")
            return
        if self.ai_regen_count >= AI_MAX_REGEN_PER_SESSION:
            self._set_review_notice(f"다시 변환은 한 번 촬영에 {AI_MAX_REGEN_PER_SESSION}번까지 할 수 있어요.")
            return
        self.ai_regen_count += 1
        booth_stats.increment("ai_regenerate")
        log.info(f"{i + 1}번 컷 다시 변환 ({self.ai_regen_count}/{AI_MAX_REGEN_PER_SESSION})")
        self._ai_submit(i, self.captured_raw_bgr[i])
        if self.ai_status[i] == "pending":
            self._enter_ai_processing()
        else:
            self._recompose_current_session()

    # ── AI 화면 버튼 좌표 (FHD 고정 / compact) ──
    def _build_ai_buttons(self):
        cx = SCREEN_W // 2
        if self.compact_ui:
            bw, bh, gap, radius = 300, 64, 20, 22
            idle_y = SCREEN_H - 86
        else:
            bw, bh, gap, radius = 440, 92, 40, 28
            idle_y = SCREEN_H - 150
        mode_w = min(bw, (SCREEN_W - gap - 32) // 2)
        mode_start = cx - (2 * mode_w + gap) // 2
        self.btn_start_ai = Button(mode_start, idle_y, mode_w, bh, "AI 변신 네컷",
                                   C_PINK, radius=radius)
        self.btn_start_id = Button(mode_start + mode_w + gap, idle_y, mode_w, bh, "AI 증명사진",
                                   C_BLUE, radius=radius)

        cols, rows = 4, 2
        if self.compact_ui:
            card_gap, top = 16, 100
            nav_w, nav_h = 300, 60
            nav_y = SCREEN_H - nav_h - 20
            card_w = min(240, (SCREEN_W - 56 * 2 - card_gap * (cols - 1)) // cols)
            card_h = min(260, (nav_y - 16 - top - card_gap) // rows)
            page_w, page_h, page_gap = 40, 96, 8
        else:
            card_gap, top = 30, 170
            nav_w, nav_h = 440, 92
            nav_y = 950
            card_w, card_h = 380, 360
            page_w, page_h, page_gap = 80, 120, 30
        grid_w = cols * card_w + (cols - 1) * card_gap
        grid_h = rows * card_h + (rows - 1) * card_gap
        gx = (SCREEN_W - grid_w) // 2
        self.concept_per_page = cols * rows
        self.concept_grid_bottom = top + grid_h
        self.concept_card_buttons = [
            Button(gx + (i % cols) * (card_w + card_gap), top + (i // cols) * (card_h + card_gap),
                   card_w, card_h, f"콘셉트 카드 {i + 1}", C_WHITE, C_DARK, radius=18)
            for i in range(self.concept_per_page)
        ]
        page_y = top + (grid_h - page_h) // 2
        self.btn_concept_prev = Button(gx - page_gap - page_w, page_y, page_w, page_h, "이전",
                                       C_WHITE, C_PINK, radius=14,
                                       border_color=C_PINK, border_width=2)
        self.btn_concept_next = Button(gx + grid_w + page_gap, page_y, page_w, page_h, "다음",
                                       C_WHITE, C_PINK, radius=14,
                                       border_color=C_PINK, border_width=2)
        self.btn_concept_back = Button(cx - gap // 2 - nav_w, nav_y, nav_w, nav_h, "뒤로",
                                       C_WHITE, C_DARK, radius=radius,
                                       border_color=C_LGRAY, border_width=2)
        self.btn_concept_confirm = Button(cx + gap // 2, nav_y, nav_w, nav_h, "이 콘셉트로 촬영",
                                          C_PINK, radius=radius)

        consent_y = SCREEN_H - 100 if self.compact_ui else 880
        self.btn_consent_no = Button(cx - gap // 2 - bw, consent_y, bw, bh, "동의 안 함",
                                     C_WHITE, C_DARK, radius=radius,
                                     border_color=C_LGRAY, border_width=2)
        self.btn_consent_yes = Button(cx + gap // 2, consent_y, bw, bh, "동의하고 촬영",
                                      C_PINK, radius=radius)

    def _build_id_buttons(self):
        gap = 18 if self.compact_ui else 30
        h = 56 if self.compact_ui else 76
        w = min(340, (SCREEN_W - 80 - 2 * gap) // 3)
        x = (SCREEN_W - 3 * w - 2 * gap) // 2
        y = SCREEN_H - h - 28
        self.btn_id_cancel = Button(x, y, w, h, "처음으로", C_WHITE, C_DARK,
                                    border_color=C_LGRAY, border_width=2)
        self.btn_id_secondary = Button(x + w + gap, y, w, h, "다시 찍기", C_WHITE, C_PINK,
                                       border_color=C_PINK, border_width=2)
        self.btn_id_primary = Button(x + 2 * (w + gap), y, w, h, "다음", C_BLUE)
        count_w = min(260, (SCREEN_W - 80 - 3 * gap) // 4)
        count_x = (SCREEN_W - 4 * count_w - 3 * gap) // 2
        count_y = (SCREEN_H - h) // 2
        self.btn_id_counts = [
            (count, Button(count_x + index * (count_w + gap), count_y, count_w, h,
                           f"{count}명", C_WHITE, C_DARK, border_color=C_LGRAY, border_width=2))
            for index, count in enumerate(range(1, 5))
        ]
        top = 115 if self.compact_ui else 170
        self.id_preview_area = pygame.Rect(SCREEN_W // 5, top, 3 * SCREEN_W // 5,
                                          y - top - self.f_small.get_height() - 32)

    def _open_id_setup(self):
        if not self._ai_available():
            self._set_idle_notice("AI 연결을 확인한 뒤 다시 시도해 주세요.")
            return
        self._reset_session()
        self.idle_notice = ""
        self.session_mode = "id"
        self._set_state(St.ID_SETUP)

    def _id_frame(self):
        if self.id_demo_frame is not None:
            return self.id_demo_frame
        return self._camera_frame()

    def _retry_id_capture(self, notice="", *, state=St.ID_CAPTURE):
        self.id_prepared = None
        self.id_result = None
        self.id_portrait_surface = None
        self.id_notice = notice
        self._set_state(state)

    def _submit_id_portrait(self, index):
        import ai_transform
        import id_portrait

        item = self.id_items[index]
        upload = ai_transform.resize_max_side(item["prepared"].image_bgr, AI_UPLOAD_MAX_SIDE)
        concept = self.ai_slot_concepts[item["slot"]]
        prompt = f"{concept.prompt}\n\n{id_portrait.PORTRAIT_PROMPT_SUFFIX}"
        self.ai_manager.submit(self.ai_generation, index, upload, prompt)
        item["status"] = "pending"
        booth_stats.increment("ai_requests")

    def _capture_id_portrait(self):
        import id_portrait

        frame = self._id_frame()
        if frame is None:
            self._retry_id_capture("카메라 연결을 확인한 뒤 다시 찍어 주세요.")
            return
        if not self.ai_slot_concepts or not self._ai_available():
            self._retry_id_capture("AI 연결을 확인한 뒤 다시 찍어 주세요.")
            return
        try:
            prepared = id_portrait.prepare_portrait(frame)
        except ValueError as exc:
            self._retry_id_capture(str(exc))
            return
        except Exception as exc:
            log.warning(f"증명사진 촬영 처리 실패: {type(exc).__name__}")
            self._retry_id_capture("사진을 처리하지 못했어요. 다시 찍어 주세요.")
            return
        # 네 칸을 참가자 순서로 배분하고, 각 참가자의 원본을 해당 칸에 재사용합니다.
        for slot in range(self.id_captured_count, len(self.ai_slot_concepts), self.id_count):
            index = len(self.id_items)
            self.id_items.append({"prepared": prepared, "slot": slot, "status": "failed",
                                  "result": None, "error": ""})
            try:
                self._submit_id_portrait(index)
            except Exception as exc:
                log.warning("증명사진 변환 요청 실패: %s", type(exc).__name__)

        self.id_captured_count += 1
        self.id_notice = ""
        if self.snd_shutter:
            self.snd_shutter.play()
        area = self.id_preview_area
        self.id_portrait_surface = fit_bgr_to_surface(prepared.image_bgr, area.w, area.h)
        if self.id_captured_count < self.id_count:
            self._set_state(St.CONSENT)
        else:
            self._set_state(St.ID_PROCESSING)

    def _receive_id_portrait(self, index, result):
        if index >= len(self.id_items) or self.id_items[index]["status"] != "pending":
            return
        item = self.id_items[index]
        if not result.ok or result.image_bgr is None:
            booth_stats.increment("ai_failure")
            if result.refused:
                booth_stats.increment("ai_refused")
            item["status"] = "failed"
            item["error"] = "AI 변환에 실패했어요."
            return

        import id_portrait

        try:
            portrait = id_portrait.finalize_portrait(item["prepared"], result.image_bgr)
        except ValueError as exc:
            booth_stats.increment("ai_failure")
            log.warning("증명사진 %d번 합성 실패: %s", index + 1, exc)
            item["status"] = "failed"
            item["error"] = "얼굴 합성 확인이 필요해요."
            return
        except Exception as exc:
            log.warning(f"증명사진 얼굴 합성 실패: {type(exc).__name__}")
            booth_stats.increment("ai_failure")
            item["status"] = "failed"
            item["error"] = "얼굴 합성에 실패했어요."
            return

        item["result"] = portrait
        item["status"] = "done"
        booth_stats.increment("ai_success")

    def _accept_id_portrait(self):
        if self.id_captured_count != self.id_count or any(item["status"] != "done" for item in self.id_items):
            return
        self.id_portraits = [item["result"] for item in sorted(self.id_items, key=lambda item: item["slot"])]
        for index, photo in enumerate(self.id_portraits):
            path = PHOTOS_DIR / f"{self.session_id}-{index}.id-source.jpg"
            if not cv2.imwrite(str(path), photo, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                log.warning("최근 증명사진을 저장하지 못했습니다: %s", path.name)
        count = len(self.id_portraits)
        self.id_print_choices = ([0] * PHOTO_COUNT if count == 1
                                 else list(range(count)) + [-1] * (PHOTO_COUNT - count))
        self.captured_bgr = [self.id_portraits[index] if index >= 0 else None
                             for index in self.id_print_choices]
        self.print_copies = 1
        self._set_state(St.ID_REVIEW)
        self._start_compose()

    def _open_id_picker(self, slot):
        """현재 촬영본과 저장된 최근 인화지의 개별 사진을 선택합니다."""
        options = [photo.copy() for photo in self.id_portraits]
        seen = [cv2.resize(photo, (48, 69)).astype(np.int16) for photo in options]
        for path in sorted(PHOTOS_DIR.glob("*.id-source.jpg"),
                           key=lambda path: path.stat().st_mtime, reverse=True)[:32]:
            photo = cv2.imread(str(path))
            if photo is None:
                continue
            key = cv2.resize(photo, (48, 69)).astype(np.int16)
            if not any(np.mean(np.abs(key - previous)) < 3 for previous in seen):
                seen.append(key)
                options.append(photo)
        from composer import PRINT_W, PRINT_H, ID_PHOTO_W, ID_PHOTO_H, ID_PHOTO_GAP
        left = (PRINT_W - 2 * ID_PHOTO_W - ID_PHOTO_GAP) // 2
        top = (PRINT_H - 2 * ID_PHOTO_H - ID_PHOTO_GAP) // 2
        sheets = sorted(PHOTOS_DIR.glob("*.id.jpg"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in sheets[:12]:
            if self.strip_path and path == self.strip_path:
                continue
            sheet = cv2.imread(str(path))
            if sheet is None or sheet.shape[:2] != (PRINT_H, PRINT_W):
                continue
            for index in range(PHOTO_COUNT):
                x = left + (index % 2) * (ID_PHOTO_W + ID_PHOTO_GAP)
                y = top + (index // 2) * (ID_PHOTO_H + ID_PHOTO_GAP)
                photo = sheet[y:y + ID_PHOTO_H, x:x + ID_PHOTO_W].copy()
                if float(np.mean(photo)) > 253:
                    continue
                key = cv2.resize(photo, (48, 69)).astype(np.int16)
                if not any(np.mean(np.abs(key - previous)) < 3 for previous in seen):
                    seen.append(key)
                    options.append(photo)
        self.id_picker_slot = slot
        self.id_picker_page = 0
        self.id_picker_options = options + [None]
        self.state_time = time.time()

    def _id_picker_rects(self):
        gap = 14
        width = min(160, (SCREEN_W - 100 - gap * 3) // 4)
        height = min(int(width * 1.25), (self.btn_id_primary.rect.top - 200) // 2)
        x = (SCREEN_W - (4 * width + 3 * gap)) // 2
        return [pygame.Rect(x + (i % 4) * (width + gap),
                            150 + (i // 4) * (height + gap), width, height)
                for i in range(8)]

    def _handle_id_click(self, pos):
        if self.state == St.ID_REVIEW and self.id_picker_slot is not None:
            if self.btn_id_cancel.hit(pos):
                self.id_picker_slot = None
                self.state_time = time.time()
                return
            if self.btn_id_secondary.hit(pos):
                self.id_picker_page = (self.id_picker_page - 1) % max(1, (len(self.id_picker_options) + 7) // 8)
                return
            if self.btn_id_primary.hit(pos):
                self.id_picker_page = (self.id_picker_page + 1) % max(1, (len(self.id_picker_options) + 7) // 8)
                return
            for i, rect in enumerate(self._id_picker_rects()):
                selected = self.id_picker_page * 8 + i
                if rect.collidepoint(pos) and selected < len(self.id_picker_options):
                    photo = self.id_picker_options[selected]
                    if photo is None:
                        self.id_print_choices[self.id_picker_slot] = -1
                    else:
                        self.id_portraits.append(photo)
                        self.id_print_choices[self.id_picker_slot] = len(self.id_portraits) - 1
                    self.captured_bgr = [self.id_portraits[i] if i >= 0 else None for i in self.id_print_choices]
                    self.id_picker_slot = None
                    self.strip_path = None
                    self.strip_surface = None
                    self._start_compose()
                    return
            return
        if self.btn_id_cancel.hit(pos) and not self._composing:
            self._reset_session()
            self._set_state(St.IDLE)
            return
        if self.state == St.CONSENT:
            self.ai_screen_input_at = time.time()
            if self.btn_id_secondary.hit(pos):
                self.id_notice = "동의하는 다음 참가자가 이 순서에서 촬영할 수 있어요."
            elif self.btn_id_primary.hit(pos):
                if self._ai_available() and self._ai_concept() is not None:
                    self.id_notice = ""
                    self._begin_countdown(new_session=False)
                else:
                    self.id_notice = "AI 연결을 확인한 뒤 다시 시도해 주세요."
            return
        if self.state == St.ID_SETUP:
            for count, button in self.btn_id_counts:
                if button.hit(pos):
                    self.id_count = count
                    self.state_time = time.time()
                    return
            if self.btn_id_primary.hit(pos):
                if self._ai_available():
                    self._open_concept_select()
                else:
                    self.id_notice = "AI 연결을 확인한 뒤 다시 시도해 주세요."
            return
        if self.state == St.ID_PROCESSING:
            if self.btn_id_primary.hit(pos):
                if all(item["status"] == "done" for item in self.id_items):
                    self._accept_id_portrait()
                else:
                    for index, item in enumerate(self.id_items):
                        if item["status"] == "failed":
                            try:
                                self._submit_id_portrait(index)
                            except Exception as exc:
                                log.warning("증명사진 재변환 요청 실패: %s", type(exc).__name__)
            return
        if self.state == St.ID_RESULT and self.btn_id_secondary.hit(pos):
            self._retry_id_capture()
            return
        if self.state == St.ID_REVIEW and not self._composing:
            for index, rect in enumerate(self._id_review_choice_rects()):
                if rect.collidepoint(pos):
                    self._open_id_picker(index)
                    return
        if not self.btn_id_primary.hit(pos):
            return
        if self.state == St.ID_CAPTURE:
            self.id_notice = ""
            self._begin_countdown(new_session=False)
        elif self.state == St.ID_RESULT:
            self._accept_id_portrait()
        elif self.state == St.ID_REVIEW and not self._composing:
            if not self.strip_path or not self.strip_path.exists():
                self.strip_path = None
                self.strip_surface = None
                self._start_compose()
            elif not any(index >= 0 for index in self.id_print_choices):
                self._set_review_notice("인쇄할 사진을 선택해 주세요.", 5.0)
            elif self._can_start_printing():
                self._start_printing()

    def _id_review_choice_rects(self):
        if not self.strip_surface:
            return []
        preview = self.strip_surface.get_rect(center=self.id_preview_area.center)
        from composer import PRINT_W, PRINT_H, ID_PHOTO_W, ID_PHOTO_H, ID_PHOTO_GAP
        left = (PRINT_W - 2 * ID_PHOTO_W - ID_PHOTO_GAP) // 2
        top = (PRINT_H - 2 * ID_PHOTO_H - ID_PHOTO_GAP) // 2
        sx, sy = preview.w / PRINT_W, preview.h / PRINT_H
        return [
            pygame.Rect(preview.x + int((left + (index % 2) * (ID_PHOTO_W + ID_PHOTO_GAP)) * sx),
                        preview.y + int((top + (index // 2) * (ID_PHOTO_H + ID_PHOTO_GAP)) * sy),
                        int(ID_PHOTO_W * sx), int(ID_PHOTO_H * sy))
            for index in range(len(self.id_print_choices))
        ]

    def _id_stage_buttons(self):
        buttons = [self.btn_id_cancel]
        if self.state == St.ID_SETUP:
            buttons.extend(button for _, button in self.btn_id_counts)
        elif self.state in {St.CONSENT, St.ID_RESULT}:
            buttons.append(self.btn_id_secondary)
        if self.state in {St.CONSENT, St.ID_SETUP, St.ID_CAPTURE, St.ID_PROCESSING, St.ID_RESULT, St.ID_REVIEW}:
            buttons.append(self.btn_id_primary)
        return buttons

    def _draw_id_stage(self):
        elapsed = time.time() - self.state_time
        if self.state == St.ID_REVIEW and self.id_picker_slot is not None:
            self.screen.fill(C_BG)
            draw_text(self.screen, f"{self.id_picker_slot + 1}번 칸에 넣을 사진을 선택하세요",
                      self.f_large, C_PINK, SCREEN_W // 2, 55, anchor="center", max_width=SCREEN_W - 50)
            for i, rect in enumerate(self._id_picker_rects()):
                index = self.id_picker_page * 8 + i
                if index >= len(self.id_picker_options):
                    break
                draw_card(self.screen, rect, radius=12)
                photo = self.id_picker_options[index]
                if photo is None:
                    draw_text(self.screen, "빈 칸", self.f_small, C_GRAY, *rect.center, anchor="center")
                else:
                    surf = fit_bgr_to_surface(photo, rect.w - 12, rect.h - 12)
                    self.screen.blit(surf, surf.get_rect(center=rect.center))
            self.btn_id_cancel.label = "취소"
            self.btn_id_secondary.label = "이전"
            self.btn_id_primary.label = "다음"
            for button in (self.btn_id_cancel, self.btn_id_secondary, self.btn_id_primary):
                self._draw_fit_button(button, self.f_medium)
            self.btn_id_cancel.label = "처음으로"
            return
        if self.state in {St.ID_SETUP, St.ID_CAPTURE, St.ID_RESULT, St.ID_REVIEW}:
            if elapsed > REVIEW_TIMEOUT and not self._composing:
                if self.state in {St.ID_CAPTURE, St.ID_RESULT} and self.id_items:
                    self._retry_id_capture(state=St.CONSENT)
                    return
                self._reset_session()
                self._set_state(St.IDLE)
                return

        self.screen.fill(C_BG)
        cx = SCREEN_W // 2
        top = 40 if self.compact_ui else 68
        subtitle_y = 90 if self.compact_ui else 132
        area = self.id_preview_area
        notice = self.id_notice
        primary = "다음"
        portrait = self.id_portrait_surface
        participant = self.id_captured_count + 1
        subtitle = f"{self.id_count}명 중 {participant}번째 참가자" if self.id_count > 1 else "1명 · 한 번 촬영"

        if self.state == St.ID_SETUP:
            title = "몇 명이 찍나요?"
            subtitle = "한 명씩 찍고 34 × 49 mm 사진을 한 장에 모아 인쇄해요."
            first = self.btn_id_counts[0][1].rect
            draw_text(self.screen, "1명도, 최대 4명도 찍을 수 있어요.", self.f_medium, C_DARK,
                      cx, first.top - 42, anchor="center", max_width=SCREEN_W - 50)
            self._draw_selected_button_group(self.id_count, self.btn_id_counts, C_BLUE,
                                             self.f_medium)
            draw_text(self.screen, "복장을 여러 개 고르면 한 장에 네 가지 컷을 배치해요.",
                      self.f_small, C_DARK, cx, first.bottom + 36, anchor="center",
                      max_width=SCREEN_W - 50)
        elif self.state == St.ID_REVIEW:
            title = "인쇄 미리보기"
            filled = len(self.captured_bgr)
            subtitle = f"사진을 눌러 바꾸세요 · 34 × 49 mm · {filled}장"
            portrait = self.strip_surface
            primary = "1장 인쇄" if self.strip_path else "다시 준비"
            if self._composing:
                notice = "인쇄할 사진을 배치하고 있어요."
                primary = "준비 중"
            elif time.time() < self.review_notice_until:
                notice = self.review_notice
        elif self.state == St.ID_PROCESSING:
            title = "복장을 합성하고 있어요"
            done = sum(item["status"] == "done" for item in self.id_items)
            failed = sum(item["status"] == "failed" for item in self.id_items)
            total = len(self.ai_slot_concepts)
            subtitle = f"{done} / {total}컷 완료"
            primary = "다시 변환" if failed else ("결과 확인" if done == total else "변환 중")
            portrait = None
            top = max(70, area.centery - 110)
            subtitle_y = top + (65 if self.compact_ui else 90)
            for item in self.id_items:
                slot = item["slot"]
                label = {"pending": "변환 중", "done": "완료", "failed": "실패"}[item["status"]]
                name = self.ai_slot_concepts[slot].name
                draw_text(self.screen, f"{slot + 1}번 · {name} · {label}", self.f_medium, C_DARK,
                          cx, subtitle_y + 55 + slot * 45, anchor="center")
        elif self.state == St.ID_RESULT:
            title = "이 사진을 사용할까요?"
            primary = "이 사진 사용"
        else:
            title = "한 명씩 정면을 봐 주세요"
            primary = "다시 찍기" if self.id_notice else "촬영 시작"
            frame = self._id_frame()
            if frame is not None:
                if PREVIEW_MIRROR:
                    frame = cv2.flip(frame, 1)
                crop = center_crop_bgr_to_aspect(frame, 34 / 49)
                portrait = fit_bgr_to_surface(crop, area.w, area.h)
            else:
                notice = "카메라 연결을 확인하고 있어요."

        draw_text(self.screen, title, self.f_big, C_PINK, cx, top,
                  anchor="center", max_width=SCREEN_W - 50)
        draw_text(self.screen, subtitle, self.f_small, C_DARK, cx, subtitle_y,
                  anchor="center", max_width=SCREEN_W - 50)
        if portrait is not None:
            rect = portrait.get_rect(center=area.center)
            draw_card(self.screen, rect.inflate(14, 14))
            self.screen.blit(portrait, rect)
            if self.state == St.COUNTDOWN:
                draw_text(self.screen, self.cd_val, self.f_huge, C_WHITE, rect.centerx,
                          rect.centery, anchor="center", shadow=True, shadow_color=C_DARK)
        if notice:
            draw_text(self.screen, notice, self.f_small, C_DARK, cx,
                      self.btn_id_primary.rect.top - 20, anchor="center",
                      max_width=SCREEN_W - 50)
        self.btn_id_primary.label = primary
        self.btn_id_primary.set_style(C_GRAY if self._composing else C_BLUE, C_WHITE)
        self.btn_id_cancel.draw(self.screen, self.f_medium)
        if self.state == St.ID_RESULT:
            self.btn_id_secondary.label = "다시 찍기"
            self._draw_fit_button(self.btn_id_secondary, self.f_medium)
        if self.state in {St.ID_SETUP, St.ID_CAPTURE, St.ID_PROCESSING, St.ID_RESULT, St.ID_REVIEW}:
            self._draw_fit_button(self.btn_id_primary, self.f_medium)

    # ── AI 화면 그리기 ──
    def _draw_check(self, cx, cy, r):
        pygame.draw.circle(self.screen, C_GREEN, (cx, cy), r)
        pygame.draw.circle(self.screen, C_WHITE, (cx, cy), r, 2)
        pts = [(cx - r * 0.45, cy), (cx - r * 0.1, cy + r * 0.38), (cx + r * 0.5, cy - r * 0.35)]
        pygame.draw.lines(self.screen, C_WHITE, False, pts, max(2, r // 5))

    def _draw_pill(self, text, font, color, cx, cy, text_color=C_WHITE, pad=14, h=None):
        tw, th = font.size(text)
        h = h or th + 10
        rect = pygame.Rect(0, 0, tw + pad * 2, h)
        rect.center = (int(cx), int(cy))
        draw_rrect(self.screen, color, rect, radius=h // 2)
        draw_text(self.screen, text, font, text_color, rect.centerx, rect.centery, anchor="center")
        return rect

    def _draw_fit_button(self, btn, font=None):
        font = font or self.f_small
        if font.size(btn.label)[0] > btn.rect.width - 24:
            font = self.f_tiny
        btn.draw(self.screen, font)
        if self._composing and self.state == St.REVIEW:
            dim = pygame.Surface(btn.rect.size, pygame.SRCALPHA)
            dim.fill((180, 180, 180, 120))
            self.screen.blit(dim, btn.rect.topleft)

    def _draw_ai_badge(self, rect, i, compact: bool):
        use_ai = (i < len(self.ai_results) and self.ai_results[i] is not None
                  and not self.ai_use_original[i])
        font = self.f_tiny if compact else self.f_small
        text = "AI" if use_ai else "원본"
        tw, th = font.size(text)
        w, h = tw + (14 if compact else 22), th + 6
        badge = pygame.Rect(rect.right - w - 4, rect.y + 4, w, h)
        draw_rrect(self.screen, C_PINK if use_ai else C_GRAY, badge, radius=h // 2)
        draw_text(self.screen, text, font, C_WHITE, badge.centerx, badge.centery, anchor="center")

    def _draw_idle_start_buttons(self):
        font = self.f_medium if self.compact_ui else self.f_large
        for btn, color in ((self.btn_start_ai, C_PINK), (self.btn_start_id, C_BLUE)):
            if self._ai_available():
                btn.set_style(color, C_WHITE)
                btn.draw(self.screen, font)
            else:
                btn.set_style((185, 185, 192), C_WHITE)
                label = btn.label
                btn.label = ""
                btn.draw(self.screen, font)
                btn.label = label
                off = 12 if self.compact_ui else 16
                draw_text(self.screen, label, self.f_medium, C_WHITE,
                          btn.rect.centerx, btn.rect.centery - off, anchor="center",
                          max_width=btn.rect.width - 24)
                draw_text(self.screen, "AI 연결 확인 중", self.f_tiny, C_WHITE,
                          btn.rect.centerx, btn.rect.centery + off + 4, anchor="center",
                          max_width=btn.rect.width - 24)
        badge_x = self.btn_start_id.rect.centerx
        badge_y = self.btn_start_id.rect.top - 25
        self._draw_pill("키링", self.f_tiny if self.compact_ui else self.f_small,
                        C_DARK, badge_x, badge_y, h=32)
        pygame.draw.polygon(self.screen, C_DARK, [
            (badge_x - 6, badge_y + 14), (badge_x + 6, badge_y + 14), (badge_x, badge_y + 23),
        ])
        if self.idle_notice and time.time() < self.idle_notice_until:
            tw = min(SCREEN_W - 80, self.f_small.size(self.idle_notice)[0] + 48)
            th = self.f_small.get_height() + 22
            toast = pygame.Rect(0, 0, tw, th)
            toast.midbottom = (SCREEN_W // 2, self.btn_start_ai.rect.y - 16)
            bg = pygame.Surface(toast.size, pygame.SRCALPHA)
            pygame.draw.rect(bg, (40, 30, 40, 215), bg.get_rect(), border_radius=th // 2)
            self.screen.blit(bg, toast.topleft)
            draw_text(self.screen, self.idle_notice, self.f_small, C_WHITE,
                      toast.centerx, toast.centery, anchor="center", max_width=tw - 32)

    def _draw_select_timer(self):
        if self.state == St.CONSENT and self.session_mode == "id" and self.id_items:
            return
        last = max(self.ai_screen_input_at, self.state_time)
        remain = max(0, int(AI_SELECT_TIMEOUT - (time.time() - last)))
        draw_text(self.screen, f"{remain}초 후 처음 화면으로", self.f_tiny, C_GRAY,
                  SCREEN_W - (110 if self.compact_ui else 130), SCREEN_H - (10 if self.compact_ui else 18),
                  anchor="center", max_width=230)

    def _concept_thumb_surface(self, concept, size):
        if concept.thumbnail_path is None:
            return None
        key = (concept.id, size)
        if key in self._concept_thumb_cache:
            return self._concept_thumb_cache[key]
        surf = None
        try:
            img = pygame.image.load(str(concept.thumbnail_path)).convert()
            iw, ih = img.get_size()
            scale = max(size[0] / iw, size[1] / ih)
            img = pygame.transform.smoothscale(img, (math.ceil(iw * scale), math.ceil(ih * scale)))
            rect = pygame.Rect(0, 0, *size)
            rect.center = (img.get_width() // 2, img.get_height() // 2)
            surf = img.subsurface(rect).copy()
        except Exception as e:
            log.warning(f"AI 콘셉트 썸네일 로드 실패 ({concept.id}): {type(e).__name__}")
        self._concept_thumb_cache[key] = surf
        return surf

    def _draw_concept_card(self, btn, concept, selected: bool):
        import ai_concepts
        r = btn.rect
        compact = self.compact_ui
        draw_card(self.screen, r, radius=18)
        pad = 10 if compact else 14
        name_h = 40 if compact else 52
        desc_h = 0 if compact else 38
        thumb = pygame.Rect(r.x + pad, r.y + pad, r.w - pad * 2, r.h - pad * 2 - name_h - desc_h)
        surf = self._concept_thumb_surface(concept, thumb.size)
        if surf is not None:
            self.screen.blit(surf, thumb.topleft)
        else:
            color = ai_concepts.CATEGORY_COLORS.get(concept.category, (220, 160, 40))
            draw_rrect(self.screen, color, thumb, radius=14)
            draw_text(self.screen, concept.name, self.f_large, C_WHITE,
                      thumb.centerx, thumb.centery - (8 if compact else 14), anchor="center",
                      max_width=thumb.w - 20)
            draw_text(self.screen, CONCEPT_CATEGORY_NAMES.get(concept.category, ""),
                      self.f_tiny if compact else self.f_small, (255, 240, 245),
                      thumb.centerx, thumb.centery + (24 if compact else 36), anchor="center",
                      max_width=thumb.w - 20)
        draw_text(self.screen, concept.name, self.f_small if compact else self.f_medium, C_DARK,
                  r.centerx, thumb.bottom + name_h // 2 + 2, anchor="center", max_width=r.w - 20)
        if desc_h and concept.description:
            draw_text(self.screen, concept.description, self.f_tiny, C_GRAY,
                      r.centerx, thumb.bottom + name_h + desc_h // 2 - 6, anchor="center",
                      max_width=r.w - 24)
        if selected:
            pygame.draw.rect(self.screen, C_PINK, r.inflate(6, 6), 6, border_radius=21)
            self._draw_check(r.right - 22, r.y + 22, 17 if compact else 22)

    def _draw_page_arrow(self, btn, direction: int):
        draw_rrect(self.screen, C_WHITE, btn.rect, radius=14, border=2, bcol=C_PINK)
        cx, cy = btn.rect.center
        s = min(btn.rect.w, btn.rect.h) // 4
        pts = [(cx - s * direction, cy - s * 1.4), (cx + s * direction, cy), (cx - s * direction, cy + s * 1.4)]
        pygame.draw.polygon(self.screen, C_PINK, pts)

    def _draw_concept_select(self):
        self.screen.fill(C_BG)
        compact = self.compact_ui
        cx = SCREEN_W // 2
        is_id = self.session_mode == "id"
        title = "어떤 복장으로 찍을까요?" if is_id else "어떤 모습으로 변신할까요?"
        count = len(self.concept_selected)
        subtitle = (f"복장 1~4개를 골라 주세요. 증명사진은 밝은 단색 배경으로 나와요  ({count} / 4)" if is_id else
                    f"복장 1~4개를 고르면 네 컷에 무작위로 배정돼요  ({count} / 4)")
        draw_text(self.screen, title, self.f_big, C_PINK,
                  cx, 36 if compact else 70, anchor="center",
                  shadow=True, shadow_color=C_LPINK, shadow_off=3, max_width=SCREEN_W - 80)
        draw_text(self.screen, subtitle,
                  self.f_tiny if compact else self.f_small, C_GRAY,
                  cx, 78 if compact else 134, anchor="center", max_width=SCREEN_W - 80)

        base = self.concept_page * self.concept_per_page
        for i, btn in enumerate(self.concept_card_buttons):
            idx = base + i
            if idx >= len(self.ai_concepts):
                break
            concept = self.ai_concepts[idx]
            self._draw_concept_card(btn, concept, concept.id in self.concept_selected)

        pages = self._concept_pages()
        if pages > 1:
            self._draw_page_arrow(self.btn_concept_prev, -1)
            self._draw_page_arrow(self.btn_concept_next, 1)
            draw_text(self.screen, f"{self.concept_page + 1} / {pages}", self.f_tiny, C_GRAY,
                      cx, (self.concept_grid_bottom + self.btn_concept_back.rect.y) // 2,
                      anchor="center")

        font = self.f_medium if compact else self.f_large
        self.btn_concept_back.draw(self.screen, font)
        if self.concept_selected:
            self.btn_concept_confirm.set_style(C_PINK, C_WHITE)
        else:
            self.btn_concept_confirm.set_style((200, 200, 206), C_WHITE)
        self.btn_concept_confirm.draw(self.screen, font)
        self._draw_select_timer()

    def _consent_lines(self) -> list[str]:
        text = AI_CONSENT_TEXT or DEFAULT_AI_CONSENT_TEXT
        return [
            line.strip().replace("{days}", str(PHOTO_RETENTION_DAYS))
            for line in text.split("|") if line.strip()
        ]

    def _draw_consent(self):
        self.screen.fill(C_BG)
        compact = self.compact_ui
        cx = SCREEN_W // 2
        is_id = self.session_mode == "id"
        title = "AI 변신 전에 확인해 주세요"
        if is_id:
            title = f"{self.id_captured_count + 1}번째 참가자 동의" if self.id_count > 1 else "AI 증명사진 촬영 동의"
        draw_text(self.screen, title, self.f_big, C_PINK,
                  cx, 40 if compact else 80, anchor="center", max_width=SCREEN_W - 80)
        concepts = self._selected_concepts()
        if concepts:
            detail = " / ".join(c.name for c in concepts)
            self._draw_pill(detail, self.f_small if compact else self.f_medium,
                            C_PINK, cx, 92 if compact else 166)
        if is_id:
            w = min(1400, SCREEN_W - 80)
            y = 124 if compact else 220
            card = pygame.Rect((SCREEN_W - w) // 2, y, w,
                               self.btn_id_primary.rect.top - 44 - y)
        elif compact:
            card = pygame.Rect(40, 124, SCREEN_W - 80, self.btn_consent_no.rect.y - 24 - 124)
        else:
            card = pygame.Rect(260, 220, 1400, 600)
        draw_card(self.screen, card, radius=18)
        lines = self._consent_lines()
        if is_id and self.id_count > 1:
            lines.append("다른 참가자의 사진과 같은 인화지에 출력됩니다.")
            if self.id_items:
                done = sum(item["status"] == "done" for item in self.id_items)
                lines.append(f"앞선 참가자 변환 {done} / {len(self.id_items)}컷 완료")
        step = min(110 if not compact else 70, (card.h - 40) // max(1, len(lines)))
        y = card.y + 20 + step // 2 + (card.h - 40 - step * len(lines)) // 2
        for line in lines:
            font = self.f_medium
            if font.size(line)[0] > card.w - 80:
                font = self.f_small
            draw_text(self.screen, f"· {line}", font, C_DARK, cx, y, anchor="center",
                      max_width=card.w - 60)
            y += step
        font = self.f_medium if compact else self.f_large
        if is_id:
            if self.id_notice:
                draw_text(self.screen, self.id_notice, self.f_small, C_DARK, cx,
                          self.btn_id_primary.rect.top - 20, anchor="center",
                          max_width=SCREEN_W - 50)
            self.btn_id_secondary.label = "동의 안 함"
            self.btn_id_primary.label = "동의하고 촬영"
            self.btn_id_primary.set_style(C_BLUE, C_WHITE)
            for btn in self._id_stage_buttons():
                self._draw_fit_button(btn, self.f_medium)
        else:
            self.btn_consent_no.draw(self.screen, font)
            self.btn_consent_yes.draw(self.screen, font)
        self._draw_select_timer()

    def _ai_slot_surface(self, idx: int, w: int, h: int):
        if idx >= len(self.captured_raw_bgr):
            return None
        if self.ai_status[idx] == "done" and self.ai_results[idx] is not None:
            img, kind = self.ai_results[idx], "ai"
        else:
            img, kind = self.captured_raw_bgr[idx], "raw"
        key = (idx, kind, id(img), w, h)
        surf = self._ai_slot_cache.get(key)
        if surf is None:
            if kind == "raw":
                import ai_transform
                img = ai_transform.crop_to_aspect(img, ai_transform.slot_aspect_for(img))
            surf = fit_bgr_to_surface(img, w, h)
            self._ai_slot_cache = {k: v for k, v in self._ai_slot_cache.items() if k[0] != idx}
            self._ai_slot_cache[key] = surf
        return surf

    def _draw_ai_processing(self):
        self.screen.fill(C_BG)
        compact = self.compact_ui
        cx = SCREEN_W // 2
        concept = self._ai_concept()
        draw_text(self.screen, "AI가 변신 중이에요", self.f_big, C_PINK,
                  cx, 44 if compact else 90, anchor="center",
                  shadow=True, shadow_color=C_LPINK, shadow_off=3, max_width=SCREEN_W - 80)
        if concept is not None:
            detail = "컷마다 선택한 복장으로 변신해요" if len(self.concept_selected) > 1 else f"{concept.name} 변신"
            draw_text(self.screen, detail, self.f_medium, C_DARK,
                      cx, 92 if compact else 162, anchor="center", max_width=SCREEN_W - 80)

        n = PHOTO_COUNT
        if compact:
            gap = 20
            slot_w = (SCREEN_W - 80 - gap * (n - 1)) // n
            slot_h = min(int(slot_w * 1.33), SCREEN_H - 330)
            y0 = 124
        else:
            gap, slot_w, slot_h, y0 = 40, 360, 480, 220
        x0 = (SCREEN_W - (n * slot_w + (n - 1) * gap)) // 2
        badge_font = self.f_tiny if compact else self.f_small
        dots = "." * (int(time.time() * 2.5) % 4)
        for i in range(n):
            r = pygame.Rect(x0 + i * (slot_w + gap), y0, slot_w, slot_h)
            draw_card(self.screen, r, radius=16)
            inner = r.inflate(-16, -16)
            st = self.ai_status[i] if i < len(self.ai_status) else "idle"
            surf = self._ai_slot_surface(i, inner.w, inner.h)
            if surf is not None:
                sw, sh = surf.get_size()
                pos = (inner.x + (inner.w - sw) // 2, inner.y + (inner.h - sh) // 2)
                self.screen.blit(surf, pos)
                if st == "pending":
                    dim = pygame.Surface((sw, sh), pygame.SRCALPHA)
                    dim.fill((255, 255, 255, 110))
                    self.screen.blit(dim, pos)
            if st == "done":
                text, color = "AI 완료", C_GREEN
                self._draw_check(r.right - 24, r.y + 24, 16 if compact else 22)
            elif st == "pending":
                text, color = f"변환 중{dots}", C_PINK
            elif st == "idle":
                text, color = "대기", C_LGRAY
            else:
                text, color = "원본 사용", C_GRAY
            pill_h = 30 if compact else 42
            self._draw_pill(text, badge_font, color, r.centerx, r.bottom - 14 - pill_h // 2, h=pill_h)
            draw_rrect(self.screen, C_PINK, (r.x + 8, r.y + 8, pill_h, pill_h), radius=pill_h // 2)
            draw_text(self.screen, str(i + 1), badge_font, C_WHITE,
                      r.x + 8 + pill_h // 2, r.y + 8 + pill_h // 2, anchor="center")
            concept = self._ai_concept(i)
            if concept is not None:
                name_rect = pygame.Rect(r.x + pill_h + 14, r.y + 8,
                                        r.w - 2 * (pill_h + 14), pill_h)
                draw_rrect(self.screen, C_WHITE, name_rect, radius=12)
                draw_text(self.screen, concept.name, badge_font, C_DARK,
                          name_rect.centerx, name_rect.centery, anchor="center",
                          max_width=name_rect.w - 10)

        settled = sum(1 for st in self.ai_status if st not in ("pending", "idle"))
        bar_w = min(1000, SCREEN_W - 160)
        bar_h = 20 if compact else 28
        bar_y = y0 + slot_h + (36 if compact else 60)
        bar = pygame.Rect(cx - bar_w // 2, bar_y, bar_w, bar_h)
        draw_rrect(self.screen, C_LGRAY, bar, radius=bar_h // 2)
        if settled:
            draw_rrect(self.screen, C_PINK, (bar.x, bar.y, int(bar_w * settled / n), bar_h),
                       radius=bar_h // 2)
        elapsed = int(time.time() - self.ai_processing_started_at)
        draw_text(self.screen, f"{settled} / {n} 완료 · {elapsed}초 경과", self.f_medium, C_DARK,
                  cx, bar.bottom + (30 if compact else 44), anchor="center", max_width=SCREEN_W - 80)
        draw_text(self.screen, "보통 20~30초 걸려요. 잠시만 기다려 주세요!", self.f_small, C_GRAY,
                  cx, bar.bottom + (64 if compact else 96), anchor="center", max_width=SCREEN_W - 80)


    def _audit_button_layout(self):
        review_common = [
            self.btn_review_home,
            self.btn_print,
            self.btn_qr,
            self.btn_retake,
            self.btn_retake_selected,
            self.btn_copies_minus,
            self.btn_copies_plus,
            *(btn for _id, btn in self.review_frame_buttons),
            *(btn for _id, btn in self.review_filter_buttons),
        ]
        groups = {
            "idle": [
                self.btn_start_ai,
                self.btn_start_id,
                self.btn_status,
            ],
            "id_setup": [self.btn_id_cancel, self.btn_id_primary,
                         *(button for _, button in self.btn_id_counts)],
            "id_result": [self.btn_id_cancel, self.btn_id_secondary, self.btn_id_primary],
            "concept_select": [
                *self.concept_card_buttons,
                self.btn_concept_prev,
                self.btn_concept_next,
                self.btn_concept_back,
                self.btn_concept_confirm,
            ],
            "consent": [
                self.btn_consent_no,
                self.btn_consent_yes,
            ],
            "review_ai": [
                *review_common,
                self.btn_ai_toggle,
                self.btn_ai_regen,
            ],
            "review": [
                self.btn_review_home,
                self.btn_print,
                self.btn_qr,
                self.btn_retake,
                self.btn_retake_selected,
                self.btn_copies_minus,
                self.btn_copies_plus,
                *(btn for _id, btn in self.review_frame_buttons),
                *(btn for _id, btn in self.review_orientation_buttons),
                *(btn for _id, btn in self.review_filter_buttons),
            ],
            "status": [self.btn_status_close],
        }
        for group_name, buttons in groups.items():
            for btn in buttons:
                if not pygame.Rect(0, 0, SCREEN_W, SCREEN_H).contains(btn.rect):
                    log.warning(f"UI 버튼 화면 밖: {group_name} '{btn.label}' {btn.rect}")
            for i, first in enumerate(buttons):
                for second in buttons[i + 1:]:
                    if first.rect.colliderect(second.rect):
                        log.warning(
                            "UI 버튼 좌표 겹침: "
                            f"{group_name} '{first.label}' {first.rect} / "
                            f"'{second.label}' {second.rect}"
                        )

    def _set_capture_orientation(self, orientation: str):
        if orientation not in {"portrait", "landscape"}:
            return False
        if self.capture_orientation != orientation:
            log.info(f"촬영 방향 변경: {self.capture_orientation} → {orientation}")
            self.capture_orientation = orientation
            return True
        self.capture_orientation = orientation
        return False

    def _set_frame_theme(self, theme_id: str):
        if theme_id not in composer.FRAME_THEMES:
            return False
        if self.selected_frame_theme != theme_id:
            log.info(f"프레임 변경: {self.selected_frame_theme} → {theme_id}")
            self.selected_frame_theme = theme_id
            return True
        self.selected_frame_theme = theme_id
        return False

    def _set_filter(self, filter_id: str):
        if filter_id not in FILTERS:
            return False
        if self.selected_filter != filter_id:
            log.info(f"필터 변경: {self.selected_filter} → {filter_id}")
            self.selected_filter = filter_id
            return True
        self.selected_filter = filter_id
        return False

    def _set_print_copies(self, copies: int):
        copies = max(1, min(copies, MAX_PRINT_COPIES))
        if copies != self.print_copies:
            log.info(f"인쇄 매수 변경: {self.print_copies} → {copies}")
        self.print_copies = copies

    def _return_to_review_from_qr(self):
        if self.strip_path and self.strip_path.exists() and len(self.captured_raw_bgr) == PHOTO_COUNT:
            self._set_state(St.REVIEW)
        else:
            self._set_state(St.IDLE)
            self._reset_session()

    def _photo_files(self):
        if not PHOTOS_DIR.exists():
            return []
        files = []
        for path in PHOTOS_DIR.glob("*.jpg"):
            try:
                if path.is_file():
                    files.append((path.stat().st_mtime, path))
            except OSError:
                continue
        return [path for _mtime, path in sorted(files)]

    def _cleanup_ai_dirs(self, now: float) -> tuple[int, int]:
        """photos/ai/{session_id}/ 폴더를 보관일 기준(폴더 mtime)으로 삭제."""
        ai_dir = PHOTOS_DIR / "ai"
        if PHOTO_RETENTION_DAYS <= 0 or not ai_dir.is_dir():
            return 0, 0
        cutoff = now - PHOTO_RETENTION_DAYS * 86400
        deleted = errors = 0
        try:
            entries = list(ai_dir.iterdir())
        except OSError as e:
            log.warning(f"AI 폴더 정리 실패: {ai_dir} ({e})")
            return 0, 1
        for path in entries:
            try:
                if path.is_dir() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                    shutil.rmtree(path)
                    deleted += 1
            except Exception as e:
                errors += 1
                log.warning(f"AI 폴더 정리 실패: {path} ({e})")
        return deleted, errors

    def _cleanup_old_photos(self, force: bool = False):
        now = time.time()
        if not force and now - self.cleanup_checked_at < CLEANUP_INTERVAL_SECS:
            return

        self.cleanup_checked_at = now
        deleted = 0
        errors = 0
        files = self._photo_files()
        protected_paths = set()
        if self.strip_path:
            try:
                protected_paths.add(self.strip_path.resolve())
            except Exception:
                protected_paths.add(self.strip_path)

        def _protected(path):
            try:
                return path.resolve() in protected_paths
            except Exception:
                return path in protected_paths

        if PHOTO_RETENTION_DAYS > 0:
            cutoff = now - PHOTO_RETENTION_DAYS * 86400
            keep = []
            for path in files:
                try:
                    if path.stat().st_mtime < cutoff and not _protected(path):
                        path.unlink()
                        deleted += 1
                    else:
                        keep.append(path)
                except Exception as e:
                    errors += 1
                    log.warning(f"사진 정리 실패: {path} ({e})")
            files = keep

        if MAX_STORED_PHOTOS > 0 and len(files) > MAX_STORED_PHOTOS:
            overflow = len(files) - MAX_STORED_PHOTOS
            removable = [path for path in files if not _protected(path)]
            for path in removable[:overflow]:
                try:
                    path.unlink()
                    deleted += 1
                except Exception as e:
                    errors += 1
                    log.warning(f"사진 정리 실패: {path} ({e})")
            files = [path for path in files if path.exists()]

        try:
            total, _used, free = shutil.disk_usage(BASE_DIR)
            min_free = max(0, MIN_FREE_GB) * (1024 ** 3)
            while MIN_FREE_GB > 0 and free < min_free and files:
                path = files.pop(0)
                if _protected(path):
                    continue
                try:
                    path.unlink()
                    deleted += 1
                    total, _used, free = shutil.disk_usage(BASE_DIR)
                except Exception as e:
                    errors += 1
                    log.warning(f"저장공간 확보 실패: {path} ({e})")
                    break
            free_gb = free / (1024 ** 3)
            total_gb = total / (1024 ** 3)
        except Exception as e:
            errors += 1
            free_gb = total_gb = 0.0
            log.warning(f"저장공간 확인 실패: {e}")

        ai_deleted, ai_errors = self._cleanup_ai_dirs(now)
        errors += ai_errors

        remaining = len(self._photo_files())
        self.cleanup_summary = (
            f"사진 {remaining}개 보관 · 정리 {deleted}개 · "
            f"여유 {free_gb:.1f}/{total_gb:.1f}GB"
        )
        if ai_deleted:
            self.cleanup_summary += f" · AI 폴더 {ai_deleted}개 정리"
        if errors:
            self.cleanup_summary += f" · 오류 {errors}개"
        if deleted or errors or force:
            log.info(f"사진 보관 정리: {self.cleanup_summary}")

    def _set_review_notice(self, text: str, seconds: float = 3.5):
        self.review_notice = text
        self.review_notice_until = time.time() + seconds

    def _camera_health(self) -> dict:
        if hasattr(self.camera, "health"):
            return self.camera.health()
        frame = self._camera_frame()
        return {
            "ok": frame is not None,
            "source": "unknown",
            "status": "프레임 확인" if frame is not None else "프레임 없음",
            "age": 0.0 if frame is not None else float("inf"),
            "failures": 0,
            "reconnects": 0,
        }

    def _camera_frame(self):
        try:
            return self.camera.get_frame(max_age=CAM_STALE_SECS)
        except TypeError:
            return self.camera.get_frame()

    def _review_thumb_rects(self):
        tw, th = self._review_thumb_size()
        if self.compact_ui:
            layout = self._compact_review_layout()
            gap = layout["thumb_gap"]
            grid_x = layout["thumb_grid_x"]
            grid_y = layout["thumb_grid_y"]
        else:
            gap    = 16
            grid_x = self.btn_retake_selected.rect.x
            grid_y = 140
        rects = []
        for i in range(PHOTO_COUNT):
            col = i % 2
            row = i // 2
            rects.append(pygame.Rect(
                grid_x + col * (tw + gap),
                grid_y + row * (th + gap),
                tw,
                th,
            ))
        return rects

    def _refresh_status(self):
        printer_ok, printer_text = printer.get_printer_status()
        total, used, free = shutil.disk_usage(BASE_DIR)
        photos_count = len(list(PHOTOS_DIR.glob("*.jpg"))) if PHOTOS_DIR.exists() else 0
        camera_health = self._camera_health()
        age = camera_health["age"]
        age_text = "새 프레임 없음" if age == float("inf") else f"최근 프레임 {age:.1f}초 전"
        camera_text = (
            f"{camera_health['status']} · {age_text} · "
            f"재연결 {camera_health['reconnects']}회"
        )
        qr_text = f"QR 서버: http://{get_local_ip()}:{QR_SERVER_PORT}"
        stats = booth_stats.summary()
        today = stats["today"]
        total_stats = stats["total"]
        stats_text = (
            f"오늘 촬영 {today['sessions']}회 · 인쇄 {today['print_success']}회 "
            f"(실패 {today['print_failure']}회) · QR {today['qr_downloads']}회 / "
            f"전체 촬영 {total_stats['sessions']}회"
        )
        if AI_ENABLED:
            stats_text += f" · AI {today['ai_success']}/{today['ai_requests']}"
        self.status_lines = [
            ("카메라", camera_text, camera_health["ok"]),
            ("프린터", printer_text or PRINTER_NAME, printer_ok),
            ("QR", qr_text, self.qr_server.is_running),
            ("저장공간", f"여유 {free // (1024 ** 3)}GB / 전체 {total // (1024 ** 3)}GB", free > 1024 ** 3),
            ("사진 폴더", f"{photos_count}개 JPG 저장됨", True),
            ("오늘 운영", stats_text, True),
            ("보관 정리", self.cleanup_summary, True),
            self._ai_status_line(),
        ]
        self.status_checked_at = time.time()

    def _oriented_frame(self, frame: np.ndarray) -> np.ndarray:
        """촬영/출력용 프레임 방향을 적용합니다."""
        if self.capture_orientation == "portrait":
            flag = (
                cv2.ROTATE_90_CLOCKWISE
                if self.portrait_rotation == "clockwise"
                else cv2.ROTATE_90_COUNTERCLOCKWISE
            )
            return cv2.rotate(frame, flag)
        return frame

    def _processed_frame(self, frame: np.ndarray) -> np.ndarray:
        frame = self._oriented_frame(frame)
        return apply_filter(frame, self.selected_filter)

    def _display_frame(self, frame: np.ndarray) -> np.ndarray:
        frame = self._processed_frame(frame)
        if PREVIEW_MIRROR:
            frame = cv2.flip(frame, 1)
        return frame

    def _preview_crop_frame(self, frame: np.ndarray) -> np.ndarray:
        frame_h, frame_w = frame.shape[:2]
        target_aspect = composer.photo_slot_aspect(
            frame_w >= frame_h, self.selected_print_layout
        )
        return center_crop_bgr_to_aspect(frame, target_aspect)

    def _draw_camera_preview_fullscreen(self, frame: np.ndarray):
        if frame is None:
            self.screen.fill((20, 20, 30))
            health = self._camera_health()
            draw_text(self.screen, "카메라 재연결 중", self.f_big, C_WHITE,
                      SCREEN_W // 2, SCREEN_H // 2 - 30, anchor="center",
                      max_width=1200)
            draw_text(self.screen, health["status"], self.f_medium, C_GRAY,
                      SCREEN_W // 2, SCREEN_H // 2 + 54, anchor="center",
                      max_width=1200)
            return
        self.screen.fill((20, 20, 30))
        display_frame = self._display_frame(frame)
        display_frame = self._preview_crop_frame(display_frame)
        prev = fit_bgr_to_surface(display_frame, SCREEN_W, SCREEN_H)
        pw, ph = prev.get_size()
        rect = pygame.Rect((SCREEN_W - pw) // 2, (SCREEN_H - ph) // 2, pw, ph)
        self.screen.blit(prev, rect.topleft)

    def _review_thumb_size(self) -> tuple[int, int]:
        if self.compact_ui:
            layout = self._compact_review_layout()
            if self.capture_orientation == "portrait":
                return layout["portrait_thumb_w"], layout["portrait_thumb_h"]
            return layout["thumb_w"], layout["thumb_h"]
        if self.capture_orientation == "portrait":
            return 150, 220
        return 230, 150

    def _print_preview_target_h(self) -> int:
        if self.compact_ui:
            return self._compact_review_layout()["preview_h"]
        return 820

    def _refresh_processed_photos(self):
        if len(self.captured_source_bgr) == len(self.captured_raw_bgr):
            self.captured_raw_bgr = [
                self._oriented_frame(source).copy()
                for source in self.captured_source_bgr
            ]
        if self.session_mode == "ai":
            # AI 결과(실패 컷은 원본) 위에 필터를 후처리로 적용
            bases = []
            for i, raw in enumerate(self.captured_raw_bgr):
                base = raw
                if (i < len(self.ai_results) and self.ai_results[i] is not None
                        and not self.ai_use_original[i]):
                    base = self.ai_results[i]
                bases.append(base)
            self.captured_bgr = [apply_filter(base, self.selected_filter) for base in bases]
        else:
            self.captured_bgr = [
                apply_filter(raw, self.selected_filter)
                for raw in self.captured_raw_bgr
            ]
        self.thumb_surfaces = []
        self.strip_surface = None
        self.strip_path = None
        self.qr_surface = None
        self.qr_url = ""

    def _recompose_current_session(self):
        if len(self.captured_raw_bgr) != PHOTO_COUNT or self._composing:
            return
        self._refresh_processed_photos()
        self._start_compose()

    # ─────────────────────────────────────────────────
    #  상태 전환
    # ─────────────────────────────────────────────────
    def _set_state(self, new_state: str):
        log.info(f"상태: {self.state} → {new_state}")
        self.state      = new_state
        self.state_time = time.time()

    # ─────────────────────────────────────────────────
    #  카운트다운 시작
    # ─────────────────────────────────────────────────
    def _begin_countdown(self, new_session=True, retake_index=None):
        if new_session:
            self._reset_session()
        if retake_index is not None:
            if not 0 <= retake_index < len(self.captured_raw_bgr):
                self._set_review_notice("다시 찍을 컷을 먼저 선택해 주세요.")
                return
            self.retake_index = retake_index
            self.selected_retake_index = retake_index
            log.info(f"{retake_index + 1}번 컷 재촬영 시작")
        else:
            self.retake_index = None
        self.review_notice = ""
        self.review_notice_until = 0.0
        self.cd_val    = COUNTDOWN_SECS
        self.cd_tick   = time.time()
        self.last_beep = COUNTDOWN_SECS + 1
        self._set_state(St.COUNTDOWN)

    # ─────────────────────────────────────────────────
    #  촬영 & 합성
    # ─────────────────────────────────────────────────
    def _capture_photo(self):
        frame = self._camera_frame()
        if frame is None:
            log.warning("카메라 프레임이 준비되지 않아 촬영을 다시 대기합니다")
            return False
        source = frame.copy()
        raw = self._oriented_frame(source).copy()
        processed = apply_filter(raw, self.selected_filter)
        if self.retake_index is not None:
            idx = self.retake_index
            self.captured_source_bgr[idx] = source
            self.captured_raw_bgr[idx] = raw
            self.captured_bgr[idx] = processed
            self.thumb_surfaces = []
            self.strip_surface = None
            self.strip_path = None
            self.qr_surface = None
            self.qr_url = ""
            self._compose_generation += 1
            self.retake_index = None
            log.info(f"{idx + 1}번 컷 재촬영 완료")
        else:
            idx = len(self.captured_raw_bgr)
            self.captured_source_bgr.append(source)
            self.captured_raw_bgr.append(raw)
            self.captured_bgr.append(processed)
            log.info(f"촬영 {len(self.captured_bgr)}/{PHOTO_COUNT}")
        self.capture_date = date.today()
        if self.session_mode == "ai" and self.ai_manager is not None and idx < PHOTO_COUNT:
            self._ai_submit(idx, raw)
        return True

    def _start_compose(self):
        """백그라운드 스레드로 스트립 합성"""
        if self._composing:
            return
        id_mode = self.session_mode == "id"
        expected_count = PHOTO_COUNT
        if len(self.captured_bgr) != expected_count:
            log.warning(
                f"합성 건너뜀: 사진 {len(self.captured_bgr)}/{expected_count}장"
            )
            return
        self._composing = True
        self._compose_generation += 1
        generation = self._compose_generation
        photos = [p.copy() if p is not None else None for p in self.captured_bgr]
        session_id = self.session_id
        frame_theme = self.selected_frame_theme
        print_layout = self.selected_print_layout
        preview_height = self.id_preview_area.h if id_mode else self._print_preview_target_h()
        capture_date = self.capture_date

        def _work():
            try:
                if id_mode:
                    preview = composer.make_id_print_preview(photos, preview_height)
                    strip_path = composer.compose_id_print_image(photos, session_id)
                else:
                    surfs = []
                    thumb_w, thumb_h = self._review_thumb_size()
                    for bgr in photos:
                        crop = center_crop_bgr_to_aspect(bgr, thumb_w / thumb_h)
                        surfs.append(bgr_to_surface(crop, (thumb_w, thumb_h)))
                    if generation == self._compose_generation:
                        self.thumb_surfaces = surfs
                    strip_path = composer.compose_print_image(
                        photos, session_id, frame_theme, print_layout,
                        capture_date=capture_date,
                    )
                    preview = composer.make_preview_image(
                        photos, preview_height, frame_theme, print_layout,
                        capture_date=capture_date,
                    )
                strip_surface = pil_to_surface(preview)
                if generation == self._compose_generation:
                    self.strip_path = strip_path
                    self.strip_surface = strip_surface
                    if not self.session_counted:
                        booth_stats.increment("sessions")
                        self.session_counted = True
                    self._cleanup_old_photos()
            except Exception as e:
                log.error(f"합성 오류: {e}")
                if id_mode and generation == self._compose_generation:
                    self._set_review_notice("인쇄 파일을 준비하지 못했어요. 다시 준비해 주세요.", 8.0)
            finally:
                if generation == self._compose_generation:
                    self._composing = False

        threading.Thread(target=_work, daemon=True, name="composer").start()

    # ─────────────────────────────────────────────────
    #  인쇄
    # ─────────────────────────────────────────────────
    def _can_start_printing(self) -> bool:
        if not self.strip_path or not self.strip_path.exists():
            self._set_review_notice("사진 합성이 끝난 뒤 인쇄할 수 있습니다.")
            return False

        ok, detail = printer.get_printer_status()
        if not ok:
            log.warning(f"프린터 상태 확인 실패: {detail}")
            self._set_review_notice("프린터 연결/등록을 확인하세요. 운영 점검에서 상태를 볼 수 있습니다.", 5.0)
            return False
        return True

    def _start_printing(self):
        self._print_done = False
        self._print_done_at = 0.0
        self._set_state(St.PRINTING)
        path = self.strip_path
        copies = self.print_copies
        actual_size = self.session_mode == "id"

        def _work():
            if path and path.exists():
                if actual_size:
                    ok = printer.print_photo(path, copies, actual_size=True)
                else:
                    ok = printer.print_photo(path, copies)
            else:
                log.error("스트립 파일 없음 - 합성 완료 전에 인쇄 요청됨")
                ok = False
            self._print_ok   = ok
            self._print_done = True
            self._print_done_at = time.time()
            booth_stats.increment("print_success" if ok else "print_failure")
            if self.snd_done and ok:
                self.snd_done.play()

        threading.Thread(target=_work, daemon=True, name="printer").start()

    def _finish_print_result(self):
        if self._print_ok:
            self._set_state(St.IDLE)
            self._reset_session()
        else:
            if self.session_mode == "id":
                self._set_state(St.ID_REVIEW)
                self._set_review_notice("인쇄에 실패했어요. 프린터를 확인한 뒤 다시 시도해 주세요.", 6.0)
            else:
                self._set_state(St.REVIEW)
                self._set_review_notice("인쇄에 실패했습니다. 프린터를 확인한 뒤 다시 시도하거나 QR을 사용하세요.", 6.0)

    # ─────────────────────────────────────────────────
    #  QR
    # ─────────────────────────────────────────────────
    def _start_qr(self):
        if not self.strip_path or not self.strip_path.exists():
            log.error("스트립 파일 없음 - 합성 완료 전에 QR 요청됨")
            self._set_review_notice("사진 합성이 끝난 뒤 QR을 만들 수 있습니다.")
            return
        if not self.qr_server.is_running:
            self.qr_server.start()
        if not self.qr_server.is_running:
            log.error("QR 서버가 실행 중이 아니어서 QR 화면을 열 수 없습니다")
            self._set_review_notice("QR 서버를 시작하지 못했습니다. 운영 점검을 확인해 주세요.", 5.0)
            return
        surf, url = self.qr_server.make_qr_surface(self.session_id)
        self.qr_surface = surf
        self.qr_url     = url
        booth_stats.increment("qr_shown")
        self._set_state(St.QR_SHOW)

    # ═══════════════════════════════════════════════
    #  이벤트 처리
    # ═══════════════════════════════════════════════
    def _handle_events(self) -> bool:
        """False 반환 시 종료"""
        mpos    = self._event_pos_to_ui(pygame.mouse.get_pos())
        mpressed = pygame.mouse.get_pressed()[0]

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False

            if event.type == pygame.VIDEORESIZE and not FULLSCREEN:
                self.display = pygame.display.set_mode(
                    (max(640, event.w), max(360, event.h)), pygame.RESIZABLE
                )
                self._update_display_transform()
                continue

            # 상태 전환 직후 입력 무시 (터치 중복/연타로 다음 화면 버튼이 눌리는 것 방지)
            input_guard = time.time() - self.state_time < INPUT_GUARD_SECS

            # ── 키보드 ────────────────────────────────
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return False
                if event.key == pygame.K_q and (
                        pygame.key.get_mods() & pygame.KMOD_CTRL):
                    return False
                if input_guard:
                    continue

                if self.state == St.IDLE:
                    if event.key == pygame.K_s:
                        self._refresh_status()
                        self._set_state(St.STATUS)
                    elif event.key in (pygame.K_a, pygame.K_RETURN, pygame.K_SPACE):
                        self._open_concept_select()
                elif self.state == St.QR_SHOW:
                    self._return_to_review_from_qr()
                elif self.state == St.STATUS:
                    self._set_state(St.IDLE)

            # ── 마우스/터치 클릭 ──────────────────────
            if event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                if event.type == pygame.FINGERDOWN:
                    # 터치 좌표(0~1)는 창 전체 기준 → 실제 픽셀로 바꾼 뒤 UI 배율/여백 반영
                    pos = self._event_pos_to_ui(
                        (event.x * self.display_w, event.y * self.display_h)
                    )
                else:
                    if event.button != 1:
                        continue
                    if getattr(event, "touch", False):
                        continue  # 터치로 합성된 마우스 이벤트는 FINGERDOWN에서 이미 처리
                    pos = self._event_pos_to_ui(event.pos)

                if input_guard:
                    continue
                self._handle_click(pos)

        # 버튼 hover 업데이트
        if self.state == St.IDLE:
            self.btn_start_ai.update(mpos, mpressed)
            self.btn_start_id.update(mpos, mpressed)
            self.btn_status.update(mpos, mpressed)
        elif self.state in ID_STAGES or (self.session_mode == "id" and self.state in {St.COUNTDOWN, St.CONSENT}):
            for btn in self._id_stage_buttons():
                btn.update(mpos, mpressed)
        elif self.state == St.CONCEPT_SELECT:
            for btn in (self.btn_concept_prev, self.btn_concept_next,
                        self.btn_concept_back, self.btn_concept_confirm):
                btn.update(mpos, mpressed)
        elif self.state == St.CONSENT:
            self.btn_consent_no.update(mpos, mpressed)
            self.btn_consent_yes.update(mpos, mpressed)
        elif self.state == St.REVIEW:
            for btn in (self.btn_review_home, self.btn_print, self.btn_qr, self.btn_retake, self.btn_retake_selected,
                        self.btn_ai_toggle, self.btn_ai_regen):
                btn.update(mpos, mpressed)
            for _, btn in self.review_frame_buttons:
                btn.update(mpos, mpressed)
            for _, btn in self.review_orientation_buttons:
                btn.update(mpos, mpressed)
            for _, btn in self.review_filter_buttons:
                btn.update(mpos, mpressed)
            self.btn_copies_minus.update(mpos, mpressed)
            self.btn_copies_plus.update(mpos, mpressed)
        elif self.state in {St.COUNTDOWN, St.FLASH} and self.session_mode != "id":
            self.btn_capture_home.update(mpos, mpressed)
        elif self.state == St.STATUS:
            self.btn_status_close.update(mpos, mpressed)

        return True

    def _handle_click(self, pos):
        if (self.state in {St.COUNTDOWN, St.FLASH} and self.session_mode != "id"
                and self.btn_capture_home.hit(pos)):
            self._reset_session()
            self._set_state(St.IDLE)
            return
        if self.state == St.IDLE:
            if self.btn_status.hit(pos):
                self._refresh_status()
                self._set_state(St.STATUS)
                return
            if self.btn_start_ai.hit(pos):
                self._open_concept_select()
            elif self.btn_start_id.hit(pos):
                self._open_id_setup()

        elif self.state in ID_STAGES or (self.session_mode == "id" and self.state in {St.COUNTDOWN, St.CONSENT}):
            self._handle_id_click(pos)

        elif self.state == St.CONCEPT_SELECT:
            self._handle_concept_click(pos)

        elif self.state == St.CONSENT:
            self.ai_screen_input_at = time.time()
            concepts = self._selected_concepts()
            if self.btn_consent_yes.hit(pos) and concepts:
                self._begin_ai_session(concepts)
            elif self.btn_consent_no.hit(pos):
                log.info("AI 변신 동의 안 함 → 대기 화면")
                self._set_state(St.IDLE)

        elif self.state == St.REVIEW:
            if self.btn_review_home.hit(pos):
                self._reset_session()
                self._set_state(St.IDLE)
                return
            for i, rect in enumerate(self._review_thumb_rects()):
                if i < len(self.captured_raw_bgr) and rect.collidepoint(pos):
                    self.selected_retake_index = i
                    self._set_review_notice(f"{i + 1}번 컷이 선택되었습니다.")
                    return

            for theme_id, btn in self.review_frame_buttons:
                if btn.hit(pos):
                    if self._composing:
                        self._set_review_notice("사진 합성 중입니다. 잠시 후 다시 눌러 주세요.")
                        return
                    if self._set_frame_theme(theme_id):
                        self._recompose_current_session()
                    return
            for filter_id, btn in self.review_filter_buttons:
                if btn.hit(pos):
                    if self._composing:
                        self._set_review_notice("사진 합성 중입니다. 잠시 후 다시 눌러 주세요.")
                        return
                    if self._set_filter(filter_id):
                        self._recompose_current_session()
                    return
            if self.session_mode == "ai":
                if self.btn_ai_toggle.hit(pos):
                    self._ai_toggle_selected()
                    return
                if self.btn_ai_regen.hit(pos):
                    self._ai_regenerate_selected()
                    return
            for orientation, btn in self.review_orientation_buttons:
                if self.session_mode == "ai":
                    break  # AI 모드에서는 방향 버튼 숨김
                if btn.hit(pos):
                    if self._composing:
                        self._set_review_notice("사진 합성 중입니다. 잠시 후 다시 눌러 주세요.")
                        return
                    if self._set_capture_orientation(orientation):
                        self._recompose_current_session()
                    return
            if self.btn_copies_minus.hit(pos):
                self._set_print_copies(self.print_copies - 1)
                return
            if self.btn_copies_plus.hit(pos):
                self._set_print_copies(self.print_copies + 1)
                return
            if self.btn_print.hit(pos):
                if self._composing:
                    self._set_review_notice("사진 합성 중입니다. 잠시 후 다시 눌러 주세요.")
                    return
                if not self._can_start_printing():
                    return
                self._start_printing()
            elif self.btn_qr.hit(pos):
                if self._composing:
                    self._set_review_notice("사진 합성 중입니다. 잠시 후 다시 눌러 주세요.")
                    return
                self._start_qr()
            elif self.btn_retake_selected.hit(pos):
                if self._composing:
                    self._set_review_notice("사진 합성 중입니다. 잠시 후 다시 눌러 주세요.")
                    return
                self._begin_countdown(new_session=False, retake_index=self.selected_retake_index)
            elif self.btn_retake.hit(pos):
                self._begin_countdown(new_session=True)

        elif self.state == St.QR_SHOW:
            self._return_to_review_from_qr()

        elif self.state == St.PRINTING and self._print_done:
            self._finish_print_result()

        elif self.state == St.STATUS:
            if self.btn_status_close.hit(pos):
                self._set_state(St.IDLE)

    # ═══════════════════════════════════════════════
    #  IDLE 렌더링
    # ═══════════════════════════════════════════════
    def _draw_selected_button_group(self, selected_id, buttons, active_color=C_BLUE, font=None):
        for item_id, btn in buttons:
            if item_id == selected_id:
                btn.set_style(active_color, C_WHITE, border_width=0)
            else:
                btn.set_style(C_WHITE, C_DARK, border_color=C_LGRAY, border_width=2)
            btn.draw(self.screen, font or self.f_small)

    def _draw_idle(self, dt):
        self.screen.fill(C_BG)

        for p in self.particles:
            p.update(dt)
            p.draw(self.screen)

        if self.compact_ui:
            draw_text(self.screen, EVENT_TITLE or "오늘의 네컷", self.f_big, C_PINK,
                      SCREEN_W // 2, 42, anchor="center",
                      shadow=True, shadow_color=C_LPINK, shadow_off=3,
                      max_width=820)
            draw_text(self.screen, BOOTH_SUBTITLE or BRAND_NAME, self.f_medium, C_DARK,
                      SCREEN_W // 2, 92, anchor="center", max_width=840)
            self.btn_status.draw(self.screen, self.f_tiny)

            frame = self._camera_frame()
            preview_top = 132
            preview_h = self.btn_start_ai.rect.top - preview_top - 52
            preview_w = min(SCREEN_W - 120, 720)
            if frame is not None:
                frame = self._display_frame(frame)
                frame = self._preview_crop_frame(frame)
                prev = fit_bgr_to_surface(frame, preview_w, preview_h)
                pw, ph = prev.get_size()
                px = SCREEN_W // 2 - pw // 2
                py = preview_top + (preview_h - ph) // 2
                draw_rrect(self.screen, C_DARK,
                           (px - 7, py - 7, pw + 14, ph + 14), radius=14)
                self.screen.blit(prev, (px, py))
            else:
                draw_text(self.screen, "카메라 연결을 확인하고 있습니다", self.f_medium, C_GRAY,
                          SCREEN_W // 2, preview_top + preview_h // 2,
                          anchor="center", max_width=760)

            self._draw_idle_start_buttons()
            return

        # FHD 15.6인치 모니터 기준 첫 화면: 제목, 실제 출력 크롭, 시작 버튼만 배치합니다.
        draw_text(self.screen, EVENT_TITLE or "오늘의 네컷", self.f_big, C_PINK,
                  SCREEN_W // 2, 72, anchor="center",
                  shadow=True, shadow_color=C_LPINK, shadow_off=4,
                  max_width=1180)
        draw_text(self.screen, BOOTH_SUBTITLE or BRAND_NAME, self.f_large, C_DARK,
                  SCREEN_W // 2, 158, anchor="center", max_width=1280)
        self.btn_status.draw(self.screen, self.f_small)

        frame = self._camera_frame()
        if frame is not None:
            frame = self._display_frame(frame)
            frame = self._preview_crop_frame(frame)
            box_w, box_h = 820, min(680, self.btn_start_ai.rect.top - 238)
            prev = fit_bgr_to_surface(frame, box_w, box_h)
            pw, ph = prev.get_size()
            px = SCREEN_W // 2 - pw // 2
            py = 218 + (box_h - ph) // 2
            draw_rrect(self.screen, C_LPINK,
                       (px - 8, py - 8, pw + 16, ph + 16), radius=14)
            self.screen.blit(prev, (px, py))
        else:
            draw_text(self.screen, "카메라 연결을 확인하고 있습니다", self.f_medium, C_GRAY,
                      SCREEN_W // 2, 520, anchor="center", max_width=1000)

        self._draw_idle_start_buttons()

        # 하단 정보
        if FOOTER_TEXT:
            draw_text(self.screen, FOOTER_TEXT, self.f_tiny, C_LGRAY,
                      SCREEN_W // 2, SCREEN_H - 30, anchor="center",
                      max_width=1600)

    # ═══════════════════════════════════════════════
    #  COUNTDOWN 렌더링 & 업데이트
    # ═══════════════════════════════════════════════
    def _update_countdown(self):
        elapsed = time.time() - self.cd_tick
        new_val = COUNTDOWN_SECS - int(elapsed)
        new_val = max(1, new_val)

        # 숫자 바뀔 때 비프음
        if new_val != self.cd_val and new_val != self.last_beep:
            self.cd_val    = new_val
            self.last_beep = new_val
            if self.snd_beep:
                self.snd_beep.play()

        # 촬영 타이밍
        if elapsed >= COUNTDOWN_SECS:
            if self.session_mode == "id":
                self._capture_id_portrait()
                return
            if not self._capture_photo():
                self.cd_val    = COUNTDOWN_SECS
                self.cd_tick   = time.time()
                self.last_beep = COUNTDOWN_SECS + 1
                return
            if self.snd_shutter:
                self.snd_shutter.play()
            self.flash_alpha = 255
            self._set_state(St.FLASH)

    def _draw_countdown(self):
        # 카메라 프리뷰 (비율 유지)
        frame = self._camera_frame()
        self._draw_camera_preview_fullscreen(frame)

        # 상단 반투명 바
        bar = pygame.Surface((SCREEN_W, 90), pygame.SRCALPHA)
        bar.fill((0, 0, 0, 150))
        self.screen.blit(bar, (0, 0))

        if self.retake_index is not None:
            shot_label = f"{self.retake_index + 1}번 컷 다시 찍기"
        else:
            shot_label = f"{len(self.captured_bgr) + 1} / {PHOTO_COUNT}"
        shot_index = self.retake_index if self.retake_index is not None else len(self.captured_bgr)
        concept = self._ai_concept(shot_index) if self.session_mode == "ai" else None
        label_limit = max(80, min(1200, SCREEN_W - 2 * (self.btn_capture_home.rect.right + 14)))
        if concept is not None:
            # AI 모드 콘셉트 배지: "소방관 변신 · 2 / 4"
            shot_label = f"{concept.name} 변신 · {shot_label}"
            label_w = min(label_limit + 28, self.f_large.size(shot_label)[0] + 56)
            draw_rrect(self.screen, C_PINK,
                       (SCREEN_W // 2 - label_w // 2, 8, label_w, 74), radius=37)
        draw_text(self.screen, shot_label, self.f_large, C_WHITE,
                  SCREEN_W // 2, 44, anchor="center", max_width=label_limit)

        # 카운트다운 숫자 (그림자 + 펄스 효과)
        pulse = 1.0 + 0.06 * math.sin(time.time() * 6)
        num_surf = self.f_huge.render(str(self.cd_val), True, C_YELLOW)
        w = int(num_surf.get_width() * pulse)
        h = int(num_surf.get_height() * pulse)
        num_surf = pygame.transform.scale(num_surf, (w, h))

        # 그림자
        shd = self.f_huge.render(str(self.cd_val), True, (80, 60, 0))
        shd = pygame.transform.scale(shd, (w, h))
        self.screen.blit(shd, (SCREEN_W // 2 - w // 2 + 6,
                               SCREEN_H // 2 - h // 2 + 6))
        self.screen.blit(num_surf, (SCREEN_W // 2 - w // 2,
                                    SCREEN_H // 2 - h // 2))

        draw_text(self.screen, "움직이지 마세요!", self.f_medium, C_WHITE,
                  SCREEN_W // 2, SCREEN_H // 2 + h // 2 + 20,
                  anchor="center", max_width=1200)

        # 하단 찍힌 사진 썸네일
        self._draw_shot_thumbnails()
        self.btn_capture_home.draw(self.screen, self.f_small)

    def _draw_shot_thumbnails(self):
        if not self.captured_bgr:
            return
        tw, th = (86, 150) if self.capture_orientation == "portrait" else (150, 105)
        gap    = 12
        total  = len(self.captured_bgr) * (tw + gap) - gap
        sx     = (SCREEN_W - total) // 2
        sy     = SCREEN_H - th - 18
        for i, bgr in enumerate(self.captured_bgr):
            surf = fit_bgr_to_surface(bgr, tw, th)
            rx   = sx + i * (tw + gap)
            draw_rrect(self.screen, C_WHITE, (rx - 4, sy - 4, tw + 8, th + 8), 8)
            sw, sh = surf.get_size()
            self.screen.blit(surf, (rx + (tw - sw) // 2, sy + (th - sh) // 2))
            if (self.session_mode == "ai" and i < len(self.ai_status)
                    and self.ai_status[i] == "done"):
                self._draw_check(rx + tw - 8, sy + 8, 13)

    # ═══════════════════════════════════════════════
    #  FLASH 렌더링
    # ═══════════════════════════════════════════════
    def _draw_flash(self):
        frame = self._camera_frame()
        self._draw_camera_preview_fullscreen(frame)

        # 흰 플래시 페이드아웃
        if self.flash_alpha > 0:
            ov = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            ov.fill((255, 255, 255, int(self.flash_alpha)))
            self.screen.blit(ov, (0, 0))
            self.flash_alpha = max(0, self.flash_alpha - 28)

        n = len(self.captured_bgr)
        draw_text(self.screen, f"찰칵!  {n} / {PHOTO_COUNT}", self.f_big, C_YELLOW,
                  SCREEN_W // 2, SCREEN_H // 2, anchor="center",
                  shadow=True, shadow_color=(100, 80, 0), shadow_off=4,
                  max_width=1200)

        self._draw_shot_thumbnails()
        self.btn_capture_home.draw(self.screen, self.f_small)

        # 플래시 시간 경과 후 다음 단계
        elapsed = time.time() - self.state_time
        if elapsed >= SHOT_DELAY and self.flash_alpha <= 0:
            if n >= PHOTO_COUNT and self.session_mode == "ai":
                self._enter_ai_processing()
            elif n >= PHOTO_COUNT:
                self._start_compose()
                self._set_state(St.REVIEW)
                if self.snd_done:
                    self.snd_done.play()
            else:
                self._begin_countdown(new_session=False)

    # ═══════════════════════════════════════════════
    #  REVIEW 렌더링
    # ═══════════════════════════════════════════════
    def _draw_review_compact(self):
        self.screen.fill(C_BG)
        layout = self._compact_review_layout()
        left_card = layout["left_card"]
        right_card = layout["right_card"]
        v = layout["v"]
        section_font = self.f_medium if v >= 0.85 else self.f_small
        button_font = self.f_small if v >= 0.85 else self.f_tiny
        ai_mode = self.session_mode == "ai"

        draw_text(self.screen, "사진 확인", self.f_big, C_PINK,
                  SCREEN_W // 2, 34, anchor="center", max_width=760)
        draw_text(self.screen, "원하는 컷과 스타일을 선택하세요",
                  self.f_tiny, C_GRAY, SCREEN_W // 2, 76,
                  anchor="center", max_width=760)

        draw_card(self.screen, left_card, radius=16)
        draw_card(self.screen, right_card, radius=16)

        preview_x = left_card.centerx
        preview_y = left_card.y + 26
        if self.strip_surface:
            px = preview_x - self.strip_surface.get_width() // 2
            self.screen.blit(self.strip_surface, (px, preview_y))
        else:
            cx = left_card.centerx
            cy = left_card.centery
            dots = "." * (int(time.time() * 2.5) % 4)
            draw_text(self.screen, f"합성 중{dots}", self.f_medium, C_GRAY,
                      cx, cy, anchor="center", max_width=left_card.width - 60)

        theme_name = composer.FRAME_THEMES[self.selected_frame_theme]["name"]
        filter_name = FILTERS[self.selected_filter]["name"]
        look_text = f"{theme_name} 프레임 · {filter_name} 필터 · 2x2 · {self.print_copies}장"
        concept = self._ai_concept(self.selected_retake_index) if ai_mode else None
        if concept is not None:
            name = "복장 믹스" if len(self.concept_selected) > 1 else concept.name
            look_text = f"{name} · {theme_name} · {filter_name} · 2x2 · {self.print_copies}장"
        draw_text(self.screen, look_text,
                  self.f_small, C_DARK, left_card.centerx, left_card.bottom - 28,
                  anchor="center", max_width=left_card.width - 46)

        rects = self._review_thumb_rects()
        draw_text(self.screen, "촬영 컷", section_font, C_DARK,
                  rects[0].x, right_card.y + int(36 * v), anchor="topleft",
                  max_width=layout["retake_w"])

        if self.thumb_surfaces:
            for i, surf in enumerate(self.thumb_surfaces[:4]):
                rect = rects[i]
                border_color = C_PINK if i == self.selected_retake_index else C_LGRAY
                draw_rrect(self.screen, C_WHITE,
                           (rect.x - 6, rect.y - 6, rect.w + 12, rect.h + 12),
                           radius=12, border=3, bcol=border_color)
                self.screen.blit(surf, rect.topleft)
                draw_rrect(self.screen, C_PINK,
                           (rect.x + 4, rect.y + 4, 28, 28), radius=14)
                draw_text(self.screen, str(i + 1), self.f_tiny, C_WHITE,
                          rect.x + 18, rect.y + 18, anchor="center")
                if ai_mode:
                    self._draw_ai_badge(rect, i, compact=True)
        else:
            for i, rect in enumerate(rects):
                border_color = C_PINK if i == self.selected_retake_index else C_LGRAY
                draw_rrect(self.screen, C_WHITE, rect, radius=10,
                           border=2, bcol=border_color)

        self._draw_fit_button(self.btn_retake_selected, button_font)

        divider_x = layout["divider_x"]
        pygame.draw.line(
            self.screen, C_LGRAY,
            (divider_x, right_card.y + int(42 * v)),
            (divider_x, right_card.y + int(292 * v)),
            2,
        )

        panel_x = layout["control_x"]
        panel_right = layout["control_right"]
        draw_text(self.screen, "프레임", section_font, C_DARK,
                  panel_x, right_card.y + int(38 * v), anchor="topleft",
                  max_width=panel_right - panel_x)
        self._draw_selected_button_group(
            self.selected_frame_theme, self.review_frame_buttons, C_PINK, button_font)

        pygame.draw.line(
            self.screen, C_LGRAY,
            (panel_x, right_card.y + int(210 * v)),
            (panel_right, right_card.y + int(210 * v)),
            1,
        )
        if ai_mode:
            self._draw_fit_button(self.btn_ai_toggle, button_font)
            self._draw_fit_button(self.btn_ai_regen, button_font)
        else:
            draw_text(self.screen, "촬영 방향", section_font, C_DARK,
                      panel_x, right_card.y + int(215 * v), anchor="topleft",
                      max_width=panel_right - panel_x)
            self._draw_selected_button_group(
                self.capture_orientation, self.review_orientation_buttons, C_BLUE, button_font)

        pygame.draw.line(
            self.screen, C_LGRAY,
            (right_card.x + 24, right_card.y + int(326 * v)),
            (right_card.right - 24, right_card.y + int(326 * v)),
            1,
        )
        draw_text(self.screen, "필터", section_font, C_DARK,
                  layout["filter_x"], right_card.y + int(339 * v), anchor="topleft", max_width=280)
        self._draw_selected_button_group(
            self.selected_filter, self.review_filter_buttons, C_BLUE, button_font)

        pygame.draw.line(
            self.screen, C_LGRAY,
            (right_card.x + 24, right_card.y + int(452 * v)),
            (right_card.right - 24, right_card.y + int(452 * v)),
            1,
        )
        draw_text(self.screen, "인쇄 매수", section_font, C_DARK,
                  layout["filter_x"], right_card.y + int(454 * v), anchor="topleft", max_width=200)
        self.btn_copies_minus.draw(self.screen, button_font)
        self.btn_copies_plus.draw(self.screen, button_font)
        copies_center_x = (self.btn_copies_minus.rect.right + self.btn_copies_plus.rect.left) // 2
        draw_text(self.screen, f"{self.print_copies} 장", section_font, C_DARK,
                  copies_center_x, self.btn_copies_minus.rect.centery, anchor="center",
                  max_width=self.btn_copies_plus.rect.left - self.btn_copies_minus.rect.right - 12)

        self.btn_retake.set_style(C_WHITE, C_PINK, border_color=C_PINK, border_width=2)
        self.btn_qr.set_style(C_WHITE, C_DARK, border_color=C_LGRAY, border_width=2)
        self.btn_print.set_style(C_PINK, C_WHITE, border_width=0)
        for btn in (self.btn_review_home, self.btn_retake, self.btn_qr, self.btn_print):
            btn.draw(self.screen, section_font)

        if self._composing:
            for btn in (self.btn_print, self.btn_qr):
                dim = pygame.Surface(
                    (btn.rect.width, btn.rect.height),
                    pygame.SRCALPHA)
                dim.fill((180, 180, 180, 120))
                self.screen.blit(dim, btn.rect.topleft)

        if self.review_notice and time.time() < self.review_notice_until:
            draw_text(self.screen, self.review_notice, self.f_small, C_PINK,
                      SCREEN_W // 2, layout["action_y"] - 14, anchor="center",
                      max_width=840)

        remain = max(0, REVIEW_TIMEOUT - int(time.time() - self.state_time))
        draw_text(self.screen, f"{remain}초 후 자동 초기화",
                  self.f_tiny, C_GRAY, SCREEN_W - 110, SCREEN_H - 16,
                  anchor="center", max_width=210)
        if remain <= 0:
            self._set_state(St.IDLE)
            self._reset_session()

    def _draw_review(self):
        if self.compact_ui:
            self._draw_review_compact()
            return

        self.screen.fill(C_BG)

        draw_text(self.screen, "최종 사진 확인", self.f_large, C_PINK,
                  SCREEN_W // 2, 44, anchor="center", max_width=900)

        # ── 좌측: 최종 출력 미리보기 ──────────────────
        preview_x = 80
        preview_y = 110
        if self.strip_surface:
            self.screen.blit(self.strip_surface, (preview_x, preview_y))
        else:
            cx = preview_x + 280
            cy = SCREEN_H // 2 - 60
            dots = "." * (int(time.time() * 2.5) % 4)
            draw_text(self.screen, f"합성 중{dots}", self.f_medium, C_GRAY,
                      cx, cy, anchor="center", max_width=520)

        # ── 가운데: 4개 촬영 컷 ───────────────────────
        rects = self._review_thumb_rects()
        draw_text(self.screen, "촬영 컷", self.f_medium, C_DARK,
                  rects[0].x, 96, anchor="topleft")

        if self.thumb_surfaces:
            for i, surf in enumerate(self.thumb_surfaces[:4]):
                rect = rects[i]
                border_color = C_BLUE if i == self.selected_retake_index else C_LPINK
                draw_rrect(self.screen, border_color,
                           (rect.x - 7, rect.y - 7, rect.w + 14, rect.h + 14), radius=12)
                self.screen.blit(surf, rect.topleft)
                # 번호 뱃지
                draw_rrect(self.screen, C_PINK,
                           (rect.x + 4, rect.y + 4, 36, 36), radius=18)
                draw_text(self.screen, str(i + 1), self.f_small, C_WHITE,
                          rect.x + 22, rect.y + 22, anchor="center")
                if self.session_mode == "ai":
                    self._draw_ai_badge(rect, i, compact=False)
        else:
            # 썸네일 아직 없을 때 (빠르게 생성되므로 드뭄)
            for i in range(4):
                rect = rects[i]
                border_color = C_BLUE if i == self.selected_retake_index else C_LGRAY
                draw_rrect(self.screen, border_color, rect, radius=12)

        draw_text(self.screen, "썸네일을 눌러 다시 찍을 컷을 고르세요.",
                  self.f_tiny, C_GRAY, self.btn_retake_selected.rect.centerx, 626, anchor="center",
                  max_width=390)
        self.btn_retake_selected.draw(self.screen, self.f_small)
        if self.session_mode == "ai":
            self._draw_fit_button(self.btn_ai_regen)
            self._draw_fit_button(self.btn_ai_toggle)

        # ── 우측: 촬영 후 프레임/필터 선택 ─────────────
        panel_x = self.review_frame_buttons[0][1].rect.x
        panel_center_x = panel_x + 220
        draw_text(self.screen, "프레임 다시 선택", self.f_medium, C_DARK,
                  panel_x, 126, anchor="topleft")
        self._draw_selected_button_group(
            self.selected_frame_theme, self.review_frame_buttons, C_PINK)

        concept = self._ai_concept(self.selected_retake_index) if self.session_mode == "ai" else None
        if self.session_mode == "ai":
            # AI 모드에서는 방향 버튼을 숨기고 콘셉트 정보를 표시
            left = max(0, AI_MAX_REGEN_PER_SESSION - self.ai_regen_count)
            draw_text(self.screen, "AI 변신", self.f_medium, C_DARK,
                      panel_x, 314, anchor="topleft")
            draw_text(self.screen, f"{concept.name if concept else ''} · 다시 변환 {left}회 남음",
                      self.f_small, C_PINK, panel_x, 370, anchor="topleft", max_width=440)
        else:
            draw_text(self.screen, "촬영 방향 다시 선택", self.f_medium, C_DARK,
                      panel_x, 314, anchor="topleft")
            self._draw_selected_button_group(
                self.capture_orientation, self.review_orientation_buttons, C_BLUE)

        draw_text(self.screen, "필터 다시 선택", self.f_medium, C_DARK,
                  panel_x, 436, anchor="topleft")
        self._draw_selected_button_group(
            self.selected_filter, self.review_filter_buttons, C_BLUE)

        theme_name = composer.FRAME_THEMES[self.selected_frame_theme]["name"]
        filter_name = FILTERS[self.selected_filter]["name"]
        look_text = f"{theme_name} 프레임 · {filter_name} 필터 · 2x2 고정"
        if concept is not None:
            look_text = f"{concept.name} · {theme_name} 프레임 · {filter_name} 필터 · 2x2"
        draw_text(self.screen, look_text,
                  self.f_small, C_GRAY, panel_center_x, 690, anchor="center",
                  max_width=440)

        draw_text(self.screen, "인쇄 매수", self.f_medium, C_DARK,
                  panel_x, 724, anchor="topleft")
        self.btn_copies_minus.draw(self.screen, self.f_large)
        self.btn_copies_plus.draw(self.screen, self.f_large)
        draw_text(self.screen, f"{self.print_copies} 장", self.f_large, C_DARK,
                  panel_center_x, 790, anchor="center")

        # ── 하단 버튼 ──────────────────────────────────
        for btn in (self.btn_review_home, self.btn_retake, self.btn_qr, self.btn_print):
            btn.draw(self.screen, self.f_medium)

        # 합성 중 출력/공유/재촬영 버튼 딤처리
        if self._composing:
            for btn in (self.btn_print, self.btn_qr, self.btn_retake_selected):
                dim = pygame.Surface(
                    (btn.rect.width, btn.rect.height),
                    pygame.SRCALPHA)
                dim.fill((180, 180, 180, 120))
                self.screen.blit(dim, btn.rect.topleft)

        if self.review_notice and time.time() < self.review_notice_until:
            draw_text(self.screen, self.review_notice, self.f_small, C_PINK,
                      SCREEN_W // 2, SCREEN_H - 138, anchor="center",
                      max_width=1180)

        remain = max(0, REVIEW_TIMEOUT - int(time.time() - self.state_time))
        draw_text(self.screen, f"{remain}초 후 자동 초기화",
                  self.f_tiny, C_GRAY, SCREEN_W - 120, SCREEN_H - 34,
                  anchor="center", max_width=230)
        if remain <= 0:
            self._set_state(St.IDLE)
            self._reset_session()

    # ═══════════════════════════════════════════════
    #  PRINTING 렌더링
    # ═══════════════════════════════════════════════
    def _wrap_detail(self, text: str, font, max_width: int, max_lines: int = 2) -> list[str]:
        """' · ' 단위로 줄바꿈. 넘치면 마지막 줄은 fit_text로 줄임."""
        parts = str(text).split(" · ")
        lines, cur = [], ""
        for part in parts:
            cand = f"{cur} · {part}" if cur else part
            if cur and font.size(cand)[0] > max_width:
                lines.append(cur)
                cur = part
            else:
                cur = cand
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            lines = lines[:max_lines - 1] + [" · ".join(lines[max_lines - 1:])]
        return lines

    def _draw_status(self):
        self.screen.fill(C_BG)
        compact = self.compact_ui
        if compact:
            draw_text(self.screen, "운영 점검", self.f_big, C_PINK,
                      SCREEN_W // 2, 34, anchor="center", max_width=SCREEN_W - 80)
            draw_text(self.screen, "카메라 · 프린터 · QR · 저장공간 · AI 상태",
                      self.f_tiny, C_GRAY, SCREEN_W // 2, 72, anchor="center",
                      max_width=SCREEN_W - 80)
        else:
            draw_text(self.screen, "운영 점검", self.f_big, C_PINK,
                      SCREEN_W // 2, 90, anchor="center", max_width=900)
            draw_text(self.screen, "카메라 · 프린터 · QR · 저장공간 · AI 상태",
                      self.f_medium, C_GRAY, SCREEN_W // 2, 170, anchor="center",
                      max_width=1000)

        if not self.status_lines or time.time() - self.status_checked_at > 10:
            self._refresh_status()

        rows = max(1, len(self.status_lines))
        if compact:
            x, w = 30, SCREEN_W - 60
            top, bottom = 94, self.btn_status_close.rect.y - 10
            badge_x, title_x, title_w = x + 52, x + 100, 140
            detail_font, title_font, badge_font = self.f_tiny, self.f_small, self.f_small
        else:
            x, w = 300, 1320
            top, bottom = 214, self.btn_status_close.rect.y - 24
            badge_x, title_x, title_w = x + 70, x + 160, 200
            detail_font, title_font, badge_font = self.f_small, self.f_medium, self.f_medium
        pitch = (bottom - top) // rows
        row_h = pitch - (6 if compact else 10)
        detail_x = title_x + title_w + 16
        detail_w = x + w - detail_x - 20
        line_h = detail_font.get_height()
        for i, (title, detail, ok) in enumerate(self.status_lines):
            y = top + i * pitch
            cy = y + row_h // 2
            color = C_GREEN if ok else C_PINK
            draw_rrect(self.screen, C_WHITE, (x, y, w, row_h), radius=12)
            draw_text(self.screen, "정상" if ok else "확인", badge_font, color,
                      badge_x, cy, anchor="center", max_width=90)
            draw_text(self.screen, title, title_font, C_DARK,
                      title_x, cy - title_font.get_height() // 2,
                      anchor="topleft", max_width=title_w)
            lines = self._wrap_detail(detail, detail_font, detail_w,
                                      max_lines=max(1, row_h // line_h))
            ly = cy - len(lines) * line_h // 2
            for line in lines:
                draw_text(self.screen, line, detail_font, C_GRAY,
                          detail_x, ly, anchor="topleft", max_width=detail_w)
                ly += line_h

        self.btn_status_close.draw(self.screen, self.f_medium)

    def _draw_printing(self):
        self.screen.fill(C_BG)
        elapsed = time.time() - self.state_time

        if not self._print_done:
            dots = "." * (int(elapsed * 2) % 4)
            draw_text(self.screen, f"인쇄 중{dots}", self.f_big, C_PINK,
                      SCREEN_W // 2, SCREEN_H // 2 - 60, anchor="center",
                      max_width=1200)
            draw_text(self.screen, f"{self.print_copies}장 출력 요청 · CUPS 작업을 확인 중입니다",
                      self.f_medium, C_GRAY,
                      SCREEN_W // 2, SCREEN_H // 2 + 40, anchor="center",
                      max_width=1400)
            draw_text(self.screen, "프린터에서 용지가 움직이기 시작할 때까지 전원을 끄지 마세요",
                      self.f_small, C_GRAY,
                      SCREEN_W // 2, SCREEN_H // 2 + 92, anchor="center",
                      max_width=1400)

            # 프린터 아이콘 바운스
            bounce_y = int(math.sin(elapsed * 3) * 8)
            icon = self.f_huge.render("PRINT", True, C_LPINK)
            self.screen.blit(icon, icon.get_rect(
                center=(SCREEN_W // 2, SCREEN_H // 2 - 200 + bounce_y)))
        else:
            done_elapsed = time.time() - self._print_done_at if self._print_done_at else 0
            remain = max(0, PRINT_RESULT_TIMEOUT - int(done_elapsed))
            if self._print_ok:
                draw_text(self.screen, "인쇄 완료!", self.f_big, C_GREEN,
                          SCREEN_W // 2, SCREEN_H // 2 - 50, anchor="center",
                          max_width=1200)
                draw_text(self.screen, f"프린터에서 사진 {self.print_copies}장을 가져가세요",
                          self.f_large, C_DARK,
                          SCREEN_W // 2, SCREEN_H // 2 + 50, anchor="center",
                          max_width=1400)
                draw_text(self.screen, f"{remain}초 후 대기 화면으로 돌아갑니다",
                          self.f_small, C_GRAY,
                          SCREEN_W // 2, SCREEN_H // 2 + 120, anchor="center",
                          max_width=1200)
            else:
                draw_text(self.screen, "인쇄 오류", self.f_big, C_GRAY,
                          SCREEN_W // 2, SCREEN_H // 2 - 50, anchor="center",
                          max_width=1200)
                draw_text(self.screen, "프린터 연결을 확인해 주세요",
                          self.f_large, C_DARK,
                          SCREEN_W // 2, SCREEN_H // 2 + 50, anchor="center",
                          max_width=1400)
                draw_text(self.screen, f"{remain}초 후 리뷰 화면으로 돌아갑니다",
                          self.f_small, C_GRAY,
                          SCREEN_W // 2, SCREEN_H // 2 + 120, anchor="center",
                          max_width=1200)

            if done_elapsed >= PRINT_RESULT_TIMEOUT:
                self._finish_print_result()

    # ═══════════════════════════════════════════════
    #  QR_SHOW 렌더링
    # ═══════════════════════════════════════════════
    def _draw_qr(self):
        self.screen.fill(C_BG)
        elapsed = time.time() - self.state_time
        remain  = max(0, QR_SHOW_TIMEOUT - int(elapsed))

        draw_text(self.screen, "QR 코드로 사진 받기", self.f_large, C_PINK,
                  SCREEN_W // 2, 50, anchor="center", max_width=900)

        if self.qr_surface:
            qr_surf = self.qr_surface
            qw, qh = qr_surf.get_size()
            max_qr = min(SCREEN_W - 160, SCREEN_H - 320, 420)
            if self.compact_ui:
                max_qr = min(SCREEN_W - 180, SCREEN_H - 280, 320)
            if max_qr > 0 and (qw > max_qr or qh > max_qr):
                scale = min(max_qr / qw, max_qr / qh)
                qr_surf = pygame.transform.smoothscale(
                    qr_surf,
                    (max(1, int(qw * scale)), max(1, int(qh * scale))),
                )
                qw, qh = qr_surf.get_size()
            qx = SCREEN_W // 2 - qw // 2
            qy = 110 if self.compact_ui else 100
            draw_rrect(self.screen, C_WHITE,
                        (qx - 24, qy - 24, qw + 48, qh + 48), radius=24)
            self.screen.blit(qr_surf, (qx, qy))

            # 안내문
            dy = qy + qh + 50
            draw_text(self.screen,
                      "같은 WiFi에 연결된 스마트폰으로 스캔하세요",
                      self.f_medium, C_DARK,
                      SCREEN_W // 2, dy, anchor="center", max_width=1200)
            dy += 44
            draw_text(self.screen, "미리보기 화면에서 사진 다운로드 버튼을 누르세요",
                      self.f_small, C_DARK,
                      SCREEN_W // 2, dy, anchor="center", max_width=1200)
            dy += 34
            draw_text(self.screen, self.qr_url, self.f_small, C_GRAY,
                      SCREEN_W // 2, dy, anchor="center", max_width=1500)
            dy += 36
            draw_text(self.screen,
                      f"화면을 터치하면 사진 확인으로 돌아갑니다 · {remain}초 후 자동 복귀",
                      self.f_small, C_GRAY,
                      SCREEN_W // 2, dy, anchor="center", max_width=1200)

        if elapsed >= QR_SHOW_TIMEOUT:
            self._return_to_review_from_qr()

    # ═══════════════════════════════════════════════
    #  메인 루프
    # ═══════════════════════════════════════════════
    def run(self):
        prev_time = time.time()
        running   = True

        while running:
            now  = time.time()
            dt   = now - prev_time
            dt   = min(dt, 0.1)   # 최대 100ms 클램프 (장시간 정지 방지)
            prev_time = now

            running = self._handle_events()
            self._cleanup_old_photos()
            if self.ai_manager is not None:
                self._poll_ai_results()

            # ── 상태별 업데이트 & 렌더링 ───────────────
            if self.state == St.IDLE:
                self._draw_idle(dt)

            elif self.state == St.CONCEPT_SELECT:
                if not self._ai_select_timed_out():
                    self._draw_concept_select()

            elif self.state == St.CONSENT:
                if not self._ai_select_timed_out():
                    self._draw_consent()

            elif self.state == St.COUNTDOWN:
                self._update_countdown()
                if self.state == St.COUNTDOWN:
                    if self.session_mode == "id":
                        self._draw_id_stage()
                    else:
                        self._draw_countdown()

            elif self.state == St.FLASH:
                self._draw_flash()

            elif self.state == St.AI_PROCESSING:
                self._update_ai_processing()
                if self.state == St.AI_PROCESSING:
                    self._draw_ai_processing()

            elif self.state == St.REVIEW:
                self._draw_review()

            elif self.state in ID_STAGES:
                self._draw_id_stage()

            elif self.state == St.PRINTING:
                self._draw_printing()

            elif self.state == St.QR_SHOW:
                self._draw_qr()

            elif self.state == St.STATUS:
                self._draw_status()

            self._present()
            self.clock.tick(FPS)

        # ── 정리 ──────────────────────────────────────
        self._ai_health_stop.set()
        if self.ai_manager is not None:
            self.ai_manager.shutdown()
        self.camera.stop()
        self.qr_server.stop()
        pygame.quit()
        log.info("포토부스 종료")


# ════════════════════════════════════════════════════
#  진입점
# ════════════════════════════════════════════════════
def _set_runtime_value(name: str, value):
    setattr(cfg, name, value)
    globals()[name] = value


def main():
    parser = argparse.ArgumentParser(description=BRAND_NAME)
    parser.add_argument("--test", action="store_true",
                        help="카메라 없이 테스트 모드 실행")
    parser.add_argument("--window", action="store_true",
                        help="창 모드로 실행 (디버그용)")
    parser.add_argument("--fullscreen", action="store_true",
                        help="전체화면으로 실행")
    parser.add_argument("--camera-index", type=int,
                        help="OpenCV 카메라 번호 (/dev/video0이면 0)")
    parser.add_argument("--camera-device",
                        help="카메라 장치 경로 예: /dev/video0")
    parser.add_argument("--capture-orientation", choices=["portrait", "landscape"],
                        help="촬영/저장 방향 선택")
    parser.add_argument("--portrait-rotation",
                        choices=["clockwise", "counterclockwise"],
                        help="세로 촬영 시 카메라 회전 방향")
    parser.add_argument("--printer",
                        help="CUPS 프린터 이름 예: CP1500")
    parser.add_argument("--qr-port", type=int,
                        help="QR 다운로드 서버 포트")
    parser.add_argument("--no-audio", action="store_true",
                        help="효과음 없이 실행")
    ai_group = parser.add_mutually_exclusive_group()
    ai_group.add_argument("--ai-mock", action="store_true",
                          help="AI 변신을 Mock Provider로 켜기 (API 호출 없음)")
    ai_group.add_argument("--no-ai", action="store_true",
                          help="AI 변신 연결 없이 실행")
    cursor = parser.add_mutually_exclusive_group()
    cursor.add_argument("--show-cursor", action="store_true",
                        help="마우스 커서 표시")
    cursor.add_argument("--hide-cursor", action="store_true",
                        help="마우스 커서 숨김")
    args = parser.parse_args()

    if args.window:
        _set_runtime_value("FULLSCREEN", False)
    if args.fullscreen:
        _set_runtime_value("FULLSCREEN", True)
    if args.camera_index is not None:
        _set_runtime_value("CAM_INDEX", args.camera_index)
    if args.camera_device:
        _set_runtime_value("CAM_DEVICE", args.camera_device)
    if args.capture_orientation:
        _set_runtime_value("CAPTURE_ORIENTATION", args.capture_orientation)
    if args.portrait_rotation:
        _set_runtime_value("PORTRAIT_ROTATION", args.portrait_rotation)
    if args.printer:
        _set_runtime_value("PRINTER_NAME", args.printer)
    if args.qr_port is not None:
        _set_runtime_value("QR_SERVER_PORT", args.qr_port)
    if args.no_audio:
        _set_runtime_value("AUDIO_ENABLED", False)
    if args.ai_mock:
        _set_runtime_value("AI_ENABLED", True)
        _set_runtime_value("AI_PROVIDER", "mock")
    if args.no_ai:
        _set_runtime_value("AI_ENABLED", False)
    if args.show_cursor:
        _set_runtime_value("MOUSE_VISIBLE", True)
    if args.hide_cursor:
        _set_runtime_value("MOUSE_VISIBLE", False)

    try:
        booth = PhotoBooth(test_mode=args.test)
        booth.run()
    except KeyboardInterrupt:
        log.info("사용자 종료")
    except Exception as e:
        log.exception(f"치명적 오류: {e}")
        pygame.quit()
        sys.exit(1)


if __name__ == "__main__":
    main()
