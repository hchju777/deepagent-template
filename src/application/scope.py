"""사이트 축 해석 — 증상 하나로 어느 사이트를 조사할지 정한다 (스펙 §4.4).

CLI는 `--gbm/--fct`를 필수로 받아 이 문제가 없었다. 웹 사용자는 그 쌍을 주지
않으므로 registry의 활성 사이트를 후보로 해석 단계가 필요하다.

**후보는 코드가 만들고 LLM은 그 안에서만 고른다**(규율 6). 이 축은 어느 법인의
Redis/Mongo/Kafka와 소스 저장소를 읽을지를 정한다 — 증상 문자열 하나가 그것을
자유 서술로 바꾸게 두면, 프롬프트에 실린 텍스트가 다른 법인의 조사를 여는 통로가
된다. 목록 밖 값은 확정이 아니라 미확정으로 처리한다.

**미확정이면 케이스를 만들지 않는다.** 스코프가 없는 케이스는 어떤 어댑터로
무엇을 조사할지도, 누가 볼 수 있는지도 정해지지 않아 뜻이 없다. 후보와 질문을
돌려주고 호출자가 사람에게 물어 다시 제출하게 한다 — 무상태 재제출이라 초안
저장소가 필요 없다.

이 모듈은 `SiteRuntime`이 아니라 `[(gbm, fct)]`만 받는다. 계획 13의 `api`
프로세스는 대상 시스템에 붙지 않으므로(스펙 §3.1) 어댑터가 달린 런타임을
요구하면 그 성질이 깨진다.
"""
from datetime import datetime
from typing import Any, Callable, Literal

from src.application.schemas import parse_structured
from src.config.schema_app import StrictModel

Site = tuple[str, str]


class ScopeResult(StrictModel):
    status: Literal["resolved", "unresolved"]
    gbm: str | None = None
    fct: str | None = None
    candidates: list[Site] = []      # 미확정일 때 호출자가 사람에게 보여줄 유효한 답
    questions: list[str] = []
    problems: list[str] = []


class _ScopeLlmOutput(StrictModel):
    gbm: str = ""
    fct: str = ""


def _label(site: Site) -> str:
    return f"{site[0]}/{site[1]}"


def _unresolved(sites: list[Site], *, problems: list[str]) -> ScopeResult:
    listing = ", ".join(_label(s) for s in sites) or "없음"
    return ScopeResult(
        status="unresolved", candidates=sites, problems=problems,
        # 질문만 주면 호출자가 유효한 답의 집합을 모른다 — 후보를 반드시 함께 준다.
        questions=[f"어느 사이트를 조사할까? 후보: {listing}"])


def _prompt(symptom: str, sites: list[Site]) -> str:
    listing = "\n".join(f"- {_label(s)}" for s in sites)
    return (
        f"[증상] {symptom}\n"
        f"[사이트 후보]\n{listing}\n\n"
        "위 증상이 어느 사이트의 문제인지 후보 중에서 정확히 하나를 골라라. "
        "후보 목록에 없는 값을 지어내지 마라 — 확신이 없으면 아무거나 고르지 말고 "
        "빈 문자열을 넣어라.\n"
        'JSON만 출력하라: {"gbm": "...", "fct": "..."}'
    )


async def resolve_scope(symptom: str, *, sites: list[Site], deps: Any,
                        clock: Callable[[], datetime],
                        gbm: str | None = None, fct: str | None = None) -> ScopeResult:
    """조사할 사이트를 정한다. 절대 raise하지 않는다.

    호출자가 준 값이 있으면 그대로 쓰되 **후보 안인지는 확인한다** — 없는 사이트로
    케이스를 열면 조립이 뒤에서 깨지고, 그때는 이미 레코드가 남아 있다.
    """
    known = [tuple(s) for s in sites]
    if not known:
        return _unresolved(known, problems=["활성 사이트가 없다 — registry를 확인하라"])

    if gbm is not None and fct is not None:
        if (gbm, fct) in known:
            return ScopeResult(status="resolved", gbm=gbm, fct=fct)
        return _unresolved(known, problems=[f"사이트 {gbm}/{fct}가 registry에 없다"])

    if len(known) == 1:
        # 사이트가 하나인 설치에서 매번 LLM을 부르는 것은 낭비이자 실패 지점이다.
        return ScopeResult(status="resolved", gbm=known[0][0], fct=known[0][1])

    try:
        response = await deps.lead_llm.ainvoke([("user", _prompt(symptom, known))])
    except Exception as exc:                                    # noqa: BLE001 — 무raise 계약
        return _unresolved(known, problems=[f"사이트 해석 LLM 호출 실패 — "
                                            f"{type(exc).__name__}: {exc}"])
    out, err = parse_structured(response.content, _ScopeLlmOutput)
    if out is None:
        return _unresolved(known, problems=[f"사이트 해석 응답 파싱 실패 — {err}"])
    if (out.gbm, out.fct) not in known:
        return _unresolved(known, problems=[f"고른 사이트 {out.gbm!r}/{out.fct!r}가 "
                                            f"후보 목록에 없다"])
    return ScopeResult(status="resolved", gbm=out.gbm, fct=out.fct)
