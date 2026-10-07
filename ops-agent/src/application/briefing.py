"""리드에게 줄 재료를 조립한다 — 케이스·증거·가설, 그리고 **부를 수 있는 것의 목록**.

## 목록을 코드가 만드는 이유

사람이 프롬프트 파일에 손으로 적으면 config와 갈라진다. 그리고 **갈라진 쪽이 곧
LLM이 믿는 세계**가 된다 — REST 항목을 config에 추가했는데 프롬프트를 안 고치면
리드는 그게 있는 줄도 모르고, 반대면 없는 것을 계속 부른다.

그래서 `domain/actions.py`의 등재표와 그 사이트의 `infra.rest.entries`에서 **생성**한다.
프롬프트 템플릿에는 `{actions}` 자리만 있다.

## 대상 데이터의 이름을 여기 적지 않는다

컬렉션·토픽·키 이름이 이 파일이나 프롬프트에 박히면, 조사는 **우리가 적어 준 곳만**
본다. 그건 "우리가 아는 만큼만 조사하는" 에이전트다([decisions ⑮](../../STEPS/decisions.md)).
이름은 `mongo.list_collections`·`kafka.list_topics`·`redis.scan`으로 **리드가 찾는다.**

## 접속 정보가 새지 않는다

url·비밀번호·계정은 프롬프트에 들어가지 않는다. 리드가 알아야 하는 것은 "무엇을
부를 수 있는가"이지 "어디에 붙어 있는가"가 아니다 — 후자는 어댑터의 일이다.
`tests/application/test_briefing.py`가 지킨다.
"""
import json
import re

from src.application.state import CaseState
from src.domain.actions import ACTIONS, describe
from src.knowledge import flow as flowgraph

# 리드가 쓸 수 없는 action. rest.query는 등재 항목마다 따로 적는다(인자가 다르다).
_RENDERED_SEPARATELY = {"rest.query"}


def action_catalog(site_config, *, services: tuple[str, ...] = (),
                   roles: dict[str, str] | None = None,
                   hide: frozenset[str] = frozenset()) -> str:
    """부를 수 있는 읽기 목록. **config에서 생성한다.**

    `services`는 대상 코드(11a)가 준비됐을 때만 채워진다 — 코드의 가용 여부는
    `site.infra`가 아니라 **knowledge(토폴로지·배포)**에서 오기 때문이다. 비어
    있으면 `code.*`는 목록에 아예 안 나온다. 없는 문을 열라고 적어 두면 리드가
    거기로 가고, 매 라운드가 "미등재 action"으로 날아간다.
    """
    lines: list[str] = []
    for name, (adapter, _, required, optional) in sorted(ACTIONS.items()):
        if name in _RENDERED_SEPARATELY or name in hide:
            continue
        if not _has(site_config, adapter, services):
            continue          # 이 사이트에 없는 시스템은 목록에 없다
        lines.append(f"- {name}({_args(required, optional)})")
    if services:
        # **이름을 목록에 박아 둔다.** 사내 모델은 완결된 구체값을 그대로 복사하고
        # 지시문 모양은 바꿔 넣는다(10b에서 측정). 서비스 이름을 여기 안 적으면
        # `service="..."`를 진짜로 조회한다.
        #
        # 그리고 **`code.*` 바로 밑에** 붙인다. 목록 맨 끝에 두면 알파벳 순서상
        # 한참 떨어지고, 순서대로 읽는 모델에게는 그만큼 안 보인다.
        after = max((n for n, line in enumerate(lines)
                     if line.startswith("- code.")), default=len(lines) - 1)
        # **역할을 이름 옆에 붙인다.** 토폴로지의 `role`은 "리드가 누구를 봐야 하나를
        # 고르는 유일한 단서"라고 적어 놓고 정작 `code.services`를 불러야만 보이게
        # 뒀었다 — 예시가 `code.config`로 바로 가라고 하니 한 번도 안 불렀다. 두 번째
        # 전체 트레이스에서 가설이 4라운드 내내 하나로 고정된 채 결론을 못 낸 것이
        # 그 결과다. 서비스가 무엇을 하는지 모르면 무엇을 확인해야 끝나는지도 모른다.
        roles = roles or {}
        named = [f"{name} — {roles[name]}" if roles.get(name) else name for name in services]
        lines.insert(after + 1, f"  (service 자리에 쓸 이름: {' / '.join(named)})")

    rest = site_config.infra.rest
    for entry_name, entry in sorted((rest.entries if rest else {}).items()):
        params = ", ".join(
            f"{k}{'' if spec.required else '?'}: {spec.type}"
            for k, spec in sorted(entry.params.items()))
        lines.append(f'- rest.query(entry="{entry_name}", params={{{params}}})')
    return "\n".join(lines) or "- (이 사이트에 부를 수 있는 것이 없다)"


# 어댑터 이름 → SiteConfig.infra의 필드 이름. 둘이 다른 것은 `mongodb` 하나뿐이다.
# `recompute`는 우리 실행기지만 mongo로 센다 — mongo가 없는 사이트에서는 목록에 없다.
_INFRA_FIELD = {"redis": "redis", "mongo": "mongodb", "kafka": "kafka", "rest": "rest", "recompute": "mongodb"}


def _args(required: tuple, optional: tuple) -> str:
    parts = list(required) + [f"{name}?" for name in optional]
    return ", ".join(parts)


def _check_of(case, site_config):
    checks = getattr(getattr(site_config, "patrol", None), "checks", None) or {}
    if not case.check or case.check not in checks:
        return None
    return checks[case.check]


