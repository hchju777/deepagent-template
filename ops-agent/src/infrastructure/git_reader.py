"""로컬 체크아웃에서 **커밋을 지정해** 읽는다. 네트워크를 안 탄다.

## 왜 네트워크를 안 타는가

`git show <커밋>:경로`는 로컬 객체만 있으면 된다. partial clone을 안 쓰기로 한
이유가 이것이다(decisions ②) — 그걸 쓰면 `show`가 필요할 때 네트워크를 타고,
**조사가 git 서버에 의존하게 된다.** 서버가 느린 날 조사가 느려지고, 막힌 날
조사가 멈춘다.

## 토큰은 여기 안 온다

읽기는 전부 로컬이라 인증이 필요 없다. 토큰이 필요한 것은 `code sync`(CLI 경계)
하나뿐이고, 그래서 **이 파일은 비밀값을 아예 모른다.**

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

# 한 번에 실어 오는 상한. 코드 파일 하나가 이보다 크면 잘라서 주고 봉투가 말한다.
_MAX_CHARS = 20000
_MAX_LINES = 400
_TIMEOUT_S = 20


class RealCodeReader(CodeReaderPort):
    def __init__(self, repos: list[RepoConfig], *, clock: Clock):
        self._repos = {r.name: Path(r.path) for r in repos}
        self._clock = clock

    def describe(self) -> str:
        return f"git({', '.join(sorted(self._repos)) or '레포 없음'})"

    async def _git(self, repo: str, args: list[str], *, source: str) -> ProbeResult:
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
                "git", "-C", str(root), *args,
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

    async def show(self, repo: str, commit: str, path: str) -> ProbeResult:
        source = f"code.show {repo}@{commit}:{path}"
        got = await self._git(repo, ["show", f"{commit}:{path}"], source=source)
        return got if got.status == "error" else _clip(got, source, clock=self._clock)

    async def grep(self, repo: str, commit: str, patterns: list[str], *,
                   path: str = "") -> ProbeResult:
        source = f"code.grep {repo}@{commit} {patterns}" + (f" in {path}" if path else "")
        if not patterns:
            return ProbeResult.failed("패턴이 없다", source=source, clock=self._clock)
        # `-e`로 넘긴다(decisions ⑨) — `-`로 시작하는 패턴이 옵션으로 읽히면
        # git이 엉뚱한 동작을 하거나 죽는다.
        args = ["grep", "-n", "-I", "--no-color"]
        for pattern in patterns:
            args += ["-e", pattern]
        args.append(commit)
        if path:
            args += ["--", path]
        got = await self._git(repo, args, source=source)
        return got if got.status == "error" else _clip(got, source, clock=self._clock)

    async def ls(self, repo: str, commit: str, path: str = "") -> ProbeResult:
        source = f"code.ls {repo}@{commit}" + (f":{path}" if path else "")
        args = ["ls-tree", "-r", "--name-only", commit]
        if path:
            args += ["--", path]
        got = await self._git(repo, args, source=source)
        if got.status == "error":
            return got
        names = [line for line in got.data.splitlines() if line]
        if len(names) <= _MAX_LINES:
            return ProbeResult.succeeded(names, source=source, clock=self._clock)
        return ProbeResult.succeeded(
            names[:_MAX_LINES], source=source, clock=self._clock,
            truncated_reason=f"{_MAX_LINES}개에서 끊음 — 더 있을 수 있다")


def _clip(got: ProbeResult, source: str, *, clock: Clock) -> ProbeResult:
    """상한에 걸리면 **잘렸다고 봉투가 말한다.**

    조용히 자르면 리드가 "그 파일에 그 문자열이 없다"를 단정한다 — 잘린 뒤쪽에
    있었을 뿐인데. 5단계의 `unreachable`, 증거의 `complete=False`와 같은 규율이다.
    """
    text, reasons = got.data, []
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
