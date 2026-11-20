# -*- coding: utf-8 -*-
"""운영 통계 저장 모듈."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime

import config as cfg

log = logging.getLogger(__name__)

STATS_PATH = cfg.BASE_DIR / "booth_stats.json"
COUNTERS = (
    "sessions",
    "print_success",
    "print_failure",
    "qr_shown",
    "qr_page_views",
    "qr_downloads",
    "ai_sessions",
    "ai_requests",
    "ai_success",
    "ai_failure",
    "ai_refused",
    "ai_regenerate",
)
CONCEPT_COUNTS_KEY = "ai_concept_counts"   # 콘셉트별 인기 통계 (COUNTERS와 별도 저장)

_lock = threading.Lock()


def _empty_counts() -> dict[str, int]:
    return {key: 0 for key in COUNTERS}


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _load_unlocked() -> dict:
    if not STATS_PATH.exists():
        return {"total": _empty_counts(), "daily": {}}
    try:
        data = json.loads(STATS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"운영 통계 읽기 실패: {e}")
        return {"total": _empty_counts(), "daily": {}}

    data.setdefault("total", {})
    data.setdefault("daily", {})
    for key in COUNTERS:
        data["total"].setdefault(key, 0)
    return data


def _save_unlocked(data: dict):
    tmp = STATS_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(STATS_PATH)


def increment(counter: str, amount: int = 1):
    if counter not in COUNTERS:
        raise ValueError(f"Unknown stats counter: {counter}")
    with _lock:
        data = _load_unlocked()
        today = _today()
        daily = data["daily"].setdefault(today, _empty_counts())
        for key in COUNTERS:
            daily.setdefault(key, 0)
            data["total"].setdefault(key, 0)
        daily[counter] += amount
        data["total"][counter] += amount
        _save_unlocked(data)


def increment_concept(concept_id: str, amount: int = 1):
    """콘셉트별 선택 횟수. 날짜 구분 없이 누적만 합니다."""
    if not concept_id:
        return
    with _lock:
        data = _load_unlocked()
        counts = data.get(CONCEPT_COUNTS_KEY)
        if not isinstance(counts, dict):
            counts = {}
            data[CONCEPT_COUNTS_KEY] = counts
        counts[concept_id] = int(counts.get(concept_id, 0)) + amount
        _save_unlocked(data)


def summary() -> dict:
    with _lock:
        data = _load_unlocked()
    today_counts = data.get("daily", {}).get(_today(), _empty_counts())
    total_counts = data.get("total", _empty_counts())
    return {
        "today": {key: int(today_counts.get(key, 0)) for key in COUNTERS},
        "total": {key: int(total_counts.get(key, 0)) for key in COUNTERS},
        "concepts": {
            str(k): int(v) for k, v in (data.get(CONCEPT_COUNTS_KEY) or {}).items()
            if isinstance(v, (int, float))
        },
    }
