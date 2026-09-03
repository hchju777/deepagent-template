"""보고서 데이터 유도 — 스펙 §2-N5(ReportModel 2단).

렌더러(마크다운·HTML)가 공유하는 유일한 데이터 소스다. 단계 체크리스트·이모지·
증거 표·Timeline이 전부 같은 값을 원하므로 여기서 한 번만 계산한다 — 렌더러마다
따로 유도하면 마크다운과 HTML이 다른 말을 하게 된다.

단계 판정은 **구조로만** 한다: caveat 문자열이나 narrative를 냄새 맡지 않는다.
그런 판정은 프롬프트를 손볼 때마다 조용히 틀려진다.

case_file은 Store에 박제된 원시 dict라 옛 스냅샷이거나 키가 빠져도 이 모듈은
절대 raise하지 않는다 — 보고서 조립 실패가 조사 종결을 막아서는 안 된다.
"""
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from src.config.schema_app import StrictModel
from src.domain.case import Verdict
from src.domain.cases import CaseRecord
from src.domain.store import EvidenceRecord

Clock = Callable[[], datetime]

Mark = Literal["ok", "fail", "warn", "skip"]

_STAGE_LABELS = [("frame", "가설 수립"), ("select", "조사 계획"), ("execute", "조사 실행"),
                 ("integrate", "결과 통합"), ("conclude", "판정"), ("verify", "검증")]


class StageCheck(StrictModel):
    """조사 단계 하나의 통과 여부. mark는 렌더러가 기호로 바꾼다."""
    stage: str
    label: str
    mark: Mark
    note: str = ""


class ReportModel(StrictModel):
    """보고서 한 건의 데이터 전부. 렌더러는 이것만 보고 문자열을 만든다."""
    record: CaseRecord
    verdict: Verdict | None
    evidence: list[EvidenceRecord]
    evidence_summaries: dict[str, str] | None
    stages: list[StageCheck]
    round_no: int | None
    plan_tasks: list[dict]
    hypotheses: list[dict]
    verify_problems: list[str]
    qa_log: list[dict]
    task_error_rate: str
    partial: bool = False             # 실패 시점 부분 스냅샷인가
    salvage_error: str | None = None  # 구제 자체가 실패했으면 그 사유
    generated_at: datetime


def _as_list(value: object) -> list:
    """컨테이너 타입 가드 — `value or []`는 5 같은 truthy 비-리스트를 통과시킨다."""
    return value if isinstance(value, list) else []


def _dicts(value: object) -> list[dict]:
    return [item for item in _as_list(value) if isinstance(item, dict)]


def _task_error_rate(plan_tasks: list[dict]) -> str:
    if not plan_tasks:
        return "없음"
    errors = sum(1 for t in plan_tasks if t.get("status") == "error")
    return f"{errors}/{len(plan_tasks)}"


def _frame_stage(hypotheses, verdict) -> tuple[Mark, str]:
    if hypotheses:
        return "ok", f"가설 {len(hypotheses)}건"
    # 가설 없이 판정만 있는 유일한 경로가 frame 출력 파싱 실패다.
    if verdict is not None:
        return "fail", "가설 없이 판정으로 직행(frame 파싱 실패)"
    return "skip", "미도달"


def _select_stage(plan_tasks) -> tuple[Mark, str]:
    if plan_tasks:
        return "ok", f"태스크 {len(plan_tasks)}건"
    return "skip", "미도달"


def _execute_stage(plan_tasks) -> tuple[Mark, str]:
    if not plan_tasks:
        return "skip", "미도달"
    errors = [t for t in plan_tasks if t.get("status") == "error"]
    done = [t for t in plan_tasks if t.get("status") == "ok"]
    if errors:
        return "fail", f"{len(done)} ok / {len(errors)} error"
    if not done:
        return "skip", "실행된 태스크 없음"
    return "ok", f"{len(done)} ok"


def _integrate_stage(round_no, qa_log) -> tuple[Mark, str]:
    if any(e.get("kind") == "integrate_parse_failure" for e in qa_log):
        return "fail", "통합 출력 파싱 실패"
    if isinstance(round_no, int) and round_no >= 1:
        return "ok", f"라운드 {round_no}"
    return "skip", "미도달"


def _conclude_stage(verdict) -> tuple[Mark, str]:
    if verdict is None:
        return "skip", "미도달"
    if verdict.verdict_type == "degraded":
        return "fail", "degraded — 판정 불가"
    return "ok", verdict.verdict_type


def _verify_stage(verdict, verify_problems, verify_attempts) -> tuple[Mark, str]:
    if verdict is None:
        return "skip", "미도달"
    if verify_problems:
        return "fail", f"미해결 문제 {len(verify_problems)}건"
    # verify_attempts가 없는 옛 스냅샷은 0으로 본다 — 과거 보고서를 전부 경고로
    # 물들이지 않기 위해서다.
    if isinstance(verify_attempts, int) and verify_attempts >= 1:
        return "warn", "재작성 후 통과(확신 강등 가능)"
    return "ok", "인용 검증 통과"


def build_report_model(record: CaseRecord, *, verdict: Verdict | None,
                       evidence: list[EvidenceRecord], case_file: dict | None,
                       clock: Clock,
                       evidence_summaries: dict[str, str] | None = None) -> ReportModel:
    """보고서 데이터를 유도한다. 순수 함수이고 절대 raise하지 않는다."""
    case_file = case_file if isinstance(case_file, dict) else {}
    plan_tasks = _dicts(case_file.get("plan_tasks"))
    hypotheses = _dicts(case_file.get("hypotheses"))
    qa_log = _dicts(case_file.get("qa_log"))
    verify_problems = [str(p) for p in _as_list(case_file.get("verify_problems"))]
    round_raw = case_file.get("round")
    round_no = round_raw if isinstance(round_raw, int) else None
    verify_attempts = case_file.get("verify_attempts", 0)

    outcomes = {
        "frame": _frame_stage(hypotheses, verdict),
        "select": _select_stage(plan_tasks),
        "execute": _execute_stage(plan_tasks),
        "integrate": _integrate_stage(round_no, qa_log),
        "conclude": _conclude_stage(verdict),
        "verify": _verify_stage(verdict, verify_problems, verify_attempts),
    }
    stages = [StageCheck(stage=key, label=label, mark=outcomes[key][0], note=outcomes[key][1])
              for key, label in _STAGE_LABELS]

    salvage_error = case_file.get("salvage_error")
    return ReportModel(
        record=record, verdict=verdict, evidence=evidence,
        evidence_summaries=evidence_summaries, stages=stages, round_no=round_no,
        plan_tasks=plan_tasks, hypotheses=hypotheses, verify_problems=verify_problems,
        qa_log=qa_log, task_error_rate=_task_error_rate(plan_tasks),
        partial=bool(case_file.get("partial")),
        salvage_error=str(salvage_error) if salvage_error else None,
        generated_at=clock())
