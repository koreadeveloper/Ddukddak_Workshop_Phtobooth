# -*- coding: utf-8 -*-
"""인쇄 모듈 - Canon SELPHY CP1500 (CUPS)"""
import re
import subprocess
import logging
import time
from pathlib import Path

import config as cfg

log = logging.getLogger(__name__)

FALLBACK_PRINTER_NAMES = ("CP1500", "Canon_CP1500")
_logged_fallback_pairs: set[tuple[str, str]] = set()


def _candidate_printer_names() -> list[str]:
    names = [cfg.PRINTER_NAME, *FALLBACK_PRINTER_NAMES]
    out = []
    for name in names:
        name = (name or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def _extract_job_id(output: str, printer_name: str | None = None) -> str | None:
    printer_name = printer_name or cfg.PRINTER_NAME
    pattern = rf"{re.escape(printer_name)}-\d+"
    match = re.search(pattern, output or "")
    if match:
        return match.group(0)
    match = re.search(r"\b[\w.-]+-\d+\b", output or "")
    return match.group(0) if match else None


def _job_is_pending(job_id: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["lpstat", "-W", "not-completed", "-o", job_id],
        capture_output=True,
        text=True,
        timeout=5,
    )
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return False, output
    return job_id in output, output


def _status_for_printer(printer_name: str) -> tuple[bool, str]:
    printer_result = subprocess.run(
        ["lpstat", "-p", printer_name],
        capture_output=True, text=True, timeout=5
    )
    printer_output = (printer_result.stdout or printer_result.stderr or "").strip()
    if printer_result.returncode != 0:
        return False, printer_output or f"{printer_name} 프린터를 찾을 수 없습니다"

    accept_result = subprocess.run(
        ["lpstat", "-a", printer_name],
        capture_output=True, text=True, timeout=5
    )
    accept_output = (accept_result.stdout or accept_result.stderr or "").strip()
    detail = " / ".join(part for part in (printer_output, accept_output) if part)
    lower = detail.lower()
    bad_terms = ("disabled", "not accepting", "paused", "stopped")
    if accept_result.returncode != 0:
        return False, detail or f"{printer_name} 인쇄 대기열 상태를 확인할 수 없습니다"
    if any(term in lower for term in bad_terms):
        return False, detail
    return True, detail


def resolve_printer_name() -> tuple[str | None, str]:
    """현재 CUPS에서 사용할 프린터 이름과 상태 설명을 반환합니다."""
    first_error = ""
    configured_name = (cfg.PRINTER_NAME or "").strip()
    try:
        for printer_name in _candidate_printer_names():
            ok, detail = _status_for_printer(printer_name)
            if ok:
                if configured_name and printer_name != configured_name:
                    message = (
                        f"설정 프린터 '{configured_name}' 대신 "
                        f"사용 가능한 '{printer_name}' 사용 / {detail}"
                    )
                    pair = (configured_name, printer_name)
                    if pair not in _logged_fallback_pairs:
                        log.warning(message)
                        _logged_fallback_pairs.add(pair)
                    return printer_name, message
                return printer_name, detail
            if not first_error:
                first_error = detail
        return None, first_error or "사용 가능한 CUPS 프린터를 찾을 수 없습니다"
    except FileNotFoundError:
        return None, "lpstat 명령을 찾을 수 없습니다. CUPS 설치가 필요합니다."
    except subprocess.TimeoutExpired:
        return None, "프린터 상태 확인 시간이 초과되었습니다."


def wait_for_print_job(job_id: str, timeout: int | None = None) -> bool:
    """CUPS 작업이 대기열에서 사라질 때까지 기다립니다.

    timeout을 넘기면 작업이 아직 진행 중일 수 있으므로 성공으로 간주해 중복 인쇄를 막습니다.
    """
    timeout = cfg.PRINT_JOB_WAIT_SECS if timeout is None else timeout
    if timeout <= 0:
        return True

    deadline = time.time() + timeout
    poll = max(1, cfg.PRINT_JOB_POLL_SECS)
    log.info(f"인쇄 작업 대기 시작: {job_id} (timeout={timeout}s)")

    while time.time() < deadline:
        printer_ok, printer_text = get_printer_status()
        if not printer_ok:
            log.error(f"인쇄 작업 중 프린터 상태 이상: {printer_text}")
            return False

        try:
            pending, detail = _job_is_pending(job_id)
        except FileNotFoundError:
            log.warning("lpstat 명령을 찾을 수 없어 인쇄 작업 추적을 건너뜁니다")
            return True
        except subprocess.TimeoutExpired:
            log.warning(f"인쇄 작업 상태 확인 타임아웃: {job_id}")
            time.sleep(poll)
            continue

        if not pending:
            log.info(f"인쇄 작업 완료 또는 대기열에서 제거됨: {job_id}")
            return True

        log.info(f"인쇄 작업 진행 중: {detail}")
        time.sleep(poll)

    log.warning(f"인쇄 작업 대기 시간 초과: {job_id}. 중복 인쇄 방지를 위해 접수 성공으로 처리합니다.")
    return True


def print_photo(file_path: Path, copies: int = 1) -> bool:
    """CUPS lp 명령으로 엽서 크기 출력"""
    printer_name, status = resolve_printer_name()
    if not printer_name:
        log.error(f"인쇄 중단 - 프린터 상태 확인 필요: {status}")
        return False

    copies = max(1, min(int(copies), cfg.MAX_PRINT_COPIES))
    cmd = [
        "lp",
        "-d", printer_name,
        "-n", str(copies),
        "-o", "media=Postcard",
        "-o", "PageSize=Postcard",
        "-o", "fit-to-page",
        "-o", "ColorModel=RGB",
        str(file_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            output = (result.stdout or result.stderr or "").strip()
            job_id = _extract_job_id(output, printer_name)
            if job_id:
                log.info(f"인쇄 요청 성공: {file_path} ({copies}장, printer={printer_name}, job={job_id})")
                return wait_for_print_job(job_id)
            log.info(f"인쇄 요청 성공: {file_path} ({copies}장, printer={printer_name}, job id 없음)")
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
        printer_name, detail = resolve_printer_name()
        if not printer_name:
            return False, detail
        return True, f"{printer_name}: {detail}"
    except Exception as e:
        return False, f"프린터 상태 확인 실패: {e}"
