"""R3-3 ② — 코드가 상류를 걷는다. 입구의 고리(키 값의 잎 = 클래스 → `code.resources` → 자원 키 읽기)를 키마다 반복하고, 리드는 표를 받는다.
나쁨은 없음·빈 배열·0행뿐(사람 결정 10-14 — 손 규칙 없음). 값이 그 클래스를 대는 키는 출력이라 입력에서 뺀다(측정 #9 P0 3)."""
import json
from datetime import timedelta

from src.application.state import CaseState
from src.application.walk import MAX_DEPTH, MAX_READS, MAX_SECONDS, SCAN_READS, literal_lines, run_walk, status_of
from src.domain.case import Case, EvidenceRef, TargetPick
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
    # 읽기 7 = 자원 2 + 입력 4 + src:L1의 단일값 d='-'에 grep 한 번(③ — 리터럴 줄이 없어 정상으로 남는다)
    assert walk.reads == 7 and [t.id for t in patch["plan_tasks"]] == [f"t-1-{i}" for i in range(1, 8)]
    assert {e.id for e in patch["evidence"]} == {f"t-1-{i}.e1" for i in range(1, 7)} and ("code.grep", "d") in runner.ran   # grep은 대본에 없어 증거가 없다
    assert ("redis.get", "alarm:stats:L1") not in runner.ran and ("redis.get", "x:L1") not in runner.ran     # 대상 키·쓰기 구간은 안 읽는다
    assert walk.hops[1].evidence_id == "t-1-2.e1" and walk.bad_ids() == frozenset({"t-1.e1", "t-1-2.e1"})
    assert (walk.hops[0].service, walk.hops[1].service, walk.hops[2].kind, walk.hops[3].kind) == ("batch", "collector", "rediskey", "collection")
    assert walk.hops[1].basis == "W0가 읽는 자원 (t-1-1.e1)" and walk.hops[0].basis == "대상 키 — 값의 metadata.event_type이 W0"
    assert walk.lines()[0] == "걷기(코드가 상류를 걸었다): 홉 5 · 읽기 7 · 0.0초 — 멈춤: 입력이 다 정상 — W1"
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
    assert walk.hops[0].status == "빈 배열 (record[0].data) · W0의 자원 못 읽음 — 대본 없음"              # 그 행에 적는다(R3-4 ①)


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
    assert (walk.hops[0].service, walk.hops[1].service) == ("a", "b") and walk.hops[1].basis == "W0[a]가 읽는 자원 (t-1-1.e1)"
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
    assert (walk.hops[0].service, walk.hops[1].service, walk.hops[1].basis) == ("a", "b", "W0가 읽는 자원 (t-1-1.e1)")


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
    assert walk.hops[1].basis == "W0[dt-proc(서비스 3개 공유)]가 읽는 자원 (t-1-1.e1)"
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
    assert walk.stopped == "W1의 자원을 못 읽었다 — 대본 없음" and walk.next_hop() is None and walk.next_evidence == "t-1-2.e1"


# ── R3-3 ③ — 단일값 필드의 나쁨은 코드 줄이 정한다 ──

GREP = ("c0ffee:proc/job.py:8:        if wg != \"-\":\n"                                              # 필드가 리터럴이 아니다 — 안 센다
        "c0ffee:proc/job.py:50:        rows = [r for r in rows if r[\"wg\"] != \"-\"]\n"               # 둘 다 리터럴 — 이 줄
        "c0ffee:proc/job.py:9:        wg = cfg.get(\"wg\")")


def test_리터럴_줄은_필드와_값이_둘_다_따옴표로_같은_줄에_있을_때다():
    assert literal_lines(GREP, "wg", "-") == [("proc/job.py", 50, 'rows = [r for r in rows if r["wg"] != "-"]')]
    assert literal_lines(GREP, "wg", "x") == [] and literal_lines("# dt-core @ c0ffee\n", "wg", "-") == []
    assert literal_lines("proc/a.py:3:    v = row['lvl'] == '-'", "lvl", "-") == [("proc/a.py", 3, "v = row['lvl'] == '-'")]    # 커밋 접두 없이도
    assert literal_lines("main:batch/r.py:7:    rows = [r for r in rows if r.get(\"state\") != \"-\"]", "state", "-") == [
        ("batch/r.py", 7, 'rows = [r for r in rows if r.get("state") != "-"]')]                                                # ref 머리(`main:`)로도


