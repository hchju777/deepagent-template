"""로컬 체크아웃에서 **커밋을 지정해** 읽는다. 네트워크를 안 탄다.

## 왜 네트워크를 안 타는가

`git show <커밋>:경로`는 로컬 객체만 있으면 된다. partial clone을 안 쓰기로 한
이유가 이것이다(decisions ②) — 그걸 쓰면 `show`가 필요할 때 네트워크를 타고,
**조사가 git 서버에 의존하게 된다.** 서버가 느린 날 조사가 느려지고, 막힌 날
조사가 멈춘다.

## 토큰은 여기 안 온다

읽기는 전부 로컬이라 인증이 필요 없다. 토큰이 필요한 것은 `code sync`(CLI 경계)
하나뿐이고, 그래서 **이 파일은 비밀값을 아예 모른다.**

## submodule은 조용히 거짓말한다

안 채워진 submodule을 두고 git이 실제로 하는 말(2.43에서 측정):
`show`는 "경로가 없다"고 하고, `grep`은 **종료코드 1에 stdout도 stderr도 비어
있다.** 우리 `_git`은 grep의 1을 "결과 없음"으로 읽으므로 그대로 두면 2차의
`code.grep`이 **"코드에 그런 게 없다"**를 단정한다. 그래서 이 파일은 매 읽기마다
그 커밋이 선언한 submodule을 먼저 보고, 못 본 구석이 있으면 봉투가 말하게 한다.

경계를 넘는 읽기는 **가정하지 않는다**: 부모 커밋의 gitlink가 submodule의 SHA를
박아 두므로 우리는 *그 배포가 실제로 쓴 버전*을 읽는다(`Pin.how="declared"`와
같은 성질).

## 실패는 값이다

`ProbeResult.failed`로 흡수한다. 커밋이 없거나 경로가 없는 것은 **일상적인 일**이다
— 배포 커밋 선언이 오래됐거나, 서비스가 그 파일을 안 쓰거나.
"""
import asyncio
from pathlib import Path

from src.config.schema_site import RepoConfig
from src.domain.base import Clock
from src.domain.envelope import ProbeResult
from src.domain.ports import CodeReaderPort
# 순수 파서 하나만 빌려 온다. 같은 `.gitmodules`를 두 계층이 따로 해석하면
# 언젠가 한쪽만 고쳐지고, 그때 `code status`는 "정상"이라 말하는데 읽기는
# 비어서 돌아온다 — 규율 8이 막는 그 실패다.
from src.knowledge.checkout import containing_submodule, parse_gitmodules

# 한 번에 실어 오는 상한. 코드 파일 하나가 이보다 크면 잘라서 주고 봉투가 말한다.
_MAX_CHARS = 20000
_MAX_LINES = 400
_TIMEOUT_S = 20


