"""명령 채널 — 답을 레코드에 싣는다. 실행은 워커의 몫이다(스펙 §3.6).

v1 인계 노트: "데몬이 파킹 케이스를 자동으로 재개하려면 사람의 답을 실어 나를
프로세스 밖 명령 채널이 필요한데 그것이 아직 없다." 이것이 그 채널이다 — 새 큐나
컬렉션이 아니라 레코드 위의 필드 둘이다.
"""
from datetime import datetime, timezone

from src.application.submit import submit_answer
from src.domain.cases import CaseRecord, InMemoryCaseRepository

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _parked(cid="c-1", **kw):
    base = dict(id=cid, gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                created_at=T, updated_at=T, status="awaiting_human", question="q",
                question_kind="investigation", question_seq=1)
    base.update(kw)
    return CaseRecord(**base)


def test_답은_레코드에_실리고_실행되지_않는다():
    # api는 실행자가 아니다 — 기록만 하고 워커가 집어 간다.
    repo = InMemoryCaseRepository(); repo.save(_parked())
    assert submit_answer("c-1", "답", key="k-1", repo=repo, clock=lambda: T) == "accepted"
    record = repo.get("c-1")
    assert record.pending_answer == "답" and record.answer_key == "k-1"
    assert record.status == "awaiting_human"          # 상태 전이는 워커의 몫


def test_같은_키는_두_번_싣지_않는다():
    # 클라이언트 재시도가 답을 두 번 넣으면 F3 복구가 두 번 돈다.
    repo = InMemoryCaseRepository(); repo.save(_parked())
    submit_answer("c-1", "답", key="k-1", repo=repo, clock=lambda: T)
    assert submit_answer("c-1", "다른 답", key="k-1", repo=repo, clock=lambda: T) == "duplicate"
    assert repo.get("c-1").pending_answer == "답"


def test_소비된_뒤_같은_키가_또_오면_여전히_duplicate다():
    # 워커가 pending_answer를 지워도 answer_key는 남긴다 — 멱등의 근거다.
    repo = InMemoryCaseRepository()
    repo.save(_parked(answer_key="k-1"))              # 소비 후 상태
    assert submit_answer("c-1", "답", key="k-1", repo=repo, clock=lambda: T) == "duplicate"


def test_새_키는_이전_답이_아직_있어도_덮지_않는다():
    # 아직 소비되지 않은 답이 있는데 다른 답이 오면 어느 쪽이 사람의 뜻인지 모른다.
    repo = InMemoryCaseRepository(); repo.save(_parked())
    submit_answer("c-1", "첫 답", key="k-1", repo=repo, clock=lambda: T)
    assert submit_answer("c-1", "둘째 답", key="k-2", repo=repo, clock=lambda: T) == "pending"
    assert repo.get("c-1").pending_answer == "첫 답"


def test_기다리지_않는_케이스에는_싣지_않는다():
    repo = InMemoryCaseRepository()
    repo.save(_parked().model_copy(update={"status": "investigating", "question": None}))
    assert submit_answer("c-1", "답", key="k", repo=repo, clock=lambda: T) == "not_waiting"
    assert repo.get("c-1").pending_answer is None


def test_없는_케이스는_not_found다():
    repo = InMemoryCaseRepository()
    assert submit_answer("없음", "답", key="k", repo=repo, clock=lambda: T) == "not_found"


def test_실린_시각이_남는다():
    # "답을 넣었는데 아무 일도 안 난다"를 진단하려면 언제 실렸는지가 있어야 한다.
    repo = InMemoryCaseRepository(); repo.save(_parked())
    submit_answer("c-1", "답", key="k-1", repo=repo, clock=lambda: T)
    assert repo.get("c-1").updated_at == T


# ── submit_case: 스코프 → 접근 → 개설 → 첫 접수 턴 (CLI와 API가 공유) ──────────
from types import SimpleNamespace                                  # noqa: E402

from src.application.submit import submit_case                     # noqa: E402
from src.config.schema_app import AccessPolicy                     # noqa: E402
from src.domain.store import InMemoryCaseStore                     # noqa: E402
from src.knowledge.topology import Topology                        # noqa: E402

