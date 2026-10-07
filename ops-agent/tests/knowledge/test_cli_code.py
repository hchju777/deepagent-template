"""`code status`/`plan`/`sync` 명령 자체를 부른다 — **배선은 배선을 불러야 보인다.**

이 리포에서 실제로 났다: `ProbeRunner`에 `clock`을 필수로 올렸는데 `__main__`의
호출부가 안 따라갔고 776개가 전부 통과했다.
"""
import json
import shutil
import subprocess

import pytest

from tests.support import (declare_submodule_at, git, make_git_repo,
                           populate_submodule, set_real_config_env)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git이 없다")

# 이 파일 안에서만 쓰는 이름이다. **리포의 config/knowledge와 무관해야 한다** —
# 운영이 거기에 진짜 이름을 적어도 이 테스트는 그대로 돌아야 한다.
REPO = "테스트레포"


def _tree(tmp_path, *, repo_path: str, url: str = "https://git.example.com/team/dt-core",
          services=None, config_paths=()):
    """**테스트가 자기 트리를 세운다.** 리포의 `config/`·`knowledge/`를 베끼지 않는다.

    처음엔 베꼈고, 그 파일들이 **운영이 채우는 칸**이라 사내에서 실제 레포 이름을
    적자마자 테스트가 깨졌다(`repo == "dt-core"`로 거르고 있었다). 운영이 자기 값을
    적는 것 때문에 우리 테스트가 빨간불이면, 사람은 그 테스트를 믿지 않게 된다.

    그리고 여기서 보려는 것은 `code status`의 **배선**이지 리포 설정의 내용이 아니다.
    설정이 온전한지는 `boot`과 `test_env_참조와_env_example이_어긋나지_않는다`가 본다.
    """
    config = tmp_path / "config"
    (config / "gbm").mkdir(parents=True)
    # 산출물(그래프)은 tmp 아래로 — 기본 `output/`은 cwd 기준이라 리포를 더럽힌다.
    (config / "app.json").write_text(json.dumps({"timezone": "Asia/Seoul",
                                                 "output_dir": str(tmp_path / "out")}),
                                     encoding="utf-8")
    (config / "registry.json").write_text(
        json.dumps({"sites": [{"gbm": "mx", "fct": "gumi"}]}), encoding="utf-8")
    (config / "gbm" / "mx.json").write_text(json.dumps({
        "infra": {"redis": {"url": "redis://h:6379"}},
        "code": {"repos": [{"name": REPO, "url": url, "path": repo_path}]}},
        ensure_ascii=False), encoding="utf-8")

    knowledge = tmp_path / "knowledge"
    (knowledge / "topology").mkdir(parents=True)
    (knowledge / "topology" / "mx.json").write_text(json.dumps({
        "services": services or {"processor": {"repo": REPO, "role": "가공한다"}},
        "config_paths": list(config_paths)}, ensure_ascii=False), encoding="utf-8")
    return config


def _make_repo(root, *, origin):
    make_git_repo(root, origin=origin)


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
    assert f"✅ {REPO}" in captured.out
    # 배포 선언이 없으므로 `main` 최신을 **가정**했다고 적혀야 한다.
    assert "(가정)" in captured.out


def test_이름이_사는_파일이_하나도_없으면_말한다(tmp_path, monkeypatch, capsys):
    """**이름은 대상의 config 파일에 산다**(decisions ③-2). 경로 앞머리가 틀리면
    리드는 아무것도 못 찾는데, 증상은 "조사가 빈손"이라 원인이 안 보인다.

    사람이 손으로 적는 칸이라 오타가 정상적으로 일어난다.
    """
    url = "https://git.example.com/team/dt-core"
    _make_repo(tmp_path / "checkout", origin=url)
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"), url=url,
                        config_paths=["config/없는파일.json"])

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 1, capsys.readouterr().err
    assert "하나도" in captured.out
    assert "없는파일.json" in captured.out


def test_실재하는_config_경로는_통과한다(tmp_path, monkeypatch, capsys):
    url = "https://git.example.com/team/dt-core"
    _make_repo(tmp_path / "checkout", origin=url)
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"), url=url,
                        config_paths=["a.py"])   # 픽스처가 만든 실재 파일

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 0, captured.out + captured.err


def test_다른_레포가_클론돼_있으면_막는다(tmp_path, monkeypatch, capsys):
    """**여기가 자물쇠다.** 남의 코드를 읽으면 그럴듯하게 틀린 판정이 나온다."""
    _make_repo(tmp_path / "checkout", origin="https://git.example.com/someone/fork")
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"))

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 1, capsys.readouterr().err
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
    assert code == 1, capsys.readouterr().err
    assert "failed" in captured.out
    assert "code plan" in captured.err


