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
import re
from pathlib import Path

from src.config.schema_site import RepoConfig
from src.domain.base import Clock
from src.domain.envelope import ProbeResult
from src.domain.ports import CodeReaderPort
# 순수 파서 하나만 빌려 온다. 같은 `.gitmodules`를 두 계층이 따로 해석하면
# 언젠가 한쪽만 고쳐지고, 그때 `code status`는 "정상"이라 말하는데 읽기는
# 비어서 돌아온다 — 규율 8이 막는 그 실패다.
from src.knowledge.checkout import (_REGISTERED, containing_submodule,
                                    parse_gitmodules)

# 한 번에 실어 오는 상한. 코드 파일 하나가 이보다 크면 잘라서 주고 봉투가 말한다.
_MAX_CHARS = 20000
_MAX_LINES = 400
# `whole=True`용 상한. **무한 읽기는 안 만든다** — 실수로 거대한 파일을 물면
# 조사 한 라운드가 그것만으로 끝난다. 다만 config 한 층은 여기 한참 못 미친다.
_WHOLE_MAX_CHARS = 1_000_000
_TIMEOUT_S = 20
# 스냅샷의 `ls-tree`·`cat-file --batch`는 레포 전체다 — 사내 규모(파일 800·수 MB)가 여기서 1초대였지만 Windows의
# 느린 디스크·백신을 생각해 넉넉히 둔다. 넘으면 값으로 실패하고 호출자가 파일별 `show`로 간다.
_SNAPSHOT_TIMEOUT_S = 120


