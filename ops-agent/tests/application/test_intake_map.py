"""R3-1 ① — 접수 끝점의 대상 행 → 대상 키. **frame 전에 코드가 읽고, 값 안에 identity가 그대로 있는 키 하나를 고른다.**

토큰·선언·이름 유사성은 없다. 키 값의 같은 객체에 identity 값 전부가 JSON 잎으로 완전 일치할 때만 "그 키"다(사내 측정 #6 정정:
c-1·c-2 모두 요약 키 값의 `record[0].summary_info`에 배지 행의 group/title이 그대로 있다)."""
import json

from src.application.fakes import ScriptedRunner
from src.application.intake_map import IntakeSpec, holds_identity, run_intake, target_row
from src.application.state import CaseState
from src.domain.case import Case, EvidenceRef
from src.domain.investigation import TaskOutcome

from tests.application.conftest import T0

FIELDS = ("group", "title")
SPEC = IntakeSpec(read=("rest.query", {"entry": "summary_badge", "params": {}}), fields=FIELDS)


def _case():
    return Case(id="c-7", gbm="mx", fct="gumi", origin="patrol", symptom="배지 0", t0=T0, check="badge_all_zero", target="L1/Alarm")


def _rows():
    return [{"group": g, "title": t, "alarm": 0, "source": "MES"} for t in ("Setup", "Alarm") for g in ("L1", "L2", "L3")]


def _value(group="L1", title="Alarm", *, data=(), extra=None):
    return json.dumps({"record": [{"summary_info": {"group": group, "title": title, "source": "MES", **(extra or {})},
                                   "data": list(data)}], "updated_at": "2026-10-13T00:00:00"})


def _ok(task_id, data, *, source, complete=True):
    ref = EvidenceRef(id=f"{task_id}.e1", source=source, summary="x", body="(원본은 raw)", complete=complete)
    return TaskOutcome(task_id=task_id, status="ok", summary=source, evidence=[ref])


def _runner(rest=None, keys=None, scans=None, *, picked=None, cut=(), reread=None):
    """`t-0`은 접수 읽기, 후보는 내부 번호 `t-0-N` 순서대로(리터럴 선언 키 먼저, 머리 scan은 그 뒤), 고른 키의 다시 읽기는 `t-1`.
    `cut`에 든 키의 후보 읽기는 본문이 잘린 것(`complete=False`), `reread`는 `t-1`이 돌려줄 값(없으면 고른 키의 값 그대로)."""
    rest = rest if rest is not None else {"request": {}, "status": 200, "response": _rows()}
    outcomes, raws = {"t-0": _ok("t-0", rest, source="rest.query entry='summary_badge'")}, {"t-0.e1": rest}
    n = 0
    for pattern, names in (scans or {}).items():
        n += 1
        outcomes[f"t-0-{n}"] = _ok(f"t-0-{n}", names, source=f"redis.scan pattern={pattern!r}")
        raws[f"t-0-{n}.e1"] = names
    for key, value in (keys or {}).items():
        n += 1
        raw = None if value is None else {"type": "string", "value": value}
        outcomes[f"t-0-{n}"] = _ok(f"t-0-{n}", raw, source=f"redis.get key={key!r}", complete=key not in cut)
        raws[f"t-0-{n}.e1"] = raw
    if picked is not None:
        raw = {"type": "string", "value": reread if reread is not None else keys[picked]}
        outcomes["t-1"] = _ok("t-1", raw, source=f"redis.get key={picked!r}")
        raws["t-1.e1"] = raw
    return ScriptedRunner(outcomes, raws=raws)


def test_값_안의_같은_객체에_identity_전부가_잎으로_있으면_그_자리를_준다():
    value = json.loads(_value())
    assert holds_identity(value, ("L1", "Alarm")) == "record[0].summary_info"
    assert holds_identity(value, ("L1", "Caution")) is None
    assert holds_identity(value, ("L10", "Alarm")) is None                       # 완전 일치 — L1은 L10이 아니다
    assert holds_identity({"a": {"group": "L1"}, "b": {"title": "Alarm"}}, ("L1", "Alarm")) is None   # 같은 객체여야 한다
    assert holds_identity({"group": "L1", "title": "Alarm", "n": 1}, ("L1", "Alarm")) == "(맨 위)"
    assert holds_identity({"x": [{"group": "L1", "title": "Alarm"}]}, ("L1", "Alarm")) == "x[0]"


