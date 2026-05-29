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

import cv2
import pygame
import numpy as np

# ─── 내부 모듈 ───────────────────────────────────────
import config as cfg
from config import *
from camera import Camera, MockCamera
import composer
from qr_share import QRServer
import printer

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


def pil_to_surface(pil_img) -> pygame.Surface:
    """PIL Image → pygame Surface"""
    arr = np.array(pil_img.convert("RGB"))
    return pygame.surfarray.make_surface(arr.swapaxes(0, 1))


def draw_text(surface, text, font, color, x, y, anchor="topleft",
              shadow=False, shadow_color=(0, 0, 0), shadow_off=3):
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

    def update(self, mpos, pressed=False):
        self._hov     = self.rect.collidepoint(mpos)
        self._pressed = pressed and self._hov

    def draw(self, surf, font):
        col = self.press_col if self._pressed else (
              self.hover_col if self._hov else self.color)
        draw_rrect(surf, col, self.rect, self.radius)
        draw_text(surf, self.label, font, self.text_color,
                  self.rect.centerx, self.rect.centery, anchor="center")

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
        pygame.display.set_caption("뚝딱 포토부스")

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

        # ── 파티클 ────────────────────────────────────
        self.particles = [Particle() for _ in range(22)]

        # ── 버튼 (REVIEW 화면) ─────────────────────────
        self.btn_start = Button(
            SCREEN_W // 2 - 220, 405, 440, 92, "촬영 시작", C_PINK, radius=28
        )

        bw, bh, gap = 330, 80, 36
        total = 3 * bw + 2 * gap
        bx = (SCREEN_W - total) // 2
        by = SCREEN_H - 115
        self.btn_print  = Button(bx,            by, bw, bh, "인쇄하기",  C_PINK)
        self.btn_qr     = Button(bx + bw + gap, by, bw, bh, "QR 받기",  C_BLUE)
        self.btn_retake = Button(bx+2*(bw+gap), by, bw, bh, "다시 찍기",
                                 (110, 110, 120))

        PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
        log.info(f"포토부스 초기화 완료 (test_mode={test_mode})")

    # ─────────────────────────────────────────────────
    #  세션 초기화
    # ─────────────────────────────────────────────────
    def _reset_session(self):
        self.session_id     = str(uuid.uuid4())[:8]
        self.captured_bgr   = []        # BGR ndarray 목록
        self.strip_path     = None
        self.strip_surface  = None
        self.thumb_surfaces = []
        self.qr_surface     = None
        self.qr_url         = ""
        self._composing     = False
        self._print_done    = False
        self._print_ok      = False

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
    def _begin_countdown(self, new_session=True):
        if new_session:
            self._reset_session()
        self.cd_val    = COUNTDOWN_SECS
        self.cd_tick   = time.time()
        self.last_beep = COUNTDOWN_SECS + 1
        self._set_state(St.COUNTDOWN)

    # ─────────────────────────────────────────────────
    #  촬영 & 합성
    # ─────────────────────────────────────────────────
    def _capture_photo(self):
        frame = self.camera.get_frame()
        if frame is None:
            frame = np.zeros((CAM_H, CAM_W, 3), dtype=np.uint8)
        self.captured_bgr.append(frame.copy())
        log.info(f"촬영 {len(self.captured_bgr)}/{PHOTO_COUNT}")

    def _start_compose(self):
        """백그라운드 스레드로 스트립 합성"""
        self._composing = True

        def _work():
            try:
                # 썸네일 (빠름)
                surfs = []
                for bgr in self.captured_bgr:
                    surfs.append(bgr_to_surface(bgr, (370, 255)))
                self.thumb_surfaces = surfs

                # 풀 스트립 저장
                self.strip_path = composer.compose_print_image(
                    self.captured_bgr, self.session_id)

                # 미리보기 Surface
                preview = composer.make_preview_image(self.captured_bgr, 820)
                self.strip_surface = pil_to_surface(preview)
            except Exception as e:
                log.error(f"합성 오류: {e}")
            finally:
                self._composing = False

        threading.Thread(target=_work, daemon=True, name="composer").start()

    # ─────────────────────────────────────────────────
    #  인쇄
    # ─────────────────────────────────────────────────
    def _start_printing(self):
        self._print_done = False
        self._set_state(St.PRINTING)

        def _work():
            if self.strip_path and self.strip_path.exists():
                ok = printer.print_photo(self.strip_path)
            else:
                log.error("스트립 파일 없음 - 합성 완료 전에 인쇄 요청됨")
                ok = False
            self._print_ok   = ok
            self._print_done = True
            if self.snd_done and ok:
                self.snd_done.play()

        threading.Thread(target=_work, daemon=True, name="printer").start()

    # ─────────────────────────────────────────────────
    #  QR
    # ─────────────────────────────────────────────────
    def _start_qr(self):
        if not self.strip_path or not self.strip_path.exists():
            log.error("스트립 파일 없음 - 합성 완료 전에 QR 요청됨")
            return
        if not self.qr_server.is_running:
            self.qr_server.start()
        if not self.qr_server.is_running:
            log.error("QR 서버가 실행 중이 아니어서 QR 화면을 열 수 없습니다")
            return
        surf, url = self.qr_server.make_qr_surface(self.session_id)
        self.qr_surface = surf
        self.qr_url     = url
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
                    self._begin_countdown()
                elif self.state == St.QR_SHOW:
                    self._set_state(St.IDLE)
                    self._reset_session()

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
        elif self.state == St.REVIEW:
            for btn in (self.btn_print, self.btn_qr, self.btn_retake):
                btn.update(mpos, mpressed)

        return True

    def _handle_click(self, pos):
        if self.state == St.IDLE:
            self._begin_countdown()

        elif self.state == St.REVIEW:
            if self.btn_print.hit(pos):
                if self._composing:
                    log.info("합성 중 - 인쇄 대기")
                    return
                self._start_printing()
            elif self.btn_qr.hit(pos):
                if self._composing:
                    log.info("합성 중 - QR 대기")
                    return
                self._start_qr()
            elif self.btn_retake.hit(pos):
                self._begin_countdown(new_session=True)

        elif self.state == St.QR_SHOW:
            self._set_state(St.IDLE)
            self._reset_session()

        elif self.state == St.PRINTING and self._print_done:
            self._set_state(St.IDLE)
            self._reset_session()

    # ═══════════════════════════════════════════════
    #  IDLE 렌더링
    # ═══════════════════════════════════════════════
    def _draw_idle(self, dt):
        self.screen.fill(C_BG)

        for p in self.particles:
            p.update(dt)
            p.draw(self.screen)

        # 메인 타이틀
        draw_text(self.screen, "오늘의 네컷", self.f_big, C_PINK,
                  SCREEN_W // 2, 190, anchor="center",
                  shadow=True, shadow_color=C_LPINK, shadow_off=4)

        draw_text(self.screen, "뚝딱 공방 포토부스", self.f_medium, C_GRAY,
                  SCREEN_W // 2, 300, anchor="center")

        self.btn_start.draw(self.screen, self.f_large)

        if int(time.time() * 1.6) % 2:
            draw_text(self.screen, "터치 또는 클릭으로 시작",
                      self.f_small, C_GRAY,
                      SCREEN_W // 2, 515, anchor="center")

        # 카메라 소형 미리보기
        frame = self.camera.get_frame()
        if frame is not None:
            pw, ph = 340, 192
            prev = bgr_to_surface(frame, (pw, ph))
            if PREVIEW_MIRROR:
                prev = pygame.transform.flip(prev, True, False)
            px = SCREEN_W // 2 - pw // 2
            py = 560
            draw_rrect(self.screen, C_LPINK,
                       (px - 6, py - 6, pw + 12, ph + 12), radius=14)
            self.screen.blit(prev, (px, py))
            draw_text(self.screen, "지금 모습이에요", self.f_small, C_GRAY,
                      SCREEN_W // 2, py + ph + 14, anchor="center")

        # 하단 정보
        draw_text(self.screen, "어린이 포토부스  |  뚝딱 공방", self.f_tiny, C_LGRAY,
                  SCREEN_W // 2, SCREEN_H - 30, anchor="center")

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
            self._capture_photo()
            if self.snd_shutter:
                self.snd_shutter.play()
            self.flash_alpha = 255
            self._set_state(St.FLASH)

    def _draw_countdown(self):
        # 풀스크린 카메라 프리뷰 (좌우 반전 = 거울 모드)
        frame = self.camera.get_frame()
        if frame is not None:
            prev = bgr_to_surface(frame, (SCREEN_W, SCREEN_H))
            if PREVIEW_MIRROR:
                prev = pygame.transform.flip(prev, True, False)
            self.screen.blit(prev, (0, 0))
        else:
            self.screen.fill((20, 20, 30))

        # 상단 반투명 바
        bar = pygame.Surface((SCREEN_W, 90), pygame.SRCALPHA)
        bar.fill((0, 0, 0, 150))
        self.screen.blit(bar, (0, 0))

        shot_label = f"{len(self.captured_bgr) + 1} / {PHOTO_COUNT}"
        draw_text(self.screen, shot_label, self.f_large, C_WHITE,
                  SCREEN_W // 2, 44, anchor="center")

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
                  SCREEN_W // 2, SCREEN_H // 2 + h // 2 + 20, anchor="center")

        # 하단 찍힌 사진 썸네일
        self._draw_shot_thumbnails()

    def _draw_shot_thumbnails(self):
        if not self.captured_bgr:
            return
        tw, th = 150, 105
        gap    = 12
        total  = len(self.captured_bgr) * (tw + gap) - gap
        sx     = (SCREEN_W - total) // 2
        sy     = SCREEN_H - th - 18
        for i, bgr in enumerate(self.captured_bgr):
            surf = bgr_to_surface(bgr, (tw, th))
            rx   = sx + i * (tw + gap)
            draw_rrect(self.screen, C_WHITE, (rx - 4, sy - 4, tw + 8, th + 8), 8)
            self.screen.blit(surf, (rx, sy))

    # ═══════════════════════════════════════════════
    #  FLASH 렌더링
    # ═══════════════════════════════════════════════
    def _draw_flash(self):
        frame = self.camera.get_frame()
        if frame is not None:
            prev = bgr_to_surface(frame, (SCREEN_W, SCREEN_H))
            if PREVIEW_MIRROR:
                prev = pygame.transform.flip(prev, True, False)
            self.screen.blit(prev, (0, 0))
        else:
            self.screen.fill((20, 20, 30))

        # 흰 플래시 페이드아웃
        if self.flash_alpha > 0:
            ov = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            ov.fill((255, 255, 255, int(self.flash_alpha)))
            self.screen.blit(ov, (0, 0))
            self.flash_alpha = max(0, self.flash_alpha - 28)

        n = len(self.captured_bgr)
        draw_text(self.screen, f"찰칵!  {n} / {PHOTO_COUNT}", self.f_big, C_YELLOW,
                  SCREEN_W // 2, SCREEN_H // 2, anchor="center",
                  shadow=True, shadow_color=(100, 80, 0), shadow_off=4)

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

        draw_text(self.screen, "사진이 완성됐어요!", self.f_large, C_PINK,
                  SCREEN_W // 2, 44, anchor="center")

        # ── 좌측: 4개 썸네일 그리드 ──────────────────
        tw, th = 370, 255
        gap    = 18
        grid_x = 80
        grid_y = 110

        if self.thumb_surfaces:
            for i, surf in enumerate(self.thumb_surfaces[:4]):
                col = i % 2
                row = i // 2
                x   = grid_x + col * (tw + gap)
                y   = grid_y + row * (th + gap)
                draw_rrect(self.screen, C_LPINK,
                           (x - 6, y - 6, tw + 12, th + 12), radius=12)
                self.screen.blit(surf, (x, y))
                # 번호 뱃지
                draw_rrect(self.screen, C_PINK,
                           (x + 4, y + 4, 36, 36), radius=18)
                draw_text(self.screen, str(i + 1), self.f_small, C_WHITE,
                          x + 22, y + 22, anchor="center")
        else:
            # 썸네일 아직 없을 때 (빠르게 생성되므로 드뭄)
            for i in range(4):
                col = i % 2
                row = i // 2
                x   = grid_x + col * (tw + gap)
                y   = grid_y + row * (th + gap)
                draw_rrect(self.screen, C_LGRAY, (x, y, tw, th), radius=12)

        # ── 우측: 스트립 미리보기 ─────────────────────
        strip_area_x = grid_x + 2 * (tw + gap) + 50
        strip_area_w = SCREEN_W - strip_area_x - 30

        if self.strip_surface:
            sw, sh = self.strip_surface.get_size()
            sx = strip_area_x + (strip_area_w - sw) // 2
            sy = 90
            draw_rrect(self.screen, C_LPINK,
                       (sx - 10, sy - 10, sw + 20, sh + 20), radius=14)
            self.screen.blit(self.strip_surface, (sx, sy))
        else:
            # 합성 중 애니메이션
            cx = strip_area_x + strip_area_w // 2
            cy = SCREEN_H // 2 - 60
            dots = "." * (int(time.time() * 2.5) % 4)
            draw_text(self.screen, f"합성 중{dots}", self.f_medium, C_GRAY,
                      cx, cy, anchor="center")

        # ── 하단 버튼 ──────────────────────────────────
        for btn in (self.btn_print, self.btn_qr, self.btn_retake):
            btn.draw(self.screen, self.f_medium)

        # 합성 중 출력/공유 버튼 딤처리
        if self._composing:
            for btn in (self.btn_print, self.btn_qr):
                dim = pygame.Surface(
                    (btn.rect.width, btn.rect.height),
                    pygame.SRCALPHA)
                dim.fill((180, 180, 180, 120))
                self.screen.blit(dim, btn.rect.topleft)

    # ═══════════════════════════════════════════════
    #  PRINTING 렌더링
    # ═══════════════════════════════════════════════
    def _draw_printing(self):
        self.screen.fill(C_BG)
        elapsed = time.time() - self.state_time

        if not self._print_done:
            dots = "." * (int(elapsed * 2) % 4)
            draw_text(self.screen, f"인쇄 중{dots}", self.f_big, C_PINK,
                      SCREEN_W // 2, SCREEN_H // 2 - 60, anchor="center")
            draw_text(self.screen, "잠시만 기다려 주세요", self.f_medium, C_GRAY,
                      SCREEN_W // 2, SCREEN_H // 2 + 40, anchor="center")

            # 프린터 아이콘 바운스
            bounce_y = int(math.sin(elapsed * 3) * 8)
            icon = self.f_huge.render("PRINT", True, C_LPINK)
            self.screen.blit(icon, icon.get_rect(
                center=(SCREEN_W // 2, SCREEN_H // 2 - 200 + bounce_y)))
        else:
            if self._print_ok:
                draw_text(self.screen, "인쇄 완료!", self.f_big, C_GREEN,
                          SCREEN_W // 2, SCREEN_H // 2 - 50, anchor="center")
                draw_text(self.screen, "프린터에서 사진을 가져가세요",
                          self.f_large, C_DARK,
                          SCREEN_W // 2, SCREEN_H // 2 + 50, anchor="center")
            else:
                draw_text(self.screen, "인쇄 오류", self.f_big, C_GRAY,
                          SCREEN_W // 2, SCREEN_H // 2 - 50, anchor="center")
                draw_text(self.screen, "프린터 연결을 확인해 주세요",
                          self.f_large, C_DARK,
                          SCREEN_W // 2, SCREEN_H // 2 + 50, anchor="center")

            # 3초 후 자동으로 대기화면
            remain = max(0, 3 - int(elapsed - (elapsed - 3 if elapsed > 3 else 0)))
            if self._print_done:
                wait_elapsed = time.time() - self.state_time
                # _print_done이 설정된 이후 시간을 추적하기 위해 별도 타이머 필요
                pass
            # 단순 처리: 상태 진입 후 일정 시간 후 복귀
            if elapsed > 5:
                self._set_state(St.IDLE)
                self._reset_session()

    # ═══════════════════════════════════════════════
    #  QR_SHOW 렌더링
    # ═══════════════════════════════════════════════
    def _draw_qr(self):
        self.screen.fill(C_BG)
        elapsed = time.time() - self.state_time
        remain  = max(0, QR_SHOW_TIMEOUT - int(elapsed))

        draw_text(self.screen, "QR 코드로 사진 받기", self.f_large, C_PINK,
                  SCREEN_W // 2, 50, anchor="center")

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
                      SCREEN_W // 2, dy, anchor="center")
            dy += 44
            draw_text(self.screen, self.qr_url, self.f_small, C_GRAY,
                      SCREEN_W // 2, dy, anchor="center")
            dy += 36
            draw_text(self.screen,
                      f"화면을 터치하면 바로 종료 · {remain}초 후 자동 복귀",
                      self.f_small, C_GRAY,
                      SCREEN_W // 2, dy, anchor="center")

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
    parser = argparse.ArgumentParser(description="뚝딱 포토부스")
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
    parser.add_argument("--printer",
                        help="CUPS 프린터 이름 예: Canon_CP1500")
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