def start_read(case, site_config) -> tuple[str, dict] | None:
    """접수 경로의 첫 칸 — 케이스를 연 판정이 **본** 프로브의 읽기 `(action, params)`. 없으면 None.

    `params.items.probe`가 판정이 본 프로브다(`only_when`의 프로브가 아니라). 리드는 같은 읽기로
    증상을 재현하는 데서 시작한다 — 사내 첫 조사 trace에서 리드가 끝점을 만드는 코드에 한 번도
    가지 않은 것은 이 출발점을 우리가 안 줬기 때문이다. 사람이 적는 표가 아니라 config다(⑮).
    """
    check = _check_of(case, site_config)
    if check is None:
        return None
    probe = check.probes.get(check.params.items.probe)
    if probe is None:
        return None
    return probe.action, dict(probe.params)


def _rest_path(site_config, read: tuple[str, dict]) -> str | None:
    action, params = read
    rest = site_config.infra.rest
    entry = params.get("entry") if action == "rest.query" else None
    if entry and rest is not None and entry in rest.entries:
        return rest.entries[entry].path
    return None


def origin_line(case, site_config) -> str | None:
    """`접수 경로:` 뒤에 붙는 한 줄 — 점검, 대상, 판정이 본 읽기와 그 REST 경로. 전부 config에서."""
    if _check_of(case, site_config) is None:
        return None
    parts = [f"순찰 점검 {case.check}"]
    if case.target:
        parts.append(f"대상 {case.target}")
    read = start_read(case, site_config)
    if read is not None:
        action, params = read
        text = describe(action, params)
        entry = params.get("entry") if action == "rest.query" else None
        rest = site_config.infra.rest
        if entry and rest is not None and entry in rest.entries:
            text += f" ({rest.entries[entry].method} {rest.entries[entry].path})"
        parts.append(f"판정이 본 읽기 {text}")
    return " · ".join(parts)


def case_block(state: CaseState, *, site_config=None) -> str:
    case = state.case
    origin = "사람" if case.origin == "human" else (origin_line(case, site_config) or "순찰")
    return (f"사이트: {case.site}\n"
            f"증상: {case.symptom}\n"
            f"발생 시각: {case.t0.isoformat()}\n"
            f"접수 경로: {origin}")


def hypotheses_block(state: CaseState) -> str:
    if not state.hypotheses:
        return "(아직 없다)"
    return "\n".join(
        f"- {h.id} [{h.status}] {_oneline(h.statement)}" for h in state.hypotheses)


def evidence_block(state: CaseState, *, budget: int = 12000) -> str:
    """**리드가 실제로 본 것**만. 이게 나중에 판정이 인용할 수 있는 우주다(12a).

    ## 왜 `summary`가 아니라 `body`인가

    `summary`는 사람이 볼 한 줄(160자)이다. 그걸 리드의 판단 재료로 그대로 쓰니
    제조 문서(258자) 한 건이 반쯤 잘려서, 리드가 **필드 이름은 보고 값은 못 보는**
    상태가 됐다. 그래서 올바른 후속 질문을 하고도 같은 질의를 반복했다.

    ## 예산을 넘으면 오래된 것부터 한 줄만 남긴다

    `- id | 출처 | 요약` 줄은 **모든 증거에 대해 끝까지 남는다** — 그래야 인용이
    계속 유효하다. 줄어드는 것은 내용(`body`)뿐이고, 줄어든 사실을 적는다.

    ## 개행은 우리가 만든 것만 있다

    대상 데이터는 여러 줄이 정상인데 날것으로 실리면 이 블록에 **가짜 항목**이 생기고,
    리드는 있지도 않은 증거 id를 인용한다. 생산자(`runner_probe.detail`)가 이미
    눕히지만 여기서 한 번 더 막는다 — `EvidenceRef`는 어디서나 만들 수 있고
    (11b의 서브에이전트가 곧 만든다), **"생산자가 다 지킨다"는 가정은 생산자가
    늘어나면 깨진다.**
    """
    if not state.evidence:
        return "(아직 없다)"

    # 뒤에서부터 예산을 채운다 — 최근 증거가 지금 판단에 쓰인다.
    detailed, used = set(), 0
    for ref in reversed(state.evidence):
        used += len(ref.body or ref.summary)
        if used > budget and detailed:
            break
        detailed.add(ref.id)

    lines = []
    for ref in state.evidence:
        # **"또 읽어라"로 읽히면 안 된다.** 같은 질의는 같은 답을 준다 —
        # 사내 측정에서 리드가 잘린 증거 셋을 정확히 그대로 다시 냈고, 중복
        # 방어가 셋 다 거부해서 조사가 `no_runnable`로 끝났다.
        cut = ("" if ref.complete else
               "  ⚠ 표본이 잘렸다 — '없다'를 주장할 수 없다. "
               "같은 질의는 같은 답이다 — 좁혀서 물어라")
        lines.append(f"- {_oneline(ref.id)} | {_oneline(ref.source)} | "
                     f"{_oneline(ref.summary)}{cut}")
        if ref.id not in detailed:
            # 예전엔 "필요하면 다시 읽어라"였다. **우리가 시켜 놓고 거부했다** —
            # 리드가 받은 지시 중 제일 구체적인 것이 이 줄이었고, 산문 규칙보다
            # 이런 줄이 훨씬 잘 먹는다(10b에서 측정한 성질이 반대로 작동했다).
            lines.append("    (내용은 예산에서 빠졌다 — 같은 질의를 또 내지 마라. "
                         "필요하면 더 좁혀서 물어라)")
            continue
        for row in (ref.body or "").splitlines():
            lines.append(f"    {_oneline(row)}")
    return "\n".join(lines)


