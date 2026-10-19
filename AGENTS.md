# AGENTS.md — AI 에이전트로 채팅+ 읽고 답하기

`jamal`은 닫혀 있는 SK텔레콤 **채팅+**를 AI 에이전트에게 열어 주는 다리입니다.
`jamal-adapter serve`가 **tsuba 0.2.7 HTTP 어댑터**로 떠서 채팅+ 대화를 받아 두고,
에이전트는 몇 개의 HTTP 엔드포인트만으로 **대화를 읽고, 실시간으로 새 메시지를 받고, 답장을 보냅니다**.

서버(예: OCI)는 **QR 로그인**으로 자기 기기로 등록되고 **토큰을 스스로 갱신**하므로,
에이전트는 로그인·토큰을 전혀 신경 쓰지 않아도 됩니다. 사람 손 없이 계속 떠 있습니다.

```
휴대폰 채팅+  ──RCS Oasis──▶  jamal (OCI, 독립 기기 · 자가 갱신)  ──tsuba HTTP──▶  saya  ──▶  AI 에이전트
```

## 인증

`/health`를 뺀 모든 요청에 `Authorization: Bearer <CHATPLUS_TOKEN>`이 필요합니다.
saya를 통해 붙으면 saya가 토큰을 쥐고 중계하므로, 에이전트는 saya의 `chatplus` provider만 고르면 됩니다.
직접 호출할 때 베이스 URL은 어댑터 포트(기본 `http://127.0.0.1:8767`)입니다.

## 할 수 있는 일 (`GET /capabilities`)

| 기능 | 뜻 |
|---|---|
| `rooms.list` | 관측된 방(대화) 목록 |
| `messages.read` | 과거 메시지 읽기 (`before` 기준 역방향 페이징, 최대 200) |
| `messages.stream` | 새 메시지 실시간 수신 (SSE, `cursor`로 이어받기) |
| `messages.send` | 문자 답장 (최대 3072자, 영수증 `accepted`) |
| `attachments.send` | 이미지 보내기 (설정 시) |
| `x-chatplus.compose` | 방이 없어도 **새 번호로** 문자 시작 |

응답 provider는 `{"id":"chatplus","name":"채팅+"}`이고, 지원하지 않는 기능은 아예 빠집니다.

## 읽기

```console
# 1) 방 목록
GET /rooms
→ {"ok":true,"rooms":[{"id":"...","title":"...","lastActivityAt":"...","features":{...}}, ...]}

# 2) 한 방의 메시지 (최신 → 과거)
GET /messages?roomId=<id>&limit=50&before=<messageId>
→ {"ok":true,"messages":[{"id","roomId","sender","text","kind","sentAt","fromMe"}, ...]}

# 3) 실시간 (SSE). since로 끊긴 지점부터 이어받기
GET /stream?since=<roomId>:<lastMessageId>
event: ready   {resumed, cursor}
event: message {message:{...}}      # 새 메시지마다
event: cursor  {cursor:{roomId:messageId}}   # 처리 지점 저장용
event: ping                          # 15초마다 keep-alive
event: error   {error:{code,message}}  # 끊기면
```

- `fromMe:true`는 내가(이 계정이) 보낸 메시지입니다. 에이전트가 자기 답장에 다시 반응하지 않도록 거르세요.
- `kind`는 `text` / `media` 등입니다.
- 실전 패턴: `/stream`을 구독 → `message` 이벤트가 오면 LLM이 판단 → 필요하면 `/send`로 답 → `cursor`를 저장해 재접속 시 `since`로 이어받기.

## 답장·보내기

```console
# 기존 방에 답장
POST /send            {"roomId":"<id>","text":"안녕하세요"}
→ {"ok":true,"message":{"id":"accepted-...","fromMe":true,...}}

# 방이 없을 때, 새 번호로 문자 시작 (한국 휴대폰 번호)
POST /x-chatplus/compose   {"to":"+8210XXXXXXXX","text":"..."}
→ {"ok":true,"receipt":"accepted"}

# 이미지 (multipart: file, roomId, caption?) — attachments.send 가 있을 때만
POST /upload
```

- 영수증 `accepted`는 **서버가 요청을 받았다**는 뜻이지, 백엔드 저장 메시지 id가 아닙니다. 실제 반영은 `/stream`에서 `fromMe:true`로 되돌아오는 것으로 확인합니다.
- 보내기가 `500 INTERNAL`로 끝나면 **결과를 모르는 상태입니다 — 재시도하지 마세요** (중복 발송 위험).

## 지원하지 않는 것

- `replyTo`(인용 답장), `/edit` `/delete` `/react` `/read` → `501 NOT_SUPPORTED`
- `SendRcs`(리치카드 등) → 미구현. 문자·이미지는 모두 XMS로 나갑니다.

## 토큰·세션 (에이전트가 몰라도 되는 부분)

- jamal이 시작할 때 토큰이 만료/임박이면 `refresh_token`으로 스스로 갱신하고 세션 파일을 원자적으로 바꿉니다.
- 그래도 구독이 끊기거나 토큰이 무효가 되면 `503 REAUTH_REQUIRED`가 옵니다. 이때는 서비스를 재시작(자가 갱신 시도)하거나, 안 되면 사람이 **QR 재로그인**을 해 주면 됩니다.

## 안전 (꼭 지킬 것)

- 같은 계정으로 실제 발송을 **많이** 하면 이용제한·오발송 위험이 있습니다. 보내기는 신중히, 사람 확인이 필요한 맥락이면 먼저 물어보세요.
- 전화번호는 한국 휴대폰(`+8210XXXXXXXX` 또는 `010XXXXXXXX`)만 받습니다.
- 문자 최대 길이는 3072자입니다.
- `Bearer` 토큰과 `session.json`(특히 `refresh_token`)은 계정을 대신할 수 있는 비밀입니다. 로그·저장소·공유 채널에 절대 노출하지 마세요.