def test_대상_행은_identity를_순찰과_같은_함수로_이은_문자열이_케이스_대상과_통째로_같은_행이다():
    """R3-5b ①(측정 #12 행 9): 대상 문자열을 '/'로 쪼개 필드 수와 맞춰 보면 제목에 '/'가 든 행을 놓친다 — 순찰이 대상을 만든 함수(`target_of`)로
    행마다 이어 통째로 비교한다."""
    from src.domain.patrol import target_of

    data = {"request": {}, "status": 200, "response": _rows()}
    assert target_row(data, fields=FIELDS, target="L1/Alarm") == ("response[3]", _rows()[3])
    assert target_row(data, fields=FIELDS, target="L9/Alarm") is None
    assert target_row(_rows(), fields=FIELDS, target="L2/Setup") == ("[1]", _rows()[1])
    slash = _rows() + [{"group": "L1", "title": "Up/Down Rate", "alarm": 0}]
    assert target_of(slash[6], FIELDS) == "L1/Up/Down Rate"
    assert target_row(slash, fields=FIELDS, target="L1/Up/Down Rate") == ("[6]", slash[6])
    assert target_row(slash, fields=FIELDS, target="L1/Up") is None                          # 조각이 아니라 통째로
    assert target_row([{"group": "L1"}], fields=FIELDS, target="L1/None") is None            # identity 필드가 없는 행은 대상이 아니다


async def test_identity가_그대로_든_키가_하나면_그_키를_고르고_State에는_접수와_대상_키_읽기만_남는다():
    """사내 측정 #6 c-2는 라벨 조각 'MES'로 틀린 키를 골랐다 — 라벨은 안 본다. 값 안에 identity가 그대로 있는 키만.
    측정 #7: 후보 29개 읽기를 전부 증거로 실었더니 예산을 넘어 **대상 키 본문이 빠졌다** — 입구 노드는 결과만 보인다(R3-1b ①):
    State에는 `t-0`(접수)·`t-1`(고른 키를 다시 읽은 것)만, 후보 읽기는 집계 한 줄. id는 리드와 같은 한 단계 모양이다."""
    runner = _runner(keys={"alarm:scan:L1": _value("L1", "Caution"), "alarm:stats:L1": _value("L1", "Alarm", data=[]),
                           "hb:sink": "2026-10-13", "gone:L1": None}, picked="alarm:stats:L1")
    patch = await run_intake(CaseState(case=_case()), SPEC, runner=runner,
                             declared=("alarm:scan:L1", "alarm:stats:L1", "hb:sink", "gone:L1"))
    pick = patch["target"]
    assert pick.key == "alarm:stats:L1" and pick.candidates == ["alarm:scan:L1", "alarm:stats:L1", "hb:sink", "gone:L1"]
    assert pick.basis == "키 값의 record[0].summary_info에 대상 identity(L1/Alarm)가 그대로 있다 (t-1.e1)"
    assert pick.line() == "대상 키: alarm:stats:L1 — 키 값의 record[0].summary_info에 대상 identity(L1/Alarm)가 그대로 있다 (t-1.e1)"
    assert (pick.read, pick.absent, pick.failed, pick.hits) == (4, ["gone:L1"], 0, 1)
    assert pick.fold_line() == "후보 4개 읽음 — 있음 3 · 없음 1 · identity 일치 1 (대상 키 alarm:stats:L1)"
    assert [t.id for t in patch["plan_tasks"]] == ["t-0", "t-1"]
    assert all(t.status == "ok" for t in patch["plan_tasks"]) and patch["plan_tasks"][0].goal.startswith("접수 읽기")
    assert patch["plan_tasks"][1].params == {"key": "alarm:stats:L1"} and "대상 키" in patch["plan_tasks"][1].goal
    assert [e.id for e in patch["evidence"]] == ["t-0.e1", "t-1.e1"]
    assert runner.ran == ["t-0", "t-0-1", "t-0-2", "t-0-3", "t-0-4", "t-1"]           # 후보는 내부 번호로만 돈다


