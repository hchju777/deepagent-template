"""R3-3 ② — 코드가 상류를 걷는다. 입구의 고리(키 값의 잎 = 클래스 → `code.resources` → 자원 키 읽기)를 키마다 반복하고, 리드는 표를 받는다.
나쁨은 없음·빈 배열·0행뿐(사람 결정 10-14 — 손 규칙 없음). 값이 그 클래스를 대는 키는 출력이라 입력에서 뺀다(측정 #9 P0 3)."""
import json
from datetime import timedelta

from src.application.state import CaseState
from src.application.walk import MAX_DEPTH, MAX_READS, MAX_SECONDS, SCAN_READS, run_walk, status_of
from src.domain.case import Case, EvidenceRef, FilterHit, FilterScan, Rung, TargetPick, Walk
from src.domain.investigation import TaskOutcome, TaskRunnerPort
from tests.application.conftest import T0, task

_NAME = ("name", "key", "pattern", "collection", "topic")


class _Reads(TaskRunnerPort):
    """`(action, 이름)` → 원본. 없는 조합은 읽기 실패. 값 None은 Redis의 없는 키(성공인데 원본이 없다)."""

    def __init__(self, table):
        self.table, self.ran, self.params, self._raws = dict(table), [], [], {}

    def describe(self):
        return "reads"

    def raw(self, evidence_id):
        return self._raws.get(evidence_id)

    async def run(self, task, *, case):
        name = task.params["patterns"][0] if "patterns" in task.params else next((task.params[k] for k in _NAME if k in task.params), None)
        self.ran.append((task.action, name))
        self.params.append((task.action, dict(task.params)))
        if (task.action, name) not in self.table:
            return TaskOutcome(task_id=task.id, status="error", error="대본 없음")
        ref = EvidenceRef(id=f"{task.id}.e1", source=f"{task.action} {name}", summary="x", body="raw")
        self._raws[ref.id] = self.table[(task.action, name)]
        return TaskOutcome(task_id=task.id, status="ok", summary="s", evidence=[ref])