class RealCodeReader(CodeReaderPort):
    def __init__(self, repos: list[RepoConfig], *, clock: Clock):
        self._repos = {r.name: Path(r.path) for r in repos}
        self._clock = clock
        # 커밋으로 주소가 매겨진 값이라 **절대 안 변한다** — 캐시해도 상할 수 없다.
        # (채워졌는지 여부는 사람이 중간에 바꿀 수 있으므로 캐시하지 않는다.)
        self._declared: dict[tuple[str, str], dict[str, str]] = {}
        # 인덱서(11d)는 submodule 안 파일을 **하나씩** 읽는다 — 사내는 레포 다섯 × 공유 라이브러리 수백 파일이라
        # 파일마다 gitlink를 풀고 객체를 확인하면 Windows에서 subprocess가 수천 개다. gitlink는 커밋이 SHA일
        # 때만 담는다(참조는 움직인다). "그 버전이 있다"는 한 번 참이면 참이고, 없다는 sync가 바꾸므로 안 담는다.
        self._gitlinks: dict[tuple[str, str, str], str] = {}
        self._have: set[tuple[str, str, str]] = set()

    def describe(self) -> str:
        return f"git({', '.join(sorted(self._repos)) or '레포 없음'})"

    async def _git(self, repo: str, args: list[str], *, source: str,
                   inside: str = "") -> ProbeResult:
        """`inside`는 레포 안의 상대 경로 — submodule에서 돌릴 때만 쓴다.

        `.git` 검사는 **레포 뿌리**에 대고 한다. submodule 쪽 `.git`은 파일일
        수도 디렉터리일 수도 있고, 그 존재 여부는 호출자가 이미 판단한다.
        """
        got = await self._git_bytes(repo, args, source=source, inside=inside)
        if got.status == "error":
            return got
        return ProbeResult.succeeded(got.data.decode("utf-8", "replace"), source=source,
                                     clock=self._clock)

    async def _git_bytes(self, repo: str, args: list[str], *, source: str, inside: str = "",
                         timeout: float = _TIMEOUT_S, input: bytes | None = None) -> ProbeResult:
        """`_git`의 바이트판 — `cat-file --batch`처럼 텍스트가 아닌 출력을 받을 때. 성공의 `data`는 bytes.
        `input`은 표준 입력으로 넣는다(`--batch`가 객체 이름을 거기서 읽는다)."""
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
                stdin=asyncio.subprocess.PIPE if input is not None else None,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(input), timeout=timeout)
        except asyncio.TimeoutError:
            return ProbeResult.failed(f"{timeout:.0f}초 안에 안 끝났다", source=source,
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
        return ProbeResult.succeeded(out or b"", source=source, clock=self._clock)

    # ── submodule 경계 ────────────────────────────────────────────

    async def _declared_entries(self, repo: str, commit: str) -> dict[str, str]:
        """그 커밋이 선언한 submodule — **이름 → 경로**. 등록은 이름으로 걸린다."""
        key = (repo, commit)
        if key not in self._declared:
            got = await self._git(repo, ["show", f"{commit}:.gitmodules"],
                                  source=f"code.submodules {repo}@{commit}")
            # `.gitmodules`가 없으면 실패로 온다 = submodule이 없다.
            self._declared[key] = ({} if got.status == "error"
                                   else parse_gitmodules(got.data))
        return self._declared[key]

    async def _declared_subs(self, repo: str, commit: str) -> list[str]:
        return sorted(set((await self._declared_entries(repo, commit)).values()))

    async def _blind(self, repo: str, commit: str) -> list[str]:
        """git이 **안 들여다보는** submodule 경로들 — 우리가 못 보는 구석.

        `.git`이 있나로만 보면 안 된다. 디렉터리가 채워져 있어도 로컬 등록이
        없으면 `grep --recurse-submodules`가 조용히 건너뛴다(측정함). 그렇다고
        `git submodule status`를 쓰지도 않는다 — 포슬린이라 사내 Windows에서
        실패했다. 평범한 config 읽기 둘이면 충분하다.
        """
        entries = await self._declared_entries(repo, commit)
        if not entries:
            return []
        root = self._repos.get(repo)
        got = await self._git(repo, ["config", "--get-regexp",
                                     r"^submodule\..*\.url"],
                              source=f"code.submodule-config {repo}")
        # `--get-regexp`는 매치가 없으면 1로 끝난다 — 우리 `_git`이 1을 성공으로
        # 보므로 "등록 0건"이 그대로 빈 집합이 된다.
        registered = set()
        if got.status == "error":
            return sorted(set(entries.values()))   # 모르는 것을 괜찮다고 적지 않는다
        for line in got.data.splitlines():
            match = _REGISTERED.match(line.strip())
            if match:
                registered.add(match.group(1))
        return sorted({path for name, path in entries.items()
                       if name not in registered
                       or root is None
                       or not (root / path / ".git").exists()})

    async def _stale(self, repo: str, commit: str, subs: list[str], *,
                     blind: list[str] | None = None) -> list[str]:
        """채워져는 있는데 **그 커밋이 박은 버전**의 객체가 없는 것들.

        부모만 fetch되고 submodule은 안 당겨진 트리에서 생긴다. `.git`이 있으므로
        `_blind`는 "채워졌다"고 말한다 — 그래서 상태가 셋이다.
        """
        blind_set = set(blind if blind is not None else await self._blind(repo, commit))
        behind = []
        for sub in subs:
            if sub in blind_set:
                continue                       # 그건 `_blind`가 센다
            sha = await self._gitlink(repo, commit, sub)
            if not sha or (repo, sub, sha) in self._have:
                continue
            got = await self._git(repo, ["cat-file", "-e", f"{sha}^{{commit}}"],
                                  source=f"code.have {repo}:{sub}@{sha[:12]}", inside=sub)
            if got.status == "error":
                behind.append(sub)
            else:
                self._have.add((repo, sub, sha))
        return behind

    def _stale_error(self, subs: list[str]) -> str:
        return (f"submodule {', '.join(subs)}의 **그 커밋이 쓴 버전**이 로컬에 없다 — "
                f"git은 이걸 \"exists on disk, but not in …\"이라고 말하지만 파일이 없는 "
                f"것이 아니라 우리가 그 버전을 안 가진 것이다. `code sync`가 고친다")

    async def _gitlink(self, repo: str, commit: str, sub: str) -> str:
        """부모 커밋이 그 submodule에 박아 둔 SHA. 못 읽으면 빈 문자열."""
        key = (repo, commit, sub)
        if key in self._gitlinks:
            return self._gitlinks[key]
        got = await self._git(repo, ["ls-tree", commit, "--", sub],
                              source=f"code.gitlink {repo}@{commit}:{sub}")
        if got.status == "error":
            return ""
        for line in got.data.splitlines():
            # `160000 commit <sha>\t<경로>`
            parts = line.split(" ", 2)
            if len(parts) == 3 and parts[1] == "commit":
                sha = parts[2].split("\t")[0].strip()
                if _FULL_SHA.fullmatch(commit):
                    self._gitlinks[key] = sha
                return sha
        return ""

    async def show(self, repo: str, commit: str, path: str, *,
                   whole: bool = False) -> ProbeResult:
        """`whole=True`면 **줄 수로 자르지 않는다.**

        기본 상한(400줄)은 리드의 컨텍스트를 지키려는 것이고, 소스 파일은 앞부분만
        봐도 쓸모가 있다. **config는 다르다** — 잘린 JSON은 쓸모가 0이 아니라
        마이너스다. 파싱이 실패하고, 그 실패가 "대상 파일이 깨졌다"로 읽힌다.
        실제로 사내에서 그렇게 났다: 400줄 넘는 층이 잘렸는데 메시지는
        "JSON이 아니다"였고, 사람을 **멀쩡한 파일** 고치러 보낼 뻔했다.
        """
        source = f"code.show {repo}@{commit}:{path}"
        sub = containing_submodule(await self._declared_subs(repo, commit), path)
        if sub:
            return await self._show_across(repo, commit, sub, path, source=source,
                                           whole=whole)
        got = await self._git(repo, ["show", f"{commit}:{path}"], source=source)
        return (got if got.status == "error"
                else _clip(got, source, clock=self._clock, whole=whole))

    async def _show_across(self, repo: str, commit: str, sub: str, path: str, *,
                           source: str, whole: bool = False) -> ProbeResult:
        """submodule 안의 파일을 **부모가 박아 둔 SHA로** 읽는다.

        `git show <부모커밋>:서브/경로`는 submodule 안으로 안 들어간다 — 채워져
        있어도 "exists on disk, but not in '<커밋>'"이라고 한다. 그래서 gitlink를
        직접 풀어서 그 레포 안에서 다시 읽는다. **최신이 아니라 배포가 쓴 버전**이다.
        """
        rest = path[len(sub):].lstrip("/")
        blind = await self._blind(repo, commit)
        if sub in blind:
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
        if await self._stale(repo, commit, [sub], blind=blind):
            return ProbeResult.failed(self._stale_error([sub]), source=source,
                                      clock=self._clock)
        if not rest:
            # 경로가 submodule 자체다. 파일이 아니므로 내용 대신 **어느 버전인가**를 준다.
            return ProbeResult.succeeded(
                f"{sub}은 submodule이다 — {commit}이 박아 둔 커밋은 {sha}",
                source=source, clock=self._clock)
        pinned = f"{source} (submodule {sub}@{sha[:12]})"
        got = await self._git(repo, ["show", f"{sha}:{rest}"], source=pinned, inside=sub)
        return (got if got.status == "error"
                else _clip(got, pinned, clock=self._clock, whole=whole))

    async def grep(self, repo: str, commit: str, patterns: list[str], *,
                   path: str = "", context: int = 0, fixed: bool = False,
                   max_lines: int | None = None, max_chars: int | None = None) -> ProbeResult:
        source = f"code.grep {repo}@{commit} {patterns}" + (f" in {path}" if path else "")
        if not patterns:
            return ProbeResult.failed("패턴이 없다", source=source, clock=self._clock)
        # `-e`로 넘긴다(decisions ⑨) — `-`로 시작하는 패턴이 옵션으로 읽히면
        # git이 엉뚱한 동작을 하거나 죽는다.
        # `--recurse-submodules`는 **채워진** submodule만 들여다본다. 안 채워진
        # 것은 조용히 건너뛰므로(종료코드 1, 출력 없음) 그건 우리가 말해야 한다.
        args = ["grep", "-n", "-I", "--no-color", "--recurse-submodules"]
        # 흐름 추출만 앞뒤 줄을 받는다(`-C1`) — 이름 꺼내기와 동사가 다른 줄에 오는
        # 문장(`coll = …["collection"]` / `mongo[coll].find(…)`) 때문이다. 리드의
        # `code.grep`은 0이다: 증거가 세 배로 불면 400줄 상한이 먼저 찬다.
        if context > 0:
            args.append(f"-C{context}")
        # 흐름 추출은 config에서 뽑은 **리터럴**을 찾는다 — `mx.alarm.main`의 점이 아무
        # 글자나 맞추면 안 된다. 리드의 `code.grep`은 정규식 그대로다(찾는 법은 리드가 고른다).
        if fixed:
            args.append("-F")
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
        return _clip(got, source, clock=self._clock, unseen=await self._blind(repo, commit),
                     max_lines=max_lines, max_chars=max_chars)

    async def _descend(self, repo: str, commit: str, names: list[str], path: str,
                       blind: list[str]) -> tuple[list[str], list[str]]:
        """채워진 submodule의 이름 하나를 **부모가 박은 SHA의** 파일들로 바꾼다(한 단계만). 인덱서(11d)가 공유
        라이브러리를 못 보던 이유가 이것이었다 — `show`·`grep`은 이미 들어가는데 목록만 안 들어갔다(사내 external
        2228건 중 738건). 안 채워진 것은 이름 그대로 두고 `_unseen_reasons`가 말한다. 버전이 없는 것도 그대로
        두고 여기서 말한다 — 전에는 "완전하다"고 했다."""
        subs = await self._declared_subs(repo, commit)
        if not subs:
            return names, []
        want = path.strip("/")
        expanded: dict[str, list[str]] = {}
        notes: list[str] = []
        stale: list[str] = []
        for sub in subs:
            if want and not (sub == want or sub.startswith(want + "/") or want.startswith(sub + "/")):
                continue
            if sub in blind:
                continue
            if await self._stale(repo, commit, [sub], blind=blind):
                stale.append(sub)
                continue
            sha = await self._gitlink(repo, commit, sub)
            if not sha:
                continue
            args = ["ls-tree", "-r", "--name-only", sha]
            if want.startswith(sub + "/"):
                args += ["--", want[len(sub) + 1:]]
            got = await self._git(repo, args, source=f"code.ls {repo}@{commit}:{sub}@{sha[:12]}", inside=sub)
            if got.status == "error":
                notes.append(f"submodule {sub}의 목록을 못 읽었다 — {got.error}")
                continue
            expanded[sub] = [f"{sub}/{n}" for n in got.data.splitlines() if n]
        if stale:
            notes.append(self._stale_error(stale))
        out: list[str] = []
        for n in names:
            out.extend(expanded.pop(n, [n]))
        for inner in expanded.values():         # 경로가 submodule 안이면 부모의 ls-tree는 아무것도 안 낸다
            out.extend(inner)
        return out, notes

    async def ls(self, repo: str, commit: str, path: str = "", *, max_names: int = _MAX_LINES) -> ProbeResult:
        # `max_names`: 리드용 기본은 400이지만 인덱서(11d)는 레포 전체(사내 794 파일)를 받아야 한다.
        source = f"code.ls {repo}@{commit}" + (f":{path}" if path else "")
        args = ["ls-tree", "-r", "--name-only", commit]
        if path:
            args += ["--", path]
        got = await self._git(repo, args, source=source)
        if got.status == "error":
            return got
        names = [line for line in got.data.splitlines() if line]
        blind = await self._blind(repo, commit)
        names, notes = await self._descend(repo, commit, names, path, blind)
        reasons = []
        if len(names) > max_names:
            names = names[:max_names]
            reasons.append(f"{max_names}개에서 끊음")
        # `ls-tree -r`는 submodule 안으로 안 들어간다 — 경로가 이름 하나로만
        # 나온다. 그걸 "그 밑에 파일이 없다"로 읽으면 안 된다.
        reasons += _unseen_reasons(blind) + notes
        return ProbeResult.succeeded(
            names, source=source, clock=self._clock,
            truncated_reason=" · ".join(reasons) + " — 더 있을 수 있다" if reasons else None)

    # ── 커밋 전체를 한 번에 (11e) ────────────────────────────────

    async def snapshot(self, repo: str, commit: str) -> ProbeResult:
        """레포당 git 두 번(`ls-tree -r` + `cat-file --batch`)으로 그 커밋의 파일 전부 — `경로 → 본문`.

        인덱서가 파일마다 `show`를 띄우던 것이 사내 `code graph` 20분의 정체였다(파일 ~1,600 ×
        Windows 프로세스 0.5~1초). 내용은 `show(whole=True)`와 **바이트까지** 같다 — 같은 blob, 같은 디코딩,
        같은 상한.

        submodule은 `ls`·`show`와 **같은 규칙**이다: 채워진 것은 부모가 박은 SHA로 그 레포에서 한 번
        더 받고, 안 채워진 것(blind)과 그 버전이 없는 것(stale)은 빼고 봉투가 말한다.
        """
        source = f"code.snapshot {repo}@{commit}"
        files, reasons = {}, []
        got = await self._blobs(repo, commit, source=source)
        if got.status == "error":
            return got
        files.update(got.data)
        subs = await self._declared_subs(repo, commit)
        if subs:
            blind = await self._blind(repo, commit)
            stale = await self._stale(repo, commit, subs, blind=blind)
            for sub in subs:
                if sub in blind or sub in stale:
                    continue
                sha = await self._gitlink(repo, commit, sub)
                if not sha:
                    continue
                inner = await self._blobs(repo, sha, source=f"{source} (submodule {sub}@{sha[:12]})",
                                          inside=sub)
                if inner.status == "error":
                    reasons.append(f"submodule {sub}을 못 받았다 — {inner.error}")
                    continue
                files.update({f"{sub}/{name}": text for name, text in inner.data.items()})
            reasons += _unseen_reasons(blind)
            if stale:
                reasons.append(self._stale_error(stale))
        clipped = sum(1 for text in files.values() if len(text) >= _WHOLE_MAX_CHARS)
        if clipped:
            reasons.append(f"{clipped}개 파일을 {_WHOLE_MAX_CHARS}자에서 끊음")
        return ProbeResult.succeeded(
            files, source=source, clock=self._clock,
            truncated_reason=" · ".join(reasons) + " — 더 있을 수 있다" if reasons else None)

    async def _blobs(self, repo: str, commit: str, *, source: str, inside: str = "") -> ProbeResult:
        """그 커밋의 blob 전부를 `경로 → 본문`으로 — `ls-tree -r -z`로 목록을, `cat-file --batch`로 본문을.

        처음엔 `git archive --format=tar` 한 번이었다. 그런데 archive는 **작업 트리용 변환**을 탄다 —
        `core.autocrlf`, `.gitattributes`의 `eol`·`filter`·`export-ignore`. 사내 Windows(autocrlf=true)에서 CRLF로
        와서 `show`(LF)와 달랐고, export-ignore 파일은 아예 빠졌다. LFS 같은 filter는 smudge로 밖에 나가려 들
        수도 있다. `cat-file`은 저장소의 blob 그대로다 — `show <커밋>:<경로>`가 돌려주는 바로 그것.

        목록의 gitlink(type `commit`)는 파일이 아니라 뺀다 — submodule은 호출자가 그 레포에서 따로 받는다.
        같은 내용은 blob 하나라 sha 하나에 경로가 여럿일 수 있다.
        """
        listed = await self._git_bytes(repo, ["ls-tree", "-r", "-z", commit], source=source, inside=inside,
                                       timeout=_SNAPSHOT_TIMEOUT_S)
        if listed.status == "error":
            return listed
        paths_by_sha: dict[str, list[str]] = {}
        for record in listed.data.split(b"\0"):
            meta, _, path = record.partition(b"\t")
            parts = meta.split()
            if len(parts) != 3 or parts[1] != b"blob":
                continue
            paths_by_sha.setdefault(parts[2].decode("ascii", "replace"), []).append(path.decode("utf-8", "replace"))
        if not paths_by_sha:
            return ProbeResult.succeeded({}, source=source, clock=self._clock)
        got = await self._git_bytes(repo, ["cat-file", "--batch"], source=source, inside=inside,
                                    timeout=_SNAPSHOT_TIMEOUT_S,
                                    input="\n".join(paths_by_sha).encode("ascii") + b"\n")
        if got.status == "error":
            return got
        out: dict[str, str] = {}
        data, pos = got.data, 0
        try:
            while pos < len(data):
                end = data.find(b"\n", pos)
                if end < 0:
                    break
                header = data[pos:end].split()
                pos = end + 1
                if len(header) != 3 or header[1] != b"blob":
                    continue                        # `<sha> missing` — 목록에 있던 blob이 없을 리 없지만, 그 파일만 빠진다
                size = int(header[2])
                text = data[pos:pos + size].decode("utf-8", "replace")[:_WHOLE_MAX_CHARS]
                pos += size + 1                     # 본문 뒤에 줄바꿈 하나
                for path in paths_by_sha.get(header[0].decode("ascii", "replace"), ()):
                    out[path] = text
        except ValueError as exc:
            return ProbeResult.failed(f"cat-file 출력을 못 읽었다 — {exc}", source=source, clock=self._clock)
        return ProbeResult.succeeded(out, source=source, clock=self._clock)


_FULL_SHA = re.compile(r"[0-9a-f]{40}")


def _unseen_reasons(blind: list[str]) -> list[str]:
    """못 본 submodule을 봉투가 말할 문장으로. 없으면 빈 목록."""
    if not blind:
        return []
    return [f"submodule {', '.join(blind)}을 못 봤다(안 채워져 있다) — "
            f"그 안은 이 결과에 없다"]


def _clip(got: ProbeResult, source: str, *, clock: Clock,
          unseen: list[str] | tuple = (), whole: bool = False,
          max_lines: int | None = None, max_chars: int | None = None) -> ProbeResult:
    """상한에 걸리면 **잘렸다고 봉투가 말한다.**

    조용히 자르면 리드가 "그 파일에 그 문자열이 없다"를 단정한다 — 잘린 뒤쪽에
    있었을 뿐인데. 5단계의 `unreachable`, 증거의 `complete=False`와 같은 규율이다.

    기본 상한은 리드에게 주는 증거 봉투 기준이다. 그래프 재료처럼 사람도 리드도 직접
    안 읽는 결과는 호출부가 `max_lines`·`max_chars`로 더 크게 준다 — 잘렸다는 말은 똑같이 한다.
    """
    text, reasons = got.data, _unseen_reasons(list(unseen))
    line_cap = max_lines or _MAX_LINES
    if not whole:
        lines = text.splitlines()
        if len(lines) > line_cap:
            lines = lines[:line_cap]
            reasons.append(f"{line_cap}줄에서 끊음")
        text = "\n".join(lines)
    cap = _WHOLE_MAX_CHARS if whole else (max_chars or _MAX_CHARS)
    if len(text) > cap:
        text = text[:cap]
        reasons.append(f"{cap}자에서 끊음")
    return ProbeResult.succeeded(
        text, source=source, clock=clock,
        truncated_reason=" · ".join(reasons) + " — 더 있을 수 있다" if reasons else None)
