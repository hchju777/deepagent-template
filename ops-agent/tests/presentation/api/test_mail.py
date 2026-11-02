"""5단계 — 메일: 누구에게 가게 돼 있나(`GET /mail/recipients`), 한 사람에게만 시험 발송(`POST /mail/test`).

시험 발송은 `mail send --to`로 나간다 — config 수신자에 **더하지 않고 덮는다**(1단계 `_mail_to`). 여기서 보는 것은 경로가
그 한 주소만 싣는가, 그날 리포트가 있으면 그 파일을 싣는가, 장부를 안 쓰는가다. 나간 argv는 진짜 CLI 파서로도 읽어 본다.
"""
import json

import pytest

from src.presentation.api.assembly import OPS
from src.presentation.api.jobs import SHORT_S
from src.presentation.api.mail_view import mask_address
from src.report import ledger

from tests.presentation.api.conftest import APP_JSON, make_app, real_parse, serve, write_config

REPORT = "daily-alarm-2026-10-29.html"


def printed(response) -> list[str]:
    job = response.json()
    assert job["status"] == "ok", job
    return json.loads(job["stdout"])["argv"]


# ── GET /mail/recipients ───────────────────────────────────────────────

async def test_수신자는_가린_주소와_수만(client):
    response = await client.get(f"{OPS}/mail/recipients")
    assert response.status_code == 200
    assert response.json() == {"enabled": True, "count": 2, "recipients": ["o***@example.com", "l***@example.com"]}
    assert "ops@" not in response.text and "lead@" not in response.text


@pytest.mark.parametrize("address,shown", [
    ("ops@example.com", "o***@example.com"),
    ("a@x.io", "a***@x.io"),                        # 한 글자 주소도 같은 모양 — 길이를 안 드러낸다
    ("first.last@corp.example.co.kr", "f***@corp.example.co.kr"),
])
def test_주소_가리기(address, shown):
    assert mask_address(address) == shown


async def test_메일이_꺼져_있어도_수신자는_보인다(cli_root):
    write_config(cli_root, app={**APP_JSON, "mail": {**APP_JSON["mail"], "enabled": False}})
    async with serve(make_app(cli_root)) as c:
        body = (await c.get(f"{OPS}/mail/recipients")).json()
    assert (body["enabled"], body["count"]) == (False, 2)


async def test_app_json을_못_읽으면_400과_이유(cli_root):
    (cli_root / "config" / "app.json").write_text("{깨짐", encoding="utf-8")
    async with serve(make_app(cli_root)) as c:
        response = await c.get(f"{OPS}/mail/recipients")
    assert response.status_code == 400 and "app.json" in response.json()["detail"]


# ── POST /mail/test ────────────────────────────────────────────────────

async def test_그날_리포트가_있으면_그_파일을_그_한_사람에게만(client, cli_root):
    html = cli_root / "output" / REPORT
    html.write_text("<html>그날 리포트</html>", encoding="utf-8")
    response = await client.post(f"{OPS}/mail/test", params={"to": "me@example.com", "date": "2026-10-29"})
    assert response.status_code == 200, response.text
    argv = printed(response)
    assert argv == ["mail", "send", "--to=me@example.com", "--subject=[테스트] 일일 알람 리포트 2026-10-29(목)",
                    f"--file={html.resolve()}"]
    parsed = real_parse(argv)
    assert (parsed.to, parsed.dry_run, parsed.file) == (["me@example.com"], False, str(html.resolve()))
    assert response.json()["timeout_s"] == SHORT_S
    assert response.json()["test"] == {"to": "me@example.com", "body": "report", "file": REPORT}


async def test_시험_발송은_장부를_안_쓴다(client, cli_root):
    """받은 사람이 한 명뿐인 시험이다 — 장부가 생기면 그날 리포트가 '나갔다'로 보이고 스케줄이 건너뛴다."""
    (cli_root / "output" / REPORT).write_text("<html>그날 리포트</html>", encoding="utf-8")
    response = await client.post(f"{OPS}/mail/test", params={"to": "me@example.com", "date": "2026-10-29"})
    assert response.json()["status"] == "ok"
    assert ledger.all_records(cli_root / "output") == []


