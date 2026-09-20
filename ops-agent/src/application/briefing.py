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
from src.application.state import CaseState
from src.domain.actions import ACTIONS

# 리드가 쓸 수 없는 action. rest.query는 등재 항목마다 따로 적는다(인자가 다르다).
_RENDERED_SEPARATELY = {"rest.query"}


def action_catalog(site_config) -> str:
    """부를 수 있는 읽기 목록. **config에서 생성한다.**"""
    lines: list[str] = []
    for name, (adapter, _, required, optional) in sorted(ACTIONS.items()):
        if name in _RENDERED_SEPARATELY:
            continue
        if getattr(site_config.infra, _INFRA_FIELD[adapter], None) is None:
            continue          # 이 사이트에 없는 시스템은 목록에 없다
        lines.append(f"- {name}({_args(required, optional)})")

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


def evidence_block(state: CaseState) -> str:
    """**리드가 실제로 본 것**만. 이게 나중에 판정이 인용할 수 있는 우주다(12a).

    **한 줄이 한 증거다.** 대상 데이터는 여러 줄이 정상인데 그게 날것으로 실리면
    이 블록에 **가짜 항목**이 생기고, 리드는 있지도 않은 증거 id를 인용한다.

    `runner_probe._summarize`가 `repr`로 이미 이스케이프하지만 여기서 한 번 더
    막는다 — `EvidenceRef`는 어디서나 만들 수 있고(11b의 서브에이전트가 곧 만든다),
    **"생산자가 다 지킨다"는 가정은 생산자가 늘어나면 깨진다.**
    """
    if not state.evidence:
        return "(아직 없다)"
    lines = []
    for ref in state.evidence:
        cut = "" if ref.complete else "  ⚠ 표본이 잘렸다 — '없다'를 주장할 수 없다"
        lines.append(f"- {_oneline(ref.id)} | {_oneline(ref.source)} | "
                     f"{_oneline(ref.summary)}{cut}")
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


# 프롬프트 템플릿이 쓸 수 있는 자리 이름. **기동 검증이 이 목록으로 템플릿을
# 검사한다**(`__main__._load_lead_prompt`) — 여기 없는 이름을 적으면 그 `{...}`는
# 치환되지 않은 채 LLM에게 나가고, 응답은 그럴듯해 보여서 아무도 못 본다.
# 9e에서 `{max_chars}`가 실제로 그렇게 새 나갔다.
FRAME_SLOTS = frozenset({"case", "actions"})
INTEGRATE_SLOTS = FRAME_SLOTS | {"hypotheses", "tasks", "evidence", "round", "max_rounds"}


def frame_fields(state: CaseState, *, site_config) -> dict[str, str]:
    return {"case": case_block(state), "actions": action_catalog(site_config)}


def integrate_fields(state: CaseState, *, site_config, max_rounds: int) -> dict[str, str]:
    return {"case": case_block(state),
            "actions": action_catalog(site_config),
            "hypotheses": hypotheses_block(state),
            "tasks": tasks_block(state),
            "evidence": evidence_block(state),
            "round": str(state.round),
            "max_rounds": str(max_rounds)}
