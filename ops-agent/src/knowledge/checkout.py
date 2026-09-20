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

## git 출력은 **로캘이 아니라 UTF-8**로 읽는다

`subprocess.run(..., text=True)`는 로캘 인코딩으로 디코딩한다. 한국어 Windows의
ANSI 코드 페이지는 cp949이고, git은 **UTF-8로 뱉는다.** 그래서 경로·브랜치명·
커밋 메시지에 한글이 하나라도 있으면 `UnicodeDecodeError`가 난다(재현함).

그게 여기서 특히 나쁜 이유: 아래 `_git`의 `except Exception`이 그걸 먹고
`code=1`을 돌려준다. 그러면 `submodules_at`는 **"이 커밋엔 submodule이 없다"**,
`status_of`는 **"origin을 읽을 수 없다"**가 된다 — 조용하고, 그럴듯하고, 틀렸다.

## 토큰은 URL에 안 들어간다 — 그리고 호스트에 묶인다

`https://<토큰>@호스트/…`로 클론하면 git이 **`.git/config`에 평문으로 저장한다.**
그래서 remote는 깨끗한 url로 두고 인증은 명령마다 헤더로 넘긴다. `plan`이 출력하는
명령에도 토큰을 찍지 않는다 — 사람이 그걸 복사해 붙이면 셸 히스토리에 남는다.