async def test_후보_읽기_본문이_잘렸으면_대상_키를_identity_객체의_부모로_좁혀_다시_읽는다():
    """"좁혀서 통째로"(측정 #7 P0 1) — 잘린 본문으로는 data가 비었는지 못 본다. 잘리지 않았으면 통째로 다시 읽는다(위 테스트)."""
    narrowed = json.dumps({"summary_info": {"group": "L1", "title": "Alarm", "source": "MES"}, "data": []})
    runner = _runner(keys={"alarm:stats:L1": _value("L1", "Alarm", data=[{"n": i} for i in range(50)])},
                     picked="alarm:stats:L1", cut=("alarm:stats:L1",), reread=narrowed)
    patch = await run_intake(CaseState(case=_case()), SPEC, runner=runner, declared=("alarm:stats:L1",))
    assert patch["plan_tasks"][1].params == {"key": "alarm:stats:L1", "path": "record[0]"}
    assert patch["target"].basis == "키 값의 summary_info에 대상 identity(L1/Alarm)가 그대로 있다 (t-1.e1)"
    assert "좁혀" in patch["plan_tasks"][1].goal


async def test_대상_키_다시_읽기가_실패하면_고른_키는_남기고_그_사실을_적는다():
    runner = _runner(keys={"alarm:stats:L1": _value("L1", "Alarm")})                 # t-1이 대본에 없다 → 증거 없는 ok
    patch = await run_intake(CaseState(case=_case()), SPEC, runner=runner, declared=("alarm:stats:L1",))
    pick = patch["target"]
    assert pick.key == "alarm:stats:L1" and "다시 읽기 실패" in pick.basis and "(t-" not in pick.basis
    assert [t.id for t in patch["plan_tasks"]] == ["t-0", "t-1"] and [e.id for e in patch["evidence"]] == ["t-0.e1"]


async def test_고르지_못하면_고르지_않고_이유와_후보를_적는다():
    none = await run_intake(CaseState(case=_case()), SPEC, runner=_runner(keys={"alarm:scan:L1": _value("L1", "Caution"),
                                                                              "hb:sink": "x"}),
                            declared=("alarm:scan:L1", "hb:sink"))
    assert none["target"].key is None and "든 키가 없다" in none["target"].problem and none["target"].candidates == ["alarm:scan:L1", "hb:sink"]
    assert none["target"].line().startswith("대상 키: 못 골랐다 — ")
    assert none["target"].fold_line() == "후보 2개 읽음 — 있음 2 · 없음 0 · identity 일치 0"
    assert [t.id for t in none["plan_tasks"]] == ["t-0"] and [e.id for e in none["evidence"]] == ["t-0.e1"]
    both = await run_intake(CaseState(case=_case()), SPEC,
                            runner=_runner(keys={"alarm:all:L1": _value("L1", "Alarm", extra={"all": True}),
                                                 "alarm:stats:L1": _value("L1", "Alarm")}),
                            declared=("alarm:all:L1", "alarm:stats:L1"))
    assert both["target"].key is None and "2개" in both["target"].problem and "alarm:all:L1" in both["target"].problem
    missing = await run_intake(CaseState(case=_case()), SPEC, runner=_runner(keys={"alarm:stats:L1": None}), declared=("alarm:stats:L1",))
    assert missing["target"].key is None and "없다" in missing["target"].problem      # 키 자체가 없다(None) — 후보엔 남는다
    assert missing["target"].absent == ["alarm:stats:L1"] and missing["target"].fold_line().startswith("후보 1개 읽음 — 있음 0 · 없음 1")
    empty = await run_intake(CaseState(case=_case()), SPEC, runner=_runner(), declared=())
    assert empty["target"].key is None and "후보" in empty["target"].problem and [t.id for t in empty["plan_tasks"]] == ["t-0"]
    assert empty["target"].fold_line() == ""


async def test_접수_읽기가_실패하거나_대상_행이_없으면_거기서_멈춘다():
    failed = ScriptedRunner({"t-0": TaskOutcome(task_id="t-0", status="error", error="연결 거부")})
    patch = await run_intake(CaseState(case=_case()), SPEC, runner=failed, declared=("alarm:stats:L1",))
    assert patch["target"].key is None and "접수 읽기 실패" in patch["target"].problem and failed.ran == ["t-0"]
    rows = [r for r in _rows() if r["group"] != "L1"]
    patch = await run_intake(CaseState(case=_case()), SPEC, runner=_runner(rest={"response": rows}), declared=("alarm:stats:L1",))
    assert "대상 행" in patch["target"].problem and len(patch["plan_tasks"]) == 1


