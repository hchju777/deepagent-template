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
    return CaseRecord(id=cid, gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                      created_at=T, updated_at=T, status="awaiting_human", question="q",
                      question_kind="investigation", **kw)


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