async def test_단일값_필드는_읽는_쪽_코드_줄에_필드와_값이_리터럴로_같이_있을_때만_나쁘고_그_값이_이어지는_동안_위로_간다():
    """측정 #9 c-2: 입력 키 554행의 두 필드가 전부 `'-'`였고, 읽는 쪽 코드에 `["<필드>"] != "-"` 줄이 있었다 — 코드가 그 값을 걸러 아무것도 안
    남는다. 사람 결정: 자리표시자 목록은 두지 않는다. 필드 이름과 값이 **한 줄에 리터럴로** 같이 있을 때만 나쁨이고 그 줄이 근거다. 잡히면 입력에
    같은 필드=같은 값이 있는 동안 위로(grep 없이), 나머지 단일값은 표시만. 읽는 쪽 서비스를 알면 그 레포만 본다."""
    rows = [{"code": f"A{i}", "wg": "-", "lvl": "x"} for i in range(3)]
    table = {("code.resources", "W0"): _res("W0", [("in:{line}", "rediskey")]),
             ("redis.get", "in:L1"): {"type": "string", "value": json.dumps({"record": rows, "metadata": {"event_type": "W1"}})},
             ("code.grep", "wg"): GREP, ("code.grep", "lvl"): "c0ffee:proc/job.py:12:        lvl = row['lvl']",
             ("code.resources", "W1"): _res("W1", [("src:{line}", "rediskey")]),
             ("redis.get", "src:L1"): {"type": "string", "value": json.dumps([{"id": i, "wg": "-"} for i in range(4)])}}
    runner = _Reads(table)
    walk = (await run_walk(_state(), runner=runner, classes=frozenset({"W0", "W1"}), class_services={"W0": ("a",)}, clock=lambda: T0))["walk"]
    assert walk.hops[1].status == "나쁨 — 모든 행이 wg='-', 코드가 거른다: proc/job.py:L50 `rows = [r for r in rows if r[\"wg\"] != \"-\"]` (t-1-3.e1)"
    assert walk.hops[1].bad and walk.hops[1].writer == "W1"
    assert ("code.grep", {"patterns": ["wg"], "service": "a"}) in runner.params and ("code.grep", "lvl") not in runner.ran   # 첫 리터럴에서 멈춘다
    assert walk.hops[2].status == "나쁨 — 같은 단일값 wg='-' (아래 홉과 같다)" and walk.hops[2].bad                       # grep 없이 이어진다
    # 멈춘 이유는 "쓴 쪽을 모른다"지만 리터럴 줄이 있으면 리드가 볼 곳은 그 줄이다(사람 결정)
    assert walk.stopped == "쓴 쪽을 모른다 — src:L1(값에 인덱스의 클래스 이름이 없다) (단일값을 거르는 코드 줄 proc/job.py:L50)"
    assert walk.next_hop() == ("code.read", {"service": "a", "path": "proc/job.py", "line": 50}) and walk.next_evidence == "t-1-3.e1"
    # 값이 리터럴로 없는 줄뿐이면 표시만 — 정상이고 행 요약에 남는다
    rows2 = [{"code": f"A{i}", "lvl": "x"} for i in range(3)]
    plain = _Reads({**table, ("redis.get", "in:L1"): {"type": "string", "value": json.dumps({"record": rows2})}})
    walk = (await run_walk(_state(), runner=plain, classes=frozenset({"W0"}), class_services={"W0": ("a",)}, clock=lambda: T0))["walk"]
    assert walk.hops[1].status == "정상 — record: 행 3 · 필드 2 · 모든 행이 같은 값: lvl='x'" and not walk.hops[1].bad
    assert ("code.grep", "lvl") in plain.ran and walk.stopped == "입력이 다 정상 — W0" and walk.next_hop() is None


async def test_리터럴_줄을_잡고도_위가_다_정상이면_다음_홉은_그_코드_줄_둘레다():
    """걷기가 멈췄는데 아래에 단일값을 거르는 코드 줄이 있었으면, 리드가 볼 곳은 그 줄을 품은 **함수**다(사람 결정 — ④-a: `code.read line=`이
    인덱스의 심볼 줄 범위로 함수 전체를 준다)."""
    rows = [{"code": f"A{i}", "wg": "-"} for i in range(3)]
    table = {("code.resources", "W0"): _res("W0", [("in:{line}", "rediskey")]),
             ("redis.get", "in:L1"): {"type": "string", "value": json.dumps({"record": rows, "metadata": {"event_type": "W1"}})},
             ("code.grep", "wg"): GREP,
             ("code.resources", "W1"): _res("W1", [("src:{line}", "rediskey")]),
             ("redis.get", "src:L1"): {"type": "string", "value": json.dumps([{"id": i, "v": i} for i in range(4)])}}
    walk = (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0", "W1"}), class_services={"W0": ("a",)}, clock=lambda: T0))["walk"]
    assert walk.stopped == "입력이 다 정상 — W1 (단일값을 거르는 코드 줄 proc/job.py:L50)"
    assert walk.next_hop() == ("code.read", {"service": "a", "path": "proc/job.py", "line": 50}) and walk.next_evidence == "t-1-3.e1"
    # 케이스 블록의 다음 홉 줄은 인자가 `name`이 아니어도 그려야 한다 — 측정판에서 frame 노드가 KeyError로 죽었다(규율 1)
    from src.application.intake_map import hop_line
    line = hop_line(_state().target, code_index=True, walk=walk)
    assert line.startswith("다음 홉: code.read line=50 path='proc/job.py' service='a' — 걷기가 멈춘 자리(입력이 다 정상 — W1")
    # 읽는 쪽 서비스를 모르면 grep은 전체이고 `code.read`는 서비스가 필요해 다음 홉이 없다
    free = _Reads(table)
    walk = (await run_walk(_state(), runner=free, classes=frozenset({"W0", "W1"}), clock=lambda: T0))["walk"]
    assert ("code.grep", {"patterns": ["wg"]}) in free.params and walk.next_hop() is None and "proc/job.py:L50" in walk.stopped
    # 서비스를 모르고 쓴 쪽도 모르면 `code.uses`가 남는다
    unknown = _Reads({**table, ("redis.get", "src:L1"): {"type": "string", "value": "[]"}})
    walk = (await run_walk(_state(), runner=unknown, classes=frozenset({"W0", "W1"}), clock=lambda: T0))["walk"]
    assert walk.next_hop() == ("code.uses", {"name": "src:{line}"}) and walk.stopped.startswith("쓴 쪽을 모른다 — src:L1")
