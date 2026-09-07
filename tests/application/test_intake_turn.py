"""접수를 턴 단위로 — 프로세스가 죽어도 문답이 남아야 한다.

지금 `intake()`는 `ask` 콜백으로 프로세스 안에서 되묻고 문답을 마지막에 한 번
돌려준다. 그 사이에 클라이언트가 끊기거나 서버가 재시작되면 전부 사라진다.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest

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
    assert turn.status == "not_ours" and any("조사 질문" in p for p in turn.problems)
    assert repo.get(case_id).status == "awaiting_human"


async def test_닫힌_케이스는_거부한다():
    # not_ours다 — 호출부가 취할 행동이 "아무것도 하지 마라"로 같다. 닫힌 케이스에
    # run_once를 걸면 워커가 "stale"로 막긴 하지만 lease를 한 번 잡았다 놓는다.
    case_id, repo, store = _case()
    repo.save(repo.get(case_id).model_copy(update={"status": "closed"}))
    turn = await _turn(case_id, repo, store, _deps(_RESOLVED))
    assert turn.status == "not_ours"


async def test_조사_중인_케이스는_접수가_건드리지_않는다():
    # 데몬의 requeue_open이 접수 중인(open) 케이스를 집어 워커가 investigating으로
    # 전이하고 스레드를 배정한 뒤, 접수가 **턴 시작 시 읽은 낡은 레코드**를 통째로
    # 저장하면 lease·상태·thread_ids가 전부 되돌아간다 — 같은 케이스에 조사 둘,
    # 그리고 등록 안 된 체크포인트가 close_case(discard_threads)에 안 걸려 영구 잔존.
    case_id, repo, store = _case()
    repo.save(repo.get(case_id).model_copy(update={
        "status": "investigating", "owner": "w-1", "thread_ids": ["t-1"]}))
    turn = await _turn(case_id, repo, store, _deps(_RESOLVED))
    assert turn.status == "not_ours" and any("조사 중" in p for p in turn.problems)
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
    assert turn.status == "not_ours"
    after = repo.get(case_id)
    assert after.status == "awaiting_human" and after.question is not None


class _ClaimsDuringCall:
    """LLM 호출 도중 워커가 케이스를 가로채는 상황을 만든다."""

    def __init__(self, repo, case_id, update, reply=_RESOLVED):
        self._repo, self._case_id, self._update, self._reply = repo, case_id, update, reply

    async def ainvoke(self, messages):
        self._repo.save(self._repo.get(self._case_id).model_copy(update=self._update))
        return SimpleNamespace(content=self._reply)


async def test_턴_도중_그래프가_파킹하면_그것을_지우지_않는다():
    # 가드를 턴 시작에만 걸면, LLM 호출 동안 워커가 claim하고 그래프가 파킹한
    # 케이스를 접수가 open으로 되돌려 **사람에게 물은 질문이 소멸한다.**
    # 그러면 requeue_open이 다시 집어 같은 케이스에 조사가 둘 붙는다.
    case_id, repo, store = _case()
    llm = _ClaimsDuringCall(repo, case_id, {
        "status": "awaiting_human", "question": "계획 변경이 있었나?",
        "question_kind": "investigation", "owner": "w-1", "thread_ids": ["t-1"]})
    turn = await _turn(case_id, repo, store, SimpleNamespace(lead_llm=llm))
    assert turn.status == "not_ours"
    after = repo.get(case_id)
    assert after.status == "awaiting_human" and after.question_kind == "investigation"
    assert after.question == "계획 변경이 있었나?" and after.owner == "w-1"


async def test_턴_도중_워커가_claim하면_파킹하지_않는다():
    # investigating 레코드를 접수가 awaiting_human으로 파킹하면, 워커의 _finish가
    # awaiting_human→awaiting_human 전이에서 LifecycleError로 터져 케이스가
    # 강제 종결된다 — 재읽기를 넣으면서 새로 생긴 실패 모양이다.
    case_id, repo, store = _case()
    llm = _ClaimsDuringCall(repo, case_id, {"status": "investigating", "owner": "w-1",
                                            "thread_ids": ["t-1"]}, reply=_MISSING)
    turn = await _turn(case_id, repo, store, SimpleNamespace(lead_llm=llm))
    assert turn.status == "not_ours"
    after = repo.get(case_id)
    assert after.status == "investigating" and after.question_kind is None


async def test_턴_도중_가로채이면_포기_경로도_저장하지_않는다():
    case_id, repo, store = _case()
    llm = _ClaimsDuringCall(repo, case_id, {"status": "investigating", "owner": "w-1"},
                            reply="파싱 불가")
    turn = await _turn(case_id, repo, store, SimpleNamespace(lead_llm=llm))
    assert turn.status == "not_ours"
    assert repo.get(case_id).status == "investigating"


async def test_가로채인_케이스는_error가_아니라_not_ours다():
    # "포기했다"(케이스는 조사 가능)와 "못 만졌다"(남이 들고 있다)는 호출부가
    # 다르게 다뤄야 한다. 둘을 error로 뭉치면 호출부가 run_once를 걸고, 그래프가
    # 파킹한 케이스라면 새 조사가 처음부터 시작돼 스레드를 잃는다.
    case_id, repo, store = _case()
    repo.save(repo.get(case_id).model_copy(update={
        "status": "awaiting_human", "question": "계획 변경이 있었나?",
        "question_kind": "investigation", "thread_ids": ["t-1"]}))
    turn = await _turn(case_id, repo, store, _deps(_RESOLVED), answer="없다")
    assert turn.status == "not_ours"


async def test_턴_시작_가드가_남의_케이스에_아무것도_안_쓴다():
    # 가드가 없으면 LLM을 부르고 그 결과를 남의 케이스 store에 박제한다.
    case_id, repo, store = _case()
    before = len(store.list_evidence(case_id))
    repo.save(repo.get(case_id).model_copy(update={"status": "investigating",
                                                   "owner": "w-1"}))
    calls = []

    class _Spy:
        async def ainvoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content=_RESOLVED)

    turn = await _turn(case_id, repo, store, SimpleNamespace(lead_llm=_Spy()), answer="답")
    assert turn.status == "not_ours"
    assert calls == []                                   # LLM을 안 불렀다
    assert len(store.list_evidence(case_id)) == before    # 증거도 안 썼다


async def test_접수가_끝나면_intake_done이_True다():
    case_id, repo, store = _case()
    repo.save(repo.get(case_id).model_copy(update={"intake_done": False}))
    turn = await _turn(case_id, repo, store, _deps(_RESOLVED))
    assert turn.status == "done" and repo.get(case_id).intake_done is True


async def test_접수를_포기해도_intake_done이_True다():
    # 포기는 "대상 없이 조사한다"는 뜻이다 — 문을 안 열면 그 케이스는 영영 안 집힌다.
    case_id, repo, store = _case()
    repo.save(repo.get(case_id).model_copy(update={"intake_done": False}))
    for _ in range(3):
        await _turn(case_id, repo, store, _deps("파싱 불가"), max_turns=3)
    assert repo.get(case_id).intake_done is True


# ---- 계획 15(P8): chat 지문 정정 -----------------------------------------------------------
async def test_접수가_끝나면_지문이_대상_기준으로_다시_계산된다():
    # 계획 12가 남긴 알려진 결함: fingerprint(gbm, fct, "chat", case_id)라 사람이 연
    # 케이스는 서로 절대 같은 지문을 갖지 않는다. 이대로 tier 1 이력 검색을 얹으면
    # human 케이스는 영원히 안 맞는다.
    from src.domain.patrol import fingerprint
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()      # 같은 repo — id가 갈린다
    ids = []
    for _ in range(2):
        record = open_case(repo=repo, store=store, symptom="OEE가 이상하다", gbm="mx", fct="gumi",
                           concern="system", requested_by=None, clock=lambda: T,
                           on_event=lambda e: None)
        assert (await _turn(record.id, repo, store, _deps(_RESOLVED))).status == "done"
        ids.append(record.id)
    assert len(set(ids)) == 2                                        # 서로 다른 케이스다
    fps = {repo.get(cid).fingerprint for cid in ids}
    assert fps == {fingerprint("mx", "gumi", "chat", "rest:/oee")}    # 같은 대상 → 같은 지문


async def test_대상을_못_정한_접수는_지문을_그대로_둔다():
    # _give_up 경로 — locator가 없으면 재계산의 재료가 없다. case_id 지문이 남는다.
    case_id, repo, store = _case()
    before = repo.get(case_id).fingerprint
    turn = await _turn(case_id, repo, store, _deps(_MISSING), max_turns=0)
    assert turn.status in ("error", "asking")
    assert repo.get(case_id).fingerprint == before


# ---- 계획 17: 접수 저장의 CAS -------------------------------------------------------------
async def test_동시에_온_두_접수_턴은_하나만_이긴다():
    # 계획 13 리뷰 S3이 실증한 형태: 증거가 중복되고, `awaiting_human`인데
    # `intake_done=True`이고 대상까지 설정된 모순 레코드가 남았다.
    case_id, repo, store = _case()
    seen = []
    real_save = repo.save

    class _Racing:
        """LLM이 도는 사이 남이 저장한다 — 읽기와 쓰기 사이의 창을 정확히 재현한다."""
        def __init__(self):
            self.fired = False

        async def ainvoke(self, messages):
            if not self.fired:
                self.fired = True
                current = repo.get(case_id)
                real_save(current.model_copy(update={"question": "남이 바꿈",
                                                     "updated_at": T + timedelta(minutes=1)}))
            return SimpleNamespace(content=_RESOLVED)

    turn = await _turn(case_id, repo, store, SimpleNamespace(lead_llm=_Racing()))
    assert turn.status == "not_ours", turn
    after = repo.get(case_id)
    assert after.question == "남이 바꿈" and after.intake_done is False
    assert after.target_locator is None          # 모순 레코드가 남지 않는다


async def test_아무도_끼어들지_않으면_예전처럼_끝난다():
    case_id, repo, store = _case()
    turn = await _turn(case_id, repo, store, _deps(_RESOLVED))
    assert turn.status == "done" and repo.get(case_id).intake_done is True


async def test_되묻는_경로도_같은_술어로_보호된다():
    # 파킹(_park)도 접수가 소유한 필드를 쓴다 — 여기만 CAS가 빠지면 되묻기 턴에서
    # 같은 모순 레코드가 생긴다.
    case_id, repo, store = _case()
    real_save = repo.save

    class _Racing:
        def __init__(self):
            self.fired = False

        async def ainvoke(self, messages):
            if not self.fired:
                self.fired = True
                current = repo.get(case_id)
                real_save(current.model_copy(update={"question": "남이 바꿈",
                                                     "updated_at": T + timedelta(minutes=1)}))
            return SimpleNamespace(content=_MISSING)

    turn = await _turn(case_id, repo, store, SimpleNamespace(lead_llm=_Racing()))
    assert turn.status == "not_ours", turn
    assert repo.get(case_id).question == "남이 바꿈"


# ---- 검증 리뷰 B1: Mongo 백엔드로도 접수가 돈다 -------------------------------------------
@pytest.fixture
def mongo_repo():
    import mongomock
    from src.infrastructure.mongo_store import MongoCaseRepository
    return MongoCaseRepository(mongomock.MongoClient()["intake_test"])


async def test_Mongo_백엔드에서도_접수가_완주한다(mongo_repo):
    # 인메모리만 도는 테스트는 프로덕션 쓰기 경로(model_dump)를 한 번도 안 지난다.
    store = InMemoryCaseStore()
    record = open_case(repo=mongo_repo, store=store, symptom="OEE가 이상하다", gbm="mx",
                       fct="gumi", concern="system", requested_by=None, clock=lambda: T,
                       on_event=lambda e: None)
    turn = await _turn(record.id, mongo_repo, store, _deps(_RESOLVED))
    assert turn.status == "done", turn
    after = mongo_repo.get(record.id)
    assert after.intake_done is True and after.target_locator == "rest:/oee"
    assert after.status == "open"


async def test_Mongo_백엔드에서도_되묻기가_된다(mongo_repo):
    store = InMemoryCaseStore()
    record = open_case(repo=mongo_repo, store=store, symptom="s", gbm="mx", fct="gumi",
                       concern="system", requested_by=None, clock=lambda: T,
                       on_event=lambda e: None)
    turn = await _turn(record.id, mongo_repo, store, _deps(_MISSING))
    assert turn.status == "asking", turn
    after = mongo_repo.get(record.id)
    assert after.status == "awaiting_human" and after.question == "어느 라인인가?"


async def test_고정_시계에서도_두_턴이_모두_이기지_않는다():
    # 검증 리뷰 M-5: 술어가 updated_at 하나면 이긴 턴이 쓴 값이 진 턴이 읽은 값과 같아
    # 둘 다 이긴다. 이 리포의 시계는 항상 고정값이므로(규율 2) 그 조건이 상시다.
    # A가 사람에게 물어 파킹하는 동안 B가 완료로 덮으면 질문이 답도 못 받고 사라진다.
    case_id, repo, store = _case()

    class _Racing:
        """B의 LLM이 도는 사이 A(되묻기 턴)가 통째로 끝난다 — 인위적 시각 조작 없이."""
        def __init__(self):
            self.fired = False

        async def ainvoke(self, messages):
            if not self.fired:
                self.fired = True
                await intake_turn(case_id, repo=repo, store=store, deps=_deps(_MISSING),
                                  topology=TOPO, clock=lambda: T)
            return SimpleNamespace(content=_RESOLVED)

    turn = await _turn(case_id, repo, store, SimpleNamespace(lead_llm=_Racing()))
    after = repo.get(case_id)
    assert turn.status == "not_ours", turn
    # A가 물은 질문이 살아 있다 — B가 덮지 않았다.
    assert after.status == "awaiting_human" and after.question == "어느 라인인가?"
    assert after.intake_done is False and after.target_locator is None


async def test_접수가_소유하지_않은_필드가_바뀌어도_진다():
    # 술어의 시각이 그것을 잡는다 — 게이트가 finding을 붙이는 등 남의 쓰기가 있었으면
    # 이 턴이 읽은 스냅샷은 이미 낡았다.
    case_id, repo, store = _case()

    class _Racing:
        def __init__(self):
            self.fired = False

        async def ainvoke(self, messages):
            if not self.fired:
                self.fired = True
                current = repo.get(case_id)
                repo.save(current.model_copy(update={
                    "finding_ids": ["f-1"], "updated_at": T + timedelta(minutes=1)}))
            return SimpleNamespace(content=_RESOLVED)

    turn = await _turn(case_id, repo, store, SimpleNamespace(lead_llm=_Racing()))
    assert turn.status == "not_ours", turn
    assert repo.get(case_id).finding_ids == ["f-1"]      # 남의 쓰기가 살아 있다


async def test_접수_질문도_번호를_대조한다():
    # 검증 리뷰 N-1: 이 웨이브의 회귀. 접수 분기가 expect_seq를 통째로 버려, Q1을 보고
    # 쓴 답이 Q2의 답으로 박제되고 접수가 그대로 완주했다.
    case_id, repo, store = _case()
    assert (await _turn(case_id, repo, store, _deps(_MISSING))).status == "asking"
    parked = repo.get(case_id)
    assert parked.question_seq == 1
    turn = await _turn(case_id, repo, store, _deps(_RESOLVED), answer="Q1의 답", expect_seq=99)
    assert turn.status == "stale_question", turn
    after = repo.get(case_id)
    assert after.status == "awaiting_human" and after.intake_done is False
    assert [r for r in store.list_evidence(case_id) if r.source == "human:answer"] == []


async def test_번호가_맞으면_접수는_그대로_이어진다():
    case_id, repo, store = _case()
    assert (await _turn(case_id, repo, store, _deps(_MISSING))).status == "asking"
    turn = await _turn(case_id, repo, store, _deps(_RESOLVED), answer="라인 7", expect_seq=1)
    assert turn.status == "done" and repo.get(case_id).intake_done is True


async def test_같은_문구로_다시_묻는_두_턴이_모두_이기지_않는다():
    # 검증 리뷰 N-4: 질문 문구가 같으면 소유 필드가 하나도 안 바뀌어 술어가 통과했다.
    # 유령 파킹이 번호를 부풀리면 #1을 읽고 답한 사람이 거짓 stale_question을 받는다.
    case_id, repo, store = _case()
    assert (await _turn(case_id, repo, store, _deps(_MISSING))).status == "asking"

    class _Racing:
        def __init__(self):
            self.fired = False

        async def ainvoke(self, messages):
            if not self.fired:
                self.fired = True
                await intake_turn(case_id, repo=repo, store=store, deps=_deps(_MISSING),
                                  topology=TOPO, clock=lambda: T, answer="답")
            return SimpleNamespace(content=_MISSING)

    turn = await _turn(case_id, repo, store, SimpleNamespace(lead_llm=_Racing()), answer="답")
    assert turn.status == "not_ours", turn
    assert repo.get(case_id).question_seq == 2      # 1→2, 3으로 뛰지 않는다


async def test_언파킹은_전이_시각을_찍는다():
    # 검증 리뷰 M16: `_SAVED_FIELDS`에서 status_since를 빼도 초록이었다.
    case_id, repo, store = _case()
    assert (await _turn(case_id, repo, store, _deps(_MISSING))).status == "asking"
    parked_since = repo.get(case_id).status_since
    later = T + timedelta(hours=1)
    turn = await intake_turn(case_id, repo=repo, store=store, deps=_deps(_RESOLVED),
                             topology=TOPO, clock=lambda: later, answer="라인 7")
    assert turn.status == "done"
    after = repo.get(case_id)
    assert after.status == "open" and after.status_since == later != parked_since
