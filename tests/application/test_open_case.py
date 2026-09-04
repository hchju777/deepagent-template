"""사람이 연 케이스의 개설 — 접수보다 **먼저** 일어난다.

지금 `_drive_chat`은 intake가 끝난 뒤에야 case_id를 만든다. CLI에서는 프로세스
하나가 처음부터 끝까지 서 있어 드러나지 않지만, HTTP에서는 첫 요청이 돌려줄
case_id가 없고 접수 중 끊기면 문답이 통째로 사라진다.
"""
from datetime import datetime, timezone

from src.application.open_case import open_case
from src.domain.cases import InMemoryCaseRepository
from src.domain.store import InMemoryCaseStore

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _open(**kw):
    repo, store, events = InMemoryCaseRepository(), InMemoryCaseStore(), []
    record = open_case(repo=repo, store=store, clock=lambda: T, on_event=events.append,
                       **{"symptom": "OEE가 이상하다", "gbm": "mx", "fct": "gumi",
                          "concern": "system", "requested_by": "alice", **kw})
    return record, repo, store, events


def test_케이스는_접수보다_먼저_열린다():
    record, repo, _store, events = _open()
    assert record.id and record.status == "open"
    assert record.origin == "human" and record.requested_by == "alice"
    # 접수가 아직 안 돌았으므로 대상은 비어 있다 — 그게 이 계획의 요점이다.
    assert record.target_locator is None
    assert repo.get(record.id) is not None                    # 즉시 영속된다
    assert [e.event for e in events] == ["case_status_changed"]


def test_원문_증상이_증거로_박제된다():
    # 접수가 증상을 다듬어도 사람이 처음 쓴 문장이 남아야 한다 — 판정이 "무엇을
    # 물었나"를 되짚을 유일한 근거다.
    record, _repo, store, _events = _open(symptom="라인 7이 멈춘 것 같다")
    sources = [r.source for r in store.list_evidence(record.id)]
    assert "human:symptom" in sources
    body = store.get_evidence(record.id, store.list_evidence(record.id)[0].id)
    assert "라인 7" in repr(body)


def test_concern과_주체가_레코드에_실린다():
    record, _repo, _store, _events = _open(concern="operation", requested_by="bob")
    assert record.concern == "operation" and record.requested_by == "bob"


def test_주체가_없어도_열린다():
    # access.allow가 비어 있는 단일 팀 설치에서 주체를 강요하지 않는다.
    record, _repo, _store, _events = _open(requested_by=None)
    assert record.requested_by is None


def test_이벤트가_실패해도_케이스는_열린다():
    # 발행은 케이스 개설을 뒤집을 수 없다 — on_event는 조사를 실패시킬 수 없는
    # 부수효과로 설계됐다(스펙 §3.6).
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()

    def _boom(_event):
        raise RuntimeError("구독자 없음")

    record = open_case(repo=repo, store=store, clock=lambda: T, on_event=_boom,
                       symptom="s", gbm="mx", fct="gumi", concern="system",
                       requested_by=None)
    assert repo.get(record.id) is not None
