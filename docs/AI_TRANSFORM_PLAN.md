# AI 변신 네컷 기획서 (구현 AI용 상세 명세)

작성일: 2026-11-20
기준 커밋: `54878ae Apply card review design` (main)
대상 독자: 이 문서를 받아 실제 코드를 구현할 다른 AI/개발자

> 이 문서는 **기획 + 구현 명세**다. 기존 코드 구조를 먼저 설명하고(1~2장),
> 무엇을 만들지(3~5장), 어떻게 넣을지(6~9장), 어떤 순서로 검증할지(10~12장)를 적었다.
> 구현자는 1~2장을 반드시 먼저 읽고, 기존 동작(일반 네컷)을 깨지 않는 것을 최우선으로 한다.

---

## 0. 한 줄 요약

기존 "인생네컷" 포토부스(Raspberry Pi 5 + 웹캠 + CP1500)에 **"AI 변신" 모드**를 추가한다.
사용자가 촬영 전에 콘셉트(정장 프로필, 소방관, 한복, 아이돌 등)를 고르면, 촬영한 4컷을
**클라우드 이미지 편집 AI**로 변환해 기존 2x2 RP-108 프레임에 합성하고 인쇄/QR 저장한다.
AI가 실패하면 **원본 사진으로 자동 대체**해서 부스는 절대 멈추지 않는다.

벤치마크: STICKY AI, 포토네모, 피키픽, 스냅박스, 치키, 캐럿, 무피 (정장·직업·한복·아이돌·스포츠 콘셉트 변환, 단체 변환, 현장 인화).

---

## 1. 현재 프로그램 분석 (As-Is)

### 1.1 하드웨어 / 런타임

| 항목 | 내용 |
|---|---|
| 본체 | Raspberry Pi 5 (Raspberry Pi OS, Wayland 기본) |
| 카메라 | Logitech USB 웹캠 (C920e 계열), OpenCV V4L2, MJPG 1280x720@30 |
| 프린터 | Canon SELPHY CP1500, CUPS 큐 이름 `CP1500`, RP-108 엽서 4x6 |
| 모니터 | ElecSync S15A 15.6" FHD 1920x1080 (현장에서 1024x768로 잡히는 경우도 있음) |
| 입력 | 마우스 또는 터치 |
| 네트워크 | Wi-Fi (현재는 QR 다운로드용 로컬 HTTP 서버에만 사용) |
| 언어/UI | Python 3, pygame (단일 프로세스, 메인 루프 + 보조 스레드) |
| 설치 | `install.sh` (apt 패키지 + venv `--system-site-packages`), 실행 `run.sh` |
| 자동 실행 | `~/.config/autostart/photobooth.desktop` (RASPBERRY_PI5_SETUP.md 7장) |
| GitHub | `koreadeveloper/Ddukddak_Workshop_Phtobooth` |

### 1.2 파일별 역할

| 파일 | 줄수 | 역할 | AI 작업 시 영향 |
|---|---|---|---|
| `main.py` | 1994 | pygame UI, 상태 머신, 촬영, 합성 스레드, 인쇄/QR 트리거 | **가장 많이 수정** |
| `config.py` | 179 | `.env` 파서 + 모든 설정 상수 | AI 설정 추가 |
| `camera.py` | 204 | `Camera`(V4L2 스레드, 재연결), `MockCamera` | 수정 없음 |
| `composer.py` | 222 | RP-108 1181x1748 2x2 시트 합성, 프레임 테마 4종 | 하단 라벨에 콘셉트명 추가 정도 |
| `effects.py` | 56 | 필터 5종 (original/bright/warm/cool/mono) | 수정 없음 (AI 결과 위에 적용) |
| `printer.py` | 196 | CUPS `lp`, 작업 추적, 프린터 이름 fallback | 수정 없음 |
| `qr_share.py` | 265 | 로컬 HTTP `/s/<sid>`, `/photo/<sid>.jpg`, `/download/<sid>.jpg` | 수정 없음 (선택: 개별 컷 다운로드) |
| `booth_stats.py` | 83 | `booth_stats.json` 카운터 | AI 카운터 추가 |
| `doctor.py` | 184 | 현장 점검 CLI | AI 점검 추가 |
| `install.sh` | 147 | 설치 | **Pi용 pip 설치 줄에 신규 패키지 추가 필수** |
| `run.sh` | 67 | `.env`의 `PHOTOBOOTH_*`만 export 후 실행 | 수정 없음 |
| `.env.example` | 42 | 설정 템플릿 (install.sh가 누락 키를 기존 .env에 append) | AI 키 추가 |

### 1.3 상태 머신 (main.py `class St`)

```
IDLE ──(아무 곳 터치/키)──▶ COUNTDOWN ──(3초)──▶ FLASH ──(1.2초)──┬─▶ COUNTDOWN (다음 컷, 4장까지)
  ▲  └─(운영 점검 버튼/S키)─▶ STATUS ─(돌아가기)─▶ IDLE            └─▶ REVIEW (4장 완료, _start_compose)
  │
  ├── REVIEW ──(인쇄하기)──▶ PRINTING ──성공──▶ IDLE(+reset)  / 실패 ──▶ REVIEW
  ├── REVIEW ──(QR 저장)───▶ QR_SHOW ──(터치/30초)──▶ REVIEW
  ├── REVIEW ──(다시 찍기)──▶ COUNTDOWN (새 세션)
  ├── REVIEW ──(선택 컷 다시 찍기)──▶ COUNTDOWN(retake_index) ──▶ FLASH ──▶ REVIEW
  └── REVIEW ──(120초 무입력)──▶ IDLE(+reset)
```

주의: IDLE에서는 `btn_status` 이외 **화면 어디를 눌러도** `_begin_countdown()`이 호출된다 (`_handle_click`). 시작 버튼만 누르는 구조가 아니다.

### 1.4 이미지 데이터 흐름 (중요)

세션 데이터 (`_reset_session`):

- `captured_source_bgr[i]` : 카메라 원본 프레임 (BGR, 1280x720, 미러 아님)
- `captured_raw_bgr[i]`    : `_oriented_frame()` 적용 (portrait면 90도 회전, landscape면 그대로)
- `captured_bgr[i]`        : `apply_filter(raw, selected_filter)` 결과 → **composer에 들어가는 것**

리뷰에서 방향 변경 → `_refresh_processed_photos()`가 source에서 raw를 다시 만들고 filter를 재적용 → `_start_compose()`.
리뷰에서 필터 변경 → raw에서 filter만 재적용 → 재합성.