class RealCodeReader(CodeReaderPort):
    def __init__(self, repos: list[RepoConfig], *, clock: Clock):
        self._repos = {r.name: Path(r.path) for r in repos}
        self._clock = clock
        # 커밋으로 주소가 매겨진 값이라 **절대 안 변한다** — 캐시해도 상할 수 없다.
        # (채워졌는지 여부는 사람이 중간에 바꿀 수 있으므로 캐시하지 않는다.)
        self._declared: dict[tuple[str, str], list[str]] = {}

    def describe(self) -> str:
        return f"git({', '.join(sorted(self._repos)) or '레포 없음'})"

    async def _git(self, repo: str, args: list[str], *, source: str,
                   inside: str = "") -> ProbeResult:
        """`inside`는 레포 안의 상대 경로 — submodule에서 돌릴 때만 쓴다.

        `.git` 검사는 **레포 뿌리**에 대고 한다. submodule 쪽 `.git`은 파일일
        수도 디렉터리일 수도 있고, 그 존재 여부는 호출자가 이미 판단한다.
        """
        root = self._repos.get(repo)
        if root is None:
            return ProbeResult.failed(
                f"등재되지 않은 레포 — {repo}. 아는 것: {', '.join(sorted(self._repos)) or '없음'}",
                source=source, clock=self._clock)
        if not (root / ".git").exists():
            return ProbeResult.failed(
                f"{root}에 .git이 없다 — 작업 트리만 복사되면 커밋을 지정해 읽을 수 없다. "
                f"`code status`를 보라",
                source=source, clock=self._clock)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(root / inside if inside else root), *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_S)
        except asyncio.TimeoutError:
            return ProbeResult.failed(f"{_TIMEOUT_S}초 안에 안 끝났다", source=source,
                                      clock=self._clock)
        except FileNotFoundError:
            return ProbeResult.failed("git 실행 파일이 없다", source=source, clock=self._clock)
        except Exception as exc:                                    # noqa: BLE001
            return ProbeResult.failed(f"{type(exc).__name__}: {exc}", source=source,
                                      clock=self._clock)
        if proc.returncode not in (0, 1):      # grep은 결과 없음이 1이다
            return ProbeResult.failed(
                (err or b"").decode("utf-8", "replace").strip()
                or f"git이 {proc.returncode}로 끝났다",
                source=source, clock=self._clock)
        return ProbeResult.succeeded((out or b"").decode("utf-8", "replace"), source=source,
                                     clock=self._clock)

    # ── submodule 경계 ────────────────────────────────────────────

    async def _declared_subs(self, repo: str, commit: str) -> list[str]:
        key = (repo, commit)
        if key not in self._declared:
            got = await self._git(repo, ["show", f"{commit}:.gitmodules"],
                                  source=f"code.submodules {repo}@{commit}")
            # `.gitmodules`가 없으면 실패로 온다 = submodule이 없다.
            self._declared[key] = ([] if got.status == "error"
                                   else parse_gitmodules(got.data))
        return self._declared[key]

    def _blind(self, repo: str, subs: list[str]) -> list[str]:
        """선언은 됐는데 **로컬에 안 채워진** 것들 — 우리가 못 보는 구석."""
        root = self._repos.get(repo)
        if root is None:
            return []
        return [sub for sub in subs if not (root / sub / ".git").exists()]

    async def _stale(self, repo: str, commit: str, subs: list[str]) -> list[str]:
        """채워져는 있는데 **그 커밋이 박은 버전**의 객체가 없는 것들.

        부모만 fetch되고 submodule은 안 당겨진 트리에서 생긴다. `.git`이 있으므로
        `_blind`는 "채워졌다"고 말한다 — 그래서 상태가 셋이다.
        """
        root = self._repos.get(repo)
        behind = []
        for sub in subs:
            if root is None or not (root / sub / ".git").exists():
                continue                       # 그건 `_blind`가 센다
            sha = await self._gitlink(repo, commit, sub)
            if not sha:
                continue
            got = await self._git(repo, ["cat-file", "-e", f"{sha}^{{commit}}"],
                                  source=f"code.have {repo}:{sub}@{sha[:12]}", inside=sub)
            if got.status == "error":
                behind.append(sub)
        return behind

    def _stale_error(self, subs: list[str]) -> str:
        return (f"submodule {', '.join(subs)}의 **그 커밋이 쓴 버전**이 로컬에 없다 — "
                f"git은 이걸 \"exists on disk, but not in …\"이라고 말하지만 파일이 없는 "
                f"것이 아니라 우리가 그 버전을 안 가진 것이다. `code sync`가 고친다")

    async def _gitlink(self, repo: str, commit: str, sub: str) -> str:
        """부모 커밋이 그 submodule에 박아 둔 SHA. 못 읽으면 빈 문자열."""
        got = await self._git(repo, ["ls-tree", commit, "--", sub],
                              source=f"code.gitlink {repo}@{commit}:{sub}")
        if got.status == "error":
            return ""
        for line in got.data.splitlines():
            # `160000 commit <sha>\t<경로>`
            parts = line.split(" ", 2)
            if len(parts) == 3 and parts[1] == "commit":
                return parts[2].split("\t")[0].strip()
        return ""

    async def show(self, repo: str, commit: str, path: str) -> ProbeResult:
        source = f"code.show {repo}@{commit}:{path}"
        sub = containing_submodule(await self._declared_subs(repo, commit), path)
        if sub:
            return await self._show_across(repo, commit, sub, path, source=source)
        got = await self._git(repo, ["show", f"{commit}:{path}"], source=source)
        return got if got.status == "error" else _clip(got, source, clock=self._clock)

    async def _show_across(self, repo: str, commit: str, sub: str, path: str, *,
                           source: str) -> ProbeResult:
        """submodule 안의 파일을 **부모가 박아 둔 SHA로** 읽는다.

        `git show <부모커밋>:서브/경로`는 submodule 안으로 안 들어간다 — 채워져
        있어도 "exists on disk, but not in '<커밋>'"이라고 한다. 그래서 gitlink를
        직접 풀어서 그 레포 안에서 다시 읽는다. **최신이 아니라 배포가 쓴 버전**이다.
        """
        rest = path[len(sub):].lstrip("/")
        if self._blind(repo, [sub]):
            return ProbeResult.failed(
                f"{sub}은 submodule인데 로컬에 안 채워져 있다 — git은 이걸 "
                f"\"경로가 없다\"고 말한다. 파일이 없는 것이 아니라 **우리가 못 보는 것**이다. "
                f"`code sync`를 돌려라",
                source=source, clock=self._clock)
        sha = await self._gitlink(repo, commit, sub)
        if not sha:
            return ProbeResult.failed(
                f"{sub}의 gitlink를 읽을 수 없다 — {commit}이 그 submodule을 가리키지 않는다",
                source=source, clock=self._clock)
        if await self._stale(repo, commit, [sub]):
            return ProbeResult.failed(self._stale_error([sub]), source=source,
                                      clock=self._clock)
        if not rest:
            # 경로가 submodule 자체다. 파일이 아니므로 내용 대신 **어느 버전인가**를 준다.
            return ProbeResult.succeeded(
                f"{sub}은 submodule이다 — {commit}이 박아 둔 커밋은 {sha}",
                source=source, clock=self._clock)
        pinned = f"{source} (submodule {sub}@{sha[:12]})"
        got = await self._git(repo, ["show", f"{sha}:{rest}"], source=pinned, inside=sub)
        return got if got.status == "error" else _clip(got, pinned, clock=self._clock)

    async def grep(self, repo: str, commit: str, patterns: list[str], *,
                   path: str = "") -> ProbeResult:
        source = f"code.grep {repo}@{commit} {patterns}" + (f" in {path}" if path else "")
        if not patterns:
            return ProbeResult.failed("패턴이 없다", source=source, clock=self._clock)
        # `-e`로 넘긴다(decisions ⑨) — `-`로 시작하는 패턴이 옵션으로 읽히면
        # git이 엉뚱한 동작을 하거나 죽는다.
        # `--recurse-submodules`는 **채워진** submodule만 들여다본다. 안 채워진
        # 것은 조용히 건너뛰므로(종료코드 1, 출력 없음) 그건 우리가 말해야 한다.
        args = ["grep", "-n", "-I", "--no-color", "--recurse-submodules"]
        for pattern in patterns:
            args += ["-e", pattern]
        args.append(commit)
        if path:
            args += ["--", path]
        subs = await self._declared_subs(repo, commit)
        # 객체가 없으면 git grep은 **종료코드 128로 통째로** 죽고
        # `unable to read tree`만 남긴다(측정). 부모 쪽 결과까지 같이 잃으므로,
        # 그 말을 그대로 흘리는 대신 무엇을 해야 하는지 우리가 말한다.
        behind = await self._stale(repo, commit, subs)
        if behind:
            return ProbeResult.failed(self._stale_error(behind), source=source,
                                      clock=self._clock)
        got = await self._git(repo, args, source=source)
        if got.status == "error":
            return got
        return _clip(got, source, clock=self._clock, unseen=self._blind(repo, subs))

    async def ls(self, repo: str, commit: str, path: str = "") -> ProbeResult:
        source = f"code.ls {repo}@{commit}" + (f":{path}" if path else "")
        args = ["ls-tree", "-r", "--name-only", commit]
        if path:
            args += ["--", path]
        got = await self._git(repo, args, source=source)
        if got.status == "error":
            return got
        names = [line for line in got.data.splitlines() if line]
        reasons = []
        if len(names) > _MAX_LINES:
            names = names[:_MAX_LINES]
            reasons.append(f"{_MAX_LINES}개에서 끊음")
        # `ls-tree -r`는 submodule 안으로 안 들어간다 — 경로가 이름 하나로만
        # 나온다. 그걸 "그 밑에 파일이 없다"로 읽으면 안 된다.
        reasons += _unseen_reasons(self._blind(repo, await self._declared_subs(repo, commit)))
        return ProbeResult.succeeded(
            names, source=source, clock=self._clock,
            truncated_reason=" · ".join(reasons) + " — 더 있을 수 있다" if reasons else None)


