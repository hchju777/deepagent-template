"""접수를 턴 단위로 — 프로세스가 죽어도 문답이 남아야 한다.

지금 `intake()`는 `ask` 콜백으로 프로세스 안에서 되묻고 문답을 마지막에 한 번
돌려준다. 그 사이에 클라이언트가 끊기거나 서버가 재시작되면 전부 사라진다.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from src.application.intake import IntakeTurn, intake_turn
from src.application.open_case import open_case
from src.domain.cases import InMemoryCaseRepository
from src.domain.store import InMemoryCaseStore
from src.infrastructure.llm import ScriptedLLM
from src.knowledge.topology import Topology

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)
TOPO = Topology.model_validate({
    "services": {"twin-api": {"writes": [{"kind": "rest", "endpoint": "/oee"}]}},
    "derivations": {"rest:/oee": {"inputs": [{"kind": "mongo", "collection": "twin_state"}],
                                  "via": "twin-api"}}})
_RESOLVED = '{"target_locator": "rest:/oee", "missing": []}'
_MISSING = '{"target_locator": null, "missing": ["어느 라인인가?"]}'


def _deps(*responses):
    return SimpleNamespace(lead_llm=ScriptedLLM(list(responses)))


def _case():
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    record = open_case(repo=repo, store=store, symptom="OEE가 이상하다", gbm="mx", fct="gumi",
                       concern="system", requested_by=None, clock=lambda: T,
                       on_event=lambda e: None)
    return record.id, repo, store


async def _turn(case_id, repo, store, deps, **kw):
    return await intake_turn(case_id, repo=repo, store=store, deps=deps, topology=TOPO,
                             clock=lambda: T, **kw)


async def test_한_번에_끝나면_대상이_레코드에_들어간다():
    case_id, repo, store = _case()
    turn = await _turn(case_id, repo, store, _deps(_RESOLVED))
    assert turn.status == "done" and turn.target_locator == "rest:/oee"
    record = repo.get(case_id)
    assert record.target_locator == "rest:/oee"
    assert record.status == "open" and record.question_kind is None


async def test_되물을_것이_있으면_파킹하고_질문을_남긴다():
    case_id, repo, store = _case()
    turn = await _turn(case_id, repo, store, _deps(_MISSING))
    assert turn.status == "asking" and "라인" in turn.question
    record = repo.get(case_id)
    assert record.status == "awaiting_human" and record.question_kind == "intake"
    assert record.question == turn.question


async def test_답은_이어가기_전에_먼저_박제된다():
    # 박제를 뒤로 미루면 그 사이 프로세스가 죽었을 때 사람의 답이 사라진다 —
    # 계획 4b의 F3 경로가 human:answer에서 같은 판단을 했다.
    case_id, repo, store = _case()
    await _turn(case_id, repo, store, _deps(_MISSING))
    await _turn(case_id, repo, store, _deps("파싱 불가"), answer="라인 7이다")
    bodies = [repr(store.get_evidence(case_id, r.id)) for r in store.list_evidence(case_id)]
    assert any("라인 7이다" in b for b in bodies)


async def test_답을_들고_이어가면_접수가_끝난다():
    case_id, repo, store = _case()
    await _turn(case_id, repo, store, _deps(_MISSING))
    turn = await _turn(case_id, repo, store, _deps(_RESOLVED), answer="라인 7")
    assert turn.status == "done"
    record = repo.get(case_id)
    assert record.status == "open" and record.question_kind is None and record.question is None
    assert record.target_locator == "rest:/oee"


async def test_LLM_실패는_error이지_예외가_아니다():
    case_id, repo, store = _case()
    turn = await _turn(case_id, repo, store, _deps("파싱 불가"))
    assert turn.status == "error" and turn.problems
    # 고아 상태로 남지 않는다 — 대상 없이도 조사에 들어갈 수 있다.
    assert repo.get(case_id).status == "open"


async def test_턴_상한을_코드가_쥔다():
    # 턴으로 바꾸면 호출자가 무한히 부를 수 있다. 상한은 config가 정하고 코드가
    # 강제한다(규율 6) — 넘으면 대상 없이 조사에 들어간다(기존 이중 실패 착지점).
    case_id, repo, store = _case()
    for _ in range(2):
        assert (await _turn(case_id, repo, store, _deps(_MISSING),
                            max_turns=2)).status == "asking"
    turn = await _turn(case_id, repo, store, _deps(_MISSING), max_turns=2, answer="답")
    assert turn.status == "error" and any("상한" in p for p in turn.problems)
    assert repo.get(case_id).status == "open" and repo.get(case_id).question_kind is None


async def test_조사_질문에_파킹된_케이스는_건드리지_않는다():
    # 그래프가 파킹한 케이스에 접수를 이어가면 스레드를 잃는다.
    case_id, repo, store = _case()
    repo.save(repo.get(case_id).model_copy(update={
        "status": "awaiting_human", "question": "계획 변경이 있었나?",
        "question_kind": "investigation"}))
    turn = await _turn(case_id, repo, store, _deps(_RESOLVED), answer="없다")
    assert turn.status == "error" and any("조사 질문" in p for p in turn.problems)
    assert repo.get(case_id).status == "awaiting_human"


async def test_닫힌_케이스는_거부한다():
    case_id, repo, store = _case()
    repo.save(repo.get(case_id).model_copy(update={"status": "closed"}))
    turn = await _turn(case_id, repo, store, _deps(_RESOLVED))
    assert turn.status == "error"


async def test_조사_중인_케이스는_접수가_건드리지_않는다():
    # 데몬의 requeue_open이 접수 중인(open) 케이스를 집어 워커가 investigating으로
    # 전이하고 스레드를 배정한 뒤, 접수가 **턴 시작 시 읽은 낡은 레코드**를 통째로
    # 저장하면 lease·상태·thread_ids가 전부 되돌아간다 — 같은 케이스에 조사 둘,
    # 그리고 등록 안 된 체크포인트가 close_case(discard_threads)에 안 걸려 영구 잔존.
    case_id, repo, store = _case()
    repo.save(repo.get(case_id).model_copy(update={
        "status": "investigating", "owner": "w-1", "thread_ids": ["t-1"]}))
    turn = await _turn(case_id, repo, store, _deps(_RESOLVED))
    assert turn.status == "error" and any("조사 중" in p for p in turn.problems)
    after = repo.get(case_id)
    assert after.status == "investigating" and after.owner == "w-1"
    assert after.thread_ids == ["t-1"]


async def test_턴_도중_바뀐_필드를_덮어쓰지_않는다():
    # 워커 모듈의 I1(read-modify-write) 규율 — 턴 시작 시 들고 있던 스냅샷을
    # wholesale 저장하면 그 사이의 동시 갱신을 잃는다.
    case_id, repo, store = _case()

    class _Racing:
        """LLM 호출 도중 다른 경로가 같은 레코드를 갱신하는 상황."""
        async def ainvoke(self, messages):
            repo.save(repo.get(case_id).model_copy(update={"finding_ids": ["f-9"]}))
            return SimpleNamespace(content=_RESOLVED)

    turn = await _turn(case_id, repo, store, SimpleNamespace(lead_llm=_Racing()))
    assert turn.status == "done"
    after = repo.get(case_id)
    assert after.finding_ids == ["f-9"]                    # 잃지 않았다
    assert after.target_locator == "rest:/oee"             # 우리 필드는 들어갔다


async def test_레거시_파킹_레코드를_접수가_언파킹하지_않는다():
    # question_kind가 None인 것은 계획 12 이전에 **그래프가** 파킹한 레코드다.
    # 가드가 "investigation이 아니면 통과"면 그것들이 전부 새어 들어와 스레드를 잃는다.
    case_id, repo, store = _case()
    repo.save(repo.get(case_id).model_copy(update={
        "status": "awaiting_human", "question": "계획 변경이 있었나?",
        "thread_ids": ["t-1"]}))                           # question_kind는 None
    turn = await _turn(case_id, repo, store, _deps(_RESOLVED), answer="없다")
    assert turn.status == "error"
    after = repo.get(case_id)
    assert after.status == "awaiting_human" and after.question is not None
