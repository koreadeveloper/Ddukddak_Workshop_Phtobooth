# AI 변신 네컷 구현 노트 (명세 대비 차이)

기준 명세: `docs/AI_TRANSFORM_PLAN.md`
기준 코드: Phase 0~4 (PR #1 ~ #5)
작성일: 2026-11-20

이 문서는 명세에서 **아직 구현하지 않은 부분**과 **명세와 다르게 구현한 부분**을 기록한다.
다음 작업자는 명세보다 이 문서를 먼저 확인할 것.

## 1. 명세에서 구현하지 않은 부분

| # | 명세 위치 | 내용 | 현재 상태 / 비고 |
|---|---|---|---|
| 1 | 6.8 | `PHOTOBOOTH_AI_SAVE_SOURCES=1`일 때 `photos/ai/{sid}/{i}_src.jpg`, `{i}_ai.jpg` 저장 | 저장 기능 없음. `photos/ai/` 하위 폴더 보관일 정리(`_cleanup_ai_dirs`)만 구현 |
| 2 | 4.4 | 콘셉트 썸네일 `assets/ai_concepts/{id}.jpg` 제작/커밋 | 없음. 카테고리 색상 카드 + 이름으로 대체 렌더링. 실제 API PoC 후 제작 필요 |
| 3 | 9장 Phase 0 | `docs/AI_POC_RESULT.md` (1/2/4인 품질, p50/p95, 거절 여부, 비용) | 없음. 실제 Gemini 키로 `tools/ai_poc.py` 실행 결과 필요 |
| 4 | 5.3 | FLASH 화면 콘셉트 배지 | COUNTDOWN 화면에만 배지 표시 |
| 5 | 8장 | 재변환 횟수 초과 시 버튼 딤 | 버튼은 그대로, 누르면 "다시 변환은 한 번 촬영에 N번까지" 안내만 |
| 6 | 4.3 | 컷 간 통일감을 위한 `seed` 고정 | Interactions API 요청에 seed를 넣지 않음 |
| 7 | 5.3 | 버튼/배지 이모지 (`✨`, `🔥`) | 한글 폰트에 글리프가 없을 수 있어 제외 |
| 8 | 3.2, 6.2 | 2순위 Provider(FLUX Kontext 등), `RelayProvider` | Provider 추상화(`AIProvider`, `get_provider`)만 있음. `gemini`, `mock`만 지원 |
| 9 | 1.2 (선택) | QR 페이지에서 개별 컷 다운로드 | 미구현 |
| 10 | 6.9 (선택) | 콘셉트별 인기 통계 화면 표시 | `booth_stats`에 저장(`increment_concept`)만 하고 화면 표시 없음 |
| 11 | 5.2 | "동의 안 함" 시 일반 네컷 제안 | IDLE로 복귀만 함 (명세가 허용한 선택지) |
| 12 | 명세 미정의 | AI 모드 리뷰에서 "다시 찍기"(4컷 전체 재촬영) | 현재는 일반 모드로 돌아감. 같은 콘셉트 재촬영 / 콘셉트 선택 화면 이동 중 결정 필요 |
| 13 | 7장 4 | Provider 데이터 정책(유료 티어, 보관 기간)을 동의 문구에 반영 | 기본 동의 문구만 있음. 운영자가 확인 후 `PHOTOBOOTH_AI_CONSENT_TEXT`로 수정 |

## 2. 명세와 다르게 구현한 부분

### 2.1 AI 백엔드

- **Gemini 호출 방식**: 명세 작성 시점의 `generateContent` 대신 Interactions API(`POST /v1beta/interactions`, `Api-Revision: 2026-05-20`)를 사용한다. 요청에 `store: false`를 넣어 서버 저장을 끈다. 기본 모델은 `gemini-nano-banana-2.1`. 공식 문서 변경에 따른 것.
- **재시도 위치**: Provider 내부가 아니라 공통 함수 `ai_transform.transform_with_retry()` 한 곳에서 1회 재시도한다. GeminiProvider는 `transform()`당 HTTP 1회만 보낸다. PoC와 `AITransformManager`가 같은 함수를 쓴다.
- **`AIResult` 필드 추가**: `retryable: bool` (429/5xx/네트워크/시간 초과).
- **안전정책 거절 판정**: 공식 문서에 거절 응답 형식이 없어 오류 문구 키워드(`safety`, `blocked`, `policy` 등)로 추정한다. 실제 API 결과로 보정 필요.
- **health_check**: Gemini는 `GET /models/{model}` (이미지 생성 없음). `ai_online`은 첫 확인 전 `False`라서 시작 직후 몇 초간 AI 버튼이 회색이다. 확인 주기는 코드 상수 `AI_HEALTH_INTERVAL_SECS = 60`.

### 2.2 화면/흐름

- **IDLE**: `MODE_SELECT` 상태 없이 IDLE 버튼 2개(명세 권장안). `AI_ENABLED=0`이면 AI 버튼을 숨기고 기존 화면/터치 동작 그대로(명세는 숨김 또는 비활성 중 선택).
- **AI 사용 불가 판정**: `ai_manager` 있음 + 콘셉트 1개 이상 + `ai_online`. 이 중 하나라도 아니면 회색 버튼 + "AI 서버 연결 확인 중".
- **A 키**: Phase 2 임시 기능(첫 콘셉트로 바로 촬영)은 콘셉트 선택 화면 열기로 바뀜.
- **AI_PROCESSING 슬롯**: 1x4 배치 (명세는 2x2 또는 1x4).
- **다시 변환**: 리뷰에서 바로 재합성하지 않고 AI_PROCESSING(1컷)을 거쳐 REVIEW로 돌아간다. 그래서 "재변환 중 인쇄/QR 딤"은 해당 화면이 없어 불필요.
- **실패 안내 문구**: "이 컷은 AI 변환이 어려워 원본으로 넣었어요" 대신 "N번 컷은 원본으로 넣었어요." (compact 화면에서 버튼과 겹치지 않게 축약). 거절/실패/시간 초과 구분 없이 같은 문구.
- **전체 시간 초과 후 늦은 결과**: 버림 (명세 권장안).
- **콘셉트 기본 프레임/필터**: 해당 세션에만 적용하고 세션이 끝나면 이전 값으로 복원.
- **AI 모드 방향 버튼**: FHD는 숨기고 그 자리에 콘셉트명·남은 재변환 횟수 표시. AI 버튼 2개는 FHD에서 "선택 컷 다시 찍기" 아래(700,744 / 700,824), compact에서는 방향 버튼 자리 재사용.

### 2.3 인쇄물

- 하단 라벨 구분자는 기존과 같은 `"  ·  "`(양옆 공백 2칸): `{브랜드}  ·  {print_label}  ·  {날짜}`.
- "AI 생성 이미지" 표기는 `label_text`와 별도 인자 `ai_disclosure`로 넘긴다. AI 컷이 1장 이상 쓰였고 `PHOTOBOOTH_AI_PRINT_DISCLOSURE=1`일 때만 라벨/표기가 붙는다. 4컷 모두 원본이면 일반 라벨.

### 2.4 통계/운영 점검

- 시간 초과도 `ai_failure`에 센다. 거절은 `ai_failure`와 `ai_refused`를 둘 다 올린다.
- STATUS "오늘 운영" 줄의 `AI 성공/요청`은 `AI_ENABLED=1`일 때만 표시. AI 행은 항상 표시(꺼져 있으면 "꺼짐").
- `doctor.py`: `AI_ENABLED=0`이고 `--ai-live`가 없으면 AI 점검 생략. `--ai-live`는 `samples/`의 첫 JPG(없으면 합성 이미지)로 1장 변환 후 `poc_out/doctor_ai_live.jpg` 저장.

### 2.5 명세 범위를 넘어 바꾼 것 (일반 모드에도 영향)

- **입력 처리**: 상태 전환 직후 250ms 입력 무시(명세 8장)에 더해, 터치로 합성된 마우스 이벤트(`event.touch`)를 무시해 1회 터치가 2번 처리되지 않게 했다. FINGERDOWN 좌표는 `event.x * display_w` → `_event_pos_to_ui` (명세 1.8-3 수정).
- **1280x720 리뷰 레이아웃**: 기존 `+`/`-` 버튼과 인쇄/QR 버튼 겹침 경고를 없애려고 compact 리뷰 오른쪽 카드 세로 간격을 비율(`v`)로 축소. 1024x768과 FHD는 변화 없음.
- **STATUS 레이아웃**: 행 간격을 행 수와 닫기 버튼 위치로 계산하고, 상세 문구를 ` · ` 단위로 줄바꿈. compact 좌표 별도.
- **`_audit_button_layout`**: 버튼이 화면 밖으로 나가는지도 검사.
- **문서**: AI 설정 절차를 명세의 Phase 5가 아니라 Phase 4에 README / DEVELOPMENT_HANDOFF / RASPBERRY_PI5_SETUP에 추가(요청에 따름).
- **촬영 방향**: `.env.example`만 `landscape`로 변경. `config.py` 기본값은 `portrait` 그대로라 `.env`에 해당 줄이 없는 기기는 세로 촬영.

## 3. 알려진 기존 문제 (이번 작업에서 고치지 않음)

- 1280x720에서 인쇄 매수 `+` 글자가 버튼보다 커서 표시되지 않음.
- compact 리뷰에서 "선택 컷 다시 찍기" 버튼이 세로 썸네일 3·4번을 가림.
- `run.sh`/`install.sh`가 git에 실행 권한 없이(644) 커밋되어 있어 `./run.sh` 대신 `bash run.sh` 필요.

## 4. 현장에서 확인해야 할 항목 (명세 11장)

- `python3 doctor.py`, `python3 doctor.py --ai-live`
- 1/2/4인 실제 변환 → CP1500 인쇄 품질
- Wi-Fi 끊김 시 AI 버튼 비활성 / 변환 중 끊김 시 원본 대체
- QR 다운로드가 AI 합성본인지 (스마트폰)
- 운영 점검 화면 AI 통계 증가
- 30분 연속 운영 메모리