헤더는 `http.extraHeader`가 아니라 **`http.<스킴://호스트>/.extraHeader`**로 준다.
측정해 보니 `-c`로 준 설정이 **submodule 하위 클론까지 전파된다**(`-c
protocol.file.allow=never`로 확인: submodule 클론이 막혔다). 그래서 묶지 않은
헤더를 쓰면 `.gitmodules`가 가리키는 **아무 호스트에나 우리 토큰이 날아간다.**
호스트로 묶으면 사내 호스트의 submodule은 여전히 인증되고 밖으로는 안 나간다.
"""
import base64
import re
import subprocess
from pathlib import Path
from typing import Literal

from src.config.schema_site import RepoConfig
from src.domain.base import StrictModel

_TIMEOUT_S = 120
_PATH_LINE = re.compile(r"path\s*=\s*(.+)$")
_HTTP_HOST = re.compile(r"(https?://[^/]+)")


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
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
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
    subs = submodules_at(repo, commit)
    return [path for path in paths if not _path_exists(repo, commit, path, subs)]


def _path_exists(repo: RepoConfig, commit: str, path: str, subs: list[str]) -> bool:
    """**submodule 경계를 넘어서** 실재 여부를 본다.

    `git cat-file -e <커밋>:<서브>/…`는 submodule이 **채워져 있어도** 실패한다
    (측정: `exists on disk, but not in 'main'`). 그대로 두면 공용 라이브러리
    submodule에 사는 config 층을 전부 "없다"로 신고하고, `code status`가
    "config_paths를 고쳐라"라는 **틀린 처방**을 내놓는다 — 경로는 맞았는데.
    """
    sub = containing_submodule(subs, path)
    if not sub:
        code, _, _ = _git(Path(repo.path), "cat-file", "-e", f"{commit}:{path}")
        return code == 0
    sha = gitlink_at(repo, commit, sub)
    rest = path[len(sub):].lstrip("/")
    if not sha or not rest:
        return False
    code, _, _ = _git(Path(repo.path) / sub, "cat-file", "-e", f"{sha}:{rest}")
    return code == 0


def config_layers(repo: RepoConfig, commit: str, paths: list[str]) -> tuple[list[str], list[str]]:
    """그 커밋에 **있는 층과 없는 층**을 나눠 돌려준다.

    층은 **선택이다** — 우리 `SITE_LAYERS`와 같다. `fct/{fct}/common.json`이 없는
    법인이 정상이듯, 대상도 그렇다. 그래서 "몇 개가 없다"는 오류가 아니다.

    **하나도 없는 것**이 오류다. 그건 경로 앞머리가 통째로 틀렸다는 뜻이고
    (`config/`인데 `conf/`라고 적었다든가), 그러면 리드는 이름을 영영 못 찾는다.
    """
    missing = missing_paths(repo, commit, paths)
    return [p for p in paths if p not in missing], missing


def parse_gitmodules(text: str) -> list[str]:
    """`.gitmodules`에서 submodule 경로만 뽑는다.

    ini 파서를 안 쓰는 이유: 섹션 이름이 `[submodule "a/b"]`처럼 따옴표와 슬래시를
    달고 오고 사람이 손으로도 고치는 파일이라, 필요한 키 하나만 줄 단위로 보는
    편이 덜 깨진다. `path`로 시작하는 다른 키(`pathspec` 같은)를 안 먹으려고
    `=`까지 붙여서 본다.
    """
    found = set()
    for line in text.splitlines():
        match = _PATH_LINE.match(line.strip())
        if match:
            found.add(match.group(1).strip())
    return sorted(found)


def submodules_at(repo: RepoConfig, commit: str) -> list[str]:
    """그 커밋이 선언한 submodule 경로들.

    ## 왜 이걸 봐야 하는가 — 측정한 사실

    안 채워진 submodule을 두고 git이 실제로 하는 말(git 2.43에서 직접 확인):

    | 명령 | 결과 |
    |---|---|
    | `git show <커밋>:서브/경로` | `does not exist in '<커밋>'` — **거짓말이다.** 있다 |
    | `git grep <패턴> <커밋>` | 종료코드 1, stdout 비어 있음, **stderr도 비어 있음** |
    | `git grep --recurse-submodules …` | 똑같이 조용한 0건 |

    두 번째가 위험하다. 2차의 `code.grep`이 0건을 "코드에 그런 게 없다"로 읽는데
    실제로는 **우리가 못 본 것**이다. 5단계의 `unreachable`을 `ok`로 적는 것과
    같은 종류의 거짓이고, 조용하다는 점에서 더 나쁘다.

    ## 버전은 가정하지 않는다

    부모 레포의 배포 커밋이 submodule의 커밋 SHA를 박아 둔다(gitlink). 그래서
    "최신이 맞겠지"가 아니라 **그 배포가 실제로 쓴 버전**을 안다 —
    `Pin.how="declared"`와 같은 성질이다.
    """
    code, out, _ = _git(Path(repo.path), "show", f"{commit}:.gitmodules")
    if code != 0:
        return []                       # `.gitmodules`가 없다 = submodule이 없다
    return parse_gitmodules(out)


def unpopulated(repo: RepoConfig, paths: list[str]) -> list[str]:
    """git이 **안 들여다보는** submodule들.

    처음엔 `(경로/.git)이 있나`로 봤다. **틀렸다.** 디렉터리를 사람이 직접 클론해
    넣어 `.git`이 멀쩡히 있어도, 로컬에 *등록*(`git submodule init`)이 안 돼 있으면
    `git grep --recurse-submodules`는 그 안을 **조용히 건너뛴다**(측정: 종료코드 1,
    출력 없음). 즉 "채워졌다"고 말하면서 grep은 계속 못 보는 상태가 존재한다.

    `git submodule status`의 앞 글자가 정확히 그 신호다:

    | 앞 글자 | 뜻 | grep이 보나 |
    |---|---|---|
    | `-` | 등록 안 됨 | ❌ 조용히 0건 |
    | (공백) | 정상 | ✅ |
    | `+` | 박힌 SHA와 체크아웃이 다름 | ✅ (트리의 SHA로 읽는다) |
    """
    code, out, _ = _git(Path(repo.path), "submodule", "status")
    if code != 0:
        # 못 물어봤으면 **"읽을 수 있다"고 말하지 않는다.** 모르는 것을
        # 괜찮은 것으로 적는 것이 이 리포가 제일 싫어하는 실패다.
        return list(paths)
    marks = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line[1:].split()          # `<앞글자><sha> <경로> (설명)`
        if len(parts) >= 2:
            marks[parts[1]] = line[0]
    return [path for path in paths if marks.get(path, "-") == "-"]


def auth_args(repo: RepoConfig) -> list[str]:
    """이 명령에만 붙일 인증 설정. **호스트로 묶는다.**

    `-c`로 준 설정은 submodule 하위 클론까지 전파된다(측정함). 묶지 않은
    `http.extraHeader`를 쓰면 `.gitmodules`가 가리키는 아무 호스트에나 토큰이
    날아간다 — 공용 라이브러리가 다른 호스트에 있는 것은 흔한 일이다.

    ssh url에는 아무것도 안 붙인다. 헤더는 http(s)에서만 뜻이 있고, ssh는 키로
    인증한다 — 거기에 토큰을 얹으면 **되는 줄 알고 안 되는** 설정이 된다.
    """
    if repo.token is None:
        return []
    host = _HTTP_HOST.match(repo.url)
    if not host:
        return []
    # GitHub fine-grained token은 basic 인증의 비밀번호 자리에 온다.
    raw = f"x-access-token:{repo.token.get_secret_value()}".encode()
    return ["-c", f"http.{host.group(1)}/.extraHeader=Authorization: Basic "
                  f"{base64.b64encode(raw).decode()}"]


def containing_submodule(subs: list[str], path: str) -> str:
    """그 경로를 품고 있는 submodule. 중첩이면 **가장 깊은 것**을 고른다."""
    hits = [sub for sub in subs
            if path == sub or path.startswith(sub.rstrip("/") + "/")]
    return max(hits, key=len) if hits else ""


def gitlink_at(repo: RepoConfig, commit: str, sub: str) -> str:
    """그 커밋이 submodule에 박아 둔 SHA. 못 읽으면 빈 문자열."""
    code, out, _ = _git(Path(repo.path), "ls-tree", commit, "--", sub)
    if code != 0:
        return ""
    for line in out.splitlines():
        parts = line.split(" ", 2)            # `160000 commit <sha>\t<경로>`
        if len(parts) == 3 and parts[1] == "commit":
            return parts[2].split("\t")[0].strip()
    return ""


def stale(repo: RepoConfig, commit: str, paths: list[str]) -> list[str]:
    """**세 번째 상태**: 채워져는 있는데 그 커밋이 박은 버전의 객체가 없다.

    `.git`이 있으니 `unpopulated`는 "채워졌다"고 말한다. 그런데 부모만 fetch되고
    submodule은 안 당겨진 트리에서 git은 이렇게 답한다(측정):

    | | |
    |---|---|
    | `git -C <서브> show <SHA>:경로` | `exists on disk, but not in '<SHA>'` |
    | `git grep --recurse-submodules <커밋>` | 종료코드 128 · `unable to read tree` |

    조용하지는 않다는 것이 다행이다. 그러나 저 말을 그대로 사람이나 리드에게
    넘기면 "파일이 없다"로 읽힌다 — 실제로는 **우리가 그 버전을 안 가진 것**이다.
    `code sync`가 실제로 고친다는 것도 확인했다.
    """
    behind = []
    for sub in paths:
        root = Path(repo.path) / sub
        if not (root / ".git").exists():
            continue                          # 그건 `unpopulated`가 센다
        sha = gitlink_at(repo, commit, sub)
        if not sha:
            continue
        code, _, _ = _git(root, "cat-file", "-e", f"{sha}^{{commit}}")
        if code != 0:
            behind.append(sub)
    return behind


def plan_for(repo: RepoConfig, state: RepoStatus) -> list[str]:
    """사람이 직접 칠 명령. **토큰은 안 찍는다** — 셸 히스토리에 남는다."""
    if not state.exists:
        return [f"git clone --recurse-submodules {repo.url} {repo.path}"
                f"    # 인증은 자격 증명 도우미에 맡겨라"]
    if not state.is_git:
        return [f"# {repo.path}에 .git이 없다 — 지우고 다시 클론하거나 올바른 경로를 config에 적어라",
                f"git clone --recurse-submodules {repo.url} {repo.path}"]
    if not state.origin_matches:
        return [f"# origin이 다르다 ({state.origin}). 의도한 것이 아니면:",
                f"git -C {repo.path} remote set-url origin {repo.url}"]
    # fetch는 **안 채워진** submodule을 채우지 않는다 — 두 줄이어야 트리가 읽힌다.
    return [f"git -C {repo.path} fetch --all --prune --recurse-submodules",
            f"git -C {repo.path} submodule update --init --recursive"]


Outcome = Literal["cloned", "fetched", "failed", "skipped"]


def sync(repo: RepoConfig, state: RepoStatus) -> tuple[Outcome, str]:
    """**여기서만 네트워크를 탄다.** 실패는 값으로 돌린다 — 사내 밖에서는 늘 실패한다.

    토큰은 `http.extraHeader`로 **이 명령에만** 넘긴다. remote URL에 박으면
    `.git/config`에 평문으로 남는다.
    """
    header = auth_args(repo)

    if state.is_git and not state.origin_matches:
        return "skipped", "origin이 config와 달라 건드리지 않는다 — 사람이 확인해야 한다"

    if not state.exists:
        try:
            done = subprocess.run(
                # **submodule까지 가져온다.** 안 그러면 그 자리가 빈 디렉터리로
                # 남고, `git show`·`git grep`이 조용히 아무것도 못 찾는다.
                ["git", *header, "clone", "--recurse-submodules",
                 repo.url, repo.path],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=_TIMEOUT_S)
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
        outcome: Outcome = "cloned"
    else:
        code, out, err = _git(Path(repo.path), *header, "fetch", "--all", "--prune",
                              "--recurse-submodules", timeout=_TIMEOUT_S)
        if code != 0:
            return "failed", _scrub(
                err or out or f"git fetch가 {code}로 끝났다(출력 없음)", repo)
        outcome = "fetched"

    # **fetch는 submodule을 채우지 않는다.** `--recurse-submodules`는 이미 채워진
    # 것을 갱신할 뿐이라, 예전에 평평하게 클론된 트리는 fetch를 몇 번 돌려도
    # 계속 빈 디렉터리다. 그 상태로 두면 grep이 조용히 0건을 돌려준다.
    # submodule이 없으면 이 명령은 아무 일도 안 한다.
    code, out, err = _git(Path(repo.path), *header, "submodule", "update",
                          "--init", "--recursive", timeout=_TIMEOUT_S)
    if code != 0:
        # fetch 자체는 됐지만 **반쪽짜리 트리를 성공이라고 부르지 않는다** —
        # 읽을 수 없는 구석이 남은 채로 조사가 돌면 "코드에 없다"가 나온다.
        return "failed", _scrub(
            f"{outcome}는 됐는데 submodule을 못 채웠다 — "
            + (err or out or f"git submodule update가 {code}로 끝났다(출력 없음)"), repo)
    return outcome, repo.path


def _scrub(message: str, repo: RepoConfig) -> str:
    """git이 뱉은 말에서 토큰을 지운다. 오류 메시지는 로그로도 화면으로도 간다."""
    if repo.token is None:
        return message
    return message.replace(repo.token.get_secret_value(), "***")
