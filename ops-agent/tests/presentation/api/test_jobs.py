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


async def run(runner: JobRunner, argv, *, timeout_s=30.0):
    """끝날 때까지 기다린다 — **상한보다 길게**(상한 + 여유). 기다림이 상한보다 짧으면 바쁜 기계에서 상한이 다 차기 전에
    기다림이 먼저 끝나 `running`을 본다(사내 5단계 검증: 기동 11초 → 상한 33.7초인데 30초만 기다렸다)."""
    job, busy = runner.submit(argv, timeout_s=timeout_s)
    assert busy is None, f"돌고 있는 작업이 있다 — {busy}"
    await runner.wait(job, timeout_s + 60)
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
    job = await run(runner_for(cli_root), ["exit", "5"])
    assert (job.status, job.exit_code) == ("failed", 5)
    assert "나가는 중" in job.stderr


@pytest.mark.parametrize("code,status", [(0, "ok"), (3, "warning"), (4, "already_sent"), (1, "failed"), (2, "failed")])
async def test_종료_코드를_CLI의_표대로_상태로_나눈다(cli_root, code, status):
    """일부 법인을 못 읽고 나간 리포트가 `failed`로 보이면 실패와 경고를 못 가른다(사내 11-01). 2는 argparse의 인자 오류다."""
    job = await run(runner_for(cli_root), ["exit", str(code)])
    assert (job.status, job.exit_code) == (status, code)


async def test_작업_id를_미리_받아_명령에_실을_수_있다(cli_root):
    runner = runner_for(cli_root)
    job_id = runner.new_id()
    job, _ = runner.submit(["echo", "--job-id", job_id], timeout_s=30.0, job_id=job_id)
    await runner.wait(job, 30)
    assert job.id == job_id and json.loads(job.stdout)["argv"] == ["echo", "--job-id", job_id]


async def test_끝나면_끝났다고_알리기_전에_뒤처리를_부른다(cli_root):
    """재발송은 202로 먼저 돌아가도 끝난 뒤 장부를 써야 한다 — 기다린 사람이 끝을 보면 장부도 이미 있어야 한다."""
    runner = runner_for(cli_root)
    seen = []

    def after(job):
        seen.append((job.status, job.done.is_set()))
        raise RuntimeError("뒤처리가 던져도")
    job, _ = runner.submit(["exit", "0"], timeout_s=30.0, on_done=after)
    await runner.wait(job, 30)
    assert seen == [("ok", False)]
    assert "끝난 뒤 처리 실패 — RuntimeError" in job.stderr and runner.running() is None


async def test_시간을_넘기면_죽이고_그때까지의_출력을_남긴다(cli_root, monkeypatch):
    """상한은 **자식이 뜨고 첫 줄을 찍는 시간보다 길어야** 한다 — 아니면 찍기 전에 죽어 이 시험이 아무것도 못 본다. 사내 PC
    (Windows venv)에서 1초로는 stdout이 비었다(11-02). 이 기계에서 자식이 뜨는 데 걸리는 시간을 먼저 재서 그 몇 배로 잡는다.

    `PYTHONUNBUFFERED`를 지운다 — 개발 컨테이너에 전역으로 걸려 있어, 있으면 `-u`가 없어도 출력이 안 사라져 이 시험이
    `-u`를 못 본다(그렇게 한동안 초록이었다). 실행기는 자식에게 os.environ을 그대로 넘긴다."""
    monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)
    runner = runner_for(cli_root)
    startup = (await run(runner, ["hangul"])).took_s
    limit = max(1.0, round(startup * 3 + 0.5, 1))
    # 자식은 상한보다 한참 더 잔다 — 30초 고정이면 상한이 30초 근처일 때 자식이 스스로 끝나 `ok`가 될 수 있다.
    job = await run(runner, ["sleep", str(limit + 60)], timeout_s=limit)
    seen = f"기동 {startup:.2f}초 · 상한 {limit:g}초 · stdout={job.stdout!r} · stderr={job.stderr!r}"
    assert job.status == "timeout", seen
    assert job.exit_code is not None and job.exit_code != 0, seen
    # flush 없이 찍은 줄이다 — `-u`가 아니면 버퍼째 사라진다.
    assert "시작" in job.stdout and "끝" not in job.stdout, seen
    assert "에러 줄" in job.stderr, seen
    assert job.took_s < limit + 30, seen                         # 자식이 스스로 끝나는 때(상한 + 60)보다 한참 전 — 죽였다


@pytest.mark.skipif(sys.platform == "win32", reason="운영(Linux)의 성질이다 — Windows는 자식만 죽인다(jobs.py docstring)")
async def test_시간을_넘기면_손자_프로세스까지_죽인다(cli_root, tmp_path):
    """운영(Linux)에서는 그룹째 죽인다 — 파이프를 물려받은 손자가 살아서 일을 끝내면 안 된다. 가짜 CLI가 손자를 띄운다."""
    marker = tmp_path / "손자가-살아남았다"
    started = time.monotonic()
    job = await run(runner_for(cli_root), ["grandchild", "3", str(marker)], timeout_s=1.0)
    assert job.status == "timeout"
    assert "손자 시작" in job.stdout and "손자 끝" not in job.stdout
    await asyncio.sleep(max(0.0, 4.5 - (time.monotonic() - started)))    # 살아 있었다면 3초 뒤에 표시를 남긴다
    assert not marker.exists(), "손자 프로세스가 살아남아 일을 끝냈다 — 트리째 죽이지 않았다"


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
