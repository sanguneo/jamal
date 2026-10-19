![Jamal (جمل)](./assets/og.jpg)

# Jamal (جمل)

**Jamal**은 아랍어로 낙타(**جمل**, jamal)라는 뜻입니다. 사막을 건너 짐을 실어 나르는 낙타처럼, 닫혀 있는 통신사 문자 앱과 내 도구 사이에서 문자를 실어 나릅니다.

SK텔레콤 **채팅+ (ChattingPlus)** Windows 데스크톱 앱(`com.sktelecom.torchlight`, Flutter Desktop)이 쓰는 **RCS Oasis** 백엔드와 직접 통신하는 저수준 클라이언트 라이브러리이자 CLI입니다. 알림을 실시간으로 받고(subscribe) 문자를 보내는(send) 프로토콜을 직접 구현했고, **자동화나 에이전트 기능은 넣지 않았습니다**. 세션은 직접 내보내서 넘겨주고, 통신은 라이브러리가 맡습니다.

> 상호 운용과 연구를 목적으로 배포된 앱을 리버스 엔지니어링해 만들었습니다. 다시 구현한 클라이언트로 실제 계정을 쓰면 계정 제한이나 잘못 보내기 위험이 있습니다. [안전](#안전)을 꼭 읽어 주세요.

## 현재 상태

운영 백엔드(`np-rapi.rcsoasis.kr:1520`, `s-ft.rcsoasis.kr`)에서 처음부터 끝까지 검증했습니다.

| 기능 | 전송 방식 | 상태 |
|---|---|---|
| 알림 수신 (`SubscribeNotification`) | gRPC server-stream | **실서버 검증** |
| 문자 보내기 (`SendXms`) | gRPC unary | **실서버 검증** (보낸 뒤 수신 스트림에 에코 확인) |
| 이미지 보내기 (`SendXms` MMS) | 파일 업로드 + gRPC unary | **실서버 검증** (보낸 뒤 수신 스트림에 에코 확인) |
| `SendRcs` | gRPC unary | 미구현 — 실제 앱도 문자와 이미지를 모두 `SendXms`로 보냅니다 |

오프라인 테스트 219개 통과 (`ruff`, `mypy` 깨끗함). 보내기 인코더는 실제로 캡처한 요청과 바이트 단위로 같습니다.

## 필요한 것

- Python 3.12와 [uv](https://docs.astral.sh/uv/)
- 세션을 내보낼, **로그인된** 채팅+ Windows 앱

```console
uv sync --locked
uv run jamal --help
```

## 인증 정보

`jamal`은 직접 로그인하거나 토큰을 갱신하지 않습니다. 수명이 짧은 **세션 내보내기 파일**만 받아서 씁니다. 로그인된 Windows PC에서 함께 들어 있는 내보내기 스크립트로 만듭니다 (읽기 전용이고, 오래 가는 refresh token은 읽지 않습니다).

```console
# 채팅+ Windows 앱이 설치되어 로그인된 PC에서:
pip install cryptography
python scripts/extract_session.py --out session.json
```

내보내기 스크립트는 앱의 `flutter_secure_storage` 값(Windows 자격 증명 관리자 키로 AES-GCM 봉인)을 읽어, 로더가 검증하는 세션 형식으로 내보냅니다. 형식은 [`docs/session-format.md`](./docs/session-format.md)를 보세요. `session.json`에는 살아 있는 bearer token이 들어 있으니 버전 관리나 공유 채널에 올리지 마세요.

기기 헤더(`x-mcs-*`)는 세션 파일에 들어 있지 않습니다. 실제 수신·발신에는 작은 기기 정보 JSON이 따로 필요합니다 — [`docs/receive.md`](./docs/receive.md)를 보세요.

## 빠른 시작 (CLI)

```console
# 세션 확인 (가려서 출력 — 비밀 값은 찍지 않습니다)
uv run jamal credentials inspect --credentials session.json

# 캡처한 알림 프레임을 오프라인으로 NDJSON 디코드
uv run jamal decode --input capture.json --type notification

# 실시간 알림 수신 (읽기 전용)
uv run jamal subscribe --credentials session.json --config config.json \
    --device device.json --limit 1

# 보내기 — 실제로 네트워크로 나갑니다. --confirm-send 가 있어야만 보냅니다
uv run jamal send xms --credentials session.json --device device.json \
    --to +8210XXXXXXXX --text "hello" --confirm-send

uv run jamal send xms-image --credentials session.json --device device.json \
    --to +8210XXXXXXXX --file pic.png --file-token-file token.txt --confirm-send
```

`--confirm-send`가 없으면 `send`는 보낼 내용만 미리 보여주고, 보내지 않은 채 0이 아닌 코드로 끝납니다.

## 빠른 시작 (라이브러리)

```python
from jamal.client import SubscribeClient
from jamal.config import load_config_snapshot
from jamal.credentials import load_session
from jamal.subscribe import DeviceHeaders

session = load_session("session.json")
config = load_config_snapshot("config.json")
device = DeviceHeaders(
    device_id="...", device_name="...", device_type="SD", os_version="...",
    client_version="1.5.4.6", firmware_version="...",
)

async with SubscribeClient(session, config=config) as client:
    async for note in client.subscribe(device):          # 수신
        for message in note.messages:
            print(message.direction, message.from_, message.text)
    await client.send_xms_text(device, "+8210XXXXXXXX", "hello")   # 문자 보내기
```

## 구조

```
src/jamal/
  credentials.py   세션 내보내기 파일 읽기·검증 (쓰기 없음, 갱신 없음)
  config.py        캡처한 부트스트랩 설정 스냅샷 읽기
  transport/h2.py  TLS 위의 네이티브 gRPC (grpc.aio), 채널 하나에 구독 하나
  codec.py         알림 봉투와 NMS JSON을 Message 레코드로 디코드
  subscribe.py     구독 요청과 메타데이터를 만들고 Notification을 내보냄
  upload.py        소용량 파일 서버로 multipart 업로드 (MMS 사전 단계)
  send.py          바이트 단위로 같은 SendXms 요청 인코더와 unary 호출
  client.py        SubscribeClient: subscribe / send_xms_text / send_xms_image
  cli.py           argparse CLI
scripts/
  extract_session.py   로그인된 Windows 앱에서 session.json 내보내기
docs/                수신·발신 안내와 형식 규격
research/            리버스 엔지니어링 근거 (비밀 값 없음)
```

## 안전

- 수명이 짧은 세션 토큰만 씁니다. refresh token은 내보내지도 저장하지도 않습니다.
- 비밀 값은 저장소에 들어가면 안 됩니다. `.gitignore`가 `*session*.json`, `*credentials*.json`, `.secure`, `.env`, 캡처와 덤프를 막습니다.
- 보내기는 실제 네트워크 동작입니다. CLI는 모든 보내기를 `--confirm-send`로 막아 두었고, 라이브러리의 `send_message`도 같은 약속을 문서로 남겼습니다. 실제 계정으로 보내기 전에는 꼭 다시 확인하세요.
- 구독하면 같은 계정의 두 번째 세션이 열립니다. 데스크톱 앱과 동시에 돌리면 앱이 강제로 다시 로그인될 수 있으니, 앱은 로그아웃하고 쓰는 것을 권합니다.

## tsuba / saya 어댑터

`uv run jamal-adapter serve`는 이 클라이언트를 tsuba 0.2.7 규격으로 열어 줍니다. 수신 기록 저장, SSE, 문자 보내기, 선택적 이미지 보내기, 채팅+ 원래 아이콘, saya 자동 등록을 지원합니다.
기존 데스크톱 캐시 어댑터에 읽기 전용으로 붙으면 예전 대화 목록과 저장된 이름도 가져오고, 새 휴대폰 번호로 문자를 시작하는 Oasis 전용 작성 엔드포인트도 있습니다.
실행 옵션과 한계는 [어댑터 설정](docs/adapter.md)을 보세요.

## 테스트

```console
uv run ruff check .
uv run mypy src/jamal
uv run pytest
```

모든 테스트는 오프라인이고 합성 데이터만 씁니다.

## 더 읽을거리

- [`docs/receive.md`](./docs/receive.md) — 수신 흐름과 기기 헤더
- [`docs/send.md`](./docs/send.md) — 발신 와이어 스키마, 업로드, 안전장치
- [`PROTOCOL_ANALYSIS.md`](./PROTOCOL_ANALYSIS.md) — 엔드포인트와 인증
- [`research/`](./research/) — 리버스 엔지니어링 근거
