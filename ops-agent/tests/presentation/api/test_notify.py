"""6단계 — 실패 알림: 스케줄 실행이 `failed`·`timeout`이면 `mail.notify_to`로 짧은 메일 한 통.

알림은 작업 실행기에 `mail send --to=<notify_to>`로 들어간다 — 발송 호출 자리를 늘리지 않는다(`test_publish.py`가 못 박는다).
작업은 하위 프로세스 없이 끝난다(`ScriptedRunner`): 여기서 보는 것은 언제 알리고, 누구에게, 무엇을 싣고, 결과를 어디 남기는가다.
"""
import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from src.config.schema_mail import MailConfig
from src.presentation.api.assembly import OPS
from src.report import ledger

from tests.presentation.api.conftest import (APP_JSON, KST, SCENARIO, FakeTime, ScriptedRunner, make_app, real_parse,
                                             serve, until, write_config)

NOTIFY = ["oncall@example.com", "lead@example.com"]
FRI_07 = datetime(2026, 10, 30, 7, 0, tzinfo=KST)          # 08:00 슬롯 전 — 그 발사의 기준일은 목(10-29)
STDERR = "\n".join(f"줄 {n:02d}" for n in range(1, 41)) + "\n"


def with_notify(cli_root, notify_to=NOTIFY, **mail):
    write_config(cli_root, app={**APP_JSON, "mail": {**APP_JSON["mail"], "notify_to": notify_to, **mail}})


def is_report(argv) -> bool:
    return argv[:2] == ["report", "run"]


def report_fails(cli_root, fake, *, code=1, timed_out=False, mail_code=0, stderr=STDERR):
    """리포트는 실패하고, 알림 메일(`mail send`)은 `mail_code`로 끝난다."""
    return ScriptedRunner(cli_root, fake.clock,
                          exit_code=lambda argv: code if is_report(argv) else mail_code,
                          stderr=lambda argv: stderr if is_report(argv) else ("게이트웨이 500\n" if mail_code else ""),
                          timed_out=lambda argv: timed_out and is_report(argv))


async def fire(cli_root, runner, fake, *, times=1):
    """슬롯을 `times`번 쏘고 (마지막 최근 결과, 실행기의 작업들 — 오래된 것부터)."""
    app = make_app(cli_root, clock=fake.clock, sleeper=fake.sleeper, runner=runner)
    async with serve(app) as c:
        await until(lambda: len(fake.asked) == 1)
        for n in range(times):
            fake.wake()
            await until(lambda: len(fake.asked) == n + 2)
        [row] = (await c.get(f"{OPS}/schedule")).json()["scenarios"]
    return row["last"], list(reversed(runner.recent()))


def mail_jobs(jobs):
    return [job for job in jobs if job.argv[:2] == ["mail", "send"]]


# ── 언제 ─────────────────────────────────────────────────────────────

async def test_실패하면_notify_to에게만_한_통(cli_root):
    with_notify(cli_root)
    fake = FakeTime(FRI_07)
    last, jobs = await fire(cli_root, report_fails(cli_root, fake), fake)
    report, mail = jobs
    assert is_report(report.argv) and report.status == "failed"
    # `--to`는 config 수신자를 **덮는다** — 실패 알림이 리포트 수신자 전원에게 가지 않는다.
    assert mail.argv[:4] == ["mail", "send", "--to=oncall@example.com", "--to=lead@example.com"]
    assert mail.argv[4] == "--subject=[실패 알림] 일일 알람 리포트 2026-10-29(목) — 실패"
    parsed = real_parse(mail.argv)
    assert parsed.to == NOTIFY and parsed.dry_run is False and parsed.file is None
    assert last["status"] == "failed"
    assert last["notify"] == {"status": "sent", "to": 2, "job_id": mail.id, "at": "2026-10-30T08:00:00+09:00"}


async def test_시간_초과도_알린다(cli_root):
    with_notify(cli_root)
    fake = FakeTime(FRI_07)
    last, jobs = await fire(cli_root, report_fails(cli_root, fake, timed_out=True), fake)
    [mail] = mail_jobs(jobs)
    assert mail.argv[4].endswith("— 시간 초과")
    assert "시간 초과 — 상한 1800초에 죽였다" in real_parse(mail.argv).body
    assert (last["status"], last["notify"]["status"]) == ("timeout", "sent")


