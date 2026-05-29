# -*- coding: utf-8 -*-
"""촬영 필터 효과."""
import cv2
import numpy as np


FILTERS = {
    "original": {
        "name": "원본",
        "description": "자연스러운 색감",
    },
    "bright": {
        "name": "화사",
        "description": "밝고 선명하게",
    },
    "warm": {
        "name": "따뜻",
        "description": "따뜻한 톤",
    },
    "cool": {
        "name": "청량",
        "description": "맑은 블루 톤",
    },
    "mono": {
        "name": "흑백",
        "description": "클래식 모노톤",
    },
}


def apply_filter(bgr: np.ndarray, filter_id: str) -> np.ndarray:
    """BGR 프레임에 선택된 필터를 적용합니다."""
    if filter_id == "bright":
        out = cv2.convertScaleAbs(bgr, alpha=1.08, beta=18)
        return cv2.GaussianBlur(out, (3, 3), 0)

    if filter_id == "warm":
        out = bgr.astype(np.int16)
        out[:, :, 2] += 18
        out[:, :, 1] += 6
        out[:, :, 0] -= 8
        return np.clip(out, 0, 255).astype(np.uint8)

    if filter_id == "cool":
        out = bgr.astype(np.int16)
        out[:, :, 0] += 18
        out[:, :, 1] += 4
        out[:, :, 2] -= 8
        return np.clip(out, 0, 255).astype(np.uint8)

    if filter_id == "mono":
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        mono = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        return cv2.convertScaleAbs(mono, alpha=1.05, beta=8)

    return bgr