def _oneline(text: str) -> str:
    return text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def tasks_block(state: CaseState) -> str:
    if not state.plan_tasks:
        return "(아직 없다)"
    lines = []
    for task in state.plan_tasks:
        # goal·error도 한 줄로 눕힌다. goal은 리드가 쓴 문장이라 개행이 들어올 수
        # 있고, error는 대상 시스템의 예외 메시지라 여러 줄인 것이 흔하다.
        detail = task.error or task.result_summary or ""
        line = (f"- {task.id} [{task.status}] {_oneline(task.goal)}"
                + (f" — {_oneline(detail)}" if detail else ""))
        # **증거를 못 만든 태스크는 질의를 여기 적는다.** 증거가 있으면 그 줄의
        # `source`가 곧 질의라 리드가 볼 수 있지만, 실패했거나 빈 결과였던 태스크는
        # 어디에도 질의가 안 보인다 — 그래서 리드가 같은 것을 또 내고 거부당한다
        # (두 번째 전체 트레이스의 t-11 `kafka.tail`, "증거엔 안 보였다").
        if task.status != "pending" and not task.result_evidence_ids and task.action:
            line += f" · 질의: {describe(task.action, task.params)}"
        lines.append(line)
    return "\n".join(lines)


# ── 예시 (`{example}`) ────────────────────────────────────────────────
#
# **예시가 곧 출력이다.** 사내 모델(Haiku·낮은 effort 급)로 재 보니 리드는 판단해서
# 고르는 게 아니라 **예시의 틀을 채운다** — `id`·`action`·`params`를 그대로 베끼고
# `goal` 문장만 자기 말로 바꿨다. 예시에 태스크가 하나였으므로 하나를 냈고,
# `input_evidence_ids`가 없었으므로 안 썼다. 산문 규칙은 아무 효과도 못 냈다.
#
# 그래서 예시는 "모양을 보여 주는 것"이 아니라 **우리가 원하는 첫 수 그 자체**여야 한다.
# 그리고 여기서도 코드가 만든다 — Kafka가 없는 사이트에 `kafka.list_topics`가 예시로
# 박혀 있으면 모델은 그걸 **그대로 부른다.**

# frame이 쓸 읽기 — 인자에 대상 이름이 안 들어간다. **베껴도 안전하다.**
_DISCOVERY = (("mongo.list_collections", {}),
              ("kafka.list_topics", {}),
              ("redis.scan", {"pattern": "*"}))

# integrate가 쓸 읽기 — 찾은 이름으로 부른다. 값이 **지시문 모양**이어야 모델이
# 바꿔 넣는다. `"collection": "..."`처럼 완결된 값을 두면 진짜로 `"..."`를 조회하고,
# 빈 결과가 "데이터가 없다"로 읽힌다 — ⑮가 경고하는 바로 그 오독이다.
# `limit`이 작은 이유: **읽을 수 없는 5건보다 읽을 수 있는 3건이 낫다.** 예산은
# 증거 하나당 고정이라 건수를 늘리면 건당 글자가 그만큼 줄고, 사내 측정에서
# `limit=5`의 문서 다섯 건이 한 건도 온전히 안 들어갔다(10b의 258자 문서와 같은 일).
#
# `filter`가 **비어 있지 않은** 이유: 예전엔 `{}`였고, 그게 리드가 가진 유일한
# `mongo.find` 본보기였다. 그래서 잘린 결과를 보고 "좁혀서 물어라"는 말을 들어도
# **좁히는 모양을 몰라서** 같은 질의를 그대로 다시 냈다(사내 측정 t-9).
# 산문으로 시키는 것과 예시로 보여 주는 것은 이 모델에게 전혀 다르다.
_NAMED_READ = (("mongo.find", {"collection": "위 증거에서 본 컬렉션 이름",
                               "filter": {"위 증거에서 본 필드 이름": "찾으려는 값"},
                               "limit": 3}),
               ("kafka.tail", {"topic": "위 증거에서 본 토픽 이름", "limit": 5}),
               # 컨슈머 lag는 파이프라인 점검의 표준 읽기인데, 예시에 없으니 베끼는 모델은
               # 한 번도 안 냈다(사내 네 실행 전부). 그룹 이름은 대상 config에 있다.
               ("kafka.group_offsets", {"group": "위 증거에서 본 컨슈머 그룹 이름"}),
               ("redis.get", {"key": "위 증거에서 본 키 이름"}))

_GOAL = "무엇을 확인하는가 (한국어)"


def _discovery(services: tuple[str, ...]):
    """발견 라운드의 수. **코드가 있으면 그게 첫 수다.**

    이름은 대상의 config에 산다(③-2). 그러니 **추측할 이유가 없다** — 10b 측정에서
    리드가 반복해서 `topic='GUMI_ALARM_EVENT'` 같은 이름을 지어냈는데, 그건 모델이
    게을러서가 아니라 **찾을 방법을 안 줬기 때문**이다.

    `service` 자리에 진짜 이름을 박는다. 사내 모델은 완결된 구체값을 그대로
    복사한다 — `code.services`를 먼저 부르게 시키는 2단계보다 훨씬 잘 먹는다.
    """
    if not services:
        return _DISCOVERY
    return (("code.config", {"service": services[0]}),) + _DISCOVERY


def _refinable(params: dict) -> bool:
    """다시 내도 **같은 질의가 아닌** 읽기 — 좁힐 축(`filter`)이 있는 것.

    `code.grep`·`kafka.tail`·`redis.get`은 리드가 아는 이름이 하나면 다시 내는 순간
    중복이다. `mongo.find`는 `filter`가 다르면 다른 질의고, 그게 조사가 실제로
    나아가는 방향이다.
    """
    return bool(params.get("filter"))