def test_기동이_선언끼리의_어긋남을_잡는다(tmp_path, monkeypatch, capsys):
    """토폴로지가 없는 레포를 가리키면 런타임 증상은 **"코드 증거가 조용히 안
    나온다"**가 된다. 조용한 실패라 기동에서 잡는다."""
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"),
                        services={"processor": {"repo": "없는레포"}})

    from src.__main__ import main

    set_real_config_env(monkeypatch)
    monkeypatch.setattr("sys.argv", ["src", "--config-root", str(config_root),
                                     "--env-file", str(tmp_path / "none"), "boot"])
    assert main() == 1, capsys.readouterr().err
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

    config_root = _tree(tmp_path, repo_path=str(root), url=url,
                        config_paths=["config/factories/{fct}/{gbm}.json"])

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 0, captured.out + captured.err
    assert "config 층 1/1개" in captured.out


def test_없는_층은_오류가_아니다(tmp_path, monkeypatch, capsys):
    """**층은 선택이다** — 우리 `SITE_LAYERS`와 같다. `fct/{fct}/common.json`이 없는
    법인이 정상이듯 대상도 그렇다. 없는 층마다 빨간불을 켜면 사람이 검사를 끈다."""
    url = "https://git.example.com/team/dt-core"
    _make_repo(tmp_path / "checkout", origin=url)
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"), url=url,
                        # 하나는 실재한다
                        config_paths=["a.py", "config/{fct}/없는층.json"])

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 0, captured.out + captured.err
    assert "config 층 1/2개" in captured.out
    assert "없음:" in captured.out            # 조용히 넘어가지도 않는다


# ── code read — 배포 커밋의 파일을 **실제로** 읽는다 ────────────────

def _repo_with_two_commits(root, *, origin):
    """옛 커밋과 새 커밋의 내용이 다른 레포. **그 차이가 요점이다.**"""
    run = lambda *a: subprocess.run(a, cwd=root, check=True, capture_output=True)  # noqa: E731
    (root / "config").mkdir(parents=True)
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    run("git", "remote", "add", "origin", origin)
    (root / "config" / "common.json").write_text('{"name": "OLD"}\n', encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "first")
    old = git("rev-parse", "HEAD", cwd=root).stdout.strip()
    (root / "config" / "common.json").write_text('{"name": "NEW"}\n', encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "second")
    return old


def test_배포_커밋의_내용을_읽는다(tmp_path, monkeypatch, capsys):
    """**11a의 핵심 계약이다.** `code status`는 파일이 *있는지*만 본다 —
    `git show <배포커밋>:경로`가 실제로 내용을 돌려주는지는 이 명령만 확인한다.

    사이트가 뒤처져 있는데 최신 코드를 읽으면 **떠 있지도 않은 코드로 확신에 찬
    오답**을 낸다. 배포 커밋을 박아 두고 옛 내용이 나오는지 본다.
    """
    url = "https://git.example.com/team/dt-core"
    old = _repo_with_two_commits(tmp_path / "checkout", origin=url)
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"), url=url)
    deployment = tmp_path / "knowledge" / "deployment"
    deployment.mkdir(parents=True, exist_ok=True)
    (deployment / "mx.json").write_text(json.dumps({"pins": {
        "processor": {"commit": old, "how": "declared"}}}), encoding="utf-8")

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "read",
                          "--service", "processor", "--path", "config/common.json")
    assert code == 0, captured.out + captured.err
    assert "OLD" in captured.out and "NEW" not in captured.out


def test_선언이_없으면_가정했다고_적는다(tmp_path, monkeypatch, capsys):
    """확인한 것과 가정한 것을 같은 모양으로 찍으면 사람이 구별을 못 한다."""
    url = "https://git.example.com/team/dt-core"
    _repo_with_two_commits(tmp_path / "checkout", origin=url)
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"), url=url)

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "read",
                          "--service", "processor", "--path", "config/common.json")
    assert code == 0, captured.out + captured.err
    assert "가정했다" in captured.out
    assert "NEW" in captured.out              # 선언이 없으면 main 최신이다


def test_없는_서비스는_아는_것을_알려준다(tmp_path, monkeypatch, capsys):
    url = "https://git.example.com/team/dt-core"
    _repo_with_two_commits(tmp_path / "checkout", origin=url)
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"), url=url)

    with pytest.raises(SystemExit) as caught:
        _run(config_root, tmp_path, monkeypatch, capsys, "code", "read",
             "--service", "없는서비스", "--path", "a.json")
    assert "processor" in str(caught.value)


def test_없는_파일은_값으로_실패한다(tmp_path, monkeypatch, capsys):
    """배포 커밋 선언이 오래되면 **일상적으로** 일어난다 — 죽으면 안 된다."""
    url = "https://git.example.com/team/dt-core"
    _repo_with_two_commits(tmp_path / "checkout", origin=url)
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"), url=url)

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "read",
                          "--service", "processor", "--path", "없는/파일.json")
    assert code == 1
    assert captured.err.strip()


