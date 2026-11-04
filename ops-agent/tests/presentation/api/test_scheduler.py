"""앱 안 스케줄러 — 가짜 시계로 발사 시각과 나가는 명령을 고정한다.

시각 계산·밀린 슬롯 건너뛰기 자체는 `tests/schedule/`가 지킨다(같은 `run_all`이다). 여기서 보는 것은 **API에 얹었을 때**의
성질이다: lifespan이 띄우고 내린다, 발사는 작업 실행기에 `report run --scenario <이름>`을 넣는다, 수동 작업이 돌고 있으면
기다린다, 일시정지·재개, `/ready`가 죽은 루프를 말한다, `GET /schedule`이 최근 결과를 보인다.
"""
import asyncio
import json
from datetime import datetime

from src.presentation.api.assembly import DIAG, OPS
from src.presentation.api.jobs import REPORT_S

from tests.presentation.api.conftest import (KST, NOW, SCENARIO, FakeTime, ScriptedRunner, make_app, real_parse, serve,
                                             until)

MON_08 = datetime(2026, 11, 2, 8, 0, tzinfo=KST)        # NOW(금 09:00) 다음 평일 08:00
TUE_08 = datetime(2026, 11, 3, 8, 0, tzinfo=KST)
WED_10 = datetime(2026, 11, 4, 10, 0, tzinfo=KST)
THU_08 = datetime(2026, 11, 5, 8, 0, tzinfo=KST)


def scheduled_app(cli_root, fake, **kwargs):
    """작업은 하위 프로세스 없이 끝난다(`ScriptedRunner`) — 여기서 보는 것은 발사와 자리 다툼이다."""
    kwargs.setdefault("runner", ScriptedRunner(cli_root, fake.clock))
    return make_app(cli_root, clock=fake.clock, sleeper=fake.sleeper, **kwargs)


async def settle() -> None:
    """이벤트 루프에 몇 번 자리를 내준다. ASGI 요청은 진짜 I/O가 없어 루프를 안 돌린다 — 막 만든 태스크가 아직 시작도 안 했을 수 있다."""
    for _ in range(10):
        await asyncio.sleep(0)


async def schedule_row(client) -> dict:
    [row] = (await client.get(f"{OPS}/schedule")).json()["scenarios"]
    return row


async def test_시각이_되면_report_run_시나리오를_작업_실행기에_넣는다(cli_root):
    fake = FakeTime(NOW)
    app = scheduled_app(cli_root, fake)
    async with serve(app) as c:
        await until(lambda: len(fake.asked) == 1)
        assert fake.asked == [MON_08]
        row = await schedule_row(c)
        assert (row["name"], row["enabled"], row["on"], row["kind"], row["cron"]) == (
            "daily-alarm", True, True, "cron", "0 8 * * 1-5")
        assert row["next_run"] == MON_08.isoformat() and row["last"] is None

        fake.wake()
        await until(lambda: len(fake.asked) == 2)
        [job] = app.state.ctx.runner.recent()
        # 계기와 작업 id가 장부에 적힌다 — 작업 id는 이 작업 자신의 것이다.
        assert job.argv == ["report", "run", "--scenario", "daily-alarm", "--trigger", "schedule", "--job-id", job.id]
        real_parse(job.argv)                                    # 진짜 CLI가 받아들이는 명령이다
        assert job.timeout_s == REPORT_S
        last = (await schedule_row(c))["last"]
        assert {k: last[k] for k in ("status", "scheduled", "started", "job_id", "exit_code")} == {
            "status": "ok", "scheduled": MON_08.isoformat(), "started": MON_08.isoformat(),
            "job_id": job.id, "exit_code": 0}
        assert fake.asked[1] == TUE_08


async def test_늦게_깨면_밀린_슬롯은_건너뛰고_다음_슬롯으로(cli_root):
    """월 08:00에 깨야 했는데 수요일 10:00에 깼다(멈춰 있었다). 화·수 08:00을 몰아서 쏘지 않는다."""
    fake = FakeTime(NOW)
    app = scheduled_app(cli_root, fake)
    async with serve(app) as c:
        await until(lambda: len(fake.asked) == 1)
        fake.wake(to=WED_10)
        await until(lambda: len(fake.asked) == 2)
        assert fake.asked[1] == THU_08
        assert len(app.state.ctx.runner.recent()) == 1          # 늦은 한 번만 돌았다
        last = (await schedule_row(c))["last"]
        assert (last["scheduled"], last["started"]) == (MON_08.isoformat(), WED_10.isoformat())


