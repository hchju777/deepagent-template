"""라벨 유입구와 캘리브레이션 게이트(계획 15/P8, 방향 문서 §4.5).

CLI(`case label`)와 API(`POST /cases/{id}/label`)가 **이 함수 하나**를 쓴다 — 분기를
각자 베끼면 언젠가 하나가 빠뜨린다(규율 8이 발행 배선에서 겪은 그것).

**게이트가 열리기 전에는 아무것도 계산하지 않는다.** 종결 라벨 n >= 30 그리고
종결의 절반 초과(선택 편향이 유계) 전에는 어떤 정확도도 내지 않는다 — 12/40으로 낸
30%는 다음 주에 뒤집힐 숫자이고, 한 번 보고되면 사람이 그것을 기억한다.

열린 뒤에 내는 것은 상관계수가 아니라 `confidence`별 적중이다(`calibration`) — 대상이
범주형이라 Pearson r은 범주 오류다. 분모 규칙은 그 함수의 docstring에 있고, **코드가
쥔다**: 어느 라벨을 셀지 사람이 고르게 하면 숫자가 원하는 대로 나온다.
"""
from datetime import datetime
from typing import Callable, Literal

from src.config.schema_app import StrictModel
from src.domain.label import Agreement, Resolution, RootCauseLabel

LabelResult = Literal["recorded", "not_found", "error"]

MIN_LABELS = 30          # 상수는 코드가 쥔다(규율 6) — config로 빼면 게이트가 협상 대상이 된다
MIN_RATE = 0.5


class LabelStats(StrictModel):
    closed_total: int
    labeled_cases: int          # 전체 라벨(진행 중 케이스 포함)
    labeled_closed: int         # 게이트를 모는 숫자 — 대조 가능한 라벨만
    gate_open: bool
    why: str             # 왜 닫혔는지(또는 열렸는지) — 건수로만 말한다


def submit_label(case_id: str, *, agreement: Agreement, resolution: Resolution | None = None,
                 actual_component: str | None = None, actual_verdict_type: str | None = None,
                 saw_report: bool = False, labeled_by: str | None = None,
                 repo, labels, clock: Callable[[], datetime]) -> LabelResult:
    """라벨 한 건을 남긴다. 절대 raise하지 않는다.

    닫히지 않은 케이스도 받는다 — 사람이 조사 중에 원인을 알 수 있다. 분모(종결 케이스)와
    라벨은 다른 축이다.
    """
    try:
        repo.get(case_id)
    except KeyError:
        return "not_found"
    except Exception:                                              # noqa: BLE001 — 무raise
        return "error"
    try:
        labels.append(RootCauseLabel(
            case_id=case_id, agreement=agreement, resolution=resolution,
            actual_root_cause_component=actual_component, actual_verdict_type=actual_verdict_type,
            saw_report=saw_report, labeled_by=labeled_by, labeled_at=clock()))
    except Exception:                                              # noqa: BLE001
        return "error"
    return "recorded"


def label_stats(*, repo, labels) -> LabelStats:
    """건수와 게이트 상태만. 적중 계산은 `calibration`이 하고, 이 게이트를 먼저 본다."""
    try:
        closed_ids = {r.id for r in repo.list_by_status("closed")}   # 한 번만 읽는다
        labeled_ids = set(labels.labeled_case_ids())
    except Exception:                                              # noqa: BLE001 — 무raise
        return LabelStats(closed_total=0, labeled_cases=0, labeled_closed=0,
                          gate_open=False, why="라벨 집계 실패")
    closed_total = len(closed_ids)
    labeled = len(labeled_ids)
    # 건수 조건도 **종결 케이스의 라벨**에 건다. 전체 라벨에 걸면 진행 중인 케이스 27건을
    # 라벨한 것만으로 게이트가 열리는데, 그때 대조 가능한 데이터는 종결 3건뿐이다
    # (검증 리뷰 M2). 캘리브레이션에 쓸 수 있는 라벨만 n으로 센다.
    labeled_closed = len(closed_ids & labeled_ids)
    gate_open = (labeled_closed >= MIN_LABELS and closed_total > 0
                 and labeled_closed > closed_total * MIN_RATE)
    why = (f"라벨 {labeled}건 / 종결 {closed_total}건 (그중 라벨됨 {labeled_closed}건) — "
           f"게이트: 종결 라벨 {MIN_LABELS}건 이상 그리고 종결의 절반 초과")
    return LabelStats(closed_total=closed_total, labeled_cases=labeled,
                      labeled_closed=labeled_closed, gate_open=gate_open, why=why)


