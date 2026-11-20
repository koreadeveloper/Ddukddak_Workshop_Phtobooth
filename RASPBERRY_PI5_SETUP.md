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
git clone https://github.com/koreadeveloper/Ddukddak_Workshop_Phtobooth.git photobooth
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

CP1500을 USB로 연결하고 URI와 등록 가능한 드라이버를 확인합니다.

```bash
lpinfo -v
lpinfo -m | grep -i cp1500
```

기존 `CP1500` 대기열이 동작하면 그대로 사용합니다. 새로 등록할 때는 CP1500용으로 표시된 실제 URI와 드라이버 ID를 사용하세요. 목록에 없으면 [CP1500을 지원하는 Gutenprint 릴리스](https://sourceforge.net/p/gimp-print/mailman/gimp-print-announce/thread/Z9HwZen6WtZ6waXx%40shaftnet.org/)를 확인하세요.

```bash
sudo lpadmin -p CP1500 \
  -E \
  -v 'lpinfo -v에 나온 CP1500 URI' \
  -m 'lpinfo -m에 나온 CP1500 드라이버 ID'

lpstat -p CP1500
```

이 프로그램의 기본 프린터 이름은 `CP1500`입니다. 이미 `lp -d CP1500 파일이름.jpg`로 출력에 성공했다면 프린터 등록은 끝난 상태입니다.

기존 설치에서 `.env`에 예전 이름이 남아 있으면 아래처럼 바꿉니다.

```bash
cd ~/photobooth
sed -i 's/^PHOTOBOOTH_PRINTER_NAME=.*/PHOTOBOOTH_PRINTER_NAME=CP1500/' .env
```

수동 테스트:

```bash
lp -d CP1500 -o media=Postcard -o fit-to-page photos/테스트파일.jpg
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
./run.sh --printer CP1500       # CUPS 프린터 이름 지정
./run.sh --no-audio             # 오디오 장치 문제 시 무음 실행
./run.sh --hide-cursor          # 터치 전용 부스에서 커서 숨김
```

기본 출력은 CP1500 RP-108 Postcard Size `100 x 148 mm` 기준 `1181 x 1748 px @ 300dpi`입니다. 한 파일에는 네 컷만 들어갑니다.

`.env.example` 기본값은 가로 촬영 후 중앙 크롭(`PHOTOBOOTH_CAPTURE_ORIENTATION=landscape`)입니다. 시작 화면은 `오늘의 네컷` / `뚝딱이 창의공작소 포토부스` 큰 제목, 실제 출력에 들어가는 중앙 크롭 미리보기, `촬영 시작` 버튼만 보여줍니다. 촬영 후 리뷰 화면에서 촬영 방향, 프레임, 필터를 다시 고르면 최종 출력 파일이 자동으로 다시 합성됩니다. 세로 사진이 거꾸로 저장되면 `.env`에서 아래 값을 바꾸거나 실행 옵션을 사용하세요.

라즈베리파이가 모니터를 `1024 x 768`로 인식해도 `PHOTOBOOTH_DISPLAY_AUTO_SIZE=1`이면 프로그램이 실제 화면 크기에 맞춰 UI를 다시 그립니다. 글자와 버튼이 작거나 흐리면 `.env`에서 이 값이 `1`인지 먼저 확인하세요.

```bash
PHOTOBOOTH_PORTRAIT_ROTATION=counterclockwise
```

기본 프레임/필터도 `.env`에서 바꿀 수 있습니다.

```bash
PHOTOBOOTH_BRAND_NAME="뚝딱이 창의공작소 포토부스"
PHOTOBOOTH_EVENT_TITLE="오늘의 네컷"
PHOTOBOOTH_BOOTH_SUBTITLE="뚝딱이 창의공작소 포토부스"
PHOTOBOOTH_FOOTER_TEXT="뚝딱이 창의공작소 포토부스"
PHOTOBOOTH_PRINT_BRAND_TEXT="뚝딱이 창의공작소"
PHOTOBOOTH_PRINT_MARK_TEXT="FOUR CUT"
PHOTOBOOTH_DOWNLOAD_PREFIX=photobooth
PHOTOBOOTH_DEFAULT_FRAME_THEME=studio_black  # soft_pink/classic_white/studio_black/sky_blue
PHOTOBOOTH_DEFAULT_FILTER=bright          # original/bright/warm/cool/mono
PHOTOBOOTH_CAM_STALE_SECS=3
PHOTOBOOTH_CAM_RECONNECT_SECS=2
PHOTOBOOTH_CAM_MAX_READ_FAILURES=30
PHOTOBOOTH_DEFAULT_PRINT_COPIES=1
PHOTOBOOTH_MAX_PRINT_COPIES=10
PHOTOBOOTH_REVIEW_TIMEOUT=120
PHOTOBOOTH_PRINT_RESULT_TIMEOUT=5
PHOTOBOOTH_PRINT_JOB_WAIT_SECS=120
PHOTOBOOTH_PRINT_JOB_POLL_SECS=2
PHOTOBOOTH_PHOTO_RETENTION_DAYS=14
PHOTOBOOTH_MAX_STORED_PHOTOS=800
PHOTOBOOTH_MIN_FREE_GB=1
PHOTOBOOTH_CLEANUP_INTERVAL_SECS=3600
```

행사명이나 기관명을 바꾸려면 위 브랜딩 값을 수정하세요. 공백이 들어가는 값은 위 예시처럼 따옴표로 감싸는 것이 안전합니다. `PHOTOBOOTH_PRINT_BRAND_TEXT`는 인쇄물 하단 문구, `PHOTOBOOTH_DOWNLOAD_PREFIX`는 QR 다운로드 파일명 앞부분에 사용됩니다.

시작 화면과 촬영 화면의 카메라 영상은 최종 네컷 합성에 실제로 들어갈 중앙 크롭만 보여줍니다. 화면에 보이지 않는 바깥 영역은 인쇄물에도 들어가지 않으므로 얼굴과 손동작은 보이는 화면 안쪽에 맞추세요.

웹캠 프레임이 끊기면 프로그램은 자동 재연결을 시도하고, 새 프레임이 들어오기 전에는 촬영을 완료하지 않습니다. 행사 중 USB 허브나 전원 문제로 화면이 멈추는 경우 `운영 점검`에서 카메라 재연결 횟수를 확인하세요.

출력 레이아웃은 `2x2`로 고정입니다. 시작 화면과 리뷰 화면에서 `auto`나 `4단`을 선택할 수 없고, RP-108 4x6 파일 하나에는 네 컷만 들어갑니다.

리뷰 화면에서는 인쇄 매수를 `1~PHOTOBOOTH_MAX_PRINT_COPIES` 범위에서 바꿀 수 있습니다. 기본 최대값은 10장입니다. 손님이 리뷰 화면을 오래 방치하면 `PHOTOBOOTH_REVIEW_TIMEOUT`초 뒤 대기 화면으로 자동 복귀합니다. 시작 화면의 `운영 점검`에서는 카메라 프레임, CUPS 프린터 등록, QR 서버 주소, 저장공간을 확인할 수 있습니다.

QR 코드는 JPG 파일을 바로 여는 대신 모바일 저장 페이지를 엽니다. 스마트폰에서 미리보기를 확인하고 `사진 다운로드` 버튼을 누르면 최종 네컷 JPG가 저장됩니다. 일부 iPhone/Android 브라우저에서 다운로드 버튼이 바로 저장되지 않으면 사진을 길게 눌러 저장하세요.

운영 통계는 `booth_stats.json`에 저장됩니다. 시작 화면의 `운영 점검`에서 오늘 촬영 횟수, 인쇄 성공/실패, QR 다운로드 횟수와 전체 촬영 횟수를 확인할 수 있습니다.

리뷰 화면의 촬영 컷 썸네일을 누르면 해당 컷이 선택됩니다. `선택 컷 다시 찍기`를 누르면 4장을 전부 다시 찍지 않고 선택한 컷만 새로 촬영한 뒤 최종 출력물을 다시 합성합니다. `전체 다시 찍기`는 현재 세션을 버리고 1번 컷부터 다시 시작합니다.

프린터가 CUPS에 등록되어 있지 않거나 비활성 상태이면 인쇄 버튼을 눌러도 출력 화면으로 넘어가지 않고 리뷰 화면에 안내가 표시됩니다. 프린터 문제를 해결한 뒤 다시 `인쇄하기`를 누르세요.

인쇄가 실패하면 사진 세션을 버리지 않고 리뷰 화면으로 돌아옵니다. 프린터를 다시 확인한 뒤 `인쇄하기`를 다시 누르거나, 손님에게 QR 다운로드를 제공할 수 있습니다. 인쇄 성공 시에는 결과 화면을 `PHOTOBOOTH_PRINT_RESULT_TIMEOUT`초 보여 준 뒤 대기 화면으로 돌아갑니다.

인쇄 요청 후에는 CUPS 작업 ID를 추적해 `PHOTOBOOTH_PRINT_JOB_WAIT_SECS`초까지 대기열을 확인합니다. CP1500 출력이 오래 걸려 타임아웃이 나더라도 중복 출력 방지를 위해 요청은 성공으로 처리합니다. 프린터가 중간에 비활성화되거나 대기열을 받을 수 없는 상태가 되면 실패로 처리되어 리뷰 화면으로 돌아옵니다.

`photos/` 폴더는 장기 운영 중 자동 정리됩니다. 기본값은 14일 초과 사진(및 `photos/ai/` 하위 폴더) 삭제, 최대 800장 보관, 저장공간 여유 1GB 미만이면 오래된 사진부터 삭제입니다. 행사가 끝난 뒤 원본을 보관해야 한다면 `photos/` 폴더를 먼저 백업하세요.

## 6. AI 변신 네컷 설정 (선택)

AI 변신 네컷은 촬영한 사진을 MonoRouter로 보내 Nano Banana 2.1로 편집한 뒤 인쇄합니다. 기본값은 꺼짐이고, 인터넷 연결이 필요합니다. 변환에 실패한 네컷은 원본 사진으로 대체합니다.

1. [MonoRouter Universal API 키](https://monogpt.kr/support/kr/articles/monorouter-api-key)를 발급합니다. 키에 이미지 편집 모델 사용 권한을 부여하세요.
2. `.env` 설정 (`.env`는 커밋하지 마세요):

```bash
cd ~/photobooth
chmod 600 .env
nano .env
```

```bash
PHOTOBOOTH_AI_ENABLED=1
PHOTOBOOTH_AI_PROVIDER=monorouter
PHOTOBOOTH_AI_MONOROUTER_API_KEY=발급받은키
```

값 뒤에 `# 주석`을 붙이지 마세요. 나머지 `PHOTOBOOTH_AI_*` 값(시간 제한, 재변환 횟수, 동의 문구 등)은 `.env.example`의 기본값을 그대로 써도 됩니다.

3. 점검:

```bash
source venv/bin/activate
python3 doctor.py            # AI 설정, 콘셉트, MonoRouter 인증 확인
python3 doctor.py --ai-live  # 실제 변환 1장 테스트 (비용 발생, 결과는 poc_out/doctor_ai_live.jpg)
```

4. 화면 확인:

```bash
./run.sh --test --window --ai-mock   # API 없이 AI 화면 흐름만 테스트
./run.sh --window                    # 실제 API 사용
./run.sh --no-ai                     # 오늘은 AI 없이 일반 네컷만
```

AI가 켜져 있으면 시작 화면에서 일반 네컷, AI 변신 네컷, AI 증명사진을 선택할 수 있습니다. 프로그램은 60초마다 AI 서버 연결을 확인합니다(이미지 생성 없음, 비용 없음). 연결이 끊겼을 때는 일반 네컷으로 촬영할 수 있습니다. `운영 점검` 화면의 `AI` 행에서 Provider, 모델, 키 설정 여부, 마지막 연결 확인 결과, 오늘 AI 성공/실패 수를 볼 수 있습니다.

비용과 시간은 MonoRouter 사용 내역과 실제 변환에 걸린 시간으로 확인하세요. 인증 점검은 모델의 편집 권한과 크레딧 소진 여부까지 보장하지 않으므로 행사 전에 `doctor.py --ai-live`로 한 장을 확인합니다.

개인정보: 손님은 `동의하고 촬영`을 눌러야 AI 촬영을 시작합니다. 어린이 대상 행사라면 보호자 동의 방식을 주최 측과 미리 정하세요. 원본/AI 개별 컷은 기본적으로 저장하지 않고(`PHOTOBOOTH_AI_SAVE_SOURCES=0`), 최종 합성본만 기존 보관 정책을 따릅니다. 인쇄물 하단에는 `AI 생성 이미지`가 표시됩니다.

### 박람회용 AI 증명사진

한 사람이 화면 중앙에서 카메라를 정면으로 보도록 안내합니다. 여러 명을 선택했을 때는 참가자마다 동의하고 한 사람씩 촬영합니다. 얼굴 검출이나 AI 변환이 실패한 경우 해당 참가자의 컷을 다시 촬영할 수 있습니다.

프린터는 RP-108과 Postcard 용지를 사용합니다. AI 증명사진은 가로 34mm × 세로 49mm 규격으로 배치하고, CUPS에는 여백에 맞추는 확대·축소 없이 300dpi PDF로 보냅니다. 행사 전 실물에서 가로·세로 치수를 측정해 드라이버의 출력 배율을 확인하세요.

## 7. 기존 Pi에서 프로그램 업데이트

이미 `~/photobooth`에 설치해 둔 상태라면 실행 중인 포토부스를 닫고 업데이트합니다. `git status --short`에 파일 변경이 표시되면 내용을 먼저 확인하세요.

```bash
cd ~/photobooth
git status --short
git pull --ff-only origin main
bash install.sh
git log --oneline -1
```

`./install.sh`는 기존 `.env` 값을 덮어쓰지 않고, 새 버전에서 추가된 `PHOTOBOOTH_...` 설정만 `.env` 끝에 붙입니다. 실행 전 `.env.backup.날짜` 파일도 남기므로 현장 설정을 되돌릴 수 있습니다.

`git pull`에서 다른 파일 충돌이 나오면 그 파일도 로컬에서 바뀐 상태입니다. 직접 고친 내용이 아니라면 아래처럼 전체 백업 후 다시 받습니다.

```bash
git stash push -m "backup pi local changes"
git pull origin main
```

업데이트 후 세로 방향 테스트:

```bash
./run.sh --window --no-audio --capture-orientation portrait --portrait-rotation counterclockwise
```

방향이 반대이면 `counterclockwise`를 `clockwise`로 바꿔 실행합니다.

## 8. 부팅 자동 실행

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

## 9. 운영 주의사항

- Pi 5는 정품 5V 5A 전원과 방열판/쿨러를 권장합니다. 전원 부족이면 웹캠, 프린터, USB가 불안정해질 수 있습니다.
- CP1500은 USB 연결이 가장 단순합니다. Wi-Fi 프린팅은 네트워크 상태에 따라 지연/실패 원인이 늘어납니다.
- QR 다운로드는 Pi와 스마트폰이 같은 Wi-Fi에 있어야 합니다. 행사장 공유기에서 기기 간 통신 차단(AP isolation)이 켜져 있으면 QR 접속이 실패합니다.
- 최신 Raspberry Pi OS는 Wayland 기반입니다. 이 프로그램은 `SDL_VIDEODRIVER`를 강제하지 않고 자동 선택하게 두었습니다. 화면이 뜨지 않을 때만 `.env`에 `PHOTOBOOTH_SDL_VIDEODRIVER=x11` 또는 `wayland`를 지정해 테스트하세요.
- 오디오 장치가 없어도 앱은 무음으로 실행됩니다. 문제가 계속되면 `.env`에 `PHOTOBOOTH_AUDIO_ENABLED=0`을 넣으세요.
- 자동 정리는 오래된 `photos/*.jpg`와 `photos/ai/` 하위 폴더만 삭제합니다. 장기 보관이 필요한 사진은 행사 후 별도 저장장치로 옮기세요.

## 10. 문제 해결 명령

```bash
tail -f photobooth.log
v4l2-ctl --list-devices
lpstat -p
lpinfo -v
hostname -I
ss -tlnp | grep 8080
```