미리보기/카운트다운 화면은 `_display_frame()`(방향+필터+미러) → `_preview_crop_frame()`(슬롯 비율 중앙 크롭)으로 **실제 인쇄 영역만** 보여준다.
composer는 각 컷을 슬롯 크기(2x2 기준 약 517x770)로 `_center_crop` 한다.

합성(`_start_compose`)은 데몬 스레드에서 돌고, `_compose_generation` 카운터로 오래된 결과를 버린다. `_composing` 동안 인쇄/QR/선택 재촬영 버튼은 딤 처리된다. **AI 작업도 이 generation 패턴을 그대로 따라야 한다.**

### 1.5 저장/공유

- 최종 JPG: `photos/{session_id}.jpg` (session_id = uuid hex 12자). 원본 컷은 디스크에 저장하지 않음 (메모리에만).
- `_cleanup_old_photos`: `photos/*.jpg` **최상위만** glob → 보관일(14일)/최대개수(800)/최소여유공간(1GB) 정리. 하위 폴더는 정리 대상 아님.
- QR: `qr_share.py`가 `photos/{sid}.jpg`를 같은 Wi-Fi에서만 서빙.

### 1.6 설정 로딩의 함정 (AI 키 관련 필수 숙지)

- `config._load_env_file`은 정규식 `^PHOTOBOOTH_[A-Z0-9_]+$`에 맞는 키만 읽는다.
- `run.sh`도 `PHOTOBOOTH_*=*`만 export한다.
- 따라서 API 키는 **반드시 `PHOTOBOOTH_` 접두사**를 써야 한다. 예: `PHOTOBOOTH_AI_API_KEY`. `GEMINI_API_KEY` 같은 이름으로 `.env`에 넣으면 읽히지 않는다. (SDK가 환경변수를 자동으로 읽는 기능에 기대지 말고 키를 코드에서 명시적으로 넘길 것.)
- `.env`는 `.gitignore`에 있음. `.env.example`에는 **빈 값**만 둔다.

### 1.7 설치 스크립트의 함정

`install.sh`는 Pi(`IS_RPI=1`)에서 `requirements.txt`를 **설치하지 않고** `pip install "qrcode[pil]>=7.4.2"`만 실행한다.
AI용 패키지(`requests` 또는 SDK)를 추가하면 **requirements.txt와 install.sh의 Pi 분기 둘 다** 고쳐야 한다.
권장: 외부 SDK 대신 `requests`(apt `python3-requests`로도 설치 가능)로 REST 직접 호출 → 의존성 최소화.

### 1.8 이미 발견한 기존 코드의 불일치/버그 (참고, AI 작업과 별개)

1. `config.py` 기본값 `CAPTURE_ORIENTATION="portrait"`, `.env.example`도 `portrait`인데, 인수인계 문서는 현장 권장값을 `landscape`라고 한다. → `.env.example`을 `landscape`로 맞추는 것을 권장.
2. `DEVELOPMENT_HANDOFF.md`는 `printer.print_file`/`printer_status`라고 쓰지만 실제 함수는 `print_photo`/`get_printer_status`.
3. `FINGERDOWN` 좌표 변환이 `int(event.x * SCREEN_W)`로 `ui_scale`/`ui_x` 오프셋을 무시한다. 디스플레이와 UI 해상도가 다르면 터치 위치가 어긋날 수 있다. (`event.x * display_w` 후 `_event_pos_to_ui` 적용이 맞음.) 또한 터치스크린은 FINGERDOWN과 MOUSEBUTTONDOWN을 **둘 다** 발생시키는 경우가 많아 한 번 터치에 클릭이 두 번 처리될 수 있다. AI 콘셉트 선택 화면에서 더블 처리되면 바로 촬영이 시작되는 문제가 생길 수 있으니 디바운스 필요.
4. `composer.resolve_layout("auto", landscape)`는 `stacked`를 반환하지만 main은 항상 `"grid"`를 넘기므로 실사용에는 영향 없음.
5. `main.py` 상단 docstring에 "어린이센터용"이라고 되어 있음 → **미성년자 얼굴 데이터를 외부 AI로 전송**하는 문제가 생긴다(7장 개인정보 참고).

---

## 2. 목표 (To-Be)

### 2.1 핵심 사용자 경험

1. 대기 화면에서 **[일반 네컷] / [AI 변신 네컷]** 중 선택.
2. AI 선택 시 콘셉트 카드 화면(예시 썸네일 포함)에서 콘셉트 1개 선택.
3. (최초 1회/세션마다) 사진이 AI 서버로 전송된다는 안내 + 동의.
4. 기존과 동일하게 4컷 촬영 (카운트다운 화면에 "소방관 변신 촬영 중" 같은 콘셉트 배지 표시).
5. **AI 변환 중 화면**: 4개 슬롯이 하나씩 변환 완료되며 채워짐, 진행률/남은 예상 시간 표시.
6. 리뷰 화면: AI 결과로 합성된 4x6 미리보기. 컷별 [원본 ↔ AI] 토글, [이 컷 다시 변환], 기존 프레임/필터/매수/선택 재촬영 유지.
7. 인쇄 / QR 저장은 기존과 동일.

### 2.2 비기능 목표

| 항목 | 목표 |
|---|---|
| 4컷 변환 총 시간 | 평균 30초 이내, 최대 90초 (타임아웃 후 원본 대체) |
| 실패 시 | 해당 컷만 원본으로 대체 + 리뷰 화면에 안내. 부스 정지 금지 |
| 네트워크 단절 | AI 버튼 비활성("AI 서버 연결 안 됨"), 일반 네컷은 정상 동작 |
| 일반 네컷 모드 | 기존 동작과 100% 동일 (회귀 금지) |
| 비용 | 세션당 대략 4회 이미지 편집 호출. 운영 점검 화면에서 오늘 AI 호출 수 확인 |
| 개발 편의 | `--test` + `PHOTOBOOTH_AI_PROVIDER=mock` 조합으로 API 없이 전체 흐름 테스트 가능 |

### 2.3 범위 밖 (이번 단계에서 하지 않음)

- Pi에서 로컬 디퓨전 모델 실행 (Pi 5 CPU로는 컷당 수 분 이상 → 부스 운영 불가)
- 결제/코인기 연동
- 외부 인터넷 공개 다운로드 서버 (QR은 계속 같은 Wi-Fi 한정)
- 영상(움짤) 생성

