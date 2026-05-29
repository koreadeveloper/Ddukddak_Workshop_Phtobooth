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
                 text_color=C_WHITE, radius=22):
        self.rect       = pygame.Rect(x, y, w, h)
        self.label      = label
        self.color      = color
        self.hover_col  = tuple(min(c + 25, 255) for c in color)
        self.press_col  = tuple(max(c - 30, 0) for c in color)
        self.text_color = text_color
        self.radius     = radius
        self._hov       = False
        self._pressed   = False

    def set_color(self, color):
        self.color     = color
        self.hover_col = tuple(min(c + 25, 255) for c in color)
        self.press_col = tuple(max(c - 30, 0) for c in color)

    def update(self, mpos, pressed=False):
        self._hov     = self.rect.collidepoint(mpos)
        self._pressed = pressed and self._hov

    def draw(self, surf, font):
        col = self.press_col if self._pressed else (
              self.hover_col if self._hov else self.color)
        draw_rrect(surf, col, self.rect, self.radius)
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
        self.clock = pygame.time.Clock()

        # ── 폰트 ──────────────────────────────────────
        fp = find_korean_font()
        self.f_huge   = pygame.font.Font(fp, 160)
        self.f_big    = pygame.font.Font(fp, 78)
        self.f_large  = pygame.font.Font(fp, 52)
        self.f_medium = pygame.font.Font(fp, 36)
        self.f_small  = pygame.font.Font(fp, 24)
        self.f_tiny   = pygame.font.Font(fp, 18)

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
            else "soft_pink"
        )
        self.selected_print_layout = "grid"
        self.print_copies = max(1, min(DEFAULT_PRINT_COPIES, MAX_PRINT_COPIES))
        self._compose_generation = 0
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

        # ── 버튼 (REVIEW 화면) ─────────────────────────
        self.btn_start = Button(
            SCREEN_W // 2 - 250, 930, 500, 92, "촬영 시작", C_PINK, radius=28
        )
        self.btn_status = Button(
            1540, 38, 270, 58, "운영 점검", (110, 110, 120), radius=18
        )
        self.review_frame_buttons = []
        for i, (theme_id, theme) in enumerate(composer.FRAME_THEMES.items()):
            x = 1120 + (i % 2) * 220
            y = 170 + (i // 2) * 68
            self.review_frame_buttons.append(
                (theme_id, Button(x, y, 200, 52, theme["name"], (110, 110, 120), radius=16))
            )
        self.review_filter_buttons = []
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
        self.btn_print  = Button(bx,            by, bw, bh, "인쇄하기",  C_PINK)
        self.btn_qr     = Button(bx + bw + gap, by, bw, bh, "QR 받기",  C_BLUE)
        self.btn_retake = Button(bx+2*(bw+gap), by, bw, bh, "전체 다시 찍기",
                                 (110, 110, 120))
        self.btn_retake_selected = Button(
            700, 660, 316, 64, "선택 컷 다시 찍기", C_BLUE, radius=18
        )
        self.btn_copies_minus = Button(1120, 760, 70, 58, "-", (110, 110, 120), radius=16)
        self.btn_copies_plus = Button(1490, 760, 70, 58, "+", C_BLUE, radius=16)
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
        if self.capture_orientation == "portrait":
            return 150, 220
        return 230, 150

    def _refresh_processed_photos(self):
        if len(self.captured_source_bgr) == len(self.captured_raw_bgr):
            self.captured_raw_bgr = [
                self._oriented_frame(source).copy()
                for source in self.captured_source_bgr
            ]
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
            self.captured_source_bgr.append(source)
            self.captured_raw_bgr.append(raw)
            self.captured_bgr.append(processed)
            log.info(f"촬영 {len(self.captured_bgr)}/{PHOTO_COUNT}")
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
                strip_path = composer.compose_print_image(photos, session_id, frame_theme, print_layout)

                # 미리보기 Surface
                preview = composer.make_preview_image(photos, 820, frame_theme, print_layout)
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
                    else:
                        self._begin_countdown()
                elif self.state == St.QR_SHOW:
                    self._set_state(St.IDLE)
                    self._reset_session()
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
            self._set_state(St.IDLE)
            self._reset_session()

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
            btn.set_color(active_color if item_id == selected_id else (110, 110, 120))
            btn.draw(self.screen, self.f_small)

    def _draw_idle(self, dt):
        self.screen.fill(C_BG)

        for p in self.particles:
            p.update(dt)
            p.draw(self.screen)

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
            if n >= PHOTO_COUNT:
                self._start_compose()
                self._set_state(St.REVIEW)
                if self.snd_done:
                    self.snd_done.play()
            else:
                self._begin_countdown(new_session=False)

    # ═══════════════════════════════════════════════
    #  REVIEW 렌더링
    # ═══════════════════════════════════════════════
    def _draw_review(self):
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
        for btn in (self.btn_print, self.btn_qr, self.btn_retake):
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
            qw, qh = self.qr_surface.get_size()
            qx = SCREEN_W // 2 - qw // 2
            qy = 100
            draw_rrect(self.screen, C_WHITE,
                       (qx - 24, qy - 24, qw + 48, qh + 48), radius=24)
            self.screen.blit(self.qr_surface, (qx, qy))

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
                      f"화면을 터치하면 바로 종료 · {remain}초 후 자동 복귀",
                      self.f_small, C_GRAY,
                      SCREEN_W // 2, dy, anchor="center", max_width=1200)

        if elapsed >= QR_SHOW_TIMEOUT:
            self._set_state(St.IDLE)
            self._reset_session()

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

            # ── 상태별 업데이트 & 렌더링 ───────────────
            if self.state == St.IDLE:
                self._draw_idle(dt)

            elif self.state == St.COUNTDOWN:
                self._update_countdown()
                if self.state == St.COUNTDOWN:
                    self._draw_countdown()

            elif self.state == St.FLASH:
                self._draw_flash()

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
