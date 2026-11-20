#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 변신 PoC / 프롬프트 튜닝 스크립트 (pygame 의존 없음)

예시:
    python3 tools/ai_poc.py samples/group4.jpg --concept all --repeat 2 --out poc_out/
    python3 tools/ai_poc.py samples/*.jpg --concept firefighter --provider mock

입력 사진은 '방향 보정된 원본'으로 취급하고, 실서비스와 같은 ai_transform 전/후처리 함수를 씁니다.
결과: 콘셉트별 JPG + {out}/report.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import statistics
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

import cv2  # noqa: E402

import config as cfg  # noqa: E402
import ai_concepts  # noqa: E402
import ai_transform  # noqa: E402

REPORT_FIELDS = ["concept", "input", "ok", "refused", "elapsed", "out_w", "out_h", "error"]
def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 변신 PoC (Provider 단독 실행)")
    parser.add_argument("inputs", nargs="+", type=Path, help="입력 사진 (jpg/png)")
    parser.add_argument("--concept", default="all", help="콘셉트 id 또는 all (쉼표로 여러 개 가능)")
    parser.add_argument("--repeat", type=int, default=1, help="콘셉트별 반복 횟수")
    parser.add_argument("--out", type=Path, default=REPO_DIR / "poc_out", help="결과 폴더")
    parser.add_argument("--provider", default=cfg.AI_PROVIDER, help="monorouter | gemini | mock (기본: .env)")
    parser.add_argument("--model", default="", help="모델 ID 덮어쓰기 (기본: .env 또는 provider 기본값)")
    parser.add_argument("--timeout", type=float, default=cfg.AI_REQUEST_TIMEOUT, help="요청당 타임아웃(초)")
    parser.add_argument("--concepts-file", type=Path, default=cfg.AI_CONCEPTS_FILE)
    parser.add_argument("--save-upload", action="store_true", help="실제 전송한 전처리 이미지도 저장")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    try:
        provider = ai_transform.get_provider(args.provider)
    except ValueError as e:
        print(f"[오류] {e}")
        return 2
    if args.model and hasattr(provider, "model"):
        provider.model = args.model
    if provider.name != "mock" and not provider.has_key:
        print(f"[오류] {provider.key_env}가 비어 있습니다. .env에 키를 넣거나 --provider mock으로 실행하세요.")
        return 2

    concepts = ai_concepts.load_concepts(args.concepts_file)
    if args.concept != "all":
        wanted = [c.strip() for c in args.concept.split(",") if c.strip()]
        unknown = set(wanted) - {c.id for c in concepts}
        if unknown:
            print(f"[오류] 알 수 없는 콘셉트: {', '.join(sorted(unknown))}")
            print("       사용 가능: " + ", ".join(c.id for c in concepts))
            return 2
        concepts = [c for c in concepts if c.id in wanted]
    if not concepts:
        print("[오류] 실행할 콘셉트가 없습니다.")
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    report_path = args.out / "report.csv"
    print(f"provider={provider.name} model={getattr(provider, 'model', '-')} "
          f"콘셉트 {len(concepts)}개 × 입력 {len(args.inputs)}장 × {args.repeat}회")

    rows = []
    with report_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
        writer.writeheader()

        def _write(row: dict):
            rows.append(row)
            writer.writerow(row)
            f.flush()

        used_stems: set[str] = set()
        for input_path in args.inputs:
            # 다른 폴더의 같은 파일명이 서로 덮어쓰지 않도록 이름 구분
            stem, n = input_path.stem, 1
            while stem in used_stems:
                n += 1
                stem = f"{input_path.stem}_{n}"
            used_stems.add(stem)
            raw = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
            if raw is None:
                for concept in concepts:
                    _write({"concept": concept.id, "input": str(input_path), "ok": 0, "refused": 0,
                            "elapsed": "0.00", "out_w": 0, "out_h": 0, "error": "입력 이미지 읽기 실패"})
                continue
            aspect = ai_transform.slot_aspect_for(raw)
            upload = ai_transform.prepare_upload_image(raw)
            if args.save_upload:
                cv2.imwrite(str(args.out / f"{stem}__upload.jpg"), upload,
                            [int(cv2.IMWRITE_JPEG_QUALITY), ai_transform.JPEG_QUALITY])

            for concept in concepts:
                for rep in range(1, args.repeat + 1):
                    # 실서비스(AITransformManager)와 같은 재시도 규칙 사용, 예외를 던지지 않음
                    res = ai_transform.transform_with_retry(provider, upload, concept.prompt, args.timeout)
                    out_w = out_h = 0
                    if res.ok and res.image_bgr is not None:
                        out_h, out_w = res.image_bgr.shape[:2]
                        final = ai_transform.postprocess_result(res.image_bgr, aspect)
                        out_file = args.out / f"{stem}__{concept.id}__{rep}.jpg"
                        if not cv2.imwrite(str(out_file), final, [int(cv2.IMWRITE_JPEG_QUALITY), 95]):
                            res.ok = False
                            res.error = f"결과 저장 실패: {out_file}"
                    status = "성공" if res.ok else ("거절" if res.refused else "실패")
                    print(f"  {input_path.name} / {concept.id} #{rep}: {status} "
                          f"{res.elapsed:.1f}s {out_w}x{out_h} {res.error}")
                    _write({
                        "concept": concept.id, "input": str(input_path),
                        "ok": int(res.ok), "refused": int(res.refused),
                        "elapsed": f"{res.elapsed:.2f}", "out_w": out_w, "out_h": out_h,
                        "error": res.error,
                    })

    ok_times = [float(r["elapsed"]) for r in rows if r["ok"]]
    print("")
    print(f"총 {len(rows)}건: 성공 {sum(r['ok'] for r in rows)} / 거절 {sum(r['refused'] for r in rows)} "
          f"/ 실패 {sum(1 for r in rows if not r['ok'] and not r['refused'])}")
    if ok_times:
        print(f"성공 응답 시간: p50 {statistics.median(ok_times):.1f}s / p95 {percentile(ok_times, 95):.1f}s "
              f"/ 최대 {max(ok_times):.1f}s")
    print(f"리포트: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
