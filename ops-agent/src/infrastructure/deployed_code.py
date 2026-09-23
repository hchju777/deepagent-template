"""**배포된 커밋의 코드를 서비스 이름으로** 읽는다. 조사(2차)가 쓰는 표면이다.

## 왜 포트를 그대로 안 쓰는가

`CodeReaderPort`는 `show(repo, commit, path)`다. 이걸 그대로 등재표에 올리면 **리드가
레포 이름과 커밋을 고르게 된다.** 리드는 SHA를 모르고, 모르면 지어낸다 — 그러면
우리는 떠 있지도 않은 코드를 읽고 확신에 찬 오답을 낸다. 규율 3(LLM이 인용한 id를
신뢰하지 않는다)이 막으려던 것과 같은 구멍이다.

그래서 **리드가 대는 것은 서비스 이름뿐**이고, 레포·커밋·법인·config 경로는 전부
여기서 정해진다(규율 6: 재현 가능·상한·감사 가능한 것은 코드가 쥔다).

| 리드가 정한다 | 코드가 정한다 |
|---|---|
| 어느 서비스를 볼지 · 무엇을 찾을지 · 어느 파일을 읽을지 | 레포 · 커밋 · 법인 · config 층 경로 · 병합 규칙 |

## "파일 하나 읽기"와 "이름이 무엇인가"는 다른 물음이다

이름은 대상의 config에 사는데 그 config는 **층으로 갈린다**(gumi면 셋). 층 하나만
읽으면 위 층이 덮어쓴 값을 사실로 단정한다. 그래서 `config()`는 층 전부를 읽어
`merge_target`으로 합친다 — `read()`와 아예 다른 메서드인 이유다.

`read()`의 `path`는 리드가 지어내는 것이 아니라 **`grep()`이 돌려준 경로**다.

## 실패는 값이다

전부 `ProbeResult`로 흡수한다. 없는 서비스, 안 읽히는 층, 잘린 파일 — 운영 중에
정상적으로 일어나는 일이고, 여기서 던지면 조사 그래프가 통째로 죽는다(규율 1).
"""
from dataclasses import replace
from typing import Callable

from src.domain.base import Clock
from src.domain.envelope import ProbeResult
from src.domain.ports import CodeReaderPort, DeployedCodePort
from src.knowledge.flow import Hit, Name, names_from_config
from src.knowledge.graph_build import parse_grep
from src.knowledge.schema import Deployment, Topology
from src.knowledge.target_config import merge_target, parse_layer

_MAX_CHARS = 20000

# 흐름 재료용 상한. 리더의 400줄·2만 자는 리드에게 주는 증거 봉투의 상한이지 그래프 재료의
# 상한이 아니다 — `alarm` 같은 키 토큰은 큰 레포에서 수백 줄이 정상이다.
FLOW_MAX_LINES = 50_000
FLOW_MAX_CHARS = 5_000_000
# 한 번의 `git grep`에 넘기는 패턴 수. 명령줄 길이(Windows 32K)와 출력 한 덩어리 크기의 균형.
FLOW_CHUNK = 20


def _evidence(layers: list[tuple[str, str, dict]], value: str) -> tuple[str, int, str] | None:
    """값이 적힌 **실제로 합친 층**의 줄 — `(경로, 줄, 본문)`. 마지막에 이긴 층부터 본다.

    레포의 config 파일을 grep해서 첫 파일을 붙이면 `config/factories/_dev/…`처럼 이 사이트에
    안 쓰이는 층이 근거로 찍힌다(사내 첫 실행). 근거는 그 서비스가 실제로 읽은 층이어야 한다.
    """
    quoted = (f'"{value}"', f"'{value}'")
    for path, text, _ in reversed(layers):
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(q in line for q in quoted):
                return path, lineno, line.strip()
    for path, text, _ in reversed(layers):        # YAML·TOML은 따옴표 없이 적기도 한다
        for lineno, line in enumerate(text.splitlines(), 1):
            if value in line:
                return path, lineno, line.strip()
    return None


