"""3단계 — 발송 장부를 API가 보는 자리: report/run의 409, resend, report/sent, GET /schedule의 오늘 상태.

기준일은 CLI와 같은 함수로 센다 — NOW(금 09:00)의 기준일은 목요일, 월요일의 기준일은 금요일이다.
"""
import json
from datetime import date, datetime

import pytest

from src.__main__ import parse_args
from src.presentation.api.assembly import OPS
from src.report import ledger

from tests.presentation.api.conftest import KST, NOW, SCENARIO, FakeTime, make_app, serve, until

THU = date(2026, 10, 29)                 # NOW(금)의 기준일
FRI = date(2026, 10, 30)


def sent(output, day, **fields):
    record = ledger.SentRecord(**{"scenario": "daily-alarm", "day": day, "sent_at": datetime(2026, 10, 30, 8, 2, tzinfo=KST),
                                  "subject": "[운영리포트] 일일 알람 리포트", "recipients": 2, "trigger": "schedule",
                                  "job_id": "s1", **fields})
    assert ledger.write(output, record) is None
    return record


def html(output, day, text="<html>그날 리포트</html>"):
    path = output / f"daily-alarm-{day.isoformat()}.html"
    path.write_text(text, encoding="utf-8")
    return path


def fail_cli(cli_root, code):
    (cli_root / "src" / "__main__.py").write_text(f"import sys\nprint('가짜 실패', file=sys.stderr)\nsys.exit({code})\n",
                                                  encoding="utf-8")


# ── report/run ───────────────────────────────────────────────────────────

async def test_장부가_있으면_보내는_실행은_409와_장부(client, app, cli_root):
    sent(cli_root / "output", THU)
    response = await client.post(f"{OPS}/report/run", params={"dry_run": False})
    assert response.status_code == 409
    assert response.json()["ledger"]["day"] == THU.isoformat()
    assert app.state.ctx.runner.recent() == []                       # 30분짜리를 띄우지 않았다


@pytest.mark.parametrize("params", [{}, {"dry_run": True}, {"dry_run": False, "no_mail": True}])
async def test_보내지_않는_실행은_장부를_안_본다(client, cli_root, params):
    sent(cli_root / "output", THU)
    response = await client.post(f"{OPS}/report/run", params=params)
    assert response.status_code == 200, response.text


async def test_force면_장부가_있어도_보내고_CLI에_force를_넘긴다(client, cli_root):
    sent(cli_root / "output", THU)
    response = await client.post(f"{OPS}/report/run", params={"dry_run": False, "force": True})
    job = response.json()
    assert response.status_code == 200
    argv = json.loads(job["stdout"])["argv"]
    assert argv == ["report", "run", "--force", "--trigger", "manual", "--job-id", job["id"]]
    parsed = parse_args(argv)
    assert (parsed.force, parsed.trigger, parsed.job_id) == (True, "manual", job["id"])


async def test_today를_주면_그날의_기준일_장부를_본다(client, cli_root):
    """월요일(11-02)에 돌면 기준일은 금요일(10-30)이다 — 돌린 날이 아니다."""
    sent(cli_root / "output", FRI)
    blocked = await client.post(f"{OPS}/report/run", params={"dry_run": False, "today": "2026-11-02"})
    assert blocked.status_code == 409 and blocked.json()["ledger"]["day"] == FRI.isoformat()
    free = await client.post(f"{OPS}/report/run", params={"dry_run": False, "today": "2026-11-03"})
    assert free.status_code == 200


# ── report/resend ────────────────────────────────────────────────────────

async def test_resend는_그날_HTML을_원래_제목으로_보내고_끝나면_장부를_쓴다(client, cli_root):
    path = html(cli_root / "output", FRI)
    response = await client.post(f"{OPS}/report/resend", params={"date": "2026-10-30"})
    job = response.json()
    assert response.status_code == 200, job
    argv = json.loads(job["stdout"])["argv"]
    assert argv == ["mail", "send", f"--file={path}", "--subject=일일 알람 리포트 2026-10-30(금)"]
    parsed = parse_args(argv)
    assert (parsed.file, parsed.subject, parsed.dry_run) == (str(path), "일일 알람 리포트 2026-10-30(금)", False)
    record, _ = ledger.read(cli_root / "output", "daily-alarm", FRI)
    assert (record.trigger, record.job_id, record.recipients) == ("manual", job["id"], 2)
    assert record.subject == "[운영리포트] 일일 알람 리포트 2026-10-30(금)"


async def test_resend가_202로_돌아가도_끝난_뒤_장부를_쓴다(client, app, cli_root):
    html(cli_root / "output", FRI)
    response = await client.post(f"{OPS}/report/resend", params={"date": "2026-10-30", "wait_s": 0})
    job = app.state.ctx.runner.get(response.json()["id"])
    await app.state.ctx.runner.wait(job, 30)
    assert ledger.exists(cli_root / "output", "daily-alarm", FRI)


async def test_resend가_실패하면_장부가_없다(client, cli_root):
    html(cli_root / "output", FRI)
    fail_cli(cli_root, 1)
    response = await client.post(f"{OPS}/report/resend", params={"date": "2026-10-30"})
    assert response.json()["status"] == "failed"
    assert not ledger.exists(cli_root / "output", "daily-alarm", FRI)


async def test_resend는_장부가_있으면_409_force면_덮는다(client, cli_root):
    html(cli_root / "output", FRI)
    sent(cli_root / "output", FRI, job_id="old")
    blocked = await client.post(f"{OPS}/report/resend", params={"date": "2026-10-30"})
    assert blocked.status_code == 409 and blocked.json()["ledger"]["job_id"] == "old"
    forced = await client.post(f"{OPS}/report/resend", params={"date": "2026-10-30", "force": True})
    assert forced.status_code == 200
    record, _ = ledger.read(cli_root / "output", "daily-alarm", FRI)
    assert record.job_id == forced.json()["id"]


