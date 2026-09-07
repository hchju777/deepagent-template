from src.application.nodes import make_nodes, route_after_verify
from src.application.state import CaseState
from src.domain.case import Case, CauseLink, EvidenceRef, PlanTask, Verdict
from tests.application.test_nodes_frame import T, _deps

VERDICT_JSON = ('{"verdict_type": "stale_data", "confidence": "high", "narrative": "n", '
                '"root_cause": {"component": "plan-sync", "evidence_ids": ["ev-1"]}}')


def _state(**kw):
    base = dict(case=Case(id="c-1", gbm="mx", fct="gumi", origin="patrol", symptom="s", t0=T))
    base.update(kw)
    return CaseState(**base)


async def test_증거_전멸은_LLM_없이_degraded():
    deps = _deps([])            # 스크립트 없음 — LLM 호출되면 RuntimeError로 테스트가 실패한다
    state = _state(plan_tasks=[PlanTask(id="t-1", goal="g", role="data_prober",
                                        status="error", error="타임아웃")])
    update = await make_nodes(deps)["conclude"](state)
    assert update["verdict"].verdict_type == "degraded"
    assert any("타임아웃" in c for c in update["verdict"].caveats)


async def test_재작성_요청은_verify_problems를_프롬프트에_싣는다():
    deps = _deps([VERDICT_JSON])
    state = _state(evidence=[EvidenceRef(id="ev-1", source="mongo:twin_state", summary="s")],
                   verify_problems=["없는 id ev-9 인용"])
    update = await make_nodes(deps)["conclude"](state)
    assert update["verdict"].root_cause.component == "plan-sync"
    assert "없는 id ev-9 인용" in str(deps.lead_llm.calls[0])


def _verdict(ids, caveats=()):
    return Verdict(verdict_type="stale_data", confidence="high", narrative="n",
                   root_cause=CauseLink(component="plan-sync", evidence_ids=list(ids)),
                   caveats=list(caveats))


async def test_없는_id_인용은_재작성_경로():
    deps = _deps([])
    state = _state(verdict=_verdict(["ev-9"]))
    update = await make_nodes(deps)["verify"](state)
    assert update["verify_attempts"] == 1 and update["verify_problems"]
    assert route_after_verify(state.model_copy(update=update)) == "conclude"


async def test_재실패는_강등_통과():
    deps = _deps([])
    state = _state(verdict=_verdict(["ev-9"]), verify_attempts=1)
    update = await make_nodes(deps)["verify"](state)
    assert update["verify_problems"] == []
    assert update["verdict"].confidence == "low"
    assert any("검증 미통과" in c for c in update["verdict"].caveats)


async def test_불완전_증거는_caveat_명시를_요구한다():
    deps = _deps([])
    eid = deps.store.put_evidence("c-1", "kafka:edge.raw", [1, 2], complete=False)
    ok_ref = EvidenceRef(id=eid, source="kafka:edge.raw", summary="s", complete=False)
    bad = _state(evidence=[ok_ref], verdict=_verdict([eid]))
    update = await make_nodes(deps)["verify"](bad)
    assert update["verify_problems"]                       # caveat에 없음 → 문제

    good = _state(evidence=[ok_ref], verdict=_verdict([eid], caveats=[f"불완전 증거 {eid} 기반"]))
    update2 = await make_nodes(deps)["verify"](good)
    assert update2["verify_problems"] == []


async def test_불완전_증거_caveat은_토큰_경계로_매칭된다():
    deps = _deps([])
    for n in range(10):
        deps.store.put_evidence("c-1", "kafka:edge.raw", [n], complete=(n != 0))
    ref1 = EvidenceRef(id="ev-1", source="kafka:edge.raw", summary="s", complete=False)
    # caveat이 ev-10만 언급 — ev-1 미명시로 판정되어야 한다
    state = _state(evidence=[ref1], verdict=_verdict(["ev-1"], caveats=["불완전 증거 ev-10 기반"]))
    update = await make_nodes(deps)["verify"](state)
    assert update["verify_problems"]


async def test_verdict_없이_verify에_들어와도_raise하지_않는다():
    deps = _deps([])
    update = await make_nodes(deps)["verify"](_state())
    assert update["verify_problems"] == []


async def test_store에만_있고_state_evidence에_없는_id_인용은_문제로_잡힌다():
    # error 태스크가 Store에 남긴 고아 본문(§2.4 인계 노트 2)처럼, store엔 있지만
    # state.evidence엔 없는 id를 store.has_evidence로 검사하면 통과해버렸다(I3) —
    # 인용 가능 우주는 state.evidence로 한정해야 한다.
    deps = _deps([])
    orphan_id = deps.store.put_evidence("c-1", "mongo:twin_state", {"v": 1})
    state = _state(evidence=[], verdict=_verdict([orphan_id]))
    update = await make_nodes(deps)["verify"](state)
    assert update["verify_problems"]
    assert any(orphan_id in p for p in update["verify_problems"])


# ---- 계획 14: 다중 RCA 후보 ---------------------------------------------------------------
_VERDICT_WITH_ALTS = (
    '{"verdict_type": "stale_data", "confidence": "high", "narrative": "n", '
    '"root_cause": {"component": "plan-sync", "evidence_ids": ["ev-1"]}, '
    '"alternates": [{"component": "plan-sync", "evidence_ids": ["ev-1"], "confidence": "low"}, '
    '{"component": "twin-state", "evidence_ids": ["ev-1"], "confidence": "low", "relation": "갱신 지연"}]}')


