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

## 왜 트리째 죽이는가

자식만 죽이면 안 된다. 진짜 일을 하는 프로세스가 손자이고 파이프를 물려받으면 손자가 살아서 일을 끝내고, 3.11의
`proc.wait()`는 프로세스가 끝나도 **파이프가 다 닫혀야** 돌아온다(3.13은 바로 돌아온다) — 상한이 안 듣고, 최악이면 자리가
영원히 안 비어 모든 요청이 409가 된다. POSIX는 새 세션으로 띄워 **그룹째** 죽인다.

Windows는 **죽이기 전에 프로세스 목록을 한 번 찍어 자손부터** 죽인다. `taskkill /T`는 사내 PC에서 손자를 놓쳤다(11-01):
venv의 `python.exe`는 진짜 파이썬을 Job Object에 넣어 띄우는 런처라, 위에서부터 죽이면 런처가 죽는 순간 그 밑이 job째
먼저 죽고, 더 밑(손자의 런처·손자)은 부모를 잃어 트리에서 안 보인다. 미리 찍어 두면 부모가 먼저 죽어도 누가 누구의
자손인지 안다.

무엇을 죽였는지는 작업의 stderr 끝에 한 줄로 남는다 — 상한에 걸린 작업이 왜 안 죽었는지를 사람이 볼 수 있게. 죽인 뒤
기다리는 것에도 상한을 둔다.
"""
import asyncio
import contextlib
import os
import shlex
import signal
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
        # POSIX는 새 세션 — 그 그룹째 죽일 수 있게(`_kill_tree`). Windows에는 이 인자가 없다.
        session = {"start_new_session": True} if os.name == "posix" else {}
        try:
            proc = await asyncio.create_subprocess_exec(
                *job.command, cwd=self._root, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **session)
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
            killed = await _kill_tree(proc)
            # 트리를 다 못 죽였으면 파이프가 안 닫혀 3.11의 wait()가 영원히 안 온다 — 기다림에도 상한을 둔다.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), _DRAIN_S)
        _, pending = await asyncio.wait(readers, timeout=_DRAIN_S)
        for reader in pending:
            reader.cancel()
        if pending:
            # 못 죽인 손자가 파이프를 쥐고 있다 — transport가 스스로는 안 닫혀 그 손자가 끝날 때까지 fd가 샌다. 포기한 쪽이 닫는다.
            # (`Process`에 닫는 공개 표면이 없다.)
            with contextlib.suppress(Exception):
                proc._transport.close()
        job.exit_code = proc.returncode
        job.stdout = _tail(out, self._max_output)
        job.stderr = _tail(err, self._max_output)
        if timed_out:
            job.stderr += f"\n[작업 실행기] 상한 {job.timeout_s:g}초 — {killed}"
        job.status = "timeout" if timed_out else ("ok" if proc.returncode == 0 else "failed")


async def _kill_tree(proc) -> str:
    """자식과 그 밑을 전부 죽이고 무엇을 했는지 한 줄로 돌려준다. **던지지 않는다** — 못 죽여도 부르는 쪽은 상한을 두고 기다린다."""
    if sys.platform == "win32":
        note = await asyncio.to_thread(_kill_windows_tree, proc.pid)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            note = f"프로세스 그룹 {proc.pid}을 죽였다"
        except Exception as exc:                                   # noqa: BLE001
            note = f"그룹째 못 죽였다 — {type(exc).__name__}: {exc}"
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.kill()                                                # 위가 안 들었을 때의 마지막 수
    return note


def postorder(children: dict[int, list[int]], root: int) -> list[int]:
    """root와 그 자손 — **자손부터**(깊은 것 먼저, root가 맨 끝). 고리(PID 재사용 등)가 있어도 끝난다."""
    order: list[int] = []
    seen = {root}
    stack = [(root, iter(children.get(root, ())))]
    while stack:
        pid, rest = stack[-1]
        child = next(rest, None)
        if child is None:
            stack.pop()
            order.append(pid)
        elif child not in seen:
            seen.add(child)
            stack.append((child, iter(children.get(child, ()))))
    return order


def descendants(rows: list[tuple[int, int]], root: int, created) -> dict[int, list[int]]:
    """(PID, 부모 PID) 목록에서 root의 자손만 — 부모 → 자식. **부모보다 먼저 생긴 자식은 뺀다.**

    Windows는 죽은 프로세스의 PID를 다른 프로세스에 다시 준다. 스냅샷의 부모 PID만 믿으면, 부모가 오래전에 죽은 **우리와
    상관없는 고아**를 그 PID를 다시 받은 우리 CLI의 자식으로 보고 죽인다(사람의 편집기일 수 있다). 진짜 자식은 부모가 생긴
    뒤에 생긴다 — psutil과 같은 규칙. `created(pid)`가 None이면(못 열면) 확인할 수 없으니 넣지 않는다.
    """
    raw: dict[int, list[int]] = {}
    for pid, parent in rows:
        if pid != parent:
            raw.setdefault(parent, []).append(pid)
    born: dict[int, int | None] = {}

    def birth(pid: int):
        if pid not in born:
            born[pid] = created(pid)
        return born[pid]
    found: dict[int, list[int]] = {}
    seen, todo = {root}, [root]
    while todo:
        parent = todo.pop()
        for child in raw.get(parent, ()):
            if child in seen:
                continue
            child_born, parent_born = birth(child), birth(parent)
            if child_born is None or parent_born is None or child_born < parent_born:
                continue
            seen.add(child)
            found.setdefault(parent, []).append(child)
            todo.append(child)
    return found


def _kill_windows_tree(root: int) -> str:
    try:
        order = postorder(descendants(_windows_processes(), root, _windows_created), root)
    except Exception as exc:                                       # noqa: BLE001
        return f"프로세스 목록을 못 읽었다 — {type(exc).__name__}: {exc}"
    missed = [pid for pid in order if not _windows_terminate(pid)]
    # 못 죽인 것 중에는 이미 스스로 끝난 것(손자가 죽어 따라 끝난 런처)도 있다 — 판정하지 않고 그대로 적는다.
    return f"자손부터 {len(order)}개를 죽였다" + (f" (TerminateProcess 실패 PID {missed})" if missed else "")


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


def _windows_processes() -> list[tuple[int, int]]:
    """(PID, 부모 PID) — Toolhelp 스냅샷 한 장. **죽이기 전에** 찍는다: 부모가 먼저 죽어도 자식 항목에는 부모 PID가 남는다."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):                                 # PROCESSENTRY32W
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel32 = _kernel32()
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)          # TH32CS_SNAPPROCESS
    if snapshot in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    rows: list[tuple[int, int]] = []
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            rows.append((entry.th32ProcessID, entry.th32ParentProcessID))
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return rows


def _windows_created(pid: int) -> int | None:
    """프로세스가 생긴 시각(FILETIME 정수). 못 열면 None."""
    import ctypes
    from ctypes import wintypes

    kernel32 = _kernel32()
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    handle = kernel32.OpenProcess(0x1000, False, pid)              # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)


def _windows_terminate(pid: int) -> bool:
    from ctypes import wintypes

    kernel32 = _kernel32()
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    handle = kernel32.OpenProcess(0x0001, False, pid)              # PROCESS_TERMINATE
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


async def _drain(stream, sink: bytearray) -> None:
    while chunk := await stream.read(65536):
        sink.extend(chunk)


def _tail(data: bytearray, limit: int) -> str:
    if len(data) <= limit:
        return data.decode("utf-8", errors="replace")
    cut = len(data) - limit
    return f"[앞 {cut}바이트 생략]\n" + bytes(data[cut:]).decode("utf-8", errors="replace")
