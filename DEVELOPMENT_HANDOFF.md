# 개발 인수인계 문서

작성일: 2026-05-29  
대상 저장소: `koreadeveloper/Ddukddak_Workshop_Phtobooth`  
현재 기준 커밋: `7ddfe53 Simplify FHD booth start screen`

## 1. 프로젝트 목적

이 프로젝트는 Raspberry Pi 5에서 실행하는 인생네컷 스타일 포토부스 프로그램이다. Logitech USB 웹캠으로 촬영하고, Canon SELPHY CP1500 포토프린터로 RP-108 Postcard Size 용지 한 장에 네 컷을 출력하는 것이 목표다.

현재 목표 하드웨어는 다음과 같다.

- Raspberry Pi 5
- Logitech USB 웹캠
- Canon SELPHY CP1500
- Canon RP-108 Postcard Size 100 x 148 mm / 4 x 6 in / 4R 용지
- ElecSync S15A 15.6인치 FHD 1920x1080 모니터
- 터치스크린 또는 마우스 입력

## 2. 오늘 개발한 주요 내용

### CP1500 프린터 출력

- 기본 프린터 이름을 `CP1500`으로 맞췄다.
- 프로그램의 `인쇄하기` 버튼을 누르면 최종 합성된 JPG 파일을 CUPS로 전달한다.
- 라즈베리파이에서 `lp -d CP1500 파일이름.jpg`가 성공한 상태를 기준으로 프로그램 출력도 연결된다.
- 관련 파일:
  - `printer.py`
  - `config.py`
  - `main.py`

### RP-108 4x6 출력 규격

- 출력 이미지는 RP-108 4x6 기준 `1181 x 1748 px @ 300dpi`로 생성한다.
- 한 JPG 파일에는 네 컷만 들어가도록 수정했다.
- 기존처럼 한 장 안에 8컷처럼 보이는 구조는 제거했다.
- 출력 레이아웃은 `2x2` 고정이다.
- 관련 파일:
  - `composer.py`
  - `main.py`

### FHD 모니터용 첫 화면 UI 정리

ElecSync S15A 1920x1080 모니터 기준으로 첫 화면을 단순화했다.

- 첫 화면에서 촬영 방향, 프레임, 필터, 출력 레이아웃 선택 UI를 제거했다.
- 첫 화면에는 큰 제목, 카메라 미리보기, `촬영 시작`, `운영 점검`만 남겼다.
- 제목은 `오늘의 네컷` / `뚝딱이 창의공작소 포토부스`로 설정했다.
- `지금 모습이에요` 문구를 제거했다.
- 카메라 미리보기는 최종 출력에 실제로 들어가는 중앙 크롭 영역만 보여준다.
- 촬영 중 카운트다운 화면도 최종 출력 크롭 화면만 보여준다.
- 관련 파일:
  - `main.py`
  - `config.py`
  - `.env.example`

### 리뷰 화면 기능

처음 화면에서는 설정을 없앴지만, 촬영 후 리뷰 화면에서는 다음을 바꿀 수 있다.

- 촬영 방향: `세로`, `가로`
- 프레임: 핑크, 화이트, 블랙, 스카이
- 필터: 원본, 화사, 따뜻, 청량, 흑백
- 인쇄 매수
- 특정 컷 재촬영

출력 레이아웃은 리뷰 화면에서도 선택하지 못한다. 항상 `2x2` 고정이다.

### QR 다운로드와 운영 안정성

오늘 이전 작업까지 포함해 현재 프로그램에는 다음 기능도 들어가 있다.

- QR 코드로 같은 Wi-Fi의 스마트폰에서 최종 사진 다운로드
- 저장 공간 부족 방지를 위한 오래된 사진 정리
- 운영 통계 저장
- 카메라 프레임 끊김 감지 및 재연결
- `운영 점검` 화면에서 카메라, 프린터, QR 서버, 저장공간 상태 확인

관련 파일:

- `qr_share.py`
- `booth_stats.py`
- `doctor.py`
- `camera.py`
- `main.py`

## 3. 현재 라즈베리파이에서 확인해야 할 것

라즈베리파이에서 예전 UI가 뜨면 최신 코드가 실행 중이 아니다. 최신 첫 화면에는 `촬영 방향`, `출력 레이아웃`, `프레임`, `필터`, `지금 모습이에요`가 나오면 안 된다.

라즈베리파이에서 현재 코드 버전을 확인한다.

```bash
cd ~/photobooth
git log -1 --oneline
```

정상 최신이면 아래처럼 떠야 한다.

```bash
7ddfe53 Simplify FHD booth start screen
```

`a272d2f`처럼 더 오래된 커밋이 뜨면 업데이트가 안 된 것이다.

