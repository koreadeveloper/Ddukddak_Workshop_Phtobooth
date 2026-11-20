#!/bin/bash
cd "$(dirname "$0")" || exit 1

if [ -x /opt/homebrew/bin/uv ]; then
    UV=/opt/homebrew/bin/uv
elif [ -x /usr/local/bin/uv ]; then
    UV=/usr/local/bin/uv
elif command -v uv >/dev/null 2>&1; then
    UV="$(command -v uv)"
else
    echo "uv를 찾을 수 없습니다. 설치 후 다시 실행해 주세요."
    read -r -p "Enter를 누르면 창이 닫힙니다."
    exit 1
fi

export PYTHONDONTWRITEBYTECODE=1
export PYGAME_HIDE_SUPPORT_PROMPT=1
"$UV" run --no-project --no-env-file --python 3.12 --with-requirements requirements.txt python -B -c '
import sys
import main
sys.argv = ["main.py", "--window", "--no-audio"]
main.main()
'
status=$?
if [ "$status" -ne 0 ]; then
    echo "포토부스가 실행되지 않았습니다. 위 오류 내용을 확인해 주세요."
    read -r -p "Enter를 누르면 창이 닫힙니다."
fi
exit "$status"