def _unseen_reasons(blind: list[str]) -> list[str]:
    """못 본 submodule을 봉투가 말할 문장으로. 없으면 빈 목록."""
    if not blind:
        return []
    return [f"submodule {', '.join(blind)}을 못 봤다(안 채워져 있다) — "
            f"그 안은 이 결과에 없다"]


def _clip(got: ProbeResult, source: str, *, clock: Clock,
          unseen: list[str] | tuple = ()) -> ProbeResult:
    """상한에 걸리면 **잘렸다고 봉투가 말한다.**

    조용히 자르면 리드가 "그 파일에 그 문자열이 없다"를 단정한다 — 잘린 뒤쪽에
    있었을 뿐인데. 5단계의 `unreachable`, 증거의 `complete=False`와 같은 규율이다.
    """
    text, reasons = got.data, _unseen_reasons(list(unseen))
    lines = text.splitlines()
    if len(lines) > _MAX_LINES:
        lines = lines[:_MAX_LINES]
        reasons.append(f"{_MAX_LINES}줄에서 끊음")
    text = "\n".join(lines)
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS]
        reasons.append(f"{_MAX_CHARS}자에서 끊음")
    return ProbeResult.succeeded(
        text, source=source, clock=clock,
        truncated_reason=" · ".join(reasons) + " — 더 있을 수 있다" if reasons else None)