---

## 3. AI 엔진 선택

### 3.1 방식: 클라우드 "이미지 편집(image-to-image)" API

얼굴/인원/포즈는 유지하고 의상·배경만 바꾸는 작업이므로 **지시문 기반 이미지 편집 모델**이 적합하다.
텍스트→이미지 생성이나 단순 얼굴 합성(face swap)은 얼굴 일치도 또는 자연스러움이 떨어진다.

### 3.2 후보 (구현 전 반드시 최신 모델 ID·가격·정책을 공식 문서에서 재확인)

| 우선순위 | 후보 | 장점 | 단점/확인사항 |
|---|---|---|---|
| 1순위 | Google Gemini 이미지 편집 모델 ("Nano Banana" 계열, Gemini API) | 인물 동일성 유지가 강함, 빠름(수 초~십수 초), 저렴, REST 단순 | 인물/미성년자 편집 안전 정책 거절 가능성, 결과물 SynthID 워터마크 |
| 2순위 | FLUX Kontext 계열 (fal.ai 또는 Replicate 호스팅) | 지시문 편집 품질 좋음, 호스팅사 선택 폭 | 큐 대기 시간 변동, 비동기 폴링 API |
| 3순위 | OpenAI 이미지 편집 (gpt-image 계열) | 품질 높음 | 느린 편(수십 초), 얼굴이 미묘하게 바뀌는 경향 |

**결정: Provider 추상화 레이어를 만들고 1순위로 구현, 2순위를 예비로 붙일 수 있게 설계한다.**
코드에 모델 ID를 하드코딩하지 말고 `PHOTOBOOTH_AI_MODEL`로 바꿀 수 있게 한다.

### 3.3 대략 비용 감각 (반드시 최신 가격으로 다시 계산)

- 이미지 1장 편집 ≈ 수십 원 수준 (2026년 상반기 기준 Gemini Flash 이미지 편집 약 $0.04/장 내외).
- 세션당 4장 ≈ 200원 안팎, 하루 300세션 축제 ≈ 6~7만 원.
- 재변환 버튼 남용 방지: 세션당 재변환 횟수 제한 (`PHOTOBOOTH_AI_MAX_REGEN_PER_SESSION`, 기본 4).

### 3.4 아키텍처: Pi에서 직접 호출 (1단계)

```
[Pi: main.py] ──HTTPS──▶ [AI Provider API]
     │
     └─ API 키는 Pi의 .env (PHOTOBOOTH_AI_API_KEY)
```

- 장점: 서버 운영 불필요, 구현 단순.
- 단점: 키가 Pi에 저장됨 → 부스 장비 분실/유출 시 키 폐기. 사용량 상한(예산 알림)을 Provider 콘솔에서 반드시 설정.
- 2단계(선택): 여러 부스 운영 시 중계 서버(키 보관, 사용량 집계, 프롬프트 원격 갱신)로 전환. Provider 인터페이스만 맞추면 `RelayProvider`로 교체 가능하게 설계.

---

## 4. 콘셉트(테마) 설계

### 4.1 콘셉트 데이터 구조

`ai_concepts.py` (또는 `assets/ai_concepts.json` + 로더)에 정의. 코드 수정 없이 추가/비활성화할 수 있게 JSON 권장.

```python
{
  "id": "suit_profile",            # 영문 소문자/숫자/_ (파일명·통계 키로 사용)
  "name": "정장 프로필",            # 화면 표시명 (짧게, 6자 이내 권장)
  "category": "profile",           # profile | job | traditional | stage | sports | fun
  "description": "취업 사진처럼 깔끔한 정장",
  "thumbnail": "assets/ai_concepts/suit_profile.jpg",  # 콘셉트 카드 예시 이미지
  "prompt": "...",                 # 영어 프롬프트 (4.3 템플릿)
  "print_label": "AI 정장 프로필",  # 인쇄물 하단 라벨에 들어갈 문구
  "default_frame": "classic_white",# 선택 시 자동 적용할 프레임 테마 (없으면 현재 유지)
  "default_filter": "original",    # AI 결과에는 보통 original 권장
  "enabled": true
}
```

### 4.2 1차 콘셉트 목록 (8개 권장, 화면 2x4 그리드)

| id | 이름 | 카테고리 | 프롬프트 핵심 |
|---|---|---|---|
| `suit_profile` | 정장 프로필 | profile | 네이비/차콜 정장, 흰 셔츠, 스튜디오 회색 배경, 증명/프로필 조명 |
| `firefighter` | 소방관 | job | 방화복, 헬멧, 소방서 배경 |
| `police` | 경찰관 | job | 경찰 정복 (특정 국가 실제 휘장 복제 대신 일반적 디자인), 경찰서 배경 |
| `doctor` | 의사 | job | 흰 가운, 청진기, 병원 배경 |
| `pilot` | 파일럿 | job | 조종사 제복/모자, 공항·조종석 배경 |
| `hanbok` | 한복 | traditional | 전통 한복, 고궁 배경 |
| `idol_stage` | 아이돌 무대 | stage | 무대 의상, 조명·콘서트 무대 배경 |
| `baseball` | 야구선수 | sports | 야구 유니폼(가상 팀), 야구장 배경 |

추가 후보: 우주비행사, 셰프, 과학자, 졸업 가운, 판사, 축구선수, 시대극(조선/1980s 레트로).
**주의:** 실존 구단·기업 로고, 실제 아이돌 이름, 실존 인물 얼굴 합성은 금지 (초상권/상표 문제). 프롬프트에 "fictional team, no real logos" 명시.

### 4.3 프롬프트 템플릿 (모든 콘셉트 공통 규칙)

```
Edit this photo. Keep every person's face, identity, facial features, skin tone,
hairstyle, expression, head position, body pose and the exact number of people
unchanged. Do not add or remove people. Keep the same camera framing and composition.

Change only: {의상 설명} and {배경 설명}.

Style: {스타일 설명}, photorealistic, natural lighting, high detail, sharp focus on faces.
No text, no watermark, no logos, no real brand or team names.
```

- 인원수는 프롬프트에 숫자로 넣지 않는다 (사람 감지 기능이 없으므로). "every person / exact number of people unchanged"로 처리.
- 컷마다 결과가 너무 다르면 네 컷 통일감이 떨어진다 → 같은 프롬프트를 쓰고, 가능하면 `seed` 고정(Provider가 지원할 때만).
- 프롬프트는 영어, 화면 문구는 한국어.

