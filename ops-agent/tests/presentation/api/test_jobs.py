"""작업 실행기 — 셸 없이, 한 번에 하나, 시간을 넘기면 죽이고 그때까지의 출력을 남긴다."""
import asyncio
import json
import os
import shlex
import sys
import time

import pytest

from src.presentation.api.jobs import (DOCTOR_S, REPO_ROOT, REPORT_S, SHORT_S,
                                       JobRunner)

from tests.presentation.api.conftest import NOW


def runner_for(root, **kwargs) -> JobRunner:
    return JobRunner(root=root, clock=lambda: NOW, **kwargs)


async def run(runner: JobRunner, argv, *, timeout_s=30.0, wait_s=30.0):
    job, busy = runner.submit(argv, timeout_s=timeout_s)
    assert busy is None, f"돌고 있는 작업이 있다 — {busy}"
    await runner.wait(job, wait_s)
    return job


def test_리포_루트는_src와_config가_나란히_있는_폴더다():
    """컨테이너에서는 `/`다. 파일 위치에서 거꾸로 센다 — cwd에 기대면 uvicorn을 어디서 띄웠냐에 따라 바뀐다."""
    assert (REPO_ROOT / "src" / "__main__.py").is_file()
    assert (REPO_ROOT / "config" / "app.json").is_file()


def test_시간_상한은_짧은_것_180_doctor_60_report_1800():
    assert (SHORT_S, DOCTOR_S, REPORT_S) == (180.0, 60.0, 1800.0)


def test_명령은_셸_없이_python_u_m_src_다(cli_root):
    runner = runner_for(cli_root, python="/opt/py")
    assert runner.command(["report", "run", "--dry-run"]) == [
        "/opt/py", "-u", "-m", "src", "report", "run", "--dry-run"]


async def test_성공하면_ok와_종료코드_0_그리고_stdout(cli_root):
    job = await run(runner_for(cli_root), ["echo", "a b", "; rm -rf /"])
    assert (job.status, job.exit_code) == ("ok", 0)
    printed = json.loads(job.stdout)
    # 셸이 없으므로 공백·세미콜론이 든 인자도 그대로 한 칸이다.
    assert printed["argv"] == ["echo", "a b", "; rm -rf /"]
    assert os.path.samefile(printed["cwd"], cli_root)      # 글자 비교는 Windows의 대소문자·짧은 이름에서 갈린다
    assert printed["io"] == "utf-8"
    assert job.took_s is not None and job.started_at == NOW


async def test_실패하면_failed와_종료코드와_stderr(cli_root):
    job = await run(runner_for(cli_root), ["exit", "3"])
    assert (job.status, job.exit_code) == ("failed", 3)
    assert "나가는 중" in job.stderr


async def test_시간을_넘기면_죽이고_그때까지의_출력을_남긴다(cli_root):
    job = await run(runner_for(cli_root), ["sleep", "30"], timeout_s=1.0)
    assert job.status == "timeout"
    assert job.exit_code is not None and job.exit_code != 0
    # flush 없이 찍은 줄이다 — `-u`가 아니면 버퍼째 사라진다.
    assert "시작" in job.stdout and "끝" not in job.stdout
    assert "에러 줄" in job.stderr
    assert job.took_s < 10


async def test_시간을_넘기면_손자_프로세스까지_죽인다(cli_root, tmp_path):
    """자식만 죽이면 안 된다. Windows venv의 `python.exe`는 진짜 파이썬을 **자식으로 띄우는 런처**라, 런처만 죽고 CLI는 계속
    돈다 — 파이프를 쥔 채로. 그러면 3.11의 `proc.wait()`는 파이프가 다 닫힐 때까지 안 돌아와 상한이 안 듣는다(사내 PC에서
    시간 초과 테스트가 그렇게 깨졌다). 여기서는 가짜 CLI가 손자를 띄워 같은 모양을 만든다 — 운영체제와 상관없이.

    상한은 **손자가 확실히 뜬 뒤**여야 한다 — 사내 PC에서 1초로는 파이썬 넷(런처·CLI·런처·손자)이 다 뜨기 전에 죽였다.
    """
    marker = tmp_path / "손자가-살아남았다"
    started = time.monotonic()
    job = await run(runner_for(cli_root), ["grandchild", "4", str(marker)], timeout_s=3.0)
    # 실패하면 실행기가 무엇을 죽였는지(stderr 끝 줄)와 stdout이 같이 나온다 — Windows에서 왜 놓쳤는지를 그것이 말한다.
    killed = job.stderr.strip().splitlines()[-1]
    seen = f"{killed} · stdout={job.stdout!r}"
    assert job.status == "timeout", seen
    assert killed.startswith("[작업 실행기] 상한 3초 — "), job.stderr
    assert "손자 시작" in job.stdout, f"손자가 상한 안에 뜨지도 못했다 — 이 시험은 아무것도 안 본 것이다: {seen}"
    assert "손자 끝" not in job.stdout, seen
    await asyncio.sleep(max(0.0, 6.0 - (time.monotonic() - started)))    # 살아 있었다면 뜬 뒤 4초에 표시를 남긴다
    assert not marker.exists(), f"손자 프로세스가 살아남아 일을 끝냈다 — {seen}"