def _named_reads(services: tuple[str, ...]):
    """이름을 찾은 뒤의 수. 코드가 있으면 **그 이름을 코드에서 대조**하는 것이 첫 수다.

    10b 진단에서 제일 많이 나온 계약 위반이 "찾지 않고 이름을 댔다"였다.
    grep은 그 이름이 **실재하는지**를 코드로 확인하는 유일한 수단이다.
    """
    if not services:
        return _NAMED_READ
    return (("code.grep", {"patterns": ["위 증거에서 본 이름"]}),) + _NAMED_READ


def _has(site_config, adapter: str, services: tuple[str, ...]) -> bool:
    """그 어댑터를 이 사이트에서 부를 수 있나.

    `code`만 판단 근거가 다르다 — config가 아니라 knowledge가 있어야 한다.
    `_INFRA_FIELD`에 없는 이름을 조용히 통과시키면, 새 어댑터를 더했을 때
    **선언하지도 않은 시스템이 목록에 뜬다.** 그래서 모르는 이름은 막는다.
    """
    if adapter == "code":
        return bool(services)
    field = _INFRA_FIELD.get(adapter)
    return field is not None and getattr(site_config.infra, field, None) is not None


def _available(site_config, shapes, limit: int,
               services: tuple[str, ...] = ()) -> list[tuple[str, dict]]:
    picked = [(action, params) for action, params in shapes
              if _has(site_config, ACTIONS[action][0], services)]
    return picked[:limit]


def _free_rest_entry(site_config) -> tuple[str, dict] | None:
    """필수 인자가 없는 등재 항목 하나. 있으면 frame 예시에 끼운다 —
    REST는 이름을 찾을 필요가 없는(config가 선언한) 읽기다."""
    rest = site_config.infra.rest
    for name, entry in sorted((rest.entries if rest else {}).items()):
        if not any(spec.required for spec in entry.params.values()):
            return ("rest.query", {"entry": name, "params": {}})
    return None


def _done(tasks, action: str, **match):
    """증거를 만든 **마지막** 태스크 — action이 같고 params가 `match`를 담은 것. 없으면 None."""
    for t in reversed(tuple(tasks)):
        if (t.action == action and t.status == "ok" and t.result_evidence_ids
                and all(t.params.get(k) == v for k, v in match.items())):
            return t
    return None


def _issued(tasks, action: str, **match) -> bool:
    """그 action을 그 인자로 **낸 적이 있나** — 상태 불문. 실패한 시도도 낸 것이다."""
    return any(t.action == action and all(t.params.get(k) == v for k, v in match.items())
               for t in tasks)


def _ladder_step(site_config, tasks, *, services: tuple[str, ...], used: tuple[str, ...],
                 flow_graph: dict | None, evidence=(), code_index: bool = False):
    """사다리의 **다음 한 칸** — `((action, params), input_evidence_ids)` 또는 None.

    rest 증거(증상 재현)가 있고 그 끝점의 사슬이 오버레이에 있으면 `code.trace(endpoint=그 path)`, trace
    증거까지 있으면 그 끝점이 읽는 컬렉션에 대한 `recompute.count(expect=그 rest 증거)`. 목록에만 있고
    예시에 없는 action은 베끼는 모델이 한 번도 안 낸다(10b) — 셋째·넷째 칸이 사내 네 실행에서 0번이었다.

    재집계 뒤 두 칸(11d 6d-2): **`code.uses(name)`** — 일치(컬렉션도 옛것)면 그 컬렉션을 누가 쓰나, 불일치
    (컬렉션은 최신인데 화면이 옛것)면 끝점이 읽은 캐시 키를 누가 쓰나. 측정판의 두 변형(sink-stopped·cache-stale)이
    정확히 이 둘이고, `match`는 재집계 증거에 그대로 있다. 그다음 **`code.callers(name)`** — uses 증거의 첫 "쓰기"
    함수를 누가 부르나. 이름은 전부 코드가 증거에서 뽑아 박는다(리드가 지어내는 자리가 아니다). 심볼 인덱스가 없는
    조사에서는 두 칸이 없다(그 action이 목록에도 없다).

    **진짜 값(path·증거 id)을 박는다.** `supporting_ids`가 모양만 보여 주는 것과 반대인데, 여기서는
    그대로 베끼는 것이 정확히 원하는 출력이기 때문이다(frame의 `code.grep patterns=[path]`와 같은 선택).
    한 라운드에 한 칸이다 — 다음 칸의 입력이 이 칸의 증거라서, 둘을 같이 보여 주면 뒤 칸은 게이트에
    붙잡힌 채 번호만 쓴다. 없는 문은 안 보여 준다 — 그래프가 없거나 그 끝점이 추적 안 됐으면
    `code.trace`는 error로 답하고, 리드는 그 라운드를 잃는다.

    `code.trace` 칸의 억제는 action 이름(`used`)이 아니라 **이 path로 낸 적이 있나**다 — 예비 측정에서
    대역이 `endpoint`에 서비스 이름을 넣어 두 번 실패했고, 이름 기준이었다면 그 뒤로 칸이 사라져 사다리를
    끝내 못 밟는다(실제로 그랬다).
    """
    rest = _done(tasks, "rest.query")
    path = _rest_path(site_config, (rest.action, rest.params)) if rest else None
    if not path:
        return None
    rest_id = rest.result_evidence_ids[0]
    traced = _done(tasks, "code.trace", endpoint=path)
    if traced is None:
        if (_issued(tasks, "code.trace", endpoint=path) or flow_graph is None
                or not _has(site_config, "code", services)
                or flowgraph.trace_lines(flow_graph, flowgraph.endpoint_id(path)) is None):
            return None
        return ("code.trace", {"endpoint": path}), [rest_id]
    ep = flowgraph.endpoint_id(path)
    collections = flowgraph.traced_reads(flow_graph, ep, kind="collection") if flow_graph is not None else []
    recomputed = _done(tasks, "recompute.count")
    if recomputed is None:
        if "recompute.count" in used or not _has(site_config, "recompute", services):
            return None
        # `expect.path`는 지시문 모양이다 — rest 원본은 `{"request", "status", "response"}`라 `response` 아래에
        # 있다는 것까지만 우리가 안다. 숫자를 옮겨 적게 하지 않는다(3b-1).
        shape = ("recompute.count", {
            "collection": collections[0] if collections else "위 추적 증거에서 본 컬렉션 이름",
            "filter": {"위 증거에서 본 필드 이름": "찾으려는 값"},
            "expect": {"evidence": rest_id,
                       "path": "response 아래 그 숫자의 위치 — response.items[0].alarm 같은 모양"}})
        return shape, [traced.result_evidence_ids[0], rest_id]
    if not code_index or flow_graph is None:
        return None
    keys = flowgraph.traced_reads(flow_graph, ep, kind="rediskey")
    match = _recompute_match(evidence, recomputed.result_evidence_ids[0])
    # 불일치인데 끝점이 키를 안 읽는 사슬이면 컬렉션으로 — 없는 이름을 박으면 리드가 그 문을 두드린다.
    name = keys[0] if (match is False and keys) else (collections[0] if collections else None)
    if name is None:
        return None
    uses = _done(tasks, "code.uses", name=name)
    if uses is None:
        if _issued(tasks, "code.uses", name=name):
            return None
        return ("code.uses", {"name": name}), [recomputed.result_evidence_ids[0]]
    writer = _first_writer(evidence, uses.result_evidence_ids[0])
    if writer is None or _issued(tasks, "code.callers", name=writer):
        return None
    return ("code.callers", {"name": writer}), [uses.result_evidence_ids[0]]


