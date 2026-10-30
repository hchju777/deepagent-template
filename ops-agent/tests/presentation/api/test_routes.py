"""경로 → 작업 실행기에 나가는 명령.

가짜 CLI가 받은 argv를 그대로 찍으므로, 각 경로가 **어떤 명령을 냈는지**를 글자 그대로 본다. 그리고 그 argv를
**진짜 CLI 파서**(`src.__main__.parse_args`)에 넣어 사람이 친 것과 같은 뜻으로 읽히는지까지 본다 — argv 모양만
맞추면 `--`·`--opt=값` 같은 자리에서 파서가 다르게 읽어도 초록이 된다.
"""
import json

import pytest

from src.__main__ import parse_args
from src.presentation.api.assembly import DIAG, OPS
from src.presentation.api.jobs import DOCTOR_S, REPORT_S, SHORT_S

from tests.presentation.api.conftest import NOW, make_app


async def post(client, path, body=None):
    if body is None:
        return await client.post(path)
    return await client.post(path, json=body)


def printed(response) -> list[str]:
    job = response.json()
    assert job["status"] == "ok", job
    return json.loads(job["stdout"])["argv"]


CASES = [
    # (경로, 본문, 나갈 argv, 시간 상한)
    (f"{OPS}/schedule/list", None, ["schedule", "--list"], SHORT_S),
    (f"{OPS}/schedule/list", {"scenario": "daily-alarm"},
     ["schedule", "--list", "--scenario", "daily-alarm"], SHORT_S),
    (f"{OPS}/report/aggregate", None, ["report", "aggregate"], REPORT_S),
    (f"{OPS}/report/aggregate", {"scenario": "daily-alarm", "today": "2026-10-29"},
     ["report", "aggregate", "--scenario", "daily-alarm", "--today", "2026-10-29"], REPORT_S),
    (f"{OPS}/report/run", None, ["report", "run", "--dry-run"], REPORT_S),
    (f"{OPS}/report/run", {"today": "2026-10-29", "dry_run": False, "no_mail": True},
     ["report", "run", "--today", "2026-10-29", "--no-mail"], REPORT_S),
    (f"{OPS}/report/run", {"dry_run": False}, ["report", "run"], REPORT_S),
    (f"{OPS}/mail/describe", None, ["mail", "describe"], SHORT_S),
    (f"{OPS}/mail/send", None, ["mail", "send", "--subject=연결 테스트", "--dry-run"], SHORT_S),
    (f"{OPS}/mail/send", {"subject": "-x 제목", "dry_run": False, "to": ["a@example.com", "b@example.com"]},
     ["mail", "send", "--subject=-x 제목", "--to=a@example.com", "--to=b@example.com"], SHORT_S),
    (f"{DIAG}/boot", None, ["boot"], SHORT_S),
    (f"{DIAG}/doctor", {"gbm": "mx", "fct": "gumi"}, ["doctor", "--gbm", "mx", "--fct", "gumi"], DOCTOR_S),
    (f"{DIAG}/doctor", None, ["doctor"], DOCTOR_S),
    (f"{DIAG}/llm/check", None, ["llm", "check"], SHORT_S),
    (f"{DIAG}/llm/check", {"role": "conclude"}, ["llm", "check", "--role", "conclude"], SHORT_S),
    (f"{DIAG}/llm/ask", {"prompt": "안녕"}, ["llm", "ask", "--", "안녕"], SHORT_S),
    (f"{DIAG}/llm/ask", {"prompt": "-h", "role": "report"}, ["llm", "ask", "--role", "report", "--", "-h"], SHORT_S),
]


@pytest.mark.parametrize("path,body,argv,timeout_s", CASES)
async def test_경로마다_나가는_명령과_시간_상한(client, path, body, argv, timeout_s):
    response = await post(client, path, body)
    assert response.status_code == 200, response.text
    assert printed(response) == argv
    assert response.json()["timeout_s"] == timeout_s
    parse_args(argv)                                  # 진짜 CLI가 받아들인다(SystemExit이면 실패)


def test_진짜_파서가_같은_뜻으로_읽는다():
    """`--`와 `--opt=값`이 없으면 `-h`로 시작하는 질문·`-x`로 시작하는 제목이 옵션으로 읽힌다."""
    ask = parse_args(["llm", "ask", "--role", "report", "--", "-h"])
    assert (ask.prompt, ask.role) == ("-h", "report")
    send = parse_args(["mail", "send", "--subject=-x 제목", "--to=a@example.com", "--to=b@example.com"])
    assert (send.subject, send.to, send.dry_run) == ("-x 제목", ["a@example.com", "b@example.com"], False)
    run = parse_args(["report", "run", "--today", "2026-10-29", "--no-mail"])
    assert (run.today, run.no_mail, run.dry_run) == ("2026-10-29", True, False)