def test_자손은_깊은_것부터이고_고리가_있어도_끝난다():
    from src.presentation.api.jobs import descendants
    rows = [(20, 10), (30, 10), (21, 20), (22, 21), (10, 22)]          # 10 → 20 → 21 → 22 → (10: PID 재사용 고리)
    order = descendants(rows, {10}, lambda pid: pid)
    assert order[:2] == [22, 21] and set(order) == {20, 21, 22, 30}
    assert descendants([], {7}, lambda pid: pid) == []


class FakeWindows:
    """프로세스 표 — 찍으면 산 것만 나오고, 죽은 것은 다시 열 수 없다(생긴 시각을 못 읽는다). `on_kill`로 "죽이는 중에 막
    생기는" 손자를 흉내 낸다."""

    def __init__(self, table, *, fail=(), on_kill=None):
        self.table = dict(table)                     # pid → (부모 pid, 생긴 시각)
        self.fail, self.on_kill, self.killed, self.snaps = set(fail), on_kill or {}, [], 0

    def processes(self):
        self.snaps += 1
        return [(pid, parent) for pid, (parent, _) in self.table.items()]

    def created(self, pid):
        return self.table[pid][1] if pid in self.table else None

    def terminate(self, pid):
        self.killed.append(pid)
        if pid in self.fail:
            return False
        self.table.pop(pid, None)
        self.table.update(self.on_kill.pop(pid, {}))
        return True

    def install(self, monkeypatch, jobs):
        monkeypatch.setattr(jobs, "_windows_processes", self.processes)
        monkeypatch.setattr(jobs, "_windows_created", self.created)
        monkeypatch.setattr(jobs, "_windows_terminate", self.terminate)


def test_Windows는_목록을_먼저_찍고_자손부터_죽인다(monkeypatch):
    """위에서부터 죽이면 런처의 Job Object가 닫히며 중간이 먼저 죽고, 그 밑의 손자는 부모를 잃어 트리에서 안 보인다
    (`taskkill /T`가 사내 PC에서 손자를 놓쳤다). 실제 호출(ctypes)은 Windows에서만 돈다 — 여기서는 순서와 기록만 본다."""
    from src.presentation.api import jobs
    # 런처(100) → CLI(101) → 손자의 런처(102) → 손자(103). 50은 상관없는 프로세스.
    world = FakeWindows({1: (0, 0), 100: (1, 10), 101: (100, 11), 102: (101, 12), 103: (102, 13), 50: (1, 5)},
                        fail={102})
    world.install(monkeypatch, jobs)
    note = jobs._kill_windows_tree(100, sleep=lambda s: None)
    assert world.killed == [103, 102, 101, 100]
    assert note == "자손부터 4개를 죽였다(2번 훑음) (TerminateProcess 실패 PID [102])"


def test_Windows는_찍은_뒤에_생긴_손자도_다시_훑어_죽인다(monkeypatch):
    """사내 PC: 상한에 걸렸을 때 손자는 아직 안 떠 있었고(3개만 찍혔다), 손자의 런처를 죽이는 사이에 손자가 생겨 살아남았다.
    그 손자의 부모(런처)는 이미 죽어 생긴 시각을 다시 못 읽는다 — 처음 본 때 기억한 시각으로 자식임을 확인한다."""
    from src.presentation.api import jobs
    world = FakeWindows({1: (0, 0), 100: (1, 10), 101: (100, 11), 102: (101, 12)},
                        on_kill={102: {103: (102, 13)}})
    world.install(monkeypatch, jobs)
    note = jobs._kill_windows_tree(100, sleep=lambda s: None)
    assert world.killed == [102, 101, 100, 103]
    assert 103 not in world.table
    assert note == "자손부터 4개를 죽였다(3번 훑음)"


def test_Windows_훑기는_상한이_있다(monkeypatch):
    """죽일 때마다 새 자손이 생겨도(포크 폭탄) 끝없이 돌지 않는다."""
    from src.presentation.api import jobs
    spawn = {pid: {pid + 1: (pid, pid)} for pid in range(100, 200)}
    world = FakeWindows({1: (0, 0), 100: (1, 100)}, on_kill=spawn)
    world.install(monkeypatch, jobs)
    note = jobs._kill_windows_tree(100, sleep=lambda s: None)
    assert world.snaps == jobs._KILL_PASSES
    assert note.endswith(f"({jobs._KILL_PASSES}번 훑음)")


def test_부모보다_먼저_생긴_자식은_자손이_아니다():
    """Windows는 죽은 프로세스의 PID를 다시 준다. 부모(오래전에 죽은 PID 101)를 잃은 고아 900이 스냅샷에 "부모 101"로 남아
    있고, 그 PID를 우리 CLI가 다시 받았다 — 부모 PID만 믿으면 사람의 프로그램을 죽인다."""
    from src.presentation.api.jobs import descendants
    rows = [(100, 1), (101, 100), (900, 101), (103, 101), (104, 999)]
    created = {1: 0, 100: 10, 101: 11, 900: 3, 103: 12, 104: 20, 999: None}.get
    assert descendants(rows, {100}, created) == [103, 101]
    # 생긴 시각을 못 읽으면 확인할 수 없으니 넣지 않는다
    assert descendants([(100, 1), (101, 100)], {100}, {100: 10, 101: None}.get) == []