@pytest.mark.parametrize("code,status", [(0, "ok"), (3, "warning"), (4, "already_sent")])
async def test_성공_경고_이미보냄은_알리지_않는다(cli_root, code, status):
    """경고는 일부 법인을 못 읽고 나간 리포트다 — 28개 중 하나는 거의 매일 안 붙어서, 알리면 매일 간다."""
    with_notify(cli_root)
    fake = FakeTime(FRI_07)
    last, jobs = await fire(cli_root, report_fails(cli_root, fake, code=code), fake)
    assert mail_jobs(jobs) == []
    assert (last["status"], last["notify"]) == (status, None)


async def test_사람이_돌린_실행의_실패는_알리지_않는다(cli_root):
    """사람은 Swagger에서 실패를 이미 봤다 — 알림은 아무도 안 보는 스케줄 실행을 위한 것이다."""
    with_notify(cli_root)
    fake = FakeTime(FRI_07)
    runner = report_fails(cli_root, fake)
    app = make_app(cli_root, clock=fake.clock, sleeper=fake.sleeper, runner=runner)
    async with serve(app) as c:
        await until(lambda: len(fake.asked) == 1)                 # 스케줄은 08:00을 기다린다 — 깨우지 않는다
        response = await c.post(f"{OPS}/report/run", params={"dry_run": False})
    assert response.json()["status"] == "failed"
    assert mail_jobs(runner.recent()) == []


# ── 무엇을 ───────────────────────────────────────────────────────────

async def test_본문에_시나리오_기준일_실패_종류_stderr_끝_30줄_할_일(cli_root):
    with_notify(cli_root)
    fake = FakeTime(FRI_07)
    _, jobs = await fire(cli_root, report_fails(cli_root, fake), fake)
    report, mail = jobs
    body = real_parse(mail.argv).body
    assert "시나리오: daily-alarm (일일 알람 리포트)" in body
    assert "기준일: 2026-10-29(목)" in body
    assert "실패 종류: 실패 — 종료 코드 1" in body
    assert f"{OPS}/jobs/{report.id} 보고 {OPS}/report/resend (date=2026-10-29)" in body
    tail = body.split("stderr 끝 30줄:\n", 1)[1]
    assert tail.splitlines() == [f"줄 {n:02d}" for n in range(11, 41)]


async def test_긴_줄은_자른다(cli_root):
    """본문은 명령줄 한 칸으로 넘어간다(Windows 명령줄은 3만 2천 자) — 30줄이 한 줄에 수만 자일 수 있다."""
    with_notify(cli_root)
    fake = FakeTime(FRI_07)
    _, jobs = await fire(cli_root, report_fails(cli_root, fake, stderr="가" * 5000 + "\n"), fake)
    [mail] = mail_jobs(jobs)
    line = real_parse(mail.argv).body.split("stderr 끝 30줄:\n", 1)[1].splitlines()[0]
    assert len(line) <= 301 and line.endswith("…")
    assert sum(len(arg) for arg in mail.argv) < 16000


async def test_stderr가_비면_비었다고_쓴다(cli_root):
    with_notify(cli_root)
    fake = FakeTime(FRI_07)
    _, jobs = await fire(cli_root, report_fails(cli_root, fake, timed_out=True, stderr=""), fake)
    [mail] = mail_jobs(jobs)
    assert "(비어 있다 — 출력 전체는" in real_parse(mail.argv).body


async def test_알림은_장부를_안_쓴다(cli_root):
    """장부가 생기면 그날 리포트가 '나갔다'로 보이고, 다음 발사·재발송이 막힌다."""
    with_notify(cli_root)
    fake = FakeTime(FRI_07)
    last, _ = await fire(cli_root, report_fails(cli_root, fake), fake)
    assert last["notify"]["status"] == "sent"
    assert ledger.all_records(cli_root / "output") == []


# ── 하루 한 통·재시도 없음·안 보낸 이유 ────────────────────────────────

async def test_하루_한_통이고_다음_날은_다시_알린다(cli_root):
    with_notify(cli_root)                                          # config 전체를 다시 쓴다 — 시나리오는 그 뒤에
    (cli_root / "config" / "scenarios" / "daily-alarm.json").write_text(
        json.dumps({**SCENARIO, "schedule": {**SCENARIO["schedule"], "cron": "0 8,9 * * 1-5"}}),
        encoding="utf-8")
    fake = FakeTime(FRI_07)
    runner = report_fails(cli_root, fake)
    app = make_app(cli_root, clock=fake.clock, sleeper=fake.sleeper, runner=runner)
    seen = []
    async with serve(app) as c:
        await until(lambda: len(fake.asked) == 1)
        for n in range(3):                                         # 금 08:00 · 금 09:00 · 월 08:00
            fake.wake()
            await until(lambda: len(fake.asked) == n + 2)
            [row] = (await c.get(f"{OPS}/schedule")).json()["scenarios"]
            seen.append((row["last"]["started"], row["last"]["notify"]["status"]))
    assert seen == [("2026-10-30T08:00:00+09:00", "sent"), ("2026-10-30T09:00:00+09:00", "skipped"),
                    ("2026-11-02T08:00:00+09:00", "sent")]
    assert len(mail_jobs(runner.recent())) == 2