### 4.4 콘셉트 썸네일 제작

- 각 콘셉트 카드에 들어갈 예시 이미지를 `assets/ai_concepts/{id}.jpg`로 준비 (정사각 또는 3:4, 400px 내외).
- 운영자 본인/동의받은 모델 사진으로 `tools/ai_poc.py`(10장)를 돌려 생성 → 저장소에 커밋.
- 썸네일 파일이 없으면 카테고리 색상 카드 + 이름 텍스트로 대체 렌더링 (없어도 크래시 금지).

---

## 5. 화면/흐름 상세 설계

### 5.1 새 상태 추가

```python
class St:
    IDLE      = "idle"
    MODE_SELECT    = "mode_select"     # (선택) 일반/AI 선택. IDLE에 버튼 2개로 합쳐도 됨
    CONCEPT_SELECT = "concept_select"  # 신규
    CONSENT        = "consent"         # 신규 (설정으로 끌 수 있음)
    COUNTDOWN = "countdown"
    FLASH     = "flash"
    AI_PROCESSING  = "ai_processing"   # 신규
    REVIEW    = "review"
    PRINTING  = "printing"
    QR_SHOW   = "qr_show"
    STATUS    = "status"
```

권장 단순안: `MODE_SELECT`를 별도 상태로 만들지 말고 **IDLE 화면의 시작 버튼을 2개**(`일반 네컷`, `AI 변신 네컷`)로 바꾼다.
그리고 IDLE에서 "아무 곳이나 터치하면 촬영 시작" 동작은 **AI 활성 시 제거**한다(버튼을 정확히 눌러야 함). AI 비활성 시엔 기존 동작 유지.

### 5.2 전체 흐름 (AI 모드)

```
IDLE
 ├─[일반 네컷]──────────────────────────────▶ COUNTDOWN … (기존 그대로)
 └─[AI 변신 네컷]─▶ CONCEPT_SELECT
                     ├─[뒤로]─▶ IDLE
                     └─[콘셉트 카드 터치]─▶ CONSENT (AI_REQUIRE_CONSENT=1일 때)
                                             ├─[동의 안 함]─▶ IDLE (또는 일반 네컷 제안)
                                             └─[동의하고 촬영]─▶ COUNTDOWN ×4 ─▶ FLASH
                                                                               │
                                     4장 완료 시 REVIEW 대신 ─────────────────▶ AI_PROCESSING
                                                                               │ (컷별 병렬 변환)
                                                                               ▼
                                                                             REVIEW (AI 결과 합성)
```

- `CONCEPT_SELECT`, `CONSENT`는 60초 무입력 시 IDLE 복귀 (`PHOTOBOOTH_AI_SELECT_TIMEOUT`).
- 1컷 촬영이 끝날 때마다 **즉시 해당 컷의 AI 변환을 백그라운드로 시작**한다 (파이프라이닝). 4번째 컷 촬영이 끝날 즈음엔 1~3번이 이미 끝나 있어 대기 시간이 크게 줄어든다. ← 핵심 최적화
- `AI_PROCESSING`은 4컷 모두 완료(성공/실패/타임아웃)되면 `_start_compose()` 호출 후 `REVIEW`로 이동.

### 5.3 화면별 레이아웃 (FHD 1920x1080 / compact 1024x768 둘 다)

기존 코드 규칙: `self.compact_ui = SCREEN_W <= 1280 or SCREEN_H <= 800`일 때 compact 좌표를 별도로 계산한다. 새 화면도 **두 레이아웃 모두** 만들고, `_audit_button_layout()`의 groups에 새 버튼들을 등록해 겹침 경고를 받는다.

**IDLE (AI 활성 시)**
- 제목/부제/카메라 미리보기 그대로.
- 하단 버튼 2개 나란히: `[일반 네컷]`(흰 바탕 핑크 테두리) `[AI 변신 네컷 ✨]`(핑크 채움, 더 강조). FHD: 각 440x92, compact: 각 300x64.
- AI 사용 불가 상태(키 없음/네트워크 끊김/`AI_ENABLED=0`)면 AI 버튼을 회색 처리 + 작은 문구 "AI 서버 연결 확인 중". 누르면 안내만.

**CONCEPT_SELECT**
- 제목 "어떤 모습으로 변신할까요?"
- 콘셉트 카드 그리드: FHD 4열x2행(카드 약 380x400: 썸네일 + 이름 + 한 줄 설명), compact 4열x2행(카드 약 220x260, 설명 생략).
- 카드 터치 → 선택 테두리 강조 후 `[이 콘셉트로 촬영]` 버튼 활성 (실수 터치 방지를 위해 2단계 확인 권장). 하단 `[뒤로]`.
- 콘셉트 8개 초과 시 페이지 버튼(◀ ▶). 스크롤 제스처는 구현하지 않는다.
- 카드 썸네일 Surface는 시작 시 1회 로드해 캐시.

**CONSENT**
- 문구 예시 (설정 `PHOTOBOOTH_AI_CONSENT_TEXT`로 바꿀 수 있게):
  - "촬영한 사진은 AI 변환을 위해 외부 AI 서버로 전송됩니다."
  - "변환이 끝난 사진은 이 부스에만 저장되며 {N}일 후 자동 삭제됩니다."
  - "만 14세 미만은 보호자와 함께 동의해 주세요."
- 버튼: `[동의 안 함]` `[동의하고 촬영]`.

**COUNTDOWN / FLASH (AI 모드일 때 추가 요소)**
- 상단 바에 콘셉트 배지: "🔥 소방관 변신 · 2 / 4".
- 하단 썸네일 줄에서 이미 변환 끝난 컷은 작은 ✓ 표시 (선택 사항).

**AI_PROCESSING**
- 제목 "AI가 변신 중이에요"
- 4개 슬롯(2x2 또는 1x4): 각 슬롯에 원본 썸네일 → 변환 완료 시 AI 썸네일로 교체 + ✓. 실패 컷은 원본 유지 + "원본 사용" 회색 배지.
- 진행 바: 완료 컷 수 / 4, 경과 시간, "보통 20~30초 걸려요".
- 일정 시간(`AI_TOTAL_TIMEOUT`, 기본 90초) 넘으면 미완료 컷 모두 원본으로 확정하고 진행.
- 이 화면에서는 입력 무시 (단, 운영자 탈출용 ESC는 기존대로 종료).

