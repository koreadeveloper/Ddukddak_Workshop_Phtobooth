# Raspberry Pi 5 설치 안내

**설치 환경 기준** · Raspberry Pi 5, Raspberry Pi OS 64-bit Desktop(Bookworm), Logitech C920 USB 웹캠, USB 연결 Canon SELPHY CP1500. 이전에 사용하던 Pi를 다시 설치하거나 새 기기에 옮길 때 아래 순서대로 진행하세요.

[프로젝트 소개](README.md) · [촬영 및 인쇄 화면](docs/SCREEN_GUIDE.md)

## 1. 장비 연결

| 장치 | 연결 방법 |
| --- | --- |
| 모니터 | Pi의 micro-HDMI 포트에서 모니터 HDMI로 연결합니다. 모니터의 입력을 해당 HDMI로 바꿉니다. |
| 웹캠 | Pi의 USB 포트에 연결하고 화면을 보는 사람의 눈높이에 맞춥니다. |
| 마우스 | Pi의 USB 포트에 연결합니다. 홈, 촬영, 인쇄를 마우스로 조작합니다. |
| 키보드 | Pi의 USB 포트에 연결합니다. 터미널과 초기 설정에 사용하고, 설치 후에는 분리해도 됩니다. |
| Canon SELPHY CP1500 | 프린터의 USB-C 포트에서 Pi의 USB 데이터 포트로 연결합니다. Pi 쪽이 USB-A라면 USB-A ↔ USB-C **데이터 케이블**을 사용합니다. 프린터는 자체 전원 어댑터에 연결합니다. |
| 용지와 잉크 | RP-108 엽서 용지 카세트와 함께 제공되는 잉크 카트리지를 장착합니다. |

