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


# ════════════════════════════════════════════════════
#  상태 상수
# ════════════════════════════════════════════════════
class St:
    IDLE      = "idle"
    COUNTDOWN = "countdown"
    FLASH     = "flash"
    AI_PROCESSING = "ai_processing"
    REVIEW    = "review"
    PRINTING  = "printing"
    QR_SHOW   = "qr_show"
    STATUS    = "status"


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
        action_h = 58
        action_y = SCREEN_H - action_h - 22
        card_top = 104 if SCREEN_H >= 720 else 88
        card_bottom = action_y - 34
        card_h = max(430, card_bottom - card_top)

        left_w = min(400, max(340, int(SCREEN_W * 0.39)))
        right_x = margin + left_w + gap
        right_w = SCREEN_W - margin - right_x
        if right_w < 520:
            left_w = max(330, SCREEN_W - margin * 2 - gap - 520)
            right_x = margin + left_w + gap
            right_w = SCREEN_W - margin - right_x

        left_card = pygame.Rect(margin, card_top, left_w, card_h)
        right_card = pygame.Rect(right_x, card_top, right_w, card_h)

        preview_h = min(
            card_h - 92,
            int(max(260, left_w - 72) * composer.PRINT_H / composer.PRINT_W),
        )
        preview_h = max(330, preview_h)

        inner_x = right_card.x + 28
        inner_right = right_card.right - 28
        thumb_gap = 12
        thumb_w = min(104, max(90, int((right_w * 0.40 - thumb_gap) / 2)))
        thumb_h = max(70, int(thumb_w * 0.68))
        portrait_thumb_w = max(76, int(thumb_w * 0.74))
        portrait_thumb_h = max(108, int(portrait_thumb_w * 1.45))

        thumb_grid_x = inner_x
        thumb_grid_y = right_card.y + 88
        divider_x = thumb_grid_x + 2 * thumb_w + thumb_gap + 24
        control_x = divider_x + 26
        choice_gap = 12
        choice_w = max(88, (inner_right - control_x - choice_gap) // 2)

        filter_gap = 10
        filter_w = max(78, (right_card.width - 56 - filter_gap * 4) // 5)
        action_gap = 30
        action_w = (SCREEN_W - margin * 2 - action_gap * 2) // 3
        action_x = margin

        return {
            "left_card": left_card,
            "right_card": right_card,
            "preview_h": preview_h,
            "thumb_gap": thumb_gap,
            "thumb_grid_x": thumb_grid_x,
            "thumb_grid_y": thumb_grid_y,
            "thumb_w": thumb_w,
            "thumb_h": thumb_h,
            "portrait_thumb_w": portrait_thumb_w,
            "portrait_thumb_h": portrait_thumb_h,
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
        self.compact_ui = SCREEN_W <= 1280 or SCREEN_H <= 800
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
        # ── AI 변신 (AI_ENABLED=0이면 manager 없음 → 일반 네컷만) ──
        self.ai_manager = None
        self.ai_concepts = []
        self.ai_generation = 0
        self.session_mode = "normal"
        self._ai_saved_look = None
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
            self.btn_start = Button(
                SCREEN_W // 2 - 180, SCREEN_H - 86, 360, 64, "촬영 시작", C_PINK, radius=22
            )
            self.btn_status = Button(
                SCREEN_W - 170, 18, 150, 44, "운영 점검", (110, 110, 120), radius=14
            )
        else:
            self.btn_start = Button(
                SCREEN_W // 2 - 250, 930, 500, 92, "촬영 시작", C_PINK, radius=28
            )
            self.btn_status = Button(
                1540, 38, 270, 58, "운영 점검", (110, 110, 120), radius=18
            )
        self.review_frame_buttons = []
        self.review_filter_buttons = []
        if self.compact_ui:
            layout = self._compact_review_layout()
            panel_x = layout["control_x"]
            col_w = layout["choice_w"]
            col_gap = layout["choice_gap"]
            button_h = 42
            for i, (theme_id, theme) in enumerate(composer.FRAME_THEMES.items()):
                x = panel_x + (i % 2) * (col_w + col_gap)
                y = layout["right_card"].y + 88 + (i // 2) * 50
                self.review_frame_buttons.append(
                    (theme_id, Button(x, y, col_w, button_h, theme["name"], C_WHITE,
                                      C_DARK, radius=14, border_color=C_LGRAY, border_width=2))
                )
            self.review_orientation_buttons = [
                ("portrait", Button(panel_x, layout["right_card"].y + 258, col_w, button_h,
                                    "세로", C_WHITE, C_DARK, radius=14,
                                    border_color=C_LGRAY, border_width=2)),
                ("landscape", Button(panel_x + col_w + col_gap, layout["right_card"].y + 258,
                                     col_w, button_h, "가로", C_WHITE, C_DARK, radius=14,
                                     border_color=C_LGRAY, border_width=2)),
            ]
            for i, (filter_id, meta) in enumerate(FILTERS.items()):
                x = layout["filter_x"] + i * (layout["filter_w"] + layout["filter_gap"])
                y = layout["right_card"].y + 384
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
            self.btn_retake = Button(bx,            by, bw, bh, "다시 찍기",
                                     C_WHITE, C_PINK, radius=18,
                                     border_color=C_PINK, border_width=2)
            self.btn_qr     = Button(bx + bw + gap, by, bw, bh, "QR 저장",
                                     C_WHITE, C_DARK, radius=18,
                                     border_color=C_LGRAY, border_width=2)
            self.btn_print  = Button(bx+2*(bw+gap), by, bw, bh, "인쇄하기",  C_PINK, radius=18)
            self.btn_retake_selected = Button(
                layout["thumb_grid_x"],
                layout["thumb_grid_y"] + 2 * (layout["thumb_h"] + layout["thumb_gap"]) + 20,
                2 * layout["thumb_w"] + layout["thumb_gap"], 44,
                "선택 컷 다시 찍기", C_WHITE, C_DARK, radius=14,
                border_color=C_LGRAY, border_width=2
            )
            copies_y = layout["right_card"].y + 492
            copies_mid = layout["right_card"].x + layout["right_card"].width // 2
            self.btn_copies_minus = Button(copies_mid - 86, copies_y, 46, 46, "-",
                                           C_WHITE, C_PINK, radius=12,
                                           border_color=C_PINK, border_width=2)
            self.btn_copies_plus = Button(copies_mid + 40, copies_y, 46, 46, "+",
                                          C_WHITE, C_PINK, radius=12,
                                          border_color=C_PINK, border_width=2)
            self.btn_status_close = Button(
                SCREEN_W // 2 - 140, SCREEN_H - 92, 280, 58, "돌아가기", C_BLUE, radius=18
            )
        else:
            for i, (theme_id, theme) in enumerate(composer.FRAME_THEMES.items()):
                x = 1120 + (i % 2) * 220
                y = 170 + (i // 2) * 68
                self.review_frame_buttons.append(
                    (theme_id, Button(x, y, 200, 52, theme["name"], (110, 110, 120), radius=16))
                )
            for i, (filter_id, meta) in enumerate(FILTERS.items()):
                x = 1120 + (i % 2) * 220
                y = 480 + (i // 2) * 64
                self.review_filter_buttons.append(
                    (filter_id, Button(x, y, 200, 50, meta["name"], (110, 110, 120), radius=16))
                )
            self.review_orientation_buttons = [
                ("portrait", Button(1120, 356, 200, 52, "세로", (110, 110, 120), radius=16)),
                ("landscape", Button(1340, 356, 200, 52, "가로", (110, 110, 120), radius=16)),
            ]

            bw, bh, gap = 330, 80, 36
            total = 3 * bw + 2 * gap
            bx = (SCREEN_W - total) // 2
            by = SCREEN_H - 115
            self.btn_retake = Button(bx,            by, bw, bh, "다시 찍기",
                                     C_WHITE, C_PINK, border_color=C_PINK, border_width=2)
            self.btn_qr     = Button(bx + bw + gap, by, bw, bh, "QR 저장",
                                     C_WHITE, C_DARK, border_color=C_LGRAY, border_width=2)
            self.btn_print  = Button(bx+2*(bw+gap), by, bw, bh, "인쇄하기",  C_PINK)
            self.btn_retake_selected = Button(
                700, 660, 316, 64, "선택 컷 다시 찍기", C_WHITE, C_DARK, radius=18,
                border_color=C_LGRAY, border_width=2
            )
            self.btn_copies_minus = Button(1120, 760, 70, 58, "-", C_WHITE, C_PINK,
                                           radius=16, border_color=C_PINK, border_width=2)
            self.btn_copies_plus = Button(1490, 760, 70, 58, "+", C_WHITE, C_PINK,
                                          radius=16, border_color=C_PINK, border_width=2)
            self.btn_status_close = Button(
                SCREEN_W // 2 - 165, SCREEN_H - 140, 330, 72, "돌아가기", C_BLUE, radius=20
            )
        self._audit_button_layout()

        PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
        self._cleanup_old_photos(force=True)
        log.info(f"포토부스 초기화 완료 (test_mode={test_mode})")

    # ─────────────────────────────────────────────────
    #  세션 초기화
    # ─────────────────────────────────────────────────
    def _reset_session(self):
        self.session_id     = uuid.uuid4().hex[:12]
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
        self.session_mode = "normal"            # "normal" | "ai"
        self.ai_concept_id = None
        self.ai_generation += 1                 # 세션 식별 (늦게 온 결과 무시용)
        self.ai_results = [None] * PHOTO_COUNT  # 성공 시 AI BGR (크롭 완료본)
        self.ai_status = ["idle"] * PHOTO_COUNT  # idle|pending|done|failed|refused|timeout
        self.ai_use_original = [False] * PHOTO_COUNT
        self.ai_regen_count = 0
        self.ai_processing_started_at = 0.0
        self._ai_recompose_needed = False

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
                log.warning("AI 비활성: PHOTOBOOTH_AI_API_KEY가 비어 있습니다")
                return
            concepts = ai_concepts.load_concepts()
            if not concepts:
                log.warning("AI 비활성: 사용할 수 있는 콘셉트가 없습니다")
                return
            self.ai_concepts = concepts
            self.ai_manager = ai_transform.AITransformManager(provider)
            log.info(f"AI 변신 준비 완료: provider={provider.name}, 콘셉트 {len(concepts)}개")
        except Exception as e:
            log.warning(f"AI 비활성: 초기화 실패 ({type(e).__name__}: {e})")
            self.ai_manager = None

    def _ai_available(self) -> bool:
        return self.ai_manager is not None and bool(self.ai_concepts)

    def _ai_concept(self):
        for concept in self.ai_concepts:
            if concept.id == self.ai_concept_id:
                return concept
        return None

    def _begin_ai_session(self, concept):
        self._begin_countdown()
        self.session_mode = "ai"
        self.ai_concept_id = concept.id
        self._ai_saved_look = (self.selected_filter, self.selected_frame_theme)
        if concept.default_filter in FILTERS:
            self.selected_filter = concept.default_filter
        if concept.default_frame in composer.FRAME_THEMES:
            self.selected_frame_theme = concept.default_frame
        booth_stats.increment("ai_sessions")
        booth_stats.increment_concept(concept.id)
        log.info(f"AI 변신 세션 시작: {concept.name} ({concept.id})")

    def _ai_submit(self, idx: int, raw: np.ndarray):
        """촬영 직후 해당 컷만 바로 변환 요청 (파이프라이닝)."""
        import ai_transform
        concept = self._ai_concept()
        self.ai_results[idx] = None
        self.ai_use_original[idx] = False
        if concept is None:
            self.ai_status[idx] = "failed"
            return
        try:
            upload = ai_transform.prepare_upload_image(raw)
            self.ai_manager.submit(self.ai_generation, idx, upload, concept)
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

    def _draw_ai_processing(self):
        self.screen.fill(C_BG)
        concept = self._ai_concept()
        cx, cy = SCREEN_W // 2, SCREEN_H // 2
        line = max(48, SCREEN_H // 14)
        draw_text(self.screen, "AI가 변신 중이에요", self.f_big, C_DARK,
                  cx, cy - line * 3, anchor="center", max_width=SCREEN_W - 80)
        if concept is not None:
            draw_text(self.screen, f"{concept.name} 변신", self.f_medium, C_PINK,
                      cx, cy - line * 2, anchor="center", max_width=SCREEN_W - 80)
        done = sum(1 for st in self.ai_status if st != "pending")
        draw_text(self.screen, f"{done} / {PHOTO_COUNT} 완료", self.f_large, C_DARK,
                  cx, cy - line // 2, anchor="center", max_width=SCREEN_W - 80)
        labels = {"pending": "변환 중", "done": "AI 완료", "failed": "원본 사용",
                  "refused": "원본 사용", "timeout": "원본 사용", "idle": "-"}
        status = "   ".join(f"{i + 1}번 {labels.get(st, st)}" for i, st in enumerate(self.ai_status))
        draw_text(self.screen, status, self.f_small, C_GRAY,
                  cx, cy + line // 2, anchor="center", max_width=SCREEN_W - 80)
        elapsed = int(time.time() - self.ai_processing_started_at)
        draw_text(self.screen, f"{elapsed}초 경과 · 보통 20~30초 걸려요", self.f_small, C_GRAY,
                  cx, cy + line * 3 // 2, anchor="center", max_width=SCREEN_W - 80)

    def _audit_button_layout(self):
        groups = {
            "idle": [
                self.btn_start,
                self.btn_status,
            ],
            "review": [
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

        remaining = len(self._photo_files())
        self.cleanup_summary = (
            f"사진 {remaining}개 보관 · 정리 {deleted}개 · "
            f"여유 {free_gb:.1f}/{total_gb:.1f}GB"
        )
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
            grid_x = 700
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
        self.status_lines = [
            ("카메라", camera_text, camera_health["ok"]),
            ("프린터", printer_text or PRINTER_NAME, printer_ok),
            ("QR", qr_text, self.qr_server.is_running),
            ("저장공간", f"여유 {free // (1024 ** 3)}GB / 전체 {total // (1024 ** 3)}GB", free > 1024 ** 3),
            ("사진 폴더", f"{photos_count}개 JPG 저장됨", True),
            ("오늘 운영", stats_text, True),
            ("보관 정리", self.cleanup_summary, True),
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
        if self.session_mode == "ai" and self.ai_manager is not None and idx < PHOTO_COUNT:
            self._ai_submit(idx, raw)
        return True

    def _start_compose(self):
        """백그라운드 스레드로 스트립 합성"""
        if self._composing:
            return
        if len(self.captured_bgr) != PHOTO_COUNT:
            log.warning(
                f"합성 건너뜀: 사진 {len(self.captured_bgr)}/{PHOTO_COUNT}장"
            )
            return
        self._composing = True
        self._compose_generation += 1
        generation = self._compose_generation
        photos = [p.copy() for p in self.captured_bgr]
        session_id = self.session_id
        frame_theme = self.selected_frame_theme
        print_layout = self.selected_print_layout
        label_text = None
        ai_disclosure = False
        if self.session_mode == "ai":
            used_ai = any(r is not None and not self.ai_use_original[i]
                          for i, r in enumerate(self.ai_results))
            concept = self._ai_concept()
            if used_ai and concept is not None:
                label_text = concept.print_label
                ai_disclosure = AI_PRINT_DISCLOSURE

        def _work():
            try:
                # 썸네일 (빠름)
                surfs = []
                thumb_w, thumb_h = self._review_thumb_size()
                for bgr in photos:
                    surfs.append(bgr_to_surface(bgr, (thumb_w, thumb_h)))
                if generation == self._compose_generation:
                    self.thumb_surfaces = surfs

                # 풀 스트립 저장
                strip_path = composer.compose_print_image(
                    photos, session_id, frame_theme, print_layout, label_text, ai_disclosure
                )

                # 미리보기 Surface
                preview = composer.make_preview_image(
                    photos, self._print_preview_target_h(), frame_theme, print_layout,
                    label_text, ai_disclosure,
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

        def _work():
            if self.strip_path and self.strip_path.exists():
                ok = printer.print_photo(self.strip_path, self.print_copies)
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

            # ── 키보드 ────────────────────────────────
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    return False
                if event.key == pygame.K_q and (
                        pygame.key.get_mods() & pygame.KMOD_CTRL):
                    return False

                if self.state == St.IDLE:
                    if event.key == pygame.K_s:
                        self._refresh_status()
                        self._set_state(St.STATUS)
                    elif event.key == pygame.K_a and self._ai_available():
                        # 임시 (Phase 2): A 키 = 첫 번째 콘셉트로 AI 모드 시작
                        self._begin_ai_session(self.ai_concepts[0])
                    else:
                        self._begin_countdown()
                elif self.state == St.QR_SHOW:
                    self._return_to_review_from_qr()
                elif self.state == St.STATUS:
                    self._set_state(St.IDLE)

            # ── 마우스/터치 클릭 ──────────────────────
            if event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                if event.type == pygame.FINGERDOWN:
                    pos = (int(event.x * SCREEN_W), int(event.y * SCREEN_H))
                else:
                    if event.button != 1:
                        continue
                    pos = self._event_pos_to_ui(event.pos)

                self._handle_click(pos)

        # 버튼 hover 업데이트
        if self.state == St.IDLE:
            self.btn_start.update(mpos, mpressed)
            self.btn_status.update(mpos, mpressed)
        elif self.state == St.REVIEW:
            for btn in (self.btn_print, self.btn_qr, self.btn_retake, self.btn_retake_selected):
                btn.update(mpos, mpressed)
            for _, btn in self.review_frame_buttons:
                btn.update(mpos, mpressed)
            for _, btn in self.review_orientation_buttons:
                btn.update(mpos, mpressed)
            for _, btn in self.review_filter_buttons:
                btn.update(mpos, mpressed)
            self.btn_copies_minus.update(mpos, mpressed)
            self.btn_copies_plus.update(mpos, mpressed)
        elif self.state == St.STATUS:
            self.btn_status_close.update(mpos, mpressed)

        return True

    def _handle_click(self, pos):
        if self.state == St.IDLE:
            if self.btn_status.hit(pos):
                self._refresh_status()
                self._set_state(St.STATUS)
                return
            self._begin_countdown()

        elif self.state == St.REVIEW:
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
            for orientation, btn in self.review_orientation_buttons:
                if btn.hit(pos):
                    if self.session_mode == "ai":
                        self._set_review_notice("AI 변신 모드에서는 촬영 방향을 바꿀 수 없어요.")
                        return
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
    def _draw_selected_button_group(self, selected_id, buttons, active_color=C_BLUE):
        for item_id, btn in buttons:
            if item_id == selected_id:
                btn.set_style(active_color, C_WHITE, border_width=0)
            else:
                btn.set_style(C_WHITE, C_DARK, border_color=C_LGRAY, border_width=2)
            btn.draw(self.screen, self.f_small)

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
            preview_h = max(360, SCREEN_H - 250)
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

            self.btn_start.draw(self.screen, self.f_large)
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
            box_w, box_h = 820, 680
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

        self.btn_start.draw(self.screen, self.f_large)

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
        draw_text(self.screen, shot_label, self.f_large, C_WHITE,
                  SCREEN_W // 2, 44, anchor="center", max_width=1200)

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

        draw_text(self.screen, "사진 확인", self.f_big, C_PINK,
                  SCREEN_W // 2, 34, anchor="center", max_width=760)
        draw_text(self.screen, "원하는 컷과 스타일을 선택하세요",
                  self.f_tiny, C_GRAY, SCREEN_W // 2, 76,
                  anchor="center", max_width=760)

        draw_card(self.screen, left_card, radius=16)
        draw_card(self.screen, right_card, radius=16)

        preview_x = left_card.centerx
        preview_y = left_card.y + 36
        if self.strip_surface:
            sw, sh = self.strip_surface.get_size()
            px = preview_x - sw // 2
            draw_rrect(self.screen, C_LPINK,
                       (px - 8, preview_y - 8, sw + 16, sh + 16), radius=12)
            self.screen.blit(self.strip_surface, (px, preview_y))
        else:
            cx = left_card.centerx
            cy = left_card.centery
            dots = "." * (int(time.time() * 2.5) % 4)
            draw_text(self.screen, f"합성 중{dots}", self.f_medium, C_GRAY,
                      cx, cy, anchor="center", max_width=left_card.width - 60)

        theme_name = composer.FRAME_THEMES[self.selected_frame_theme]["name"]
        filter_name = FILTERS[self.selected_filter]["name"]
        draw_text(self.screen, f"{theme_name} 프레임 · {filter_name} 필터 · 2x2 · {self.print_copies}장",
                  self.f_small, C_DARK, left_card.centerx, left_card.bottom - 38,
                  anchor="center", max_width=left_card.width - 46)

        rects = self._review_thumb_rects()
        draw_text(self.screen, "촬영 컷", self.f_medium, C_DARK,
                  rects[0].x, right_card.y + 36, anchor="topleft", max_width=260)

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
        else:
            for i, rect in enumerate(rects):
                border_color = C_PINK if i == self.selected_retake_index else C_LGRAY
                draw_rrect(self.screen, C_WHITE, rect, radius=10,
                           border=2, bcol=border_color)

        self.btn_retake_selected.draw(self.screen, self.f_small)

        divider_x = layout["divider_x"]
        pygame.draw.line(
            self.screen, C_LGRAY,
            (divider_x, right_card.y + 42),
            (divider_x, right_card.y + 292),
            2,
        )

        panel_x = layout["control_x"]
        draw_text(self.screen, "프레임", self.f_medium, C_DARK,
                  panel_x, right_card.y + 42, anchor="topleft", max_width=250)
        self._draw_selected_button_group(
            self.selected_frame_theme, self.review_frame_buttons, C_PINK)

        pygame.draw.line(
            self.screen, C_LGRAY,
            (panel_x, right_card.y + 210),
            (right_card.right - 28, right_card.y + 210),
            1,
        )
        draw_text(self.screen, "촬영 방향", self.f_medium, C_DARK,
                  panel_x, right_card.y + 222, anchor="topleft", max_width=250)
        self._draw_selected_button_group(
            self.capture_orientation, self.review_orientation_buttons, C_BLUE)

        pygame.draw.line(
            self.screen, C_LGRAY,
            (right_card.x + 24, right_card.y + 326),
            (right_card.right - 24, right_card.y + 326),
            1,
        )
        draw_text(self.screen, "필터", self.f_medium, C_DARK,
                  layout["filter_x"], right_card.y + 348, anchor="topleft", max_width=280)
        self._draw_selected_button_group(
            self.selected_filter, self.review_filter_buttons, C_BLUE)

        pygame.draw.line(
            self.screen, C_LGRAY,
            (right_card.x + 24, right_card.y + 452),
            (right_card.right - 24, right_card.y + 452),
            1,
        )
        draw_text(self.screen, "인쇄 매수", self.f_medium, C_DARK,
                  layout["filter_x"], right_card.y + 454, anchor="topleft", max_width=200)
        self.btn_copies_minus.draw(self.screen, self.f_large)
        self.btn_copies_plus.draw(self.screen, self.f_large)
        copies_center_x = (self.btn_copies_minus.rect.right + self.btn_copies_plus.rect.left) // 2
        draw_text(self.screen, f"{self.print_copies} 장", self.f_large, C_DARK,
                  copies_center_x, self.btn_copies_minus.rect.centery, anchor="center",
                  max_width=150)

        self.btn_retake.set_style(C_WHITE, C_PINK, border_color=C_PINK, border_width=2)
        self.btn_qr.set_style(C_WHITE, C_DARK, border_color=C_LGRAY, border_width=2)
        self.btn_print.set_style(C_PINK, C_WHITE, border_width=0)
        for btn in (self.btn_retake, self.btn_qr, self.btn_print):
            btn.draw(self.screen, self.f_medium)

        if self._composing:
            for btn in (self.btn_print, self.btn_qr, self.btn_retake_selected):
                dim = pygame.Surface(
                    (btn.rect.width, btn.rect.height),
                    pygame.SRCALPHA)
                dim.fill((180, 180, 180, 120))
                self.screen.blit(dim, btn.rect.topleft)

        if self.review_notice and time.time() < self.review_notice_until:
            draw_text(self.screen, self.review_notice, self.f_small, C_PINK,
                      SCREEN_W // 2, layout["action_y"] - 18, anchor="center",
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
            sw, sh = self.strip_surface.get_size()
            draw_rrect(self.screen, C_LPINK,
                       (preview_x - 10, preview_y - 10, sw + 20, sh + 20), radius=14)
            self.screen.blit(self.strip_surface, (preview_x, preview_y))
        else:
            cx = preview_x + 280
            cy = SCREEN_H // 2 - 60
            dots = "." * (int(time.time() * 2.5) % 4)
            draw_text(self.screen, f"합성 중{dots}", self.f_medium, C_GRAY,
                      cx, cy, anchor="center", max_width=520)

        # ── 가운데: 4개 촬영 컷 ───────────────────────
        grid_x = 700
        rects = self._review_thumb_rects()
        draw_text(self.screen, "촬영 컷", self.f_medium, C_DARK,
                  grid_x, 96, anchor="topleft")

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
        else:
            # 썸네일 아직 없을 때 (빠르게 생성되므로 드뭄)
            for i in range(4):
                rect = rects[i]
                border_color = C_BLUE if i == self.selected_retake_index else C_LGRAY
                draw_rrect(self.screen, border_color, rect, radius=12)

        draw_text(self.screen, "썸네일을 눌러 다시 찍을 컷을 고르세요.",
                  self.f_tiny, C_GRAY, 858, 626, anchor="center",
                  max_width=390)
        self.btn_retake_selected.draw(self.screen, self.f_small)

        # ── 우측: 촬영 후 프레임/필터 선택 ─────────────
        draw_text(self.screen, "프레임 다시 선택", self.f_medium, C_DARK,
                  1120, 126, anchor="topleft")
        self._draw_selected_button_group(
            self.selected_frame_theme, self.review_frame_buttons, C_PINK)

        draw_text(self.screen, "촬영 방향 다시 선택", self.f_medium, C_DARK,
                  1120, 314, anchor="topleft")
        self._draw_selected_button_group(
            self.capture_orientation, self.review_orientation_buttons, C_BLUE)

        draw_text(self.screen, "필터 다시 선택", self.f_medium, C_DARK,
                  1120, 436, anchor="topleft")
        self._draw_selected_button_group(
            self.selected_filter, self.review_filter_buttons, C_BLUE)

        theme_name = composer.FRAME_THEMES[self.selected_frame_theme]["name"]
        filter_name = FILTERS[self.selected_filter]["name"]
        draw_text(self.screen, f"{theme_name} 프레임 · {filter_name} 필터 · 2x2 고정",
                  self.f_small, C_GRAY, 1340, 690, anchor="center",
                  max_width=500)

        draw_text(self.screen, "인쇄 매수", self.f_medium, C_DARK,
                  1120, 724, anchor="topleft")
        self.btn_copies_minus.draw(self.screen, self.f_large)
        self.btn_copies_plus.draw(self.screen, self.f_large)
        draw_text(self.screen, f"{self.print_copies} 장", self.f_large, C_DARK,
                  1340, 790, anchor="center")

        # ── 하단 버튼 ──────────────────────────────────
        for btn in (self.btn_retake, self.btn_qr, self.btn_print):
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
    def _draw_status(self):
        self.screen.fill(C_BG)
        draw_text(self.screen, "운영 점검", self.f_big, C_PINK,
                  SCREEN_W // 2, 90, anchor="center", max_width=900)
        draw_text(self.screen, "카메라 · 프린터 · QR · 저장공간 상태",
                  self.f_medium, C_GRAY, SCREEN_W // 2, 178, anchor="center",
                  max_width=1000)

        if not self.status_lines or time.time() - self.status_checked_at > 10:
            self._refresh_status()

        y = 250
        for title, detail, ok in self.status_lines:
            color = C_GREEN if ok else C_PINK
            draw_rrect(self.screen, C_WHITE, (360, y - 16, 1200, 78), radius=12)
            draw_text(self.screen, "정상" if ok else "확인", self.f_medium, color,
                      430, y + 22, anchor="center", max_width=115)
            draw_text(self.screen, title, self.f_medium, C_DARK,
                      540, y + 2, anchor="topleft", max_width=190)
            draw_text(self.screen, detail, self.f_small, C_GRAY,
                      760, y + 8, anchor="topleft", max_width=760)
            y += 92

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

            elif self.state == St.COUNTDOWN:
                self._update_countdown()
                if self.state == St.COUNTDOWN:
                    self._draw_countdown()

            elif self.state == St.FLASH:
                self._draw_flash()

            elif self.state == St.AI_PROCESSING:
                self._update_ai_processing()
                if self.state == St.AI_PROCESSING:
                    self._draw_ai_processing()

            elif self.state == St.REVIEW:
                self._draw_review()

            elif self.state == St.PRINTING:
                self._draw_printing()

            elif self.state == St.QR_SHOW:
                self._draw_qr()

            elif self.state == St.STATUS:
                self._draw_status()

            self._present()
            self.clock.tick(FPS)

        # ── 정리 ──────────────────────────────────────
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
                          help="AI 변신 끄기 (일반 네컷만)")
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