_TOPO = Topology.model_validate({
    "services": {"twin-api": {"writes": [{"kind": "rest", "endpoint": "/oee"}]}},
    "derivations": {}})


def _llm(reply):
    async def ainvoke(messages):
        return SimpleNamespace(content=reply)
    return SimpleNamespace(ainvoke=ainvoke)


def _sites(reply='{"target_locator": "rest:/oee", "missing": []}'):
    return {("mx", "gumi"): SimpleNamespace(gbm="mx", fct="gumi", topology=_TOPO,
                                             lead_llm=_llm(reply))}


async def test_케이스가_열리고_첫_턴까지_돈다():
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    out = await submit_case("OEE가 이상하다", gbm="mx", fct="gumi", concern="system",
                            subject=None, sites=_sites(), access=AccessPolicy(),
                            repo=repo, store=store, clock=lambda: T, on_event=lambda e: None,
                            max_intake_turns=3)
    assert out.status == "opened" and out.case_id
    assert repo.get(out.case_id).target_locator == "rest:/oee"
    assert out.turn.status == "done"


async def test_되물을_것이_있으면_질문이_함께_온다():
    # 첫 응답에 질문이 실려야 클라이언트가 폴링하지 않는다.
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    out = await submit_case("s", gbm="mx", fct="gumi", concern="system", subject=None,
                            sites=_sites('{"target_locator": null, "missing": ["어느 라인?"]}'),
                            access=AccessPolicy(), repo=repo, store=store, clock=lambda: T,
                            on_event=lambda e: None, max_intake_turns=3)
    assert out.status == "opened" and out.turn.status == "asking"
    assert out.turn.question and repo.get(out.case_id).status == "awaiting_human"


async def test_스코프_미확정은_케이스를_만들지_않는다():
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    sites = {**_sites(), ("mx", "suwon"): SimpleNamespace(
        gbm="mx", fct="suwon", topology=_TOPO, lead_llm=_llm('{"gbm": "없음", "fct": "없음"}'))}
    out = await submit_case("뭔가", gbm=None, fct=None, concern="system", subject=None,
                            sites=sites, access=AccessPolicy(), repo=repo, store=store,
                            clock=lambda: T, on_event=lambda e: None, max_intake_turns=3)
    assert out.status == "unresolved" and ("mx", "gumi") in out.scope.candidates
    assert repo.list_open() == []


async def test_접근_거부는_케이스를_만들지_않는다():
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    out = await submit_case("s", gbm="mx", fct="gumi", concern="system", subject="bob",
                            sites=_sites(), access=AccessPolicy(allow={"alice": ["mx/gumi"]}),
                            repo=repo, store=store, clock=lambda: T, on_event=lambda e: None,
                            max_intake_turns=3)
    assert out.status == "forbidden" and repo.list_open() == []


# ── 원자성 (계획 13 리뷰 블로커 1·2·3) ──────────────────────────────────────────
def test_싣기는_상태_lease_스레드를_건드리지_않는다():
    # 리뷰 S7: 워커가 pending을 지우고 claim(investigating)한 직후 api의 전체 레코드
    # save가 착지 → status가 awaiting_human으로 되돌아감 → 워커 _finish가
    # LifecycleError로 터져 케이스가 "워커 실패"로 종결. 싣기는 필드 셋만 조건부로.
    repo = InMemoryCaseRepository()
    repo.save(_parked(question_seq=1))
    stale = repo.get("c-1")                                  # api가 읽은 시점의 레코드
    repo.save(stale.model_copy(update={"status": "investigating", "owner": "w-1",
                                       "thread_ids": ["t-1"], "pending_answer": None}))
    # api가 stale 레코드로 싣기를 시도한다 — 조건(awaiting_human)이 깨졌으므로 거부
    assert submit_answer("c-1", "답", key="k-1", repo=repo, clock=lambda: T) == "not_waiting"
    after = repo.get("c-1")
    assert after.status == "investigating" and after.owner == "w-1" and after.thread_ids == ["t-1"]