class DeployedCode(DeployedCodePort):
    """`Adapters.code`에 꽂히는 어댑터. 사이트 하나(=`(gbm, fct)`) 기준이다."""

    def __init__(self, reader: CodeReaderPort, topology: Topology,
                 deployment: Deployment, *, gbm: str, fct: str, clock: Clock):
        self._reader = reader
        self._topology = topology
        self._deployment = deployment
        self._gbm, self._fct = gbm, fct
        self._clock = clock

    def service_names(self) -> tuple[str, ...]:
        """브리핑이 목록과 예시에 박을 이름들. **호출부가 토폴로지를 뒤지지 않게** 한다."""
        return tuple(sorted(self._topology.services))

    def service_roles(self) -> dict[str, str]:
        """이름 → 역할. 역할이 빈 서비스는 뺀다 — 빈 괄호는 정보가 아니라 소음이다."""
        return {name: svc.role for name, svc in sorted(self._topology.services.items())
                if svc.role}

    def describe(self) -> str:
        return f"code({self._gbm}/{self._fct}, 서비스 {len(self._topology.services)}개)"

    # ── 흐름 그래프(11c) 재료 ────────────────────────────────────

    def pinned(self) -> dict[str, str]:
        """레포 → 이 사이트의 배포 커밋(참조 그대로). 같은 레포는 커밋도 같다."""
        out: dict[str, str] = {}
        for name, svc in sorted(self._topology.services.items()):
            out.setdefault(svc.repo, self._deployment.pin_for(name, fct=self._fct).commit)
        return out

    async def flow_names(self) -> tuple[list[Name], list[str]]:
        """모든 서비스의 합친 config에서 뽑은 이름들(어느 서비스가 가졌는지 포함)과, 못 읽은
        서비스의 사유. 같은 레포의 서비스라도 환경변수로 고른 층이 다르면 합친 config가
        다르다 — 그래서 이름마다 서비스를 기억한다."""
        found: dict[tuple, Name] = {}
        holders: dict[tuple, set[str]] = {}
        evidence: dict[tuple, list] = {}
        problems = []
        for service in sorted(self._topology.services):
            got = await self._layers(service, f"code.config {service}")
            if isinstance(got, ProbeResult):
                problems.append(f"{service}: {got.error}")
                continue
            _, layers, _, _ = got
            merged = merge_target([(path, value) for path, _, value in layers])
            for n in names_from_config(merged, self._topology.flow.sources):
                key = (n.kind, n.value, n.key_path)
                found.setdefault(key, n)
                holders.setdefault(key, set()).add(service)
                where = _evidence(layers, n.value)
                if where:
                    evidence.setdefault(key, []).append((service, *where))
        return ([replace(n, services=tuple(sorted(holders[k])),
                         evidence=tuple(sorted(evidence.get(k, []))))
                 for k, n in found.items()], problems)

    async def flow_hits(self, patterns: list[str], *,
                        progress: Callable[[str], None] | None = None
                        ) -> tuple[dict[str, list[Hit]], list[str]]:
        """배포 커밋에서 `git grep -n -F -C1`을 **레포마다 몇 번**(패턴 `FLOW_CHUNK`개씩)으로.
        `(패턴 → 히트, 잘림 사유)`.

        패턴마다 한 번씩 띄우면 이름 100개·레포 3개에 600번이고, 사내 Windows에서는 분
        단위로 조용히 기다리게 된다. 히트는 줄 본문에 패턴이 들어 있는지로 패턴별로 나눈다
        — `-F`라 git이 맞춘 것도 정확히 그 부분 문자열이다. 잘린 결과는 버리지 않고 사유로
        돌려준다(그래프에 엣지가 빠졌을 수 있다). 실패한 레포는 조용히 빈다 — `code status`가
        레포 상태를 따로 말한다. 앞뒤 한 줄은 `Hit.context`로 간다.
        """
        table: dict[str, list[Hit]] = {p: [] for p in patterns}
        notes: list[str] = []
        chunks = [patterns[i:i + FLOW_CHUNK] for i in range(0, len(patterns), FLOW_CHUNK)]
        for repo, commit in self.pinned().items():
            if progress:
                progress(f"{repo}: 코드에서 이름 찾는 중 (패턴 {len(patterns)}개, git grep {len(chunks)}번)")
            for chunk in chunks:
                got = await self._reader.grep(repo, commit, chunk, context=1, fixed=True,
                                              max_lines=FLOW_MAX_LINES, max_chars=FLOW_MAX_CHARS)
                if got.status == "error" or not isinstance(got.data, str):
                    continue
                if not got.envelope.complete:
                    notes.append(f"{repo}: 코드 찾기가 잘렸다({got.envelope.truncated_reason}) — "
                                 f"그래프에 엣지가 빠졌을 수 있다")
                for hit in parse_grep(repo, commit, got.data):
                    for p in chunk:
                        if p in hit.text:
                            table[p].append(hit)
        return table, notes

    # ── 서비스 해석 ──────────────────────────────────────────────

    def _resolve(self, service: str, source: str):
        """서비스 이름 → `(레포, 커밋, 선언인가)`. 못 찾으면 `ProbeResult`."""
        known = self._topology.services.get(service)
        if known is None:
            return ProbeResult.failed(
                f"없는 서비스 — {service}. 아는 것: "
                f"{', '.join(sorted(self._topology.services)) or '없음'}",
                source=source, clock=self._clock)
        pin = self._deployment.pin_for(service, fct=self._fct)
        return known, pin.commit, pin.how

    def _pinned(self, source: str, commit: str, how: str) -> str:
        """증거에 **어느 커밋을 읽었는지**가 남아야 판정을 되짚을 수 있다."""
        return f"{source} @ {commit[:12]}" + ("" if how == "declared" else " (가정)")

    # ── action 넷 ────────────────────────────────────────────────

    async def services(self) -> ProbeResult:
        """무엇을 조사할 수 있나. **인자가 없는 것이 정상이다** — 발견용이다."""
        source = f"code.services {self._gbm}"
        rows = []
        for name, service in sorted(self._topology.services.items()):
            pin = self._deployment.pin_for(name, fct=self._fct)
            rows.append({"service": name, "repo": service.repo, "role": service.role,
                         "commit": pin.commit[:12],
                         **({"selects": service.selects} if service.selects else {})})
        return ProbeResult.succeeded(rows, source=source, clock=self._clock)

    async def config(self, service: str) -> ProbeResult:
        """그 서비스가 배포 시점에 **실제로 보는 설정.** 층을 전부 합친 결과다."""
        source = f"code.config {service}"
        got = await self._layers(service, source)
        if isinstance(got, ProbeResult):
            return got
        _, layers, broken, source = got
        read = " → ".join(path for path, _, _ in layers)
        return ProbeResult.succeeded(
            merge_target([(path, value) for path, _, value in layers]),
            source=f"{source} [{read}]", clock=self._clock,
            # 깨진 층이 있으면 **합친 결과가 틀렸다.** 그걸 완전하다고 적으면
            # 리드가 "이 설정은 이렇다"를 단정한다.
            truncated_reason=(" · ".join(broken) + " — 합친 값이 실제와 다를 수 있다"
                              if broken else None))

    async def _layers(self, service: str, source: str):
        """그 서비스의 config 층들 — `(커밋, [(경로, 원문, 값)], 깨진 층, source)`. 못 읽으면
        실패 `ProbeResult`. `config()`와 `flow_names()`가 같이 쓴다 — 근거 줄을 찾으려면
        합친 값만이 아니라 **어느 층의 몇 번째 줄**인지가 필요하다."""
        resolved = self._resolve(service, source)
        if isinstance(resolved, ProbeResult):
            return resolved
        known, commit, how = resolved
        source = self._pinned(source, commit, how)

        wanted = self._topology.resolved_config_paths(self._gbm, self._fct)
        if not wanted:
            return ProbeResult.failed(
                f"config_paths가 선언돼 있지 않다 — knowledge/topology/{self._gbm}.json에 "
                f"이름이 사는 파일들을 적어야 한다",
                source=source, clock=self._clock)

        layers, broken = [], []
        for path in wanted:
            # **통째로 읽는다.** 잘린 JSON은 못 쓴다 — 400줄 상한에 걸린 층을
            # 그냥 파싱하면 "JSON이 아니다"가 나오고, 그 말은 **대상 파일이
            # 깨졌다**는 뜻으로 읽힌다. 사내에서 실제로 그랬다.
            got = await self._reader.show(known.repo, commit, path, whole=True)
            if got.status == "error":
                continue                    # 없는 층은 **정상이다**(층은 선택)
            if not got.envelope.complete:
                # **파싱하기 전에** 본다. 우리가 자른 것을 대상 탓으로 돌리지 않는다.
                broken.append(f"{path}: 우리가 잘라서 읽었다 — "
                              f"{got.envelope.truncated_reason}. 이 층은 안 썼다")
                continue
            value, why = parse_layer(path, got.data)
            if value is None:
                broken.append(why)
                continue
            layers.append((path, got.data, value))

        if not layers:
            # **왜 없는지까지 말한다.** 읽다가 실패한 층이 있으면 그게 원인이고,
            # 그걸 빼면 "경로가 틀렸다"로 읽혀서 사람이 맞는 경로를 고치러 간다.
            why = (" · ".join(broken) if broken
                   else f"찾은 자리: {', '.join(wanted)}. `code status`를 보라")
            return ProbeResult.failed(
                f"쓸 수 있는 config 층이 하나도 없다 — {why}",
                source=source, clock=self._clock)
        return commit, layers, broken, source

    async def grep(self, patterns: list[str], service: str = "") -> ProbeResult:
        """**이 이름을 누가 쓰나.** 11a가 존재하는 이유다.

        `service`를 안 주면 모든 레포를 본다 — 이름이 어느 서비스 것인지 모르는
        상태가 정상이기 때문이다(decisions ⑮).
        """
        source = f"code.grep {patterns}" + (f" in {service}" if service else "")
        if not patterns:
            return ProbeResult.failed("찾을 문자열이 없다", source=source, clock=self._clock)

        if service:
            resolved = self._resolve(service, source)
            if isinstance(resolved, ProbeResult):
                return resolved
            known, commit, how = resolved
            targets = [(service, known.repo, commit, how, known.path)]
        else:
            # 레포가 같으면 커밋도 같다(코드는 GBM 단위로 같다) — 한 번만 본다.
            seen, targets = set(), []
            for name, known in sorted(self._topology.services.items()):
                pin = self._deployment.pin_for(name, fct=self._fct)
                key = (known.repo, pin.commit)
                if key in seen:
                    continue
                seen.add(key)
                targets.append((name, known.repo, pin.commit, pin.how, ""))

        chunks, problems, complete = [], [], True
        for _, repo, commit, how, path in targets:
            got = await self._reader.grep(repo, commit, patterns, path=path)
            if got.status == "error":
                problems.append(f"{repo}: {got.error}")
                continue
            if not got.envelope.complete:
                complete = False
                problems.append(f"{repo}: {got.envelope.truncated_reason}")
            if got.data.strip():
                chunks.append(f"# {repo} @ {commit[:12]}\n{got.data.rstrip()}")

        text = "\n".join(chunks)
        if len(text) > _MAX_CHARS:
            text, complete = text[:_MAX_CHARS], False
            problems.append(f"{_MAX_CHARS}자에서 끊음")
        if not chunks and problems and len(problems) == len(targets):
            # 전부 실패했으면 "못 찾았다"가 아니라 **못 봤다**이다(⑪).
            return ProbeResult.failed(" · ".join(problems), source=source,
                                      clock=self._clock)
        return ProbeResult.succeeded(
            text, source=source, clock=self._clock,
            truncated_reason=(" · ".join(problems) + " — 더 있을 수 있다"
                              if (problems or not complete) else None))

    async def read(self, service: str, path: str) -> ProbeResult:
        """파일 하나. **`path`는 `grep`이 돌려준 경로다** — 지어내는 자리가 아니다."""
        source = f"code.read {service}:{path}"
        resolved = self._resolve(service, source)
        if isinstance(resolved, ProbeResult):
            return resolved
        known, commit, how = resolved
        got = await self._reader.show(known.repo, commit, path)
        pinned = self._pinned(source, commit, how)
        if got.status == "error":
            return ProbeResult.failed(got.error, source=pinned, clock=self._clock)
        return ProbeResult.succeeded(
            got.data, source=f"{pinned} ({got.source})", clock=self._clock,
            truncated_reason=got.envelope.truncated_reason)
