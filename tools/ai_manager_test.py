#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 백엔드 단위 테스트 (pygame/네트워크/API 키 불필요)

    python3 tools/ai_manager_test.py

MockProvider로 AITransformManager의 submit/poll, 세션 리셋 후 늦은 결과 무시,
FAIL_RATE=1 원본 대체, 재시도/거절 규칙, 타임아웃과 콘셉트 로더/통계 카운터를 확인합니다.
실패한 항목이 있으면 종료 코드 1.
"""
from __future__ import annotations

import base64
import json
import logging
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import Mock

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

import numpy as np  # noqa: E402

import ai_concepts  # noqa: E402
import ai_transform  # noqa: E402
import booth_stats  # noqa: E402
from ai_transform import AIResult, AITransformManager, MockProvider  # noqa: E402

PHOTO_COUNT = 4
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    RESULTS.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def frames() -> list[np.ndarray]:
    out = []
    for i in range(PHOTO_COUNT):
        img = np.zeros((600, 800, 3), np.uint8)
        img[:] = (40 * i, 80, 160)
        out.append(img)
    return out


def wait_results(mgr: AITransformManager, want: int, timeout: float) -> list:
    got, deadline = [], time.monotonic() + timeout
    while len(got) < want and time.monotonic() < deadline:
        got.extend(mgr.poll())
        time.sleep(0.02)
    return got


class ScriptedProvider:
    """정해진 결과를 순서대로 돌려주는 가짜 Provider (호출 횟수 기록)."""

    name = "scripted"

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0
        self._lock = threading.Lock()

    def transform(self, image_bgr, prompt, timeout):
        with self._lock:
            self.calls += 1
            item = self.results.pop(0) if self.results else AIResult(False, error="결과 없음")
        if isinstance(item, Exception):
            raise item
        return item

    def health_check(self, timeout=5.0):
        return True, "ok"


def test_submit_poll(concept):
    mgr = AITransformManager(MockProvider(delay=0.3, fail_rate=0.0), max_workers=4, request_timeout=5)
    gen = 1
    started = time.monotonic()
    for i, img in enumerate(frames()):
        mgr.submit(gen, i, img, concept)
    pending_now = mgr.pending_count(gen)
    got = wait_results(mgr, PHOTO_COUNT, 5)
    elapsed = time.monotonic() - started
    idxs = sorted(idx for _g, idx, _r in got)
    check("submit 4컷 → poll 결과 4개", len(got) == 4 and idxs == [0, 1, 2, 3], f"index={idxs}")
    check("4컷 모두 성공 + BGR 이미지", all(r.ok and r.image_bgr is not None and r.image_bgr.ndim == 3
                                       for _g, _i, r in got))
    check("동시 처리 (4컷이 1컷 시간 안에)", elapsed < 1.0, f"{elapsed:.2f}s")
    check("pending_count 4 → 0", pending_now == 4 and mgr.pending_count(gen) == 0,
          f"{pending_now} → {mgr.pending_count(gen)}")
    check("poll 반복 호출 시 중복 없음", mgr.poll() == [])
    mgr.shutdown()


def test_reset_ignores_late(concept):
    mgr = AITransformManager(MockProvider(delay=0.5, fail_rate=0.0), max_workers=4, request_timeout=5)
    for i, img in enumerate(frames()):
        mgr.submit(1, i, img, concept)
    time.sleep(0.1)                 # 세대 1 요청이 진행 중인 상태에서 세션 리셋
    mgr.cancel_all()
    check("cancel_all 직후 세대 1 pending 0", mgr.pending_count(1) == 0)
    for i, img in enumerate(frames()):
        mgr.submit(2, i, img, concept)
    got = wait_results(mgr, PHOTO_COUNT, 5)
    time.sleep(0.7)                 # 세대 1의 늦은 응답이 도착할 시간
    got.extend(mgr.poll())
    gens = sorted({g for g, _i, _r in got})
    check("세션 리셋 후 늦게 온 세대 1 결과 무시", gens == [2] and len(got) == 4,
          f"받은 세대={gens}, 개수={len(got)}")
    mgr.shutdown()


def test_resubmit_same_index(concept):
    mgr = AITransformManager(MockProvider(delay=0.4, fail_rate=0.0), max_workers=4, request_timeout=5)
    img_a, img_b = frames()[0], frames()[1]
    mgr.submit(5, 0, img_a, concept)
    time.sleep(0.1)
    mgr.submit(5, 0, img_b, concept)   # 재촬영/재변환
    got = wait_results(mgr, 1, 3)
    time.sleep(0.5)
    got.extend(mgr.poll())
    check("같은 컷 재요청 시 이전 결과 버림", len(got) == 1, f"결과 {len(got)}개")
    mgr.shutdown()


def test_fail_rate_one(concept):
    mgr = AITransformManager(MockProvider(delay=0.05, fail_rate=1.0), max_workers=4, request_timeout=10)
    originals = frames()
    for i, img in enumerate(originals):
        mgr.submit(3, i, img, concept)
    got = wait_results(mgr, PHOTO_COUNT, 10)
    check("FAIL_RATE=1 → 4컷 모두 실패 결과로 도착", len(got) == 4 and all(not r.ok for _g, _i, r in got),
          f"{len(got)}개, 오류={got[0][2].error if got else '-'}")
    # main.py가 할 원본 대체를 흉내: 실패 컷은 원본 사용
    ai_results = [None] * PHOTO_COUNT
    for _g, idx, r in got:
        if r.ok:
            ai_results[idx] = r.image_bgr
    final = [ai_results[i] if ai_results[i] is not None else originals[i] for i in range(PHOTO_COUNT)]
    check("실패 컷은 원본으로 대체", all(final[i] is originals[i] for i in range(PHOTO_COUNT)))
    mgr.shutdown()


def test_retry_rules():
    img = frames()[0]
    p = ScriptedProvider([AIResult(False, error="HTTP 503", retryable=True), AIResult(True, image_bgr=img)])
    r = ai_transform.transform_with_retry(p, img, "x", 10, backoff=0.05)
    check("일시 오류(503) → 1회 재시도 후 성공", r.ok and p.calls == 2, f"호출 {p.calls}회")

    p = ScriptedProvider([AIResult(False, error="HTTP 429", retryable=True)] * 3)
    r = ai_transform.transform_with_retry(p, img, "x", 10, backoff=0.05)
    check("계속 실패해도 재시도는 1회까지", not r.ok and p.calls == 2, f"호출 {p.calls}회")

    p = ScriptedProvider([AIResult(False, error="안전정책 거절", refused=True, retryable=True)] * 2)
    r = ai_transform.transform_with_retry(p, img, "x", 10, backoff=0.05)
    check("안전정책 거절은 재시도 안 함", r.refused and p.calls == 1, f"호출 {p.calls}회")

    p = ScriptedProvider([AIResult(False, error="HTTP 400")] * 2)
    r = ai_transform.transform_with_retry(p, img, "x", 10, backoff=0.05)
    check("재시도 불가 오류(400)는 재시도 안 함", not r.ok and p.calls == 1, f"호출 {p.calls}회")

    p = ScriptedProvider([RuntimeError("boom")])
    mgr = AITransformManager(p, max_workers=1, request_timeout=5)
    mgr.submit(9, 0, img, "x")
    got = wait_results(mgr, 1, 3)
    check("Provider 예외도 실패 결과로 변환 (크래시 없음)", len(got) == 1 and not got[0][2].ok,
          got[0][2].error if got else "결과 없음")
    mgr.shutdown()


def test_timeout(concept):
    mgr = AITransformManager(MockProvider(delay=3.0, fail_rate=0.0), max_workers=4, request_timeout=0.5)
    started = time.monotonic()
    mgr.submit(4, 0, frames()[0], concept)
    got = wait_results(mgr, 1, 3)
    elapsed = time.monotonic() - started
    check("요청 타임아웃 → 시간 초과 실패, 오래 붙잡지 않음",
          len(got) == 1 and not got[0][2].ok and elapsed < 1.5,
          f"{elapsed:.2f}s, {got[0][2].error if got else '결과 없음'}")
    mgr.shutdown()


def test_concepts_loader(tmp: Path):
    real = ai_concepts.load_concepts(REPO_DIR / "assets/ai_concepts.json", base_dir=REPO_DIR)
    check("기본 콘셉트 8개와 예시 이미지 모두 로드",
          len(real) == 8 and all(c.thumbnail_path and c.thumbnail_path.is_file() for c in real),
          ", ".join(c.id for c in real))

    thumb = tmp / "ok.jpg"
    thumb.write_bytes(b"x")
    items = [
        {"id": "good", "name": "정상", "prompt": "p", "thumbnail": str(thumb)},
        {"id": "no_thumb", "name": "썸네일없음", "prompt": "p", "thumbnail": "assets/none.jpg"},
        {"id": "no_prompt", "name": "프롬프트없음"},
        {"id": "Bad-ID", "name": "잘못된id", "prompt": "p"},
        {"id": "good", "name": "중복", "prompt": "p"},
        {"id": "off", "name": "비활성", "prompt": "p", "enabled": False},
        {"id": "odd_cat", "name": "카테고리", "prompt": "p", "category": "zzz"},
        "문자열 항목",
    ]
    path = tmp / "concepts.json"
    path.write_text(json.dumps({"concepts": items}, ensure_ascii=False), encoding="utf-8")
    got = ai_concepts.load_concepts(path, base_dir=tmp)
    ids = [c.id for c in got]
    check("잘못된 콘셉트만 제외하고 계속", ids == ["good", "no_thumb", "odd_cat"], f"{ids}")
    check("썸네일 없음 → thumbnail_path None (대체 카드용)", got[1].thumbnail_path is None
          and got[0].thumbnail_path == thumb)
    check("알 수 없는 카테고리 → fun, print_label 기본값", got[2].category == "fun"
          and got[2].print_label == "AI 카테고리")

    broken = tmp / "broken.json"
    broken.write_text("{ 깨진 json", encoding="utf-8")
    check("깨진 JSON/없는 파일 → 빈 목록 (예외 없음)",
          ai_concepts.load_concepts(broken) == [] and ai_concepts.load_concepts(tmp / "x.json") == [])


def test_stats(tmp: Path):
    original = booth_stats.STATS_PATH
    booth_stats.STATS_PATH = tmp / "booth_stats.json"
    try:
        booth_stats.STATS_PATH.write_text(json.dumps({"total": {"sessions": 3}, "daily": {}}), encoding="utf-8")
        for key in ("ai_sessions", "ai_requests", "ai_success", "ai_failure", "ai_refused", "ai_regenerate"):
            booth_stats.increment(key)
        booth_stats.increment("ai_requests", 3)
        booth_stats.increment_concept("firefighter")
        booth_stats.increment_concept("firefighter")
        s = booth_stats.summary()
        check("6.9장 AI 카운터 증가 + 기존 json 호환",
              s["total"]["sessions"] == 3 and s["today"]["ai_requests"] == 4 and s["total"]["ai_regenerate"] == 1,
              f"today={ {k: v for k, v in s['today'].items() if k.startswith('ai_')} }")
        check("콘셉트별 인기 통계 별도 저장", s["concepts"] == {"firefighter": 2}, f"{s['concepts']}")
    finally:
        booth_stats.STATS_PATH = original


def test_monorouter_provider():
    photo = frames()[0]
    encoded = base64.b64encode(ai_transform.encode_jpeg(photo)).decode("ascii")
    provider = ai_transform.MonoRouterProvider("test-key")
    provider._session = Mock()
    response = provider._session.post.return_value
    response.status_code = 200
    response.json.return_value = {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/jpeg", "data": encoded}},
    ]}}]}
    result = provider.transform(photo, "Edit the outfit", 10)
    args, kwargs = provider._session.post.call_args
    check("MonoRouter로 Bearer 인증과 이미지 편집 요청 전송",
          args[0] == "https://monogpt.kr/api/monorouter/v1/gemini/v1beta/models/gemini-nano-banana-2.1:generateContent"
          and kwargs["headers"]["Authorization"] == "Bearer test-key"
          and kwargs["json"]["contents"][0]["parts"][1]["inlineData"]["data"] == encoded
          and kwargs["json"]["generationConfig"]["responseModalities"] == ["IMAGE"])
    check("MonoRouter 이미지 응답 디코딩", result.ok and result.image_bgr.shape == photo.shape)
    response.json.return_value = {"promptFeedback": {"blockReason": "SAFETY"}}
    blocked = provider.transform(photo, "Edit the outfit", 10)
    check("MonoRouter 안전 정책 거절 처리", blocked.refused and not blocked.retryable)
    provider._session.get.return_value.status_code = 200
    healthy, _ = provider.health_check()
    check("MonoRouter 상태 확인은 이미지 호출 없이 크레딧 조회",
          healthy and provider._session.get.call_args.args[0].endswith("/v1/credits"))
    image_provider = ai_transform.MonoRouterImageProvider("test-key")
    image_provider._session = Mock()
    response = image_provider._session.post.return_value
    response.status_code = 200
    response.json.return_value = {"data": [{"b64_json": encoded}]}
    image_result = image_provider.transform(photo, "Edit the outfit", 10)
    args, kwargs = image_provider._session.post.call_args
    check("GPT Image 2.6 multipart 편집", args[0].endswith("/v1/images/edits")
          and kwargs["data"]["model"] == "gpt-image-2.6" and image_result.ok)


def main() -> int:
    logging.basicConfig(level=logging.ERROR)
    concepts = ai_concepts.load_concepts()
    concept = concepts[0] if concepts else {"prompt": "test"}
    test_submit_poll(concept)
    test_reset_ignores_late(concept)
    test_resubmit_same_index(concept)
    test_fail_rate_one(concept)
    test_retry_rules()
    test_timeout(concept)
    test_monorouter_provider()
    with tempfile.TemporaryDirectory() as d:
        test_concepts_loader(Path(d))
        test_stats(Path(d))
    failed = [name for name, ok, _ in RESULTS if not ok]
    print("")
    print(f"총 {len(RESULTS)}개 중 통과 {len(RESULTS) - len(failed)}개, 실패 {len(failed)}개")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
