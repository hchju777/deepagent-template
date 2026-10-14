"""R3-3 ② — 코드가 상류를 걷는다. 입구의 고리(키 값의 잎 = 클래스 → `code.resources` → 자원 키 읽기)를 키마다 반복하고, 리드는 표를 받는다.
나쁨은 없음·빈 배열·0행뿐(사람 결정 10-14 — 손 규칙 없음). 값이 그 클래스를 대는 키는 출력이라 입력에서 뺀다(측정 #9 P0 3)."""
import json
from datetime import timedelta

from src.application.state import CaseState
from src.application.walk import MAX_DEPTH, MAX_READS, MAX_SECONDS, SCAN_READS, run_walk, status_of
from src.domain.case import Case, EvidenceRef, TargetPick
from src.domain.investigation import TaskOutcome, TaskRunnerPort
from tests.application.conftest import T0, task

_NAME = ("name", "key", "pattern", "collection", "topic")


class _Reads(TaskRunnerPort):
    """`(action, 이름)` → 원본. 없는 조합은 읽기 실패. 값 None은 Redis의 없는 키(성공인데 원본이 없다)."""

    def __init__(self, table):
        self.table, self.ran, self._raws = dict(table), [], {}

    def describe(self):
        return "reads"

    def raw(self, evidence_id):
        return self._raws.get(evidence_id)

    async def run(self, task, *, case):
        name = next((task.params[k] for k in _NAME if k in task.params), None)
        self.ran.append((task.action, name))
        if (task.action, name) not in self.table:
            return TaskOutcome(task_id=task.id, status="error", error="대본 없음")
        ref = EvidenceRef(id=f"{task.id}.e1", source=f"{task.action} {name}", summary="x", body="raw")
        self._raws[ref.id] = self.table[(task.action, name)]
        return TaskOutcome(task_id=task.id, status="ok", summary="s", evidence=[ref])


def _res(writer, reads, writes=()):
    text = f"{writer} (x.py:L1) [dt-core · batch]\n  읽기 {len(reads)}:\n" + "".join(f"    {n} [{k}] — {writer}._run (x.py:L9)\n" for n, k in reads)
    if writes:
        text += f"  쓰기 {len(writes)}:\n" + "".join(f"    {n} [{k}] — {writer}._run (x.py:L12)\n" for n, k in writes)
    return text.rstrip("\n")


def _value(**obj):
    return {"type": "string", "value": json.dumps(obj)}


def _state(writer="W0", empty=("record[0].data",)):
    case = Case(id="c-9", gbm="mx", fct="gumi", origin="patrol", symptom="배지 0", t0=T0, check="badge_all_zero", target="L1/Alarm")
    picked = TargetPick(key="alarm:stats:L1", declared="alarm:stats:{line}", basis="b", empty=list(empty), writer=writer, writer_at="metadata.event_type")
    return CaseState(case=case, target=picked,
                     plan_tasks=[task("t-1", action="redis.get", params={"key": "alarm:stats:L1"}, status="ok", result_evidence_ids=["t-1.e1"])],
                     evidence=[EvidenceRef(id="t-1.e1", source="s", summary="x", body="")])


