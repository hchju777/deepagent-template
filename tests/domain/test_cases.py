from datetime import datetime, timedelta

import pytest
from src.domain.cases import CaseRecord, InMemoryCaseRepository

T = datetime(2026, 9, 3, 8, 0)


def test_열린_케이스만_지문으로_찾는다():
    repo = InMemoryCaseRepository()
    cid = repo.new_case_id()
    assert cid == "c-1" and repo.new_case_id() == "c-2"
    repo.save(CaseRecord(id=cid, gbm="mx", fct="gumi", fingerprint="fp-a",
                         symptom="OEE 512%", t0=T, created_at=T, updated_at=T))
    assert repo.find_open_by_fingerprint("fp-a").id == cid
    closed = repo.get(cid).model_copy(update={"status": "closed"})
    repo.save(closed)
    assert repo.find_open_by_fingerprint("fp-a") is None
    assert [r.id for r in repo.list_by_status("closed")] == [cid]
    with pytest.raises(KeyError):
        repo.get("c-9")


def test_list_open은_열린_상태_전부():
    repo = InMemoryCaseRepository()
    for i, status in enumerate(["open", "investigating", "awaiting_human", "closed"]):
        repo.save(CaseRecord(id=f"c-{i}", gbm="mx", fct="gumi", fingerprint=f"fp{i}",
                             symptom="s", t0=T, created_at=T, updated_at=T, status=status))
    assert sorted(r.id for r in repo.list_open()) == ["c-0", "c-1", "c-2"]


def test_claim은_남의_살아있는_lease를_뺏지_않는다():
    from datetime import timedelta

    from src.domain.cases import InMemoryCaseRepository
    repo = InMemoryCaseRepository()
    repo.save(CaseRecord(id="c-1", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                         t0=T, created_at=T, updated_at=T))
    assert repo.claim("c-1", "w-1", now=T, ttl_s=60) is not None
    assert repo.claim("c-1", "w-2", now=T, ttl_s=60) is None          # 살아있는 남의 lease
    assert repo.claim("c-1", "w-1", now=T, ttl_s=60) is not None      # 같은 owner는 갱신
    later = T + timedelta(seconds=120)
    assert repo.claim("c-1", "w-2", now=later, ttl_s=60) is not None  # 만료됐으면 회수


# ---- 계획 15(P8): 이력 조회 표면 -------------------------------------------------------
def _closed(repo, cid, *, fp="fp", locator=None, gbm="mx", fct="gumi", at=None):
    at = at or T
    repo.save(CaseRecord(id=cid, gbm=gbm, fct=fct, fingerprint=fp, symptom="s", t0=at,
                         created_at=at, updated_at=at, status_since=at, status="closed",
                         target_locator=locator, closed_reason="조사 완료"))


def test_지문으로_종결_케이스를_최신순으로_찾는다():
    from datetime import timedelta
    repo = InMemoryCaseRepository()
    _closed(repo, "c-1", fp="fp-a", at=T - timedelta(days=2))
    _closed(repo, "c-2", fp="fp-a", at=T)
    _closed(repo, "c-3", fp="fp-b")
    repo.save(CaseRecord(id="c-4", gbm="mx", fct="gumi", fingerprint="fp-a", symptom="s", t0=T,
                         created_at=T, updated_at=T, status="investigating"))   # 안 닫혔다
    assert [r.id for r in repo.closed_by_fingerprint("fp-a", exclude_case_id="c-9")] == ["c-2", "c-1"]
    assert [r.id for r in repo.closed_by_fingerprint("fp-a", exclude_case_id="c-2")] == ["c-1"]
    assert repo.closed_by_fingerprint("fp-a", exclude_case_id="c-9", limit=1)[0].id == "c-2"
    assert repo.closed_by_fingerprint("없음", exclude_case_id="c-9") == []


