"""라벨 유입구와 게이트(계획 15/P8)."""
from datetime import datetime, timezone

from src.application.labels import label_stats, submit_label
from src.domain.cases import CaseRecord, InMemoryCaseRepository
from src.domain.label import InMemoryLabelStore

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _repo(closed=0, open_=0):
    repo = InMemoryCaseRepository()
    for i in range(closed):
        repo.save(CaseRecord(id=f"c-{i}", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                             t0=T, created_at=T, updated_at=T, status="closed",
                             closed_reason="조사 완료"))
    for i in range(open_):
        repo.save(CaseRecord(id=f"o-{i}", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                             t0=T, created_at=T, updated_at=T, status="investigating"))
    return repo


def test_라벨은_기록되고_없는_케이스는_거절된다():
    repo, labels = _repo(closed=1), InMemoryLabelStore()
    assert submit_label("c-0", agreement="wrong", resolution="fixed",
                        actual_component="plan-sync", saw_report=True, labeled_by="hchju",
                        repo=repo, labels=labels, clock=lambda: T) == "recorded"
    row = labels.list_for("c-0")[0]
    assert row.agreement == "wrong" and row.actual_root_cause_component == "plan-sync"
    assert row.saw_report is True and row.labeled_by == "hchju" and row.labeled_at == T
    assert submit_label("없음", agreement="correct", repo=repo, labels=labels,
                        clock=lambda: T) == "not_found"


def test_조사_중인_케이스도_라벨할_수_있다():
    # 사람이 조사가 끝나기 전에 원인을 알 수 있다. 분모(종결)와는 다른 축이다.
    repo, labels = _repo(open_=1), InMemoryLabelStore()
    assert submit_label("o-0", agreement="correct", repo=repo, labels=labels,
                        clock=lambda: T) == "recorded"


def test_저장소_장애는_error다():
    class _Boom(InMemoryLabelStore):
        def append(self, label):
            raise RuntimeError("mongo down")
    assert submit_label("c-0", agreement="correct", repo=_repo(closed=1), labels=_Boom(),
                        clock=lambda: T) == "error"


def test_게이트는_닫혀_있고_퍼센트를_내지_않는다():
    # n>=30 그리고 라벨률>50% 전에는 어떤 정확도도 내지 않는다 — 건수만.
    repo, labels = _repo(closed=40), InMemoryLabelStore()
    for i in range(12):
        submit_label(f"c-{i}", agreement="correct", repo=repo, labels=labels, clock=lambda: T)
    stats = label_stats(repo=repo, labels=labels)
    assert stats.closed_total == 40 and stats.labeled_cases == 12 and stats.gate_open is False
    assert "12" in stats.why and "40" in stats.why and "%" not in stats.why


def test_게이트는_건수와_라벨률을_둘_다_요구한다():
    repo, labels = _repo(closed=100), InMemoryLabelStore()
    for i in range(40):                      # 40건이지만 라벨률 40%
        submit_label(f"c-{i}", agreement="correct", repo=repo, labels=labels, clock=lambda: T)
    assert label_stats(repo=repo, labels=labels).gate_open is False
    repo3, labels3 = _repo(closed=40), InMemoryLabelStore()
    for i in range(25):                      # 라벨률 62%지만 25건 — 건수 미달
        submit_label(f"c-{i}", agreement="correct", repo=repo3, labels=labels3, clock=lambda: T)
    assert label_stats(repo=repo3, labels=labels3).gate_open is False
    repo2, labels2 = _repo(closed=40), InMemoryLabelStore()
    for i in range(30):                      # 30건, 75%
        submit_label(f"c-{i}", agreement="correct", repo=repo2, labels=labels2, clock=lambda: T)
    assert label_stats(repo=repo2, labels=labels2).gate_open is True


def test_종결_케이스가_없으면_라벨률은_0이고_게이트는_닫힌다():
    assert label_stats(repo=_repo(), labels=InMemoryLabelStore()).gate_open is False