async def test_걷기는_쓴_쪽의_입력을_읽고_나쁜_입력의_쓴_쪽으로_한_홉_더_가며_입력이_다_정상이면_멈춘다():
    """표 한 행 = 코드가 읽은 키 하나. 대상 키 자신(선언 이름이 입력 목록에 있다 — ①-b의 보수적 읽기 분류)은 다시 안 읽고, 값이 이 클래스를 대는
    키는 **출력**으로 표에만 남긴다. 컬렉션 입력은 `mongo.find`. 읽기마다 State 태스크(`t-1-N`)와 증거 — 판정이 인용한다."""
    rows = [{"c": 1, "d": "-"}, {"c": 2, "d": "-"}]
    runner = _Reads({
        ("code.resources", "W0"): _res("W0", [("in:a:{line}", "rediskey"), ("alarm:stats:{line}", "rediskey"), ("out:{line}", "rediskey"),
                                              ("events", "collection")], writes=[("x:{line}", "rediskey")]),
        ("redis.get", "in:a:L1"): _value(record=[], metadata={"event_type": "W1"}),
        ("redis.get", "out:L1"): _value(metadata={"event_type": "W0"}, n=1),
        ("mongo.find", "events"): [{"a": 1}, {"a": 2}],
        ("code.resources", "W1"): _res("W1", [("src:{line}", "rediskey")]),
        ("redis.get", "src:L1"): {"type": "string", "value": json.dumps(rows)}})
    patch = await run_walk(_state(), runner=runner, classes=frozenset({"W0", "W1"}), class_services={"W0": ("batch",), "W1": ("collector",)},
                           clock=lambda: T0)
    walk = patch["walk"]
    assert [(h.key, h.depth, h.writer, h.status, h.bad) for h in walk.hops] == [
        ("alarm:stats:L1", 0, "W0", "빈 배열 (record[0].data)", True),
        ("in:a:L1", 1, "W1", "빈 배열 (record)", True),
        ("out:L1", 1, "W0", "출력 (W0 자신의 것 — 값의 metadata.event_type)", False),
        ("events", 1, "", "정상 — 행 2 · 필드 1", False),
        ("src:L1", 2, "", "정상 — 행 2 · 필드 2 · 모든 행이 같은 값: d='-'", False)]
    assert walk.stopped == "입력이 다 정상 — W1" and walk.next_hop() is None and walk.next_evidence == ""
    assert walk.reads == 6 and [t.id for t in patch["plan_tasks"]] == [f"t-1-{i}" for i in range(1, 7)]
    assert {e.id for e in patch["evidence"]} == {f"t-1-{i}.e1" for i in range(1, 7)}
    assert ("redis.get", "alarm:stats:L1") not in runner.ran and ("redis.get", "x:L1") not in runner.ran     # 대상 키·쓰기 구간은 안 읽는다
    assert walk.hops[1].evidence_id == "t-1-2.e1" and walk.bad_ids() == frozenset({"t-1.e1", "t-1-2.e1"})
    assert (walk.hops[0].service, walk.hops[1].service, walk.hops[2].kind, walk.hops[3].kind) == ("batch", "collector", "rediskey", "collection")
    assert walk.hops[1].basis == "W0가 읽는 자원 (t-1-1.e1)" and walk.hops[0].basis == "대상 키 — 값의 metadata.event_type이 W0"
    assert walk.lines()[0] == "걷기(코드가 상류를 걸었다): 홉 5 · 읽기 6 · 0.0초 — 멈춤: 입력이 다 정상 — W1"
    assert walk.lines()[2] == "  alarm:stats:L1 | W0 [batch] | 빈 배열 (record[0].data) | t-1.e1" and walk.lines()[5] == "  events | — | 정상 — 행 2 · 필드 1 | t-1-4.e1"
    # 쓴 쪽을 모르면(입구가 못 골랐다) 걷지 않는다 — 빈 패치
    assert await run_walk(_state(writer=""), runner=runner, classes=frozenset(), clock=lambda: T0) == {}


async def test_나쁜_입력의_쓴_쪽을_모르면_거기서_멈추고_다음_홉은_그_키의_uses이며_못_채우는_템플릿은_scan_뒤_다섯_개까지다():
    names = [f"q:s{i}:L1" for i in range(1, 8)]
    table = {("code.resources", "W0"): _res("W0", [("in:a:{line}", "rediskey"), ("q:{site}:{line}", "rediskey")]),
             ("redis.get", "in:a:L1"): None, ("redis.scan", "q:*"): names,
             **{("redis.get", n): {"type": "string", "value": "[]"} for n in names}}
    runner = _Reads(table)
    walk = (await run_walk(_state(), runner=runner, classes=frozenset({"W0"}), clock=lambda: T0))["walk"]
    assert [(h.key, h.status, h.bad) for h in walk.hops[1:]] == [("in:a:L1", "없음", True)] + [(n, "0행", True) for n in names[:SCAN_READS]]
    assert walk.stopped == "쓴 쪽을 모른다 — in:a:L1(값에 인덱스의 클래스 이름이 없다)"
    assert walk.next_hop() == ("code.uses", {"name": "in:a:{line}"}) and walk.next_evidence == "t-1-2.e1"
    assert ("redis.get", names[SCAN_READS]) not in runner.ran and walk.reads == 1 + 1 + 1 + SCAN_READS
    # 읽기 실패는 나쁨이 아니다 — 모르는 것이지 비어 있는 것이 아니다
    failing = _Reads({("code.resources", "W0"): _res("W0", [("k", "rediskey")])})
    walk = (await run_walk(_state(), runner=failing, classes=frozenset({"W0"}), clock=lambda: T0))["walk"]
    assert walk.hops[1].status == "읽기 실패 — 대본 없음" and not walk.hops[1].bad and walk.stopped == "입력이 다 정상 — W0"
    # 쓴 쪽의 자원 자체를 못 읽었으면 거기서 멈춘다
    walk = (await run_walk(_state(), runner=_Reads({}), classes=frozenset({"W0"}), clock=lambda: T0))["walk"]
    assert walk.stopped == "W0의 자원을 못 읽었다 — 대본 없음" and walk.next_hop() is None and len(walk.hops) == 1