**REVIEW (AI 모드일 때 변경점)**
- 왼쪽 출력 미리보기: AI 결과로 합성된 시트.
- 썸네일 위 배지: AI 성공 컷 "AI", 실패/원본 컷 "원본".
- 컷 선택 후 동작 버튼:
  - `[선택 컷 다시 찍기]` (기존) → 재촬영 후 **그 컷만** AI 재변환 → AI_PROCESSING(1컷) → REVIEW
  - `[선택 컷 다시 변환]` (신규) → 같은 원본으로 재변환 (세션당 횟수 제한)
  - `[원본/AI 전환]` (신규) → 해당 컷 표시 소스 토글, API 호출 없음
- **촬영 방향 버튼은 AI 모드에서 숨긴다.** 방향을 바꾸면 크롭이 달라져 AI를 4장 전부 다시 호출해야 하기 때문 (비용/시간).
- 필터 버튼은 유지: AI 결과 위에 후처리로 적용 (API 재호출 없음). 콘셉트의 `default_filter`(보통 `original`)로 초기화.
- 프레임 버튼 유지. 콘셉트 `default_frame`으로 초기화.
- compact 레이아웃은 현재도 꽉 차 있으므로 방향 버튼 자리(`right_card.y + 222 ~ 300`)를 신규 버튼 2개 자리로 재사용한다.
- 하단 상태 문구: "{콘셉트명} · {프레임} · {필터} · 2x2 · {n}장".

**STATUS (운영 점검) 추가 행**
- "AI" 행: Provider/모델명, 키 설정 여부(키 값은 절대 표시 금지, 앞 4자리도 X), 마지막 연결 확인 결과, 오늘 AI 성공/실패 수.
- 행이 하나 늘어나므로 FHD `y` 간격 92 기준 8행이면 하단 닫기 버튼(SCREEN_H-140)과 겹치는지 확인. compact는 현재 좌표가 FHD 전용(`360, y-16, 1200, 78`)이라 1024 폭에서 잘림 → 이번에 compact 좌표도 같이 정리.

---

## 6. 모듈/코드 설계

### 6.1 신규 파일

```
ai_transform.py          # Provider 추상화 + 작업 관리자
ai_concepts.py           # 콘셉트 로더/검증 (assets/ai_concepts.json 읽기)
assets/ai_concepts.json  # 콘셉트 정의
assets/ai_concepts/*.jpg # 콘셉트 썸네일
tools/ai_poc.py          # 단독 실행 PoC/프롬프트 튜닝 스크립트 (pygame 의존 없음)
```

### 6.2 `ai_transform.py` 인터페이스

```python
@dataclass
class AIResult:
    ok: bool
    image_bgr: np.ndarray | None   # 성공 시 BGR
    error: str = ""                # 실패 사유 (로그/운영용, 사용자에게는 일반 문구)
    elapsed: float = 0.0
    refused: bool = False          # 안전정책 거절 여부

class AIProvider(Protocol):
    name: str
    def transform(self, image_bgr: np.ndarray, prompt: str, timeout: float) -> AIResult: ...
    def health_check(self, timeout: float = 5.0) -> tuple[bool, str]: ...

class GeminiProvider: ...   # requests로 REST 호출
class MockProvider: ...     # sleep(PHOTOBOOTH_AI_MOCK_DELAY) 후 색조 변환 + 콘셉트명 텍스트 오버레이
                            # PHOTOBOOTH_AI_MOCK_FAIL_RATE(0~1)로 실패 시뮬레이션

def get_provider() -> AIProvider   # cfg.AI_PROVIDER로 선택: "gemini" | "mock" | (추후 "fal", "relay")

class AITransformManager:
    """세션 단위 비동기 변환 관리자. main.py는 이것만 사용."""
    def __init__(self, provider, max_workers=cfg.AI_MAX_CONCURRENCY): ...
    def submit(self, generation: int, index: int, image_bgr, concept) -> None
    def poll(self) -> list[tuple[int, int, AIResult]]   # (generation, index, result) 완료분 꺼내기
    def cancel_all(self) -> None                         # 세션 리셋 시. 진행 중 요청 결과는 generation으로 무시
    def pending_count(self, generation: int) -> int
```

구현 규칙:

- `concurrent.futures.ThreadPoolExecutor(max_workers=4)` 사용. 결과는 `queue.Queue`에 넣고 메인 루프에서 `poll()`로 꺼낸다. **pygame Surface 생성/상태 변경은 메인 스레드에서만** 하는 것을 권장 (기존 composer 스레드가 Surface를 만드는 건 그대로 두되, 새 코드는 이 규칙을 따른다).
- 컷마다 `(session_generation, index)`로 결과를 식별. `_reset_session()`에서 세션 generation이 바뀌면 늦게 온 결과는 버린다. 기존 `_compose_generation`과 **별도로** `self._ai_generation`을 둔다 (compose generation은 재합성마다 증가하므로 AI 식별용으로 부적합).
- 재시도: 네트워크 오류/5xx/429는 1회 재시도(지수 백오프 1~2초). 안전정책 거절(refused)은 재시도하지 않음.
- 타임아웃: 요청당 `AI_REQUEST_TIMEOUT`(기본 45초). 전체는 main에서 `AI_TOTAL_TIMEOUT`으로 관리.
- 실패해도 예외를 밖으로 던지지 말고 `AIResult(ok=False, ...)` 반환.
- 로그에 API 키, 요청 본문(base64 이미지) 절대 출력 금지.

### 6.3 이미지 전/후처리 (정확히 이 순서)

**전송 전:**
1. `raw = captured_raw_bgr[i]` (방향 보정만 된 원본, 필터 X, 미러 X)
2. `crop = center_crop_bgr_to_aspect(raw, composer.photo_slot_aspect(is_landscape, "grid"))`
   → 인쇄 영역만 보내서 AI가 화면 밖 영역을 그리느라 낭비하지 않게 함. 크롭 후 비율 ≈ 0.67 (세로형).
3. 긴 변 1024px로 리사이즈 (`AI_UPLOAD_MAX_SIDE`), JPEG quality 90 인코딩.

**수신 후:**
1. 디코드 → BGR.
2. 결과 해상도/비율이 다를 수 있으므로 다시 `center_crop_bgr_to_aspect(result, 같은 aspect)`.
3. 세션 데이터에 저장 (6.4).
4. `apply_filter(ai_bgr, selected_filter)` → `captured_bgr[i]`로 사용 → composer.