## 4. 라즈베리파이 업데이트 명령어

일반 업데이트:

```bash
cd ~/photobooth
git fetch origin
git stash push -u -m "before update"
git switch main
git reset --hard origin/main
chmod +x install.sh run.sh
./install.sh
```

실행:

```bash
pkill -f main.py
./run.sh --window --no-audio
```

전체화면 실행:

```bash
pkill -f main.py
./run.sh --fullscreen --no-audio
```

## 5. 권장 `.env` 설정

현재 웹캠은 물리적으로 일반 가로 방향으로 설치되어 있다. 그래서 카메라를 세로로 돌려 처리하면 화면이 옆으로 돌아가 보일 수 있다. 현재 운영 권장값은 가로 촬영 후 중앙 크롭이다.

라즈베리파이의 `~/photobooth/.env`에 아래 값을 권장한다.

```bash
PHOTOBOOTH_BRAND_NAME="뚝딱이 창의공작소 포토부스"
PHOTOBOOTH_EVENT_TITLE="오늘의 네컷"
PHOTOBOOTH_BOOTH_SUBTITLE="뚝딱이 창의공작소 포토부스"
PHOTOBOOTH_FOOTER_TEXT="뚝딱이 창의공작소 포토부스"
PHOTOBOOTH_PRINT_BRAND_TEXT="뚝딱이 창의공작소"
PHOTOBOOTH_PRINTER_NAME=CP1500
PHOTOBOOTH_CAPTURE_ORIENTATION=landscape
PHOTOBOOTH_PREVIEW_MIRROR=1
PHOTOBOOTH_DEFAULT_FILTER=bright
PHOTOBOOTH_DEFAULT_FRAME_THEME=soft_pink
PHOTOBOOTH_DEFAULT_PRINT_COPIES=1
PHOTOBOOTH_MAX_PRINT_COPIES=3
PHOTOBOOTH_REVIEW_TIMEOUT=120
```

`.env`를 한 번에 수정하려면:

```bash
cd ~/photobooth

grep -q '^PHOTOBOOTH_CAPTURE_ORIENTATION=' .env \
  && sed -i 's/^PHOTOBOOTH_CAPTURE_ORIENTATION=.*/PHOTOBOOTH_CAPTURE_ORIENTATION=landscape/' .env \
  || echo 'PHOTOBOOTH_CAPTURE_ORIENTATION=landscape' >> .env

grep -q '^PHOTOBOOTH_PRINTER_NAME=' .env \
  && sed -i 's/^PHOTOBOOTH_PRINTER_NAME=.*/PHOTOBOOTH_PRINTER_NAME=CP1500/' .env \
  || echo 'PHOTOBOOTH_PRINTER_NAME=CP1500' >> .env
```

예전 설정에서 아래 값이 남아 있으면 삭제해도 된다. 최신 코드에서는 쓰지 않는다.

```bash
PHOTOBOOTH_DEFAULT_PRINT_LAYOUT
PHOTOBOOTH_SHOW_CROP_GUIDE
```

삭제 명령어:

```bash
sed -i '/^PHOTOBOOTH_DEFAULT_PRINT_LAYOUT=/d;/^PHOTOBOOTH_SHOW_CROP_GUIDE=/d' .env
```

## 6. 다음 개발자가 먼저 봐야 할 파일

### `main.py`

프로그램의 메인 UI, 상태 전환, 촬영 흐름, 리뷰 화면, 인쇄 버튼 처리가 들어 있다.

중요한 부분:

- `PhotoBooth.__init__`: 버튼 배치와 초기 상태
- `_draw_idle`: 첫 화면 UI
- `_draw_countdown`: 촬영 카운트다운 화면
- `_draw_review`: 촬영 후 리뷰 화면
- `_capture_photo`: 실제 프레임 저장
- `_start_print`: 최종 JPG 인쇄 요청
- `_preview_crop_frame`: 화면 미리보기 중앙 크롭

UI를 바꾸려면 먼저 이 파일을 봐야 한다.

### `composer.py`

최종 인쇄용 4x6 JPG를 만드는 파일이다.

중요한 부분:

- `PRINT_W = 1181`
- `PRINT_H = 1748`
- `_slot_layout`: 2x2 슬롯 위치 계산
- `photo_slot_aspect`: 미리보기 크롭 비율 계산
- `compose_print_image`: 최종 JPG 저장

출력물에 사진이 잘리거나 4x6 비율이 맞지 않으면 이 파일을 확인해야 한다.

### `printer.py`

CUPS 프린터와 연결되는 파일이다.

중요한 부분:

- `print_file`: `lp` 명령으로 최종 JPG 출력
- `printer_status`: 프린터 등록/상태 확인