async def test_알림이_실패하면_최근_결과에_남기고_다시_보내지_않는다(cli_root):
    with_notify(cli_root)
    fake = FakeTime(FRI_07)
    last, jobs = await fire(cli_root, report_fails(cli_root, fake, mail_code=1), fake)
    [mail] = mail_jobs(jobs)
    assert last["notify"]["status"] == "failed" and last["notify"]["job_id"] == mail.id
    assert "게이트웨이 500" in last["notify"]["why"]


async def test_알림이_실패해도_그날은_다시_시도하지_않는다(cli_root):
    """하루 한 통은 **시도** 기준이다 — 게이트웨이가 죽은 날 발사마다 다시 보내면 그 실패가 그대로 반복된다."""
    with_notify(cli_root)
    (cli_root / "config" / "scenarios" / "daily-alarm.json").write_text(
        json.dumps({**SCENARIO, "schedule": {**SCENARIO["schedule"], "cron": "0 8,9 * * 1-5"}}), encoding="utf-8")
    fake = FakeTime(FRI_07)
    last, jobs = await fire(cli_root, report_fails(cli_root, fake, mail_code=1), fake, times=2)
    assert len(mail_jobs(jobs)) == 1
    assert last["notify"]["status"] == "skipped"


async def test_notify_to가_비면_안_보내고_이유를_남긴다(cli_root):
    fake = FakeTime(FRI_07)                                        # 시험 config(APP_JSON)에는 notify_to가 없다
    last, jobs = await fire(cli_root, report_fails(cli_root, fake), fake)
    assert mail_jobs(jobs) == []
    assert last["notify"] == {"status": "skipped", "why": "mail.notify_to가 비어 있다"}


async def test_메일이_꺼져_있으면_안_보내고_이유를_남긴다(cli_root):
    with_notify(cli_root, enabled=False)
    fake = FakeTime(FRI_07)
    last, jobs = await fire(cli_root, report_fails(cli_root, fake), fake)
    assert mail_jobs(jobs) == []
    assert last["notify"] == {"status": "skipped", "why": "mail.enabled=false"}


async def test_app_json을_못_읽으면_실패로_남긴다(cli_root):
    """뜬 뒤에 config가 깨졌다 — 알림을 못 보낸 것도 사람이 알아야 한다."""
    with_notify(cli_root)
    fake = FakeTime(FRI_07)
    runner = report_fails(cli_root, fake)
    app = make_app(cli_root, clock=fake.clock, sleeper=fake.sleeper, runner=runner)
    async with serve(app) as c:
        await until(lambda: len(fake.asked) == 1)
        (cli_root / "config" / "app.json").write_text("{깨짐", encoding="utf-8")
        fake.wake()
        await until(lambda: len(fake.asked) == 2)
        [row] = (await c.get(f"{OPS}/schedule")).json()["scenarios"]
    assert row["last"]["notify"]["status"] == "failed" and "app.json" in row["last"]["notify"]["why"]
    assert mail_jobs(runner.recent()) == []


# ── config ───────────────────────────────────────────────────────────

def test_notify_to는_config_값만_쓴다_수신자로_채우지_않는다():
    assert MailConfig(recipients=["ops@example.com"]).notify_to == []


@pytest.mark.parametrize("bad", ["주소아님", "a@example.com\nsubject : x", "a@example.com, b@example.com"])
def test_notify_to도_수신자와_같은_규칙으로_거른다(bad):
    with pytest.raises(ValidationError) as caught:
        MailConfig(notify_to=[bad])
    [error] = caught.value.errors()
    assert (error["loc"], error["type"]) == (("notify_to",), "value_error")      # 모르는 키(extra)가 아니라 주소 규칙


async def test_수신자_보기에_notify_to도_가려서(cli_root):
    with_notify(cli_root)
    async with serve(make_app(cli_root)) as c:
        body = (await c.get(f"{OPS}/mail/recipients")).json()
    assert (body["notify_count"], body["notify_to"]) == (2, ["o***@example.com", "l***@example.com"])
    assert "oncall@" not in str(body)