async def test_수동_작업이_돌고_있으면_끝날_때까지_기다렸다_돈다(cli_root):
    fake = FakeTime(NOW)
    app = scheduled_app(cli_root, fake)
    runner = app.state.ctx.runner
    async with serve(app) as c:
        await until(lambda: len(fake.asked) == 1)
        runner.hold("manual")
        manual, _ = runner.submit(["diag", "boot"], timeout_s=10.0, job_id="manual")
        fake.wake()
        await until(lambda: app.state.ctx.scheduler.firing("daily-alarm"))
        await settle()
        assert runner.recent() == [manual] and len(fake.asked) == 1   # 발사했지만 자리가 빌 때까지 안 들어갔다
        runner.release("manual")
        await until(lambda: len(fake.asked) == 2)
        scheduled, first = runner.recent()
        assert first is manual and manual.status == "ok"           # 수동은 끝까지 돌았다(스케줄이 끊지 않았다)
        assert scheduled.argv[:4] == ["report", "run", "--scenario", "daily-alarm"] and scheduled.status == "ok"
        assert (await schedule_row(c))["last"]["job_id"] == scheduled.id


async def test_스케줄이_기다리는_동안_사람_요청은_여전히_409(cli_root):
    fake = FakeTime(NOW)
    app = scheduled_app(cli_root, fake)
    runner = app.state.ctx.runner
    async with serve(app) as c:
        await until(lambda: len(fake.asked) == 1)
        runner.hold("manual")
        manual, _ = runner.submit(["diag", "boot"], timeout_s=10.0, job_id="manual")
        fake.wake()
        await until(lambda: app.state.ctx.scheduler.firing("daily-alarm"))
        response = await c.post(f"{DIAG}/boot", params={"wait_s": 0})
        assert response.status_code == 409 and response.json()["running"]["id"] == manual.id
        runner.release("manual")
        await until(lambda: len(fake.asked) == 2)


async def test_일시정지면_건너뛰고_재개하면_다시_돈다(cli_root):
    fake = FakeTime(NOW)
    app = scheduled_app(cli_root, fake)
    runner = app.state.ctx.runner
    async with serve(app) as c:
        await until(lambda: len(fake.asked) == 1)
        paused = (await c.post(f"{OPS}/schedule/pause")).json()
        assert paused["paused"] is True and paused["scenarios"][0]["on"] is False
        assert paused["scenarios"][0]["next_run"] == MON_08.isoformat()      # 다음 슬롯은 보이되 건너뛴다
        ready = (await c.get("/ready")).json()
        assert {x["name"]: x for x in ready["checks"]}["scheduler"]["detail"].endswith("(일시정지)")

        fake.wake()
        await until(lambda: len(fake.asked) == 2)
        assert runner.recent() == []
        assert (await schedule_row(c))["last"]["status"] == "paused"

        resumed = (await c.post(f"{OPS}/schedule/resume")).json()
        assert resumed["paused"] is False and resumed["scenarios"][0]["on"] is True
        fake.wake()
        await until(lambda: len(fake.asked) == 3)
        [job] = runner.recent()
        last = (await schedule_row(c))["last"]
        assert (last["status"], last["job_id"]) == ("ok", job.id)


async def test_일시정지는_메모리뿐이라_다시_뜨면_켜진다(cli_root):
    fake = FakeTime(NOW)
    async with serve(scheduled_app(cli_root, fake)) as c:
        await c.post(f"{OPS}/schedule/pause")
    async with serve(scheduled_app(cli_root, FakeTime(NOW))) as c:
        assert (await c.get(f"{OPS}/schedule")).json()["paused"] is False


async def test_실패한_작업은_실패로_남는다(cli_root):
    fake = FakeTime(NOW)
    app = scheduled_app(cli_root, fake, runner=ScriptedRunner(cli_root, fake.clock, exit_code=1, stderr="집계 실패"))
    async with serve(app) as c:
        await until(lambda: len(fake.asked) == 1)
        fake.wake()
        await until(lambda: len(fake.asked) == 2)
        last = (await schedule_row(c))["last"]
        assert (last["status"], last["exit_code"]) == ("failed", 1)
        assert "집계 실패" in (await c.get(f"{OPS}/jobs/{last['job_id']}")).json()["stderr"]
        assert (await c.get("/ready")).status_code == 200      # 한 번 실패했다고 루프가 죽지 않는다


