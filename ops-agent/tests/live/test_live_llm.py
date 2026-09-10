"""**사내 LLM 게이트웨이에 실제로 묻는** 테스트 — 사내망에서만 돈다.

```powershell
.venv\\Scripts\\python.exe -m pytest tests/live -m live_llm -v
```

오프라인 테스트(`tests/infrastructure/test_llm_adapters.py`)가 잠그는 것은
**배선**이다 — 헤더가 실리는가, 응답을 어떻게 파싱하는가, 실패를 값으로 바꾸는가.
인프로세스 가짜 게이트웨이가 헤더를 검사하므로 그것까지는 오프라인에서 증명된다.

여기 있는 것은 그것으로 **절대 알 수 없는 것들**이다:

1. 게이트웨이가 실제로 응답하는가 (TLS·프록시·방화벽)
2. 우리가 가진 키가 유효한가
3. **이 모델이 조사 엔진이 요구하는 JSON을 낼 수 있는가** ← 제일 중요

3번이 제일 중요한 이유: 8단계의 노드(frame/integrate/conclude)와 접수가 전부
LLM 응답을 JSON으로 파싱한다. 모델이 그걸 못 하면 **조사가 도중에 조용히 멈추고**,
증상은 "보고서가 비어 있다"로 나타난다. 엔진을 다 만든 뒤에 알면 프롬프트 설계를
다시 해야 한다 — 그래서 이 테스트를 엔진보다 **먼저** 만들었다.

이 파일은 `config/app.json`의 **실제 설정**을 읽는다(env가 아니라). 운영에서 쓸
base_url·키·TLS 선택 그대로 물어봐야 의미가 있기 때문이다.
"""
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from src.config.loader import ConfigError, load_app_config
from src.infrastructure.llm_factory import build_llm
from src.infrastructure.tls import tls_problems

pytestmark = pytest.mark.live_llm

CONFIG_ROOT = Path(os.environ.get("OPS_CONFIG_ROOT", "config"))


@pytest.fixture(scope="module")
def llm():
    from dotenv import load_dotenv
    load_dotenv()
    try:
        app = load_app_config(CONFIG_ROOT, env=os.environ)
    except (ConfigError, FileNotFoundError) as exc:
        pytest.skip(f"{CONFIG_ROOT}/app.json을 읽을 수 없다 — {exc}")
    if app.llm is None:
        pytest.skip("app.json에 llm 설정이 없다")
    problems = tls_problems(app.llm.tls)
    if problems:
        pytest.skip(f"TLS 설정 문제 — {problems[0]}")
    print(f"\n  {app.llm.describe()}")
    return build_llm(app.llm, clock=lambda: datetime.now().astimezone(), warn=print)


# ── 붙는가 ────────────────────────────────────────────────────────────

async def test_게이트웨이가_응답한다(llm):
    reply = await llm.ask("Reply with exactly: pong")
    assert reply.status == "ok", reply.error
    assert reply.text.strip(), "빈 응답 — 200을 줬지만 내용이 없다"


async def test_한국어로_답한다(llm):
    reply = await llm.ask("한국어로 한 문장만: 설비 가동률이 낮아지는 흔한 원인 하나.")
    assert reply.status == "ok", reply.error
    assert any("가" <= ch <= "힣" for ch in reply.text), (
        f"한국어가 안 나왔다 — 보고서가 영어로 나올 수 있다: {reply.text[:200]}")


async def test_설정한_모델이_실제로_쓰인다(llm):
    """게이트웨이가 응답에 모델 이름을 실어 주면, 그게 우리가 요청한 것이어야 한다.

    다르면 "config가 안 먹었다"는 뜻인데 **답은 오므로 아무도 알아채지 못한다.**
    """
    reply = await llm.ask("hi")
    assert reply.status == "ok", reply.error
    if not reply.reported_model:
        pytest.skip("게이트웨이가 응답에 모델 이름을 안 싣는다")
    assert reply.model in reply.reported_model, (
        f"config는 {reply.model}을 요청했는데 게이트웨이는 {reply.reported_model}로 응답했다")


# ── 엔진이 요구하는 것을 낼 수 있는가 ──────────────────────────────────

def _parse_json(text: str):
    """코드펜스를 벗겨서라도 파싱한다 — 8단계가 쓸 관용 범위와 같게 본다."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        cleaned = cleaned[4:] if cleaned.startswith("json") else cleaned
    return json.loads(cleaned.strip())


async def test_JSON_객체를_낼_수_있다(llm):
    reply = await llm.ask(
        '아래 형식의 JSON만 출력하라. 설명도 코드펜스도 없이 JSON 객체 하나만:\n'
        '{"verdict": "ok", "score": 1}')
    assert reply.status == "ok", reply.error
    parsed = _parse_json(reply.text)
    assert isinstance(parsed, dict) and "verdict" in parsed, reply.text[:300]


async def test_조사_엔진이_요구하는_모양의_JSON을_낼_수_있다(llm):
    """8단계 frame 노드가 실제로 요구할 모양이다 — 중첩 배열과 한국어 값.

    이것이 실패하면 프롬프트 설계를 바꿔야 한다(예: 필드를 줄이거나,
    예시를 더 주거나, 한 번에 하나씩 묻거나).
    """
    reply = await llm.ask(
        "구미 3라인 OEE가 512로 관측됐다(정상 범위 0~100).\n"
        "가설을 2개 세우고 각 가설을 확인할 태스크를 하나씩 써라.\n"
        "JSON만 출력하라:\n"
        '{"hypotheses": [{"id": "h1", "statement": "...", '
        '"tasks": [{"id": "t1", "goal": "..."}]}]}')
    assert reply.status == "ok", reply.error
    parsed = _parse_json(reply.text)
    assert "hypotheses" in parsed, reply.text[:400]
    assert len(parsed["hypotheses"]) >= 1
    first = parsed["hypotheses"][0]
    assert {"id", "statement"} <= set(first), f"필드가 빠졌다 — {first}"


async def test_같은_질문에_같은_답을_낸다(llm):
    """temperature=0이면 재현돼야 한다. 안 되면 벤치 시나리오를 만들 수 없다.

    게이트웨이가 무작위성을 강제하는 경우도 있으므로 실패하면 **건너뛰지 않고
    알린다** — 그 사실을 알고 설계해야 하기 때문이다.
    """
    prompt = 'JSON만: {"answer": 2 더하기 2}'
    first = await llm.ask(prompt)
    second = await llm.ask(prompt)
    assert first.status == second.status == "ok"
    if first.text.strip() != second.text.strip():
        pytest.fail("temperature=0인데 답이 달라진다 — 결정론 벤치를 만들 수 없다.\n"
                    f"  1차: {first.text[:150]}\n  2차: {second.text[:150]}")
