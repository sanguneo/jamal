# AOT transport evidence (T03, offline)

> 오모냥(Opus)이 로컬 `extracted_msix/data/app.so` 문자열/바이트 스캔으로 확보. 도너(win) 실행본과 **해시 동일 확인**이라 그대로 유효. (2026-10-15)

## Binary fingerprints — 도너와 100% 일치
app_version `1.5.4+6`, install `com.sktelecom.torchlight_1.5.4.6_x64__82zr7n7shan6r`
| file | SHA-256 | size |
|---|---|---|
| data/app.so | 83a233d8c9bb99f8fc1c97ac9e30f0d8df7e6354a93618bbd8400ac2a74a5cf2 | 18088880 |
| flutter_windows.dll | 30b7dca4c5f0436bc9baf7a4e2fad0adf51095521e97485db501ac68b77ca3d1 | 18523072 |
| ChattingPlus.exe | d7405529fc9d821b7ce748f7052d0682b49bfe1eb1fe30b93cebdb45f39cd619 | 207296 |

## Runtime / snapshot (T03 재료)
- ELF Dart AOT snapshot (magic 7f45 4c46). 심볼: `_kDartVmSnapshotInstructions`, `_kDartIsolateSnapshotData`, `_kDartSnapshotBuildId`. → 표준 AOT 스냅샷 파서 적용 가능.
- Flutter `3.43.0-1.0.pre-76` (문자열 확인). 스냅샷/BuilderInfo 해석 시 이 버전에 맞는 Dart SDK 소스를 써야 함.
- package:grpc, dart:io HTTP/2 스택 내장 (`SecurityContext_SetAlpnProtocols`, `Received unknown HTTP/2 frame type`, `HTTP/2 error:`).

## 전송 구조 (핵심 — 두 채널로 분리, 확인됨)
### A. 유니캐리 RPC = 네이티브 gRPC over HTTP/2
- 대상 루트: `oasis_grpc_root = np-rapi.rcsoasis.kr:1520` (host:port, 평문 https 아님 → 네이티브 gRPC/HTTP2). `oasis_server_root=https://np-rapi.rcsoasis.kr/oasis` (REST).
- gRPC 프레이밍 문자열: `application/grpc`, `grpc-status`, `grpc-encoding`, `grpc-accept-encoding`, `grpc-timeout`, `grpc-message`, `grpc-status-details-bin`, `grpc-exp`, `trailers`, HTTP/2 의사헤더 `:path :method :authority :scheme :status`.
- 메서드(전체 경로 확인): SendRcs, SendXms, SendBulkImdn, CapabilityService/Query, GroupChatService/Request, ProfileService/GetProfile·GetSimpleProfile. → 표준 gRPC 5바이트 프레임(1B compressed flag + 4B length) + HTTP/2 추정(캡처로 확정).

### B. 수신/구독 = 커스텀 WebSocket 봉투
- 대상: `oasis_wss_root = wss://wss.rcsoasis.kr`. WS 핸드셰이크 전체 문자열 존재(`Sec-WebSocket-Protocol/Key/Accept/Version/Extensions`, `permessage-deflate`).
- 앱 레벨 WS RPC 래퍼: `WebSocketSendClient`, `WebSocketReceiveClient`, `WebSocketSendRequestType.`, `send websocket request tid:`, `onResponse tid:`, `onResponse parsing failed tid:`, `websocket send request timeout.`. → **tid(트랜잭션 id) 기반 요청/응답 봉투.** 표준 gRPC-over-WS와 다를 수 있음(plan이 경고한 "커스텀 WS dialect").
- 구독 메서드: `/oasis.client.api.SubscribeNotificationService/SubscribeNotification` (server streaming). `[Subscribe] grpc channel shut down.`, `[Subscribe] access token updated. reconnect with new token.` → WS 위에서 subscribe를 열고 토큰 갱신 시 재연결.
- 메시지 타입: `SubscribeNotificationRequest/Response`, `Notification`, `RcsMessage(.ExtendedMessaging/.ExtendedRCS/.Richcard)`, `XmsReport`.

### 아직 UNVERIFIED (캡처/심층 AOT 필요)
- WS 봉투의 정확한 바이트 프레이밍(tid·requestType·payload 배치), `WebSocketSendRequestType` 열거형 멤버값(AOT 문자열이 힙에 흩어져 인접 복구 안 됨).
- 각 message의 protobuf 필드↔태그↔타입 매핑(여전히 미복구).
- 유니캐리가 정말 HTTP/2 네이티브인지 vs WS로도 보내는지(SendRcs 호출부 추적 필요). config fetch(`oapi/v1/device/config`)의 정확한 HTTP 메서드/바디.

## 함의 (구현)
- `transport/h2.py`(네이티브 gRPC, package grpcio) + `transport/ws.py`(커스텀 tid 봉투) **둘 다** 필요. 수신 MVP는 ws.py 경로.
- 스키마 복구 T07은 캡처 와이어 + AOT BuilderInfo 교차. 전송 라우팅은 위로 상당 부분 확정.


