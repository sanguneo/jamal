# Receive path — captured wire evidence (T07 partial)

> 보노봇 win 캡처(transport-fixtures.json)에서 오모냥이 디코드. **원본 fixture는 저장소 밖**(C:/Users/sknah/.omo/omonyang/chatplus/transport-fixtures.json) — token/account/config 응답 바디에 실토큰·PII 있어 repo 금지. 여기엔 안전한 구조만. (2026-10-15)

## Transport (확정)
- SubscribeNotification = **POST `np-rapi.rcsoasis.kr:1520` `/oasis.client.api.SubscribeNotificationService/SubscribeNotification`, HTTP/2, content-type `application/grpc`, `te: trailers`, `grpc-timeout` 설정, 비압축(grpc-encoding 없음).** 표준 네이티브 gRPC 5바이트 프레임(flag=0 + 4B len + protobuf).
- 필수 헤더(gRPC): `authorization`(access_token Bearer), `x-mcs-deviceid`, `x-mcs-devicename`, `x-mcs-devicetype`(=SD), `x-mcs-osversion`, `x-mcs-clientversion`(=1.5.4.6), `x-mcs-firmwareversion`, `x-mcs-clientip`(=127.0.0.1), `user-agent`(Dart/3.10).
- gRPC 바디는 **평문 protobuf**(127B 요청 클린 디코드). `oasis-encrypt: true`는 REST(rapi/np-rapi :443) 부트스트랩 바디에만 해당할 수 있음 — gRPC 수신 경로엔 미적용.

## REST 부트스트랩 순서 (캡처 flow 순서)
1. POST `rapi.rcsoasis.kr/oapi/v1/ad/app-version` (body `{is_execute_call, poc:"CHAT_PLUS_WIN", current_app_version:"1.5.4.6"}`)
2. GET `rapi.rcsoasis.kr/oapi/v1/auth/device?client_id=<uuid>&mdn=<+82...>`
3. POST `rapi.rcsoasis.kr/oasis/oapi/v1/token`  ← 토큰 발급(응답 민감, repo 금지)
4. GET `np-rapi.rcsoasis.kr/oasis/oapi/v1/device/config`  ← 서비스 루트/설정(~39kB)
5. GET `np-rapi.rcsoasis.kr/oasis/oapi/v1/<mdn>/account`
6. GET `np-rapi.rcsoasis.kr/oasis/ds/v1/spam/<mdn>/events?lastEventSeq=N`
(참고: Sentry/GA 트래픽은 텔레메트리, 무시)

## SubscribeNotificationRequest (확정 — request side)
```proto
message SubscribeNotificationRequest {
  // field 1: 이 캡처에선 미출현
  string subscription_key = 2; // base64("<account_hash>:<client_id>:<epoch_micros>")
}
```
- 디코드 예: base64 → `<account_hash>:<client_id>:<epoch_micros>`
- client_id = REST `/auth/device`의 client_id와 동일. epoch µs = 구독 연결 시각(nonce).

## 아직 필요 (③ 인바운드)
- **SubscribeNotificationResponse / Notification 와이어 바이트** — 캡처 중 수신 메시지 0건이라 스트림 응답이 비어 있음(resplen=0). 주인님 폰(채팅+)에서 "나와의 채팅"에 문자 1통 → 보노봇이 알림 페이로드 캡처 → 그걸로 Notification 스키마(발신자/본문/conversation·message id) 복구.
- 그 전까지 코덱(T08)의 수신 디코드는 요청측만 확정, 응답측 opaque 유지.


## REST 바디 암호화 (oasis-encrypt) — 캡처 확인
- rapi/np-rapi :443 REST 응답(그리고 요청)은 `{"encrypted_data":"<hex>"}` **앱 레벨 암호화**. 헤더 `oasis-encrypt: true`. 예: /oapi/v1/ad/app-version, /oasis/oapi/v1/device/config 둘 다 encrypted_data.
- 키/알고리즘 **UNVERIFIED** (복구하려면 추가 RE 필요 — app.so의 oasis-encrypt 구현부 또는 secure store 키).
- **수신 MVP 영향 없음**: 서비스 루트(oasis_grpc_root/wss_root 등)는 이미 secure store에서 확보(session.prd.json / config-snapshot.prd.json)했으므로, 라이브 device/config fetch 없이 **스냅샷 로드**로 진행. 라이브 부트스트랩(encrypted_data 복호화)은 추후 과제로 연기.
- gRPC(:1520) 수신 경로는 평문 protobuf라 이 암호화와 무관.


## Notification wire schema (확정 — ③ 실캡처 디코드 완료)
수신 스트림(:1520 SubscribeNotification server-stream)은 gRPC 길이접두 프레임(1B flag=0 + 4B BE len + protobuf) 연속. 각 메시지(= 1 Notification):

protobuf 봉투:
- field 3 (varint): push epoch seconds.
- field 1 (bytes, wt2): 제어/경량 서브메시지 (예: 구독 ping/IMDN). 내부에 conversation-id 문자열 + base64("account:client:ts") 토큰. (상세 미복구, opaque 유지 가능)
- field 2 (string, wt2): **NMS 이벤트 JSON** (실제 메시지 내용). field1과 field2는 알림 종류에 따라 택일로 보임.

field 2 JSON (OMA CPM / RCS NMS):
```
{ "mdn": "+<e164>",
  "nmsEventList": {
    "index": <int>,
    "link": [{"href": "<NmsSubscription url>", "rel": "NmsSubscription"}],
    "restartToken": "<base64>",   // grpc_restart_token과 동류 (스트림 재개)
    "nmsEvent": [ { "changedObject": { "attributes": { "attribute": [
        {"name":"From","value":"tel:+<e164>"},
        {"name":"To","value":["tel:+<e164>"]},         // 배열
        {"name":"Conversation-ID","value":"<uuid>"},
        {"name":"Date","value":"<iso8601 +09:00>"},
        {"name":"Content-Type","value":"text/plain"},
        {"name":"P-Asserted-Service","value":"urn:urn-7:3gpp-service.ims.icsi.oma.cpm.session"},
        {"name":"Message-Context","value":"chat-message"},
        {"name":"Direction","value":"In"|"Out"},        // In=수신, Out=발신(내가 보낸 것도 백로그에 옴)
        {"name":"TextContent","value":"<메시지 본문 텍스트>"}
    ] } } } , ... ]  // 재구독 시 백로그 다건
  } }
```
수신 Message 모델 매핑: from=From(tel: 제거), to=To[0], conversation_id=Conversation-ID, date=Date, text=TextContent, direction=Direction, content_type=Content-Type.
참고: 다른 알림엔 changedObject 외 chat-message 외 타입(IMDN 등)도 있을 수 있음 — 미지 name/attribute는 opaque 보존.
