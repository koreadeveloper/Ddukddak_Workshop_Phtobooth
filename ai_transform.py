# -*- coding: utf-8 -*-
"""AI 변신 네컷 - 이미지 편집 AI Provider 모듈

pygame에 의존하지 않습니다. main.py와 tools/ai_poc.py가 같은 전/후처리 함수를 씁니다.
API 키와 base64 이미지 본문은 절대 로그에 남기지 않습니다.
"""
from __future__ import annotations

import base64
import logging
import math
import random
import time
import zlib
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np
import requests

import config as cfg
import composer

log = logging.getLogger(__name__)


# ─── 결과/인터페이스 ─────────────────────────────────
@dataclass
class AIResult:
    ok: bool
    image_bgr: np.ndarray | None = None   # 성공 시 BGR
    error: str = ""                       # 실패 사유 (로그/운영용, 사용자에게는 일반 문구)
    elapsed: float = 0.0
    refused: bool = False                 # 안전정책 거절 여부


class AIProvider(Protocol):
    name: str

    def transform(self, image_bgr: np.ndarray, prompt: str, timeout: float) -> AIResult: ...

    def health_check(self, timeout: float = 5.0) -> tuple[bool, str]: ...


# ─── 이미지 전/후처리 (AI_TRANSFORM_PLAN 6.3) ─────────
JPEG_QUALITY = 90


def crop_to_aspect(bgr: np.ndarray, target_aspect: float) -> np.ndarray:
    """BGR 이미지를 지정한 가로/세로 비율로 중앙 크롭합니다 (main.center_crop_bgr_to_aspect와 동일)."""
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


def slot_aspect_for(raw_bgr: np.ndarray) -> float:
    """인쇄 슬롯(2x2) 비율. main._preview_crop_frame과 같은 기준을 씁니다."""
    h, w = raw_bgr.shape[:2]
    return composer.photo_slot_aspect(w >= h, "grid")


def resize_max_side(bgr: np.ndarray, max_side: int) -> np.ndarray:
    """긴 변이 max_side보다 크면 비율 유지 축소 (확대는 하지 않음)."""
    h, w = bgr.shape[:2]
    long_side = max(h, w)
    if max_side <= 0 or long_side <= max_side:
        return bgr
    scale = max_side / long_side
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)


def prepare_upload_image(raw_bgr: np.ndarray, max_side: int | None = None) -> np.ndarray:
    """전송 전 처리: 방향 보정된 원본(필터/미러 X) → 인쇄 영역 중앙 크롭 → 긴 변 축소."""
    if max_side is None:
        max_side = cfg.AI_UPLOAD_MAX_SIDE
    crop = crop_to_aspect(raw_bgr, slot_aspect_for(raw_bgr))
    return np.ascontiguousarray(resize_max_side(crop, max_side))


