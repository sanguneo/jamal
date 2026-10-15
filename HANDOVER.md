# Project Handover Document: chatplus (for DESKTOP-L3S2S356)

## 1. 인계 배경 및 작업 맥락
- **작업 내용**: 바탕화면의 `ChattingPlusSetup.exe` (SK텔레콤 채팅플러스 PC 버전) 리버스 엔지니어링 수행.
- **분석 완료 사항**:
  - 인스톨러 내부에서 `ChattingPlus_Setup.msix` 언팩 완료
  - Flutter Desktop x64 런타임 구조 분석 (`flutter_windows.dll`, `data/app.so`)
  - RCS Oasis 백엔드 통신 구조 (gRPC over WebSocket `wss://wss.rcsoasis.kr`, `rapi.rcsoasis.kr`) 규격 추출 완료
  - SQLite 로컬 DB 및 Windows Credential Manager 토큰 저장 방식 규명 완료
- **작업 머신**: `DESKTOP-EOS0O0J` -> 인계 대상 머신: `DESKTOP-L3S2S356`

---

## 2. 프로젝트 파일 현황
프로젝트 경로: `C:\Users\sknah\OmO Projects\chatplus`
- `ChattingPlus_Setup.msix`: 추출된 정식 MSIX 패키지 (31.9 MB)
- `extracted_msix/`: 패키지 압축해제 디렉토리 (845개 파일, `app.so`, `sqlite3.dll`, pdb 등)
- `README.md`: 프로젝트 전반 요약
- `PROTOCOL_ANALYSIS.md`: Oasis gRPC/REST/WebSocket 엔드포인트 및 인증 상세 명세서
- `AGENTIC_CORE_ARCHITECTURE.md`: 에이전틱 코어 데몬 설계도

---

## 3. DESKTOP-L3S2S356 에서 새 세션 시작 시 해야 할 작업 (Action Items)

### Step 1. 채팅플러스 1회 설치 및 로그인 (최초 1회만 필요)
- `ChattingPlus_Setup.msix` 또는 원본 `ChattingPlusSetup.exe`를 실행하여 설치
- 실행 후 스마트폰 채팅플러스 앱에서 QR 코드로 로그인하여 로컬 자격증명 생성

### Step 2. Credential Manager 토큰 덤프 도구 작성
- Windows Credential Manager에 등록된 `flutter_secure_storage` 타깃 항목을 조회하는 파이썬/JS 스크립트 작성:
  ```python
  import win32cred # 또는 ctypes Advapi32.CredReadW
  # TargetName 형태 예: com.sktelecom.torchlight / flutter_secure_storage
  ```
- `grpc_bearer_token`, `deviceId` 획득 확인

### Step 3. Oasis WebSocket 수신 리스너 프로토타입 작성
- `wss://wss.rcsoasis.kr`에 접속하여 Protobuf 스트림 수신 확인
- 참고: [PROTOCOL_ANALYSIS.md](./PROTOCOL_ANALYSIS.md)의 `SubscribeNotificationService` 명세 참조

### Step 4. LLM 에이전트 연동 (chatplus-core)
- 수신 메시지 인입 시 LLM 에이전트 도구 호출 루프 구성
- `SendRcs` / `SendXms` 발송 모듈 구현

---

## 4. 핵심 환경 및 주의사항
- 타깃 OS: Windows 10/11 x64
- 런타임 환경: Bun / Python 3.10+
- 보안 주의: Oasis Bearer Token은 모바일 세션과 연동되어 있으므로 외부 유출 금지
