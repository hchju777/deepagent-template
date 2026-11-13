"""앱 안 스케줄러 — lifespan의 asyncio 태스크 하나.

기존 `src/schedule`(cron 파서·`run_all`)을 **그대로** 돌린다 — 시각 계산, 밀린 슬롯 건너뛰기, "발사 하나가 실패해도
루프는 안 죽는다"를 다시 만들지 않는다. 바뀌는 것은 잡이 하는 일뿐이다: 리포트를 앱 안에서 조립하지 않고 작업
실행기에 `report run --scenario <이름>`을 넣는다. 사람이 Swagger에서 누르는 것과 **같은 명령, 같은 자리**(한 번에
하나)다.

## 수동 작업이 돌고 있으면 기다린다

409로 거절당하는 것은 사람의 요청이다 — 사람은 다시 누를 수 있다. 스케줄 발사가 거절되면 그날 리포트가 안
나가고, 아무도 다시 누르지 않는다. 그래서 스케줄은 자리가 빌 때까지 기다렸다 들어간다.

## 일시정지는 메모리에만

재시작하면 다시 켜진다. 파일로 남기면 "잠깐 멈춘" 것이 몇 주 뒤까지 남는다 — 멈춘 줄 모르는 상태가 제일 위험하다.

## 실패하면 알린다(`notify.py`)

스케줄 발사가 `failed`·`timeout`이면 같은 발사 안에서 `mail.notify_to`로 한 통을 실행기에 넣는다 — 시나리오마다 달력 하루에
한 번만 **시도**한다(보내기에 실패해도 그날은 끝 — 자동 재시도 없음). interval 스케줄이 실패를 거듭하면 그만큼 메일이 가기
때문이다. 기억은 메모리뿐이다: cron 슬롯은 하루 한 번이고 재시작 뒤 그날 슬롯은 건너뛰므로, 잃어도 두 통이 되지 않는다.
결과(보냄·실패·안 보냄과 이유)는 최근 결과의 `notify`에 남는다 — 알림을 못 보낸 것도 사람이 알아야 한다.
app.json은 그때 읽는다(시나리오와 달리) — 알림 받을 사람을 고치려고 스케줄을 다시 띄울 필요는 없다.

## 시나리오는 뜰 때 한 번 읽는다

config가 바뀌면 컨테이너를 다시 띄운다(배포). 돌다가 다시 읽으면 반쯤 고친 파일을 읽는 순간이 생긴다.
"""
import asyncio
import contextlib
from pathlib import Path

from src.config.loader import ConfigError, load_app_config, load_scenarios
from src.domain.base import Clock
from src.presentation.api.jobs import REPORT_S, SHORT_S, Job, JobRunner
from src.presentation.api.notify import NOTIFY_ON, compose
from src.presentation.api.sent import report_day
from src.schedule.runner import Fire, Sleeper, run_all, wait_or_stop
from src.schedule.runner import Job as ScheduleJob

# 내릴 때 루프가 스스로 끝나기를 기다리는 상한. 컨테이너 종료 유예(보통 30초) 안이어야 한다.
_STOP_GRACE_S = 5.0