### 6.4 세션 데이터 확장 (`_reset_session`에 추가)

```python
self.session_mode = "normal"            # "normal" | "ai"
self.ai_concept_id = None
self.ai_generation += 1                 # 세션 식별 (위 6.2)
self.ai_results = [None] * PHOTO_COUNT  # 성공 시 AI BGR (크롭 완료본)
self.ai_status  = ["idle"] * PHOTO_COUNT  # idle|pending|done|failed|refused|timeout
self.ai_use_original = [False] * PHOTO_COUNT  # 리뷰에서 원본 토글
self.ai_regen_count = 0
self.ai_processing_started_at = 0.0
```

`_refresh_processed_photos()` 수정:

```python
for i, raw in enumerate(self.captured_raw_bgr):
    base = raw
    if self.session_mode == "ai" and self.ai_results[i] is not None and not self.ai_use_original[i]:
        base = self.ai_results[i]
    self.captured_bgr[i] = apply_filter(base, self.selected_filter)
```

`_capture_photo()` 수정: AI 모드면 촬영 직후 `manager.submit(self.ai_generation, idx, raw_crop, concept)` 호출, `ai_status[idx]="pending"`. 재촬영(retake)이면 해당 인덱스의 `ai_results[idx]=None`, `ai_use_original[idx]=False`로 초기화 후 submit.

**주의:** 현재 `_capture_photo`는 `captured_bgr`에 필터 적용본을 append한다. AI 모드에선 4장 촬영 직후 composer를 바로 돌리면 안 되고, `_draw_flash`의 `n >= PHOTO_COUNT` 분기에서 `session_mode == "ai"`면 `_set_state(St.AI_PROCESSING)`로 가야 한다.

### 6.5 메인 루프 연동

`run()`의 매 프레임에서 (상태와 무관하게) `self._poll_ai_results()` 호출:

```python
def _poll_ai_results(self):
    for gen, idx, res in self.ai_manager.poll():
        if gen != self.ai_generation:
            continue  # 지난 세션 결과 버림
        if res.ok:
            self.ai_results[idx] = res.image_bgr; self.ai_status[idx] = "done"
            booth_stats.increment("ai_success")
        else:
            self.ai_status[idx] = "refused" if res.refused else "failed"
            booth_stats.increment("ai_failure")
        # REVIEW 상태에서 재변환 결과가 도착했다면 즉시 재합성
        if self.state == St.REVIEW and not self._composing:
            self._recompose_current_session()
```

`AI_PROCESSING` 업데이트:
- 모든 컷이 `pending`이 아니면 → `_refresh_processed_photos()` → `_start_compose()` → `REVIEW`.
- `time.time() - ai_processing_started_at > AI_TOTAL_TIMEOUT` → pending 컷을 `timeout`으로 마킹(원본 사용) → 동일하게 진행. (늦게 도착한 결과는 REVIEW에서 받아 자동 반영해도 되고 버려도 됨 — **버리는 쪽 권장**: 사용자가 이미 보고 있는 결과가 갑자기 바뀌면 혼란. timeout 마킹된 컷은 poll에서 무시.)

`REVIEW` 자동 초기화 타이머(`REVIEW_TIMEOUT`)는 `state_time` 기준이라 AI_PROCESSING 시간은 포함되지 않는다 (현행 유지).

### 6.6 composer 변경 (최소)

- `compose_print_image(..., label_text: str | None = None)` / `make_preview_image(..., label_text=None)` 인자 추가.
- `_make_sheet` 하단 라벨: `label_text`가 있으면 `f"{PRINT_BRAND_TEXT} · {label_text} · {today}"`, 없으면 기존 문자열 그대로.
- 선택: AI 컷 인쇄물 구석에 작은 "AI 생성 이미지" 표기 (`PHOTOBOOTH_AI_PRINT_DISCLOSURE=1` 기본 ON). AI 생성물 표시 의무화 추세 대응.

### 6.7 설정 추가 (`config.py` + `.env.example`)

```bash
# ─── AI 변신 ───
PHOTOBOOTH_AI_ENABLED=0                 # 기본 OFF. 키 설정 후 1로
PHOTOBOOTH_AI_PROVIDER=gemini           # gemini | mock
PHOTOBOOTH_AI_API_KEY=                  # 절대 커밋 금지
PHOTOBOOTH_AI_MODEL=                    # 비우면 provider 기본값. 최신 모델 ID 확인 후 기입
PHOTOBOOTH_AI_REQUEST_TIMEOUT=45
PHOTOBOOTH_AI_TOTAL_TIMEOUT=90
PHOTOBOOTH_AI_MAX_CONCURRENCY=4
PHOTOBOOTH_AI_UPLOAD_MAX_SIDE=1024
PHOTOBOOTH_AI_MAX_REGEN_PER_SESSION=4
PHOTOBOOTH_AI_REQUIRE_CONSENT=1
PHOTOBOOTH_AI_SELECT_TIMEOUT=60
PHOTOBOOTH_AI_PRINT_DISCLOSURE=1
PHOTOBOOTH_AI_SAVE_SOURCES=0            # 1이면 photos/ai/{sid}/ 에 원본·AI 컷 저장 (디버그/품질 분석용)
PHOTOBOOTH_AI_MOCK_DELAY=3
PHOTOBOOTH_AI_MOCK_FAIL_RATE=0
PHOTOBOOTH_AI_CONCEPTS_FILE=assets/ai_concepts.json
```

- `config.py`에 `_env_float` 헬퍼가 없으므로 `AI_MOCK_FAIL_RATE`용으로 추가.
- `AI_ENABLED=1`인데 키가 비었고 provider가 mock이 아니면 → 시작 시 경고 로그 + AI 버튼 비활성 (크래시 금지).
- CLI 옵션 추가 (`main()`의 argparse): `--ai-mock` (provider=mock, enabled=1 강제), `--no-ai`.

### 6.8 저장/정리

- `AI_SAVE_SOURCES=1`일 때만 `photos/ai/{session_id}/{i}_src.jpg`, `{i}_ai.jpg` 저장.
- `_cleanup_old_photos`는 최상위 `*.jpg`만 보므로, `photos/ai/` 하위 폴더도 같은 보관일 기준으로 지우는 로직을 추가 (폴더 mtime 기준 `shutil.rmtree`).
- 최종 합성본 경로/QR은 기존과 동일 (`photos/{sid}.jpg`).

