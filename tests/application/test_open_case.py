"""사람이 연 케이스의 개설 — 접수보다 **먼저** 일어난다.

지금 `_drive_chat`은 intake가 끝난 뒤에야 case_id를 만든다. CLI에서는 프로세스
하나가 처음부터 끝까지 서 있어 드러나지 않지만, HTTP에서는 첫 요청이 돌려줄
case_id가 없고 접수 중 끊기면 문답이 통째로 사라진다.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

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


async def test_없는_케이스에_답해도_raise하지_않는다():
    # repo.get은 포트 계약상 KeyError를 던진다. 계획 13의 POST /answers가 잘못된
    # id를 받으면 500이 된다 — 접수 경로 전체가 무raise 계약이다.
    from src.application.answer import answer_case
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    result = await answer_case("없는-id", "답", repo=repo, store=store, deps=None,
                               topology=None, worker=None, clock=lambda: T)
    assert result == "skipped"


async def test_가로채인_케이스에는_조사를_걸지_않는다():
    # intake_turn이 "못 만졌다"고 돌려준 케이스에 run_once를 걸면, 그래프가
    # 파킹한 케이스가 새 스레드로 처음부터 재조사돼 원래 스레드와 질문을 잃는다 —
    # lifecycle.py가 "절대 하면 안 된다"고 적어 둔 그것이다.
    from src.application.answer import answer_case
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    record = open_case(repo=repo, store=store, symptom="s", gbm="mx", fct="gumi",
                       concern="system", requested_by=None, clock=lambda: T,
                       on_event=lambda e: None)
    repo.save(repo.get(record.id).model_copy(update={
        "status": "awaiting_human", "question": "q", "question_kind": "intake",
        "thread_ids": ["t-1"]}))

    class _Worker:
        def __init__(self):
            self.calls = []

        async def run_once(self, case_id, *, interaction_policy="autonomous"):
            self.calls.append(("run_once", interaction_policy))
            return "closed"

        async def resume_once(self, case_id, answer):
            self.calls.append(("resume_once", answer))
            return "closed"

    worker = _Worker()

    class _Racing:
        """접수 가드를 통과한 뒤 워커가 가로채는 상황."""
        async def ainvoke(self, messages):
            repo.save(repo.get(record.id).model_copy(update={
                "status": "investigating", "owner": "w-1"}))
            return type("R", (), {"content": '{"target_locator": "rest:/oee", "missing": []}'})()

    result = await answer_case(record.id, "답", repo=repo, store=store,
                               deps=SimpleNamespace(lead_llm=_Racing()), topology=None,
                               worker=worker, clock=lambda: T)
    assert result == "busy" and worker.calls == []
    assert repo.get(record.id).status == "investigating"


async def test_접수_완료_후의_조사도_대화형_정책을_유지한다():
    # answer_case → run_once 홉이 interaction_policy를 안 넘기면 chat이 연 케이스가
    # 조용히 autonomous로 강등돼 사람에게 묻는 대신 기본값으로 답하고 지나간다 —
    # 정책을 레코드에 박제한 이유가 바로 그 버그였다.
    from src.application.answer import answer_case
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    record = open_case(repo=repo, store=store, symptom="s", gbm="mx", fct="gumi",
                       concern="system", requested_by=None, clock=lambda: T,
                       on_event=lambda e: None)
    repo.save(repo.get(record.id).model_copy(update={
        "status": "awaiting_human", "question": "q", "question_kind": "intake"}))
    seen = []

    class _Worker:
        async def run_once(self, case_id, *, interaction_policy="autonomous"):
            seen.append(interaction_policy)
            return "closed"

    topo = type("T", (), {"locators": lambda self: []})()
    deps = SimpleNamespace(lead_llm=type("L", (), {
        "ainvoke": staticmethod(lambda m: _resolved())})())
    await answer_case(record.id, "답", repo=repo, store=store, deps=deps, topology=topo,
                      worker=_Worker(), clock=lambda: T, interaction_policy="interactive")
    assert seen == ["interactive"]


async def _resolved():
    return type("R", (), {"content": '{"target_locator": "rest:/oee", "missing": []}'})()


async def test_포기_사유가_호출부까지_전달된다():
    # answer_case가 turn.problems를 통째로 버리면 case resume 사용자는 접수가 왜
    # 실패했는지 절대 못 본다.
    from src.application.answer import answer_case
    repo, store = InMemoryCaseRepository(), InMemoryCaseStore()
    record = open_case(repo=repo, store=store, symptom="s", gbm="mx", fct="gumi",
                       concern="system", requested_by=None, clock=lambda: T,
                       on_event=lambda e: None)
    repo.save(repo.get(record.id).model_copy(update={
        "status": "awaiting_human", "question": "q", "question_kind": "intake"}))
    logged = []
    topo = type("T", (), {"locators": lambda self: []})()

    async def _broken(m):
        return type("R", (), {"content": "파싱 불가"})()

    class _Worker:
        async def run_once(self, case_id, *, interaction_policy="autonomous"):
            return "closed"

    await answer_case(record.id, "답", repo=repo, store=store,
                      deps=SimpleNamespace(lead_llm=type("L", (), {"ainvoke": staticmethod(_broken)})()),
                      topology=topo, worker=_Worker(), clock=lambda: T,
                      on_problem=logged.append)
    assert logged and any("파싱" in p for p in logged)