async def test_템플릿_키는_머리로_scan해_실재하는_키를_후보로_삼고_상한을_지킨다():
    runner = _runner(scans={"alarm:stats:*": ["alarm:stats:L1", "alarm:stats:L2"]},
                     keys={"alarm:stats:L1": _value("L1", "Alarm"), "alarm:stats:L2": _value("L2", "Alarm")}, picked="alarm:stats:L1")
    patch = await run_intake(CaseState(case=_case()), SPEC, runner=runner, declared=("alarm:stats:{line}",))
    assert patch["target"].key == "alarm:stats:L1" and patch["target"].candidates == ["alarm:stats:L1", "alarm:stats:L2"]
    assert runner.ran[1] == "t-0-1" and [t.action for t in patch["plan_tasks"]] == ["rest.query", "redis.get"]   # scan은 내부 번호
    many = _runner(scans={"k:*": [f"k:{i}" for i in range(80)]}, keys={f"k:{i}": "x" for i in range(80)})
    patch = await run_intake(CaseState(case=_case()), SPEC, runner=many, declared=("k:{n}",))
    assert len(patch["target"].candidates) == 50 and "상한" in patch["target"].problem


# ── R3-1b ③ — 다음 홉 ──

async def test_고른_키의_선언_이름과_identity_옆의_빈_목록을_적는다():
    """③의 다음 홉은 `code.uses <선언 이름>`이다 — 인덱스는 템플릿 이름(`alarm:stats:{line}`)으로 답하므로 고른 키가 어느 선언에서 왔는지 남긴다.
    identity 옆의 빈 목록(`record[0].data`)은 코드가 보는 사실 — 사내 c-1의 바로 그 모양(키는 있고 data가 `[]`)."""
    runner = _runner(scans={"alarm:stats:*": ["alarm:stats:L1"]}, keys={"alarm:stats:L1": _value("L1", "Alarm", data=[])},
                     picked="alarm:stats:L1")
    pick = (await run_intake(CaseState(case=_case()), SPEC, runner=runner, declared=("alarm:stats:{line}",)))["target"]
    assert pick.declared == "alarm:stats:{line}" and pick.empty == ["record[0].data"]
    literal = _runner(keys={"alarm:stats:L1": _value("L1", "Alarm", data=[{"n": 1}])}, picked="alarm:stats:L1")
    pick = (await run_intake(CaseState(case=_case()), SPEC, runner=literal, declared=("alarm:stats:L1",)))["target"]
    assert pick.declared == "alarm:stats:L1" and pick.empty == []


def test_다음_홉은_고른_키의_선언_이름으로_code_uses이고_인덱스가_없으면_없다():
    from src.application.intake_map import hop_line, next_hop
    from src.domain.case import TargetPick

    picked = TargetPick(key="alarm:stats:L1", declared="alarm:stats:{line}", basis="b")
    assert next_hop(picked, code_index=True) == ("code.uses", {"name": "alarm:stats:{line}"})
    assert hop_line(picked, code_index=True) == "다음 홉: code.uses name='alarm:stats:{line}' — 이 키를 누가 쓰나(값이 어디서 오나)"
    assert next_hop(picked, code_index=False) is None and hop_line(picked, code_index=False) == ""     # 없는 문은 안 가리킨다
    assert next_hop(TargetPick(problem="못 골랐다"), code_index=True) is None and next_hop(None, code_index=True) is None


def test_입구_줄은_이미_읽은_것과_빈_목록과_다음_홉을_말한다():
    from src.application.intake_map import intake_lines
    from src.domain.case import TargetPick
    from tests.application.conftest import task

    picked = TargetPick(key="alarm:stats:L1", declared="alarm:stats:{line}", basis="b", empty=["record[0].data"])
    state = CaseState(case=_case(), target=picked, plan_tasks=[
        task("t-0", action="rest.query", params={"entry": "summary_badge", "params": {}}, status="ok", result_evidence_ids=["t-0.e1"]),
        task("t-1", action="redis.get", params={"key": "alarm:stats:L1"}, status="ok", result_evidence_ids=["t-1.e1"])])
    assert intake_lines(state, code_index=True) == (
        "입구 노드가 이미 읽었다: 접수 읽기(t-0.e1) · 대상 키 alarm:stats:L1(t-1.e1) — 같은 읽기를 다시 내지 마라\n"
        "값: record[0].data가 비었다\n"
        "다음 홉: code.uses name='alarm:stats:{line}' — 이 키를 누가 쓰나(값이 어디서 오나)")
    assert intake_lines(state, code_index=False).count("\n") == 1                                  # 홉 줄만 빠진다
    assert intake_lines(CaseState(case=_case()), code_index=True) == ""                           # 입구가 안 돌았다