def test_경로의_자리표시자가_치환된다(tmp_path, monkeypatch, capsys):
    """`config_paths`와 같은 문법이어야 사람이 거기서 복사해 붙일 수 있다."""
    url = "https://git.example.com/team/dt-core"
    root = tmp_path / "checkout"
    _repo_with_two_commits(root, origin=url)
    (root / "config" / "gumi").mkdir()
    (root / "config" / "gumi" / "mx.json").write_text('{"층": "법인"}\n', encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "layer"], cwd=root, check=True,
                   capture_output=True)
    config_root = _tree(tmp_path, repo_path=str(root), url=url)

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "read",
                          "--service", "processor", "--path", "config/{fct}/{gbm}.json")
    assert code == 0, captured.out + captured.err
    assert "법인" in captured.out


def test_fct_없이도_코드_명령이_돈다(tmp_path, monkeypatch, capsys):
    """**코드는 GBM 단위로 같다.** "어느 법인이냐"는 답이 뜻이 없는 질문이고,
    사람에게 그걸 물으면 매번 의미 없는 값을 타이핑하게 된다."""
    url = "https://git.example.com/team/dt-core"
    _make_repo(tmp_path / "checkout", origin=url)
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"), url=url)

    from src.__main__ import main

    set_real_config_env(monkeypatch)
    for what in ("plan", "status"):
        monkeypatch.setattr("sys.argv", [
            "src", "--config-root", str(config_root), "--env-file", str(tmp_path / "none"),
            "code", what, "--gbm", "mx"])          # --fct 없음
        assert main() in (0, 1), what
        assert capsys.readouterr().out, f"{what}가 아무것도 안 찍었다"


def test_같은_GBM인데_레포_선언이_갈리면_말한다(tmp_path, monkeypatch, capsys):
    """**"코드는 GBM 단위로 같다"가 이 설계의 전제다**(decisions ③).

    누가 `fct/` 층에 `code.repos`를 적으면 그 전제가 조용히 깨지고, 우리는
    **사이트마다 다른 코드를 읽으면서도 같은 것을 읽는 줄 안다.** 그 상태로 낸
    판정은 "구미에서는 맞고 SEVT에서는 틀린" 답이 되는데, 원인이 안 보인다.
    """
    url = "https://git.example.com/team/dt-core"
    _make_repo(tmp_path / "checkout", origin=url)
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"), url=url)

    # 사이트를 둘로 늘리고, 한쪽 fct 층이 레포를 덮어쓰게 한다.
    (config_root / "registry.json").write_text(json.dumps({"sites": [
        {"gbm": "mx", "fct": "gumi"}, {"gbm": "mx", "fct": "sevt"}]}), encoding="utf-8")
    layer = config_root / "fct" / "sevt"
    layer.mkdir(parents=True)
    (layer / "mx.json").write_text(json.dumps({"code": {"repos": [
        {"name": REPO, "url": "https://git.example.com/team/다른레포",
         "path": str(tmp_path / "checkout")}]}}, ensure_ascii=False), encoding="utf-8")

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert "사이트마다 레포 선언이 다르다" in captured.err, captured.out + captured.err
    assert "다른레포" in captured.err


def test_선언이_같으면_경고하지_않는다(tmp_path, monkeypatch, capsys):
    """모든 사이트를 볼 때마다 경고가 뜨면 사람이 그 경고를 안 읽게 된다."""
    url = "https://git.example.com/team/dt-core"
    _make_repo(tmp_path / "checkout", origin=url)
    config_root = _tree(tmp_path, repo_path=str(tmp_path / "checkout"), url=url)
    (config_root / "registry.json").write_text(json.dumps({"sites": [
        {"gbm": "mx", "fct": "gumi"}, {"gbm": "mx", "fct": "sevt"}]}), encoding="utf-8")

    _, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert "레포 선언이 다르다" not in captured.err


def _with_submodule(tmp_path, url, *, populate: bool):
    """부모 + submodule 하나를 만들고, 채우거나 안 채운 체크아웃을 돌려준다."""
    origin = tmp_path / "origin"
    _make_repo(origin, origin=url)
    lib = tmp_path / "libs"
    _make_repo(lib, origin="https://git.example.com/team/libs")
    (lib / "kafka.json").write_text('{"topic": "X"}\n', encoding="utf-8")
    git("add", "-A", cwd=lib)
    git("commit", "-qm", "topic", cwd=lib)
    head = git("rev-parse", "HEAD", cwd=lib).stdout.strip()
    # `git submodule add`를 안 쓴다 — 로컬 경로에 대해 막혀 있고, 우리 코드가
    # 읽는 것은 `.gitmodules`와 gitlink 둘뿐이다.
    declare_submodule_at(origin, lib, head, path="vendor/libs")
    checkout = tmp_path / "checkout"
    git("clone", "-q", str(origin), str(checkout), cwd=tmp_path)
    if populate:
        populate_submodule(checkout, "vendor/libs")
    git("remote", "set-url", "origin", url, cwd=checkout)
    return checkout