def test_locator로_종결_케이스를_찾되_빈_목록은_전체를_긁지_않는다():
    from datetime import timedelta
    repo = InMemoryCaseRepository()
    _closed(repo, "c-1", locator="rest:/oee", at=T - timedelta(days=1))
    _closed(repo, "c-2", locator="mongo:twin_state", at=T)
    _closed(repo, "c-3", locator=None)
    assert [r.id for r in repo.closed_by_locators(["rest:/oee", "mongo:twin_state"],
                                                  exclude_case_id="c-9")] == ["c-2", "c-1"]
    assert repo.closed_by_locators([], exclude_case_id="c-9") == []      # 전체 조회로 번지지 않는다
    assert [r.id for r in repo.closed_by_locators(["rest:/oee"], exclude_case_id="c-1")] == []


# ---- 계획 17: 답이 어느 질문의 답인가 -----------------------------------------------------
def _parked(repo, cid="c-1", **kw):
    base = dict(id=cid, gbm="mx", fct="gumi", fingerprint="fp", symptom="s", t0=T,
                created_at=T, updated_at=T, status="awaiting_human", question="q",
                question_kind="investigation", question_seq=1)
    base.update(kw)
    repo.save(CaseRecord(**base))


def test_다른_질문의_답은_거절되고_쓰이지_않는다():
    # 사람이 Q1을 보고 답을 쓰는 사이 그래프가 Q2로 파킹했다 — 그 답이 Q2의 답으로
    # 소비되면 리드가 엉뚱한 대답을 근거로 판정한다.
    repo = InMemoryCaseRepository()
    _parked(repo, question_seq=2, question="Q2")
    assert repo.attach_answer("c-1", answer="Q1의 답", key="k-1", now=T,
                              expect_seq=1) == "stale_question"
    assert repo.get("c-1").pending_answer is None


def test_번호가_맞으면_받는다():
    repo = InMemoryCaseRepository()
    _parked(repo, question_seq=2)
    assert repo.attach_answer("c-1", answer="a", key="k", now=T, expect_seq=2) == "accepted"


def test_번호를_안_주면_예전처럼_받는다():
    # 하위 호환 — 기존 클라이언트를 깨지 않는다.
    repo = InMemoryCaseRepository()
    _parked(repo, question_seq=2)
    assert repo.attach_answer("c-1", answer="a", key="k", now=T) == "accepted"


def test_읽은_시점_이후의_저장은_진다():
    # 접수 저장의 CAS — 그 사이 남이 저장했으면 아무것도 안 바뀐다.
    repo = InMemoryCaseRepository()
    _parked(repo)
    stale = repo.get("c-1").updated_at
    repo.save(repo.get("c-1").model_copy(update={"updated_at": T + timedelta(minutes=1),
                                                 "question": "남이 바꿈"}))
    assert repo.update_if("c-1", expect={"updated_at": stale},
                          fields={"question": "내 것"}, now=T) is False
    assert repo.get("c-1").question == "남이 바꿈"
    fresh = repo.get("c-1").updated_at
    assert repo.update_if("c-1", expect={"updated_at": fresh},
                          fields={"question": "내 것"}, now=T) is True
    assert repo.get("c-1").question == "내 것" and repo.get("c-1").updated_at == T


def test_없는_케이스의_조건부_저장은_False다():
    assert InMemoryCaseRepository().update_if(
        "없음", expect={"updated_at": T}, fields={}, now=T) is False


def test_소유_필드가_바뀌면_시각이_같아도_진다():
    # 고정 시계에서 이긴 턴이 쓴 값이 진 턴이 읽은 값과 같으면 둘 다 이긴다(리뷰 M-5).
    repo = InMemoryCaseRepository()
    _parked(repo)
    record = repo.get("c-1")
    repo.save(record.model_copy(update={"question": "남이 바꿈"}))      # 시각은 그대로
    assert repo.update_if("c-1", expect={"updated_at": record.updated_at,
                                         "question": record.question},
                          fields={"target_locator": "rest:/oee"}, now=T) is False
    assert repo.get("c-1").target_locator is None       # 아무것도 안 바뀌었다
