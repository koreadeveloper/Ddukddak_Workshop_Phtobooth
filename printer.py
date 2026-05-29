# -*- coding: utf-8 -*-
"""인쇄 모듈 - Canon SELPHY CP1500 (CUPS)"""
import subprocess
import logging
from pathlib import Path

import config as cfg

log = logging.getLogger(__name__)


def print_photo(file_path: Path) -> bool:
    """CUPS lp 명령으로 엽서 크기 출력"""
    cmd = [
        "lp",
        "-d", cfg.PRINTER_NAME,
        "-n", "1",
        "-o", "media=Postcard",
        "-o", "PageSize=Postcard",
        "-o", "fit-to-page",
        "-o", "ColorModel=RGB",
        str(file_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            log.info(f"인쇄 요청 성공: {file_path}")
            return True
        log.error(f"인쇄 실패 (code={result.returncode}): {result.stderr.strip()}")
        return False
    except FileNotFoundError:
        log.error("lp 명령을 찾을 수 없습니다. CUPS가 설치되어 있는지 확인하세요.")
        return False
    except subprocess.TimeoutExpired:
        log.error("인쇄 명령 타임아웃")
        return False
    except Exception as e:
        log.error(f"인쇄 예외: {e}")
        return False


def printer_available() -> bool:
    """프린터 연결 여부 확인"""
    return bool(get_printer_status()[0])


def get_printer_status() -> tuple[bool, str]:
    """CUPS에 등록된 프린터 상태를 반환합니다."""
    try:
        result = subprocess.run(
            ["lpstat", "-p", cfg.PRINTER_NAME],
            capture_output=True, text=True, timeout=5
        )
        output = (result.stdout or result.stderr or "").strip()
        return result.returncode == 0, output
    except FileNotFoundError:
        return False, "lpstat 명령을 찾을 수 없습니다. CUPS 설치가 필요합니다."
    except Exception as e:
        return False, f"프린터 상태 확인 실패: {e}"
