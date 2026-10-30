"""작업 실행기 — CLI 명령을 하위 프로세스로, **한 번에 하나**.

## 왜 함수 호출이 아니라 하위 프로세스인가

API가 부르는 일(리포트·메일·doctor·LLM 확인)은 전부 이미 CLI 명령이고, 사내에서 그 명령으로 확인됐다. 같은 일을
앱 안에서 다시 조립하면 "CLI로는 되는데 API로는 안 된다"가 생긴다 — 조립이 두 벌이면 언젠가 하나가 빠뜨린다
(규율 8과 같은 이유). 하위 프로세스면 API가 내는 명령이 사람이 터미널에 치는 명령과 **글자까지 같다.**

셸을 거치지 않는다(`create_subprocess_exec`). 인자에 공백·`;`·따옴표가 들어도 그대로 한 칸이다.

## 왜 한 번에 하나인가

리포트 한 번이 28개 법인을 읽고 LLM을 부른다. 둘이 겹치면 대상 시스템과 게이트웨이에 두 배가 가고, 같은 날짜의
파일을 둘이 쓴다. 겹침을 막는 것은 큐가 아니라 거절(409)이다 — 큐에 쌓아 두면 Swagger에서 두 번 누른 것이
30분 뒤에 메일 두 통이 된다.

## 왜 `-u`인가

시간을 넘겨 죽일 때, 자식의 출력이 파이썬 버퍼에 남아 있으면 **같이 사라진다.** "어디까지 갔다가 멈췄나"가
제일 필요한 순간에 빈 stdout이 남는다. `-u`면 찍은 것은 이미 파이프에 있다.
"""
import asyncio
import os
import shlex
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from src.domain.base import Clock

# `src/presentation/api/jobs.py`에서 세 칸 위 — `src/`와 `config/`가 나란히 있는 폴더. 컨테이너에서는 `/`다.
# cwd를 쓰지 않는다: uvicorn을 어디서 띄웠느냐에 따라 CLI가 엉뚱한 `config/`·`.env`를 읽는다.
REPO_ROOT = Path(__file__).resolve().parents[3]

SHORT_S = 180.0
DOCTOR_S = 60.0
# 28개 법인 집계 + LLM 서술. 사내 원본도 1800초다.
REPORT_S = 1800.0

KEEP = 50
# 스트림마다 남길 바이트 상한. 넘으면 **끝을** 남긴다 — 실패의 이유는 보통 마지막 줄에 있다.
MAX_OUTPUT = 512 * 1024
# 죽인 뒤 파이프가 닫히기를 기다리는 상한. 자식이 손자를 남겼으면 파이프가 안 닫힌다 — 그래도 자리는 비워야 한다.
_DRAIN_S = 5.0


@dataclass
class Job:
    id: str
    argv: list[str]
    command: list[str]
    timeout_s: float
    started_at: datetime
    status: str = "running"            # running | ok | failed | timeout
    exit_code: int | None = None
    took_s: float | None = None
    stdout: str = ""
    stderr: str = ""
    done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def finished(self) -> bool:
        return self.status != "running"

    def view(self) -> dict:
        return {"id": self.id, "command": shlex.join(self.command), "argv": list(self.argv),
                "status": self.status, "started_at": self.started_at.isoformat(),
                "took_s": self.took_s, "exit_code": self.exit_code, "timeout_s": self.timeout_s,
                "stdout": self.stdout, "stderr": self.stderr}


class JobRunner:
    def __init__(self, *, root: Path, clock: Clock, python: str = sys.executable,
                 ticker: Callable[[], float] = time.monotonic, keep: int = KEEP,
                 max_output: int = MAX_OUTPUT, new_id: Callable[[], str] | None = None):
        self._root = root
        self._clock = clock
        self._python = python
        self._ticker = ticker
        self._max_output = max_output
        self._new_id = new_id or (lambda: uuid.uuid4().hex[:12])
        self._jobs: deque[Job] = deque(maxlen=keep)
        self._current: Job | None = None
        self._tasks: set[asyncio.Task] = set()

    def command(self, argv: list[str]) -> list[str]:
        return [self._python, "-u", "-m", "src", *argv]

    def running(self) -> Job | None:
        return self._current if self._current is not None and not self._current.finished else None

    def recent(self) -> list[Job]:
        return list(self._jobs)

    def get(self, job_id: str) -> Job | None:
        return next((job for job in self._jobs if job.id == job_id), None)

    def submit(self, argv: list[str], *, timeout_s: float) -> tuple[Job | None, Job | None]:
        """(띄운 작업, None) 또는 (None, 돌고 있는 작업). **던지지 않는다** — 거절도 값이다.

        확인과 자리 잡기 사이에 await가 없다 — 같은 루프의 두 요청이 둘 다 비었다고 보는 틈이 없다.
        """
        busy = self.running()
        if busy is not None:
            return None, busy
        job = Job(id=self._new_id(), argv=list(argv), command=self.command(argv),
                  timeout_s=timeout_s, started_at=self._clock())
        self._jobs.appendleft(job)
        self._current = job
        task = asyncio.get_running_loop().create_task(self._run(job))
        self._tasks.add(task)                       # 참조를 안 쥐면 태스크가 GC로 사라질 수 있다
        task.add_done_callback(self._tasks.discard)
        return job, None

    async def wait(self, job: Job, wait_s: float) -> Job:
        """`wait_s`까지만 기다린다. 끝났든 아니든 그 작업을 돌려준다."""
        try:
            await asyncio.wait_for(job.done.wait(), wait_s)
        except asyncio.TimeoutError:
            pass
        return job

    async def _run(self, job: Job) -> None:
        started = self._ticker()
        try:
            await self._spawn_and_wait(job)
        except Exception as exc:                                   # noqa: BLE001
            # 최외곽 방어선. 여기서 던지면 상태가 영원히 running이고 자리가 안 비워진다 — 모든 요청이 409가 된다.
            job.status = "failed"
            job.stderr = (job.stderr + f"\n[작업 실행기] {type(exc).__name__}: {exc}").lstrip()
        finally:
            job.took_s = round(self._ticker() - started, 3)
            job.done.set()

    async def _spawn_and_wait(self, job: Job) -> None:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            proc = await asyncio.create_subprocess_exec(
                *job.command, cwd=self._root, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except Exception as exc:                                   # noqa: BLE001
            job.status = "failed"
            job.stderr = f"[작업 실행기] 못 띄웠다 — {type(exc).__name__}: {exc}"
            return
        out, err = bytearray(), bytearray()
        readers = [asyncio.ensure_future(_drain(proc.stdout, out)),
                   asyncio.ensure_future(_drain(proc.stderr, err))]
        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), job.timeout_s)
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            await proc.wait()
        _, pending = await asyncio.wait(readers, timeout=_DRAIN_S)
        for reader in pending:
            reader.cancel()
        job.exit_code = proc.returncode
        job.stdout = _tail(out, self._max_output)
        job.stderr = _tail(err, self._max_output)
        job.status = "timeout" if timed_out else ("ok" if proc.returncode == 0 else "failed")


async def _drain(stream, sink: bytearray) -> None:
    while chunk := await stream.read(65536):
        sink.extend(chunk)


def _tail(data: bytearray, limit: int) -> str:
    if len(data) <= limit:
        return data.decode("utf-8", errors="replace")
    cut = len(data) - limit
    return f"[앞 {cut}바이트 생략]\n" + bytes(data[cut:]).decode("utf-8", errors="replace")
