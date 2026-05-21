#!/bin/bash
# 뚝딱 포토부스 실행 스크립트
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 디스플레이 설정 (라즈베리파이 데스크톱)
export DISPLAY="${DISPLAY:-:0}"
export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-x11}"

# 오디오 (선택 사항)
export SDL_AUDIODRIVER=alsa

# 가상환경 활성화 (있을 경우)
if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
fi

echo "뚝딱 포토부스 시작..."
python3 main.py "$@"