def test_안_채워진_submodule을_status가_말한다(tmp_path, monkeypatch, capsys):
    """**이걸 여기서 안 말하면 아무 데서도 안 보인다.**

    git 자신이 조용하다: 안 채워진 submodule을 두고 `git grep`은 종료코드 1에
    출력이 없다. 사람이 `code status`를 초록으로 보고 조사를 돌리면, 2차의 판정이
    "코드에 그런 게 없다"를 확신에 차서 단정한다.
    """
    url = "https://git.example.com/team/dt-core"
    checkout = _with_submodule(tmp_path, url, populate=False)
    config_root = _tree(tmp_path, repo_path=str(checkout), url=url)

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 1, captured.out + captured.err
    assert "vendor/libs" in captured.out
    assert "submodule update --init" in captured.out


def test_submodule_안의_config_층을_없다고_하지_않는다(tmp_path, monkeypatch, capsys):
    """**측정으로 잡은 오진이다.** `git cat-file -e <커밋>:<서브>/…`는 채워져
    있어도 실패한다. 그대로 두면 공용 라이브러리에 사는 층을 "없다"로 신고하고
    "config_paths를 고쳐라"라는 틀린 처방이 나온다 — 경로는 맞았는데.
    """
    url = "https://git.example.com/team/dt-core"
    checkout = _with_submodule(tmp_path, url, populate=True)
    config_root = _tree(tmp_path, repo_path=str(checkout), url=url,
                        config_paths=["vendor/libs/kafka.json"])

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 0, captured.out + captured.err
    assert "하나도" not in captured.out
    assert "config 층 1/1개" in captured.out


def test_submodule이_안_읽히면_config_경로를_탓하지_않는다(tmp_path, monkeypatch, capsys):
    """둘 다 빨간불이지만 **처방이 달라야 한다.** 안 채워진 submodule 때문에
    그 안의 층이 안 보이는 것인데 "경로를 고쳐라"라고 하면, 사람은 맞는 경로를
    고치다가 진짜 원인을 영영 못 본다.
    """
    url = "https://git.example.com/team/dt-core"
    checkout = _with_submodule(tmp_path, url, populate=False)
    config_root = _tree(tmp_path, repo_path=str(checkout), url=url,
                        config_paths=["vendor/libs/kafka.json"])

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 1, captured.out + captured.err
    assert "하나도" in captured.out
    assert "config_paths를 고쳐라" not in captured.out
    assert "먼저 위의 submodule부터" in captured.out


def test_코드가_없어도_조사가_죽지_않는다(tmp_path, monkeypatch):
    """**토폴로지를 안 적은 사이트가 정상이다** — 코드 확보는 선택이다.

    여기서 던지면 코드와 무관한 조사까지 통째로 못 돈다. 대신 비어 있으면
    `code.*`가 목록에도 예시에도 안 나가므로 리드가 없는 문을 두드릴 일도 없다.
    """
    from src.__main__ import _code_if_ready
    from src.config.loader import load_site_config

    clock = lambda: None                                          # noqa: E731
    url = "https://git.example.com/team/dt-core"
    checkout = _with_submodule(tmp_path, url, populate=True)
    config_root = _tree(tmp_path, repo_path=str(checkout), url=url)
    site, _ = load_site_config(config_root, "mx", "gumi", env={})

    # ① 레포 선언이 아예 없다
    bare = site.model_copy(update={"code": site.code.model_copy(update={"repos": []})})
    assert _code_if_ready(bare, "mx", "gumi", knowledge_root=tmp_path,
                          clock=clock)[:2] == (None, ())

    # ② 레포는 있는데 knowledge가 없다 — 사람이 아직 안 적은 상태
    assert site.code.repos, "픽스처가 레포를 선언했어야 한다"
    code, services, _, _ = _code_if_ready(site, "mx", "gumi",
                                    knowledge_root=tmp_path / "없는지식", clock=clock)
    assert (code, services) == (None, ())


def test_지식이_있으면_서비스_이름이_나온다(tmp_path):
    """비어 있는 것과 **못 읽은 것**을 같은 답으로 뭉개면, 지식을 제대로 적어
    뒀는데도 리드가 코드를 못 보는 상태를 아무도 못 찾는다."""
    from src.__main__ import _code_if_ready
    from src.config.loader import load_site_config

    url = "https://git.example.com/team/dt-core"
    checkout = _with_submodule(tmp_path, url, populate=True)
    config_root = _tree(tmp_path, repo_path=str(checkout), url=url)
    site, _ = load_site_config(config_root, "mx", "gumi", env={})
    code, services, _, _ = _code_if_ready(site, "mx", "gumi",
                                    knowledge_root=tmp_path / "knowledge",
                                    clock=lambda: None)
    assert code is not None and services == ("processor",)


# ── 흐름 그래프 (11c) ──────────────────────────────────────────────

LAYERS = ["config/gbm/{gbm}.json", "config/factories/{fct}/common.json",
          "config/factories/{fct}/{gbm}.json"]