_MATCH = re.compile(r"\bmatch'?: (True|False)")
# 실행기는 문자열 결과를 repr로 눕힌다(`detail`) — 개행이 `\n` 두 글자로 온다. 둘 다 받는다.
_WRITER = re.compile(r"쓰기 \d+:(?:\\n|\n)\s*(\S+) \(")


def _evidence_text(evidence, evidence_id: str) -> str:
    ref = next((e for e in evidence if e.id == evidence_id), None)
    return f"{ref.summary}\n{ref.body}" if ref is not None else ""


def _recompute_match(evidence, evidence_id: str) -> bool | None:
    m = _MATCH.search(_evidence_text(evidence, evidence_id))
    return None if m is None else m.group(1) == "True"


def _first_writer(evidence, evidence_id: str) -> str | None:
    m = _WRITER.search(_evidence_text(evidence, evidence_id))
    return m.group(1) if m else None


def _task(index: int, action: str, params: dict, *, rank: int = 1, **extra) -> dict:
    # `priority`는 번호가 아니라 **이 라운드 안의 순서**를 따른다. 번호를 곱하면
    # 라운드가 깊어질수록 우선순위가 커져(늦어져) 앞 라운드의 잔여 태스크에 계속
    # 밀린다 — 정작 지금 제일 궁금한 읽기가 제일 나중이 된다.
    # `role`은 예시에 없다 — 코드가 action에서 정한다(`role_for`). 보여 주면 모델이
    # 태스크마다 다른 이름으로 "다듬고", 그게 닫힌 어휘라 답 전체가 거부됐었다.
    return {"id": f"t-{index}", "goal": _GOAL,
            "action": action, "params": params, "priority": rank * 10, **extra}


_TASK_NUMBER = re.compile(r"^t-(\d+)$")


def next_task_number(state: CaseState) -> int:
    """리드가 써야 할 **다음 빈 태스크 번호**.

    예시의 id까지 그대로 베끼는 모델이므로, 예시가 **이미 쓴 번호**를 보여 주면
    완료된 태스크가 같은 id로 다시 들어온다. 사내에서 실제로 났다 — 예시의
    `t-4`·`t-5`를 매 라운드 베껴서 같은 읽기가 세 번씩 돌았다.

    `_accept_tasks`의 `taken`이 그걸 막지만, 막기만 하면 3라운드부터 새 태스크가
    0이 되어 조사가 얕아진다. **베끼는 성질과 싸우지 말고 이용한다** — 매 라운드
    다음 번호를 보여 주면 베껴도 새 id가 된다.
    """
    used = [int(m.group(1)) for t in state.plan_tasks
            if (m := _TASK_NUMBER.match(t.id))]
    return max(used, default=0) + 1