def test_아직_파킹된_적_없는_질문에는_싣지_않는다():
    # question_seq(파킹 횟수) > answered_seq(답한 파킹)일 때만 "답할 질문이 있다".
    repo = InMemoryCaseRepository()
    repo.save(_parked(question_seq=0, answered_seq=0))
    assert submit_answer("c-1", "답", key="k", repo=repo, clock=lambda: T) == "not_waiting"


def test_워커가_가져간_뒤_다음_파킹_전에는_싣지_않는다():
    # 리뷰 S2-F: 워커가 pending을 지운 직후(아직 awaiting_human) 둘째 답이 accepted
    # → 그래프가 새 질문으로 다시 파킹하면 **옛 질문의 답이 새 질문에** 소비된다.
    repo = InMemoryCaseRepository()
    repo.save(_parked(question_seq=1))
    submit_answer("c-1", "첫 답", key="k-1", repo=repo, clock=lambda: T)
    taken = repo.take_answer("c-1", now=T)
    assert taken == "첫 답"
    assert repo.get("c-1").answered_seq == 1                # 이 질문은 답했다
    assert submit_answer("c-1", "둘째 답", key="k-2", repo=repo, clock=lambda: T) == "not_waiting"


def test_접수_질문에는_answers로_싣지_않는다():
    # 리뷰 S2-E: /answers가 접수 질문에도 실리면, /intake-answers가 접수를 끝낸 뒤
    # pending이 남아 워커가 접수 답을 조사 답(human:answer)으로 재소비한다.
    repo = InMemoryCaseRepository()
    repo.save(_parked(question_kind="intake", question_seq=1))
    assert submit_answer("c-1", "답", key="k", repo=repo, clock=lambda: T) == "not_waiting"


def test_되돌리기는_다음_파킹이_없을_때만_된다():
    # 소비 실패(busy/skipped/not_ours) 시 답을 되돌린다. 그 사이 그래프가 새 질문으로
    # 파킹했으면(question_seq가 올라감) 옛 답은 되돌리지 않는다 — 새 질문에 붙는다.
    repo = InMemoryCaseRepository()
    repo.save(_parked(question_seq=1))
    submit_answer("c-1", "답", key="k-1", repo=repo, clock=lambda: T)
    repo.take_answer("c-1", now=T)
    assert repo.restore_answer("c-1", answer="답", now=T) is True
    assert repo.get("c-1").pending_answer == "답" and repo.get("c-1").answered_seq == 0
    # 새 파킹 뒤에는 되돌리지 않는다
    repo.take_answer("c-1", now=T)
    repo.save(repo.get("c-1").model_copy(update={"question_seq": 2, "question": "새 질문"}))
    assert repo.restore_answer("c-1", answer="답", now=T) is False
    assert repo.get("c-1").pending_answer is None


def test_저장소_장애는_not_found가_아니라_error다():
    # 계획 13 인계 #11: attach_answer가 던지면 not_found(404)로 보였다 — 클라이언트는
    # "케이스가 없다"고 믿고 재시도하지 않는다. 장애는 장애라고 말한다.
    from datetime import datetime, timezone
    from src.application.submit import submit_answer

    class _Boom:
        def attach_answer(self, *a, **k):
            raise RuntimeError("mongo down")
    assert submit_answer("c-1", "a", key="k", repo=_Boom(),
                         clock=lambda: datetime(2026, 9, 3, tzinfo=timezone.utc)) == "error"


def test_다른_질문의_답은_stale_question이다():
    from src.domain.cases import CaseRecord, InMemoryCaseRepository
    repo = InMemoryCaseRepository()
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                         created_at=T, updated_at=T, status="awaiting_human", question="Q2",
                         question_kind="investigation", question_seq=2))
    assert submit_answer("c-1", "Q1의 답", key="k-1", repo=repo, clock=lambda: T,
                         expect_seq=1) == "stale_question"
    assert repo.get("c-1").pending_answer is None
    assert submit_answer("c-1", "Q2의 답", key="k-1", repo=repo, clock=lambda: T,
                         expect_seq=2) == "accepted"
