#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""라즈베리파이 현장 실행 전 점검 도구."""
from __future__ import annotations

import argparse
import importlib
import os
import shutil
import socket
import subprocess
import sys

import config as cfg
import booth_stats
import printer


def _ok(message: str):
    print(f"[OK] {message}")


def _warn(message: str):
    print(f"[WARN] {message}")


def _fail(message: str):
    print(f"[FAIL] {message}")


def _run(cmd: list[str], timeout: int = 5) -> tuple[int, str]:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout or result.stderr or "").strip()
        return result.returncode, output
    except FileNotFoundError:
        return 127, f"명령을 찾을 수 없습니다: {cmd[0]}"
    except Exception as e:
        return 1, str(e)


def check_python_modules() -> bool:
    modules = ["pygame", "cv2", "PIL", "qrcode", "numpy"]
    passed = True
    for module in modules:
        try:
            importlib.import_module(module)
            _ok(f"Python 모듈: {module}")
        except Exception as e:
            _fail(f"Python 모듈 누락/오류: {module} ({e})")
            passed = False
    return passed


def check_display() -> bool:
    display = os.getenv("DISPLAY")
    wayland = os.getenv("WAYLAND_DISPLAY")
    if display or wayland:
        _ok(f"디스플레이 환경: DISPLAY={display or '-'}, WAYLAND_DISPLAY={wayland or '-'}")
        return True
    _warn("DISPLAY/WAYLAND_DISPLAY가 없습니다. SSH가 아니라 Pi 데스크톱에서 실행하세요.")
    return False


def check_camera() -> bool:
    code, output = _run(["v4l2-ctl", "--list-devices"])
    if code == 0 and output:
        _ok("v4l2 카메라 목록 확인")
        print(output)
    else:
        _warn(f"v4l2-ctl 확인 실패: {output}")

    try:
        import cv2

        source = cfg.CAM_DEVICE or cfg.CAM_INDEX
        cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            _fail(f"카메라 열기 실패: {source}")
            return False
        ok, _frame = cap.read()
        cap.release()
        if not ok:
            _fail(f"카메라 프레임 읽기 실패: {source}")
            return False
        _ok(f"카메라 프레임 읽기 성공: {source}")
        _ok(
            "카메라 복구 설정: "
            f"{cfg.CAM_STALE_SECS}초 이상 새 프레임 없음 또는 "
            f"{cfg.CAM_MAX_READ_FAILURES}회 읽기 실패 시 "
            f"{cfg.CAM_RECONNECT_SECS}초 간격 재연결"
        )
        return True
    except Exception as e:
        _fail(f"카메라 점검 오류: {e}")
        return False


def check_printer() -> bool:
    code, output = _run(["systemctl", "is-active", "cups"])
    if code == 0 and output == "active":
        _ok("CUPS 서비스 active")
    else:
        _warn(f"CUPS 서비스 확인 필요: {output}")

    ok, status = printer.get_printer_status()
    if ok:
        _ok(f"CUPS 프린터 사용 가능: {status}")
        _ok(
            "인쇄 작업 추적 설정: "
            f"{cfg.PRINT_JOB_WAIT_SECS}초까지 "
            f"{cfg.PRINT_JOB_POLL_SECS}초 간격으로 CUPS 대기열 확인"
        )
        return True
    _warn(f"프린터 확인 실패: {cfg.PRINTER_NAME} ({status})")
    return False


def check_qr_port() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", cfg.QR_SERVER_PORT))
            _ok(f"QR 포트 사용 가능: {cfg.QR_SERVER_PORT}")
            return True
        except OSError as e:
            _warn(f"QR 포트가 이미 사용 중입니다: {cfg.QR_SERVER_PORT} ({e})")
            return False


def check_storage() -> bool:
    total, _used, free = shutil.disk_usage(cfg.BASE_DIR)
    free_gb = free / (1024 ** 3)
    total_gb = total / (1024 ** 3)
    message = (
        f"저장공간 여유 {free_gb:.1f}GB / 전체 {total_gb:.1f}GB "
        f"(최소 {cfg.MIN_FREE_GB}GB, 보관 {cfg.PHOTO_RETENTION_DAYS}일, 최대 {cfg.MAX_STORED_PHOTOS}장)"
    )
    if free_gb >= cfg.MIN_FREE_GB:
        _ok(message)
        return True
    _warn(message)
    return False


def check_stats() -> bool:
    stats = booth_stats.summary()
    today = stats["today"]
    _ok(
        "운영 통계: "
        f"오늘 촬영 {today['sessions']}회, "
        f"인쇄 성공 {today['print_success']}회, "
        f"QR 다운로드 {today['qr_downloads']}회"
    )
    return True