CP1500 프린터 문제가 있으면 먼저 터미널에서 아래를 확인한다.

```bash
lpstat -p
lp -d CP1500 photos/파일이름.jpg
```

터미널 출력이 되는데 프로그램 출력이 안 되면 `printer.py`와 `main.py`의 인쇄 흐름을 확인한다.

### `camera.py`

웹캠 연결, 프레임 읽기, 재연결 로직이 들어 있다.

중요한 부분:

- `Camera._open_capture`: OpenCV 카메라 열기
- `Camera._loop`: 프레임 읽기 루프
- `Camera.health`: 운영 점검용 상태

카메라가 멈추거나 검은 화면이면 이 파일과 USB 전원/케이블을 같이 확인해야 한다.

### `config.py`와 `.env`

기본 설정과 라즈베리파이 운영 설정을 관리한다.

주의할 점:

- `config.py` 기본값보다 `.env` 값이 우선이다.
- `install.sh`는 기존 `.env`를 보존할 수 있으므로, 코드 업데이트 후에도 예전 `.env` 때문에 화면이나 방향 설정이 그대로일 수 있다.
- UI가 이상하면 코드보다 먼저 `.env`를 확인한다.

### `RASPBERRY_PI5_SETUP.md`

라즈베리파이에 설치, 실행, 자동 실행, 프린터 확인 방법이 정리된 운영 문서다. 실제 현장 설치 담당자는 이 문서를 먼저 보면 된다.

## 7. 테스트 체크리스트

코드를 수정한 뒤 로컬 또는 라즈베리파이에서 최소 아래 검사를 한다.

```bash
python -m py_compile main.py composer.py effects.py camera.py qr_share.py printer.py config.py doctor.py booth_stats.py
bash -n install.sh
bash -n run.sh
```

합성 이미지 규격 테스트:

```bash
python - <<'PY'
import uuid
import numpy as np
from PIL import Image
import composer

photos = [np.zeros((720, 1280, 3), dtype=np.uint8) for _ in range(4)]
out = composer.compose_print_image(photos, 'handoff_test_' + uuid.uuid4().hex[:8], 'classic_white', 'grid')
try:
    with Image.open(out) as im:
        assert im.size == (composer.PRINT_W, composer.PRINT_H), im.size
    print('compose ok', out)
finally:
    out.unlink(missing_ok=True)
PY
```

현장 테스트 순서:

1. `git log -1 --oneline`으로 최신 커밋 확인
2. `./run.sh --window --no-audio`로 창 모드 실행
3. 첫 화면에서 설정 버튼들이 사라졌는지 확인
4. 카메라가 옆으로 돌아가지 않는지 확인
5. 네 컷 촬영 후 JPG가 `photos/`에 저장되는지 확인
6. 리뷰 화면에서 프레임/필터/촬영 방향 변경 후 자동 재합성되는지 확인
7. `인쇄하기` 버튼으로 CP1500 출력 확인
8. QR 다운로드 확인

## 8. 알려진 주의사항

- 라즈베리파이에 예전 코드가 남아 있으면 화면이 예전 UI로 보인다. 이때는 `git reset --hard origin/main`으로 맞춘다.
- `.env`는 코드 업데이트로 자동 초기화되지 않을 수 있다. 특히 카메라 방향은 `.env`를 확인해야 한다.
- 웹캠을 물리적으로 세로로 돌리지 않는 한 `PHOTOBOOTH_CAPTURE_ORIENTATION=landscape`가 더 안정적이다.
- CP1500은 CUPS에서 `CP1500` 이름으로 등록되어 있어야 한다.
- 실제 인쇄 전에 `lp -d CP1500 테스트파일.jpg`가 성공해야 프로그램 출력도 안정적이다.
- 포토프린터는 전력, 케이블, CUPS 큐 상태 문제의 영향을 많이 받는다. 출력 실패 시 `lpstat -p`, `lpq -P CP1500`, `cancel -a CP1500`을 확인한다.
- 현장 운영 전에는 반드시 실제 웹캠, 실제 모니터, 실제 프린터로 전체 흐름을 한 번 이상 테스트해야 한다.

## 9. 다음 개선 후보

- 첫 화면 디자인을 실제 행사 브랜딩에 맞춰 더 다듬기
- 리뷰 화면의 프레임/필터 버튼을 더 크게 조정해 터치 편의성 높이기
- 가로 촬영 후 2x2 크롭 결과가 얼굴 중심에 더 잘 맞도록 크롭 위치 조정 옵션 추가
- 인쇄 실패 시 사용자가 이해하기 쉬운 오류 메시지 강화
- 운영자 전용 설정 화면 추가
- 행사 종료 후 사진/통계 백업 기능 추가