def _flow_repo(root, *, origin):
    """사내 모양의 config를 든 레포. 서비스 둘(processor·sink)이 `infra`를 공유한다."""
    _make_repo(root, origin=origin)

    def write(path, value):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                          encoding="utf-8")

    write("config/gbm/mx.json", {
        "infra": {"kafka": {"consumer": {"group_id": "mx-core",
                                         "topic": {"topic1": "mx.alarm.raw", "topic2": "mx.alarm.main"}},
                            "producer": {"topic": {"topic1": "mx.alarm.main"}}}},
        "mongodb_collection": {"alarm": "alarm_events"}})
    write("config/factories/gumi/common.json", {"lines": ["L1"]})
    write("processor/handler.py",
          'def run(cfg, consumer, producer):\n'
          '    k = cfg["infra"]["kafka"]\n'
          '    for m in consumer.subscribe(k["consumer"]["topic"]["topic1"], group=k["consumer"]["group_id"]):\n'
          '        producer.send(k["producer"]["topic"]["topic1"], m)\n')
    write("sink/writer.py",
          'def run(cfg, consumer, mongo):\n'
          '    k = cfg["infra"]["kafka"]\n'
          '    for m in consumer.subscribe(k["consumer"]["topic"]["topic2"], group=k["consumer"]["group_id"]):\n'
          '        mongo[cfg["mongodb_collection"]["alarm"]].insert_one(m)\n')
    # 라우트 선언 하나 — 끝점 노드(11c 커밋 5). 레포를 두 서비스가 나눠 쓰므로 serves는 레포에 붙는다.
    write("api/r.py", 'router = APIRouter(prefix="/summary")\n\n\n@router.post("/badge")\ndef badge(cfg, mongo):\n'
          '    return mongo[cfg["mongodb_collection"]["alarm"]].count_documents({})\n')
    git("add", "-A", cwd=root)
    git("commit", "-qm", "flow", cwd=root)


def _flow_tree(tmp_path):
    url = "https://git.example.com/team/dt-core"
    _flow_repo(tmp_path / "checkout", origin=url)
    return _tree(tmp_path, repo_path=str(tmp_path / "checkout"), url=url,
                 services={"processor": {"repo": REPO, "role": "가공한다"},
                           "sink": {"repo": REPO, "role": "저장한다"}},
                 config_paths=LAYERS)


def test_code_graph가_배포_커밋에_그래프를_박는다(tmp_path, monkeypatch, capsys):
    """**배선을 부른다.** graphify가 없는 환경이 기본이다 — 오버레이만으로도 만들어져야 한다."""
    monkeypatch.setenv("GRAPHIFY_BIN", str(tmp_path / "없는-graphify"))
    config_root = _flow_tree(tmp_path)
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "graph")
    assert code == 0, captured.out + captured.err
    assert "graphify 없음" in captured.out and "skipped" in captured.out
    bundle = tmp_path / "out" / "graph" / "mx-gumi"
    assert (bundle / "graph.json").exists() and (bundle / "meta.json").exists()
    meta = json.loads((bundle / "meta.json").read_text(encoding="utf-8"))
    assert len(meta["commits"][REPO]) == 40, "참조가 아니라 SHA에 박혀야 한다"
    # config 엣지는 서비스별 합친 config에서 바로 나오고, 코드는 processor/·sink/로 갈린다 —
    # "못 가른 엣지"도 "path를 채워라"도 "안 만진다"도 나오면 안 된다.
    assert "오버레이 노드" in captured.out
    assert "path" not in captured.out and "안 만진다" not in captured.out
    assert not (tmp_path / "checkout" / "graphify-out").exists(), "체크아웃을 더럽혔다"
    # graphify가 없어도 flow.html은 선다(오버레이만으로 그린다). 리포트·wiki는 없다.
    assert (bundle / "flow.html").exists() and not (bundle / "wiki").exists() and not (bundle / "reports").exists()
    assert not (bundle / "worktrees").exists(), "빈 worktrees 껍데기가 남았다"
    # 진행은 stderr에, 경과 시간과 함께 — 사내에서 몇 분을 말없이 돌자 멈춘 줄 알았다.
    assert "이름 " in captured.err and "찾는 중" in captured.err and "graphify 없음" in captured.err


def test_조사에는_배포_커밋과_같은_그래프만_실린다(tmp_path, monkeypatch, capsys):
    """없음·낡음은 같은 취급(None)이다 — 낡은 배선을 리드가 믿으면 떠 있지도 않은 코드를 본다."""
    from src.__main__ import _flow_graph_if_fresh

    monkeypatch.setenv("GRAPHIFY_BIN", str(tmp_path / "없는-graphify"))
    config_root = _flow_tree(tmp_path)
    bundle = tmp_path / "out" / "graph" / "mx-gumi"
    assert _flow_graph_if_fresh(bundle, {})[0] is None
    _run(config_root, tmp_path, monkeypatch, capsys, "code", "graph")
    commits = json.loads((bundle / "meta.json").read_text(encoding="utf-8"))["commits"]
    fresh, note = _flow_graph_if_fresh(bundle, commits)
    assert fresh is not None and fresh["nodes"] and "실림" in note
    stale, note = _flow_graph_if_fresh(bundle, {REPO: "0" * 40})
    assert stale is None and "낡음" in note


