"""체크아웃 진단 — **자물쇠가 실제로 잠기는가.**

`origin` 대조가 이 파일의 핵심이다. 누가 다른 레포를 그 경로에 클론하면 우리는
그럴듯한 코드를 읽고 그럴듯한 판정을 낸다 — **전부 틀린 채로.** "못 읽었다"보다
훨씬 나쁘다: 실패가 조용하고 판정은 확신에 차 있다.
"""
import shutil
import subprocess

import pytest

from src.config.schema_site import RepoConfig
from src.knowledge.checkout import (auth_args, config_layers, has_commit,
                                    parse_gitmodules, plan_for, stale, status_of,
                                    submodules_at, sync, unpopulated)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git이 없다")

URL = "https://git.example.com/team/dt-core"


def _make_repo(root, *, origin: str = URL):
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True,
                   capture_output=True)
    for key, value in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "config", key, value], cwd=root, check=True,
                       capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=root, check=True,
                   capture_output=True)
    (root / "a.py").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=root, check=True,
                   capture_output=True)
    return root


def repo_at(path, *, url: str = URL) -> RepoConfig:
    return RepoConfig(name="dt-core", url=url, path=str(path))


def test_제대로_된_체크아웃은_통과한다(tmp_path):
    state = status_of(repo_at(_make_repo(tmp_path / "dt-core")))
    assert state.ready and state.problems == []


def test_경로가_없으면_말한다(tmp_path):
    state = status_of(repo_at(tmp_path / "없음"))
    assert not state.ready and "경로가 없다" in state.problems[0]


def test_git이_아니면_말한다(tmp_path):
    """작업 트리만 복사되면 `git show <배포커밋>:경로`가 통째로 안 된다 —
    **우리 설계의 전제가 무너지는 자리다**(⑤-2)."""
    plain = tmp_path / "plain"
    plain.mkdir()
    state = status_of(repo_at(plain))
    assert state.exists and not state.is_git
    assert ".git이 없다" in state.problems[0]


def test_다른_레포가_클론돼_있으면_막는다(tmp_path):
    """**이 파일에서 제일 중요한 테스트다.**

    origin 대조를 지우면 우리는 남의 코드를 읽고 확신에 찬 판정을 낸다.
    """
    root = _make_repo(tmp_path / "dt-core", origin="https://git.example.com/someone/fork")
    state = status_of(repo_at(root))
    assert not state.ready
    assert "origin이 config와 다르다" in state.problems[0]


def test_git_꼬리와_끝_슬래시는_같은_것으로_본다(tmp_path):
    """`...dt-core.git`과 `...dt-core`는 같은 레포다. 이걸로 실패하면 사람이
    자물쇠를 끄고 싶어진다 — **끌 수 있는 자물쇠는 잠기지 않는다.**"""
    root = _make_repo(tmp_path / "dt-core", origin=URL + ".git")
    assert status_of(repo_at(root)).ready


def test_호스트가_다르면_같게_안_본다(tmp_path):
    """관대함이 여기까지 오면 자물쇠가 아니다."""
    root = _make_repo(tmp_path / "dt-core", origin="https://다른호스트/team/dt-core")
    assert not status_of(repo_at(root)).ready


def test_커밋이_로컬에_있는지_본다(tmp_path):
    """배포 커밋 선언이 로컬보다 앞서면 `git show`가 실패한다 — 미리 안다(⑤-4)."""
    root = _make_repo(tmp_path / "dt-core")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()
    repo = repo_at(root)
    assert has_commit(repo, head)
    assert not has_commit(repo, "0" * 40)


# ── plan: 사람이 직접 칠 명령 ──────────────────────────────────────

def test_경로가_없으면_clone을_알려준다(tmp_path):
    repo = repo_at(tmp_path / "없음")
    assert "git clone" in plan_for(repo, status_of(repo))[0]


def test_준비됐으면_fetch를_알려준다(tmp_path):
    repo = repo_at(_make_repo(tmp_path / "dt-core"))
    assert "fetch" in plan_for(repo, status_of(repo))[0]


