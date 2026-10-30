"""API 테스트의 재료 — 가짜 리포 루트와 그 위의 앱.

## 왜 가짜 CLI를 진짜 하위 프로세스로 띄우는가

작업 실행기가 지키는 것(셸 없음·`-u`·cwd·`PYTHONIOENCODING`·시간 초과에 죽이고 그때까지의 출력 남기기)은
전부 **프로세스 경계에서만** 일어난다. 실행 함수를 가짜로 바꾸면 그 성질이 하나도 시험되지 않는다. 그래서
`<tmp>/src/__main__.py`를 진짜로 두고 `python -u -m src`가 그것을 찾게 한다 — 모양이 실물과 같다.

가짜 CLI는 받은 argv를 JSON으로 그대로 찍는다. 경로 테스트는 그 줄로 "어떤 명령이 나갔는가"를 본다.
"""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

FAKE_MAIN = '''\
import json, os, sys, time
argv = sys.argv[1:]
if argv[:1] == ["sleep"]:
    # flush 없이 찍는다 — `-u`가 없으면 죽일 때 버퍼째 사라진다.
    print("시작")
    print("에러 줄", file=sys.stderr)
    time.sleep(float(argv[1]))
    print("끝")
    sys.exit(0)
if argv[:1] == ["exit"]:
    print("나가는 중", file=sys.stderr)
    sys.exit(int(argv[1]))
if argv[:1] == ["hangul"]:
    print("한글 — 출력")
    sys.exit(0)
if argv[:1] == ["big"]:
    sys.stdout.write("x" * int(argv[1]))
    sys.exit(0)
print(json.dumps({"argv": argv, "cwd": os.getcwd(),
                  "io": os.environ.get("PYTHONIOENCODING")}, ensure_ascii=False))
'''

KST = ZoneInfo("Asia/Seoul")
# 금요일 09:00 KST — 다음 평일 08:00은 월요일이다.
NOW = datetime(2026, 10, 30, 9, 0, 0, tzinfo=KST)

ENV = {"LLM_BASE_URL": "https://gw-user:gw-secret@gw.example.com/v1",
       "LLM_PASS_KEY": "pass-secret-1",
       "LLM_CLIENT_KEY": "client-secret-2",
       "MAIL_AGENT_ID": "agent-7",
       "MAIL_AGENT_API_KEY": "mail-secret-3",
       "REDIS_PASSWORD": "redis-secret-4"}

APP_JSON = {
    "timezone": "Asia/Seoul",
    "output_dir": "output",
    "llm": {"adapter": "http", "provider": "openai_compatible", "model": "m", "model_id": "339",
            "base_url": "${LLM_BASE_URL}", "pass_key": "${LLM_PASS_KEY}",
            "client_key": "${LLM_CLIENT_KEY}"},
    "mail": {"enabled": True, "recipients": ["ops@example.com", "lead@example.com"],
             "api_base": "https://agent.example.net/api/v1/run",
             "agent_id": "${MAIL_AGENT_ID}", "api_key": "${MAIL_AGENT_API_KEY}"},
}

SCENARIO = {
    "kind": "alarm_daily", "title": "일일 알람 리포트",
    "source": {"collection": "alarm", "date_field": "occ_date"},
    "scope": {"gbms": ["mx"], "sites": ["mx/gumi", "mx/sevt"]},
    "schedule": {"enabled": True, "kind": "cron", "cron": "0 8 * * 1-5"},
}


def write_config(root: Path, *, app: dict | None = None) -> Path:
    config = root / "config"
    (config / "gbm").mkdir(parents=True, exist_ok=True)
    (config / "scenarios").mkdir(parents=True, exist_ok=True)
    (config / "app.json").write_text(json.dumps(app or APP_JSON, ensure_ascii=False), encoding="utf-8")
    (config / "registry.json").write_text(json.dumps({"sites": [
        {"gbm": "mx", "fct": "gumi"}, {"gbm": "mx", "fct": "sevt"},
        {"gbm": "nw", "fct": "sevt"}, {"gbm": "mx", "fct": "off", "enabled": False}]}), encoding="utf-8")
    (config / "gbm" / "common.json").write_text(json.dumps({"infra": {"redis": {
        "db": 0, "password": "${REDIS_PASSWORD}"}}}), encoding="utf-8")
    for gbm, fct in (("mx", "gumi"), ("mx", "sevt"), ("nw", "sevt"), ("mx", "off")):
        (config / "fct" / fct).mkdir(parents=True, exist_ok=True)
        (config / "fct" / fct / f"{gbm}.json").write_text(json.dumps({"infra": {
            "redis": {"url": f"redis://{fct}-{gbm}.example:6379"},
            "mongodb": {"url": f"mongodb://{fct}-{gbm}.example:27017", "database": "ops"}}}),
            encoding="utf-8")
    (config / "scenarios" / "daily-alarm.json").write_text(
        json.dumps(SCENARIO, ensure_ascii=False), encoding="utf-8")
    return config


@pytest.fixture
def cli_root(tmp_path) -> Path:
    """`src/`와 `config/`가 나란히 있는 가짜 리포 루트 — 컨테이너의 `/`와 같은 모양."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "__main__.py").write_text(FAKE_MAIN, encoding="utf-8")
    (root / "output").mkdir()
    write_config(root)
    return root


@pytest.fixture
def fixed_clock():
    return lambda: NOW


def make_app(root: Path, *, env: dict | None = None, clock=None, **kwargs):
    from src.presentation.api.assembly import create_app
    return create_app(root=root, env=ENV if env is None else env, clock=clock or (lambda: NOW),
                      build=kwargs.pop("build", {"commit": "abc1234", "built_at": "2026-10-30T08:00:00+09:00"}),
                      **kwargs)


@pytest.fixture
def app(cli_root):
    return make_app(cli_root)


@pytest.fixture
async def client(app):
    """앱과 같은 이벤트 루프에서 돈다 — 작업 실행기의 태스크가 요청 사이에 살아 있어야 한다."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as c:
        yield c
