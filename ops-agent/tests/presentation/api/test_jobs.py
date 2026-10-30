"""작업 실행기 — 셸 없이, 한 번에 하나, 시간을 넘기면 죽이고 그때까지의 출력을 남긴다."""
import asyncio
import json
import os
import shlex
import sys

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