class _Lookup:
    """어댑터의 단일값 조회(`filter_lines`) 대역 — `(클래스, 필드들)` → `FilterScan`. 필드 없는 호출은 링크만 묻는 것(멈춘 자리의 뒤 칸). 없는 조합은 거르는
    줄 없음(클래스 파일 하나, 전부 대조)."""

    def __init__(self, table):
        self.table, self.calls = dict(table), []

    async def __call__(self, name, *, fields, **params):
        key = (name, tuple(sorted(fields.items())))
        self.calls.append((name, dict(fields), dict(params)))
        return self.table.get(key, FilterScan(files=[f"{name.lower()}.py"], checked=list(fields)))


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
    키는 **출력**으로 표에만 남긴다. 컬렉션 입력은 `mongo.find`. 읽기마다 State 태스크(입구 뒤 다음 번호부터 `t-2`…, `walked` 플래그)와 증거 — 판정이 인용한다."""
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
    # 멈춘 자리는 가장 깊은 나쁜 홉(in:a:L1 — 그 쓴 쪽 W1의 입력이 다 정상), 다음 칸은 W1의 읽는 메서드 전체(`code.read line=`, R3-4 ②)
    assert walk.stopped == "입력이 다 정상 — W1" and walk.stop_at == "t-3.e1" and walk.chain_ids() == frozenset({"t-1.e1", "t-3.e1"})
    assert walk.next_hop() == ("code.read", {"service": "collector", "path": "x.py", "line": 9}) and walk.next_evidence == "t-6.e1"
    # 읽기 6 = 자원 2 + 입력 4 — 단일값 검사는 조회(R3-4 ③)라 읽기가 아니고, 조회가 없으면(인덱스 없음) 표시만
    assert walk.reads == 6 and [t.id for t in patch["plan_tasks"]] == [f"t-{i}" for i in range(2, 8)] and all(t.walked for t in patch["plan_tasks"])
    assert {e.id for e in patch["evidence"]} == {f"t-{i}.e1" for i in range(2, 8)} and ("code.grep", "d") not in runner.ran
    assert ("redis.get", "alarm:stats:L1") not in runner.ran and ("redis.get", "x:L1") not in runner.ran     # 대상 키·쓰기 구간은 안 읽는다
    assert walk.hops[1].evidence_id == "t-3.e1" and walk.bad_ids() == frozenset({"t-1.e1", "t-3.e1"})
    assert (walk.hops[0].service, walk.hops[1].service, walk.hops[2].kind, walk.hops[3].kind) == ("batch", "collector", "rediskey", "collection")
    assert walk.hops[1].basis == "W0가 읽는 자원 (t-2.e1)" and walk.hops[0].basis == "대상 키 — 값의 metadata.event_type이 W0"
    assert walk.lines()[0] == "걷기(코드가 상류를 걸었다): 홉 5 · 읽기 6 · 0.0초 — 멈춤: 입력이 다 정상 — W1"
    assert walk.lines()[2] == "  alarm:stats:L1 | W0 [batch] | 빈 배열 (record[0].data) | t-1.e1" and walk.lines()[5] == "  events | — | 정상 — 행 2 · 필드 1 | t-5.e1"
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
    assert walk.stopped == "키가 없다 — in:a:L1"                                     # 값이 없으면 볼 것도 없다(R3-5d ①-a)
    assert walk.next_hop() == ("code.uses", {"name": "in:a:{line}"}) and walk.next_evidence == "t-3.e1"
    assert ("redis.get", names[SCAN_READS]) not in runner.ran and walk.reads == 1 + 1 + 1 + SCAN_READS
    # 읽기 실패는 나쁨이 아니다 — 모르는 것이지 비어 있는 것이 아니다. 그래서 멈춤도 "입력이 다 정상"이 아니라 "다 못 봤다"(R3-5b ②, 사람 결정 10-16 —
    # 전에는 여기서 "입력이 다 정상"을 단정했다)
    failing = _Reads({("code.resources", "W0"): _res("W0", [("k", "rediskey")])})
    walk = (await run_walk(_state(), runner=failing, classes=frozenset({"W0"}), clock=lambda: T0))["walk"]
    assert walk.hops[1].status == "읽기 실패 — 대본 없음" and not walk.hops[1].bad and walk.stopped == "입력을 다 못 봤다 — W0(읽기 실패 k)"
    # 쓴 쪽의 자원 자체를 못 읽었으면 거기서 멈춘다
    walk = (await run_walk(_state(), runner=_Reads({}), classes=frozenset({"W0"}), clock=lambda: T0))["walk"]
    assert walk.stopped == "W0의 자원을 못 읽었다 — 대본 없음" and walk.next_hop() == ("code.resources", {"name": "W0"}) and len(walk.hops) == 1
    assert walk.hops[0].status == "빈 배열 (record[0].data) · W0의 자원 못 읽음 — 대본 없음"              # 그 행에 적는다(R3-4 ①)


async def test_걷기의_상한은_깊이_읽기_시간이고_상한에서_멈추면_다음_홉은_그_자리의_resources다():
    chain = {}
    for i in range(5):
        chain[("code.resources", f"W{i}")] = _res(f"W{i}", [(f"k{i + 1}", "rediskey")])
        chain[("redis.get", f"k{i + 1}")] = _value(record=[], metadata={"event_type": f"W{i + 1}"})
    walk = (await run_walk(_state(), runner=_Reads(chain), classes=frozenset(f"W{i}" for i in range(6)), clock=lambda: T0))["walk"]
    assert [h.depth for h in walk.hops] == [0, 1, 2, 3, 4] and walk.stopped == f"깊이 {MAX_DEPTH} 상한 — W4부터는 리드가"
    assert walk.next_hop() == ("code.resources", {"name": "W4"}) and walk.next_evidence == walk.hops[4].evidence_id == "t-9.e1"
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


async def test_같은_클래스_이름이_두_레포면_그_키를_선언한_서비스로_가르고_못_가르면_둘_다_걷는다():
    """측정 #9 P1 4: 같은 클래스 이름이 수집기·처리기 두 레포에 있고 `파일:qualname`도 같다. 사람 결정: 대상 키를 config로 선언한 서비스(흐름
    그래프 `declares`)로 가른다 — 후보끼리 가를 때만, 표에 근거. 둘 다 선언이거나 선언이 없으면 둘 다 걷는다(판정의 component 후보도 둘 다).
    깊은 홉은 **그 입력 키의 선언 이름**으로 가른다. 한 곳뿐이면 가를 것이 없고 `service` 인자도 안 단다(리드의 중복이 같은 질의여야 잡힌다)."""
    table = {("code.resources", "W0"): _res("W0", [("in:a:{line}", "rediskey")]),
             ("redis.get", "in:a:L1"): _value(record=[], metadata={"event_type": "W1"}),
             ("code.resources", "W1"): _res("W1", [("src:{line}", "rediskey")]),
             ("redis.get", "src:L1"): _value(n=1)}
    runner = _Reads(table)
    walk = (await run_walk(_state(), runner=runner, classes=frozenset({"W0", "W1"}), class_services={"W0": ("a", "b"), "W1": ("a", "b")},
                           declared_by={"alarm:stats:{line}": ("a",), "in:a:{line}": ("b",)}, clock=lambda: T0))["walk"]
    assert [p for a, p in runner.params if a == "code.resources"] == [{"name": "W0", "service": "a"}, {"name": "W1", "service": "b"}]
    assert (walk.hops[0].service, walk.hops[1].service) == ("a", "b") and walk.hops[1].basis == "W0[a]가 읽는 자원 (t-2.e1)"
    assert walk.notes == ["W0는 레포 2곳(a, b)에 있다 — alarm:stats:{line}을 선언한 a로 골랐다", "W1는 레포 2곳(a, b)에 있다 — in:a:{line}을 선언한 b로 골랐다"]
    assert "  주: W0는 레포 2곳(a, b)에 있다 — alarm:stats:{line}을 선언한 a로 골랐다" in walk.lines()
    assert (walk.hops[0].candidates, walk.hops[1].candidates) == (["a"], ["b"])                 # 판정의 component 후보 — 가른 쪽
    both = _Reads(table)
    walk = (await run_walk(_state(), runner=both, classes=frozenset({"W0", "W1"}), class_services={"W0": ("a", "b")},
                           declared_by={"alarm:stats:{line}": ("a", "b")}, clock=lambda: T0))["walk"]
    assert [p for a, p in both.params if a == "code.resources"][:2] == [{"name": "W0", "service": "a"}, {"name": "W0", "service": "b"}]
    assert walk.hops[0].service == "a, b" and walk.notes[0].endswith("(둘 다 선언): 둘 다 걷는다") and walk.hops[0].candidates == ["a", "b"]
    assert [h.key for h in walk.hops] == ["alarm:stats:L1", "in:a:L1", "src:L1"]            # 둘째 가지의 같은 입력은 다시 안 읽는다
    walk = (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0", "W1"}), class_services={"W0": ("a", "b")},
                           clock=lambda: T0))["walk"]
    assert walk.notes[0].endswith("(선언 없음): 둘 다 걷는다")
    one = _Reads(table)
    walk = (await run_walk(_state(), runner=one, classes=frozenset({"W0", "W1"}), class_services={"W0": ("a",), "W1": ("b",)}, clock=lambda: T0))["walk"]
    assert walk.notes == [] and [p for a, p in one.params if a == "code.resources"] == [{"name": "W0"}, {"name": "W1"}]
    assert (walk.hops[0].service, walk.hops[1].service, walk.hops[1].basis) == ("a", "b", "W0가 읽는 자원 (t-2.e1)")


# ── R3-4 ① — 레포 단위 가르기·가지 격리 ──

async def test_한_레포를_여러_서비스가_공유하면_레포_단위로_가르고_표에_공유로_적으며_그_레포를_resources에_넘긴다():
    """측정 #10 P0 2: 처리기 레포는 서비스 5개가 `path` 없이 한 레포·한 config를 공유한다 — 서비스로는 못 가르고 원천 키 셋이 수집기로 붙었다.
    사람 결정: 레포 단위로 가르고 표에 `<레포>(서비스 N개 공유)`, component 후보는 그 레포의 서비스 전부, 가른 레포를 `code.resources repo=`로
    넘긴다(공용 키 멈춤의 직접 원인). 한 레포뿐이면(공유라도) 인자 없이 — 리드의 중복이 같은 질의여야 잡힌다."""
    table = {("code.resources", "W0"): _res("W0", [("in:a:{line}", "rediskey")]),
             ("redis.get", "in:a:L1"): _value(record=[], metadata={"event_type": "W1"}),
             ("code.resources", "W1"): _res("W1", [("src:{line}", "rediskey")]),
             ("redis.get", "src:L1"): _value(n=1)}
    runner = _Reads(table)
    repos = {"col": "dt-col", "p1": "dt-proc", "p2": "dt-proc", "p3": "dt-proc"}
    walk = (await run_walk(_state(), runner=runner, classes=frozenset({"W0", "W1"}),
                           class_services={"W0": ("col", "p1", "p2", "p3"), "W1": ("p1", "p2", "p3")}, service_repos=repos,
                           declared_by={"alarm:stats:{line}": ("p1", "p2", "p3")}, clock=lambda: T0))["walk"]
    assert [p for a, p in runner.params if a == "code.resources"] == [{"name": "W0", "repo": "dt-proc"}, {"name": "W1"}]
    assert (walk.hops[0].service, walk.hops[0].candidates) == ("dt-proc(서비스 3개 공유)", ["p1", "p2", "p3"])
    assert (walk.hops[1].service, walk.hops[1].candidates) == ("dt-proc(서비스 3개 공유)", ["p1", "p2", "p3"])
    assert walk.hops[1].basis == "W0[dt-proc(서비스 3개 공유)]가 읽는 자원 (t-2.e1)"
    assert walk.notes == ["W0는 레포 2곳(col, dt-proc(서비스 3개 공유))에 있다 — alarm:stats:{line}을 선언한 dt-proc(서비스 3개 공유)로 골랐다"]
    assert walk.stopped == "입력이 다 정상 — W1"
    # 선언이 양쪽이면 레포 둘 다 걷는다 — 한 서비스 레포는 service=, 공유 레포는 repo=. 후보는 전부.
    both = _Reads(table)
    walk = (await run_walk(_state(), runner=both, classes=frozenset({"W0", "W1"}), class_services={"W0": ("col", "p1", "p2")},
                           service_repos=repos, declared_by={"alarm:stats:{line}": ("col", "p1")}, clock=lambda: T0))["walk"]
    assert [p for a, p in both.params if a == "code.resources"][:2] == [{"name": "W0", "service": "col"}, {"name": "W0", "repo": "dt-proc"}]
    assert (walk.hops[0].service, walk.hops[0].candidates) == ("col, dt-proc(서비스 2개 공유)", ["col", "p1", "p2"])
    assert walk.notes[0] == "W0는 레포 2곳(col, dt-proc(서비스 2개 공유))에 있다 — alarm:stats:{line}을 선언한 쪽으로 못 가른다(둘 다 선언): 둘 다 걷는다"


async def test_한_가지의_resources_실패는_그_행에만_적고_다른_가지는_계속_걷는다():
    """측정 #10 P0 2: 공용 키 하나의 쓴 쪽 `code.resources`가 실패하자 걷기 전체가 멈췄다. 실패는 그 입력 행에 `자원 못 읽음`으로 남기고 다른
    가지는 끝까지 걷는다. 멈춘 이유는 (모르는 쓴 쪽이 없으면) 그 실패고 다음 홉은 없다 — 증거는 그 행의 것."""
    table = {("code.resources", "W0"): _res("W0", [("in:a:{line}", "rediskey"), ("in:b:{line}", "rediskey")]),
             ("redis.get", "in:a:L1"): _value(record=[], metadata={"event_type": "W1"}),
             ("redis.get", "in:b:L1"): _value(record=[], metadata={"event_type": "W2"}),
             ("code.resources", "W2"): _res("W2", [("src:{line}", "rediskey")]),
             ("redis.get", "src:L1"): _value(n=1)}
    runner = _Reads(table)
    walk = (await run_walk(_state(), runner=runner, classes=frozenset({"W0", "W1", "W2"}), class_services={"W1": ("a",)}, clock=lambda: T0))["walk"]
    assert [h.key for h in walk.hops] == ["alarm:stats:L1", "in:a:L1", "in:b:L1", "src:L1"]
    assert walk.hops[1].status == "빈 배열 (record) · W1의 자원 못 읽음 — 대본 없음" and walk.hops[1].bad
    assert walk.hops[3].status.startswith("정상") and ("code.resources", "W2") in runner.ran
    assert walk.stopped == "W1의 자원을 못 읽었다 — 대본 없음" and walk.next_hop() == ("code.resources", {"name": "W1"}) and walk.next_evidence == "t-3.e1"


async def test_멈춘_자리는_가장_깊은_나쁜_홉이고_다음_칸은_늘_있으며_입력이_없으면_입력을_모른다고_적는다():
    """측정 #10 P0 3·4: 멈춘 이유가 "마지막 실패"였고 다음 홉이 없으면 frame 예시가 네 칸으로 돌아갔다. 사람 결정: 멈춘 자리 = 가장 깊은 나쁜 홉,
    다음 칸은 늘(리터럴 함수 → `code.uses` → `code.resources` → `_run` 전체), 입력 정상과 입력 모름(인덱스 자원 0 — 메시지 입력일 수 있다)을
    가른다. pinned 본문은 그 홉까지의 사슬만 — 다른 가지의 나쁜 홉은 접힌다."""
    table = {("code.resources", "W0"): _res("W0", [("in:a:{line}", "rediskey"), ("in:b:{line}", "rediskey")]),
             ("redis.get", "in:a:L1"): _value(record=[], metadata={"event_type": "W1"}),
             ("redis.get", "in:b:L1"): _value(record=[]),                                   # 쓴 쪽 모름, 깊이 1 — 먼저 생겼지만 얕다
             ("code.resources", "W1"): _res("W1", [("src:a:{line}", "rediskey")]),
             ("redis.get", "src:a:L1"): _value(record=[])}                                   # 쓴 쪽 모름, 깊이 2 — 멈춘 자리
    walk = (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0", "W1"}), class_services={"W1": ("a",)}, clock=lambda: T0))["walk"]
    assert walk.stopped == "쓴 쪽을 모른다 — src:a:L1(값에 인덱스의 클래스 이름이 없다)" and walk.stop_at == "t-6.e1"
    assert walk.next_hop() == ("code.uses", {"name": "src:a:{line}"}) and walk.next_evidence == "t-6.e1"
    assert walk.chain_ids() == frozenset({"t-1.e1", "t-3.e1", "t-6.e1"}) and walk.bad_ids() == frozenset({"t-1.e1", "t-3.e1", "t-4.e1", "t-6.e1"})
    assert [h.parent for h in walk.hops] == ["", "t-1.e1", "t-1.e1", "t-3.e1"]
    # 입력이 0이면 "입력을 모른다" — 다음 칸은 그 클래스 본문(`code.read line=`이 머리 줄을 품은 심볼 전체를 준다)
    none = _Reads({("code.resources", "W0"): "W0 (x.py:L1) [dt-core · a]\n  읽거나 쓰는 자원이 인덱스에 없다 — 메서드 2개"})
    walk = (await run_walk(_state(), runner=none, classes=frozenset({"W0"}), class_services={"W0": ("a",)}, clock=lambda: T0))["walk"]
    assert walk.stopped == "입력을 모른다 — W0(인덱스 자원 0 — 메시지 입력일 수 있다 · 포인터 없음)" and walk.stop_at == "t-1.e1"
    assert walk.rungs == [Rung(action="code.read", params={"service": "a", "path": "x.py", "line": 1}, evidence="t-2.e1")]
    # 읽는 쪽 서비스를 모르면 code.read를 못 만든다 — 칸 없음
    walk = (await run_walk(_state(), runner=none, classes=frozenset({"W0"}), clock=lambda: T0))["walk"]
    assert walk.rungs == [] and walk.next_hop() is None and walk.next_evidence == ""


# ── R3-5 ① — 단일값 통과 뒤 두 레포 가르기(측정 #11 P0 1), 던진 걷기도 보인다(P0 2) ──

async def test_단일값_통과_뒤_두_레포_가르기가_같은_걷기에서_이어진다():
    """측정 #11 P0 1: 단일값 분기의 문자열 변수가 중첩 함수 이름을 덮어 그 뒤 `branches()`가 TypeError를 냈다 — 걷기 노드가 삼켜 케이스 블록에 아무것도
    안 떴고 리드가 혼자 걸었다(c-1이 #9 수준으로 되돌아간 원인). 통과한 입력 뒤에 두 레포인 쓴 쪽이 와도 가른다. 보드(palette-empty)는 가르기가 통과보다
    먼저라 못 잡았다 — 순서가 테스트에 있어야 한다."""
    rows = [{"code": f"A{i}", "lvl": "x"} for i in range(3)]
    table = {("code.resources", "W0"): _res("W0", [("in:a:{line}", "rediskey"), ("in:b:{line}", "rediskey")]),
             ("redis.get", "in:a:L1"): {"type": "string", "value": json.dumps({"record": rows})},        # 단일값 lvl='x' — 통과
             ("redis.get", "in:b:L1"): _value(record=[], metadata={"event_type": "W1"}),                  # 나쁨 — 쓴 쪽 W1은 레포 둘
             ("code.resources", "W1"): _res("W1", [("src:{line}", "rediskey")]),
             ("redis.get", "src:L1"): _value(n=1)}
    runner = _Reads(table)
    walk = (await run_walk(_state(), runner=runner, classes=frozenset({"W0", "W1"}), class_services={"W0": ("a",), "W1": ("a", "b")},
                           declared_by={"in:b:{line}": ("b",)}, lookup=_Lookup({}), clock=lambda: T0))["walk"]
    assert walk.hops[1].status.endswith("· 단일값 lvl='x' — 거르는 줄 없음(1/1 대조, 1파일)")
    assert walk.notes == ["W1는 레포 2곳(a, b)에 있다 — in:b:{line}을 선언한 b로 골랐다"]
    assert ("code.resources", {"name": "W1", "service": "b"}) in runner.params and walk.stopped == "입력이 다 정상 — W1"
    # 거르는 줄을 잡은 뒤(다른 문자열 분기)에도 같다
    hit = _Lookup({("W0", (("lvl", "x"),)): FilterScan(hits=[HIT], checked=["lvl"], files=["w0.py"])})
    walk = (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0", "W1"}), class_services={"W0": ("a",), "W1": ("a", "b")},
                           declared_by={"in:b:{line}": ("b",)}, lookup=hit, clock=lambda: T0))["walk"]
    assert walk.hops[1].bad and walk.notes == ["W1는 레포 2곳(a, b)에 있다 — in:b:{line}을 선언한 b로 골랐다"]
    # 던진 걷기(홉 0)도 멈춤 사유 한 줄은 보인다 — 아무것도 없으면 걷기가 있었는지조차 모른다
    assert Walk(stopped="걷기 노드가 던졌다 — TypeError: 'str' object is not callable").lines() == [
        "걷기(코드가 상류를 걸었다): 홉 0 · 읽기 0 · 0.0초 — 멈춤: 걷기 노드가 던졌다 — TypeError: 'str' object is not callable"]
    assert Walk().lines() == []                                                                 # 걷기 자체가 없으면 전처럼 빈 목록


# ── R3-3 ③ → R3-4 ③ — 단일값 필드의 나쁨은 코드 줄이 정한다(어댑터 안의 조회) ──

HIT = FilterHit(path="proc/job.py", line=50, text='rows = [r for r in rows if r["wg"] != "-"]', repo="r", field="wg", value="-")


async def test_단일값_필드는_읽는_쪽_코드_줄에_필드와_값이_한_비교식_양쪽에_있을_때만_나쁘고_그_값이_이어지는_동안_위로_간다():
    """측정 #9 c-2: 입력 키 554행의 두 필드가 전부 `'-'`였고, 읽는 쪽 코드에 `["<필드>"] != "-"` 줄이 있었다 — 코드가 그 값을 걸러 아무것도 안
    남는다. 측정 #10 P0 1: 레포 전체 grep은 사이트 상수 config 줄과 다른 처리기의 dict 줄을 가짜로 잡고 진짜 줄은 놓쳤다 — 조회는 어댑터 안에서
    (범위 = 읽는 클래스의 파일 + 이은 컴포넌트 파일, 짝 = 한 비교식의 양쪽), runner를 안 거쳐 읽기 상한·증거 밖(P0 4). 잡히면 입력에 같은 필드=
    같은 값이 있는 동안 위로(조회 없이), 통과한 필드는 전부 표에 적는다."""
    rows = [{"code": f"A{i}", "wg": "-", "lvl": "x"} for i in range(3)]
    table = {("code.resources", "W0"): _res("W0", [("in:{line}", "rediskey")]),
             ("redis.get", "in:L1"): {"type": "string", "value": json.dumps({"record": rows, "metadata": {"event_type": "W1"}})},
             ("code.resources", "W1"): _res("W1", [("src:{line}", "rediskey")]),
             ("redis.get", "src:L1"): {"type": "string", "value": json.dumps([{"id": i, "wg": "-"} for i in range(4)])}}
    both = (("lvl", "x"), ("wg", "-"))
    runner, lookup = _Reads(table), _Lookup({("W0", both): FilterScan(hits=[HIT], checked=["wg", "lvl"], files=["w0.py", "proc/job.py"], linked=["flt"])})
    walk = (await run_walk(_state(), runner=runner, classes=frozenset({"W0", "W1"}), class_services={"W0": ("a",)}, lookup=lookup, clock=lambda: T0))["walk"]
    # 문구는 사실만(사람 결정 10-15): 행 수·그 값인 필드·그 값이 든 비교식의 자리와 원문. "코드가 거른다"처럼 원인을 말하지 않는다.
    assert walk.hops[1].status == "나쁨 — 3행 전부 wg='-' · 이 필드 값이 든 비교식 proc/job.py:L50 'rows = [r for r in rows if r[\"wg\"] != \"-\"]'"
    assert walk.hops[1].bad and walk.hops[1].writer == "W1"
    # 단일값 전부를 한 번에 대조한다(상한 없음) — 조회는 클래스마다 링크 한 번 + 입력마다 한 번이고 읽기가 아니다
    # 이어 온 단일값이 있는 클래스(W1)의 링크 조회는 그 필드를 싣는다 — 어댑터가 그 필드 이름이 든 컴포넌트를 뒤 칸 앞에 둔다(R3-5 ④)
    assert lookup.calls == [("W0", {}, {}), ("W0", {"wg": "-", "lvl": "x"}, {}), ("W1", {"wg": "-"}, {})] and walk.reads == 4
    assert walk.hops[2].status == "나쁨 — 같은 단일값 wg='-' (아래 홉과 같다)" and walk.hops[2].bad                       # 조회 없이 이어진다
    # 멈춘 이유는 "쓴 쪽을 모른다"지만 리터럴 줄이 있으면 리드가 볼 곳은 그 줄의 함수다(사람 결정) — 입력은 그 입력 키의 읽기
    assert walk.stopped == "쓴 쪽을 모른다 — src:L1(값에 인덱스의 클래스 이름이 없다) (단일값이 든 비교식 proc/job.py:L50)"
    assert walk.next_hop() == ("code.read", {"service": "a", "path": "proc/job.py", "line": 50}) and walk.next_evidence == "t-3.e1"
    # 거르는 줄이 없으면 표시만 — 정상이고, 통과한 필드와 본 범위를 적는다
    rows2 = [{"code": f"A{i}", "lvl": "x"} for i in range(3)]
    plain = _Reads({**table, ("redis.get", "in:L1"): {"type": "string", "value": json.dumps({"record": rows2})}})
    walk = (await run_walk(_state(), runner=plain, classes=frozenset({"W0"}), class_services={"W0": ("a",)}, lookup=lookup, clock=lambda: T0))["walk"]
    assert walk.hops[1].status == "정상 — record: 행 3 · 필드 2 · 모든 행이 같은 값: lvl='x' · 단일값 lvl='x' — 거르는 줄 없음(1/1 대조, 1파일)" and not walk.hops[1].bad
    assert ("W0", {"lvl": "x"}, {}) in lookup.calls and walk.stopped == "입력이 다 정상 — W0"
    assert walk.next_hop() == ("code.read", {"service": "a", "path": "x.py", "line": 9})             # 입력이 다 정상 → 읽는 메서드 전체(R3-4 ②)
    # 가지의 인자(service·repo)가 조회에 그대로 간다 — 같은 이름이 두 레포면 그 레포의 파일이어야 한다
    two = _Lookup({})
    await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0", "W1"}), class_services={"W0": ("a", "b")}, declared_by={"alarm:stats:{line}": ("b",)},
                   lookup=two, clock=lambda: T0)
    assert two.calls[0] == ("W0", {}, {"service": "b"}) and two.calls[1] == ("W0", {"wg": "-", "lvl": "x"}, {"service": "b"})


async def test_이은_컴포넌트_클래스_본문은_멈춘_자리의_뒤_칸이_되고_못_이음은_일부만_검사로_정상이라_하지_않는다():
    """사람 결정(10-14·10-15): 입력이 정상에서 멈추면 읽는 메서드 전체 + `get("X")`↔`register(component="X")(<클래스>)`로 이은 **클래스 본문**이 다음
    칸들이다(등록 줄이 아니라 — 결정적인 줄은 컴포넌트에 있다). 링크는 단일값과 무관하게 클래스 파일에서 뽑는다(자원 0인 클래스도). 못 이은 컴포넌트가
    하나라도 있으면 `N=M`이라도 "일부만 검사"이고 행은 정상이 아니라 미확인, 멈춤은 `입력을 다 못 봤다`. 조회 실패는 `일부만 검사(0/M)`."""
    rows = [{"code": f"A{i}", "lvl": "x", "n": 1} for i in range(3)]
    table = {("code.resources", "W0"): _res("W0", [("in:{line}", "rediskey")]),
             ("redis.get", "in:L1"): {"type": "string", "value": json.dumps({"record": rows})}}
    at = FilterHit(path="comp/filters.py", line=7, text="class StateFilter", repo="r")
    base, lvl = ("W0", ()), ("W0", (("lvl", "x"),))
    linked = _Lookup({base: FilterScan(files=["w0.py", "comp/filters.py"], linked=["state_filter"], linked_at=[at]),
                      lvl: FilterScan(files=["w0.py", "comp/filters.py"], linked=["state_filter"], linked_at=[at], checked=["lvl"])})
    walk = (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0"}), class_services={"W0": ("a",)}, lookup=linked, clock=lambda: T0))["walk"]
    # 문자열 아닌 단일값(n=1)은 대조 밖 — 따로 센다
    assert walk.hops[1].status.endswith("· 단일값 lvl='x' — 거르는 줄 없음(1/1 대조, 2파일, 이은 컴포넌트 state_filter) · 대조 안 함 1개(문자열 아님)")
    assert walk.stopped == "입력이 다 정상 — W0"
    assert walk.rungs == [Rung(action="code.read", params={"service": "a", "path": "x.py", "line": 9}, evidence="t-2.e1"),
                          Rung(action="code.read", params={"service": "a", "path": "comp/filters.py", "line": 7}, evidence="t-2.e1")]
    varied = _Lookup({lvl: FilterScan(files=["w0.py"], unlinked=["변수 name"], checked=["lvl"])})
    walk = (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0"}), class_services={"W0": ("a",)}, lookup=varied, clock=lambda: T0))["walk"]
    assert walk.hops[1].status.startswith("미확인 — record: 행 3") and "— 일부만 검사(1/1, 못 이음: 변수 name)" in walk.hops[1].status and not walk.hops[1].bad
    assert walk.stopped == "입력을 다 못 봤다 — W0(일부만 검사)" and len(walk.rungs) == 1
    broken = _Lookup({lvl: FilterScan(problem="dt-core의 스냅샷을 못 받았다")})
    walk = (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0"}), class_services={"W0": ("a",)}, lookup=broken, clock=lambda: T0))["walk"]
    assert "· 단일값 lvl='x' — 일부만 검사(0/1): dt-core의 스냅샷을 못 받았다" in walk.hops[1].status and walk.hops[1].status.startswith("미확인")
    assert walk.stopped == "입력을 다 못 봤다 — W0(일부만 검사)"
    # 조회가 없으면(인덱스 없음) 검사도 없다 — 행 요약의 단일값 표시만
    walk = (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0"}), class_services={"W0": ("a",)}, clock=lambda: T0))["walk"]
    assert walk.hops[1].status == "정상 — record: 행 3 · 필드 3 · 모든 행이 같은 값: lvl='x', n=1"


async def test_리터럴_줄을_잡고도_위가_다_정상이면_다음_홉은_그_코드_줄_둘레다():
    """걷기가 멈췄는데 아래에 단일값이 든 비교식이 있었으면, 리드가 볼 곳은 그 줄을 품은 **함수**다(사람 결정 — ④-a: `code.read line=`이
    인덱스의 심볼 줄 범위로 함수 전체를 준다)."""
    rows = [{"code": f"A{i}", "wg": "-"} for i in range(3)]
    table = {("code.resources", "W0"): _res("W0", [("in:{line}", "rediskey")]),
             ("redis.get", "in:L1"): {"type": "string", "value": json.dumps({"record": rows, "metadata": {"event_type": "W1"}})},
             ("code.resources", "W1"): _res("W1", [("src:{line}", "rediskey")]),
             ("redis.get", "src:L1"): {"type": "string", "value": json.dumps([{"id": i, "v": i} for i in range(4)])}}
    lookup = _Lookup({("W0", (("wg", "-"),)): FilterScan(hits=[HIT], checked=["wg"], files=["proc/job.py"])})
    walk = (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0", "W1"}), class_services={"W0": ("a",), "W1": ("a",)},
                           lookup=lookup, clock=lambda: T0))["walk"]
    assert walk.stopped == "입력이 다 정상 — W1 (단일값이 든 비교식 proc/job.py:L50)"
    assert walk.next_hop() == ("code.read", {"service": "a", "path": "proc/job.py", "line": 50}) and walk.next_evidence == "t-3.e1"
    # 리터럴 줄의 함수가 첫 칸, 멈춘 자리(W1 — 입력이 다 정상)의 읽는 메서드 전체가 둘째 칸(R3-4 ②)
    assert walk.rungs[1] == Rung(action="code.read", params={"service": "a", "path": "x.py", "line": 9}, evidence="t-4.e1") and len(walk.rungs) == 2
    # 케이스 블록의 다음 홉 줄은 인자가 `name`이 아니어도 그려야 한다 — 측정판에서 frame 노드가 KeyError로 죽었다(규율 1)
    from src.application.intake_map import hop_line
    line = hop_line(_state().target, code_index=True, walk=walk)
    assert line.startswith("다음 홉: code.read line=50 path='proc/job.py' service='a' — 걷기가 멈춘 자리(입력이 다 정상 — W1")
    # 읽는 쪽 서비스를 모르면 조회는 인자 없이 가고 `code.read`는 서비스가 필요해 칸이 없다
    free = _Lookup(lookup.table)
    walk = (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0", "W1"}), lookup=free, clock=lambda: T0))["walk"]
    assert free.calls[1] == ("W0", {"wg": "-"}, {}) and walk.next_hop() is None and "proc/job.py:L50" in walk.stopped
    # 서비스를 모르고 쓴 쪽도 모르면 `code.uses`가 남는다
    unknown = _Reads({**table, ("redis.get", "src:L1"): {"type": "string", "value": "[]"}})
    walk = (await run_walk(_state(), runner=unknown, classes=frozenset({"W0", "W1"}), lookup=free, clock=lambda: T0))["walk"]
    assert walk.next_hop() == ("code.uses", {"name": "src:{line}"}) and walk.stopped.startswith("0행 — src:L1(값이 비어")


async def test_한_줄에_짝이_둘이면_한_줄로_센다():
    """측정판 c-2형(R3-5 ④): `r.get("state") != "-" and r.get("wg") != "-"` 한 줄에 짝이 둘이다 — 짝 수로 세면 "외 1줄"이 없는 줄을 말한다."""
    rows = [{"code": f"A{i}", "wg": "-", "st": "-"} for i in range(3)]
    table = {("code.resources", "W0"): _res("W0", [("in:{line}", "rediskey")]), ("redis.get", "in:L1"): _value(record=rows)}
    one = 'r for r in rows if r["st"] != "-" and r["wg"] != "-"'
    pair = [FilterHit(path="p.py", line=5, text=one, repo="r", field=f, value="-") for f in ("st", "wg")]
    other = FilterHit(path="q.py", line=9, text='x["wg"] != "-"', repo="r", field="wg", value="-")
    for hits, tail in ((pair, f"p.py:L5 '{one}'"), (pair + [other], f"p.py:L5 '{one}' 외 1줄")):
        lookup = _Lookup({("W0", (("st", "-"), ("wg", "-"))): FilterScan(hits=hits, checked=["wg", "st"], files=["p.py", "q.py"])})
        walk = (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0"}), class_services={"W0": ("a",)}, lookup=lookup,
                               clock=lambda: T0))["walk"]
        assert walk.hops[1].status == f"나쁨 — 3행 전부 st='-' wg='-' · 이 필드 값이 든 비교식 {tail}"


# ── R3-5b ② — "비었다"는 한 판정, `입력이 다 정상`은 전부 읽고 전부 정상일 때만 ──

def test_빈_목록과_빈_객체는_한_판정이고_행이_든_목록이_하나라도_있으면_정상이다():
    """측정 #12: `<입력 키>`의 `record {}`를 정상이라 했다 — 걷기의 상태(`status_of`)와 대상 키 홉의 빈 자리(`_empty_lists`)가 따로 놀아 빈 객체를
    놓쳤다. 판정은 `empties` 하나(사람 결정 10-16): 객체 바로 아래에 행이 든 목록이 하나라도 있으면 정상, 없을 때만 빈 목록·빈 객체를 적는다."""
    from src.application.intake_map import empties, empty_text

    assert status_of(_value(metadata={"event_type": "X"}, record={})) == ("빈 객체 (record)", True)
    assert status_of({"type": "string", "value": "{}"}) == ("빈 객체", True)
    assert status_of(_value(a=[], b={})) == ("빈 배열 (a) · 빈 객체 (b)", True)
    assert status_of(_value(meta={}, rows=[{"x": 1}, {"x": 2}])) == ("정상 — rows: 행 2 · 필드 1", False)   # 행이 든 목록이 있으면 정상
    assert empties({"summary_info": {"g": "L1"}, "data": []}, "record[0]") == (["record[0].data"], [])
    assert empties({"data": {}, "rows": [{"x": 1}]}) == ([], []) and empties([]) == ([], [])
    assert empty_text([], []) == "" and empty_text(["a"], ["b", "c"]) == "빈 배열 (a) · 빈 객체 (b, c)"


async def test_대상_키_홉의_빈_객체도_같은_판정으로_나쁘다():
    state = _state(empty=())
    state = state.model_copy(update={"target": state.target.model_copy(update={"empty_objects": ["record[0].data"]})})
    walk = (await run_walk(state, runner=_Reads({("code.resources", "W0"): _res("W0", [])}), classes=frozenset({"W0"}), clock=lambda: T0))["walk"]
    assert walk.hops[0].status == "빈 객체 (record[0].data)" and walk.hops[0].bad


async def test_입력이_다_정상은_나열된_입력을_전부_읽고_전부_정상일_때만이고_읽은_입력이_없으면_모른다():
    """측정 #12(사람 결정 10-16): `<클래스>`는 인덱스 자원이 자기 출력뿐인데 "입력이 다 정상"이었다 — 입력 수를 자원 줄 수로 셌다. 읽기가 실패한
    입력도 나쁨이 아니라서 거기에 섞였다. 키 없음(None)은 읽기 실패가 아니다 — 사실 `없음`(나쁨)이고 둘을 섞지 않는다."""
    # 대상 키의 쓴 쪽이 읽는 자원이 대상 키(자기 출력)뿐 — 대상 홉은 정상이어도 "모른다", 다음 칸은 클래스 본문
    own = {("code.resources", "W0"): _res("W0", [("alarm:stats:{line}", "rediskey")])}
    walk = (await run_walk(_state(empty=()), runner=_Reads(own), classes=frozenset({"W0"}), class_services={"W0": ("a",)}, clock=lambda: T0))["walk"]
    assert walk.stopped == "입력을 모른다 — W0(인덱스 자원이 자기 출력뿐 · 포인터 없음)" and walk.rungs[0].params == {"service": "a", "path": "x.py", "line": 1}
    # 읽기 실패 — 그 행은 나쁨이 아니지만 멈춤은 "다 못 봤다"
    failed = {("code.resources", "W0"): _res("W0", [("in:{line}", "rediskey"), ("ok:{line}", "rediskey")]),
              ("redis.get", "ok:L1"): _value(rows=[{"a": 1}, {"a": 2}])}
    walk = (await run_walk(_state(empty=()), runner=_Reads(failed), classes=frozenset({"W0"}), class_services={"W0": ("a",)}, clock=lambda: T0))["walk"]
    assert walk.hops[1].status.startswith("읽기 실패") and not walk.hops[1].bad
    assert walk.stopped == "입력을 다 못 봤다 — W0(읽기 실패 in:L1)" and walk.rungs[0].params["line"] == 9           # 읽은 입력이 있다 → 읽는 메서드
    # 키 없음은 읽기 실패가 아니다 — 사실 `없음`, 나쁨(그 키에서 멈춘다)
    gone = {**failed, ("redis.get", "in:L1"): None}
    walk = (await run_walk(_state(empty=()), runner=_Reads(gone), classes=frozenset({"W0"}), class_services={"W0": ("a",)}, clock=lambda: T0))["walk"]
    assert walk.hops[1].status == "없음" and walk.hops[1].bad and walk.stopped == "키가 없다 — in:L1"
    # 다 읽고 다 정상일 때만 "입력이 다 정상"
    fine = {**failed, ("redis.get", "in:L1"): _value(rows=[{"a": 1}, {"a": 3}])}
    walk = (await run_walk(_state(empty=()), runner=_Reads(fine), classes=frozenset({"W0"}), class_services={"W0": ("a",)}, clock=lambda: T0))["walk"]
    assert walk.stopped == "입력이 다 정상 — W0"


async def test_못_채우는_템플릿을_다_못_읽었거나_scan이_실패하면_다_못_봤다():
    """scan이 돌려준 실재 키를 상한(다섯)에 걸려 다 못 읽었거나, 0개였거나, scan 자체가 실패하면 그 입력은 다 본 것이 아니다(사람 결정 10-16:
    `<템플릿> scan N개 중 M개`)."""
    n = SCAN_READS + 2
    many = {("code.resources", "W0"): _res("W0", [("x:{zone}", "rediskey")]), ("redis.scan", "x:*"): [f"x:{i}" for i in range(n)],
            **{("redis.get", f"x:{i}"): _value(rows=[{"a": i}, {"a": i + 1}]) for i in range(n)}}
    for table, why in ((many, f"x:{{zone}} scan {n}개 중 {SCAN_READS}개"), ({**many, ("redis.scan", "x:*"): []}, "x:{zone} scan 0개 중 0개"),
                       ({k: v for k, v in many.items() if k[0] != "redis.scan"}, "읽기 실패 x:{zone}(scan)")):
        walk = (await run_walk(_state(empty=()), runner=_Reads(table), classes=frozenset({"W0"}), class_services={"W0": ("a",)},
                               clock=lambda: T0))["walk"]
        assert walk.stopped == f"입력을 다 못 봤다 — W0({why})"


async def test_다른_클래스가_이미_읽은_입력은_그_읽기_결과를_따른다():
    """같은 입력을 두 클래스가 나열하면 두 번째는 다시 안 읽는다 — 그 입력의 읽기 결과(정상·실패)를 따라 센다(R3-5b ②). 안 세면 두 번째 클래스가
    "읽는 자원이 자기 출력뿐"이 되고, 실패를 안 따르면 "입력이 다 정상"이 된다."""
    table = {("code.resources", "W0"): _res("W0", [("in:{line}", "rediskey"), ("mid:{line}", "rediskey")]),
             ("redis.get", "in:L1"): _value(rows=[{"a": 1}, {"a": 2}]),
             ("redis.get", "mid:L1"): _value(record=[], metadata={"event_type": "W1"}),
             ("code.resources", "W1"): _res("W1", [("in:{line}", "rediskey")])}
    services = {"W0": ("a",), "W1": ("a",)}
    walk = (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0", "W1"}), class_services=services, clock=lambda: T0))["walk"]
    assert walk.hops[-1].key == "mid:L1" and walk.hops[-1].bad and walk.stopped == "입력이 다 정상 — W1"
    failed = {k: v for k, v in table.items() if k != ("redis.get", "in:L1")}
    walk = (await run_walk(_state(), runner=_Reads(failed), classes=frozenset({"W0", "W1"}), class_services=services, clock=lambda: T0))["walk"]
    assert walk.stopped == "입력을 다 못 봤다 — W1(읽기 실패 in:L1)"



async def test_걷기의_입력도_행_안의_클래스_이름을_쓴_쪽으로_안_삼는다():
    """R3-5c ①(측정 #12b P0 1) — 쓴 쪽 판정은 입구·걷기의 입력·`--key`가 같은 함수를 쓴다. 행 안의 값이 클래스 이름과 같아도 쓴 쪽이 아니다."""
    table = {("code.resources", "W0"): _res("W0", [("in:{line}", "rediskey")]),
             ("redis.get", "in:L1"): {"type": "string", "value": json.dumps([{"view": "W1"}, {"view": "W1"}])}}
    walk = (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0", "W1"}), class_services={"W0": ("a",)}, clock=lambda: T0))["walk"]
    assert walk.hops[1].key == "in:L1" and walk.hops[1].writer == ""



# ── R3-5d ①-a — 레포 가르기는 모든 홉에, 값 없는 홉의 멈춤 문구 ──

async def test_정상_홉의_쓴_쪽도_키_선언으로_레포를_가르고_못_가르면_둘_다_후보다():
    """측정 #13 고침 1: 걷기는 나쁜 홉에서만 레포를 갈랐다 — 정상 홉의 쓴 쪽은 두 레포의 서비스 전부가 후보로 실렸고, 판정 번역이 그 목록을 "가른
    결과"로 믿어 첫 것(`<수집기>`)을 골랐다. 정상 홉도 그 키의 선언으로 가른다(표의 쓴 쪽 칸·후보·주 줄). 못 가르면 "둘 다 후보"."""
    # `q:{z}`는 못 채우는 템플릿 — scan한 실재 키 둘의 쓴 쪽이 같은 두 레포 클래스라 주 줄이 두 번 나올 자리(한 번만 적어야 한다)
    table = {("code.resources", "W0"): _res("W0", [("in:{line}", "rediskey"), ("pal", "rediskey"), ("q:{z}", "rediskey")]),
             ("redis.get", "in:L1"): _value(rows=[{"a": 1}, {"a": 2}], metadata={"event_type": "W1"}),
             ("redis.get", "pal"): _value(colors=[{"c": 1}, {"c": 2}], metadata={"event_type": "W2"}),
             ("redis.scan", "q:*"): ["q:1", "q:2"],
             **{("redis.get", k): _value(rows=[{"a": 1}, {"a": 2}], metadata={"event_type": "W2"}) for k in ("q:1", "q:2")}}
    repos = {"api": "dt-api", "p1": "dt-core", "p2": "dt-core", "p3": "dt-core"}
    services = {"W0": ("p1",), "W1": ("api", "p1", "p2", "p3"), "W2": ("api", "p1", "p2", "p3")}
    walk = (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0", "W1", "W2"}), class_services=services, service_repos=repos,
                           declared_by={"in:{line}": ("p1", "p2", "p3"), "pal": ("api", "p1")}, clock=lambda: T0))["walk"]
    by_key = {h.key: h for h in walk.hops}
    assert (by_key["in:L1"].service, by_key["in:L1"].candidates) == ("dt-core(서비스 3개 공유)", ["p1", "p2", "p3"])      # 선언으로 골랐다
    assert (by_key["pal"].service, by_key["pal"].candidates) == ("api, dt-core(서비스 3개 공유)", ["api", "p1", "p2", "p3"])   # 못 가른다 — 둘 다
    assert walk.notes == ["W1는 레포 2곳(api, dt-core(서비스 3개 공유))에 있다 — in:{line}을 선언한 dt-core(서비스 3개 공유)로 골랐다",
                          "W2는 레포 2곳(api, dt-core(서비스 3개 공유))에 있다 — pal을 선언한 쪽으로 못 가른다(둘 다 선언): 둘 다 후보",
                          "W2는 레포 2곳(api, dt-core(서비스 3개 공유))에 있다 — q:{z}을 선언한 쪽으로 못 가른다(선언 없음): 둘 다 후보"]   # q:1·q:2에 한 번
    assert walk.stopped == "입력이 다 정상 — W0" and walk.reads == 6                                                       # 정상 홉은 걷지 않는다


async def test_값이_없는_홉에서_멈추면_키가_없다_또는_0행이라_적는다():
    """측정 #13 추가분 4: 키가 없거나 컬렉션이 0행이어도 "값에 인덱스의 클래스 이름이 없다"였다 — 값이 없으면 볼 것도 없다. 다음 칸은 그대로
    `code.uses`(그 키를 누가 쓰나)."""
    base = {("code.resources", "W0"): _res("W0", [("in:{line}", "rediskey"), ("docs", "collection")])}
    walk = (await run_walk(_state(), runner=_Reads({**base, ("redis.get", "in:L1"): None, ("mongo.find", "docs"): []}), classes=frozenset({"W0"}),
                           clock=lambda: T0))["walk"]
    assert walk.stopped == "키가 없다 — in:L1" and walk.next_hop() == ("code.uses", {"name": "in:{line}"})
    walk = (await run_walk(_state(), runner=_Reads({**base, ("redis.get", "in:L1"): _value(n=1), ("mongo.find", "docs"): []}), classes=frozenset({"W0"}),
                           clock=lambda: T0))["walk"]
    assert walk.stopped == "0행 — docs(값이 비어 쓴 쪽을 알 수 없다)" and walk.next_hop() == ("code.uses", {"name": "docs"})
    walk = (await run_walk(_state(), runner=_Reads({**base, ("redis.get", "in:L1"): _value(record=[]), ("mongo.find", "docs"): [{"a": 1}]}),
                           classes=frozenset({"W0"}), clock=lambda: T0))["walk"]
    assert walk.stopped == "쓴 쪽을 모른다 — in:L1(값에 인덱스의 클래스 이름이 없다)"                                    # 값은 있는데 이름이 없다



# ── R3-5d ④ — 키를 만든 메시지 한 건을 입력으로(포인터 홉) ──

_POINTER = {"topic": "t", "partition": 0, "offset": 5, "at": "metadata.source"}
_NONE_RES = "W0 (x.py:L1) [dt-core · a]\n  읽거나 쓰는 자원이 인덱스에 없다 — 메서드 2개"


def _msg(value, *, found=True, begin=0, end=9):
    """`kafka.message_at`의 원본 모양(실어댑터·stub과 같다)."""
    return {"topic": "t", "partition": 0, "offset": 5, "begin": begin, "end": end, "found": found, "bytes": 30 if found else 0,
            "message": {"partition": 0, "offset": 5, "timestamp": None, "key": None, "value": value} if found else None,
            **({} if found else {"note": "보존 범위 밖"})}


def _pointed(state=None):
    state = state or _state()
    return state.model_copy(update={"target": state.target.model_copy(update={"pointer": dict(_POINTER)})})


async def test_포인터가_있으면_그_메시지_한_건을_입력으로_읽고_정상이면_다_정상_비었으면_거기서_멈춘다():
    """측정 #13 추가분 2(사람 결정 10-19): 메시지 입력은 인덱스가 못 세고 키 값의 포인터가 그 키를 만든 메시지를 가리킨다 — 한 건이면 충분하다. 발행 쪽은
    클래스가 아니라 config 발행 선언(같은 이름 클래스가 두 레포), 없으면 우리 밖. 그 메시지로 더 걷지는 않는다. 메시지만 읽은 클래스의 다음 칸은 본문."""
    rows = [{"ws": "A", "n": 1}, {"ws": "B", "n": 2}]
    table = {("code.resources", "W0"): _NONE_RES, ("kafka.message_at", "t"): _msg({"line": "L1", "rows": rows})}
    runner = _Reads(table)
    walk = (await run_walk(_pointed(), runner=runner, classes=frozenset({"W0"}), class_services={"W0": ("a",)}, topic_producers={"t": ("col", "pub")},
                           clock=lambda: T0))["walk"]
    assert runner.params[-1] == ("kafka.message_at", {"topic": "t", "partition": 0, "offset": 5})
    hop = walk.hops[-1]
    assert (hop.key, hop.kind, hop.writer, hop.service, hop.candidates, hop.status, hop.bad, hop.parent) == (
        "t[0]@5", "message", "", "발행 선언: col, pub", ["col", "pub"], "정상 — rows: 행 2 · 필드 2", False, "t-1.e1")
    assert hop.basis == "W0를 댄 키 값의 포인터 metadata.source (t-1.e1)" and hop.line() == "  t[0]@5 | 발행 선언: col, pub | 정상 — rows: 행 2 · 필드 2 | t-3.e1"
    assert walk.stopped == "입력이 다 정상 — W0" and walk.reads == 2
    assert walk.rungs == [Rung(action="code.read", params={"service": "a", "path": "x.py", "line": 1}, evidence="t-2.e1")]
    # 비었으면(봉투 없는 목록도 맨 위 목록으로 센다) 거기서 멈춘다 — 발행 선언이 없으면 우리 밖. 다음 칸: 그 메시지를 받는 클래스 본문 → 지금 오는 것
    empty = _Reads({**table, ("kafka.message_at", "t"): _msg([])})
    walk = (await run_walk(_pointed(), runner=empty, classes=frozenset({"W0"}), class_services={"W0": ("a",)}, clock=lambda: T0))["walk"]
    assert (walk.hops[-1].status, walk.hops[-1].bad, walk.hops[-1].service, walk.hops[-1].candidates) == ("0행", True, "발행 선언 없음(우리 밖)", [])
    assert walk.stopped == "입력 메시지가 나쁘다 — W0의 t[0]@5(발행 선언 없음(우리 밖))" and walk.stop_at == "t-3.e1"
    assert walk.rungs == [Rung(action="code.read", params={"service": "a", "path": "x.py", "line": 1}, evidence="t-2.e1"),
                          Rung(action="kafka.tail", params={"topic": "t", "limit": 5}, evidence="t-3.e1")]
    assert walk.chain_ids() == frozenset({"t-1.e1", "t-3.e1"})


async def test_포인터의_메시지가_보존_범위_밖이면_키를_만든_메시지가_토픽에_없다고_멈추고_다음_칸은_지금_오는_것이_먼저다():
    """측정 #13 추가분 2: 범위 밖 2개가 둘 다 진짜 원인 자리 — 읽기 실패가 아니라 사실 `없음`(나쁨, 키 없음과 같은 종류)."""
    table = {("code.resources", "W0"): _NONE_RES, ("kafka.message_at", "t"): _msg(None, found=False, begin=100, end=105)}
    walk = (await run_walk(_pointed(), runner=_Reads(table), classes=frozenset({"W0"}), class_services={"W0": ("a",)}, topic_producers={"t": ("col",)},
                           clock=lambda: T0))["walk"]
    assert walk.hops[-1].status == "없음 — 키를 만든 메시지가 토픽에 없다(보존 범위 100~105)" and walk.hops[-1].bad
    assert walk.stopped == "키를 만든 메시지가 토픽에 없다 — W0의 t[0]@5(보존 범위 100~105 · 발행 선언: col)"
    assert [r.action for r in walk.rungs] == ["kafka.tail", "code.read"] and walk.rungs[0].params == {"topic": "t", "limit": 5}


async def test_포인터가_없으면_그_사실만_적고_나쁜_입력의_쓴_쪽으로_내려가면_그_키_값의_포인터를_따라간다():
    """포인터가 없으면 토픽을 짐작하지 않는다(사람 결정 10-19: "포인터 없음"만). 아래 홉의 쓴 쪽은 **그 키 값**의 포인터로 메시지를 읽는다."""
    none = _Reads({("code.resources", "W0"): _NONE_RES})
    walk = (await run_walk(_state(), runner=none, classes=frozenset({"W0"}), class_services={"W0": ("a",)}, clock=lambda: T0))["walk"]
    assert walk.stopped == "입력을 모른다 — W0(인덱스 자원 0 — 메시지 입력일 수 있다 · 포인터 없음)" and walk.reads == 1
    table = {("code.resources", "W0"): _res("W0", [("in:{line}", "rediskey")]),
             ("redis.get", "in:L1"): _value(record=[], metadata={"event_type": "W1", "source": {"topic": "t", "partition": 0, "offset": 5}}),
             ("code.resources", "W1"): "W1 (y.py:L1) [dt-core · a]\n  읽거나 쓰는 자원이 인덱스에 없다 — 메서드 1개",
             ("kafka.message_at", "t"): _msg({"rows": [{"a": 1}]})}
    runner = _Reads(table)
    walk = (await run_walk(_state(), runner=runner, classes=frozenset({"W0", "W1"}), class_services={"W0": ("a",), "W1": ("a",)}, clock=lambda: T0))["walk"]
    assert [(h.key, h.parent) for h in walk.hops] == [("alarm:stats:L1", ""), ("in:L1", "t-1.e1"), ("t[0]@5", "t-3.e1")]
    assert walk.stopped == "입력이 다 정상 — W1" and ("kafka.message_at", "t") in runner.ran


async def test_메시지의_단일값도_읽는_쪽_코드_줄로_판정하고_그_줄의_함수가_첫_칸이다():
    """사내 `<작업자 배지>`의 모양: 메시지는 오는데 한 필드가 전부 같은 값이라 처리기가 거른다 — 키 값과 같은 단일값 판정(③)을 메시지에도."""
    rows = [{"ws": "-", "n": i} for i in range(3)]
    hit = FilterHit(path="p.py", line=7, text='if row.get("ws") != "-"', repo="r", field="ws", value="-")
    lookup = _Lookup({("W0", (("ws", "-"),)): FilterScan(files=["w0.py", "p.py"], checked=["ws"], hits=[hit])})
    table = {("code.resources", "W0"): _NONE_RES, ("kafka.message_at", "t"): _msg({"rows": rows})}
    walk = (await run_walk(_pointed(), runner=_Reads(table), classes=frozenset({"W0"}), class_services={"W0": ("a",)}, lookup=lookup, clock=lambda: T0))["walk"]
    assert walk.hops[-1].status == "나쁨 — 3행 전부 ws='-' · 이 필드 값이 든 비교식 p.py:L7 'if row.get(\"ws\") != \"-\"'" and walk.hops[-1].bad
    assert walk.stopped == "입력 메시지가 나쁘다 — W0의 t[0]@5(발행 선언 없음(우리 밖)) (단일값이 든 비교식 p.py:L7)"
    assert [(r.action, r.params.get("line") or r.params.get("topic")) for r in walk.rungs] == [("code.read", 7), ("code.read", 1), ("kafka.tail", "t")]


async def test_포인터_메시지_읽기가_실패하면_다_못_봤다():
    """읽기 실패는 모르는 것이지 비어 있는 것이 아니다(R3-5b ②) — 메시지도 같다."""
    walk = (await run_walk(_pointed(), runner=_Reads({("code.resources", "W0"): _NONE_RES}), classes=frozenset({"W0"}), class_services={"W0": ("a",)},
                           clock=lambda: T0))["walk"]
    assert walk.hops[-1].status == "읽기 실패 — 대본 없음" and not walk.hops[-1].bad
    assert walk.stopped == "입력을 다 못 봤다 — W0(읽기 실패 t[0]@5)"


async def test_시간_상한은_메시지_읽기에_쓴_시간을_빼고_잰다():
    """측정 #15 메모: 메시지 한 건 읽기(브로커 왕복)로 걷기 두 건이 53초 — 30초 상한에 걸렸다. 그 시간은 상한 밖이다(읽기 수 상한은 그대로 — 세는 것이지
    재는 것이 아니다). 같은 시간이 키 읽기에 들면 전처럼 상한이다. `seconds`는 실제로 걸린 시간 그대로."""
    class Clock:
        def __init__(self):
            self.n = 0

        def __call__(self):
            return T0 + timedelta(seconds=20 * self.n)

    class Slow(_Reads):
        def __init__(self, table, clock, slow):
            super().__init__(table)
            self.clock, self.slow = clock, slow

        async def run(self, task, *, case):
            if task.action == self.slow:
                self.clock.n += 2                                                            # 그 읽기 한 번에 40초
            return await super().run(task, case=case)

    table = {("code.resources", "W0"): _res("W0", [("in:a:{line}", "rediskey")]),
             ("redis.get", "in:a:L1"): _value(record=[], metadata={"event_type": "W1"}),
             ("kafka.message_at", "t"): _msg({"rows": [{"ws": "A", "n": 1}]}),
             ("code.resources", "W1"): _res("W1", [])}
    clock = Clock()
    walk = (await run_walk(_pointed(), runner=Slow(table, clock, "kafka.message_at"), classes=frozenset({"W0", "W1"}), clock=clock))["walk"]
    assert "상한" not in walk.stopped and walk.stopped.startswith("입력을 모른다 — W1") and walk.seconds >= 40
    assert [h.key for h in walk.hops] == ["alarm:stats:L1", "in:a:L1", "t[0]@5"]
    clock = Clock()
    walk = (await run_walk(_pointed(), runner=Slow(table, clock, "redis.get"), classes=frozenset({"W0", "W1"}), clock=clock))["walk"]
    assert walk.stopped == f"{MAX_SECONDS:.0f}초 상한 — W0의 메시지부터는 리드가"


async def test_메시지_읽기가_한_번_실패하면_같은_걷기의_다음_메시지_읽기는_건너뛴다():
    """측정 #17 메모: kafka 접속 실패는 메시지 한 건에 50~60초이고 시간 상한 밖(R3-5e ⑥)이라 c-9 걷기가 135초였다. 한 걷기에서 한 번 실패하면 다시 안 붙는다 —
    건너뛴 홉은 읽기 실패로 적고(사유에 앞 실패), 읽기 수·증거에 안 센다. 멈춤은 전처럼 "입력을 다 못 봤다"."""
    table = {("code.resources", "W0"): _res("W0", [("in:a:{line}", "rediskey")]),
             ("redis.get", "in:a:L1"): _value(record=[], metadata={"event_type": "W1", "source": {"topic": "t", "partition": 0, "offset": 6}}),
             ("code.resources", "W1"): _res("W1", [])}
    runner = _Reads(table)
    walk = (await run_walk(_pointed(), runner=runner, classes=frozenset({"W0", "W1"}), clock=lambda: T0))["walk"]
    assert [r for r in runner.ran if r[0] == "kafka.message_at"] == [("kafka.message_at", "t")]              # 두 번째는 안 읽었다
    first, second = [h for h in walk.hops if h.kind == "message"]
    assert first.key == "t[0]@5" and first.status == "읽기 실패 — 대본 없음"
    assert second.key == "t[0]@6" and second.status == "읽기 실패 — 앞 메시지 읽기가 실패해 건너뜀(대본 없음)" and second.evidence_id == ""
    assert walk.stopped == "입력을 다 못 봤다 — W1(읽기 실패 t[0]@6)" and walk.reads == 4


# ── R3-5g ④-b — 조인 검사: 병합식의 키 필드로 두 입력의 값이 겹치는지 ──

def test_join_check는_병합식의_키_필드를_가진_두_입력의_값이_하나도_안_겹칠_때만_짝_없음이다():
    """P1-5(사람 결정 10-20): 코드 비교식이 아니라 양쪽 데이터의 키 차이로만 보이는 거름 — 병합식(AST 사실)의 필드로 읽은 두 입력의 값 집합을 센다. 변수를
    따라가지 않는다: 왼쪽 키 필드를 전부 가진 입력이 왼쪽, 오른쪽 키 필드를 가진 다른 입력이 오른쪽. 한쪽이 비었거나 겹치는 값이 하나라도 있으면 아니다."""
    from src.application.walk import join_check
    from src.domain.case import MergeHit

    m = MergeHit(path="j.py", line=7, text="x.merge(y, left_on='ws', right_on='code')", left=["ws"], right=["code"])
    events = ("ev:L1", "t-2.e1", [{"ws": "", "n": 1}, {"ws": "", "n": 2}], "", None, "ev:{line}")
    ref = ("ref", "t-3.e1", [{"code": "A1"}, {"code": "A2"}, {"code": "A3"}, {"code": "A4"}], "", None, "ref")
    other = ("st:L1", "t-4.e1", [{"id": 1}], "", None, "st:{line}")
    got = join_check([m], [events, ref, other])
    assert [(a, b, note) for a, b, note, _ in got] == [(("ev:L1", "t-2.e1"), ("ref", "t-3.e1"),
                                                        "ev:L1.ws {''} ↔ ref.code {'A1', 'A2', 'A3', …} 겹침 0 (inner 병합)")]
    assert join_check([m], [("ev:L1", "t-2.e1", [{"ws": "A2"}], "", None, "ev:{line}"), ref]) == []            # 하나라도 겹치면 아니다
    assert join_check([m], [("ev:L1", "t-2.e1", [], "", None, "ev:{line}"), ref]) == []                          # 빈 쪽은 겹침을 셀 수 없다
    assert join_check([m], [events]) == [] and join_check([], [events, ref]) == []
    same = MergeHit(path="j.py", line=9, text="x.merge(y, on=['ws', 'd'])", left=["ws", "d"], right=["ws", "d"])
    a = ("a", "t-2.e1", [{"ws": "x", "d": 1}], "", None, "a")
    b = ("b", "t-3.e1", [{"ws": "x", "d": 2}], "", None, "b")
    pairs = join_check([same], [a, b])
    assert len(pairs) == 1 and pairs[0][2] == "a.ws+d {('x', 1)} ↔ b.ws+d {('x', 2)} 겹침 0 (inner 병합)"        # 같은 필드면 한 쌍만, 키는 묶음


async def test_걷기는_병합식의_두_입력이_안_겹치면_둘_다_나쁨으로_적고_쓴_쪽으로_한_홉_더_가며_멈춤은_병합_0건이다():
    """사내 작업자 배지: 원천의 빈 작업대 코드가 기준표에 없어 inner 병합이 0건 — 입력 둘은 각각 정상이다. 병합식은 어댑터(`FilterScan.merges`)가 주고 걷기가
    값을 센다. 첫 칸은 병합 줄, 그다음은 전처럼 그 입력의 칸. 나빠진 입력에 아는 쓴 쪽이 있으면 한 홉 더 간다."""
    from src.domain.case import MergeHit

    table = {("code.resources", "W0"): _res("W0", [("in:a:{line}", "rediskey"), ("ref:{line}", "rediskey")]),
             ("redis.get", "in:a:L1"): _value(metadata={"event_type": "W1"}, rows=[{"ws": "", "n": 1}, {"ws": "", "n": 2}]),
             ("redis.get", "ref:L1"): _value(codes=[{"ws": "A"}, {"ws": "B"}]),
             ("code.resources", "W1"): _res("W1", [("up:{line}", "rediskey")]),
             ("redis.get", "up:L1"): _value(rows=[{"ws": "", "n": 1}])}
    merge = MergeHit(path="w0.py", line=5, text="pd.merge(a, b, on='ws')", left=["ws"], right=["ws"])
    lookup = _Lookup({("W0", ()): FilterScan(files=["w0.py"], merges=[merge])})
    walk = (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0", "W1"}), lookup=lookup, clock=lambda: T0))["walk"]
    by_key = {h.key: h for h in walk.hops}
    note = "in:a:L1.ws {''} ↔ ref:L1.ws {'A', 'B'} 겹침 0 (inner 병합)"
    assert by_key["in:a:L1"].bad and by_key["in:a:L1"].status.startswith(f"나쁨 — 병합 짝 없음: {note} (w0.py:L5) · rows: 행 2")
    assert by_key["ref:L1"].bad and by_key["ref:L1"].status.startswith(f"나쁨 — 병합 짝 없음: {note} (w0.py:L5) · codes: 행 2")
    assert by_key["in:a:L1"].writer == "W1" and by_key["up:L1"].parent == by_key["in:a:L1"].evidence_id    # 쓴 쪽으로 한 홉 더
    assert walk.stopped == "입력이 다 정상 — W1"                                                                  # 더 깊은 홉이 정상이라 멈춤은 거기
    # 쓴 쪽을 모르는 쪽이 멈춘 자리면 사유는 병합 0건이고 첫 칸은 병합 줄
    flat = {k: v for k, v in table.items() if k[1] != "W1"}
    flat[("redis.get", "in:a:L1")] = _value(rows=[{"ws": "", "n": 1}, {"ws": "", "n": 2}])
    walk = (await run_walk(_state(), runner=_Reads(flat), classes=frozenset({"W0"}), lookup=lookup, clock=lambda: T0))["walk"]
    assert walk.stopped == f"병합이 0건 — W0: {note} (w0.py:L5)"
    assert [r.action for r in walk.rungs][:2] == ["code.read", "code.uses"] and walk.rungs[0].params == {"service": "", "path": "w0.py", "line": 5}
