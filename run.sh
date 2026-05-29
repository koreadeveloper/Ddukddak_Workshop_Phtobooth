#!/bin/bash
# 뚝딱 포토부스 실행 스크립트
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# .env 설정 로드
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.env"
    set +a
fi

export PYTHONUNBUFFERED=1
export PYGAME_HIDE_SUPPORT_PROMPT=1

# 디스플레이 설정. 최신 Raspberry Pi OS는 Wayland가 기본이므로 SDL이 자동 선택하게 둡니다.
if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    export DISPLAY=:0
fi
if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi
if [ -n "${PHOTOBOOTH_SDL_VIDEODRIVER:-}" ]; then
    export SDL_VIDEODRIVER="$PHOTOBOOTH_SDL_VIDEODRIVER"
fi
if [ -n "${PHOTOBOOTH_AUDIO_DRIVER:-}" ]; then
    export SDL_AUDIODRIVER="$PHOTOBOOTH_AUDIO_DRIVER"
fi

# 가상환경 활성화 (있을 경우)
if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
fi

echo "뚝딱 포토부스 시작..."
python3 -u main.py "$@"