# ── R3-1c ①-a — 쓴 쪽은 데이터로 ──

async def test_값의_문자열_잎이_인덱스의_클래스_이름과_완전_일치하면_그_클래스가_쓴_쪽이다():
    """측정 #8: 동사 목록 규칙은 사내 모양(뒷줄 `save`, 앞줄 클래스 이름 `Publish…`)에서 틀렸다. 사내 요약 키 값의 `metadata.event_type`은
    쓴 파이프라인 클래스 이름과 완전 일치한다(c-1·c-2 둘 다) — 입구가 이미 읽은 값에서, 닫힌 집합(인덱스의 클래스 이름)과 완전 일치로 고른다.
    둘 이상 다른 클래스면 고르지 않고, 없으면 모른다."""
    from src.application.intake_map import writer_of

    classes = frozenset({"SummaryRefresh", "AlarmSink"})
    value = {"record": [{"summary_info": {"group": "L1", "title": "Alarm"}, "data": []}],
             "metadata": {"event_type": "SummaryRefresh", "source": "MES"}}
    assert writer_of(value, classes) == ("SummaryRefresh", "metadata.event_type", "")
    assert writer_of({"event_type": "AlarmSink"}, classes) == ("AlarmSink", "event_type", "")
    both = writer_of({"a": "SummaryRefresh", "b": {"c": "AlarmSink"}}, classes)
    assert both[:2] == ("", "") and "2개" in both[2] and "AlarmSink, SummaryRefresh" in both[2]
    assert writer_of({"event_type": "summaryrefresh"}, classes) == ("", "", "")          # 완전 일치 — 대소문자도
    assert writer_of(value, frozenset()) == ("", "", "")                                  # 인덱스가 없으면 모른다

    runner = _runner(keys={"alarm:stats:L1": json.dumps({**value})}, picked="alarm:stats:L1")
    pick = (await run_intake(CaseState(case=_case()), SPEC, runner=runner, declared=("alarm:stats:L1",), classes=classes))["target"]
    assert (pick.writer, pick.writer_at, pick.writer_problem) == ("SummaryRefresh", "metadata.event_type", "")
    assert pick.writer_line() == "쓴 쪽: SummaryRefresh — 값의 metadata.event_type과 인덱스의 클래스 이름이 완전 일치"
    unknown = (await run_intake(CaseState(case=_case()), SPEC, runner=_runner(keys={"alarm:stats:L1": _value("L1", "Alarm")}, picked="alarm:stats:L1"),
                                declared=("alarm:stats:L1",), classes=classes))["target"]
    assert unknown.writer == "" and unknown.writer_line() == "쓴 쪽: 모른다 — 값에 인덱스의 클래스 이름이 없다"
    blind = (await run_intake(CaseState(case=_case()), SPEC, runner=_runner(keys={"alarm:stats:L1": _value("L1", "Alarm")}, picked="alarm:stats:L1"),
                              declared=("alarm:stats:L1",)))["target"]
    assert blind.writer_line() == ""                                                      # 인덱스 자체가 없으면 말이 없다


def test_다음_홉은_쓴_쪽을_알면_그_클래스의_입력이고_모르면_선언_키의_uses다():
    from src.application.intake_map import hop_line, intake_lines, next_hop
    from src.domain.case import TargetPick
    from tests.application.conftest import task

    known = TargetPick(key="alarm:stats:L1", declared="alarm:stats:{line}", basis="b", writer="SummaryRefresh", writer_at="metadata.event_type")
    assert next_hop(known, code_index=True) == ("code.resources", {"name": "SummaryRefresh"})
    assert hop_line(known, code_index=True) == "다음 홉: code.resources name='SummaryRefresh' — 쓴 쪽이 읽는 자원(입력)"
    assert next_hop(known, code_index=False) is None
    unknown = TargetPick(key="alarm:stats:L1", declared="alarm:stats:{line}", basis="b")
    assert next_hop(unknown, code_index=True) == ("code.uses", {"name": "alarm:stats:{line}"})
    state = CaseState(case=_case(), target=known, plan_tasks=[
        task("t-0", action="rest.query", params={"entry": "summary_badge", "params": {}}, status="ok", result_evidence_ids=["t-0.e1"]),
        task("t-1", action="redis.get", params={"key": "alarm:stats:L1"}, status="ok", result_evidence_ids=["t-1.e1"])])
    lines = intake_lines(state, code_index=True).split("\n")
    assert lines[1] == "쓴 쪽: SummaryRefresh — 값의 metadata.event_type과 인덱스의 클래스 이름이 완전 일치"
    assert lines[2] == "다음 홉: code.resources name='SummaryRefresh' — 쓴 쪽이 읽는 자원(입력)"