def test_후보는_코드가_상한과_중복을_쥔다():
    # 규율 4·6: LLM이 5개를 내도 3개, 최상위와 같은 컴포넌트·서로 같은 컴포넌트는 버린다.
    # 조용히 버리지 않는다 — caveat에 남긴다.
    from src.application.nodes import _sanitize_causes
    v = Verdict(verdict_type="stale_data", confidence="high", narrative="n",
                root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]),
                alternates=[CauseLink(component=c, evidence_ids=["ev-1"])
                            for c in ["plan-sync", "a", "a", "b", "c", "d"]])
    out = _sanitize_causes(v)
    assert [a.component for a in out.alternates] == ["a", "b", "c"]
    assert any("후보" in cv and "plan-sync" in cv and "d" in cv for cv in out.caveats)
    assert _sanitize_causes(out) is out            # 버릴 것이 없으면 그대로


async def test_conclude는_후보를_소독해서_State에_올린다():
    deps = _deps([_VERDICT_WITH_ALTS])
    state = _state(evidence=[EvidenceRef(id="ev-1", source="mongo:twin_state", summary="s")])
    update = await make_nodes(deps)["conclude"](state)
    assert [a.component for a in update["verdict"].alternates] == ["twin-state"]
    assert any("plan-sync" in c for c in update["verdict"].caveats)
    # 프롬프트가 후보를 묻는다 — 규칙과 예시 둘 다.
    assert "alternates" in str(deps.lead_llm.calls[0])


async def test_후보의_인용도_같은_우주로_검사한다():
    # 규율 3: 후보도 LLM이 인용한 id다. 최상위·기여 요인은 깨끗하고 후보만 더러운 형태 —
    # 후보를 검사하지 않으면 통과해 §2에 환각 id가 나간다.
    deps = _deps([])
    state = _state(evidence=[EvidenceRef(id="ev-1", source="mongo:twin_state", summary="s")],
                   verdict=Verdict(verdict_type="stale_data", confidence="high", narrative="n",
                                   root_cause=CauseLink(component="plan-sync", evidence_ids=["ev-1"]),
                                   alternates=[CauseLink(component="twin-state", evidence_ids=["ev-9"])]))
    update = await make_nodes(deps)["verify"](state)
    assert any("ev-9" in p for p in update["verify_problems"])


def test_후보_소독은_결론_없는_판정에도_적용된다():
    # 리뷰 A8: inconclusive + 후보들은 계획이 명시한 형태인데 그 경우의 상한·중복 제거를
    # 아무 테스트도 안 봤다.
    from src.application.nodes import _sanitize_causes
    v = Verdict(verdict_type="inconclusive", confidence="low", narrative="n",
                alternates=[CauseLink(component=c, evidence_ids=["ev-1"]) for c in ["a", "a", "b", "c", "d"]])
    assert [a.component for a in _sanitize_causes(v).alternates] == ["a", "b", "c"]


def test_후보_소독은_빈_컴포넌트와_긴_relation과_최상위의_신뢰도를_정리한다():
    # 리뷰 L2(규율 3·4): ""와 "   "가 서로 다른 후보로 살아남고, relation 5,000자가 §2로
    # 나가고, LLM이 채운 root_cause.confidence가 판정의 confidence와 나란히 API로 나갔다.
    from src.application.nodes import MAX_RELATION_CHARS, _sanitize_causes
    v = Verdict(verdict_type="stale_data", confidence="high", narrative="n",
                root_cause=CauseLink(component=" plan-sync ", evidence_ids=["ev-1"], confidence="low"),
                contributing=[CauseLink(component="x", evidence_ids=["ev-1"], confidence="high")],
                alternates=[CauseLink(component="", evidence_ids=[]),
                            CauseLink(component="   ", evidence_ids=["ev-1"]),
                            CauseLink(component=" twin-state ", evidence_ids=["ev-1"], relation="r" * 5000)])
    out = _sanitize_causes(v)
    assert [a.component for a in out.alternates] == ["twin-state"]
    assert len(out.alternates[0].relation) == MAX_RELATION_CHARS and out.alternates[0].relation.endswith("…")
    assert out.root_cause.component == "plan-sync" and out.root_cause.confidence is None
    assert out.contributing[0].confidence is None
    assert any("빈 컴포넌트" in c for c in out.caveats)


async def test_conclude_프롬프트는_후보_규칙을_싣는다():
    # 리뷰 A7: 예시 JSON에 "alternates"가 있어 규칙 줄을 지워도 통과했다.
    deps = _deps([VERDICT_JSON])
    state = _state(evidence=[EvidenceRef(id="ev-1", source="mongo:twin_state", summary="s")])
    await make_nodes(deps)["conclude"](state)
    assert "유력한 순" in str(deps.lead_llm.calls[0])


async def test_conclude는_결론_없는_판정의_후보도_소독한다():
    # 리뷰 A8: 함수 직접 호출 테스트는 노드 배선을 보증하지 않는다.
    deps = _deps(['{"verdict_type": "inconclusive", "confidence": "low", "narrative": "n", '
                  '"alternates": [{"component": "a", "evidence_ids": ["ev-1"]}, '
                  '{"component": "a", "evidence_ids": ["ev-1"]}, {"component": "", "evidence_ids": []}]}'])
    state = _state(evidence=[EvidenceRef(id="ev-1", source="mongo:twin_state", summary="s")])
    update = await make_nodes(deps)["conclude"](state)
    assert [a.component for a in update["verdict"].alternates] == ["a"]
    assert any("빈 컴포넌트 ×1" in c and "a" in c for c in update["verdict"].caveats)


def test_relation_상한은_300자다():
    # 리뷰 A12: 상수를 상징적으로 쓰면 값이 바뀌어도 모른다 — 계획서가 300을 명시한다.
    from src.application.nodes import MAX_ALTERNATES, MAX_RELATION_CHARS
    assert (MAX_RELATION_CHARS, MAX_ALTERNATES) == (300, 3)
