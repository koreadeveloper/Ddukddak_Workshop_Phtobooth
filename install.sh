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
IS_RPI=0
if [ -r /proc/device-tree/model ] && tr -d '\0' < /proc/device-tree/model | grep -qi "Raspberry Pi"; then
    IS_RPI=1
fi

sudo apt-get update -q
sudo apt-get install -y \
    git \
    python3-pip \
    python3-venv \
    python3-dev \
    python3-opencv \
    python3-pygame \
    python3-numpy \
    python3-pil \
    python3-qrcode \
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
if [ "$IS_RPI" = "1" ]; then
    python3 -m venv --system-site-packages venv
else
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip --quiet

echo "  ✓ 가상환경 생성 완료"

# ── Python 패키지 설치 ───────────────────────────
echo "[3/5] Python 패키지 설치 중..."
if [ "$IS_RPI" = "1" ]; then
    # Pi에서는 apt의 OpenCV/Pygame 빌드를 우선 사용합니다.
    pip install "qrcode[pil]>=7.4.2" --quiet
else
    pip install -r requirements.txt --quiet
fi
echo "  ✓ Python 패키지 설치 완료"

# ── 디렉터리 생성 ────────────────────────────────
echo "[4/5] 폴더 생성 중..."
mkdir -p photos assets
echo "  ✓ photos/ assets/ 생성 완료"

# ── CUPS 프린터 설정 안내 ────────────────────────
echo "[5/5] CUPS 프린터 설정..."
sudo systemctl enable --now cups 2>/dev/null || true
sudo usermod -aG video,lpadmin "$USER" 2>/dev/null || true
echo ""
echo "  SELPHY CP1500을 USB로 연결한 뒤 아래 명령으로 프린터 추가:"
echo ""
echo "    sudo systemctl enable --now cups"
echo "    lpinfo -v                  # USB URI 확인"
echo "    lpstat -p                  # 프린터 목록 확인"
echo "    # 또는 브라우저에서 http://localhost:631 접속"
echo ""
echo "  프린터 이름 확인 후 config.py 의 PRINTER_NAME 을 수정하세요."
echo ""

ENV_TEMPLATE=".env.example"
if [ ! -f "$ENV_TEMPLATE" ]; then
    echo "  ! $ENV_TEMPLATE 파일이 없어 .env 설정 생성을 건너뜁니다"
elif [ ! -f .env ]; then
    cp "$ENV_TEMPLATE" .env
    echo "  ✓ .env 기본 설정 파일 생성"
else
    backup=".env.backup.$(date +%Y%m%d%H%M%S)"
    cp .env "$backup"
    added=0
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            PHOTOBOOTH_*=*)
                key="${line%%=*}"
                if ! grep -qE "^[[:space:]]*(export[[:space:]]+)?${key}=" .env; then
                    if [ "$added" -eq 0 ]; then
                        {
                            echo ""
                            echo "# install.sh가 추가한 새 기본 설정"
                        } >> .env
                    fi
                    echo "$line" >> .env
                    added=$((added + 1))
                fi
                ;;
        esac
    done < "$ENV_TEMPLATE"
    if [ "$added" -gt 0 ]; then
        echo "  ✓ 기존 .env 유지, 누락 설정 ${added}개 추가 (백업: $backup)"
    else
        rm -f "$backup"
        echo "  ✓ 기존 .env 설정 최신 상태"
    fi
fi

# ── 실행 권한 ────────────────────────────────────
chmod +x run.sh

echo "================================================"
echo "  ✅ 설치 완료!"
echo ""
echo "  실행 방법:"
echo "    ./run.sh              # 일반 실행"
echo "    ./run.sh --test       # 카메라 없이 테스트"
echo "    ./run.sh --window     # 창 모드 (디버그)"
echo ""
echo "  참고:"
echo "    그룹 권한(video/lpadmin)을 적용하려면 로그아웃 후 재로그인하거나 재부팅하세요."
echo "================================================"
