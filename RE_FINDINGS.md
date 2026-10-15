# chatplus-core — app.so에서 검증한 RE Ground Truth

> 오모냥이 `extracted_msix/data/app.so` (17.3MB, Dart AOT) ASCII 문자열/바이트 스캔으로 **실제 존재 확인**한 사실만 적는다. 추정은 "미확인"으로 표시. (2026-10-15)

## 0. 목표 (주인님 지시)
- SK텔레콤 채팅+(com.sktelecom.torchlight, Flutter Desktop)가 쓰는 **RCS Oasis 백엔드 프로토콜을 저수준에서 직접** 쓰는 `chatplus-core` 라이브러리/CLI.
- **자동화(LLM 에이전트) 계층은 범위 밖.** 순수 프로토콜 클라이언트.
- 로그인 세션: 이 PC엔 채팅+ 미설치. 보노봇 PC(win)의 로그인된 세션 토큰을 빌려 쓴다(수신/구독 먼저, refresh_token은 보류).

## 1. gRPC 서비스·메서드 (전체 경로 문자열 확인됨)
```
/oasis.client.api.SendMessageService/SendRcs
/oasis.client.api.SendMessageService/SendXms
/oasis.client.api.SendMessageService/SendBulkImdn
/oasis.client.api.SubscribeNotificationService/SubscribeNotification   (server streaming)
/oasis.client.api.CapabilityService/Query
/oasis.client.api.GroupChatService/Request
/oasis.client.api.ProfileService/GetProfile
/oasis.client.api.ProfileService/GetSimpleProfile
```

## 2. 메시지 타입 이름 (문자열 확인됨)
- 요청/응답: `SendRcsRequest`, `SendXmsRequest`, `SendBulkImdnRequest`, `SubscribeNotificationRequest`, `SubscribeNotificationResponse`, `CapabilityRequest`, `CapabilityResponse`, `GroupChatResponse`
- 콘텐츠: `RcsMessage` (+ `.ExtendedMessaging`, `.ExtendedRCS`, `.Richcard`), `XmsReport`, `Notification`
- **미확인(복구 필요): 각 메시지의 필드 이름/태그번호/와이어타입.** Dart AOT 문자열 풀에 필드명 후보(queryCapabilityRequestedDate, virtualFileId, definitionurl 등)가 흩어져 있으나 message→field→tag 매핑은 단순 스캔으로 복구 불가. → **스키마 복구 전략 결정 필요** (아래 7).

## 3. 엔드포인트 (prd/stg/dev, 문자열 확인됨)
- 실시간 스트리밍(WS): `wss://wss.rcsoasis.kr` (stg: `wss://stg-wss.rcsoasis.kr`, dev: `wss://dev-wss.rcsoasis.kr`)
- API GW: `https://rapi.rcsoasis.kr` (stg-rapi / dev-rapi)
- 파일 전송(FT): `s-ft.rcsoasis.kr`, `l-ft.rcsoasis.kr` (대용량) + stg/dev 변형
- NMS 동기화: `https://np-rapi.rcsoasis.kr/oasis/nms/v1/os/`
- SKT AI(에이닷): `https://aitelco.sktswe.net/api`
- 인증/보안: `https://slf.sktswe.net`, `https://beta-sl.sktswe.net`
- 신고 정책: `https://support.rcsoasis.kr:2001/chat-plus/policy/message-report.html`
- **중요: URL은 하드코딩이 아니라 서버 config로 주입됨.** config 키: `oasis_config`, `oasis_grpc_root`, `oasis_wss_root`, `oasis_server_root`, `oasis_small_file_server_root`, `oasis_large_file_server_root`, `oasis_upload_fileserver_root`, `oasis_max_group_xms_users`, `oasis_max_small_file_size`, `oasis_max_upload_file_size`. → `oapi/v1/device/config`로 받아온다(추정, 경로는 확인됨).

## 4. 인증/세션 (문자열 확인됨)
- 로그인: QR 연동. 경로 `oapi/v1/auth/method/qrcode`, `oapi/v1/device/config`, 토큰 발급 `oasis/oapi/v1/token`, `/token/v1`.
- 토큰 종류(secure storage 키): `grpc_bearer_token`, `rest_bearer_token`, `file_bearer_token`, `access_token`, `basic_token`, `OASIS_EXP_ACCESS_TOKEN`, `OASIS_EXP_REFRESH_TOKEN`, `refresh_token`.
- 디바이스 식별/헤더: `deviceId`, `x-mcs-deviceId`, `x-mcs-clientIp`, `x-mcs-clientVersion`, `x-mcs-deviceName`, `x-mcs-deviceType`, `x-mcs-firmwareVersion`, `x-mcs-osVersion`.
- 토큰은 JWT로 보임(`parseJwtRemainTime token:` 문자열). 만료 임박 시 자동 갱신, 401 시 refresh 재시도.
- 구독 재연결: `[Subscribe] access token updated. reconnect with new token.` → 토큰 갱신 시 WS 재연결하는 구조.
- grpc access token 유효성 검사 문자열: `[CreateGrpcSendClient] oasis grpc access token is invalid:`.

