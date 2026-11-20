# -*- coding: utf-8 -*-
"""포토부스 출력 이미지 합성 모듈
SELPHY CP1500 RP-108 엽서(100x148mm / 4x6 / 4R @ 300dpi) 기준:
1181 x 1748 px 한 장에 네컷 또는 34×49 mm 증명사진을 배치합니다.
"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from datetime import datetime
import cv2
import numpy as np
import logging

from config import PHOTOS_DIR, FONT_CANDIDATES, PRINT_BRAND_TEXT, PRINT_MARK_TEXT

log = logging.getLogger(__name__)

# RP-108 Postcard Size: 100 x 148 mm @ 300dpi
PRINT_DPI = 300
PRINT_W = 1181
PRINT_H = 1748
ID_PHOTO_W = round(34 * PRINT_DPI / 25.4)
ID_PHOTO_H = round(49 * PRINT_DPI / 25.4)
ID_PHOTO_GAP = round(10 * PRINT_DPI / 25.4)

FRAME_THEMES = {
    "soft_pink": {
        "name": "핑크",
        "bg": (255, 248, 250),
        "accent": (255, 106, 145),
        "border": (255, 206, 222),
        "text": (158, 93, 116),
    },
    "classic_white": {
        "name": "화이트",
        "bg": (255, 255, 252),
        "accent": (78, 92, 112),
        "border": (224, 228, 232),
        "text": (72, 78, 92),
    },
    "studio_black": {
        "name": "블랙",
        "bg": (28, 28, 32),
        "accent": (250, 250, 246),
        "border": (68, 68, 76),
        "text": (245, 245, 240),
    },
    "sky_blue": {
        "name": "스카이",
        "bg": (241, 248, 255),
        "accent": (74, 154, 230),
        "border": (198, 225, 250),
        "text": (52, 92, 126),
    },
}

AI_DISCLOSURE_TEXT = "AI 생성 이미지"

PRINT_LAYOUTS = {
    "auto": "자동",
    "grid": "2x2",
    "stacked": "4단",
}


def get_frame_theme(theme_id: str | None) -> dict:
    return FRAME_THEMES.get(theme_id or "", FRAME_THEMES["studio_black"])


def resolve_layout(layout_id: str | None, is_landscape: bool) -> str:
    if layout_id in {"grid", "stacked"}:
        return layout_id
    return "stacked" if is_landscape else "grid"


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


def _photos_are_landscape(photos: list) -> bool:
    h, w = photos[0].shape[:2]
    return w >= h


def _slot_layout(is_landscape: bool, layout_id: str | None = None):
    layout = resolve_layout(layout_id, is_landscape)
    margin  = 58
    footer  = 92

    if layout == "stacked":
        gutter = 20
        slot_h = (PRINT_H - 2 * margin - footer - 3 * gutter) // 4
        if is_landscape:
            slot_w = min(PRINT_W - 2 * margin, int(slot_h * 16 / 9))
        else:
            slot_w = min(PRINT_W - 2 * margin, int(slot_h * 4 / 3))
        x = (PRINT_W - slot_w) // 2
        coords = [
            (x, margin + i * (slot_h + gutter))
            for i in range(4)
        ]
    else:
        gutter = 30
        slot_w = (PRINT_W - 2 * margin - gutter) // 2
        slot_h = (PRINT_H - 2 * margin - gutter - footer) // 2
        coords = [
            (margin,             margin),
            (margin + slot_w + gutter, margin),
            (margin,             margin + slot_h + gutter),
            (margin + slot_w + gutter, margin + slot_h + gutter),
        ]
    return layout, slot_w, slot_h, coords


def photo_slot_aspect(is_landscape: bool, layout_id: str | None = None) -> float:
    """최종 출력 시 한 컷이 들어가는 슬롯의 가로/세로 비율."""
    _layout, slot_w, slot_h, _coords = _slot_layout(is_landscape, layout_id)
    return slot_w / slot_h


# ─── RP-108 단일 시트 생성 ─────────────────────────────
def _make_sheet(
    photos: list,
    frame_theme_id: str | None = None,
    layout_id: str | None = None,
    label_text: str | None = None,
    ai_disclosure: bool = False,
) -> Image.Image:
    """4장 BGR ndarray → RP-108 한 장짜리 네컷 PIL Image

    label_text: AI 콘셉트 라벨 (있으면 하단 문구를 '브랜드 · 라벨 · 날짜'로 바꿈)
    ai_disclosure: True면 하단 구석에 'AI 생성 이미지' 작게 표기
    """
    if len(photos) != 4:
        raise ValueError(f"compose requires exactly 4 photos, got {len(photos)}")
    theme = get_frame_theme(frame_theme_id)
    is_landscape = _photos_are_landscape(photos)
    layout, slot_w, slot_h, coords = _slot_layout(is_landscape, layout_id)
    footer = 92

    canvas = Image.new("RGB", (PRINT_W, PRINT_H), theme["bg"])
    draw = ImageDraw.Draw(canvas)

    for photo, (x, y) in zip(photos, coords):
        img = _bgr_to_pil(photo)
        img = _center_crop(img, slot_w, slot_h)
        img = _add_round_corners(img, 10)
        draw.rounded_rectangle(
            [x - 8, y - 8, x + slot_w + 8, y + slot_h + 8],
            radius=18,
            fill=theme["border"],
        )
        canvas.paste(img, (x, y))

    # 하단 브랜드 텍스트
    font  = _pil_font(28)
    today = datetime.now().strftime("%Y.%m.%d")
    if label_text:
        label = f"{PRINT_BRAND_TEXT}  ·  {label_text}  ·  {today}"
    else:
        label = f"{PRINT_BRAND_TEXT}  ·  {theme['name']}  ·  {PRINT_LAYOUTS[layout]}  ·  {today}"
    bbox  = draw.textbbox((0, 0), label, font=font)
    tx = (PRINT_W - (bbox[2] - bbox[0])) // 2
    ty = PRINT_H - footer + 26
    draw.text((tx, ty), label, fill=theme["text"], font=font)

    mark_font = _pil_font(18)
    mark = PRINT_MARK_TEXT
    mark_bbox = draw.textbbox((0, 0), mark, font=mark_font)
    draw.text(
        (PRINT_W - mark_bbox[2] - 44, 28),
        mark,
        fill=theme["accent"],
        font=mark_font,
    )

    if ai_disclosure:
        note_font = _pil_font(18)
        note = AI_DISCLOSURE_TEXT
        note_bbox = draw.textbbox((0, 0), note, font=note_font)
        draw.text(
            (PRINT_W - (note_bbox[2] - note_bbox[0]) - 44, PRINT_H - 34),
            note,
            fill=theme["text"],
            font=note_font,
        )

    return canvas


def _make_id_sheet(photos: list[np.ndarray]) -> Image.Image:
    if not 1 <= len(photos) <= 4:
        raise ValueError("ID photo printing requires 1 to 4 photos")

    sheet = Image.new("RGB", (PRINT_W, PRINT_H), "white")
    draw = ImageDraw.Draw(sheet)
    left = (PRINT_W - 2 * ID_PHOTO_W - ID_PHOTO_GAP) // 2
    top = (PRINT_H - 2 * ID_PHOTO_H - ID_PHOTO_GAP) // 2
    for index, photo in enumerate(photos):
        row, col = divmod(index, 2)
        x = left + col * (ID_PHOTO_W + ID_PHOTO_GAP)
        y = top + row * (ID_PHOTO_H + ID_PHOTO_GAP)
        if photo is None:
            continue
        sheet.paste(_center_crop(_bgr_to_pil(photo), ID_PHOTO_W, ID_PHOTO_H), (x, y))

        # 재단선은 사진 영역 바깥에만 배치합니다.
        for edge_x, dx in ((x, -1), (x + ID_PHOTO_W, 1)):
            for edge_y, dy in ((y, -1), (y + ID_PHOTO_H, 1)):
                draw.line((edge_x + dx * 6, edge_y, edge_x + dx * 20, edge_y),
                          fill="#888888", width=2)
                draw.line((edge_x, edge_y + dy * 6, edge_x, edge_y + dy * 20),
                          fill="#888888", width=2)
    return sheet


# ─── 공개 API ─────────────────────────────────────────
def compose_print_image(
    photos: list,
    session_id: str,
    frame_theme_id: str | None = None,
    layout_id: str | None = None,
    label_text: str | None = None,
    ai_disclosure: bool = False,
) -> Path:
    """RP-108 한 장에 네 컷만 배치해 JPEG 저장 후 경로 반환"""
    sheet = _make_sheet(photos, frame_theme_id, layout_id, label_text, ai_disclosure)
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    out = PHOTOS_DIR / f"{session_id}.jpg"
    sheet.save(out, "JPEG", quality=95, dpi=(PRINT_DPI, PRINT_DPI))
    log.info(f"RP-108 네컷 저장: {out} ({PRINT_W}x{PRINT_H})")
    return out


def make_preview_image(
    photos: list,
    target_h: int = 820,
    frame_theme_id: str | None = None,
    layout_id: str | None = None,
    label_text: str | None = None,
    ai_disclosure: bool = False,
) -> Image.Image:
    """화면 표시용 미리보기 PIL Image (RP-108 시트, 세로 target_h 기준)"""
    sheet = _make_sheet(photos, frame_theme_id, layout_id, label_text, ai_disclosure)
    target_w = int(sheet.width * target_h / sheet.height)
    return sheet.resize((target_w, target_h), Image.LANCZOS)


def compose_id_print_image(photos: list[np.ndarray], session_id: str) -> Path:
    """34×49 mm 증명사진을 RP-108 엽서 한 장에 배치하고 JPEG로 저장합니다."""
    sheet = _make_id_sheet(photos)
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    # 증명사진 인쇄물은 일반 네컷의 QR 공유 경로에서 제외합니다.
    out = PHOTOS_DIR / f"{session_id}.id.jpg"
    sheet.save(out, "JPEG", quality=95, subsampling=0, dpi=(PRINT_DPI, PRINT_DPI))
    log.info(f"RP-108 증명사진 저장: {out} ({len(photos)}컷, {ID_PHOTO_W}x{ID_PHOTO_H} px/컷)")
    return out


def make_id_print_preview(photos: list[np.ndarray], target_h: int = 820) -> Image.Image:
    """실제 출력 배치를 유지한 증명사진 엽서 미리보기."""
    sheet = _make_id_sheet(photos)
    target_w = int(sheet.width * target_h / sheet.height)
    return sheet.resize((target_w, target_h), Image.LANCZOS)