def test_조사에는_신선한_번들의_심볼_인덱스도_실리고_낡으면_같이_빠진다(tmp_path, monkeypatch, capsys):
    """6d-1 — 역질문(`code.callers`·`code.uses`)은 그래프와 같은 번들의 인덱스를 읽는다. 낡은 번들은 둘 다 None,
    인덱스만 없는 옛 번들은 그래프는 싣되 인덱스 없음을 말한다(그러면 역질문이 목록에서 빠진다)."""
    from src.__main__ import _code_if_ready
    from src.config.loader import load_site_config

    monkeypatch.setenv("GRAPHIFY_BIN", str(tmp_path / "없는-graphify"))
    config_root = _flow_tree(tmp_path)
    bundle = tmp_path / "out" / "graph" / "mx-gumi"
    _run(config_root, tmp_path, monkeypatch, capsys, "code", "graph")
    site, _ = load_site_config(config_root, "mx", "gumi", env={})
    ready = lambda: _code_if_ready(site, "mx", "gumi", knowledge_root=tmp_path / "knowledge",   # noqa: E731
                                   clock=lambda: None, graph_dir=bundle)
    code, _, graph, note = ready()
    assert graph is not None and code.has_index() and "인덱스 없음" not in note
    git("commit", "-q", "--allow-empty", "-m", "moved on", cwd=tmp_path / "checkout")   # 배포 커밋이 바뀌었다
    code, _, graph, note = ready()
    assert graph is None and not code.has_index() and "낡음" in note   # 인덱스 파일은 그대로 있어도 안 붙인다
    _run(config_root, tmp_path, monkeypatch, capsys, "code", "graph")
    (bundle / "symbols.json").unlink()                                   # 6a 이전 번들 모양
    code, _, graph, note = ready()
    assert graph is not None and not code.has_index() and "심볼 인덱스 없음" in note


def test_code_flow가_흐름_경로를_보여준다(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GRAPHIFY_BIN", str(tmp_path / "없는-graphify"))
    config_root = _flow_tree(tmp_path)
    _run(config_root, tmp_path, monkeypatch, capsys, "code", "graph")
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys,
                          "code", "flow", "processor", "--to", "sink")
    assert code == 0, captured.out + captured.err
    assert "processor —produces→ mx.alarm.main ←consumes— sink" in captured.out
    assert "processor/handler.py:L4" in captured.out
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "flow", "alarm_events")
    assert code == 0 and "sink —writes→ alarm_events [collection]" in captured.out
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "flow")
    assert code == 0 and "mx.alarm.main" in captured.out       # 허브 목록


def test_code_status가_낡은_그래프를_말한다(tmp_path, monkeypatch, capsys):
    """그래프는 SHA에 박힌다. 배포 커밋이 앞으로 가면 **낡았다**고 말해야 한다 —
    조용히 옛 그래프를 쓰는 것이 참고한 글의 첫 번째 함정이었다."""
    monkeypatch.setenv("GRAPHIFY_BIN", str(tmp_path / "없는-graphify"))
    config_root = _flow_tree(tmp_path)
    _run(config_root, tmp_path, monkeypatch, capsys, "code", "graph")
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 0 and "✅ 만든 시각" in captured.out
    checkout = tmp_path / "checkout"
    (checkout / "note.txt").write_text("새 커밋", encoding="utf-8")
    git("add", "-A", cwd=checkout)
    git("commit", "-qm", "advance", cwd=checkout)
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert "⚠ 낡음" in captured.out and "code sync" in captured.out


def test_그래프가_없으면_status가_만드는_법을_말한다(tmp_path, monkeypatch, capsys):
    config_root = _flow_tree(tmp_path)
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 0 and "code graph" in captured.out
    with pytest.raises(SystemExit, match="code graph"):
        _run(config_root, tmp_path, monkeypatch, capsys, "code", "flow", "x")



def test_code_graph가_끝점을_싣고_flow와_status가_말한다(tmp_path, monkeypatch, capsys):
    """끝점은 사람이 적지 않는다 — 코드의 라우트 선언에서 온다. 공유 레포면 레포가 serves한다."""
    monkeypatch.setenv("GRAPHIFY_BIN", str(tmp_path / "없는-graphify"))
    config_root = _flow_tree(tmp_path)
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "graph")
    assert code == 0, captured.out + captured.err
    assert "끝점 1개 중 등재 0개" in captured.out and "끝점 1개" in captured.err
    assert "자원까지 이어진 1개" in captured.out and "끝점 추적" in captured.err
    overlay = json.loads((tmp_path / "out" / "graph" / "mx-gumi" / "overlay.json").read_text(encoding="utf-8"))
    assert [n["label"] for n in overlay["nodes"] if n["type"] == "endpoint"] == ["/summary/badge"]
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "flow", "/summary/badge")
    assert code == 0 and "—serves→ /summary/badge [endpoint]" in captured.out and "api/r.py:L4" in captured.out
    # 11b 커밋 2 — 끝점마다 추적기가 돌아 읽는 자원이 엣지가 된다. config 키 경유라 추정(INFERRED)이다.
    assert "/summary/badge [endpoint] —reads→ alarm_events [collection]   [INFERRED] api/r.py:L6" in captured.out
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 0 and "끝점 1(등재 0 · 서빙 미상 0 · 자원까지 1 · 막힘 0)" in captured.out
    # 11b 커밋 3a — 사람도 리드가 받는 사슬을 그대로 본다. config 키 경유 읽기는 `config키`로 표시된다.
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "trace", "/summary/badge")
    assert code == 0, captured.out + captured.err
    assert captured.out.splitlines()[0].startswith("  api/r.py:L")
    assert "reads: alarm_events [collection] config키" in captured.out
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "trace", "/nope")
    assert code == 1 and "끝점에 없다" in captured.out