class ApiScheduler:
    def __init__(self, *, config_root: Path, runner: JobRunner, clock: Clock, env: dict[str, str] | None = None,
                 sleeper: Sleeper = wait_or_stop):
        self._config_root = config_root
        self._env = env or {}
        self._runner = runner
        self._clock = clock
        self._sleeper = sleeper
        self._scenarios: dict = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._error: str | None = None
        self._paused = False
        self._anchor = None
        self._pending: dict[str, dict] = {}
        self._last: dict[str, dict] = {}
        self._firing: set[str] = set()
        self._notified: dict[str, object] = {}         # 시나리오 → 알림을 시도한 날(달력)

    # ── 수명 ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """lifespan 시작에서 부른다. **던지지 않는다** — 시나리오를 못 읽으면 태스크 없이 그 이유를 남긴다(`/ready`가 503으로 말한다)."""
        self._anchor = self._clock()
        try:
            self._scenarios = load_scenarios(self._config_root)
        except Exception as exc:                                   # noqa: BLE001
            self._error = str(exc) if isinstance(exc, ConfigError) else f"{type(exc).__name__}: {exc}"
            return
        self._stop = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._task), _STOP_GRACE_S)
        except asyncio.TimeoutError:
            # 리포트 작업을 기다리는 중이면 여기 온다. 작업 자체는 실행기 것이라 같이 죽이지 않는다.
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        except Exception:                                          # noqa: BLE001
            pass

    def alive(self) -> bool:
        return self._task is not None and not self._task.done()

    def why_dead(self) -> str:
        if self._error:
            return self._error
        if self._task is None:
            return "스케줄러가 뜨지 않았다"
        if self._task.done() and not self._task.cancelled() and self._task.exception() is not None:
            exc = self._task.exception()
            return f"{type(exc).__name__}: {exc}"
        return "스케줄 루프가 끝났다"

    async def _loop(self) -> None:
        jobs = [ScheduleJob(name=name, spec=scenario.schedule, run=self._job(name))
                for name, scenario in self._wanted().items()]
        if not jobs:
            # 돌릴 것이 없는 것은 죽은 것이 아니다 — 멈춤 신호까지 산다.
            await self._stop.wait()
            return
        await run_all(jobs, clock=self._clock, stop=self._stop, on_fire=self._record, sleeper=self._sleeper)
        if not self._stop.is_set():
            # `run_all`은 잡 루프의 예외를 삼키고 돌아온다. 멈추라고 안 했는데 돌아왔으면 루프가 죽은 것이다 —
            # 조용히 두면 프로세스는 살아 있는데 리포트가 영원히 안 나간다.
            self._error = "스케줄 루프가 멈춤 신호 없이 끝났다"

    def _wanted(self) -> dict:
        return {name: scenario for name, scenario in self._scenarios.items()
                if scenario.enabled and scenario.schedule.enabled}

    # ── 발사 ──────────────────────────────────────────────────────────

    def _job(self, name: str):
        async def run() -> tuple[bool, str]:
            if self._paused:
                self._pending[name] = {"status": "paused"}
                return True, "일시정지 중 — 건너뛰었다"
            self._firing.add(name)
            try:
                job_id = self._runner.new_id()
                # 계기와 작업 id는 장부에 적힌다 — "오늘 것은 스케줄이 보냈나, 사람이 보냈나"를 장부만 보고 안다.
                job = await self._submit_when_free(
                    ["report", "run", "--scenario", name, "--trigger", "schedule", "--job-id", job_id], job_id,
                    timeout_s=REPORT_S)
                await job.done.wait()
                notify = await self._notify(name, job) if job.status in NOTIFY_ON else None
            finally:
                self._firing.discard(name)
            # 상태는 실행기가 종료 코드로 나눈 것 그대로 — ok·warning·already_sent·failed·timeout.
            self._pending[name] = {"status": job.status, "job_id": job.id, "exit_code": job.exit_code,
                                   "notify": notify}
            return job.status in ("ok", "warning", "already_sent"), \
                f"작업 {job.id} {job.status} (종료 코드 {job.exit_code})"
        return run

    async def _notify(self, name: str, failed: Job) -> dict:
        """실패 알림 한 통 — 무엇을 했는지(보냄·실패·안 보냄과 이유)를 돌려준다. **던지지 않는다** — 발사가 죽으면 그 실패의
        기록까지 사라진다."""
        try:
            today = self._clock().date()
            if self._notified.get(name) == today:
                return {"status": "skipped", "why": "오늘 이미 알렸다 — 시나리오마다 하루 한 통"}
            try:
                mail = load_app_config(self._config_root, env=self._env).mail
            except Exception as exc:                               # noqa: BLE001
                return {"status": "failed", "why": f"app.json을 못 읽었다 — {exc}"}
            if not mail.enabled:
                # 꺼져 있으면 `mail send`가 건너뛰고도 0으로 끝난다 — '보냈다'로 남으면 거짓말이다.
                return {"status": "skipped", "why": "mail.enabled=false"}
            if not mail.notify_to:
                return {"status": "skipped", "why": "mail.notify_to가 비어 있다"}
            scenario = self._scenarios[name]
            subject, body = compose(name=name, scenario=scenario, day=report_day(scenario, failed.started_at.date()),
                                    job=failed)
            self._notified[name] = today
            # `--to`는 config 수신자를 **덮는다** — 실패 알림이 리포트 수신자 전원에게 가지 않는다. 장부는 안 쓴다(`mail send`).
            argv = ["mail", "send", *[f"--to={address}" for address in mail.notify_to],
                    f"--subject={subject}", f"--body={body}"]
            job = await self._submit_when_free(argv, self._runner.new_id(), timeout_s=SHORT_S)
            await job.done.wait()
            if job.status == "ok":
                return {"status": "sent", "to": len(mail.notify_to), "job_id": job.id, "at": self._clock().isoformat()}
            why = next((line for line in reversed(job.stderr.splitlines()) if line.strip()), "")
            return {"status": "failed", "to": len(mail.notify_to), "job_id": job.id,
                    "why": f"{job.status} (종료 코드 {job.exit_code}) — {why}".rstrip(" —")}
        except Exception as exc:                                   # noqa: BLE001
            return {"status": "failed", "why": f"{type(exc).__name__}: {exc}"}

    async def _submit_when_free(self, argv: list[str], job_id: str, *, timeout_s: float) -> Job:
        while True:
            job, busy = self._runner.submit(argv, timeout_s=timeout_s, job_id=job_id)
            if job is not None:
                return job
            # 빈 뒤에 사람이 먼저 잡을 수 있다 — 그러면 다시 기다린다.
            await busy.done.wait()

    def _record(self, fire: Fire) -> None:
        info = self._pending.pop(fire.job, {})
        self._last[fire.job] = {
            "status": info.get("status", "ok" if fire.ok else "failed"),
            "scheduled": fire.scheduled.isoformat(), "started": fire.started.isoformat(),
            "took_s": round(fire.took_seconds, 3), "job_id": info.get("job_id"),
            "exit_code": info.get("exit_code"), "detail": fire.detail, "notify": info.get("notify")}

    # ── 보기·조작 ──────────────────────────────────────────────────────

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def scenarios(self) -> dict:
        return dict(self._scenarios)

    def last(self, name: str) -> dict | None:
        return self._last.get(name)

    def firing(self, name: str) -> bool:
        return name in self._firing

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def view(self) -> dict:
        now = self._clock()
        rows = []
        for name, scenario in self._scenarios.items():
            spec = scenario.schedule
            wanted = scenario.enabled and spec.enabled
            upcoming = spec.next_after(now, anchor=self._anchor) if wanted and self._anchor else None
            rows.append({"name": name, "enabled": wanted, "on": wanted and not self._paused,
                         "kind": spec.kind, "cron": spec.cron if spec.kind == "cron" else None,
                         "schedule": spec.describe(),
                         "next_run": upcoming.isoformat() if upcoming else None,
                         "last": self._last.get(name)})
        return {"running": self.alive(), "paused": self._paused,
                "error": None if self.alive() else self.why_dead(), "scenarios": rows}