### 6.9 통계 (`booth_stats.COUNTERS`에 추가)

`ai_sessions`, `ai_requests`, `ai_success`, `ai_failure`, `ai_refused`, `ai_regenerate`
- 기존 `_load_unlocked`는 total에 setdefault를 해 주므로 기존 json과 호환됨. daily도 increment에서 setdefault 함. OK.
- 콘셉트별 인기 통계(선택): `ai_concept_counts` 딕셔너리를 별도 키로 저장 (COUNTERS 구조와 분리).
- STATUS 화면 "오늘 운영" 줄에 `AI {성공}/{요청}` 추가.

### 6.10 doctor.py

`check_ai()` 추가:
- AI_ENABLED 여부, provider, 키 존재(값 출력 금지), 콘셉트 JSON 파싱/썸네일 존재 여부.
- `--ai-live` 옵션일 때만 실제 테스트 이미지 1장 변환(비용 발생하므로 기본은 health_check만).
- 인터넷 연결: Provider 호스트 DNS/HTTPS 접속 확인.

### 6.11 네트워크 상태 감시

- 시작 시 + 60초마다 백그라운드 스레드에서 `provider.health_check()` (가벼운 요청, 예: 모델 목록 조회 or HEAD) → `self.ai_online` 갱신.
- 실패하면 IDLE의 AI 버튼 회색 처리. 성공하면 다시 활성.
- health_check는 이미지 생성 호출을 하지 말 것 (비용).

---

## 7. 개인정보·안전·법적 고려 (반드시 구현)

1. **외부 전송 고지/동의**: CONSENT 화면. 어린이 대상 행사면 보호자 동의 문구. 행사 주최 측과 동의 방식 협의(현장 서면 동의 등)가 필요할 수 있음 — 운영자에게 알릴 것.
2. **최소 전송**: 인쇄 크롭 영역만, 1024px로 축소해 전송. 원본 프레임 전체를 보내지 않는다.
3. **보관 최소화**: 기본적으로 원본/AI 개별 컷은 디스크에 저장하지 않는다 (`AI_SAVE_SOURCES=0`). 최종 합성본만 기존 보관 정책(14일)을 따름.
4. **Provider 데이터 정책 확인**: 유료 API 티어에서 입력 이미지가 학습에 쓰이지 않는지, 보관 기간이 어떤지 확인 후 동의 문구에 반영. 무료 티어는 학습에 사용될 수 있어 **사용 금지**.
5. **안전정책 거절 처리**: 미성년자/인물 편집이 거절될 수 있음 → `refused`로 기록, 원본 대체, 사용자 문구는 "이 컷은 AI 변환이 어려워 원본으로 넣었어요".
6. **콘텐츠 제한**: 실존 인물·브랜드·구단 로고·실제 국가 휘장 재현 금지 프롬프트. 노출/폭력 콘셉트 금지.
7. **AI 생성물 표기**: 인쇄물 하단 "AI 생성 이미지" 작게 표기 (6.6).
8. **키 보안**: `.env` 권한 600 안내, 로그·화면·QR 페이지에 키 노출 금지, Provider 콘솔에서 월 예산 상한 설정.

---

## 8. 오류/엣지 케이스 표

| 상황 | 처리 |
|---|---|
| 키 없음 / AI_ENABLED=0 | AI 버튼 숨김 또는 비활성, 일반 네컷만 |
| 네트워크 끊김 (촬영 전) | AI 버튼 비활성 + "AI 서버 연결 확인 중" |
| 네트워크 끊김 (변환 중) | 재시도 1회 → 실패 컷 원본 대체 → REVIEW에 안내 |
| 429 Rate limit | 1~2초 백오프 후 1회 재시도, 이후 원본 |
| 안전정책 거절 | 재시도 없이 원본, 안내 문구 |
| 결과 이미지 비율 다름 | 슬롯 비율로 중앙 크롭 |
| 결과에 사람 수/얼굴이 바뀜 | 자동 감지 안 함 → 리뷰에서 사용자가 [원본/AI 전환] 또는 [다시 변환] |
| 전체 90초 초과 | 미완료 컷 원본 확정, 늦은 응답 무시 |
| AI_PROCESSING 중 세션 리셋 | ai_generation 증가 → 늦은 결과 무시, `cancel_all()` (진행 중 HTTP는 끝까지 돌 수 있음, 결과만 버림) |
| 리뷰에서 재변환 중 인쇄 버튼 | `_composing`과 동일하게 "변환 중" 안내 후 무시 (`ai_status`에 pending 있으면 인쇄/QR 딤) |
| 재변환 횟수 초과 | 버튼 딤 + "다시 변환은 세션당 N회까지" |
| 같은 터치로 클릭 2회 발생 | 상태 전환 직후 250ms 입력 무시(디바운스) |
| 일반 네컷 모드 | AI 관련 코드 경로를 전혀 타지 않아야 함 (회귀 테스트로 확인) |

---

## 9. 구현 단계 (Phase) — 이 순서대로 진행

각 Phase 끝마다 기존 체크(`python -m py_compile ...`, `bash -n install.sh run.sh`, 합성 규격 테스트)와 `./run.sh --test --window` 일반 네컷 회귀 확인.

### Phase 0. PoC (코드 통합 전, 반나절)
- `tools/ai_poc.py <input.jpg> --concept firefighter --out out/` : pygame 없이 Provider만 사용.
- 확인 항목: (1) 1인/2인/4인 사진에서 얼굴 유지 품질 (2) 응답 시간 p50/p95 (3) 미성년자/단체사진 거절 여부 (4) 결과 해상도/비율 (5) 장당 비용.
- 결과를 `docs/AI_POC_RESULT.md`에 표로 기록. **이 결과로 Provider/프롬프트 확정.** 품질이 부족하면 2순위 Provider로 같은 테스트.

### Phase 1. 백엔드 모듈
- `ai_transform.py`(Gemini + Mock), `ai_concepts.py`, `assets/ai_concepts.json`, config/.env.example, booth_stats 카운터, requirements/install.sh(Pi 분기 포함).
- 단위 테스트(간단 스크립트): MockProvider로 submit/poll/generation 무시/타임아웃 동작 확인.