## CAPTURE CORRECTION (T02, 보노봇 2026-10-15) — supersedes AOT guess on subscribe
- cert pinning 없음 → Windows 루트 CA로 평문 MITM 성공.
- **SubscribeNotification = 상시 연결 np-rapi.rcsoasis.kr:1520 (IP 13.209.206.19:1520) 네이티브 gRPC server-streaming over HTTP/2.** (AOT에서 WS 봉투로 추정했으나, 실캡처는 구독이 :1520 gRPC 지속연결임을 보여줌. wss.rcsoasis.kr WS는 주 구독 경로가 아닐 수 있음 — fixture로 최종 확정.)
- config fetch 확정: **GET /oasis/oapi/v1/device/config** (응답 ~38.2k).
- 인증/부트스트랩: POST /oasis/oapi/v1/token, GET /oapi/v1/auth/device, GET /oasis/oapi/v1/{msisdn}/acc...
- 서버 IP: 15.165.140.83, 13.209.206.19 (+ Sentry 34.160.81.0 = 무시).
- 함의: 수신 MVP transport = 네이티브 gRPC(grpcio, HTTP/2) :1520 경로 우선. transport/ws.py보다 transport/h2.py가 수신에도 필요할 수 있음. fixture 도착 후 T06/T07 확정.


## 라이브 검증 1차 (2026-10-15, 실서버 왕복 — 비밀값 없음)
수신 구현(H2Transport→subscribe→codec)을 실엔드포인트 `np-rapi.rcsoasis.kr:1520`에 연결 시도한 결과:
- **TLS·HTTP/2·gRPC 프레이밍·메서드 경로·x-mcs 메타데이터·subscription_key 요청이 모두 서버에 수용됨.** 서버가 `UNIMPLEMENTED`가 아니라 **토큰 검증 단계까지 진행**했으므로 transport·request·method·header 설계가 실서버 기준으로 실증됨.
- 반환: `StatusCode.UNAUTHENTICATED — "invalid auth token: token validate failed. not matched with issued token info"`.
- 해석: **만료가 아닌 토큰 supersede**(덤프 이후 앱이 새 토큰을 발급/갱신하면 이전 access_token이 무효). 재검증에는 앱을 닫은 상태에서 뜬 **신선한 토큰 1회분**이 필요. refresh_token은 주인님 지시로 보류라 자가 갱신 없음.
- 결론: 수신 경로 코드는 정상. 남은 것은 유효 토큰 확보 후 실메시지 1건 디코드 확인뿐.


## 라이브 검증 2차 — 성공 (2026-10-15, 신선 토큰, PII 비기재)
신선 세션(exported_at 09:47:05Z, refresh 제외)으로 `np-rapi.rcsoasis.kr:1520` 재연결:
- **인증 통과 → 구독 성립 → 서버 스트림 수신 → 코덱 디코드까지 end-to-end 성공.**
- 수신: Notification 2건 = ①제어 알림(field1 control, 메시지 0) ②동기화 백로그(메시지 70건).
- 디코드 검증: 각 메시지가 from/direction(In·Out 모두)/content_type/text로 정상 매핑. 관측된 content_type = `text/plain`, `multipart/related`, `application/vnd.gsma.xbotmessage.v1.0+json`. NMS JSON은 UTF-8로 정상 파싱(콘솔의 cp949 깨짐은 표시 전용, 라이브러리 디코드와 무관).
- 결론: **수신 MVP(transport+auth+subscribe+codec)가 운영 백엔드 기준으로 완전 실증됨.**

### 로더 포맷 후속(관측)
보노봇 실제 export는 roots를 config식 키(`oasis_grpc_root` 등), limits를 `oasis_max_*` + 문자열값으로 덤프함. 현재 `load_session`은 짧은 키(`grpc`/`wss`/...)와 정수 limits만 수용 → 수동 정규화 후 통과. 후속: 로더가 두 포맷을 모두 수용하거나 export 측 표준화 필요.


## 송신 스키마 복구 가능성 평가 (2026-10-15)
app.so(18.1MB, Dart AOT) 문자열 분석 결과:
- 서비스/메서드 확정: `/oasis.client.api.SendMessageService/{SendRcs,SendXms,SendBulkImdn}`. 메시지 타입명 존재: `SendRcsRequest`, `SendXmsRequest`, `SendBulkImdnRequest`, `RcsMessage`(Extended/Richcard 변형).
- **proto 필드명은 release 빌드에서 제거됨(_omitFieldNames).** 결정적 근거: 캡처로 확정된 수신 필드 `subscription_key`가 바이너리에 전무(x0). 따라서 `conversationId/messageId/recipient/...` 등은 앱 레이어 Dart 식별자이지 wire 필드명이 아님 — 문자열만으로 송신 요청 스키마를 신뢰성 있게 복구 불가.
- **결론: 권위있는 송신 스키마 = 실제 SendRcs/SendXms 요청 1건 캡처.** (수신 때 transport-fixtures.json로 요청측을 확정한 것과 동일 방식.) 캡처된 req_body_b64를 decode_grpc.py로 분해 → 태그/와이어타입 확정 → 송신 빌더 구현.


