# ChatPlus Agentic Core Architecture Specification

## 1. 목표
SK텔레콤 채팅플러스(ChattingPlus)를 통해 유입되는 RCS/SMS/MMS 메시지를 실시간으로 감지하고, LLM 기반 에이전트(Agent)가 맥락 이해, 도구 호출(Tool Calling), 외부 시스템 연동을 수행한 뒤 자율적으로 응답 및 업무를 처리하는 **Headless Agentic Messaging Core**를 구축합니다.

---

## 2. 권장 시스템 아키텍처

```text
       [ SK Telecom RCS Oasis Platform ]
      ( wss://wss.rcsoasis.kr / rapi.rcsoasis.kr )
                         ▲
                         │ gRPC-Web / WebSocket
                         ▼
        ┌───────────────────────────────────┐
        │       chatplus-core Daemon        │
        │                                   │
        │ ┌───────────────────────────────┐ │
        │ │ 1. Session & Auth Manager     │ │
        │ │  - Credential Manager 읽기    │ │
        │ │  - Bearer Token 자동 갱신     │ │
        │ └──────────────┬────────────────┘ │
        │                ▼                  │
        │ ┌───────────────────────────────┐ │
        │ │ 2. Oasis Protocol Adapter     │ │
        │ │  - SubscribeNotification 리스너│ │
        │ │  - SendRcs / SendXms 발송기   │ │
        │ └──────────────┬────────────────┘ │
        │                ▼                  │
        │ ┌───────────────────────────────┐ │
        │ │ 3. Message Router & Event Bus │ │
        │ └──────────────┬────────────────┘ │
        └────────────────┼──────────────────┘
                         │
                         ▼
        ┌───────────────────────────────────┐
        │       Agentic Engine (LLM)        │
        │  - Intent Classifier (의도 분석)  │
        │  - Context & Memory Retriever     │
        │  - Function & Tool Calling        │
        │  - Autonomous Response Generator  │
        └───────────────────────────────────┘
```

---

## 3. 핵심 컴포넌트 설계

### 3.1 Session & Auth Manager
- **책임**: Windows 자격증명 관리자(`CredReadW`)에서 ChattingPlus의 `grpc_bearer_token`, `deviceId`를 로드하고, 토큰 만료 주기 전에 자동 갱신(`/oapi/v1/token`)을 수행합니다.
- **구현 방식**:
  - Python: `win32cred` 또는 `ctypes` 기반 `Advapi32.CredReadW`
  - Node/Bun: Win32 FFI 또는 `keytar`

### 3.2 Oasis Protocol Adapter
- **수신 스트림**:
  - `wss://wss.rcsoasis.kr`에 WebSocket 세션 개설 후 `oasis.client.api.SubscribeNotificationService/SubscribeNotification` 스트림 등록.
  - 패킷 수신 시 Protobuf 디코딩 -> 메시지 내용, 발신자 번호(`msisdn`), `conversationId`, 타임스탬프 추출.
  - 수신 즉시 `/oasis.client.api.SendMessageService/SendBulkImdn` 호출하여 읽음 표시(1 삭제/수신확인) 처리.
- **발송 클라이언트**:
  - 수신자의 RCS 지원 여부 확인: `CapabilityService/Query`
  - RCS 가능 시: `SendMessageService/SendRcs`
  - RCS 불가(일반 피처폰/아이폰 iMessage 등) 시: `SendMessageService/SendXms`

### 3.3 Agentic Pipeline
1. **수신 게이트 (Incoming Gate)**:
   - 스팸/광고 필터링 (`/v2/filter` 모델 적용 가능)
   - 사용자별 대화 세션 히스토리 메모리 로드
2. **에이전트 추론 (Agent Reasoning & Tool Use)**:
   - 질문 답변, 일정 등록, 데이터 조회, 크롤링, 알림 등의 도구 실행
   - 필요 시 다중 턴(Multi-turn) 인터랙션 수행
3. **응답 포맷터 (Response Formatter)**:
   - 텍스트 메시지 또는 RCS 리치카드(`RichCard`), 버튼 액션(웹링크, 전화걸기) 포맷팅 후 발송

---

## 4. 단계별 구현 로드맵

- **Phase 1: 자격증명 추출 및 프로토콜 검증**
  - 1회 정식 앱 로그인 후 Windows Credential 저장소 키 스키마 덤프
  - Python/Bun 프로토타입으로 `wss.rcsoasis.kr` 연결 및 핸드셰이크 테스트
- **Phase 2: Protobuf 메시지 정의 및 스트리밍 클라이언트 구현**
  - `SendRcsRequest`, `SendXmsRequest`, `SubscribeNotification` proto 정의
  - 실시간 수신 이벤트 핸들러 완성
- **Phase 3: LLM 에이전트 브리지 연동**
  - 수신 메시지 -> 에이전트 프롬프트 -> 도구 호출 -> 발송 루프 완성
- **Phase 4: 배포 및 백그라운드 서비스화**
  - Windows 백그라운드 서비스(Service/Daemon) 패키징
