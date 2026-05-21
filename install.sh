#!/bin/bash
# 뚝딱 포토부스 - 라즈베리파이 초기 설치 스크립트
set -e

echo "================================================"
echo "  뚝딱 포토부스 설치 시작"
echo "  Raspberry Pi 5 / Logitech C920e / SELPHY CP1500"
echo "================================================"
echo ""

# ── 시스템 패키지 설치 ────────────────────────────
echo "[1/5] 시스템 패키지 설치 중..."
sudo apt-get update -q
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    python3-dev \
    libopencv-dev \
    libatlas-base-dev \
    libsdl2-dev \
    libsdl2-image-dev \
    libsdl2-mixer-dev \
    fonts-nanum \
    cups \
    printer-driver-gutenprint \
    v4l-utils \
    libjpeg-dev \
    libpng-dev

echo "  ✓ 시스템 패키지 설치 완료"

# ── 가상환경 생성 ────────────────────────────────
echo "[2/5] Python 가상환경 생성 중..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip --quiet

echo "  ✓ 가상환경 생성 완료"

# ── Python 패키지 설치 ───────────────────────────
echo "[3/5] Python 패키지 설치 중..."
pip install -r requirements.txt --quiet
echo "  ✓ Python 패키지 설치 완료"

# ── 디렉터리 생성 ────────────────────────────────
echo "[4/5] 폴더 생성 중..."
mkdir -p photos assets
echo "  ✓ photos/ assets/ 생성 완료"

# ── CUPS 프린터 설정 안내 ────────────────────────
echo "[5/5] CUPS 프린터 설정..."
sudo usermod -aG lpadmin $USER 2>/dev/null || true
echo ""
echo "  SELPHY CP1500을 USB로 연결한 뒤 아래 명령으로 프린터 추가:"
echo ""
echo "    sudo systemctl start cups"
echo "    lpstat -p                  # 프린터 목록 확인"
echo "    # 또는 브라우저에서 http://localhost:631 접속"
echo ""
echo "  프린터 이름 확인 후 config.py 의 PRINTER_NAME 을 수정하세요."
echo ""

# ── 실행 권한 ────────────────────────────────────
chmod +x run.sh

echo "================================================"
echo "  ✅ 설치 완료!"
echo ""
echo "  실행 방법:"
echo "    ./run.sh              # 일반 실행"
echo "    ./run.sh --test       # 카메라 없이 테스트"
echo "    ./run.sh --window     # 창 모드 (디버그)"
echo "================================================"
