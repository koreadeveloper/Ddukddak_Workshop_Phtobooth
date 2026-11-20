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
- (선택) AI 변신 네컷: 콘셉트 선택 → 동의 → 4컷 촬영 → 클라우드 AI(Gemini) 변환 → 합성/인쇄. 실패한 컷은 원본으로 대체

## 빠른 실행

```bash
chmod +x install.sh run.sh
./install.sh
./run.sh --test --window
./run.sh
```

`.env.example`에는 기본 실행 설정이 들어 있습니다. `./install.sh`는 기존 `.env`를 덮어쓰지 않고 누락된 새 설정만 자동으로 추가합니다. 공백이 들어가는 문구 값은 따옴표로 감싸면 안전하고, 프로그램은 `.env`를 직접 읽으므로 `python3 doctor.py` 같은 점검 명령도 같은 설정을 사용합니다.

## AI 변신 네컷 (선택)

기본값은 꺼짐입니다. Gemini API 키(결제가 연결된 유료 티어 프로젝트)를 `.env`에 넣고 켭니다. 키는 화면·로그에 표시되지 않으며 `.env`는 커밋하지 마세요.

```bash
PHOTOBOOTH_AI_ENABLED=1
PHOTOBOOTH_AI_PROVIDER=gemini
PHOTOBOOTH_AI_API_KEY=발급받은키
```

```bash
python3 doctor.py            # AI 설정/키 존재/콘셉트/서버 연결 확인 (비용 없음)
python3 doctor.py --ai-live  # 실제 변환 1장 테스트 (비용 발생)
./run.sh --test --window --ai-mock   # API 없이 AI 화면 흐름 테스트
./run.sh --no-ai                     # AI를 끄고 일반 네컷만
```

자세한 절차는 [RASPBERRY_PI5_SETUP.md](RASPBERRY_PI5_SETUP.md)의 'AI 변신 네컷 설정', 기능 명세는 [docs/AI_TRANSFORM_PLAN.md](docs/AI_TRANSFORM_PLAN.md)를 보세요.

라즈베리파이 설치, 프린터 등록, 자동 실행 설정은 [RASPBERRY_PI5_SETUP.md](RASPBERRY_PI5_SETUP.md)를 보세요.
오늘 개발한 내용과 다음 개발자를 위한 코드 구조/운영 인수인계는 [DEVELOPMENT_HANDOFF.md](DEVELOPMENT_HANDOFF.md)를 보세요.