@pytest.mark.parametrize("path,body", [
    (f"{OPS}/report/run", {"scenario": "--today"}),           # 이름 자리에 옵션을 실을 수 없다
    (f"{OPS}/report/run", {"today": "2026-13-01"}),
    (f"{OPS}/report/run", {"모르는": 1}),                      # StrictModel — 오타가 조용히 버려지지 않는다
    (f"{OPS}/mail/send", {"to": ["a@example.com\nsubject : x"]}),
    (f"{OPS}/mail/send", {"to": ["주소아님"]}),
    (f"{DIAG}/llm/ask", {"prompt": "x" * 2001}),
    (f"{DIAG}/llm/ask", {"prompt": ""}),
    (f"{DIAG}/llm/ask", None),
    (f"{DIAG}/llm/check", {"role": "triage"}),
    (f"{DIAG}/doctor", {"gbm": "mx x"}),
    (f"{DIAG}/boot", {"wait_s": 56}),
    (f"{DIAG}/boot", {"wait_s": -1}),
])
async def test_이상한_입력은_명령이_나가기_전에_422(client, app, path, body):
    response = await post(client, path, body)
    assert response.status_code == 422, response.text
    assert app.state.ctx.runner.recent() == []


async def test_돌고_있으면_409와_돌고_있는_작업(client, app):
    runner = app.state.ctx.runner
    busy, _ = runner.submit(["sleep", "30"], timeout_s=2.0)
    response = await post(client, f"{DIAG}/boot")
    assert response.status_code == 409
    body = response.json()
    assert body["running"]["id"] == busy.id and body["running"]["argv"] == ["sleep", "30"]
    assert len(runner.recent()) == 1                  # 거절된 것은 기록에도 안 남는다
    await runner.wait(busy, 30)


async def test_기다림_안에_안_끝나면_202와_id_그리고_jobs로_본다(client, app):
    runner = app.state.ctx.runner
    job, _ = runner.submit(["sleep", "30"], timeout_s=1.0)
    await runner.wait(job, 30)                        # 비운다
    # 이번엔 경로로 — 짧게 기다리게 하고 202를 받는다.
    response = await post(client, f"{OPS}/report/run", {"wait_s": 0})
    assert response.status_code in (200, 202)
    started = response.json()
    if response.status_code == 202:
        assert started["status"] == "running"
    got = await client.get(f"{OPS}/jobs/{started['id']}", params={"wait_s": 30})
    assert got.status_code == 200 and got.json()["status"] == "ok"
    listed = (await client.get(f"{OPS}/jobs")).json()["jobs"]
    assert [j["id"] for j in listed] == [started["id"], job.id]
    assert "stdout" not in listed[0]                  # 목록은 가볍다 — 출력은 /jobs/{id}로


async def test_202는_정말_안_끝났을_때다(client, app):
    """실행기에서 직접 긴 작업을 걸고 경로의 기다림 규칙만 본다."""
    from src.presentation.api.routes_ops import respond
    job, _ = app.state.ctx.runner.submit(["sleep", "30"], timeout_s=1.0)
    response = await respond(app.state.ctx, job, None, wait_s=0.05)
    assert response.status_code == 202
    assert json.loads(response.body)["status"] == "running"
    await app.state.ctx.runner.wait(job, 30)


async def test_모르는_작업은_404(client):
    response = await client.get(f"{OPS}/jobs/nope")
    assert response.status_code == 404


# ── 루트 ─────────────────────────────────────────────────────────────────

async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


async def test_ready는_설정_비밀_스케줄러를_본다(client):
    response = await client.get("/ready")
    assert response.status_code == 200, response.text
    checks = {c["name"]: c for c in response.json()["checks"]}
    assert set(checks) == {"config", "secrets", "scheduler"}
    assert all(c["ok"] for c in checks.values())


async def test_비밀이_비면_ready가_503이고_이름을_말한다(cli_root):
    import httpx
    env = {"LLM_BASE_URL": "https://gw.example.com/v1", "MAIL_AGENT_ID": "a"}
    app = make_app(cli_root, env=env)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as c:
        response = await c.get("/ready")
    assert response.status_code == 503
    checks = {c["name"]: c for c in response.json()["checks"]}
    assert not checks["secrets"]["ok"]
    for name in ("LLM_PASS_KEY", "LLM_CLIENT_KEY", "MAIL_AGENT_API_KEY"):
        assert name in checks["secrets"]["detail"]
    assert not checks["config"]["ok"]


