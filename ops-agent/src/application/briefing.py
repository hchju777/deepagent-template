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
from src.domain.actions import ACTIONS

# 리드가 쓸 수 없는 action. rest.query는 등재 항목마다 따로 적는다(인자가 다르다).
_RENDERED_SEPARATELY = {"rest.query"}


def action_catalog(site_config, *, services: tuple[str, ...] = ()) -> str:
    """부를 수 있는 읽기 목록. **config에서 생성한다.**

    `services`는 대상 코드(11a)가 준비됐을 때만 채워진다 — 코드의 가용 여부는
    `site.infra`가 아니라 **knowledge(토폴로지·배포)**에서 오기 때문이다. 비어
    있으면 `code.*`는 목록에 아예 안 나온다. 없는 문을 열라고 적어 두면 리드가
    거기로 가고, 매 라운드가 "미등재 action"으로 날아간다.
    """
    lines: list[str] = []
    for name, (adapter, _, required, optional) in sorted(ACTIONS.items()):
        if name in _RENDERED_SEPARATELY:
            continue
        if not _has(site_config, adapter, services):
            continue          # 이 사이트에 없는 시스템은 목록에 없다
        lines.append(f"- {name}({_args(required, optional)})")
    if services:
        # **이름을 목록에 박아 둔다.** 사내 모델은 완결된 구체값을 그대로 복사하고
        # 지시문 모양은 바꿔 넣는다(10b에서 측정). 서비스 이름을 여기 안 적으면
        # `service="..."`를 진짜로 조회한다.
        lines.append(f"  (service 자리에 쓸 이름: {', '.join(services)})")

    rest = site_config.infra.rest
    for entry_name, entry in sorted((rest.entries if rest else {}).items()):
        params = ", ".join(
            f"{k}{'' if spec.required else '?'}: {spec.type}"
            for k, spec in sorted(entry.params.items()))
        lines.append(f'- rest.query(entry="{entry_name}", params={{{params}}})')
    return "\n".join(lines) or "- (이 사이트에 부를 수 있는 것이 없다)"


# 어댑터 이름 → SiteConfig.infra의 필드 이름. 둘이 다른 것은 `mongodb` 하나뿐이다.
_INFRA_FIELD = {"redis": "redis", "mongo": "mongodb", "kafka": "kafka", "rest": "rest"}


def _args(required: tuple, optional: tuple) -> str:
    parts = list(required) + [f"{name}?" for name in optional]
    return ", ".join(parts)


def case_block(state: CaseState) -> str:
    case = state.case
    return (f"사이트: {case.site}\n"
            f"증상: {case.symptom}\n"
            f"발생 시각: {case.t0.isoformat()}\n"
            f"접수 경로: {'사람' if case.origin == 'human' else '순찰'}")


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
        cut = "" if ref.complete else "  ⚠ 표본이 잘렸다 — '없다'를 주장할 수 없다"
        lines.append(f"- {_oneline(ref.id)} | {_oneline(ref.source)} | "
                     f"{_oneline(ref.summary)}{cut}")
        if ref.id not in detailed:
            lines.append("    (내용은 예산에서 빠졌다 — 필요하면 다시 읽어라)")
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
        lines.append(f"- {task.id} [{task.status}] {_oneline(task.goal)}"
                     + (f" — {_oneline(detail)}" if detail else ""))
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
_NAMED_READ = (("mongo.find", {"collection": "위 증거에서 본 컬렉션 이름",
                               "filter": {}, "limit": 5}),
               ("kafka.tail", {"topic": "위 증거에서 본 토픽 이름", "limit": 10}),
               ("redis.get", {"key": "위 증거에서 본 키 이름"}))

_GOAL = "무엇을 확인하는가 (한국어)"


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


def _task(index: int, action: str, params: dict, *, rank: int = 1, **extra) -> dict:
    # `priority`는 번호가 아니라 **이 라운드 안의 순서**를 따른다. 번호를 곱하면
    # 라운드가 깊어질수록 우선순위가 커져(늦어져) 앞 라운드의 잔여 태스크에 계속
    # 밀린다 — 정작 지금 제일 궁금한 읽기가 제일 나중이 된다.
    return {"id": f"t-{index}", "goal": _GOAL, "role": "data_prober",
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


def example_block(site_config, *, phase: str, start: int = 1) -> str:
    """프롬프트의 `{example}` 자리. **이게 다음 라운드의 실제 출력이 된다.**"""
    if phase == "frame":
        shapes = _available(site_config, _DISCOVERY, 3)
        free = _free_rest_entry(site_config)
        if free and len(shapes) < 3:
            shapes.append(free)
        if not shapes:
            shapes = _available(site_config, _NAMED_READ, 1) or [("rest.query", {
                "entry": "등재 목록의 항목 이름", "params": {}})]
        body = {"hypotheses": [{"id": "h-1", "statement": "원인 가설 하나 (한국어 한 문장)"},
                               {"id": "h-2", "statement": "다른 가능성 (한국어 한 문장)"}],
                "tasks": [_task(start + n, a, p, rank=n + 1)
                          for n, (a, p) in enumerate(shapes)]}
    else:
        shapes = _available(site_config, _NAMED_READ, 2) or [("rest.query", {
            "entry": "등재 목록의 항목 이름", "params": {}})]
        body = {"decision": "continue",
                "hypotheses": [{"id": "h-1", "statement": "갱신한 가설 (한국어 한 문장)",
                                "status": "supported",
                                "supporting_ids": ["위 <모은 증거>에 실제로 있는 id"],
                                "refuting_ids": []}],
                "tasks": [_task(start + n, a, p, rank=n + 1, input_evidence_ids=[])
                          for n, (a, p) in enumerate(shapes)]}
    return json.dumps(body, ensure_ascii=False, indent=2)


# 프롬프트 템플릿이 쓸 수 있는 자리 이름. **기동 검증이 이 목록으로 템플릿을
# 검사한다**(`__main__._load_lead_prompt`) — 여기 없는 이름을 적으면 그 `{...}`는
# 치환되지 않은 채 LLM에게 나가고, 응답은 그럴듯해 보여서 아무도 못 본다.
# 9e에서 `{max_chars}`가 실제로 그렇게 새 나갔다.
FRAME_SLOTS = frozenset({"case", "actions", "example"})
INTEGRATE_SLOTS = FRAME_SLOTS | {"hypotheses", "tasks", "evidence", "round", "max_rounds"}


def frame_fields(state: CaseState, *, site_config) -> dict[str, str]:
    return {"case": case_block(state), "actions": action_catalog(site_config),
            "example": example_block(site_config, phase="frame",
                                     start=next_task_number(state))}


def integrate_fields(state: CaseState, *, site_config, max_rounds: int,
                     evidence_budget: int = 12000) -> dict[str, str]:
    return {"case": case_block(state),
            "actions": action_catalog(site_config),
            "example": example_block(site_config, phase="integrate",
                                     start=next_task_number(state)),
            "hypotheses": hypotheses_block(state),
            "tasks": tasks_block(state),
            "evidence": evidence_block(state, budget=evidence_budget),
            "round": str(state.round),
            "max_rounds": str(max_rounds)}