def example_block(site_config, *, phase: str, start: int = 1,
                  services: tuple[str, ...] = (), used: tuple[str, ...] = (),
                  first_read: tuple[str, dict] | None = None,
                  flow_graph: dict | None = None, tasks=(), evidence=(), code_index: bool = False) -> str:
    """프롬프트의 `{example}` 자리. **이게 다음 라운드의 실제 출력이 된다.**

    `used`는 이 케이스에서 **이미 낸 action**들이다. 빼지 않으면 예시가 라운드마다
    똑같고, 모델은 그걸 그대로 복사해 **같은 질의를 다시 낸다** — 사내 측정에서
    t-8·t-9가 정확히 그랬다. 예시가 곧 명세라는 성질(10b)이 반대로 작동한 것이다.

    `tasks`는 State의 태스크(integrate만) — 어느 증거까지 왔는지를 보고 사다리의 다음 칸을
    첫 줄에 둔다(`_ladder_step`).
    """
    if phase == "frame":
        shapes: list[tuple[str, dict]] = []
        if first_read and _has(site_config, ACTIONS[first_read[0]][0], services):
            # 사다리의 첫 두 칸이 예시다 — ① 판정이 본 읽기로 증상을 재현하고 ② 그 path를 코드에서
            # 찾는다. 예시가 곧 출력이라(위) 규칙 문장으로는 안 되고 여기 있어야 리드가 밟는다.
            shapes.append(first_read)
            path = _rest_path(site_config, first_read)
            if path and services:
                # 그래프가 서빙 서비스를 하나로 알면 그 레포만 뒤진다 — 모르면 전체다.
                served = flowgraph.serving_services(flow_graph, path) if flow_graph else []
                shapes.append(("code.grep", {"patterns": [path],
                                             **({"service": served[0]} if len(served) == 1 else {})}))
        room = (4 if shapes else 3) - len(shapes)
        shapes += _available(site_config, _discovery(services), room, services)
        free = None if first_read else _free_rest_entry(site_config)
        if free and len(shapes) < 3:
            shapes.append(free)
        if not shapes:
            shapes = _available(site_config, _named_reads(services), 1, services) or [("rest.query", {
                "entry": "등재 목록의 항목 이름", "params": {}})]
        body = {"hypotheses": [{"id": "h-1", "statement": "원인 가설 하나 (한국어 한 문장)"},
                               {"id": "h-2", "statement": "다른 가능성 (한국어 한 문장)"}],
                "tasks": [_task(start + n, a, p, rank=n + 1)
                          for n, (a, p) in enumerate(shapes)]}
    else:
        # **`filter`가 있는 읽기는 이미 썼어도 다시 보여 준다.** 같은 action이어도
        # 좁힌 질의는 새 질의다. 처음엔 action 이름으로 뺐는데, 그러면 리드가 문서를
        # 읽어 **필드를 알게 된 바로 그 라운드부터** 좁히는 본보기가 사라진다 —
        # 사내 측정에서 r1에만 보이고(채울 필드가 아직 없을 때) r2부터 없어졌고,
        # 리드는 끝까지 `filter={}`였다.
        fresh = tuple(shape for shape in _named_reads(services)
                      if shape[0] not in used or _refinable(shape[1]))
        # 전부 써 봤으면 어쩔 수 없이 다시 보여 준다 — 빈 예시는 형식 자체를
        # 못 보여 주므로 더 나쁘다. 그때는 좁힌 `filter`가 차이를 만든다.
        # **이 사이트에서 쓸 수 있는 것**이 남았는지로 판단한다 — `fresh` 자체는
        # `mongo.find`가 늘 남아 비지 않으므로, mongo가 없는 사이트에서 `fresh`만
        # 보면 전부 쓴 뒤 예시가 빈다(RED 스윕이 잡았다).
        shapes = (_available(site_config, fresh, 2, services)
                  or _available(site_config, _named_reads(services), 2, services))
        if not shapes:
            # 마지막 수단도 **이 사이트에 실재하는 것**이어야 한다. 예전엔
            # `rest.query`를 손으로 박아 뒀는데, REST가 없는 사이트에도 그게
            # 나가서 모델이 **그대로 부른다** — 이 파일 맨 위가 경고하는 그 실패다.
            entry = _free_rest_entry(site_config)
            shapes = [entry] if entry else []
        ladder = _ladder_step(site_config, tasks, services=services, used=used, flow_graph=flow_graph,
                              evidence=evidence, code_index=code_index)
        lead = ([_task(start, ladder[0][0], ladder[0][1], rank=1, input_evidence_ids=ladder[1])]
                if ladder else [])
        body = {"decision": "continue",
                "hypotheses": [{"id": "h-1", "statement": "갱신한 가설 (한국어 한 문장)",
                                "status": "supported",
                                # **모양을 같이 적는다.** 지시문만 있으면 모델은
                                # 무엇이든 id처럼 생긴 것을 넣는데, 사내 측정에서
                                # 태스크 id(`t-5`)를 넣었다 — 증거 id는 `t-5.e1`이다.
                                # 환각이 아니라 형식을 몰랐던 것이고, 그건 우리가
                                # 안 보여 준 탓이다(이 단계에서 네 번째로 같은 교훈).
                                "supporting_ids": ["위 <모은 증거>에 실제로 있는 id "
                                                   "— `t-3.e1` 같은 모양"],
                                "refuting_ids": []}],
                "tasks": lead + [_task(start + len(lead) + n, a, p, rank=len(lead) + n + 1,
                                       input_evidence_ids=[])
                                 for n, (a, p) in enumerate(shapes)]}
    return json.dumps(body, ensure_ascii=False, indent=2)


# 프롬프트 템플릿이 쓸 수 있는 자리 이름. **기동 검증이 이 목록으로 템플릿을
# 검사한다**(`__main__._load_lead_prompt`) — 여기 없는 이름을 적으면 그 `{...}`는
# 치환되지 않은 채 LLM에게 나가고, 응답은 그럴듯해 보여서 아무도 못 본다.
# 9e에서 `{max_chars}`가 실제로 그렇게 새 나갔다.
FRAME_SLOTS = frozenset({"case", "actions", "example", "flow"})