async def test_스케줄러가_죽었으면_ready가_503(cli_root):
    import httpx
    app = make_app(cli_root, scheduler_alive=lambda: False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as c:
        response = await c.get("/ready")
    assert response.status_code == 503
    assert {c["name"]: c["ok"] for c in response.json()["checks"]}["scheduler"] is False


async def test_version은_빌드_해시_시나리오_다음_실행(client, cli_root):
    body = (await client.get("/version")).json()
    assert (body["commit"], body["built_at"]) == ("abc1234", "2026-10-30T08:00:00+09:00")
    assert set(body["config"]["files"]) == {"app.json", "scenarios/daily-alarm.json"}
    [scenario] = body["scenarios"]
    assert scenario["name"] == "daily-alarm" and scenario["title"] == "일일 알람 리포트"
    assert scenario["schedule"] == "cron 0 8 * * 1-5"
    # 금요일 09:00 → 다음 평일 08:00은 월요일
    assert scenario["next_run"] == "2026-11-02T08:00:00+09:00"
    assert body["started_at"] == NOW.isoformat()


async def test_version의_해시는_치환_전_원문이라_비밀이_안_섞이고_파일이_바뀌면_바뀐다(cli_root):
    import httpx

    async def version(env):
        app = make_app(cli_root, env=env)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as c:
            return (await c.get("/version")).json()["config"]

    first = await version({"LLM_PASS_KEY": "하나"})
    assert await version({"LLM_PASS_KEY": "둘"}) == first          # env 값이 바뀌어도 같다
    app_json = cli_root / "config" / "app.json"
    app_json.write_text(app_json.read_text(encoding="utf-8").replace('"output"', '"out2"'), encoding="utf-8")
    second = await version({})
    assert second["files"]["app.json"] != first["files"]["app.json"]
    assert second["hash"] != first["hash"]
    assert second["files"]["scenarios/daily-alarm.json"] == first["files"]["scenarios/daily-alarm.json"]


async def test_빌드_ARG가_없으면_빈_값으로_나간다(cli_root):
    import httpx
    app = make_app(cli_root, build={})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api") as c:
        body = (await c.get("/version")).json()
    assert (body["commit"], body["built_at"]) == ("", "")


def test_Swagger에_경로가_전부_있다(app):
    paths = {(method.upper(), path) for path, item in app.openapi()["paths"].items() for method in item}
    expected = {
        ("GET", "/health"), ("GET", "/ready"), ("GET", "/version"),
        ("GET", f"{OPS}/jobs"), ("GET", f"{OPS}/jobs/{{job_id}}"),
        ("POST", f"{OPS}/schedule/list"),
        ("POST", f"{OPS}/report/aggregate"), ("POST", f"{OPS}/report/run"),
        ("POST", f"{OPS}/mail/describe"), ("POST", f"{OPS}/mail/send"),
        ("GET", f"{OPS}/output"), ("GET", f"{OPS}/output/{{name}}"),
        ("POST", f"{DIAG}/boot"), ("POST", f"{DIAG}/doctor"), ("POST", f"{DIAG}/doctor/all"),
        ("POST", f"{DIAG}/llm/check"), ("POST", f"{DIAG}/llm/ask"), ("POST", f"{DIAG}/llm/raw"),
        ("GET", f"{DIAG}/env"),
        ("POST", f"{DIAG}/net"), ("POST", f"{DIAG}/net/egress"), ("POST", f"{DIAG}/net/trace"),
    }
    assert paths == expected


def test_접두는_사내_원본과_같다():
    assert OPS == "/internal/v1/ops"
    assert DIAG == "/internal/v1/ops/diag"


# ── output ───────────────────────────────────────────────────────────────

def _touch(path, text, mtime):
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


async def test_output_목록은_최근_순이다(client, cli_root):
    out = cli_root / "output"
    _touch(out / "daily-alarm-2026-10-28.html", "a", 1_000)
    _touch(out / "daily-alarm-2026-10-29.html", "bb", 3_000)
    _touch(out / "cases.json", "{}", 2_000)
    (out / "graph").mkdir()
    body = (await client.get(f"{OPS}/output")).json()
    assert [f["name"] for f in body["files"]] == [
        "daily-alarm-2026-10-29.html", "cases.json", "daily-alarm-2026-10-28.html"]
    assert body["files"][0]["size"] == 2


async def test_output의_HTML은_그대로_보인다(client, cli_root):
    html = "<!DOCTYPE html><html><body>한글 리포트</body></html>"
    _touch(cli_root / "output" / "daily-alarm-2026-10-29.html", html, 1_000)
    response = await client.get(f"{OPS}/output/daily-alarm-2026-10-29.html")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "attachment" not in response.headers.get("content-disposition", "")
    assert response.text == html


async def test_output_아래_폴더의_파일도_열린다(client, cli_root):
    _touch(cli_root / "output" / "sent" / "daily-alarm-2026-10-29.json", '{"a": 1}', 1_000)
    response = await client.get(f"{OPS}/output/sent/daily-alarm-2026-10-29.json")
    assert response.status_code == 200 and response.json() == {"a": 1}


async def test_없는_파일은_404(client):
    assert (await client.get(f"{OPS}/output/nope.html")).status_code == 404


@pytest.mark.parametrize("name", ["../config/app.json", "/etc/passwd", "sub/../../config/app.json"])
def test_output_폴더_밖은_거부한다(cli_root, name):
    from src.presentation.api.routes_ops import resolve_output
    assert resolve_output(cli_root / "output", name) is None


async def test_output_경로에_점점이_와도_HTTP에서_거부한다(client):
    response = await client.get(f"{OPS}/output/%2e%2e/config/app.json")
    assert response.status_code in (400, 404)
    assert "timezone" not in response.text