async def test_걷기의_상한은_깊이_읽기_시간이고_상한에서_멈추면_다음_홉은_그_자리의_resources다():
    chain = {}
    for i in range(5):
        chain[("code.resources", f"W{i}")] = _res(f"W{i}", [(f"k{i + 1}", "rediskey")])
        chain[("redis.get", f"k{i + 1}")] = _value(record=[], metadata={"event_type": f"W{i + 1}"})
    walk = (await run_walk(_state(), runner=_Reads(chain), classes=frozenset(f"W{i}" for i in range(6)), clock=lambda: T0))["walk"]
    assert [h.depth for h in walk.hops] == [0, 1, 2, 3, 4] and walk.stopped == f"깊이 {MAX_DEPTH} 상한 — W4부터는 리드가"
    assert walk.next_hop() == ("code.resources", {"name": "W4"}) and walk.next_evidence == walk.hops[4].evidence_id == "t-1-8.e1"
    many = {("code.resources", "W0"): _res("W0", [(f"k{i}", "rediskey") for i in range(1, 21)]),
            **{("redis.get", f"k{i}"): _value(n=i) for i in range(1, 21)}}
    runner = _Reads(many)
    walk = (await run_walk(_state(), runner=runner, classes=frozenset({"W0"}), clock=lambda: T0))["walk"]
    assert walk.reads == MAX_READS and len(walk.hops) == MAX_READS and walk.stopped == f"읽기 {MAX_READS} 상한 — W0의 k{MAX_READS}부터는 리드가"
    assert walk.next_hop() == ("code.resources", {"name": "W0"}) and walk.next_evidence == "t-1.e1"

    class Clock:
        def __init__(self):
            self.n = 0

        def __call__(self):
            self.n += 1
            return T0 + timedelta(seconds=20 * (self.n - 1))

    walk = (await run_walk(_state(), runner=_Reads(many), classes=frozenset({"W0"}), clock=Clock()))["walk"]
    assert walk.stopped == f"{MAX_SECONDS:.0f}초 상한 — W0의 k1부터는 리드가" and walk.reads == 1 and walk.seconds > MAX_SECONDS


def test_상태는_없음_0행_빈_배열만_나쁘고_정상이면_행_요약을_단다():
    assert status_of(None) == ("없음", True)
    assert status_of({"type": "string", "value": "[]"}) == ("0행", True)
    assert status_of([]) == ("0행", True)
    assert status_of({"type": "string", "value": json.dumps({"record": [], "metadata": {"x": 1}})}) == ("빈 배열 (record)", True)
    assert status_of([{"a": 1, "b": "-"}, {"a": 2, "b": "-"}]) == ("정상 — 행 2 · 필드 2 · 모든 행이 같은 값: b='-'", False)
    assert status_of({"type": "string", "value": json.dumps({"record": [{"a": 1}], "meta": []})}) == ("정상 — record: 행 1 · 필드 1", False)
    assert status_of(["a", "b"]) == ("정상 — 2건", False) and status_of({"type": "string", "value": "7"}) == ("정상", False)
