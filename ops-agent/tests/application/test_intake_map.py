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


def _ok(task_id, data, *, source):
    ref = EvidenceRef(id=f"{task_id}.e1", source=source, summary="x", body="(원본은 raw)")
    return TaskOutcome(task_id=task_id, status="ok", summary=source, evidence=[ref])


def _runner(rest=None, keys=None, scans=None):
    """`t-0`은 접수 읽기, 그다음 번호는 후보 순서대로 — 리터럴 선언 키 먼저, 머리 scan은 그 뒤."""
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
        outcomes[f"t-0-{n}"] = _ok(f"t-0-{n}", raw, source=f"redis.get key={key!r}")
        raws[f"t-0-{n}.e1"] = raw
    return ScriptedRunner(outcomes, raws=raws)


def test_값_안의_같은_객체에_identity_전부가_잎으로_있으면_그_자리를_준다():
    value = json.loads(_value())
    assert holds_identity(value, ("L1", "Alarm")) == "record[0].summary_info"
    assert holds_identity(value, ("L1", "Caution")) is None
    assert holds_identity(value, ("L10", "Alarm")) is None                       # 완전 일치 — L1은 L10이 아니다
    assert holds_identity({"a": {"group": "L1"}, "b": {"title": "Alarm"}}, ("L1", "Alarm")) is None   # 같은 객체여야 한다
    assert holds_identity({"group": "L1", "title": "Alarm", "n": 1}, ("L1", "Alarm")) == "(맨 위)"
    assert holds_identity({"x": [{"group": "L1", "title": "Alarm"}]}, ("L1", "Alarm")) == "x[0]"


def test_대상_행은_identity_필드가_전부_같은_행이다():
    data = {"request": {}, "status": 200, "response": _rows()}
    assert target_row(data, fields=FIELDS, values=("L1", "Alarm")) == ("response[3]", _rows()[3])
    assert target_row(data, fields=FIELDS, values=("L9", "Alarm")) is None
    assert target_row(_rows(), fields=FIELDS, values=("L2", "Setup")) == ("[1]", _rows()[1])


async def test_identity가_그대로_든_키가_하나면_그_키를_고르고_근거와_증거가_State에_남는다():
    """사내 측정 #6 c-2는 라벨 조각 'MES'로 틀린 키를 골랐다 — 라벨은 안 본다. 값 안에 identity가 그대로 있는 키만."""
    runner = _runner(keys={"alarm:scan:L1": _value("L1", "Caution"), "alarm:stats:L1": _value("L1", "Alarm", data=[]),
                           "hb:sink": "2026-10-13"})
    patch = await run_intake(CaseState(case=_case()), SPEC, runner=runner,
                             declared=("alarm:scan:L1", "alarm:stats:L1", "hb:sink"))
    pick = patch["target"]
    assert pick.key == "alarm:stats:L1" and pick.candidates == ["alarm:scan:L1", "alarm:stats:L1", "hb:sink"]
    assert pick.basis == "키 값의 record[0].summary_info에 대상 identity(L1/Alarm)가 그대로 있다 (t-0-2.e1)"
    assert pick.line() == "대상 키: alarm:stats:L1 — 키 값의 record[0].summary_info에 대상 identity(L1/Alarm)가 그대로 있다 (t-0-2.e1)"
    assert [t.id for t in patch["plan_tasks"]] == ["t-0", "t-0-1", "t-0-2", "t-0-3"]
    assert all(t.status == "ok" for t in patch["plan_tasks"]) and patch["plan_tasks"][0].goal.startswith("접수 읽기")
    assert [e.id for e in patch["evidence"]] == ["t-0.e1", "t-0-1.e1", "t-0-2.e1", "t-0-3.e1"]
    assert runner.ran == ["t-0", "t-0-1", "t-0-2", "t-0-3"]


async def test_고르지_못하면_고르지_않고_이유와_후보를_적는다():
    none = await run_intake(CaseState(case=_case()), SPEC, runner=_runner(keys={"alarm:scan:L1": _value("L1", "Caution"),
                                                                              "hb:sink": "x"}),
                            declared=("alarm:scan:L1", "hb:sink"))
    assert none["target"].key is None and "든 키가 없다" in none["target"].problem and none["target"].candidates == ["alarm:scan:L1", "hb:sink"]
    assert none["target"].line().startswith("대상 키: 못 골랐다 — ")
    both = await run_intake(CaseState(case=_case()), SPEC,
                            runner=_runner(keys={"alarm:all:L1": _value("L1", "Alarm", extra={"all": True}),
                                                 "alarm:stats:L1": _value("L1", "Alarm")}),
                            declared=("alarm:all:L1", "alarm:stats:L1"))
    assert both["target"].key is None and "2개" in both["target"].problem and "alarm:all:L1" in both["target"].problem
    missing = await run_intake(CaseState(case=_case()), SPEC, runner=_runner(keys={"alarm:stats:L1": None}), declared=("alarm:stats:L1",))
    assert missing["target"].key is None and "없다" in missing["target"].problem      # 키 자체가 없다(None) — 후보엔 남는다
    empty = await run_intake(CaseState(case=_case()), SPEC, runner=_runner(), declared=())
    assert empty["target"].key is None and "후보" in empty["target"].problem and [t.id for t in empty["plan_tasks"]] == ["t-0"]


async def test_접수_읽기가_실패하거나_대상_행이_없으면_거기서_멈춘다():
    failed = ScriptedRunner({"t-0": TaskOutcome(task_id="t-0", status="error", error="연결 거부")})
    patch = await run_intake(CaseState(case=_case()), SPEC, runner=failed, declared=("alarm:stats:L1",))
    assert patch["target"].key is None and "접수 읽기 실패" in patch["target"].problem and failed.ran == ["t-0"]
    rows = [r for r in _rows() if r["group"] != "L1"]
    patch = await run_intake(CaseState(case=_case()), SPEC, runner=_runner(rest={"response": rows}), declared=("alarm:stats:L1",))
    assert "대상 행" in patch["target"].problem and len(patch["plan_tasks"]) == 1


async def test_템플릿_키는_머리로_scan해_실재하는_키를_후보로_삼고_상한을_지킨다():
    runner = _runner(scans={"alarm:stats:*": ["alarm:stats:L1", "alarm:stats:L2"]},
                     keys={"alarm:stats:L1": _value("L1", "Alarm"), "alarm:stats:L2": _value("L2", "Alarm")})
    patch = await run_intake(CaseState(case=_case()), SPEC, runner=runner, declared=("alarm:stats:{line}",))
    assert patch["target"].key == "alarm:stats:L1" and patch["target"].candidates == ["alarm:stats:L1", "alarm:stats:L2"]
    assert patch["plan_tasks"][1].action == "redis.scan" and patch["plan_tasks"][1].params == {"pattern": "alarm:stats:*"}
    many = _runner(scans={"k:*": [f"k:{i}" for i in range(80)]}, keys={f"k:{i}": "x" for i in range(80)})
    patch = await run_intake(CaseState(case=_case()), SPEC, runner=many, declared=("k:{n}",))
    assert len(patch["target"].candidates) == 50 and "상한" in patch["target"].problem
