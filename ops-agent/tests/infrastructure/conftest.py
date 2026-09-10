"""인프로세스 가짜 게이트웨이 — **진짜 HTTP**로 어댑터를 검증한다.

httpx를 목으로 갈아끼우지 않는 이유: 그러면 "헤더가 실제로 소켓에 나갔는가"를
검증할 수 없다. 헤더 이름 오타(`X-OPENAI-TOKEN` vs `X-OPENAPI-TOKEN`)는 목에서는
절대 안 잡히고 사내에서 401로 나타난다.

서버가 헤더 셋을 검사해 틀리면 401을 돌려주므로, 200이 온 것 자체가
"헤더가 맞게 나갔다"는 증거다.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

PASS_KEY, CLIENT_KEY, MODEL_ID = "pass-123", "client-456", "339"


class _Recorder:
    def __init__(self):
        self.requests: list[dict] = []
        self.reply = "pong"
        self.status = 200
        self.payload = None          # 설정하면 이 모양을 그대로 돌려준다


def _handler_for(recorder: _Recorder):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _send(self, code, body):
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            headers = {k.lower(): v for k, v in self.headers.items()}
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            recorder.requests.append({"path": self.path, "headers": headers, "body": body})

            wrong = [n for n, want in (("x-fabrix-client", PASS_KEY),
                                       ("x-openapi-token", CLIENT_KEY),
                                       ("x-llm-model-id", MODEL_ID))
                     if headers.get(n) != want]
            if wrong:
                return self._send(401, {"error": {"message": f"bad headers: {wrong}"}})
            if recorder.status >= 400:
                return self._send(recorder.status, {"error": {"message": "boom"}})
            if recorder.payload is not None:
                return self._send(200, recorder.payload)
            self._send(200, {"id": "chatcmpl-fake", "model": body.get("model"),
                             "choices": [{"message": {"role": "assistant",
                                                      "content": recorder.reply}}]})
    return Handler


@pytest.fixture
def gateway():
    """(base_url, recorder)를 돌려준다. 포트는 OS가 고른다."""
    recorder = _Recorder()
    server = HTTPServer(("127.0.0.1", 0), _handler_for(recorder))
    # poll_interval 기본값(0.5s)이면 shutdown()이 그만큼 기다린다 — 테스트마다
    # 0.5초가 쌓여 14개에 7초가 된다.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01},
                              daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", recorder
    finally:
        server.shutdown()
        server.server_close()


# ── 메일 Agent 대역 ───────────────────────────────────────────────────

MAIL_API_KEY = "mail-key-789"
_FIELD = __import__("re").compile(r"^\s*(to_email|subject)\s*:\s*(.*)$",
                                  __import__("re").IGNORECASE)


class _AgentRecorder:
    def __init__(self):
        self.requests: list[dict] = []
        self.status = 200


def _agent_handler_for(recorder: "_AgentRecorder"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _send(self, code, body):
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.headers.get("x-api-key") != MAIL_API_KEY:
                return self._send(401, {"detail": "invalid api key"})
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)))
                              or b"{}")
            recorder.requests.append({"path": self.path, "body": body})
            if recorder.status >= 400:
                return self._send(recorder.status, {"detail": "boom"})

            # **일부러 제일 취약한 파서**: 줄 앵커로 찾고 여러 개면 마지막이 이긴다.
            # 본문 주입이 통하면 여기서 수신자가 바뀌어 보인다.
            parsed = {}
            for line in body.get("input_value", "").split("\n"):
                match = _FIELD.match(line)
                if match:
                    parsed[match.group(1).lower()] = match.group(2).strip()
            recipients = [a.strip() for a in parsed.get("to_email", "").split(",")
                          if a.strip()]
            self._send(200, {"session_id": "sess-fake",
                             "outputs": [{"results": {"message": {"text": "보냈습니다"}}}],
                             "delivered_to": recipients,
                             "read_subject": parsed.get("subject", "")})
    return Handler


@pytest.fixture
def mail_agent():
    """(api_base, recorder)를 돌려준다."""
    recorder = _AgentRecorder()
    server = HTTPServer(("127.0.0.1", 0), _agent_handler_for(recorder))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01},
                              daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api/v1/run", recorder
    finally:
        server.shutdown()
        server.server_close()
