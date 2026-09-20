"""체크아웃 진단 — **자물쇠가 실제로 잠기는가.**

`origin` 대조가 이 파일의 핵심이다. 누가 다른 레포를 그 경로에 클론하면 우리는
그럴듯한 코드를 읽고 그럴듯한 판정을 낸다 — **전부 틀린 채로.** "못 읽었다"보다
훨씬 나쁘다: 실패가 조용하고 판정은 확신에 차 있다.
"""
import shutil
import subprocess

import pytest

from src.config.schema_site import RepoConfig
from src.knowledge.checkout import has_commit, plan_for, status_of, sync

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
    """사내 밖에서는 **늘** 실패한다 — 던지면 CLI가 죽는다."""
    repo = RepoConfig(name="dt-core", url="https://127.0.0.1:1/없는/레포",
                      path=str(tmp_path / "없음"))
    outcome, why = sync(repo, status_of(repo))
    assert outcome == "failed" and why


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