def flow_block(state: CaseState, graph: dict | None, *, budget: int = 800,
               texts: tuple[str, ...] = ()) -> str:
    """`<데이터 흐름>` — 그래프(11c)를 리드에게 준다. **config 층만**, 씨앗은 증상·증거·가설에
    나온 이름. 그래프가 없거나 낡았으면(호출부가 None을 준다) 없다고 적는다 — 낡은 배선을
    사실처럼 실으면 리드가 떠 있지도 않은 코드의 흐름을 믿는다."""
    if graph is None:
        return "(없음 — `python -m src code graph`로 만들면 여기 실린다)"
    # 접수 경로 줄(REST path)이 `texts`로 들어온다 — 증상 문장엔 그래프 이름이 없어도 끝점 노드가
    # 씨앗이 되어 첫 줄이 "이 path를 누가 서빙하나"가 된다(사다리의 첫 칸).
    seeds_from = [state.case.symptom, *texts]
    seeds_from += [f"{ref.summary}\n{ref.body}" for ref in state.evidence]
    seeds_from += [h.statement for h in state.hypotheses]
    body = flowgraph.flow_text(graph, flowgraph.find_seeds(graph, seeds_from), budget=budget)
    # 머리말은 한 줄 — 사내 블록에서 머리말이 본문만큼 길었다. 규칙은 프롬프트 본문이 말한다.
    return ("config에서 뽑은 배선 — 실제 동작은 프로브로 확인. `레포{a,b}`는 같은 config를 쓰는 "
            "서비스 전부. 다른 이름은 code.flow(name).\n" + body)
INTEGRATE_SLOTS = FRAME_SLOTS | {"hypotheses", "tasks", "evidence", "round",
                                 "max_rounds", "rejected", "open"}


_DUP_MARK = "이미 한 읽기를 또 냈다"


def open_questions_block(state: CaseState) -> str:
    """`<열린 질문>` — 코드가 아는 "모르는 것"을 매 턴 리드 앞에 둔다.

    강한 모델은 잘린 표본·실패한 읽기·자기가 반복한 질의를 스스로 기억한다. 약한 모델은 못 한다 — c-1의 r2가 쥐고 있던
    "키가 없다"가 r3에서 사라졌다. 그래서 하네스가 유지한다: 잘린 증거(잘린 표본으로는 "없다"를 주장할 수 없다), 실패한
    읽기(사유 그대로 — 프록시 의심 같은 안내가 거기 있다), 거부된 중복(결과는 이미 증거에 있다). R2-2b가 "선언됐는데
    없는 키"를 더한다. 리드가 정하는 것은 없다 — 사실의 목록이다.
    """
    lines = []
    cut = [ref.id for ref in state.evidence if not ref.complete]
    if cut:
        lines.append(f"- 잘린 증거 {len(cut)}건: {', '.join(cut)} — 잘린 표본으로는 \"없다\"를 주장할 수 없다. "
                     f"projection·limit·path로 좁혀 다시 읽어라")
    failed = [t for t in state.plan_tasks if t.status == "error"]
    if failed:
        lines.append(f"- 실패한 읽기 {len(failed)}건: " + "; ".join(f"{t.id} ({_oneline(t.error or '원인 불명')[:120]})"
                                                                for t in failed))
    dup = [e for e in state.llm_errors if _DUP_MARK in e]
    if dup:
        found = sorted({part.strip() for e in dup if "그 결과는 " in e
                        for part in e.split("그 결과는 ", 1)[1].split(",")})
        lines.append(f"- 같은 읽기를 {len(dup)}번 다시 냈다 — 결과는 이미 증거에 있다"
                     + (f": {', '.join(found)}" if found else ""))
    return "\n".join(lines) if lines else "(없음)"


def rejected_block(state: CaseState) -> str:
    """**버려진 태스크를 리드에게 돌려준다.**

    이게 없어서 리드는 자기 태스크가 버려진 걸 몰랐다. 사내 측정에서 같은 질의를
    세 번 냈고, 세 번 다 조용히 거부됐고, 낼 것이 없어져 `no_runnable`로 끝났다.
    **피드백 없이 같은 상태를 보여 주면 같은 답이 나오는 것이 당연하다.**

    통제 경계는 그대로다 — 무엇을 받을지는 여전히 코드가 정한다(규율 6).
    여기서 주는 것은 결정권이 아니라 **결과**다.
    """
    if not state.llm_errors:
        return "(없음)"
    return "\n".join(f"- {_oneline(reason)}" for reason in state.llm_errors)


def frame_fields(state: CaseState, *, site_config,
                 services: tuple[str, ...] = (),
                 roles: dict[str, str] | None = None,
                 flow_graph: dict | None = None, code_index: bool = False) -> dict[str, str]:
    return {"case": case_block(state, site_config=site_config),
            "actions": action_catalog(site_config, services=services, roles=roles,
                                      hide=_hidden(flow_graph, code_index)),
            "example": example_block(site_config, phase="frame",
                                     start=next_task_number(state),
                                     services=services,
                                     first_read=start_read(state.case, site_config),
                                     flow_graph=flow_graph),
            "flow": flow_block(state, flow_graph,
                               texts=(origin_line(state.case, site_config) or "",))}


def _hidden(flow_graph: dict | None, code_index: bool = False) -> frozenset[str]:
    # 없는 문을 열라고 적어 두면 리드가 거기로 간다 — 그래프가 없으면 `code.flow`·`code.trace`를, 심볼 인덱스가
    # 없으면(옛 번들) `code.callers`·`code.uses`를 목록에서 뺀다.
    out: set[str] = set()
    if flow_graph is None:
        out |= {"code.flow", "code.trace"}
    if not code_index:
        out |= {"code.callers", "code.uses"}
    return frozenset(out)