def encode_jpeg(bgr: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise ValueError("JPEG 인코딩 실패")
    return buf.tobytes()


def decode_image(data: bytes) -> np.ndarray | None:
    """JPEG/PNG 바이트 → BGR. 실패 시 None."""
    if not data:
        return None
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        return None
    return img


def postprocess_result(result_bgr: np.ndarray, target_aspect: float) -> np.ndarray:
    """수신 후 처리: 결과 비율이 달라도 슬롯 비율로 다시 중앙 크롭."""
    return np.ascontiguousarray(crop_to_aspect(result_bgr, target_aspect))


# ─── Gemini (Interactions API REST, requests) ────────
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_API_REVISION = "2026-05-20"
# 2026-11 공식 문서 기준 최신 Stable 이미지 편집 모델. 바뀌면 PHOTOBOOTH_AI_MODEL로 교체.
GEMINI_DEFAULT_MODEL = "gemini-nano-banana-2.1"
GEMINI_IMAGE_SIZE = "1K"
GEMINI_ASPECT_RATIOS = {
    "1:1": 1.0, "2:3": 2 / 3, "3:2": 3 / 2, "3:4": 3 / 4, "4:3": 4 / 3,
    "4:5": 4 / 5, "5:4": 5 / 4, "9:16": 9 / 16, "16:9": 16 / 9,
}
# 응답/오류에 이 단어가 있으면 안전정책 거절로 봅니다 (재시도하지 않음)
GEMINI_REFUSAL_WORDS = ("safety", "prohibited", "blocklist", "blocked", "policy", "spii", "recitation")
RETRY_BACKOFF_SECS = 1.5


def closest_aspect_ratio(target_aspect: float) -> str:
    return min(
        GEMINI_ASPECT_RATIOS,
        key=lambda k: abs(math.log(GEMINI_ASPECT_RATIOS[k] / target_aspect)),
    )


def _short(text, limit: int = 160) -> str:
    return " ".join(str(text or "").split())[:limit]


def _looks_refused(*texts) -> bool:
    joined = " ".join(str(t or "") for t in texts).lower()
    return any(word in joined for word in GEMINI_REFUSAL_WORDS)


def _error_message(err) -> str:
    if isinstance(err, dict):
        return _short(err.get("message") or err.get("status") or err.get("code") or "")
    return _short(err)


def _api_error_message(resp) -> str:
    try:
        msg = _error_message(resp.json().get("error"))
    except Exception:
        msg = ""
    return f"HTTP {resp.status_code}" + (f": {msg}" if msg else "")


class GeminiProvider:
    """Gemini Interactions API(POST /v1beta/interactions)로 이미지 편집 (SDK 없이 requests 사용)."""

    name = "gemini"

    def __init__(self, api_key: str, model: str = "", api_base: str = GEMINI_API_BASE):
        self._api_key = api_key or ""
        self.model = model or GEMINI_DEFAULT_MODEL
        self._api_base = api_base.rstrip("/")
        self._session = requests.Session()

    def __repr__(self):
        return f"GeminiProvider(model={self.model!r})"   # 키는 표시하지 않음

    @property
    def has_key(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict:
        return {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
            "Api-Revision": GEMINI_API_REVISION,
        }

    def _payload(self, image_bgr: np.ndarray, prompt: str) -> dict:
        h, w = image_bgr.shape[:2]
        return {
            "model": self.model,
            "input": [
                {"type": "text", "text": prompt},
                {
                    "type": "image",
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(encode_jpeg(image_bgr)).decode("ascii"),
                },
            ],
            "response_format": {
                "type": "image",
                "mime_type": "image/jpeg",
                "aspect_ratio": closest_aspect_ratio(w / h),
                "image_size": GEMINI_IMAGE_SIZE,
            },
            # 기본값은 요청/응답을 서버에 저장하므로 끕니다 (얼굴 사진 개인정보 보호)
            "store": False,
        }

    def _parse_response(self, data: dict) -> AIResult:
        status = str(data.get("status") or "")
        image = None
        texts = []
        for step in data.get("steps") or []:
            if step.get("type") != "model_output":
                continue   # thought 단계의 중간 이미지는 건너뜀
            for block in step.get("content") or []:
                if block.get("type") == "image" and block.get("data"):
                    try:
                        decoded = decode_image(base64.b64decode(block["data"]))
                    except Exception:
                        decoded = None
                    if decoded is not None:
                        image = decoded   # 마지막 이미지가 최종 결과
                elif block.get("type") == "text" and block.get("text"):
                    texts.append(block["text"])
        if image is not None:
            return AIResult(True, image_bgr=image)
        err = _error_message(data.get("error"))
        note = _short(" ".join(texts), 120)
        detail = f"이미지 없음 (status={status or '-'})"
        if err:
            detail += f" {err}"
        if note:
            detail += f" {note}"
        refused = _looks_refused(err, note, data.get("finish_reason"), data.get("block_reason"))
        if refused:
            detail = "안전정책 거절: " + detail
        return AIResult(False, error=detail, refused=refused)

    def transform(self, image_bgr: np.ndarray, prompt: str, timeout: float) -> AIResult:
        started = time.monotonic()

        def _done(res: AIResult) -> AIResult:
            res.elapsed = time.monotonic() - started
            return res

        if not self._api_key:
            return _done(AIResult(False, error="API 키 없음 (PHOTOBOOTH_AI_API_KEY)"))
        try:
            payload = self._payload(image_bgr, prompt)
        except Exception as e:
            return _done(AIResult(False, error=f"전처리 실패: {type(e).__name__}"))

        url = f"{self._api_base}/interactions"
        error = "알 수 없는 오류"
        for attempt in range(2):   # 네트워크/429/5xx는 1회 재시도
            remaining = timeout - (time.monotonic() - started)
            if remaining < 1:
                error = "요청 시간 초과"
                break
            retryable = False
            try:
                resp = self._session.post(
                    url, headers=self._headers(), json=payload,
                    timeout=(min(10.0, remaining), remaining),
                )
            except requests.Timeout:
                error, retryable = "요청 시간 초과", True
            except requests.RequestException as e:
                error, retryable = f"네트워크 오류: {type(e).__name__}", True
            else:
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except ValueError:
                        return _done(AIResult(False, error="응답 JSON 파싱 실패"))
                    return _done(self._parse_response(data))
                error = _api_error_message(resp)
                if resp.status_code == 400 and _looks_refused(error):
                    return _done(AIResult(False, error=f"안전정책 거절: {error}", refused=True))
                retryable = resp.status_code == 429 or resp.status_code >= 500
            if not retryable or attempt == 1:
                break
            wait = RETRY_BACKOFF_SECS * (attempt + 1)
            if timeout - (time.monotonic() - started) <= wait + 1:
                break
            log.warning(f"AI 요청 재시도 ({error})")
            time.sleep(wait)
        return _done(AIResult(False, error=error))

    def health_check(self, timeout: float = 5.0) -> tuple[bool, str]:
        """모델 정보 조회만 합니다 (이미지 생성 호출 없음, 비용 없음)."""
        if not self._api_key:
            return False, "API 키 없음"
        try:
            resp = self._session.get(
                f"{self._api_base}/models/{self.model}",
                headers={"x-goog-api-key": self._api_key}, timeout=timeout,
            )
        except requests.RequestException as e:
            return False, f"연결 실패: {type(e).__name__}"
        if resp.status_code == 200:
            return True, f"정상 ({self.model})"
        if resp.status_code in (401, 403):
            return False, "API 키 오류/권한 없음"
        if resp.status_code == 404:
            return False, f"모델 없음: {self.model}"
        return False, _api_error_message(resp)


# ─── Mock (API 없이 흐름 테스트) ─────────────────────
class MockProvider:
    """지연 후 색조 변환 + 'AI MOCK' 표시. FAIL_RATE로 실패를 흉내냅니다."""

    name = "mock"
    model = "mock"

    def __init__(self, delay: float | None = None, fail_rate: float | None = None):
        self.delay = max(0.0, cfg.AI_MOCK_DELAY if delay is None else delay)
        rate = cfg.AI_MOCK_FAIL_RATE if fail_rate is None else fail_rate
        self.fail_rate = min(1.0, max(0.0, rate))

    def transform(self, image_bgr: np.ndarray, prompt: str, timeout: float) -> AIResult:
        started = time.monotonic()
        wait = min(self.delay, max(0.0, timeout))
        time.sleep(wait)
        elapsed = time.monotonic() - started
        if self.delay > timeout:
            return AIResult(False, error="요청 시간 초과 (mock)", elapsed=elapsed)
        if random.random() < self.fail_rate:
            return AIResult(False, error="모의 실패 (mock)", elapsed=elapsed)
        try:
            out = self._tint(image_bgr, prompt)
        except Exception as e:
            return AIResult(False, error=f"모의 변환 실패: {type(e).__name__}", elapsed=elapsed)
        return AIResult(True, image_bgr=out, elapsed=time.monotonic() - started)

    @staticmethod
    def _tint(image_bgr: np.ndarray, prompt: str) -> np.ndarray:
        # 프롬프트(콘셉트)마다 다른 색조가 나오도록 해시로 색 결정
        hue = zlib.crc32(prompt.encode("utf-8")) % 180
        color = cv2.cvtColor(np.uint8([[[hue, 200, 230]]]), cv2.COLOR_HSV2BGR)[0, 0]
        overlay = np.empty_like(image_bgr)
        overlay[:] = color
        out = cv2.addWeighted(image_bgr, 0.7, overlay, 0.3, 0)
        h, w = out.shape[:2]
        scale = max(0.6, w / 500)
        cv2.putText(out, "AI MOCK", (int(w * 0.06), int(h * 0.12)),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255),
                    max(2, int(scale * 3)), cv2.LINE_AA)
        return out

    def health_check(self, timeout: float = 5.0) -> tuple[bool, str]:
        return True, "정상 (mock)"


def get_provider(name: str | None = None) -> AIProvider:
    """cfg.AI_PROVIDER로 Provider 선택: gemini | mock. 알 수 없는 값이면 ValueError."""
    name = (name or cfg.AI_PROVIDER or "").strip().lower()
    if name == "mock":
        return MockProvider()
    if name == "gemini":
        return GeminiProvider(cfg.AI_API_KEY, cfg.AI_MODEL)
    raise ValueError(f"알 수 없는 AI provider: {name!r} (gemini | mock)")