## 5. 자격증명 저장소 (문자열 확인됨)
- `flutter_secure_storage` (plugin `plugins.it_nomads.com/flutter_secure_storage`), 서비스명 `flutter_secure_storage_service`.
- Windows에선 자격 증명 관리자(CredReadW)에 저장. 패키지 패밀리/타깃 흔적: `com.sktelecom.torchlight_82zr7n7shan6r!helloclient`, 개발 변형 `com.skt.torchlight.dev`.
- 고정 시드로 보이는 문자열: `prd_messaging_oasis_forever_12!@r` / `stg_...` / `dev_messaging_oasis_forever_12!@r` (secure storage AES 파생/네임스페이스 추정, 미확인).

## 6. 로컬 저장 (문자열 확인됨)
- SQLite3(`sqlite3.dll`) + Dart `drift` ORM. (기존 sanguneo/chatplus-core는 Hive 박스를 읽음 — 버전 차이 가능)
- Imdn/메시지/대화/연락처 관련 로직 문자열 다수(`_insertImdnInternal`, `sendBulkImdn done.`, `RcsInfoRepositoryImpl` 등).
- **미확인: 실제 DB 파일 경로/파일명/테이블 스키마.** → 보노봇 PC(win)의 `%LOCALAPPDATA%\Packages\com.sktelecom.torchlight_*\LocalState\`에서 파일목록+drift 테이블 스키마 받기로 요청함.

## 7. 열린 결정사항 (플래너가 정할 것)
1. **Protobuf 스키마 복구 전략**: (a) Dart 스냅샷 객체그래프/BuilderInfo 역어셈블, (b) 보노봇 세션으로 **실 gRPC 트래픽 캡처**(TLS MITM) 후 와이어 역추론, (c) 하이브리드. — 저수준 송수신엔 이게 선결.
2. **transport**: gRPC-over-HTTP/2 직접 vs gRPC-Web over WebSocket(wss_root). 수신 스트림은 WS 확정. 송신(SendRcs)이 rapi HTTP/2인지 WS인지 확인 필요.
3. **구현 언어/런타임**: 기존 chatplus-core는 Python(uv). 이번 저수준 클라이언트도 Python인지 Bun/TS인지.
4. **범위 1차 목표**: 수신 구독(SubscribeNotification) 디코드까지를 MVP로, 송신은 스키마 확정 후.

## 8. 안전/위험
- refresh_token이 PC 밖으로 나가면 계정 영구 행세 가능(되돌리기 어려움). 1차엔 수명 짧은 bearer 토큰만 사용.
- 재구현 클라이언트로 동일 계정 동시 세션/실발송 시 **이용제한·오발송 위험.** 실발송 단계 전 주인님 재확인 필수.


## 9. 자격증명 복호화 레시피 (확정 — chatplus-core/storage.py + 보노봇 교차확인)
Windows:
- 마스터키: 자격 증명 관리자 CredReadW target `key_.` → 16바이트. (설치별 키, app.so에 하드코딩 아님)
- `.secure` 파일 위치: `%LOCALAPPDATA%/Packages/com.sktelecom.torchlight_82zr7n7shan6r/LocalCache/Roaming/SK Telecom Co., Ltd/ChattingPlus/`, 파일명 `.sK2Nwk<base64(키이름)>.secure`.
- 복호화: `AESGCM(master_key).decrypt(blob[:12], blob[28:]+blob[12:28], None)` → 레이아웃 [12B IV][16B tag][ciphertext].
- cipher_key_<account>_<guid> 복호화 결과 = URL-safe base64 → 32B Hive 키 (edb/*.hive 박스용).
- 같은 마스터키·같은 AES-GCM로 다른 `.secure` 값 파일(토큰류)도 복호화됨.
macOS: Hive 키가 shared_preferences plist의 `flutter.cipher_key_<profile>_<device>`에 평문 base64로 있음.

## 10. 실제 저장된 secure 키 (보노봇 구조 덤프 교차확인)
- 수명 짧은 자격(수신 MVP에 필요): `access_token`(276B, JWT 추정), `grpc_restart_token_<account>`(149B, 구독 스트림 재개용), `client_uuid`(65B), `device_code`/`qr_device_code`(260B). (app.so의 grpc_bearer_token/rest_bearer_token/file_bearer_token은 헤더/인메모리 이름으로 보이고, 영속 저장은 access_token/refresh_token 체계)
- 보류(장기): `refresh_token`(277B), `grpc_restart_token_*`는 세션 재개용이라 포함.
- 설정(비밀 아님, URL): `oasis_grpc_root`, `oasis_wss_root`, `oasis_server_root`, `oasis_small/large_file_server_root`, `oasis_max_*`.
- 에이닷 AI: `_adot:keyAccessToken`(812B)/`keyRefreshToken` — 범위 밖.
- 참고 소스: reference/chatplus-core/{storage,hive,values,reader}.py 로 저장함.
