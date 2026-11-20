# 뚝딱 포토부스

Raspberry Pi 5, Logitech USB 웹캠, Canon SELPHY CP1500 조합으로 동작하는 4컷 포토부스 프로그램입니다.

주요 기능:

- 터치스크린 또는 마우스로 촬영 시작
- `.env`에서 부스명, 행사명, 출력물 브랜드 문구 변경
- 시작 화면은 `오늘의 네컷` / `뚝딱이 창의공작소 포토부스` 큰 제목, 실제 출력 크롭 미리보기, 촬영 시작 버튼만 표시
- 촬영 화면에는 실제 인쇄에 남는 중앙 크롭 영상만 표시
- 1024x768 같은 낮은 실제 화면 해상도에서는 UI를 자동으로 재배치해 축소 흐림과 버튼 겹침 방지
- 웹캠 프레임 실패 시 자동 재연결 및 촬영 보류
- 4x6 한 장 안에 2x2 네컷 레이아웃 고정
- 기본 블랙 프레임, 프레임 4종과 필터 5종 선택
- 촬영 후 리뷰 화면에서 촬영 방향, 프레임, 필터 재선택 및 자동 재합성
- 리뷰 화면에서 원하는 컷만 선택해 개별 재촬영
- 리뷰 화면에서 인쇄 매수 최대 10장 선택, 대기 시 자동 초기화
- 프린터 미등록/비활성 상태에서는 인쇄 전 안내 표시
- CUPS 인쇄 작업 ID 추적 및 대기열 상태 확인
- 저장공간 보호를 위한 사진 보관일/최대 개수 자동 정리
- 오늘/전체 촬영, 인쇄, QR 다운로드 운영 통계 저장
- 운영 점검 화면에서 카메라/프린터/QR/저장공간 확인
- 3초 카운트다운 후 4컷 촬영
- RP-108 Postcard Size 100 x 148 mm 한 장에 4컷 합성 및 JPEG 저장
- CUPS를 통한 CP1500 엽서 출력
- QR 코드 모바일 페이지에서 미리보기 및 JPG 다운로드, QR 화면 이후 사진 확인 화면으로 복귀
- (선택) AI 변신 네컷: 콘셉트 선택 → 동의 → 4컷 촬영 → MonoRouter를 통한 AI 변환 → 합성/인쇄. 실패한 컷은 원본으로 대체
- (선택) AI 증명사진: 한 명씩 촬영하고 원하는 복장으로 변환해 34×49mm 사진을 인쇄

## 빠른 실행

```bash
chmod +x install.sh run.sh
./install.sh
./run.sh --test --window
./run.sh
```

`.env.example`에는 기본 실행 설정이 들어 있습니다. `./install.sh`는 기존 `.env`를 덮어쓰지 않고 누락된 새 설정만 자동으로 추가합니다. 공백이 들어가는 문구 값은 따옴표로 감싸면 안전하고, 프로그램은 `.env`를 직접 읽으므로 `python3 doctor.py` 같은 점검 명령도 같은 설정을 사용합니다.

## AI 변신 네컷 (선택)

기본값은 꺼짐입니다. GPT Image 2.6을 사용할 수 있는 [MonoRouter Universal API 키](https://monogpt.kr/support/kr/articles/monorouter-api-key)를 발급해 `.env`에 넣고 켭니다. 기존 설정에서 전환하는 경우 아래 값으로 바꾸세요.

```bash
PHOTOBOOTH_AI_ENABLED=1
PHOTOBOOTH_AI_PROVIDER=monorouter
PHOTOBOOTH_AI_MONOROUTER_API_KEY=발급받은키
```

```bash
python3 doctor.py            # AI 설정/콘셉트 및 MonoRouter 인증 확인
python3 doctor.py --ai-live  # 실제 변환 1장 테스트 (비용 발생)
./run.sh --test --window --ai-mock   # API 없이 AI 화면 흐름 테스트
./run.sh --no-ai                     # AI를 끄고 일반 네컷만
```

자세한 절차는 [RASPBERRY_PI5_SETUP.md](RASPBERRY_PI5_SETUP.md)의 'AI 변신 네컷 설정', 기능 명세는 [docs/AI_TRANSFORM_PLAN.md](docs/AI_TRANSFORM_PLAN.md)를 보세요.

## 박람회용 AI 증명사진

AI를 켠 상태에서 시작 화면의 증명사진 모드를 선택합니다. 얼굴이 한 개만 검출되면 사진을 AI로 편집해 선택한 복장과 밝은 단색 배경의 사진을 만듭니다.

- **한 사람**은 사진 한 컷을 촬영해 같은 사진 4장을 출력합니다.
- **여러 사람**은 2~4명을 선택해 한 명씩 동의·촬영합니다. 사람마다 한 컷씩 배치하고 빈 칸은 그대로 둡니다.

Canon SELPHY CP1500의 RP-108 엽서(절취 후 100×148mm)에 가로 34mm × 세로 49mm 사진을 2×2로 배치합니다. 인쇄에는 300dpi PDF와 배율을 유지하는 CUPS 옵션을 사용합니다. 행사 전에 실물 한 장을 출력해 재단 표시 사이를 자로 확인하세요.

인쇄 미리보기에서 각 칸을 누르면 이번 촬영과 저장된 최근 증명사진을 선택할 수 있습니다. 편집된 개별 사진과 인화지는 `photos/`에 저장되며 사진 보관 설정에 따라 정리됩니다.

라즈베리파이 설치, 프린터 등록, 자동 실행 설정은 [RASPBERRY_PI5_SETUP.md](RASPBERRY_PI5_SETUP.md)를 보세요.
오늘 개발한 내용과 다음 개발자를 위한 코드 구조/운영 인수인계는 [DEVELOPMENT_HANDOFF.md](DEVELOPMENT_HANDOFF.md)를 보세요.