async def test_그날_리포트가_없으면_짧은_본문(client):
    response = await client.post(f"{OPS}/mail/test", params={"to": "me@example.com", "date": "2026-10-29"})
    assert response.status_code == 200, response.text
    argv = printed(response)
    assert argv == ["mail", "send", "--to=me@example.com", "--subject=[테스트] 메일 연결 확인"]
    real_parse(argv)
    assert response.json()["test"] == {"to": "me@example.com", "body": "short",
                                       "why": f"그날 리포트 파일이 없다 — {REPORT}"}


async def test_date가_없으면_짧은_본문(client):
    response = await client.post(f"{OPS}/mail/test", params={"to": "me@example.com"})
    assert printed(response) == ["mail", "send", "--to=me@example.com", "--subject=[테스트] 메일 연결 확인"]
    assert response.json()["test"] == {"to": "me@example.com", "body": "short", "why": "date를 안 줬다"}


async def test_미리보기_파일은_그날_리포트가_아니다(client, cli_root):
    """`.preview.html`은 안 보낸 실행의 결과다 — 시험이라도 '그날 리포트'로 실어 보내면 안 된다."""
    (cli_root / "output" / "daily-alarm-2026-10-29.preview.html").write_text("미리보기", encoding="utf-8")
    response = await client.post(f"{OPS}/mail/test", params={"to": "me@example.com", "date": "2026-10-29"})
    assert response.json()["test"]["body"] == "short"


@pytest.mark.parametrize("params", [
    {},                                                     # 받을 사람이 없다
    {"to": "주소아님"},
    {"to": "a@example.com\nsubject : x"},                   # 줄바꿈 — 주입 통로
    {"to": "a@example.com,b@example.com"},                  # 한 칸에 여럿
    {"to": "a@example.com", "date": "2026-13-01"},
    {"to": "a@example.com", "scenario": "--today"},
    {"to": "a@example.com", "모르는": 1},
])
async def test_이상한_입력은_보내기_전에_422(client, app, params):
    response = await client.post(f"{OPS}/mail/test", params=params)
    assert response.status_code == 422, response.text
    assert app.state.ctx.runner.recent() == []


async def test_to를_두_번_주면_422(client, app):
    """문자열 칸에 같은 이름을 두 번 실으면 FastAPI는 마지막 것만 조용히 쓴다 — '한 사람에게만'이 다른 사람에게 갈 수 있다."""
    response = await client.post(f"{OPS}/mail/test", params=[("to", "a@example.com"), ("to", "b@example.com")])
    assert response.status_code == 422, response.text
    [error] = response.json()["detail"]
    assert error["loc"] == ["query", "to"] and "한 주소" in error["msg"]
    assert app.state.ctx.runner.recent() == []


async def test_to는_한_칸이고_필수다(app):
    params = {p["name"]: p for p in app.openapi()["paths"][f"{OPS}/mail/test"]["post"]["parameters"]}
    assert set(params) == {"to", "date", "scenario", "wait_s"}
    assert params["to"]["required"] is True and params["to"]["schema"]["type"] == "string"
    assert all(p["in"] == "query" for p in params.values())


async def test_메일이_꺼져_있으면_409이고_안_띄운다(cli_root):
    """꺼져 있으면 `mail send`가 건너뛰고도 0으로 끝난다 — 시험이 '성공'으로 보이는데 아무도 못 받는다."""
    write_config(cli_root, app={**APP_JSON, "mail": {**APP_JSON["mail"], "enabled": False}})
    app = make_app(cli_root)
    async with serve(app) as c:
        response = await c.post(f"{OPS}/mail/test", params={"to": "me@example.com"})
    assert response.status_code == 409 and "enabled=false" in response.json()["detail"]
    assert app.state.ctx.runner.recent() == []


async def test_date를_줬는데_시나리오를_모르면_400(client, app):
    response = await client.post(f"{OPS}/mail/test",
                                 params={"to": "me@example.com", "date": "2026-10-29", "scenario": "nope"})
    assert response.status_code == 400 and "nope" in response.json()["detail"]
    assert app.state.ctx.runner.recent() == []


async def test_JSON_본문은_읽지_않는다(client, app):
    response = await client.post(f"{OPS}/mail/test", json={"to": "me@example.com"})
    assert response.status_code == 422                     # to는 쿼리 칸에만 — 본문은 무시된다
    assert app.state.ctx.runner.recent() == []
