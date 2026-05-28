# 🚀 뚝딱 포토부스 — 라즈베리파이 실행 가이드

> **파일이 이미 복사된 상태**에서 시작하는 기준입니다.

---

## 📋 목차
1. [파일 위치 확인](#1-파일-위치-확인)
2. [패키지 설치](#2-패키지-설치)
3. [카메라 인식 확인](#3-카메라-인식-확인)
4. [화면 없이 테스트 실행](#4-테스트-실행카메라-없이)
5. [카메라 연결 후 실제 실행](#5-카메라-연결-후-실제-실행)
6. [프린터 설정 (SELPHY CP1500)](#6-프린터-설정-selphy-cp1500)
7. [QR 공유 테스트](#7-qr-공유-테스트)
8. [부팅 자동 시작 설정](#8-부팅-자동-시작-설정)
9. [문제 해결](#9-문제-해결)

---

## 1. 파일 위치 확인

터미널을 열고 파일이 복사된 폴더로 이동합니다.

```bash
# 예시: 홈 폴더 아래 photobooth 폴더에 복사한 경우
cd ~/photobooth

# 파일 목록 확인 — 아래 파일들이 있어야 합니다
ls -la
```

**있어야 할 파일 목록:**
```
main.py          ← 메인 앱
config.py        ← 설정 파일
camera.py        ← 카메라 모듈
composer.py      ← 스트립 합성
qr_share.py      ← QR 서버
printer.py       ← 인쇄 모듈
requirements.txt ← Python 패키지 목록
install.sh       ← 설치 스크립트
run.sh           ← 실행 스크립트
```

---

## 2. 패키지 설치

### 2-1. 실행 권한 부여

```bash
chmod +x install.sh run.sh
```

### 2-2. 설치 스크립트 실행

```bash
./install.sh
```

이 스크립트가 자동으로 설치하는 항목:
- `python3-venv` `python3-dev` — Python 환경
- `fonts-nanum` — ✅ **한국어 폰트** (필수!)
- `libopencv-dev` — 카메라용 OpenCV
- `cups` `printer-driver-gutenprint` — 프린터 드라이버
- `pygame`, `Pillow`, `qrcode` 등 Python 패키지

> 📶 **인터넷 연결** 상태에서 실행하세요. 시간이 5~10분 걸릴 수 있습니다.

### 2-3. 설치 확인

```bash
# 가상환경 활성화
source venv/bin/activate

# 패키지 확인
python3 -c "import pygame, cv2, PIL, qrcode; print('✅ 모든 패키지 정상')"
```

---

## 3. 카메라 인식 확인

C920e를 USB에 꽂고 확인합니다.

```bash
# 연결된 카메라 목록 확인
v4l2-ctl --list-devices
```

출력 예시:
```
HD Pro Webcam C920 (usb-0000:01:00.0-1.4):
        /dev/video0    ← 이 번호가 중요!
        /dev/video1
```

**`/dev/video0`** 이 아닌 다른 번호면 `config.py` 수정:

```bash
nano config.py
```

```python
# 아래 줄을 찾아서 번호 변경
CAM_INDEX = 0    # ← /dev/video1 이면 1로 변경
```

저장: `Ctrl+O` → `Enter` → `Ctrl+X`

---

## 4. 테스트 실행 (카메라 없이)

카메라나 프린터 없이 **화면과 UI만 테스트**합니다.

```bash
./run.sh --test
```

확인 항목:
- [x] 분홍색 배경 + "뚝딱 포토부스" 타이틀 표시
- [x] 파티클(방울) 애니메이션 움직임
- [x] 화면 터치/클릭 시 카운트다운 시작
- [x] 3-2-1 카운트다운 숫자 표시
- [x] 4장 찍힌 후 REVIEW 화면 전환
- [x] 버튼 3개 (인쇄하기 / QR 받기 / 다시 찍기) 표시

> ❌ 종료하려면 `ESC` 키

---

## 5. 카메라 연결 후 실제 실행

```bash
./run.sh
```

또는 **창 모드** (디버그할 때 편함):

```bash
./run.sh --window
```

확인 항목:
- [x] IDLE 화면에 실제 카메라 소형 미리보기 표시
- [x] 카운트다운 중 풀스크린 카메라 화면
- [x] 사진 찍힐 때 흰 플래시 효과
- [x] REVIEW 화면에 4장 썸네일 + 스트립 미리보기

---

## 6. 프린터 설정 (SELPHY CP1500)

### 6-1. CUPS 서비스 시작

```bash
sudo systemctl enable cups
sudo systemctl start cups

# 상태 확인
sudo systemctl status cups
```

### 6-2. USB로 CP1500 연결 후 URI 확인

```bash
lpinfo -v
```

출력 예시:
```
direct usb://Canon/CP1500?serial=XXXXXXXXX&interface=1
```

### 6-3. 프린터 등록

```bash
sudo lpadmin -p Canon_CP1500 \
  -E \
  -v "usb://Canon/CP1500?serial=XXXXXXXXX&interface=1" \
  -m gutenprint.5.3://canonselphycp1500/expert

# 등록 확인
lpstat -p Canon_CP1500
```

출력에 `printer Canon_CP1500 is idle` 이 나오면 성공.

### 6-4. 프린터 이름이 다를 경우

```bash
# 등록된 모든 프린터 이름 확인
lpstat -p
```

`config.py`에서 이름 수정:

```bash
nano config.py
```

```python
PRINTER_NAME = "Canon_CP1500"   # ← 실제 이름으로 수정
```

### 6-5. 테스트 인쇄

```bash
# 엽서 크기로 테스트 인쇄 (이미지가 있을 경우)
lp -d Canon_CP1500 -o media=Postcard photos/test.jpg
```

---

## 7. QR 공유 테스트

### QR이 작동하는 조건
- 라즈베리파이와 스마트폰이 **같은 WiFi**에 연결되어 있어야 합니다.

### 테스트 순서

1. 포토부스 앱 실행
2. 사진 4장 촬영
3. REVIEW 화면에서 **"📱 QR 받기"** 버튼 클릭
4. QR 코드 화면에서 **Pi의 IP 주소 확인**
5. 같은 WiFi 스마트폰으로 QR 스캔
6. 브라우저에서 사진 다운로드 확인

### Pi IP 주소 수동 확인

```bash
hostname -I
# 또는
ip addr show | grep "inet " | grep -v 127.0.0.1
```

### 방화벽 포트 허용 (필요 시)

```bash
sudo ufw allow 8080/tcp
```

---

## 8. 부팅 자동 시작 설정

라즈베리파이를 켜면 자동으로 포토부스가 시작되도록 설정합니다.

### 방법 A: 데스크톱 자동 시작 (권장)

```bash
mkdir -p ~/.config/autostart

cat > ~/.config/autostart/photobooth.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=뚝딱 포토부스
Exec=/home/pi/photobooth/run.sh
X-GNOME-Autostart-enabled=true
EOF
```

> ⚠️ 경로를 실제 폴더 위치로 수정하세요:
> `/home/pi/photobooth/run.sh` → `~/photobooth/run.sh` 등

### 방법 B: crontab (X 데스크톱 없이 사용 시)

```bash
crontab -e
```

맨 아래에 추가:

```
@reboot DISPLAY=:0 /home/pi/photobooth/run.sh >> /home/pi/photobooth/photobooth.log 2>&1
```

### 재부팅 테스트

```bash
sudo reboot
```

---

## 9. 문제 해결

### ❌ "한국어가 깨져 보여요 / 네모로 나와요"

```bash
# 나눔 폰트 설치
sudo apt install -y fonts-nanum fonts-nanum-extra

# 폰트 캐시 갱신
fc-cache -fv

# 폰트 경로 확인
fc-list | grep -i nanum
```

출력 예시:
```
/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf: NanumGothic:style=Bold
```

이 경로가 `config.py`의 `FONT_CANDIDATES` 목록에 있는지 확인.

---

### ❌ "카메라 화면이 검정이에요"

```bash
# 카메라 연결 확인
ls /dev/video*

# 카메라 영상 테스트 (qv4l2 또는 ffplay)
ffplay /dev/video0

# 권한 확인
sudo usermod -aG video $USER
```

로그아웃 후 다시 로그인.

---

### ❌ "인쇄가 안 돼요"

```bash
# 프린터 상태 확인
lpstat -p Canon_CP1500

# CUPS 에러 로그 확인
sudo tail -50 /var/log/cups/error_log

# 수동 테스트 인쇄
lp -d Canon_CP1500 photos/세션ID.jpg
```

---

### ❌ "QR 스캔 후 연결이 안 돼요"

```bash
# Pi IP 확인
hostname -I

# 8080 포트 리스닝 확인
ss -tlnp | grep 8080

# 다른 기기에서 접속 테스트
curl http://[PI_IP]:8080/photo/test
```

스마트폰과 Pi가 **같은 공유기/WiFi**에 연결되어 있는지 확인.

---

### ❌ "앱이 갑자기 꺼져요"

```bash
# 로그 확인
tail -100 ~/photobooth/photobooth.log
```

---

### ❌ "pygame 창이 안 열려요 (display 오류)"

```bash
# 디스플레이 환경변수 설정 후 실행
DISPLAY=:0 python3 main.py

# X 서버 실행 여부 확인
echo $DISPLAY
```

---

## 📝 자주 쓰는 명령어 요약

```bash
# 앱 실행
./run.sh

# 테스트 모드 (카메라 없이)
./run.sh --test

# 창 모드 (디버그)
./run.sh --window

# 로그 실시간 보기
tail -f photobooth.log

# 찍힌 사진 확인
ls -lt photos/

# 프린터 상태
lpstat -p

# IP 주소 확인
hostname -I
```

---

*뚝딱 공방 포토부스 · Raspberry Pi 5 + Logitech C920e + Canon SELPHY CP1500*