def test_plan에_토큰이_안_찍힌다(tmp_path):
    """사람이 복사해 붙이면 **셸 히스토리에 남는다.**"""
    repo = RepoConfig(name="dt-core", url=URL, path=str(tmp_path / "없음"),
                      token="ghp_secret_token_value")
    assert "ghp_secret_token_value" not in "\n".join(plan_for(repo, status_of(repo)))


# ── sync: 네트워크를 타는 유일한 곳 ────────────────────────────────

def test_origin이_다르면_sync가_건드리지_않는다(tmp_path):
    """자동으로 고치면 **사람이 의도한 포크를 조용히 날린다.** 사람이 봐야 한다."""
    root = _make_repo(tmp_path / "dt-core", origin="https://git.example.com/someone/fork")
    repo = repo_at(root)
    outcome, why = sync(repo, status_of(repo))
    assert outcome == "skipped" and "origin" in why


def test_붙을_수_없으면_값으로_실패한다(tmp_path):
    """사내 밖에서는 **늘** 실패한다 — 던지면 CLI가 죽는다.

    **실패하면 무엇이 왔는지 그대로 보여 준다.** `outcome == "failed" and why`처럼
    묶어서 단정하면 어느 쪽이 틀렸는지 안 보이고, 사내에서 이 테스트가 깨졌을 때
    실제로 그래서 한 번 더 물어봐야 했다.
    """
    repo = RepoConfig(name="dt-core", url="https://127.0.0.1:1/없는/레포",
                      path=str(tmp_path / "없음"))
    from tests.support import running_source

    outcome, why = sync(repo, status_of(repo))
    assert outcome == "failed", f"outcome={outcome!r} why={why!r}"
    assert why.strip(), (
        "실패했는데 이유가 비어 있다 — 왜인지 아무도 모르는 상태다.\n"
        "지금 코드는 출력이 없어도 종료 코드를 적게 돼 있다. 비어 있다면 "
        "**src/가 tests/보다 낡은 것**이다. 지금 돌고 있는 sync:\n"
        + running_source(sync))


def test_실패_메시지에_토큰이_안_샌다(tmp_path):
    """git이 뱉은 말은 화면으로도 로그로도 간다.

    **`_scrub`을 직접 부른다.** 통합 경로만 보면 git이 우연히 토큰을 안 뱉는 것을
    "우리가 지웠다"로 읽는다 — 실제로 그랬다. 방어를 지워도 초록이었다.
    """
    from src.knowledge.checkout import _scrub

    repo = RepoConfig(name="dt-core", url="https://127.0.0.1:1/없는/레포",
                      path=str(tmp_path / "없음"), token="ghp_secret_token_value")
    leaked = "fatal: Authorization: Basic ghp_secret_token_value rejected"
    assert "ghp_secret_token_value" not in _scrub(leaked, repo)
    assert "***" in _scrub(leaked, repo)

    _, why = sync(repo, status_of(repo))
    assert "ghp_secret_token_value" not in why


def test_로컬_레포를_실제로_클론한다(tmp_path):
    """**네트워크 없이 sync의 성공 경로를 본다.** 실패 경로만 테스트하면
    "늘 실패하는 함수"여도 전부 통과한다."""
    origin = _make_repo(tmp_path / "origin")
    target = tmp_path / "clone"
    # 로컬 경로는 url 검증(https/ssh만)을 안 통과하므로 모델을 우회해 만든다.
    # 검증 자체는 위 스키마 테스트가 지킨다.
    repo = RepoConfig.model_construct(name="dt-core", url=str(origin),
                                      path=str(target), token=None)
    outcome, where = sync(repo, status_of(repo))
    assert outcome == "cloned", where
    assert (target / ".git").exists() and (target / "a.py").exists()


def test_subprocess_출력이_None이어도_안_죽는다(monkeypatch, tmp_path):
    """**진단 코드가 자기가 먼저 죽으면 안 된다.**

    `capture_output=True`가 빠진 채로 돌면 `stdout`/`stderr`가 `None`이고
    `.strip()`이 `AttributeError`로 죽는다 — handover의 Windows 함정이고 이 리포가
    이미 한 번 물렸다. 그 순간 "왜 실패했나"를 말해 줄 함수가 사라진다.
    """
    import subprocess as sp

    from src.knowledge.checkout import _git

    class Dead:
        returncode, stdout, stderr = 1, None, None

    monkeypatch.setattr(sp, "run", lambda *a, **k: Dead())
    code, out, err = _git(tmp_path, "remote", "get-url", "origin")
    assert (code, out, err) == (1, "", "")