class ConfidenceBucket(StrictModel):
    """한 confidence 값의 적중. 퍼센트는 여기 없다 — 표시는 호출부가 정한다."""
    confidence: str             # "high"/"medium"/"low"/"미상"
    n: int                      # 분모(unknown 라벨 제외)
    correct: int = 0
    partially_correct: int = 0
    wrong: int = 0
    excluded_unknown: int = 0   # 분모에서 뺀 수 — 숨기면 n이 작은 이유를 모른다
    # **세어진 라벨이** 보고서를 봤나 — 케이스가 앵커링됐나가 아니다. 마지막 라벨
    # 한 행에서 agreement와 함께 뽑으므로, 눈감고 달았다가 보고서를 보고 정정한
    # 케이스는 앵커링으로 세어지고 그 반대는 표시를 잃는다.
    saw_report: int = 0


class Calibration(StrictModel):
    gate_open: bool
    why: str
    buckets: list[ConfidenceBucket] = []   # 게이트가 닫혀 있으면 비어 있다


# 표시 순서는 코드가 쥔다 — dict 순서에 맡기면 실행마다 표가 흔들린다.
_CONFIDENCE_ORDER = ("high", "medium", "low", "미상")


def calibration(*, repo, labels, snapshots) -> Calibration:
    """게이트가 열렸을 때만 `confidence`별 적중을 낸다. 절대 raise하지 않는다.

    분모 규칙은 **코드가 쥔다**(규율 6) — 어느 라벨을 셀지 사람이 고르게 하면 숫자가
    원하는 대로 나온다:

    - `unknown` 라벨은 분모에서 뺀다("모르겠다"는 틀렸다는 증거가 아니다). 뺀 수를
      `excluded_unknown`으로 **함께** 보고한다 — 조용히 빼면 n이 왜 작은지 모른다.
    - 케이스당 **마지막 라벨만** 센다. append-only라 한 케이스에 여럿 붙고, 전부 세면
      여러 번 고친 케이스가 분모를 지배한다.
    - `confidence`가 없는 스냅샷은 버리지 않고 `"미상"` 버킷에 넣는다 — 버리면 분모에
      생존 편향이 생긴다(confidence를 못 낸 판정이 곧 어려운 케이스다).
    - 스냅샷이 없는 라벨은 대조 대상이 없으므로 세지 않는다.
    """
    stats = label_stats(repo=repo, labels=labels)
    if not stats.gate_open:
        return Calibration(gate_open=False, why=stats.why)
    try:
        closed_ids = {r.id for r in repo.list_by_status("closed")}
        rows: dict[str, dict] = {}
        for case_id in sorted(closed_ids & set(labels.labeled_case_ids())):
            snapshot = snapshots.get(case_id)
            if snapshot is None:
                continue
            history = labels.list_for(case_id)
            if not history:
                continue
            # 시각으로 최댓값을 고르면 **같은 시각의 두 라벨**에서 먼저 온 것이 이긴다
            # (고정 시계 테스트, 같은 초의 두 요청). 저장소가 단 순서를 보장한다 —
            # `MongoLabelStore.list_for`가 `(labeled_at, _id)`로 정렬하는 이유다.
            # ObjectId의 중간 5바이트가 프로세스별 난수라 **같은 초에 다른 프로세스가**
            # 단 두 라벨은 도착 순서와 어긋날 수 있다(그때는 정정 순서 자체가 모호하다).
            last = history[-1]
            key = snapshot.confidence or "미상"
            bucket = rows.setdefault(key, {"correct": 0, "partially_correct": 0,
                                           "wrong": 0, "excluded_unknown": 0,
                                           "saw_report": 0})
            if last.saw_report:
                bucket["saw_report"] += 1
            if last.agreement == "unknown":
                bucket["excluded_unknown"] += 1
            else:
                bucket[last.agreement] += 1
    except Exception as exc:                                       # noqa: BLE001 — 무raise
        return Calibration(gate_open=stats.gate_open,
                           why=f"{stats.why} / 캘리브레이션 집계 실패: {type(exc).__name__}")
    buckets = [
        ConfidenceBucket(
            confidence=key,
            n=rows[key]["correct"] + rows[key]["partially_correct"] + rows[key]["wrong"],
            **rows[key])
        for key in sorted(rows, key=lambda k: (_CONFIDENCE_ORDER.index(k)
                                               if k in _CONFIDENCE_ORDER else len(_CONFIDENCE_ORDER),
                                               k))]
    return Calibration(gate_open=True, why=stats.why, buckets=buckets)


def label_texts(labels, case_id: str) -> list[str]:
    """푸터에 보일 라벨 표현. 저장소 장애는 빈 목록이다 — 보고서를 막지 않는다(규율 1)."""
    if labels is None:
        return []
    try:
        rows = labels.list_for(case_id)
    except Exception:                                              # noqa: BLE001
        return []
    return [row.agreement + (f" ({row.resolution})" if row.resolution else "") for row in rows]
