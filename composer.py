# -*- coding: utf-8 -*-
"""4컷 스트립 이미지 합성 모듈
SELPHY CP1500 엽서(100x148mm @ 300dpi) 기준: 1181 x 1748 px
스트립 2장을 나란히 배치해 1장으로 출력 → 절취선으로 분리
"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from datetime import datetime
import cv2
import numpy as np
import logging

from config import PHOTOS_DIR, FONT_CANDIDATES

log = logging.getLogger(__name__)

# 엽서 전체 크기 (300dpi)
PRINT_W = 1181
PRINT_H = 1748

BRAND = "뚝딱 포토부스"
FRAME_BG = (255, 248, 242)


# ─── 내부 유틸 ────────────────────────────────────────
def _pil_font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(str(p), size)
        except Exception:
            pass
    return ImageFont.load_default()


def _bgr_to_pil(bgr: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


def _center_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    sw, sh = img.size
    if sw / sh > w / h:
        new_w = int(sh * w / h)
        img = img.crop(((sw - new_w) // 2, 0, (sw + new_w) // 2, sh))
    else:
        new_h = int(sw * h / w)
        img = img.crop((0, (sh - new_h) // 2, sw, (sh + new_h) // 2))
    return img.resize((w, h), Image.LANCZOS)


def _add_round_corners(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, img.width - 1, img.height - 1],
                           radius=radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.paste(out, mask=out.split()[3])
    return bg.convert("RGB")


# ─── 스트립 1장 생성 ──────────────────────────────────
def _make_one_strip(photos: list) -> Image.Image:
    """4장 BGR ndarray → 스트립 PIL Image (PRINT_W//2 x PRINT_H)"""
    assert len(photos) == 4

    sw = PRINT_W // 2
    sh = PRINT_H
    margin  = 28
    gutter  = 14
    footer  = 52

    slot_w = (sw - 2 * margin - gutter) // 2
    slot_h = (sh - 2 * margin - gutter - footer) // 2

    canvas = Image.new("RGB", (sw, sh), FRAME_BG)

    coords = [
        (margin,             margin),
        (margin + slot_w + gutter, margin),
        (margin,             margin + slot_h + gutter),
        (margin + slot_w + gutter, margin + slot_h + gutter),
    ]

    for photo, (x, y) in zip(photos, coords):
        img = _bgr_to_pil(photo)
        img = _center_crop(img, slot_w, slot_h)
        img = _add_round_corners(img, 10)
        canvas.paste(img, (x, y))

    # 하단 브랜드 텍스트
    draw  = ImageDraw.Draw(canvas)
    font  = _pil_font(20)
    today = datetime.now().strftime("%Y.%m.%d")
    label = f"{BRAND}  ·  {today}"
    bbox  = draw.textbbox((0, 0), label, font=font)
    tx = (sw - (bbox[2] - bbox[0])) // 2
    ty = sh - footer + 14
    draw.text((tx, ty), label, fill=(165, 130, 145), font=font)

    return canvas


# ─── 공개 API ─────────────────────────────────────────
def compose_print_image(photos: list, session_id: str) -> Path:
    """스트립 2장 나란히 → JPEG 저장 후 경로 반환"""
    strip = _make_one_strip(photos)

    full = Image.new("RGB", (PRINT_W, PRINT_H), FRAME_BG)
    full.paste(strip, (0, 0))
    full.paste(strip, (PRINT_W // 2, 0))

    # 중앙 절취선
    draw = ImageDraw.Draw(full)
    cx = PRINT_W // 2
    for y in range(0, PRINT_H, 20):
        draw.line([(cx, y), (cx, min(y + 11, PRINT_H))],
                  fill=(190, 175, 180), width=2)

    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    out = PHOTOS_DIR / f"{session_id}.jpg"
    full.save(out, "JPEG", quality=95, dpi=(300, 300))
    log.info(f"스트립 저장: {out}")
    return out


def make_preview_image(photos: list, target_h: int = 820) -> Image.Image:
    """화면 표시용 미리보기 PIL Image (스트립 1장, 세로 target_h 기준)"""
    strip  = _make_one_strip(photos)
    target_w = int(strip.width * target_h / strip.height)
    return strip.resize((target_w, target_h), Image.LANCZOS)