[Canon CP1500 연결 사양](https://downloads.canon.com/selphy/SELPHY-CP1500-Spec-Sheet.pdf)

## 2. Raspberry Pi OS 준비

1. 이미 Raspberry Pi OS Desktop이 설치되어 있다면 데스크톱에 로그인하고 다음 단계로 이동합니다.
2. 새 microSD라면 맥북에서 Raspberry Pi Imager를 실행하고 기기로 **Raspberry Pi 5**, 운영체제로 **Raspberry Pi OS 64-bit Desktop**을 고릅니다. 현장 설치를 그대로 재현하려면 OS 선택 목록에서 **Bookworm 기반 64-bit Desktop** 이미지를 선택합니다. Imager가 선택한 microSD를 초기화하니 저장 장치를 확인하세요.
3. Imager에서 사용자 이름과 비밀번호, Wi-Fi, 시간대(한국에서는 `Asia/Seoul`)를 설정합니다. 비밀번호는 이후 `sudo` 명령에도 사용합니다.
4. microSD를 Pi에 꽂고 부팅합니다. 데스크톱에 로그인해 화면 상단의 네트워크 메뉴에서 Wi-Fi 연결을 확인합니다. 설치부터 운영까지 Pi에 연결된 모니터에서 진행할 수 있습니다.

[Raspberry Pi Imager 설치 안내](https://www.raspberrypi.com/documentation/computers/getting-started.html) · [Raspberry Pi OS 이미지](https://www.raspberrypi.com/software/operating-systems/)

## 3. GitHub에서 프로그램 설치

이미 `~/photobooth`에 설치되어 있다면 [업데이트](#업데이트)로 이동하세요. 처음 설치하는 경우 Pi 데스크톱에서 터미널을 열고 아래를 순서대로 실행합니다.

```bash
sudo apt update
sudo apt install -y git
cd ~
git clone https://github.com/koreadeveloper/Ddukddak_Workshop_Phtobooth.git photobooth
cd ~/photobooth
bash install.sh
sudo reboot
```

설치 스크립트가 OpenCV, pygame, CUPS, Gutenprint와 Python 가상환경을 준비하고 `.env`를 만듭니다. 재부팅한 뒤 **같은 사용자**로 로그인하세요.

## 4. MonoRouter와 마우스 설정

[MonoRouter 키 발급 안내](https://monogpt.kr/support/kr/articles/monorouter-api-key)에서 GPT Image 2.6을 사용할 수 있는 **Universal API 키**를 준비합니다. Pi 터미널에서 설정 파일을 엽니다.

```bash
cd ~/photobooth
nano .env
```

아래 항목을 찾아 값을 바꾸고, `발급받은_키`에는 본인의 키를 입력하세요. Nano에서 `Ctrl+O` → `Enter`로 저장하고 `Ctrl+X`로 나옵니다.

```dotenv
PHOTOBOOTH_AI_ENABLED=1
PHOTOBOOTH_AI_PROVIDER=monorouter
PHOTOBOOTH_AI_MODEL=gpt-image-2.6
PHOTOBOOTH_AI_MONOROUTER_API_KEY=발급받은_키
PHOTOBOOTH_FULLSCREEN=1
PHOTOBOOTH_MOUSE_VISIBLE=1
PHOTOBOOTH_PRINTER_NAME=CP1500
```

API 키는 Pi의 `.env`에 보관하세요. 프로그램은 인터넷을 통해 MonoRouter에 합성을 요청합니다. Wi-Fi와 남은 크레딧이 있어야 두 촬영 모드를 사용할 수 있습니다.

## 5. CP1500을 USB 프린터로 등록

프린터 전원을 켜고 케이블과 용지를 확인한 뒤 아래 명령을 실행합니다. `lpstat -v CP1500`에 `usb://Canon/SELPHY...`가 나오고 기존 대기열로 출력이 된다면 등록을 건너뛰세요.

```bash
sudo systemctl enable --now cups
lpstat -v CP1500
sudo /usr/sbin/lpinfo -v | grep -i selphy
sudo /usr/sbin/lpinfo -m | grep -Ei 'cp1500|cp1300'
```

새로 등록할 때는 출력된 **USB 장치 주소**와 **드라이버 ID**를 사용합니다. 아래 명령은 CP1500 전용 드라이버를 먼저 찾고, Bookworm에서 실제 사용 중인 CP1300 호환 드라이버로 대체합니다. 연결된 CP1500 한 대를 기준으로 합니다.

```bash
(
  set -e
  URI=$(sudo /usr/sbin/lpinfo -v | awk 'tolower($2) ~ /^usb:\/\/canon\/selphy.*cp1500/ {print $2; exit}')
  DRIVERS=$(sudo /usr/sbin/lpinfo -m)
  DRIVER=$(printf '%s\n' "$DRIVERS" | awk 'tolower($0) ~ /canon selphy cp1500/ {print $1; exit}')
  if [ -z "$DRIVER" ]; then
    DRIVER=$(printf '%s\n' "$DRIVERS" | awk 'tolower($0) ~ /canon selphy cp1300/ {print $1; exit}')
  fi
  if [ -z "$URI" ] || [ -z "$DRIVER" ]; then
    echo "CP1500 USB 주소 또는 Gutenprint 드라이버를 찾지 못했습니다."
    exit 1
  fi
  sudo /usr/sbin/lpadmin -p CP1500 -E -v "$URI" -m "$DRIVER"
  lpstat -v CP1500
  lpstat -p CP1500
)
```

`lpstat`에서 `CP1500` 대기열이 보이면 다음 단계로 진행합니다. 드라이버를 못 찾을 때는 `sudo apt install printer-driver-gutenprint`를 실행하고 프린터를 다시 켠 뒤 확인하세요.

## 6. 카메라와 실행 점검

```bash
cd ~/photobooth
v4l2-ctl --list-devices
venv/bin/python doctor.py
```

점검 결과에서 카메라 프레임 읽기, `CP1500` 대기열, QR 포트, 저장 공간, MonoRouter 인증이 정상인지 확인합니다. 웹캠이 여러 개라면 목록에서 사용할 장치를 골라 `.env`의 `PHOTOBOOTH_CAM_DEVICE=/dev/video0` 값을 실제 장치로 설정합니다.

```bash
./run.sh --test --window --ai-mock --no-audio  # 예시 카메라와 모의 AI로 화면 확인
./run.sh                                     # 테스트 창을 닫은 뒤 실제 운영 앱 실행
```

첫 줄 실행 후 `Esc`로 테스트 창을 닫고 둘째 줄을 실행합니다. 실제 운영 앱에서는 다음을 한 번씩 확인하세요.

- **AI 변신 네컷** · 복장 선택 → 네 컷 촬영 → 미리보기 → CP1500에서 종이 한 장 출력.
- **키링용 AI 증명사진** · 한 명 촬영 → 사진 칸 선택 → 한 장 출력. 재단 표시 사이가 **34 × 49 mm**인지 자로 측정합니다.
- **QR 저장** · 휴대폰을 같은 로컬 Wi-Fi에 연결하고 QR을 스캔해 JPG 다운로드를 확인합니다.

RP-108 엽서 기준 출력물은 절취 후 **100 × 148 mm**입니다. 키링 모드는 배율을 유지하기 위해 300dpi PDF로 CUPS에 전송합니다. CUPS에서 접수됐다는 표시가 나오면 프린터에서 종이가 실제로 나왔는지도 확인하세요.

## 7. 바탕화면 바로가기 만들기

동일한 Pi 사용자로 아래 명령을 한 번 실행합니다. 그 뒤 바탕화면의 **포토부스 실행** 아이콘을 열면 됩니다. 데스크톱에서 실행 확인을 묻는 경우 **실행**을 누르세요. 아래 `flock` 설정은 바로가기를 여러 번 눌러도 중복 실행을 막습니다.

```bash
DESKTOP_DIR="$(xdg-user-dir DESKTOP)"
mkdir -p "$DESKTOP_DIR" "$HOME/.local/state/photobooth"

cat > "$DESKTOP_DIR/포토부스 실행.desktop" <<EOF_DESKTOP
[Desktop Entry]
Type=Application
Name=포토부스 실행
Comment=뚝딱이 창의공작소 포토부스
Exec=/usr/bin/flock -n "$HOME/.local/state/photobooth/run.lock" "$HOME/photobooth/run.sh"
Path=$HOME/photobooth
Icon=camera-photo
Terminal=false
EOF_DESKTOP

chmod +x "$DESKTOP_DIR/포토부스 실행.desktop"
```

매번 클릭해서 시작하려면 여기까지면 됩니다. 전체화면 앱은 `Esc`로 닫을 수 있습니다. 마우스만으로 창을 닫는 운영이 필요하면 바로가기의 `Exec=` 줄 끝에 `--window`를 붙이세요. 데스크톱의 시스템 종료는 촬영과 인쇄가 모두 끝난 뒤 사용하세요. Pi 데스크톱 로그인과 함께 앱을 띄우려면 아래를 실행합니다.

```bash
mkdir -p ~/.config/autostart
cp "$(xdg-user-dir DESKTOP)/포토부스 실행.desktop" ~/.config/autostart/photobooth.desktop
```

자동 실행을 등록했다면 한 번 재로그인해 카메라 미리보기까지 뜨는지 확인합니다.

## 현장에 맞게 조정하기

| 조정할 항목 | 설정 |
| --- | --- |
| 행사명과 부스명 | `.env`의 `PHOTOBOOTH_EVENT_TITLE`, `PHOTOBOOTH_BOOTH_SUBTITLE`을 수정합니다. 공백이 들어간 값은 큰따옴표로 감쌉니다. |
| 웹캠 방향 | `PHOTOBOOTH_CAPTURE_ORIENTATION`을 `landscape` 또는 `portrait`로 설정합니다. 세로가 거꾸로 보이면 `PHOTOBOOTH_PORTRAIT_ROTATION`을 `clockwise` 또는 `counterclockwise`로 바꿉니다. |
| 사진 보관 | 인화 파일과 키링 선택용 사진은 `photos/`에 저장됩니다. `PHOTOBOOTH_PHOTO_RETENTION_DAYS`, `PHOTOBOOTH_MAX_STORED_PHOTOS`, `PHOTOBOOTH_MIN_FREE_GB`로 정리 기준을 정합니다. |
| AI 이미지 생성 점검 | `venv/bin/python doctor.py --ai-live`는 실제 변환 한 장을 요청하며 크레딧을 사용합니다. |

설정을 저장한 뒤 앱을 다시 실행합니다. 전체 항목은 [설정 예시](.env.example)를 참고하세요.

## 문제 해결

| 증상 | 확인할 것 |
| --- | --- |
| 시작 화면에서 AI 버튼이 회색 | `venv/bin/python doctor.py`로 인터넷, 모델 ID, 키 인증을 확인하고 MonoRouter에서 남은 크레딧을 확인합니다. |
| 카메라가 안 보임 | 케이블을 다시 연결하고 `v4l2-ctl --list-devices`로 장치 번호를 확인합니다. 웹캠을 사용하는 다른 앱이 있으면 종료합니다. |
| 프린터가 연결됐는데 출력되지 않음 | 프린터 자체 전원, USB 데이터 케이블, 용지·잉크를 확인합니다. `lpstat -v CP1500`, `lpstat -p CP1500`, `lpstat -o CP1500`으로 주소, 활성 상태, 작업을 확인합니다. |
| 키링 크기가 다름 | 키링 모드에서 출력했는지, RP-108을 사용했는지 확인하고 재단선 사이를 다시 측정합니다. |
| QR 페이지가 열리지 않음 | 휴대폰이 Pi와 같은 로컬 Wi-Fi에 있고, 게스트망의 기기 간 통신 차단이 없는지 확인합니다. QR의 기본 접속 포트는 `8080`입니다. |
| 화면이 늦거나 장시간 사용 후 느려짐 | `vcgencmd measure_temp`와 `vcgencmd get_throttled`로 온도를 확인하고 케이스 통풍을 점검합니다. |
| 원인을 확인하고 싶음 | `cd ~/photobooth && tail -n 80 photobooth.log`를 실행합니다. 시작 화면의 **운영 점검**에서도 상태를 볼 수 있습니다. |

## 업데이트

촬영과 인쇄가 끝난 뒤 앱을 닫고 업데이트하세요. 현장에서 수정한 소스 파일이 있으면 `git status --short`로 확인하고 먼저 백업하세요.

```bash
cd ~/photobooth
git status --short
git pull --ff-only
./run.sh
```

기존 `.env`와 `photos/`는 Pi에 보관됩니다. 새 Pi로 옮길 때는 MonoRouter 키를 발급하거나 기존 키를 안전하게 옮기고, 유지해야 할 촬영 파일이 있다면 별도로 백업하세요.
