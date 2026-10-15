# RCS Oasis 프로토콜 및 네트워크 통신 분석 보고서

## 1. 개요
ChattingPlus PC 버전은 SK텔레콤 및 이동통신 3사의 RCS 네트워크 허브인 **RCS Oasis 플랫폼**과 통신합니다.  
기본 통신 스택은 **gRPC over HTTP/2 & WebSocket (gRPC-Web)** 기반이며, 대용량 파일 전송 및 AI 부가기능은 REST API를 혼용합니다.

---

## 2. 서버 엔드포인트 목록

### 2.1 운영(Production) 및 스테이징 환경
| 용도 | 호스트 및 URL | 프로토콜 |
| :--- | :--- | :--- |
| **실시간 스트리밍** | `wss://wss.rcsoasis.kr` | WebSocket (gRPC-Web 스트리밍) |
| **개발 스트리밍** | `wss://dev-wss.rcsoasis.kr` / `wss://stg-wss.rcsoasis.kr` | WebSocket |
| **Oasis API Gateway** | `https://rapi.rcsoasis.kr` | gRPC / HTTP/2 / REST |
| **개발 API Gateway** | `https://dev-rapi.rcsoasis.kr` | gRPC / HTTP/2 |
| **파일/미디어 전송(FT)** | `https://s-ft.rcsoasis.kr` | HTTPS REST (Upload/Download) |
| **NMS 동기화** | `https://np-rapi.rcsoasis.kr/oasis/nms/v1/os/` | HTTPS REST (히스토리 동기화) |
| **SKT AI/에이닷 연동** | `https://aitelco.sktswe.net/api` | HTTPS REST (대화 요약, 답장 추천 등) |
| **인증/보안 서비스** | `https://slf.sktswe.net`, `https://beta-sl.sktswe.net` | HTTPS REST |

---

## 3. 핵심 gRPC 서비스 및 메소드

`data/app.so` 바이너리 심볼 추출 결과 확인된 실제 gRPC 서비스 목록:

### 3.1 메시지 송수신 서비스 (`SendMessageService`)
- `/oasis.client.api.SendMessageService/SendRcs`
  - 요청 메시지: `SendRcsRequest`
  - 용도: RCS 1:1 및 그룹 텍스트, 리치카드, 이모티콘 메시지 발송
- `/oasis.client.api.SendMessageService/SendXms`
  - 요청 메시지: `SendXmsRequest`
  - 용도: 수신자가 RCS 미지원 시 레거시 SMS/MMS(XMS) 발송
- `/oasis.client.api.SendMessageService/SendBulkImdn`
  - 요청 메시지: `SendBulkImdnRequest`
  - 용도: 메시지 전달 확인(Delivery Report) 및 수신/읽음 확인(Read Report)

### 3.2 실시간 알림 구독 서비스 (`SubscribeNotificationService`)
- `/oasis.client.api.SubscribeNotificationService/SubscribeNotification`
  - 요청: `SubscribeNotificationRequest`
  - 응답: `SubscribeNotificationResponse` (Server Streaming)
  - 채널: `wss://wss.rcsoasis.kr`
  - 용도: 새 수신 메시지, 상대방 읽음 상태, 대화방 초대 등 실시간 이벤트 수신

### 3.3 상대방 기능 조회 (`CapabilityService`)
- `/oasis.client.api.CapabilityService/Query`
  - 용도: 특정 전화번호가 RCS 지원 단말인지, 챗봇인지, 대용량 파일 수신 가능한지 조회

### 3.4 대화방 관리 (`GroupChatService` & `ProfileService`)
- `/oasis.client.api.GroupChatService/Request` (그룹 채팅방 생성, 초대, 퇴장)
- `/oasis.client.api.ProfileService/GetProfile` / `GetSimpleProfile` (사용자/상대방 프로필)

---

## 4. 인증(Auth) 및 세션 유지 구조

### 4.1 로그인 절차 (모바일 QR 연동)
1. 클라이언트가 `/v1/auth/method/qrcode` 호출하여 QR 코드 세션 키 생성.
2. 모바일 채팅플러스 앱에서 PC 화면의 QR 코드를 스캔 및 생체/비밀번호 인증.
3. 클라이언트는 `_pollingQrCodeAuthorized`를 통해 인증 완료 상태 폴링.
4. `/v1/auth/method/confirm` 및 `/oapi/v1/token` 호출하여 최종 인증 토큰 세트 획득.

### 4.2 토큰 종류 및 보관 위치
- **토큰 종류**:
  - `grpc_bearer_token`: gRPC 서비스 호출용 Bearer 토큰
  - `rest_bearer_token`: Oasis REST 엔드포인트 호출용 토큰
  - `file_bearer_token`: 파일 전송 서버(`s-ft.rcsoasis.kr`) 업/다운로드용 토큰
  - `refresh_token` (`OASIS_EXP_REFRESH_TOKEN`): 토큰 만료 시 자동 갱신
- **디바이스 식별자**:
  - `deviceId`, `x-mcs-deviceId`
- **저장소 메커니즘**:
  - `flutter_secure_storage` 플러그인이 Windows Credential Manager에 안전하게 저장.
  - Windows API `CredReadW`를 호출하여 외부 프로세스(에이전트 데몬)에서도 현재 사용자 권한으로 즉시 읽어올 수 있음.

---

## 5. 로컬 데이터베이스 (SQLite 3) 구조

- 모듈: `sqlite3.dll` (표준 SQLite v3)
- ORM: Dart `drift`
- 저장 파일 위치:
  `%LOCALAPPDATA%\Packages\com.sktelecom.torchlight_*\LocalState\`
- 주요 테이블 모델:
  - `messages`: 수신/발신 메시지 본문, 타임스탬프, 발신자 번호, 상태
  - `conversation`: 대화방 ID(`Conversation-ID`), 참여자, 방 타입
  - `contacts`: 동기화된 연락처 정보
  - `imdn`: 읽음 확인 및 전송 상태
