# -*- coding: utf-8 -*-
"""AI 변신 콘셉트 로더/검증 (assets/ai_concepts.json)

pygame에 의존하지 않습니다. 잘못된 콘셉트는 그것만 제외하고 나머지로 계속 실행하며,
파일이 없거나 깨져도 빈 목록을 돌려줄 뿐 예외를 던지지 않습니다.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import config as cfg

log = logging.getLogger(__name__)

REQUIRED_KEYS = ("id", "name", "prompt")
CATEGORIES = ("profile", "job", "traditional", "stage", "sports", "fun")
ID_PATTERN = re.compile(r"^[a-z0-9_]+$")

# 썸네일이 없을 때 카드 배경으로 쓰는 카테고리 색 (RGB)
CATEGORY_COLORS = {
    "profile": (70, 90, 130),
    "job": (200, 90, 60),
    "traditional": (170, 60, 90),
    "stage": (120, 60, 170),
    "sports": (40, 140, 80),
    "fun": (220, 160, 40),
}


@dataclass(frozen=True)
class AIConcept:
    id: str
    name: str
    prompt: str
    category: str = "fun"
    description: str = ""
    print_label: str = ""
    default_frame: str = ""
    default_filter: str = "original"
    thumbnail: str = ""
    thumbnail_path: Path | None = None   # 파일이 실제로 있을 때만 설정

    @property
    def color(self) -> tuple[int, int, int]:
        return CATEGORY_COLORS.get(self.category, CATEGORY_COLORS["fun"])


def _text(item: dict, key: str, default: str = "") -> str:
    value = item.get(key, default)
    return value.strip() if isinstance(value, str) else default


def _parse_item(item, base_dir: Path) -> AIConcept | None:
    """콘셉트 1개 검증. 문제가 있으면 경고 로그를 남기고 None."""
    if not isinstance(item, dict):
        log.warning("AI 콘셉트 제외: 항목이 객체가 아님")
        return None
    cid = item.get("id")
    label = cid if isinstance(cid, str) and cid else "?"
    missing = [k for k in REQUIRED_KEYS if not _text(item, k)]
    if missing:
        log.warning(f"AI 콘셉트 제외 ({label}): 필수 키 누락 {missing}")
        return None
    cid = _text(item, "id")
    if not ID_PATTERN.match(cid):
        log.warning(f"AI 콘셉트 제외 ({label}): id는 영문 소문자/숫자/_ 만 가능")
        return None

    category = _text(item, "category", "fun")
    if category not in CATEGORIES:
        log.warning(f"AI 콘셉트 {cid}: 알 수 없는 카테고리 {category!r} → fun")
        category = "fun"

    thumbnail = _text(item, "thumbnail")
    thumbnail_path = None
    if thumbnail:
        path = Path(thumbnail)
        if not path.is_absolute():
            path = base_dir / path
        try:
            is_file = path.is_file()
        except OSError:
            is_file = False
        if is_file:
            thumbnail_path = path
        else:
            log.info(f"AI 콘셉트 {cid}: 썸네일 없음 ({thumbnail}) → 색상 카드로 대체")

    name = _text(item, "name")
    return AIConcept(
        id=cid,
        name=name,
        prompt=_text(item, "prompt"),
        category=category,
        description=_text(item, "description"),
        print_label=_text(item, "print_label") or f"AI {name}",
        default_frame=_text(item, "default_frame"),
        default_filter=_text(item, "default_filter", "original") or "original",
        thumbnail=thumbnail,
        thumbnail_path=thumbnail_path,
    )


def load_concepts(path: Path | str | None = None, base_dir: Path | None = None) -> list[AIConcept]:
    """활성화된 정상 콘셉트 목록. 파일 오류 시 빈 목록 (예외 없음)."""
    path = Path(path) if path is not None else Path(cfg.AI_CONCEPTS_FILE)
    base_dir = base_dir or cfg.BASE_DIR
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning(f"AI 콘셉트 파일 없음: {path}")
        return []
    except Exception as e:
        log.warning(f"AI 콘셉트 파일 읽기 실패 ({path}): {type(e).__name__}: {e}")
        return []

    items = data.get("concepts") if isinstance(data, dict) else data
    if not isinstance(items, list):
        log.warning(f"AI 콘셉트 파일 형식 오류 ({path}): concepts 목록이 없음")
        return []

    concepts: list[AIConcept] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, dict) and not item.get("enabled", True):
            continue
        concept = _parse_item(item, base_dir)
        if concept is None:
            continue
        if concept.id in seen:
            log.warning(f"AI 콘셉트 제외 ({concept.id}): id 중복")
            continue
        seen.add(concept.id)
        concepts.append(concept)
    log.info(f"AI 콘셉트 {len(concepts)}개 로드")
    return concepts


def find_concept(concepts: list[AIConcept], concept_id: str | None) -> AIConcept | None:
    for concept in concepts:
        if concept.id == concept_id:
            return concept
    return None
