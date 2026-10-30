"""경계 — CMD 한 줄(`uvicorn src.presentation.api.app:app`)이 실제로 뜨고, 진짜 CLI를 부르는가.

경로 테스트는 가짜 CLI로 argv만 본다. 여기는 **실물 전 구간**이다: uvicorn 프로세스 → 작업 실행기 → 이 리포의
`python -m src schedule --list`. 다른 폴더에서 띄워도 리포 루트의 config를 읽는지(cwd에 안 기대는지)도 같이 본다.
"""
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from src.presentation.api.jobs import REPO_ROOT

DUMMY_ENV = {"LLM_BASE_URL": "https://gw.example.com/v1", "LLM_PASS_KEY": "p", "LLM_CLIENT_KEY": "c",
             "MAIL_AGENT_ID": "a", "MAIL_AGENT_API_KEY": "k",
             "BUILD_COMMIT": "deadbee", "BUILD_TIME": "2026-10-30T08:00:00+09:00"}


def test_uvicorn이_찾는_이름이_있다():
    from uvicorn.importer import import_from_string
    app = import_from_string("src.presentation.api.app:app")
    ctx = app.state.ctx
    assert ctx.root == REPO_ROOT
    assert str(ctx.clock().tzinfo) == "Asia/Seoul"            # app.json의 timezone — 컨테이너 TZ(UTC)가 아니다


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def served(tmp_path):
    port = _free_port()
    env = {**os.environ, **DUMMY_ENV, "PYTHONPATH": str(REPO_ROOT)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.presentation.api.app:app", "--host", "127.0.0.1",
         "--port", str(port)],
        cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if proc.poll() is not None or time.monotonic() > deadline:
                out = proc.stdout.read().decode("utf-8", "replace") if proc.poll() is not None else ""
                pytest.fail(f"uvicorn이 안 떴다 — {out}")
            time.sleep(0.2)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_uvicorn으로_떠서_진짜_CLI를_부른다(served):
    version = httpx.get(f"{served}/version").json()
    assert version["commit"] == "deadbee"
    assert any(s["name"] == "daily-alarm" for s in version["scenarios"])
    ready = httpx.get(f"{served}/ready")
    assert ready.status_code == 200, ready.text

    response = httpx.post(f"{served}/internal/v1/ops/schedule/list", json={"wait_s": 50}, timeout=60)
    job = response.json()
    assert response.status_code == 200, job
    assert job["status"] == "ok", job["stderr"]
    assert job["command"].endswith("-u -m src schedule --list")
    assert "daily-alarm" in job["stdout"]

    listed = httpx.get(f"{served}/internal/v1/ops/jobs").json()
    assert listed["jobs"][0]["id"] == job["id"]