async def test_resend는_파일이_없으면_404(client):
    response = await client.post(f"{OPS}/report/resend", params={"date": "2026-10-30"})
    assert response.status_code == 404 and "daily-alarm-2026-10-30.html" in response.json()["detail"]


async def test_resend는_메일이_꺼져_있으면_안_보낸다(cli_root):
    """꺼져 있으면 `mail send`가 건너뛰고도 0으로 끝난다 — 그걸 발송으로 알고 장부를 쓰면 안 된다."""
    from tests.presentation.api.conftest import APP_JSON, write_config
    write_config(cli_root, app={**APP_JSON, "mail": {**APP_JSON["mail"], "enabled": False}})
    html(cli_root / "output", FRI)
    app = make_app(cli_root)
    async with serve(app) as c:
        response = await c.post(f"{OPS}/report/resend", params={"date": "2026-10-30"})
    assert response.status_code == 409 and "mail.enabled=false" in response.json()["detail"]
    assert app.state.ctx.runner.recent() == []


# ── report/sent ──────────────────────────────────────────────────────────

async def test_장부_목록과_하루(client, cli_root):
    sent(cli_root / "output", THU)
    sent(cli_root / "output", FRI, trigger="manual")
    listed = (await client.get(f"{OPS}/report/sent")).json()["records"]
    assert [r["day"] for r in listed] == [FRI.isoformat(), THU.isoformat()]
    one = await client.get(f"{OPS}/report/sent/2026-10-30")
    assert one.status_code == 200 and one.json()["trigger"] == "manual"
    assert (await client.get(f"{OPS}/report/sent/2026-10-28")).status_code == 404


# ── GET /schedule — 최근 결과의 나눔과 오늘 상태 ─────────────────────────────

@pytest.mark.parametrize("code,status", [(3, "warning"), (4, "already_sent"), (1, "failed")])
async def test_스케줄_최근_결과는_성공_경고_이미보냄_실패를_나눈다(cli_root, code, status):
    fail_cli(cli_root, code)
    fake = FakeTime(datetime(2026, 10, 30, 7, 0, tzinfo=KST))
    app = make_app(cli_root, clock=fake.clock, sleeper=fake.sleeper)
    async with serve(app) as c:
        await until(lambda: len(fake.asked) == 1)
        fake.wake()
        await until(lambda: len(fake.asked) == 2)
        [row] = (await c.get(f"{OPS}/schedule")).json()["scenarios"]
    assert (row["last"]["status"], row["last"]["exit_code"]) == (status, code)


async def today(cli_root, now, **kwargs):
    async with serve(make_app(cli_root, clock=lambda: now, **kwargs)) as c:
        [row] = (await c.get(f"{OPS}/schedule")).json()["scenarios"]
    return row["today"]


async def test_오늘_슬롯이_지났는데_발사도_장부도_없으면_missed(cli_root):
    """08:00 근처에 파드가 다시 뜨면 그날 슬롯은 건너뛰어지고 실패 알림도 안 간다 — 사람이 여기서 본다."""
    got = await today(cli_root, NOW)
    assert got == {"day": THU.isoformat(), "sent": False, "ledger": None,
                   "slot": "2026-10-30T08:00:00+09:00", "missed": True}


async def test_장부가_있으면_보냈고_missed가_아니다(cli_root):
    sent(cli_root / "output", THU)
    got = await today(cli_root, NOW)
    assert (got["sent"], got["missed"], got["ledger"]["job_id"]) == (True, False, "s1")


async def test_슬롯_전이면_missed가_아니다(cli_root):
    got = await today(cli_root, datetime(2026, 10, 30, 7, 59, tzinfo=KST))
    assert (got["sent"], got["missed"]) == (False, False)


async def test_주말엔_슬롯이_없다(cli_root):
    got = await today(cli_root, datetime(2026, 10, 31, 10, 0, tzinfo=KST))
    assert (got["slot"], got["missed"], got["day"]) == (None, False, FRI.isoformat())


async def test_오늘_발사했으면_장부가_없어도_missed가_아니다_결과가_말한다(cli_root):
    """실패한 발사는 최근 결과에 `failed`로 남는다(6단계에서 알림) — 건너뛴 것과 다르다."""
    fail_cli(cli_root, 1)
    fake = FakeTime(datetime(2026, 10, 30, 7, 0, tzinfo=KST))
    app = make_app(cli_root, clock=fake.clock, sleeper=fake.sleeper)
    async with serve(app) as c:
        await until(lambda: len(fake.asked) == 1)
        fake.wake()
        await until(lambda: len(fake.asked) == 2)
        fake.now = datetime(2026, 10, 30, 9, 0, tzinfo=KST)
        [row] = (await c.get(f"{OPS}/schedule")).json()["scenarios"]
    assert row["last"]["status"] == "failed"
    assert (row["today"]["sent"], row["today"]["missed"]) == (False, False)


async def test_꺼진_시나리오는_missed가_아니다(cli_root):
    body = {**SCENARIO, "schedule": {**SCENARIO["schedule"], "enabled": False}}
    (cli_root / "config" / "scenarios" / "daily-alarm.json").write_text(json.dumps(body), encoding="utf-8")
    got = await today(cli_root, NOW)
    assert (got["slot"], got["missed"]) == (None, False)


async def test_일시정지_중이면_missed가_아니다(cli_root):
    app = make_app(cli_root, clock=lambda: NOW)
    async with serve(app) as c:
        [row] = (await c.post(f"{OPS}/schedule/pause")).json()["scenarios"]
    assert row["today"]["missed"] is False
