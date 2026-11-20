# -*- coding: utf-8 -*-
"""AI 변신 네컷 - 이미지 편집 AI Provider 모듈

pygame에 의존하지 않습니다. main.py와 tools/ai_poc.py가 같은 전/후처리 함수를 씁니다.
API 키와 base64 이미지 본문은 절대 로그에 남기지 않습니다.
"""
from __future__ import annotations

import base64
import logging
import math
import queue
import random
import threading
import time
import zlib
from concurrent.futures import Future, ThreadPoolExecutor
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
    retryable: bool = False               # 네트워크/429/5xx/시간초과처럼 다시 시도할 만한 실패


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

        # 재시도는 transform_with_retry가 맡습니다 (여기서는 1회만 요청)
        url = f"{self._api_base}/interactions"
        try:
            resp = self._session.post(
                url, headers=self._headers(), json=payload,
                timeout=(min(10.0, timeout), timeout),
            )
        except requests.Timeout:
            return _done(AIResult(False, error="요청 시간 초과", retryable=True))
        except requests.RequestException as e:
            return _done(AIResult(False, error=f"네트워크 오류: {type(e).__name__}", retryable=True))
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
        return _done(AIResult(False, error=error, retryable=retryable))

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
            return AIResult(False, error="요청 시간 초과 (mock)", elapsed=elapsed, retryable=True)
        if random.random() < self.fail_rate:
            return AIResult(False, error="모의 실패 (mock)", elapsed=elapsed, retryable=True)
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


# ─── 재시도 (모든 Provider 공통) ─────────────────────
def transform_with_retry(provider: AIProvider, image_bgr: np.ndarray, prompt: str,
                         timeout: float, backoff: float = RETRY_BACKOFF_SECS) -> AIResult:
    """retryable 실패면 1회만 재시도. 안전정책 거절은 재시도하지 않음. 예외를 던지지 않음.

    timeout은 재시도를 포함한 이 컷의 전체 시간 예산입니다.
    """
    started = time.monotonic()
    res = AIResult(False, error="실행 오류")
    for attempt in range(2):
        remaining = timeout - (time.monotonic() - started)
        if attempt and remaining < 1:
            break
        try:
            res = provider.transform(image_bgr, prompt, max(0.0, remaining))
        except Exception as e:   # Provider는 예외를 던지지 않아야 하지만 방어
            res = AIResult(False, error=f"예외: {type(e).__name__}")
        if res is None:
            res = AIResult(False, error="Provider 결과 없음")
        if res.ok or res.refused or not res.retryable or attempt == 1:
            break
        wait = backoff * random.uniform(0.7, 1.3)
        if timeout - (time.monotonic() - started) <= wait + 1:
            break
        log.warning(f"AI 요청 재시도 ({res.error})")
        time.sleep(wait)
    res.elapsed = time.monotonic() - started
    return res


def _concept_prompt(concept) -> str:
    if isinstance(concept, str):
        return concept
    if isinstance(concept, dict):
        return str(concept.get("prompt", ""))
    return str(getattr(concept, "prompt", ""))


# ─── 세션 단위 비동기 변환 관리자 ────────────────────
class AITransformManager:
    """세션 단위 비동기 변환 관리자. main.py는 이것만 사용.

    결과는 (generation, index, AIResult)로 큐에 쌓이고 메인 루프가 poll()로 꺼냅니다.
    같은 (generation, index)를 다시 submit하면 이전 요청 결과는 버립니다 (재촬영/재변환).
    cancel_all() 이후 도착한 결과도 버립니다. 진행 중 HTTP 요청은 끝까지 돌 수 있습니다.
    """

    def __init__(self, provider: AIProvider, max_workers: int | None = None,
                 request_timeout: float | None = None):
        self.provider = provider
        workers = max(1, int(max_workers or cfg.AI_MAX_CONCURRENCY))
        self.request_timeout = float(request_timeout or cfg.AI_REQUEST_TIMEOUT)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ai")
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._token = 0
        self._latest: dict[tuple[int, int], int] = {}     # (gen, idx) → 최신 요청 토큰
        self._running: dict[tuple[int, int], Future] = {}  # 아직 끝나지 않은 최신 요청
        self._closed = False

    def submit(self, generation: int, index: int, image_bgr: np.ndarray, concept) -> None:
        key = (int(generation), int(index))
        prompt = _concept_prompt(concept)
        image = np.ascontiguousarray(image_bgr).copy()   # 메인 스레드가 원본을 바꿔도 안전
        with self._lock:
            if self._closed:
                self._queue.put((key[0], key[1], -1, AIResult(False, error="관리자 종료됨")))
                return
            self._token += 1
            token = self._token
            old = self._running.pop(key, None)
            if old is not None:
                old.cancel()
            self._latest[key] = token
            future = self._executor.submit(self._run, key, token, image, prompt)
            self._running[key] = future
        log.info(f"AI 변환 요청: 세대 {key[0]} 컷 {key[1] + 1}")

    def _run(self, key: tuple[int, int], token: int, image: np.ndarray, prompt: str):
        try:
            res = transform_with_retry(self.provider, image, prompt, self.request_timeout)
        except Exception as e:
            res = AIResult(False, error=f"예외: {type(e).__name__}")
        with self._lock:
            if self._latest.get(key) != token:
                return   # 취소되었거나 더 새 요청이 있음
            self._running.pop(key, None)
            self._queue.put((key[0], key[1], token, res))
        if res.ok:
            log.info(f"AI 변환 완료: 세대 {key[0]} 컷 {key[1] + 1} ({res.elapsed:.1f}s)")
        else:
            kind = "거절" if res.refused else "실패"
            log.warning(f"AI 변환 {kind}: 세대 {key[0]} 컷 {key[1] + 1} ({res.elapsed:.1f}s) {res.error}")

    def poll(self) -> list[tuple[int, int, AIResult]]:
        """완료된 결과를 꺼냅니다. 메인 스레드에서 매 프레임 호출해도 가볍습니다."""
        out = []
        while True:
            try:
                gen, idx, token, res = self._queue.get_nowait()
            except queue.Empty:
                break
            with self._lock:
                if token != -1:
                    if self._latest.get((gen, idx)) != token:
                        continue
                    del self._latest[(gen, idx)]
            out.append((gen, idx, res))
        return out

    def pending_count(self, generation: int) -> int:
        """해당 세대에서 아직 결과가 나오지 않은 요청 수."""
        with self._lock:
            return sum(1 for (gen, _idx) in self._running if gen == generation)

    def cancel_all(self) -> None:
        """세션 리셋 시 호출. 시작 전 요청은 취소, 진행 중 요청의 결과는 버립니다."""
        with self._lock:
            for future in self._running.values():
                future.cancel()
            self._running.clear()
            self._latest.clear()
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break

    def shutdown(self) -> None:
        """프로그램 종료 시 호출. 진행 중 요청을 기다리지 않습니다."""
        self.cancel_all()
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