def test_code_trace와_flow는_Git_Bash가_바꾼_끝점_path를_되돌려_읽는다(tmp_path, monkeypatch, capsys):
    """사내(Git Bash)에서 `code trace /items/…`가 `C:\\Program Files/Git/items/…`로 와 "끝점에 없다"가 났다."""
    monkeypatch.setenv("GRAPHIFY_BIN", str(tmp_path / "없는-graphify"))
    config_root = _flow_tree(tmp_path)
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "graph")
    assert code == 0, captured.out + captured.err
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys,
                          "code", "trace", "C:\\Program Files/Git/summary/badge")
    assert code == 0, captured.out + captured.err
    assert "Git Bash가 바꾼 인자를 /summary/badge로 읽었다" in captured.out and "api/r.py:L" in captured.out
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys,
                          "code", "flow", "C:/Program Files/Git/summary/badge")
    assert code == 0 and "—serves→ /summary/badge [endpoint]" in captured.out
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "trace", "C:/Program Files/Git/nope")
    assert code == 1 and "MSYS_NO_PATHCONV=1" in captured.out
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "trace", "/v2/summary/badge")
    assert code == 1 and "Git Bash" not in captured.out and "MSYS" not in captured.out


def test_code_graph가_심볼_인덱스를_쓰고_status와_check가_말한다(tmp_path, monkeypatch, capsys):
    """11d 6a — 레포 전체의 심볼·엣지가 번들에 든다. `code check`는 사람이 한 줄로 돌려 숫자 몇 줄을 받는 하네스다."""
    from src.infrastructure.git_reader import RealCodeReader

    monkeypatch.setenv("GRAPHIFY_BIN", str(tmp_path / "없는-graphify"))
    config_root = _flow_tree(tmp_path)
    # 11e — 인덱스는 커밋을 `ls-tree`+`cat-file` 한 쌍으로 받는다. 파일마다 `show`를 띄우면 사내 Windows에서 20분이다.
    spawned, real = [], RealCodeReader._git_bytes

    async def counting(self, repo, args, **kw):
        spawned.append(list(args))
        return await real(self, repo, args, **kw)

    monkeypatch.setattr(RealCodeReader, "_git_bytes", counting)
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "graph")
    assert code == 0, captured.out + captured.err
    assert [a for a in spawned if a[0] == "show" and a[1].endswith(".py")] == [], "파일마다 show를 띄웠다"
    assert sum(1 for a in spawned if a[:2] == ["cat-file", "--batch"]) == 1   # 레포 하나, 서브모듈 없음
    bundle = tmp_path / "out" / "graph" / "mx-gumi"
    symbols = json.loads((bundle / "symbols.json").read_text(encoding="utf-8"))
    assert {s["qualname"] for s in symbols["symbols"] if s["kind"] == "module"} >= {"processor.handler", "sink.writer", "api.r"}
    assert (bundle / "edges.json").exists() and "심볼" in captured.err
    assert "processor.handler.run" in (bundle / "calls.html").read_text(encoding="utf-8")   # 사람용 한 장(6c-1b)
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "status")
    assert code == 0 and "심볼 " in captured.out and "엣지 " in captured.out
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "check")
    assert code == 0, captured.out + captured.err
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) <= 8 and any("커버리지" in l for l in lines) and any("정밀도" in l for l in lines)
    # 6b-0 — `--unresolved`는 못 푼 호출의 모양을 덧붙인다(공유 라이브러리·수신자 묶음). 기본 출력은 그대로 일곱 줄.
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "check", "--unresolved")
    assert code == 0, captured.out + captured.err
    more = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) < len(more) <= 16 and any("external" in l for l in more) and any("수신자" in l for l in more)



