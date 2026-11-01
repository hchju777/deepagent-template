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
    시간 초과 테스트가 그렇게 깨졌다). 여기서는 가짜 CLI가 손자를 띄워 같은 모양을 만든다 — 운영체제와 상관없이."""
    marker = tmp_path / "손자가-살아남았다"
    started = time.monotonic()
    job = await run(runner_for(cli_root), ["grandchild", "3", str(marker)], timeout_s=1.0)
    # 실패하면 실행기가 무엇을 죽였는지(stderr 끝 줄)가 같이 나온다 — Windows에서 왜 놓쳤는지를 그 줄이 말한다.
    killed = job.stderr.strip().splitlines()[-1]
    assert job.status == "timeout", killed
    assert killed.startswith("[작업 실행기] 상한 1초 — "), job.stderr
    assert "손자 시작" in job.stdout and "손자 끝" not in job.stdout, killed
    await asyncio.sleep(max(0.0, 4.5 - (time.monotonic() - started)))    # 살아 있었다면 3초 뒤에 표시를 남긴다
    assert not marker.exists(), f"손자 프로세스가 살아남아 일을 끝냈다 — {killed}"


def test_트리_순서는_자손부터이고_root가_맨_끝이다():
    from src.presentation.api.jobs import postorder
    tree = {10: [20, 30], 20: [21], 21: [22], 30: []}
    assert postorder(tree, 10) == [22, 21, 20, 30, 10]
    assert postorder({1: [2], 2: [1]}, 1) == [2, 1]            # PID 재사용으로 고리가 생겨도 끝난다
    assert postorder({}, 7) == [7]


def test_Windows는_목록을_먼저_찍고_자손부터_죽인다(monkeypatch):
    """위에서부터 죽이면 런처의 Job Object가 닫히며 중간이 먼저 죽고, 그 밑의 손자는 부모를 잃어 트리에서 안 보인다
    (`taskkill /T`가 사내 PC에서 손자를 놓쳤다). 실제 호출(ctypes)은 Windows에서만 돈다 — 여기서는 순서와 기록만 본다."""
    from src.presentation.api import jobs
    killed = []
    # 런처(100) → CLI(101) → 손자의 런처(102) → 손자(103). 생긴 시각은 부모 뒤.
    monkeypatch.setattr(jobs, "_windows_processes", lambda: [(100, 1), (101, 100), (102, 101), (103, 102), (50, 1)])
    monkeypatch.setattr(jobs, "_windows_created", lambda pid: {1: 0, 100: 10, 101: 11, 102: 12, 103: 13, 50: 5}[pid])
    monkeypatch.setattr(jobs, "_windows_terminate", lambda pid: killed.append(pid) or pid != 102)
    note = jobs._kill_windows_tree(100)
    assert killed == [103, 102, 101, 100]
    assert note == "자손부터 4개를 죽였다 (TerminateProcess 실패 PID [102])"


def test_부모보다_먼저_생긴_자식은_자손이_아니다():
    """Windows는 죽은 프로세스의 PID를 다시 준다. 부모(오래전에 죽은 PID 101)를 잃은 고아 900이 스냅샷에 "부모 101"로 남아
    있고, 그 PID를 우리 CLI가 다시 받았다 — 부모 PID만 믿으면 사람의 프로그램을 죽인다."""
    from src.presentation.api.jobs import descendants, postorder
    rows = [(100, 1), (101, 100), (900, 101), (103, 101), (104, 999)]
    created = {1: 0, 100: 10, 101: 11, 900: 3, 103: 12, 104: 20, 999: None}.get
    tree = descendants(rows, 100, created)
    assert tree == {100: [101], 101: [103]}
    assert postorder(tree, 100) == [103, 101, 100]
    # 생긴 시각을 못 읽으면 확인할 수 없으니 넣지 않는다
    assert descendants([(100, 1), (101, 100)], 100, {100: 10, 101: None}.get) == {}


def test_Windows_목록을_못_읽으면_던지지_않고_이유를_적는다(monkeypatch):
    from src.presentation.api import jobs

    def broken():
        raise OSError("스냅샷 실패")
    monkeypatch.setattr(jobs, "_windows_processes", broken)
    assert jobs._kill_windows_tree(100) == "프로세스 목록을 못 읽었다 — OSError: 스냅샷 실패"


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