# ── R3-5b ① — 대상 행은 통째로, 쓴 쪽은 전체 값에서 ──

async def test_제목에_슬래시가_든_대상도_고르고_identity는_그_행의_값이다():
    rows = _rows() + [{"group": "L1", "title": "Up/Down Rate", "alarm": 0, "source": "MES"}]
    case = _case().model_copy(update={"target": "L1/Up/Down Rate"})
    runner = _runner(rest={"response": rows}, keys={"alarm:rate:L1": _value("L1", "Up/Down Rate")}, picked="alarm:rate:L1")
    pick = (await run_intake(CaseState(case=case), SPEC, runner=runner, declared=("alarm:rate:L1",)))["target"]
    assert pick.key == "alarm:rate:L1" and "대상 identity(L1/Up/Down Rate)가 그대로 있다" in pick.basis


async def test_쓴_쪽은_좁힌_다시_읽기가_아니라_후보_읽기의_전체_값에서_찾는다():
    """측정 #12: data가 커서 후보 본문이 잘린 키는 identity의 부모로 좁혀 다시 읽는다 — 그 값엔 형제 자리(`metadata.event_type`)가 없어 16행이
    "쓴 쪽: 모른다"였다(틀린 사실). 실행기는 후보 읽기의 원본을 잘리지 않은 채 들고 있다 — 다시 읽기가 실패해도 쓴 쪽은 안다."""
    data = [{"n": i} for i in range(50)]
    full = json.dumps({**json.loads(_value("L1", "Alarm", data=data)), "metadata": {"event_type": "SummaryRefresh"}})
    narrowed = json.dumps({"summary_info": {"group": "L1", "title": "Alarm", "source": "MES"}, "data": data})
    classes = frozenset({"SummaryRefresh"})
    runner = _runner(keys={"alarm:stats:L1": full}, picked="alarm:stats:L1", cut=("alarm:stats:L1",), reread=narrowed)
    patch = await run_intake(CaseState(case=_case()), SPEC, runner=runner, declared=("alarm:stats:L1",), classes=classes)
    assert patch["plan_tasks"][1].params == {"key": "alarm:stats:L1", "path": "record[0]"}             # 좁혀 다시 읽었다
    assert (patch["target"].writer, patch["target"].writer_at) == ("SummaryRefresh", "metadata.event_type")
    failed = _runner(keys={"alarm:stats:L1": full})                                                  # t-1이 대본에 없다
    pick = (await run_intake(CaseState(case=_case()), SPEC, runner=failed, declared=("alarm:stats:L1",), classes=classes))["target"]
    assert pick.writer == "SummaryRefresh" and "다시 읽기 실패" in pick.basis



async def test_대상_키_홉의_빈_자리도_걷기와_같은_한_판정이다():
    """R3-5b ②(사람 결정 10-16): 대상 키 홉은 identity의 부모에서 같은 판정(`empties`)을 쓴다 — 빈 객체도 비었다, 행이 든 목록이 옆에 있으면 정상.
    자리는 후보 읽기의 전체 값 기준이다(좁힌 다시 읽기 안의 자리가 아니다)."""
    def keyed(**parent):
        return json.dumps({"record": [{"summary_info": {"group": "L1", "title": "Alarm", "source": "MES"}, **parent}]})

    for parent, want in (({"data": {}}, ([], ["record[0].data"])), ({"data": []}, (["record[0].data"], [])),
                         ({"data": [], "rows": [{"n": 1}]}, ([], []))):
        runner = _runner(keys={"alarm:stats:L1": keyed(**parent)}, picked="alarm:stats:L1")
        pick = (await run_intake(CaseState(case=_case()), SPEC, runner=runner, declared=("alarm:stats:L1",)))["target"]
        assert (pick.empty, pick.empty_objects) == want
    from src.application.intake_map import intake_lines
    runner = _runner(keys={"alarm:stats:L1": keyed(data={})}, picked="alarm:stats:L1")
    patch = await run_intake(CaseState(case=_case()), SPEC, runner=runner, declared=("alarm:stats:L1",))
    assert "값: record[0].data가 비었다" in intake_lines(CaseState(case=_case(), **patch), code_index=False)
