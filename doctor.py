#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""라즈베리파이 현장 실행 전 점검 도구."""
from __future__ import annotations

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


def main() -> int:
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
    ]
    if all(checks):
        _ok("전체 점검 통과")
        return 0
    _warn("일부 항목 확인이 필요합니다. 위 FAIL/WARN을 먼저 처리하세요.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
