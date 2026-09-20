"""체크아웃이 **우리가 읽을 수 있는 상태인가** — 그리고 아니면 무엇을 해야 하는가.

## 왜 status / plan / sync 셋인가

우리 프로세스가 사내 git에 붙을 수 있는 것은 사내에 배포됐을 때뿐이다. 개발
환경에서는 못 붙는다([decisions ⑤]). 그래서 네트워크를 타는 것 하나만 떼어 둔다:

| | 네트워크 | 어디서나 |
|---|---|---|
| `status` | 없음 | ✅ 진단 |
| `plan` | 없음 | ✅ 사람이 직접 칠 git 명령을 출력 |
| `sync` | 있음 | 사내에서만 |

## `origin` 대조는 문서가 아니라 자물쇠다

누가 포크나 다른 레포를 그 경로에 클론하면 우리는 그럴듯한 코드를 읽고 그럴듯한
로직 명세를 만들고 그럴듯한 재계산을 한다 — **전부 틀린 채로.** "코드를 못 읽었다"
보다 훨씬 나쁘다. 실패가 조용하고 판정은 확신에 차 있다.

## 토큰은 URL에 안 들어간다

`https://<토큰>@호스트/…`로 클론하면 git이 **`.git/config`에 평문으로 저장한다.**
그래서 remote는 깨끗한 url로 두고 인증은 명령마다 헤더로 넘긴다. `plan`이 출력하는
명령에도 토큰을 찍지 않는다 — 사람이 그걸 복사해 붙이면 셸 히스토리에 남는다.
"""
import base64
import subprocess
from pathlib import Path
from typing import Literal

from src.config.schema_site import RepoConfig
from src.domain.base import StrictModel

_TIMEOUT_S = 120


class RepoStatus(StrictModel):
    """레포 하나의 상태. **문제를 모아서** 돌려준다(boot.py와 같은 철학)."""

    name: str
    path: str
    exists: bool = False
    is_git: bool = False
    origin: str = ""
    origin_matches: bool = False
    problems: list[str] = []

    @property
    def ready(self) -> bool:
        return self.exists and self.is_git and self.origin_matches and not self.problems


def _text(value) -> str:
    """`subprocess`의 출력은 **None일 수 있다.**

    `capture_output=True`면 문자열이지만, 그 인자가 빠진 채로 돌면 `None`이 오고
    `.strip()`이 `AttributeError`로 죽는다. handover의 Windows 함정이 정확히 이것이고,
    이 리포에서 이미 한 번 물렸다(`test_dead_settings`의 grep 호출).

    **죽는 자리가 진단 코드라는 것이 제일 나쁘다** — 왜 실패했는지 말해 줘야 할
    함수가 자기가 먼저 죽으면 사람은 원인을 못 본다.
    """
    return (value or "").strip()


def _git(root: Path, *args, timeout: int = 10) -> tuple[int, str, str]:
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                              text=True, timeout=timeout)
        return done.returncode, _text(done.stdout), _text(done.stderr)
    except FileNotFoundError:
        return 127, "", "git 실행 파일이 없다"
    except subprocess.TimeoutExpired:
        return 124, "", f"{timeout}초 안에 안 끝났다"
    except Exception as exc:                                        # noqa: BLE001
        return 1, "", f"{type(exc).__name__}: {exc}"


def _same_repo(a: str, b: str) -> bool:
    """`.git` 꼬리와 끝 슬래시만 무시하고 비교한다. **호스트·경로는 안 봐준다.**"""
    normal = lambda u: u.rstrip("/").removesuffix(".git").strip()   # noqa: E731
    return normal(a) == normal(b)


def status_of(repo: RepoConfig) -> RepoStatus:
    """네트워크 없이 볼 수 있는 것 전부(⑤가 적은 넷)."""
    root = Path(repo.path)
    state = RepoStatus(name=repo.name, path=str(root))
    if not root.exists():
        state.problems.append(f"경로가 없다 — {root}")
        return state
    state.exists = True
    if not (root / ".git").exists():
        state.problems.append(
            ".git이 없다 — 작업 트리만 복사되면 `git show <배포커밋>:경로`가 통째로 "
            "안 된다. 우리 설계의 전제가 무너지는 자리다")
        return state
    state.is_git = True

    code, out, err = _git(root, "remote", "get-url", "origin")
    if code != 0:
        state.problems.append(f"origin을 읽을 수 없다 — {err or code}")
        return state
    state.origin = out
    state.origin_matches = _same_repo(out, repo.url)
    if not state.origin_matches:
        state.problems.append(
            f"origin이 config와 다르다 — 여기는 {out}, config는 {repo.url}. "
            f"다른 레포를 읽으면 **그럴듯하게 틀린 판정**이 나온다")
    return state


def has_commit(repo: RepoConfig, commit: str) -> bool:
    """그 커밋이 로컬에 실재하는가(⑤-4). 없으면 `code sync`가 필요하다."""
    code, _, _ = _git(Path(repo.path), "cat-file", "-e", f"{commit}^{{commit}}")
    return code == 0