def test_clone이_실패해도_출력이_None이면_안_죽는다(monkeypatch, tmp_path):
    import subprocess as sp

    class Dead:
        returncode, stdout, stderr = 128, None, None

    monkeypatch.setattr(sp, "run", lambda *a, **k: Dead())
    repo = repo_at(tmp_path / "없음")
    outcome, why = sync(repo, status_of(repo))
    assert outcome == "failed" and isinstance(why, str)


def test_실패에는_반드시_이유가_붙는다(monkeypatch, tmp_path):
    """**이유 없는 실패를 만들지 않는다.**

    git이 아무 말도 안 하고 끝나는 경우가 있다(환경에 따라 stdout으로만 뱉기도 한다).
    그때 빈 문자열을 돌려주면 "실패했는데 왜인지 아무도 모른다"가 되고, 그건
    `unreachable`을 `ok`로 적는 것과 같은 종류의 거짓말이다.
    """
    import subprocess as sp

    class Silent:
        returncode, stdout, stderr = 128, "", ""

    monkeypatch.setattr(sp, "run", lambda *a, **k: Silent())
    repo = repo_at(tmp_path / "없음")
    outcome, why = sync(repo, status_of(repo))
    assert outcome == "failed"
    assert why.strip(), "실패했는데 이유가 비어 있다"
    assert "128" in why


# ── submodule: git이 조용히 거짓말하는 자리 ──────────────────────────

def _make_parent_with_submodule(tmp_path):
    """부모 레포 하나 + 그 안에 진짜 submodule 하나.

    가짜 `.gitmodules`를 적어 두는 것으로는 증명이 안 된다 — 우리가 잡으려는 것은
    **git이 실제로 하는 행동**(gitlink를 남기고, 안 채워진 자리를 조용히 건너뛰는
    것)이지 우리가 파일에 뭘 적었는가가 아니다.
    """
    lib = _make_repo(tmp_path / "libs", origin="https://git.example.com/team/libs")
    (lib / "kafka.json").write_text('{"topic": "ALARM_EVENT"}\n', encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=lib, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "topic"], cwd=lib, check=True,
                   capture_output=True)

    parent = _make_repo(tmp_path / "parent")
    subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "add",
                    "-q", str(lib), "vendor/libs"], cwd=parent, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-qm", "add submodule"], cwd=parent, check=True,
                   capture_output=True)
    return parent


def _flat_clone(tmp_path, parent):
    """`--recurse-submodules` **없이** 클론한다 — 사내에서 기본으로 일어나는 모양."""
    target = tmp_path / "flat"
    subprocess.run(["git", "-c", "protocol.file.allow=always", "clone", "-q",
                    str(parent), str(target)], check=True, capture_output=True)
    # 로컬 경로 submodule을 채우려면 git이 file 프로토콜을 허락해야 한다.
    # 테스트 픽스처의 사정이지 제품 코드의 사정이 아니라 여기서만 켠다.
    subprocess.run(["git", "config", "protocol.file.allow", "always"], cwd=target,
                   check=True, capture_output=True)
    return target


def test_gitmodules에서_경로만_뽑는다():
    text = ('[submodule "vendor/libs"]\n'
            '\tpath = vendor/libs\n'
            '\turl = https://git.example.com/team/libs\n'
            '[submodule "x"]\n'
            '\tpath = third_party/x\n')
    assert parse_gitmodules(text) == ["third_party/x", "vendor/libs"]


def test_path로_시작하는_다른_키를_안_먹는다():
    """`startswith("path")`만 보면 `pathspec`도 경로로 읽힌다 — 그러면 있지도 않은
    submodule을 "안 채워졌다"고 신고해서, 진짜 경고가 묻힌다."""
    assert parse_gitmodules("\tpathspec = vendor/x\n") == []


