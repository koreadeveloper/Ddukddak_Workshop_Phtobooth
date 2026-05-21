# 뚝딱 포토부스 — 개발 진행 로그

## 현재 검증된 상태

- 저장소 루트: `C:\Users\USER\Desktop\Ddukddak_Workshop_Phtobooth`
- 표준 시작 경로: `python3 main.py`
- 테스트 실행: `python3 main.py --test`
- 구문 검증: `python3 -m py_compile main.py composer.py camera.py qr_share.py printer.py`
- 현재 최우선 미완료 기능: **pb-003 SELPHY CP1500 인쇄 테스트** (라즈베리파이 현장 필요)
- 현재 차단 요인: 없음 (라즈베리파이 현장 설치 전)

---

## 세션 001 — 2026-05-21

- **목표**: 포토부스 소프트웨어 전체 구현
- **완료 항목**:
  - `config.py` — 전역 설정 (화면/카메라/색상/폰트 경로)
  - `camera.py` — C920e V4L2 스레드 캡처 + MockCamera
  - `composer.py` — 4컷 스트립 합성 (100x148mm @ 300dpi)
  - `qr_share.py` — 로컬 HTTP 서버(port 8080) + QR 코드 생성
  - `printer.py` — CUPS `lp` 래퍼
  - `main.py` — pygame 상태 머신 (IDLE/COUNTDOWN/FLASH/REVIEW/PRINTING/QR_SHOW)
  - `requirements.txt`, `install.sh`, `run.sh`
  - `feature_list.json` — 프로젝트 기능 목록 업데이트
- **검증 실행**: 구문 검사만 (라즈베리파이 미연결)
- **수집된 증거**: 코드 작성 완료
- **커밋**: 없음 (git 미설정)
- **업데이트된 파일**: 위 전체
- **알려진 위험/미해결 이슈**:
  - 라즈베리파이에서 pygame 풀스크린 + 카메라 동시 동작 성능 미확인
  - CUPS 프린터 등록 이름 `Canon_CP1500` — 실제 lpstat 결과로 맞춰야 함
  - QR 공유는 Pi와 폰이 같은 WiFi여야 함
- **다음 최적 단계**:
  1. 라즈베리파이에서 `./install.sh` 실행
  2. `./run.sh --test` 로 화면 동작 확인
  3. 카메라 연결 후 `./run.sh` 실행
  4. CUPS 프린터 등록 후 인쇄 테스트

---

## 라즈베리파이 설치 순서

```bash
# 저장소 전송 후
cd ~/photobooth
chmod +x install.sh run.sh
./install.sh

# 테스트 (카메라 없이)
./run.sh --test

# 실제 실행
./run.sh
```

## CUPS 프린터 등록

```bash
sudo systemctl enable --now cups
lpinfo -v                         # USB URI 확인
sudo lpadmin -p Canon_CP1500 -E \
  -v usb://Canon/CP1500?serial=XXX \
  -m gutenprint.5.3://canonselphycp1500/expert
lpstat -p Canon_CP1500            # 등록 확인
```

프린터 이름이 다르면 `config.py` 의 `PRINTER_NAME` 수정.

## 부팅 자동 시작 (X11)

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
