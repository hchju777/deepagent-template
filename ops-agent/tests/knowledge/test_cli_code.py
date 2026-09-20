"""`code status`/`plan`/`sync` 명령 자체를 부른다 — **배선은 배선을 불러야 보인다.**

이 리포에서 실제로 났다: `ProbeRunner`에 `clock`을 필수로 올렸는데 `__main__`의
호출부가 안 따라갔고 776개가 전부 통과했다.
"""
import json
import shutil
import subprocess

import pytest

from tests.support import set_real_config_env

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git이 없다")


def _tree(tmp_path, *, repo_path: str, url: str = "https://git.example.com/team/dt-core"):
    """리포의 실제 `config/`·`knowledge/`를 복사하고 레포 경로만 바꾼다."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    shutil.copytree(root / "config", tmp_path / "config")
    shutil.copytree(root / "knowledge", tmp_path / "knowledge")

    mx = tmp_path / "config" / "gbm" / "mx.json"
    body = json.loads(mx.read_text(encoding="utf-8"))
    body["code"]["repos"] = [{"name": "dt-core", "url": url, "path": repo_path}]
    mx.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    topology = tmp_path / "knowledge" / "topology" / "mx.json"
    shape = json.loads(topology.read_text(encoding="utf-8"))
    shape["services"] = {k: v for k, v in shape["services"].items()
                         if v["repo"] == "dt-core"}
    shape["config_paths"] = []        # 테스트마다 따로 세운다
    topology.write_text(json.dumps(shape, ensure_ascii=False), encoding="utf-8")
    return tmp_path / "config"


def _make_repo(root, *, origin):
    root.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(a, cwd=root, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    run("git", "remote", "add", "origin", origin)
    (root / "a.py").write_text("x\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "first")


def _run(config_root, tmp_path, monkeypatch, capsys, *argv):
    from src.__main__ import main

    set_real_config_env(monkeypatch)
    monkeypatch.setattr("sys.argv", [
        "src", "--config-root", str(config_root), "--env-file", str(tmp_path / "none"),
        *argv, "--gbm", "mx", "--fct", "gumi"])
    code = main()
    return code, capsys.readouterr()


def test_준비된_체크아웃은_통과한다(tmp_path, monkeypatch, capsys):
    url = "https://git.example.com/team/dt-core"
    _make_repo(tmp_path / "checkout", origin=url)
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"), url=url)

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 0, captured.out + captured.err
    assert "✅ dt-core" in captured.out
    # 배포 선언이 없으므로 `main` 최신을 **가정**했다고 적혀야 한다.
    assert "(가정)" in captured.out


def test_이름이_사는_파일이_하나도_없으면_말한다(tmp_path, monkeypatch, capsys):
    """**이름은 대상의 config 파일에 산다**(decisions ③-2). 경로 앞머리가 틀리면
    리드는 아무것도 못 찾는데, 증상은 "조사가 빈손"이라 원인이 안 보인다.

    사람이 손으로 적는 칸이라 오타가 정상적으로 일어난다.
    """
    url = "https://git.example.com/team/dt-core"
    _make_repo(tmp_path / "checkout", origin=url)
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"), url=url)

    topology = tmp_path / "knowledge" / "topology" / "mx.json"
    body = json.loads(topology.read_text(encoding="utf-8"))
    body["config_paths"] = ["config/없는파일.json"]
    topology.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 1
    assert "하나도" in captured.out
    assert "없는파일.json" in captured.out


def test_실재하는_config_경로는_통과한다(tmp_path, monkeypatch, capsys):
    url = "https://git.example.com/team/dt-core"
    _make_repo(tmp_path / "checkout", origin=url)
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"), url=url)

    topology = tmp_path / "knowledge" / "topology" / "mx.json"
    body = json.loads(topology.read_text(encoding="utf-8"))
    body["config_paths"] = ["a.py"]              # 픽스처가 만든 실재 파일
    topology.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 0, captured.out + captured.err


def test_다른_레포가_클론돼_있으면_막는다(tmp_path, monkeypatch, capsys):
    """**여기가 자물쇠다.** 남의 코드를 읽으면 그럴듯하게 틀린 판정이 나온다."""
    _make_repo(tmp_path / "checkout", origin="https://git.example.com/someone/fork")
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"))

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 1
    assert "origin이 config와 다르다" in captured.out


def test_체크아웃이_없으면_직접_칠_명령을_알려준다(tmp_path, monkeypatch, capsys):
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "없음"))
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "plan")
    assert code == 0 and "git clone" in captured.out


def test_sync는_붙을_수_없으면_값으로_실패하고_plan을_안내한다(tmp_path, monkeypatch, capsys):
    """사내 밖에서는 **늘** 이 경로다 — 죽으면 안 된다(decisions ⑤)."""
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "없음"),
                        url="https://127.0.0.1:1/없는/레포")
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "sync")
    assert code == 1
    assert "failed" in captured.out
    assert "code plan" in captured.err


def test_기동이_선언끼리의_어긋남을_잡는다(tmp_path, monkeypatch, capsys):
    """토폴로지가 없는 레포를 가리키면 런타임 증상은 **"코드 증거가 조용히 안
    나온다"**가 된다. 조용한 실패라 기동에서 잡는다."""
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"))
    topology = tmp_path / "knowledge" / "topology" / "mx.json"
    body = json.loads(topology.read_text(encoding="utf-8"))
    body["services"]["processor"]["repo"] = "없는레포"
    topology.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    from src.__main__ import main

    set_real_config_env(monkeypatch)
    monkeypatch.setattr("sys.argv", ["src", "--config-root", str(config_root),
                                     "--env-file", str(tmp_path / "none"), "boot"])
    assert main() == 1
    assert "없는레포" in capsys.readouterr().err