def test_submodule이_없으면_빈_목록이다(tmp_path):
    """`.gitmodules`가 없는 것은 **정상**이다 — 오류로 만들면 대부분의 레포가 빨개진다."""
    repo = repo_at(_make_repo(tmp_path / "dt-core"))
    assert submodules_at(repo, "main") == []


def test_커밋이_선언한_submodule을_찾는다(tmp_path):
    parent = _make_parent_with_submodule(tmp_path)
    assert submodules_at(repo_at(parent), "main") == ["vendor/libs"]


def test_안_채워진_submodule을_찾아낸다(tmp_path):
    """**이 파일의 두 번째 자물쇠다.**

    안 채워진 채로 두면 `git grep`이 종료코드 1에 출력 없이 끝난다 — 우리는 그걸
    "결과 없음"으로 읽고, 2차의 판정은 "코드에 그런 게 없다"가 된다.
    origin 대조와 같은 종류의 사고다: 조용하고, 확신에 차 있고, 틀렸다.
    """
    parent = _make_parent_with_submodule(tmp_path)
    flat = _flat_clone(tmp_path, parent)
    repo = repo_at(flat)
    subs = submodules_at(repo, "main")
    assert subs == ["vendor/libs"]
    assert unpopulated(repo, subs) == ["vendor/libs"]
    # 그 자리가 실제로 비어 있는지 — 우리 판단이 아니라 디스크를 본다.
    assert list((flat / "vendor" / "libs").iterdir()) == []


def test_채워졌으면_신고하지_않는다(tmp_path):
    parent = _make_parent_with_submodule(tmp_path)
    flat = _flat_clone(tmp_path, parent)
    subprocess.run(["git", "submodule", "update", "--init", "--recursive"], cwd=flat,
                   check=True, capture_output=True)
    repo = repo_at(flat)
    assert unpopulated(repo, submodules_at(repo, "main")) == []


def test_plan이_submodule_채우는_줄까지_준다(tmp_path):
    """fetch만 적어 주면 사람이 그대로 따라 해도 트리가 반쪽으로 남는다 —
    `fetch --recurse-submodules`는 **이미 채워진** 것만 갱신한다."""
    repo = repo_at(_make_repo(tmp_path / "dt-core"))
    lines = plan_for(repo, status_of(repo))
    assert any("submodule update --init" in line for line in lines), lines


def test_sync가_안_채워진_submodule을_채운다(tmp_path):
    """`fetch`는 안 채워진 submodule을 절대 안 채운다. `code sync`를 돌리고도
    트리가 반쪽이면, 사람은 "동기화했다"고 믿은 채로 못 보는 코드를 갖게 된다."""
    parent = _make_parent_with_submodule(tmp_path)
    flat = _flat_clone(tmp_path, parent)
    repo = RepoConfig.model_construct(name="dt-core", url=str(parent),
                                      path=str(flat), token=None)
    outcome, where = sync(repo, status_of(repo))
    assert outcome == "fetched", where
    assert (flat / "vendor" / "libs" / "kafka.json").exists()
    assert unpopulated(repo, submodules_at(repo, "main")) == []


def _commit_in(root, message="more"):
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=root, check=True,
                   capture_output=True)


def test_submodule_안의_config_층을_없다고_하지_않는다(tmp_path):
    """**측정으로 잡은 오진이다.**

    `git cat-file -e <커밋>:<서브>/…`는 submodule이 **채워져 있어도** 실패한다
    (`exists on disk, but not in 'main'`). 그대로 두면 공용 라이브러리에 사는
    config 층을 전부 "없다"로 신고하고, `code status`가 "config_paths를 고쳐라"라는
    **틀린 처방**을 내놓는다 — 경로는 맞았는데 사람은 맞는 경로를 고치게 된다.
    """
    parent = _make_parent_with_submodule(tmp_path)
    full = tmp_path / "full"
    subprocess.run(["git", "-c", "protocol.file.allow=always", "clone", "-q",
                    "--recurse-submodules", str(parent), str(full)],
                   check=True, capture_output=True)
    repo = repo_at(full)
    here, gone = config_layers(repo, "main", ["a.py", "vendor/libs/kafka.json"])
    assert gone == [], f"submodule 안의 층을 없다고 했다: {gone}"
    assert here == ["a.py", "vendor/libs/kafka.json"]