def test_Windows_목록을_못_읽으면_던지지_않고_이유를_적는다(monkeypatch):
    from src.presentation.api import jobs

    def broken():
        raise OSError("스냅샷 실패")
    monkeypatch.setattr(jobs, "_windows_processes", broken)
    assert jobs._kill_windows_tree(100) == "프로세스 목록을 못 읽었다 — OSError: 스냅샷 실패 (그 전까지 0개)"


@pytest.mark.skipif(sys.platform == "win32", reason="새 세션으로 빠져나가는 손자는 POSIX에서만 만들 수 있다")
async def test_못_죽인_손자가_파이프를_쥐고_있어도_자리는_상한_안에_빈다(cli_root, monkeypatch):
    """마지막 방어선. 3.11의 `wait()`는 파이프가 닫혀야 돌아오므로, 죽인 뒤의 기다림에 상한이 없으면 자리가 영원히 안 빈다 —
    그러면 모든 요청이 409다."""
    from src.presentation.api import jobs
    monkeypatch.setattr(jobs, "_DRAIN_S", 0.5)
    runner = runner_for(cli_root)
    job = await run(runner, ["escaped", "6"], timeout_s=0.5)
    assert job.status == "timeout"
    assert job.took_s < 4
    assert runner.running() is None


async def test_한글_출력이_깨지지_않는다(cli_root):
    job = await run(runner_for(cli_root), ["hangul"])
    assert job.stdout.strip() == "한글 — 출력"


async def test_돌고_있으면_새_작업을_안_받고_돌고_있는_작업을_돌려준다(cli_root):
    runner = runner_for(cli_root)
    first, _ = runner.submit(["sleep", "30"], timeout_s=1.0)
    started, busy = runner.submit(["echo"], timeout_s=30.0)
    assert started is None and busy is first
    assert runner.running() is first
    await runner.wait(first, 30)
    assert runner.running() is None
    again, busy = runner.submit(["echo"], timeout_s=30.0)
    assert busy is None and again is not None
    await runner.wait(again, 30)


async def test_기다림이_짧으면_돌고_있는_채로_돌려준다(cli_root):
    runner = runner_for(cli_root)
    job, _ = runner.submit(["sleep", "30"], timeout_s=1.5)
    await runner.wait(job, 0.1)
    assert job.status == "running" and job.exit_code is None
    await runner.wait(job, 30)
    assert job.status == "timeout"


async def test_최근_것만_새것부터_남긴다(cli_root):
    runner = runner_for(cli_root, keep=3)
    ids = []
    for i in range(5):
        ids.append((await run(runner, ["echo", str(i)])).id)
    assert [job.id for job in runner.recent()] == ids[::-1][:3]
    assert runner.get(ids[0]) is None and runner.get(ids[-1]) is not None


async def test_기본_보관은_50개다(cli_root):
    runner = runner_for(cli_root)
    for i in range(52):
        job, _ = runner.submit(["exit", "0"], timeout_s=30.0)
        await runner.wait(job, 30)
    assert len(runner.recent()) == 50


async def test_못_띄우면_던지지_않고_failed로_남기고_자리를_비운다(cli_root):
    runner = runner_for(cli_root, python=str(cli_root / "없는-파이썬"))
    job = await run(runner, ["echo"])
    assert job.status == "failed" and job.exit_code is None
    assert "없는-파이썬" in job.stderr or "FileNotFoundError" in job.stderr
    assert runner.running() is None


async def test_출력이_너무_크면_끝을_남기고_앞을_잘랐다고_적는다(cli_root):
    runner = runner_for(cli_root, max_output=1000)
    job = await run(runner, ["big", "5000"])
    assert job.status == "ok"
    assert job.stdout.endswith("x" * 900)
    assert "앞 4000바이트 생략" in job.stdout


async def test_보기는_명령_전체와_상태를_담는다(cli_root):
    # 지금 도는 파이썬으로 — `python3`이라는 이름은 Windows에 없거나 스토어 별칭이다.
    job = await run(runner_for(cli_root), ["exit", "0"], timeout_s=12.0)
    view = job.view()
    assert view["command"] == shlex.join([sys.executable, "-u", "-m", "src", "exit", "0"])
    assert view["argv"] == ["exit", "0"]
    assert view["status"] == "ok" and view["exit_code"] == 0
    assert view["timeout_s"] == 12.0
    assert view["started_at"] == NOW.isoformat()


@pytest.mark.skipif(sys.platform == "win32", reason="kill 신호 모양이 다르다")
async def test_시간_초과로_죽인_뒤에도_자리가_비워진다(cli_root):
    runner = runner_for(cli_root)
    job = await run(runner, ["sleep", "30"], timeout_s=0.5)
    assert job.status == "timeout"
    assert runner.running() is None
    await asyncio.sleep(0)