def test_체크아웃이_없어도_기동은_막지_않는다(tmp_path, monkeypatch, capsys):
    """디스크 상태는 `code status`의 일이다. 기동이 거기서 막히면 **사람이 기동
    검증을 통째로 끈다** — 개발 환경에는 체크아웃이 없는 것이 정상이다."""
    from src.__main__ import main

    config_root = _tree(tmp_path, repo_path=str(tmp_path / "없음"))
    set_real_config_env(monkeypatch)
    monkeypatch.setattr("sys.argv", ["src", "--config-root", str(config_root),
                                     "--env-file", str(tmp_path / "none"), "boot"])
    assert main() == 0, capsys.readouterr().err


def test_법인별로_갈린_config_경로를_해석한다(tmp_path, monkeypatch, capsys):
    """대상의 config가 **법인별로도 갈린다**(`config/factories/{fct}/{gbm}.json`).
    토폴로지는 GBM 단위라 자리표시자로만 표현할 수 있다 — 우리 `SITE_LAYERS`와 같다.
    """
    url = "https://git.example.com/team/dt-core"
    root = tmp_path / "checkout"
    _make_repo(root, origin=url)
    # 이 사이트(mx/gumi)의 층만 만든다.
    (root / "config" / "factories" / "gumi").mkdir(parents=True)
    (root / "config" / "factories" / "gumi" / "mx.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "layers"], cwd=root, check=True, capture_output=True)

    config_root = _tree(tmp_path, repo_path=str(root), url=url)
    topology = tmp_path / "knowledge" / "topology" / "mx.json"
    body = json.loads(topology.read_text(encoding="utf-8"))
    body["config_paths"] = ["config/factories/{fct}/{gbm}.json"]
    topology.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 0, captured.out + captured.err
    assert "config 층 1/1개" in captured.out


def test_없는_층은_오류가_아니다(tmp_path, monkeypatch, capsys):
    """**층은 선택이다** — 우리 `SITE_LAYERS`와 같다. `fct/{fct}/common.json`이 없는
    법인이 정상이듯 대상도 그렇다. 없는 층마다 빨간불을 켜면 사람이 검사를 끈다."""
    url = "https://git.example.com/team/dt-core"
    _make_repo(tmp_path / "checkout", origin=url)
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"), url=url)

    topology = tmp_path / "knowledge" / "topology" / "mx.json"
    body = json.loads(topology.read_text(encoding="utf-8"))
    body["config_paths"] = ["a.py", "config/{fct}/없는층.json"]   # 하나는 실재한다
    topology.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 0, captured.out + captured.err
    assert "config 층 1/2개" in captured.out
    assert "없음:" in captured.out            # 조용히 넘어가지도 않는다