def _flow_tree_with_shared(tmp_path):
    """공유 라이브러리를 서브모듈로 쓰는 레포 — 사내 모양이다(레포마다 같은 라이브러리를 `src`와 같은 깊이에
    서브모듈로 두고, 레포마다 핀이 다를 수 있다)."""
    url = "https://git.example.com/team/dt-core"
    origin = tmp_path / "origin"
    _flow_repo(origin, origin=url)
    lib = tmp_path / "shared"
    _make_repo(lib, origin="https://git.example.com/team/shared-lib")
    (lib / "clock.py").write_text("def utc_stamp(m):\n    return m\n", encoding="utf-8")
    (lib / "base.py").write_text("class Step:\n    def apply(self, m):\n        return m\n", encoding="utf-8")
    git("add", "-A", cwd=lib)
    git("commit", "-qm", "lib", cwd=lib)
    head = git("rev-parse", "HEAD", cwd=lib).stdout.strip()
    (origin / "processor" / "stamp.py").write_text(
        "from shared_lib.clock import utc_stamp\nfrom shared_lib.base import Step\n\n\n"
        "class Stamp(Step):\n    def apply(self, m):\n        return utc_stamp(m)\n", encoding="utf-8")
    git("add", "-A", cwd=origin)
    git("commit", "-qm", "stamp", cwd=origin)
    declare_submodule_at(origin, lib, head, path="shared_lib")
    checkout = tmp_path / "checkout"
    git("clone", "-q", str(origin), str(checkout), cwd=tmp_path)
    populate_submodule(checkout, "shared_lib")
    git("remote", "set-url", "origin", url, cwd=checkout)
    return _tree(tmp_path, repo_path=str(checkout), url=url,
                 services={"processor": {"repo": REPO, "role": "가공한다"},
                           "sink": {"repo": REPO, "role": "저장한다"}},
                 config_paths=LAYERS)


def test_code_graph가_채워진_공유_서브모듈까지_인덱싱해_소비_코드에서_확실로_잇는다(tmp_path, monkeypatch, capsys):
    """사내 세 번째 숫자: external 2228 중 공유 라이브러리 738. 공유 레포를 따로 등재하지 않는다 — 부모가 박은
    버전으로 서브모듈 안을 읽으면 `from <서브>.x import f`가 **같은 레포 안에서** 풀리고, 레포마다 자기 핀이다."""
    monkeypatch.setenv("GRAPHIFY_BIN", str(tmp_path / "없는-graphify"))
    config_root = _flow_tree_with_shared(tmp_path)
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "graph")
    assert code == 0, captured.out + captured.err
    bundle = tmp_path / "out" / "graph" / "mx-gumi"
    symbols = json.loads((bundle / "symbols.json").read_text(encoding="utf-8"))["symbols"]
    edges = json.loads((bundle / "edges.json").read_text(encoding="utf-8"))["edges"]
    q = {s["id"]: s["qualname"] for s in symbols}
    assert {"shared_lib.clock", "shared_lib.base"} <= {s["qualname"] for s in symbols if s["kind"] == "module"}
    got = {(q[e["src"]], q[e["dst"]], e["type"], e["certainty"]) for e in edges}
    assert ("processor.stamp.Stamp.apply", "shared_lib.clock.utc_stamp", "calls", "exact") in got
    assert ("processor.stamp.Stamp", "shared_lib.base.Step", "inherits", "exact") in got
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "check", "--unresolved")
    assert code == 0, captured.out + captured.err
    assert "불변식 OK" in captured.out and "공유 라이브러리 0" in captured.out


def test_code_callers_path_uses가_인덱스로_역질문에_답한다(tmp_path, monkeypatch, capsys):
    """11d 6c-1 — "이 컬렉션에 누가 쓰고 읽나", "이 함수를 누가 부르나", "A에서 B로 어떻게 가나". 사람이 사내에서 한 줄로
    돌려 아는 답과 맞는지 볼 수 있어야 한다 — 리드에 잇기(6d) 전에."""
    monkeypatch.setenv("GRAPHIFY_BIN", str(tmp_path / "없는-graphify"))
    config_root = _flow_tree_with_shared(tmp_path)
    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "graph")
    assert code == 0, captured.out + captured.err

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "uses", "alarm_events")
    assert code == 0, captured.out + captured.err
    out = captured.out
    assert "쓰기 1" in out and "sink.writer.run" in out and "읽기 1" in out and "api.r.badge" in out
    assert "라우트 router.post" in out                                 # 읽는 쪽 진입점이 라우트라고 말한다
    assert "· sink]" in out                                            # 서비스 이름이 붙는다

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "callers", "utc_stamp")
    assert code == 0, captured.out + captured.err
    assert "processor.stamp.Stamp.apply" in captured.out and "shared_lib.clock.utc_stamp" in captured.out
    assert "=>" in captured.out and "shared_lib/clock.py" in captured.out   # Step.apply => Stamp.apply

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "callers", "run")
    assert code == 1 and "여럿" in captured.out                       # 모호하면 후보를 보여 주고 멈춘다
    assert "processor.handler.run" in captured.out and "sink.writer.run" in captured.out

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys,
                          "code", "path", "shared_lib.base.Step.apply", "utc_stamp")
    assert code == 0, captured.out + captured.err
    assert "base.Step.apply => stamp.Stamp.apply → clock.utc_stamp" in captured.out

    code, captured = _run(config_root, tmp_path, monkeypatch, capsys, "code", "uses", "없는이름")
    assert code == 1 and "없다" in captured.out
