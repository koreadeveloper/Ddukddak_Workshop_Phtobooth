# Raspberry Pi 5 실행 가이드

대상 구성:

- Raspberry Pi 5, Raspberry Pi OS 64-bit Desktop
- Logitech USB 웹캠, 예: C920/C920e
- Canon SELPHY CP1500 포토 프린터, USB 연결 권장
- 터치스크린 또는 HDMI 모니터 + 마우스

## 1. Pi 준비

Raspberry Pi OS는 Desktop 버전을 사용하세요. Lite 버전은 pygame 전체화면을 띄울 데스크톱 세션이 없어 추가 설정이 필요합니다.

```bash
sudo apt update
sudo apt install -y git
```

저장소를 Pi에 받습니다.

```bash
cd ~
git clone https://github.com/youbin999/Ddukddak_Workshop_Phtobooth.git photobooth
cd ~/photobooth
chmod +x install.sh run.sh
./install.sh
```

설치 후 `video`, `lpadmin` 그룹 권한이 적용되도록 한 번 재부팅하세요.

```bash
sudo reboot
```

## 2. 기본 점검

재부팅 후 Pi 데스크톱 터미널에서 실행합니다.

```bash
cd ~/photobooth
./run.sh --test --window
```

카메라 없이 화면 흐름만 테스트합니다. 창이 뜨고 화면 클릭/터치 후 4컷 촬영 흐름이 진행되면 UI 기본 동작은 정상입니다.

전체 환경 점검은 다음 명령으로 확인합니다.

```bash
source venv/bin/activate
python3 doctor.py
```

## 3. 웹캠 설정

웹캠을 USB에 연결한 뒤 장치 번호를 확인합니다.

```bash
v4l2-ctl --list-devices
```

예를 들어 `/dev/video0`가 실제 카메라라면 기본 설정 그대로 실행합니다.

```bash
./run.sh
```

다른 번호라면 실행 옵션이나 `.env`로 지정합니다.

```bash
./run.sh --camera-index 1
```

또는 `.env`:

```bash
PHOTOBOOTH_CAM_INDEX=1
```

장치 경로를 직접 고정할 수도 있습니다.

```bash
PHOTOBOOTH_CAM_DEVICE=/dev/video0
```

## 4. CP1500 프린터 설정

CUPS를 켭니다.

```bash
sudo systemctl enable --now cups
```

CP1500을 USB로 연결하고 URI를 확인합니다.

```bash
lpinfo -v
```

`usb://Canon/...` 형태의 URI가 보이면 프린터를 등록합니다. 아래 URI는 예시이므로 실제 출력값으로 바꾸세요.

```bash
sudo lpadmin -p Canon_CP1500 \
  -E \
  -v "usb://Canon/CP1500?serial=XXXXXXXX&interface=1" \
  -m gutenprint.5.3://canonselphycp1500/expert

lpstat -p Canon_CP1500
```

등록된 이름이 `Canon_CP1500`이 아니면 `.env` 또는 실행 옵션으로 맞춥니다.

```bash
PHOTOBOOTH_PRINTER_NAME=실제_프린터_이름
```

수동 테스트:

```bash
lp -d Canon_CP1500 -o media=Postcard -o fit-to-page photos/테스트파일.jpg
```

## 5. 실제 실행

```bash
cd ~/photobooth
./run.sh
```

자주 쓰는 옵션:

```bash
./run.sh --test                 # 카메라 없이 UI/촬영 흐름 테스트
./run.sh --window               # 창 모드
./run.sh --camera-index 1       # 카메라 번호 지정
./run.sh --capture-orientation portrait  # 세로 촬영/저장
./run.sh --capture-orientation landscape # 가로 촬영/저장
./run.sh --portrait-rotation counterclockwise # 세로 방향이 반대일 때
./run.sh --printer Canon_CP1500 # CUPS 프린터 이름 지정
./run.sh --no-audio             # 오디오 장치 문제 시 무음 실행
./run.sh --hide-cursor          # 터치 전용 부스에서 커서 숨김
```

기본 출력은 CP1500 RP-108 Postcard Size `100 x 148 mm` 기준 `1181 x 1748 px @ 300dpi`입니다. 한 파일에는 네 컷만 들어갑니다.

세로 촬영이 기본입니다. 화면에서 `세로 촬영` / `가로 촬영` 버튼을 눌러 세션 시작 전에 바꿀 수 있습니다. 세로 사진이 거꾸로 저장되면 `.env`에서 아래 값을 바꾸거나 실행 옵션을 사용하세요.

```bash
PHOTOBOOTH_PORTRAIT_ROTATION=counterclockwise
```

## 6. 부팅 자동 실행

`Exec` 경로는 실제 저장소 위치에 맞추세요.

```bash
mkdir -p ~/.config/autostart
nano ~/.config/autostart/photobooth.desktop
```

내용:

```ini
[Desktop Entry]
Type=Application
Name=Ddukddak Photobooth
Exec=/home/pi/photobooth/run.sh
Terminal=false
X-GNOME-Autostart-enabled=true
```

저장 후 재부팅합니다.

```bash
sudo reboot
```

## 7. 운영 주의사항

- Pi 5는 정품 5V 5A 전원과 방열판/쿨러를 권장합니다. 전원 부족이면 웹캠, 프린터, USB가 불안정해질 수 있습니다.
- CP1500은 USB 연결이 가장 단순합니다. Wi-Fi 프린팅은 네트워크 상태에 따라 지연/실패 원인이 늘어납니다.
- QR 다운로드는 Pi와 스마트폰이 같은 Wi-Fi에 있어야 합니다. 행사장 공유기에서 기기 간 통신 차단(AP isolation)이 켜져 있으면 QR 접속이 실패합니다.
- 최신 Raspberry Pi OS는 Wayland 기반입니다. 이 프로그램은 `SDL_VIDEODRIVER`를 강제하지 않고 자동 선택하게 두었습니다. 화면이 뜨지 않을 때만 `.env`에 `PHOTOBOOTH_SDL_VIDEODRIVER=x11` 또는 `wayland`를 지정해 테스트하세요.
- 오디오 장치가 없어도 앱은 무음으로 실행됩니다. 문제가 계속되면 `.env`에 `PHOTOBOOTH_AUDIO_ENABLED=0`을 넣으세요.
- `photos/` 폴더에 출력 JPEG가 계속 쌓입니다. 장기 운영 전에는 저장 공간을 확인하세요.

## 8. 문제 해결 명령

```bash
tail -f photobooth.log
v4l2-ctl --list-devices
lpstat -p
lpinfo -v
hostname -I
ss -tlnp | grep 8080
```