def missing_paths(repo: RepoConfig, commit: str, paths: list[str]) -> list[str]:
    """그 커밋에 **실재하지 않는** config 경로들.

    이름이 대상의 config 파일에 살기 때문에(decisions ③-2), 이 경로가 틀리면 리드는
    **아무것도 못 찾는다.** 그런데 증상은 "조사가 빈손"이라 원인이 안 보인다.
    사람이 손으로 적는 칸이라 오타가 정상적으로 일어난다 — 기동이 아니라 여기서
    잡는 이유는 커밋마다 답이 다르기 때문이다.
    """
    gone = []
    for path in paths:
        code, _, _ = _git(Path(repo.path), "cat-file", "-e", f"{commit}:{path}")
        if code != 0:
            gone.append(path)
    return gone


def config_layers(repo: RepoConfig, commit: str, paths: list[str]) -> tuple[list[str], list[str]]:
    """그 커밋에 **있는 층과 없는 층**을 나눠 돌려준다.

    층은 **선택이다** — 우리 `SITE_LAYERS`와 같다. `fct/{fct}/common.json`이 없는
    법인이 정상이듯, 대상도 그렇다. 그래서 "몇 개가 없다"는 오류가 아니다.

    **하나도 없는 것**이 오류다. 그건 경로 앞머리가 통째로 틀렸다는 뜻이고
    (`config/`인데 `conf/`라고 적었다든가), 그러면 리드는 이름을 영영 못 찾는다.
    """
    missing = missing_paths(repo, commit, paths)
    return [p for p in paths if p not in missing], missing


def plan_for(repo: RepoConfig, state: RepoStatus) -> list[str]:
    """사람이 직접 칠 명령. **토큰은 안 찍는다** — 셸 히스토리에 남는다."""
    if not state.exists:
        return [f"git clone {repo.url} {repo.path}    # 인증은 자격 증명 도우미에 맡겨라"]
    if not state.is_git:
        return [f"# {repo.path}에 .git이 없다 — 지우고 다시 클론하거나 올바른 경로를 config에 적어라",
                f"git clone {repo.url} {repo.path}"]
    if not state.origin_matches:
        return [f"# origin이 다르다 ({state.origin}). 의도한 것이 아니면:",
                f"git -C {repo.path} remote set-url origin {repo.url}"]
    return [f"git -C {repo.path} fetch --all --prune"]


Outcome = Literal["cloned", "fetched", "failed", "skipped"]


def sync(repo: RepoConfig, state: RepoStatus) -> tuple[Outcome, str]:
    """**여기서만 네트워크를 탄다.** 실패는 값으로 돌린다 — 사내 밖에서는 늘 실패한다.

    토큰은 `http.extraHeader`로 **이 명령에만** 넘긴다. remote URL에 박으면
    `.git/config`에 평문으로 남는다.
    """
    header = []
    if repo.token is not None:
        # GitHub fine-grained token은 basic 인증의 비밀번호 자리에 온다.
        raw = f"x-access-token:{repo.token.get_secret_value()}".encode()
        header = ["-c", f"http.extraHeader=Authorization: Basic "
                        f"{base64.b64encode(raw).decode()}"]

    if state.is_git and not state.origin_matches:
        return "skipped", "origin이 config와 달라 건드리지 않는다 — 사람이 확인해야 한다"

    if not state.exists:
        try:
            done = subprocess.run(
                ["git", *header, "clone", repo.url, repo.path],
                capture_output=True, text=True, timeout=_TIMEOUT_S)
        except Exception as exc:                                    # noqa: BLE001
            return "failed", f"{type(exc).__name__}: {exc}"
        if done.returncode != 0:
            # **이유 없는 실패를 만들지 않는다.** git이 아무 말도 안 하고 끝나는
            # 경우가 있고(환경에 따라 stdout으로만 뱉기도 한다), 빈 문자열을 돌려주면
            # "실패했는데 왜인지 아무도 모른다"가 된다 — `unreachable`을 `ok`로
            # 적는 것과 같은 종류의 거짓말이다.
            return "failed", _scrub(
                _text(done.stderr) or _text(done.stdout)
                or f"git clone이 {done.returncode}로 끝났다(출력 없음)", repo)
        return "cloned", repo.path

    code, out, err = _git(Path(repo.path), *header, "fetch", "--all", "--prune",
                          timeout=_TIMEOUT_S)
    if code != 0:
        return "failed", _scrub(
            err or out or f"git fetch가 {code}로 끝났다(출력 없음)", repo)
    return "fetched", repo.path


def _scrub(message: str, repo: RepoConfig) -> str:
    """git이 뱉은 말에서 토큰을 지운다. 오류 메시지는 로그로도 화면으로도 간다."""
    if repo.token is None:
        return message
    return message.replace(repo.token.get_secret_value(), "***")