### Phase 2. 촬영 흐름 연동 (UI 최소)
- 세션 필드, `_capture_photo` submit, `_poll_ai_results`, `AI_PROCESSING` 상태(단순 진행률 텍스트), `_refresh_processed_photos` 분기, composer `label_text`.
- 임시로 IDLE에서 `A` 키 = AI 모드(첫 번째 콘셉트)로 시작.
- `./run.sh --test --window --ai-mock`으로 끝까지 인쇄 직전까지 확인.

### Phase 3. 화면
- IDLE 버튼 2개, CONCEPT_SELECT, CONSENT, AI_PROCESSING 슬롯 UI, REVIEW 배지/토글/재변환/방향 버튼 숨김, COUNTDOWN 배지.
- FHD와 compact(1024x768) **둘 다** 스크린샷으로 확인. `_audit_button_layout` 경고 0개.

### Phase 4. 운영 기능
- STATUS 화면 AI 행 + compact 좌표 정리, doctor `check_ai`, health_check 스레드, `photos/ai` 정리, 인쇄물 AI 표기, 콘셉트 썸네일 제작/커밋.

### Phase 5. 현장 테스트 (실제 Pi + 웹캠 + CP1500 + 현장 Wi-Fi)
- 12장 체크리스트 전부 통과 후 `DEVELOPMENT_HANDOFF.md`, `README.md`, `RASPBERRY_PI5_SETUP.md`에 AI 설정 절차 추가.

---

## 10. PoC 스크립트 요구사항 (`tools/ai_poc.py`)

```
python3 tools/ai_poc.py samples/group4.jpg --concept all --repeat 2 --out poc_out/
```
- `--concept all`이면 json의 모든 콘셉트 실행.
- 콘셉트별 결과 jpg 저장 + `poc_out/report.csv` (concept, input, ok, refused, elapsed, out_w, out_h, error).
- 입력 전처리는 6.3과 **같은 함수**를 import해서 사용 (PoC와 실서비스 결과가 같아야 함).
- 샘플 사진은 저장소에 커밋하지 않는다 (`.gitignore`에 `samples/`, `poc_out/` 추가).

---

## 11. 테스트 체크리스트

### 자동/로컬
- [ ] `python -m py_compile main.py composer.py effects.py camera.py qr_share.py printer.py config.py doctor.py booth_stats.py ai_transform.py ai_concepts.py`
- [ ] `bash -n install.sh && bash -n run.sh`
- [ ] 기존 합성 규격 테스트(DEVELOPMENT_HANDOFF 7장) + `label_text` 넣은 버전 1181x1748 확인
- [ ] MockProvider: 4컷 submit → 4개 결과 poll, 세션 리셋 후 도착한 결과 무시, FAIL_RATE=1에서 전부 원본 대체
- [ ] `ai_concepts.json` 오류(필수 키 누락, 썸네일 없음) 시 해당 콘셉트만 제외하고 계속 실행
- [ ] `AI_ENABLED=0`에서 IDLE 화면/동작이 현재 main과 동일

### 화면 (`--test --window --ai-mock`)
- [ ] FHD 1920x1080, 1280x720, 1024x768 각각에서 IDLE / CONCEPT_SELECT / CONSENT / AI_PROCESSING / REVIEW / STATUS 겹침 없음
- [ ] 60초 무입력 시 CONCEPT_SELECT → IDLE
- [ ] AI_PROCESSING 90초 타임아웃 (MOCK_DELAY=120으로 확인)
- [ ] 리뷰: 원본/AI 토글, 재변환 횟수 제한, 선택 컷 재촬영 후 그 컷만 재변환, 필터 변경 시 API 호출 없음
- [ ] 변환 중 인쇄/QR 버튼 딤

### 현장 (실제 Pi)
- [ ] `python3 doctor.py` 및 `python3 doctor.py --ai-live` 통과
- [ ] 1인/2인/4인 각 콘셉트 최소 1회 실제 변환 → CP1500 인쇄 품질 확인 (AI 결과는 1024px 내외라 슬롯 517x770 기준 충분한지)
- [ ] Wi-Fi 끊고 AI 버튼 비활성 / 일반 네컷 정상
- [ ] 변환 도중 Wi-Fi 끊기 → 원본 대체 후 인쇄 가능
- [ ] QR 다운로드가 AI 합성본으로 나오는지
- [ ] 운영 점검 화면 AI 통계 증가 확인
- [ ] 30분 연속 운영 시 메모리 증가 없음 (`top`으로 확인, ThreadPool/Surface 누수 점검)

---

## 12. 구현 시 지켜야 할 코드 규칙 (기존 스타일)

- 한국어 주석/로그, 섹션 구분 주석(`# ─── ... ───`) 스타일 유지.
- 새 설정은 반드시 `config.py` + `.env.example` 둘 다 추가 (install.sh가 example에서 누락 키를 기존 `.env`에 자동 추가함).
- 모든 화면 좌표는 FHD 고정 좌표와 compact 계산 좌표 두 벌. 텍스트는 `draw_text(..., max_width=...)`로 넘침 방지.
- 새 버튼은 `_audit_button_layout` groups에 등록.
- 백그라운드 작업은 데몬 스레드/Executor, 결과는 generation으로 검증.
- 예외로 메인 루프가 죽으면 부스가 꺼진다 → AI 경로는 전부 try/except로 감싸고 원본 fallback.
- `.env`, API 키, `photos/`, `samples/`, `poc_out/` 커밋 금지.
- 일반 네컷 경로 회귀 금지. AI 관련 분기는 `if self.session_mode == "ai":` 안에 가둔다.

---

## 13. 향후 확장 아이디어 (우선순위 낮음)

1. **"1장 촬영 → 4가지 콘셉트"** 모드: 한 번 찍고 4개 다른 직업으로 변환해 네 컷 구성 (피키픽/캐럿류에서 인기). 위 설계에서 `submit` 시 컷마다 다른 concept을 넘기면 됨.
2. 콘셉트 전용 프레임 디자인(배경 이미지 PNG 오버레이) — composer에 `overlay_png` 옵션.
3. 중계 서버(RelayProvider): 여러 부스 키 관리, 원격 콘셉트 배포, 사용량 대시보드.
4. 인터넷 공개 QR(클라우드 스토리지 업로드 + 만료 링크) — 같은 Wi-Fi 제약 해소. 개인정보 동의 범위 재검토 필요.
5. 얼굴 개수 감지(OpenCV DNN)로 결과 검증 → 사람 수가 달라졌으면 자동 재변환 1회.
6. 운영자 전용 설정 화면에서 콘셉트 ON/OFF, AI 일시정지.