def _ai_live_test_image():
    """samples/의 첫 JPG, 없으면 합성 테스트 이미지."""
    import cv2
    import numpy as np

    samples = sorted((cfg.BASE_DIR / "samples").glob("*.jpg"))
    for path in samples:
        img = cv2.imread(str(path))
        if img is not None:
            return img, path.name
    img = np.full((720, 1280, 3), (200, 210, 225), np.uint8)
    cv2.circle(img, (640, 330), 150, (150, 180, 220), -1)
    cv2.rectangle(img, (470, 470), (810, 720), (90, 90, 140), -1)
    return img, "합성 테스트 이미지"


def check_ai(live: bool = False) -> bool:
    """AI 설정/키 존재/콘셉트/네트워크 점검. 키 값은 절대 출력하지 않음.

    live=True일 때만 실제 이미지 1장을 변환합니다 (비용 발생).
    """
    if not cfg.AI_ENABLED and not live:
        _ok("AI 변신 꺼짐 (PHOTOBOOTH_AI_ENABLED=0) - 점검 생략")
        return True
    if not cfg.AI_ENABLED:
        _warn("PHOTOBOOTH_AI_ENABLED=0 상태지만 --ai-live 요청으로 점검합니다")

    try:
        import requests  # noqa: F401
        import ai_concepts
        import ai_transform
    except Exception as e:
        _fail(f"AI 모듈 불러오기 실패: {type(e).__name__}: {e}")
        return False

    passed = True
    try:
        provider = ai_transform.get_provider()
    except Exception as e:
        _fail(f"AI Provider 설정 오류: {e}")
        return False
    model = getattr(provider, "model", "") or provider.name
    _ok(f"AI Provider: {provider.name} · 모델 {model}")
    if provider.name != "mock":
        if getattr(provider, "has_key", False):
            _ok("AI API 키 설정됨 (값은 표시하지 않음)")
        else:
            _fail("PHOTOBOOTH_AI_API_KEY가 비어 있습니다")
            passed = False

    concepts = ai_concepts.load_concepts()
    if concepts:
        missing = [c.id for c in concepts if c.thumbnail_path is None]
        _ok(f"AI 콘셉트 {len(concepts)}개 로드 ({cfg.AI_CONCEPTS_FILE})")
        if missing:
            _warn(f"콘셉트 썸네일 없음 {len(missing)}개 → 색상 카드로 표시: {', '.join(missing)}")
    else:
        _fail(f"사용할 수 있는 AI 콘셉트가 없습니다: {cfg.AI_CONCEPTS_FILE}")
        passed = False

    if provider.name != "mock":
        from urllib.parse import urlparse

        host = urlparse(ai_transform.GEMINI_API_BASE).hostname
        try:
            socket.getaddrinfo(host, 443)
            _ok(f"AI 서버 DNS 확인: {host}")
        except OSError as e:
            _fail(f"AI 서버 DNS 실패: {host} ({e}) - 인터넷 연결을 확인하세요")
            return False
    ok, text = provider.health_check(timeout=10.0)
    if ok:
        _ok(f"AI 서버 HTTPS 연결 확인: {text}")
    else:
        _fail(f"AI 서버 연결 확인 실패: {text}")
        passed = False

    if not live:
        return passed
    if not passed or not concepts:
        _warn("기본 점검이 실패해 실제 변환 테스트(--ai-live)는 생략합니다")
        return False

    import cv2

    image, label = _ai_live_test_image()
    concept = concepts[0]
    upload = ai_transform.prepare_upload_image(image)
    print(f"실제 변환 1장 테스트: {concept.name} · {label} (비용 발생)")
    res = ai_transform.transform_with_retry(provider, upload, concept.prompt, cfg.AI_REQUEST_TIMEOUT)
    if not res.ok:
        kind = "안전정책 거절" if res.refused else "실패"
        _fail(f"AI 실제 변환 {kind} ({res.elapsed:.1f}초): {res.error}")
        return False
    out_dir = cfg.BASE_DIR / "poc_out"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "doctor_ai_live.jpg"
    result = ai_transform.postprocess_result(res.image_bgr, ai_transform.slot_aspect_for(image))
    saved = cv2.imwrite(str(out_path), result)
    _ok(f"AI 실제 변환 성공: {res.elapsed:.1f}초" + (f" → {out_path}" if saved else " (결과 저장 실패)"))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="포토부스 현장 점검")
    parser.add_argument("--ai-live", action="store_true",
                        help="AI 실제 변환 1장 테스트 (API 비용 발생)")
    args = parser.parse_args()

    print(f"{cfg.BRAND_NAME} Pi 점검")
    print(f"Python: {sys.version.split()[0]}")
    checks = [
        check_python_modules(),
        check_display(),
        check_camera(),
        check_printer(),
        check_qr_port(),
        check_storage(),
        check_stats(),
        check_ai(live=args.ai_live),
    ]
    if all(checks):
        _ok("전체 점검 통과")
        return 0
    _warn("일부 항목 확인이 필요합니다. 위 FAIL/WARN을 먼저 처리하세요.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