def test_진짜로_없는_경로는_여전히_없다고_한다(tmp_path):
    """경계를 넘게 만들면서 검사가 **아무거나 통과시키게** 되면 안 된다."""
    parent = _make_parent_with_submodule(tmp_path)
    full = tmp_path / "full"
    subprocess.run(["git", "-c", "protocol.file.allow=always", "clone", "-q",
                    "--recurse-submodules", str(parent), str(full)],
                   check=True, capture_output=True)
    _, gone = config_layers(repo_at(full), "main", ["vendor/libs/없는파일.json"])
    assert gone == ["vendor/libs/없는파일.json"]


def test_채워졌어도_그_커밋의_버전이_없으면_찾아낸다(tmp_path):
    """**세 번째 상태다.** `.git`이 있으니 `unpopulated`는 "채워졌다"고 말한다.

    부모만 fetch되고 submodule은 안 당겨진 트리에서 실제로 생긴다. 이걸 못 보면
    `code status`는 초록인데 읽기는 `exists on disk, but not in …`으로 실패한다 —
    사람은 그 말을 "파일이 없다"로 읽는다.
    """
    parent = _make_parent_with_submodule(tmp_path)
    flat = _flat_clone(tmp_path, parent)
    subprocess.run(["git", "submodule", "update", "--init", "--recursive"], cwd=flat,
                   check=True, capture_output=True)
    repo = repo_at(flat)
    assert unpopulated(repo, submodules_at(repo, "main")) == []   # 채워는 졌다
    assert stale(repo, "main", submodules_at(repo, "main")) == []

    # 원본 submodule이 앞서 나가고, 부모가 그걸 가리키게 된다.
    lib = tmp_path / "libs"
    (lib / "kafka.json").write_text('{"topic": "NEW"}\n', encoding="utf-8")
    _commit_in(lib, "v2")
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=parent / "vendor" / "libs",
                   check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=parent / "vendor" / "libs",
                   check=True, capture_output=True)
    _commit_in(parent, "sub moved")
    # **부모만** 당긴다 — submodule은 그대로 둔다. 체크아웃된 브랜치로는 직접
    # fetch가 안 되므로 받아서 ff-merge한다(작업 트리의 submodule은 안 움직인다).
    subprocess.run(["git", "-c", "fetch.recurseSubmodules=no", "fetch", "-q", "origin",
                    "main"], cwd=flat, check=True, capture_output=True)
    subprocess.run(["git", "-c", "submodule.recurse=false", "merge", "--ff-only", "-q",
                    "FETCH_HEAD"], cwd=flat, check=True, capture_output=True)

    subs = submodules_at(repo, "main")
    assert unpopulated(repo, subs) == []        # 여전히 "채워짐"으로 보인다
    assert stale(repo, "main", subs) == ["vendor/libs"]


def test_토큰_헤더는_호스트에_묶인다(tmp_path):
    """`-c`는 **submodule 하위 클론까지 전파된다**(측정). 묶지 않은 헤더를 쓰면
    `.gitmodules`가 가리키는 아무 호스트에나 우리 토큰이 날아간다."""
    repo = RepoConfig(name="dt-core", url="https://git.example.com/team/dt-core",
                      path=str(tmp_path / "x"), token="ghp_secret_token_value")
    args = auth_args(repo)
    assert args[0] == "-c"
    assert args[1].startswith("http.https://git.example.com/.extraHeader=")


def test_ssh_url에는_토큰_헤더를_안_붙인다(tmp_path):
    """헤더는 http(s)에서만 뜻이 있다. ssh에 얹으면 **되는 줄 알고 안 되는** 설정이 된다."""
    repo = RepoConfig(name="dt-core", url="git@git.example.com:team/dt-core",
                      path=str(tmp_path / "x"), token="ghp_secret_token_value")
    assert auth_args(repo) == []