def integrate_fields(state: CaseState, *, site_config, max_rounds: int,
                     evidence_budget: int = 12000,
                     services: tuple[str, ...] = (),
                     roles: dict[str, str] | None = None,
                     flow_graph: dict | None = None, code_index: bool = False) -> dict[str, str]:
    return {"case": case_block(state, site_config=site_config),
            "actions": action_catalog(site_config, services=services, roles=roles,
                                      hide=_hidden(flow_graph, code_index)),
            "flow": flow_block(state, flow_graph,
                               texts=(origin_line(state.case, site_config) or "",)),
            "example": example_block(site_config, phase="integrate",
                                     start=next_task_number(state),
                                     services=services,
                                     used=tuple(t.action for t in state.plan_tasks
                                                if t.action),
                                     tasks=tuple(state.plan_tasks), flow_graph=flow_graph,
                                     evidence=tuple(state.evidence), code_index=code_index),
            "hypotheses": hypotheses_block(state),
            "tasks": tasks_block(state),
            "evidence": evidence_block(state, budget=evidence_budget),
            "rejected": rejected_block(state),
            "open": open_questions_block(state),
            "round": str(state.round),
            "max_rounds": str(max_rounds)}


# ── 12a — 판정 턴의 재료 ──────────────────────────────────────────────

CONCLUDE_SLOTS = frozenset({"case", "flow", "hypotheses", "tasks", "evidence", "ended",
                            "rewrite", "components", "example", "open"})
# 판정 턴에는 부를 읽기가 없다 — `{actions}`를 요구하면 운영이 빈 목록을 넣어 통과시킨다.
# 대신 `{evidence}`가 없으면 리드는 인용할 것을 못 본다.
CONCLUDE_REQUIRED = ("case", "evidence", "example")

_ENDED = {"decision": "리드가 끝냈다(원인이 충분히 좁혀졌다고 판단)",
          "max_rounds": "라운드 상한에 닿았다 — 억지 결론 대신 미확정을 허용한다",
          "no_runnable": "더 볼 것이 없었다(낼 읽기가 남지 않았다)",
          "llm_error": "리드 LLM이 응답하지 못했다"}


def ended_line(state: CaseState) -> str:
    """조사가 **왜** 끝났는지를 사람 말로. `stopped_by` 값을 그대로 보이면 리드는 그 토큰을 판정에
    베낀다. 태스크 오류율을 같이 적는다 — 실패한 읽기가 많은 조사의 "없다"는 약하다."""
    reason = _ENDED.get(state.stopped_by or "", state.stopped_by or "아직 안 끝났다")
    errors = sum(1 for t in state.plan_tasks if t.status == "error")
    return (f"라운드 {state.round}에서 끝났다 — {reason}. "
            f"태스크 오류 {errors}/{len(state.plan_tasks)}건")


def rewrite_block(state: CaseState) -> str:
    """verify가 남긴 문제 — 되물을 때만 실린다. 문제 없는 첫 판정에 빈 머리말을 두면 리드가
    "고칠 것"을 지어낸다."""
    if not state.verify_problems:
        return ""
    return ("## 재작성\n\n앞의 판정은 검증을 통과하지 못했다. 아래를 고쳐 **다시** 쓰라 — "
            "없는 id는 빼고, 없는 부품은 증거에 있는 이름으로 바꾸고, 잘린 증거는 caveats에 적어라.\n"
            + "\n".join(f"- {_oneline(p)}" for p in state.verify_problems))


def components_line(services: tuple[str, ...]) -> str:
    """`component`에 쓸 수 있는 이름 — 토폴로지에서 **생성**한다(손으로 적으면 ⑮를 어긴다)."""
    tail = "위 <모은 증거>에 나온 자원 이름(컬렉션·토픽·키)"
    if not services:
        return tail + " 또는 <데이터 흐름>의 서비스 이름"
    return "서비스 " + ", ".join(services) + " — 또는 " + tail


def verdict_example() -> str:
    """판정 JSON의 틀. **예시가 곧 출력이다** — `degraded`를 보여 주면 리드가 베낀다(그건 코드의
    낙인이다). `verdict_type` 자리는 값이 아니라 고를 목록이라 그대로 두면 검증이 거부하고 수리
    재시도가 "그중 하나를 적어라"를 전한다."""
    body = {
        "verdict_type": "<logic_bug | data_loss | config_error | stale_data | external | inconclusive 중 하나>",
        "root_cause": {"component": "위 component 후보 중 하나",
                       "evidence_ids": ["위 <모은 증거>에 실제로 있는 id"]},
        "alternates": [{"component": "위 component 후보 중 하나",
                        "evidence_ids": ["위 <모은 증거>에 실제로 있는 id"],
                        "confidence": "low",
                        "relation": "왜 후보이고 왜 최상위가 아닌가 (한국어)"}],
        "contributing": [],
        "confidence": "<high | medium | low 중 하나>",
        "recommendations": ["사람이 할 조치 (한국어)"],
        "caveats": ["잘린 증거(⚠)로 주장했다면 그 증거 id를 여기 적는다"],
        "narrative": "인과 사슬을 한 문단으로 — 무엇이 어디서 멈춰 증상이 됐나 (한국어)",
    }
    return json.dumps(body, ensure_ascii=False, indent=2)


def conclude_fields(state: CaseState, *, site_config, evidence_budget: int = 12000,
                    services: tuple[str, ...] = (),
                    flow_graph: dict | None = None) -> dict[str, str]:
    return {"case": case_block(state, site_config=site_config),
            "flow": flow_block(state, flow_graph,
                               texts=(origin_line(state.case, site_config) or "",)),
            "hypotheses": hypotheses_block(state),
            "tasks": tasks_block(state),
            "evidence": evidence_block(state, budget=evidence_budget),
            "open": open_questions_block(state),
            "ended": ended_line(state),
            "rewrite": rewrite_block(state),
            "components": components_line(tuple(services)),
            "example": verdict_example()}