async def test_lifespan이_띄우고_내린다(cli_root):
    app = scheduled_app(cli_root, FakeTime(NOW))
    scheduler = app.state.ctx.scheduler
    assert not scheduler.alive()
    async with serve(app) as c:
        await settle()
        assert scheduler.alive()
        response = await c.get("/ready")
        assert response.status_code == 200
        assert {x["name"]: x["ok"] for x in response.json()["checks"]}["scheduler"] is True
    assert not scheduler.alive()


async def test_내릴_때_리포트를_기다리는_중이어도_빨리_내린다(cli_root, monkeypatch):
    """컨테이너 종료 유예 안에 끝나야 한다. 기다리던 작업은 실행기 것이라 같이 죽이지 않는다(그 작업의 상한이 정리한다)."""
    from src.presentation.api import scheduler as module
    monkeypatch.setattr(module, "_STOP_GRACE_S", 0.2)
    fake = FakeTime(NOW)
    app = scheduled_app(cli_root, fake)
    runner = app.state.ctx.runner

    async def up_and_down():
        async with serve(app):
            await until(lambda: len(fake.asked) == 1)
            runner.hold("manual")
            runner.submit(["diag", "boot"], timeout_s=10.0, job_id="manual")
            fake.wake()
            await until(lambda: app.state.ctx.scheduler.firing("daily-alarm"))   # 깼다 — 이제 자리가 비기를 기다린다
            await settle()

    # 수동 작업은 풀어 주기 전엔 안 끝난다 — 내리기가 그것을 기다렸다면 여기서 걸린다.
    await asyncio.wait_for(up_and_down(), 5)
    manual = runner.get("manual")
    assert app.state.ctx.scheduler._task.cancelled()             # 기다리던 중이었다 — 스스로 끝난 게 아니라 끊었다
    assert runner.recent() == [manual]                           # 스케줄 작업은 안 들어갔다
    assert not manual.finished                                   # 기다리던 작업을 같이 죽이지 않았다
    runner.release("manual")
    await runner.wait(manual, 5)
    assert manual.status == "ok"


async def test_루프가_죽으면_ready가_503이고_이유를_말한다(cli_root):
    async def broken(seconds, stop):
        raise RuntimeError("잠이 깨졌다")
    app = make_app(cli_root, sleeper=broken)
    async with serve(app) as c:
        await until(lambda: not app.state.ctx.scheduler.alive())
        response = await c.get("/ready")
        assert response.status_code == 503
        detail = {x["name"]: x for x in response.json()["checks"]}["scheduler"]["detail"]
        assert "멈춤 신호 없이" in detail
        assert (await c.get(f"{OPS}/schedule")).json()["running"] is False


async def test_시나리오를_못_읽으면_스케줄러가_안_뜨고_ready가_이유를_말한다(cli_root):
    (cli_root / "config" / "scenarios" / "daily-alarm.json").write_text("{깨짐", encoding="utf-8")
    async with serve(make_app(cli_root)) as c:
        response = await c.get("/ready")
        view = (await c.get(f"{OPS}/schedule")).json()
    assert response.status_code == 503
    detail = {x["name"]: x for x in response.json()["checks"]}["scheduler"]["detail"]
    assert "daily-alarm.json" in detail
    assert view["running"] is False and "daily-alarm.json" in view["error"]


async def test_돌릴_것이_없어도_스케줄러는_살아_있다(cli_root):
    """꺼 둔 것은 죽은 것이 아니다 — 시나리오 schedule을 끄면 /ready가 503이 되는 것은 틀린 경보다."""
    body = {**SCENARIO, "schedule": {**SCENARIO["schedule"], "enabled": False}}
    (cli_root / "config" / "scenarios" / "daily-alarm.json").write_text(json.dumps(body), encoding="utf-8")
    fake = FakeTime(NOW)
    app = scheduled_app(cli_root, fake)
    async with serve(app) as c:
        await settle()                       # 루프가 첫 걸음을 뗀 뒤에 본다 — 안 그러면 "바로 끝나는 루프"도 산 것으로 보인다
        assert app.state.ctx.scheduler.alive()
        assert (await c.get("/ready")).status_code == 200
        row = await schedule_row(c)
        assert (row["enabled"], row["on"], row["next_run"]) == (False, False, None)
        assert fake.asked == []