## 송신 스키마 확정 (2026-10-15, 실캡처 디코드 — 바이트 일치 증명, PII 비기재)
보노봇 self 송신 캡처(텍스트+이미지, 둘 다 SendXms)를 decode_grpc로 분해해 확정. 내 인코더 출력이 실 요청과 **바이트 단위 100% 일치** 검증됨.
- **텍스트 SendXms**: `{ f1: XmsText{ f1:수신 MSISDN(str), f4:varint=1(종류), f31:본문(str) }, f10:클라이언트IP(str) }`. (요청엔 메시지ID/대화ID 없음 — 서버가 부여. 응답 f1=서버 확정 메시지.)
- **이미지 SendXms**: `{ f3: Mms{ f1:수신(str), f31:캡션(str,빈값 가능), f33: FileInfo{ f1:size(varint), f2:fileName(str), f3:contentType(str), f4:href(str) } }, f10:클라이언트IP(str) }`.
- **파일 업로드(이미지 선행)**: `POST /oasis/oapi/v1/mms-file` multipart/form-data (part "Content-Type" + part "file"; filename/content-type) → 201 `{file:{contentType,fileName,href,size}}`. href가 FileInfo.f4로 들어감.
- **블로커(이미지 실발송)**: 업로드 authorization은 **file_bearer_token**을 사용 — grpc 세션 덤프엔 access_token/grpc_restart_token만 있어 미포함. 이미지 라이브 검증엔 file 토큰 추가 필요.
- 구현: `send.build_xms_text_request`, `send.build_xms_mms_request` (둘 다 바이트 일치), `client.send_xms_text`, CLI `send xms --confirm-send` 게이트. pytest 183 그린.


## 송신 라이브 검증 — 텍스트 성공 (2026-10-15, 허가 범위 1/2, PII 비기재)
주인님 허가("송신 2번까지 허용") 범위에서 chatplus-core가 self("나와의 채팅")로 **실 텍스트 SendXms 1건을 실발송**:
- 경로: CLI `send xms --confirm-send` → build_xms_text_request → H2Transport.unary(/SendMessageService/SendXms) + Bearer/x-mcs.
- 결과: **exit 0, 서버 수락, 응답 775B**(캡처 성공 응답 751B와 동급). UNAUTHENTICATED/INVALID 없음.
- 결론: **텍스트 송신 경로 end-to-end 라이브 실증 완료.** (수신에 이어 송신도 운영 백엔드에서 실동작 확인.)
- 이미지 송신은 인코더·업로드·오케스트레이션 구현+오프라인검증 완료이나, 라이브는 file_bearer_token 필요로 미실행(허가 2건 중 1건 잔여).


## 송신 도착 독립 검증 — 텍스트 (2026-10-15)
방금 실발송한 텍스트 메시지를 **내가 구현한 수신 경로(SubscribeNotification)로 재구독**해 계정 스트림에서 조회:
- 결과: 해당 테스트 문구가 **direction=Out(발신)으로 백로그에 존재** 확인(verify_send.py → FOUND direction=Out, MATCH).
- 의미: 서버 수락(775B 응답)을 넘어 **실제 전송·계정 반영까지 독립 증명**. 송신 경로가 재구현 수신 경로로 교차검증됨. (주인님 수동 확인 불필요.)
- 종합: **수신 + 텍스트 송신 모두 운영 백엔드에서 end-to-end 실증 완료.** 이미지 송신은 구현·오프라인검증 완료, 라이브만 file_bearer_token 대기.


## 송신 라이브 검증 — 이미지 성공 (2026-10-15, 허가 2/2, PII 비기재)
file_bearer_token은 보안저장소에 없고, **s-ft /mms-file 업로드 Authorization = Bearer <access_token>**임을 보노봇이 확인(캡처 마스킹값=access_token). 즉 세션 access_token으로 업로드 가능.
- 실행: CLI `send xms-image --confirm-send` → upload_mms_file(더미 PNG, Bearer access_token) → build_xms_mms_request → H2Transport.unary(SendXms).
- 결과: **exit 0, 서버 수락, 응답 1146B**(캡처 성공 1081B 동급). 업로드 201 + SendXms 수락 모두 통과.
- 도착 교차검증: 수신 경로 재구독 시 보낸 이미지 캡션이 **direction=Out으로 스트림에 존재**(CAP_MATCH=2) 확인.
- 결론: **이미지 송신(파일서버 업로드 + MMS SendXms) end-to-end 라이브 실증 완료.**

## 종합 — 전체 실증 완료
수신(SubscribeNotification) + 텍스트 송신(SendXms) + 이미지 송신(업로드+MMS SendXms) 모두 운영 백엔드 기준 end-to-end 실증. chatplus-core가 RCS Oasis를 저수준에서 직접 수신·발송함을 실증. (SendRcs는 실클라이언트 미사용/미캡처로 미구현, 문서화됨. pytest 188 그린.)
