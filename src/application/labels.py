"""라벨 유입구와 캘리브레이션 게이트(계획 15/P8, 방향 문서 §4.5).

CLI(`case label`)와 API(`POST /cases/{id}/label`)가 **이 함수 하나**를 쓴다 — 분기를
각자 베끼면 언젠가 하나가 빠뜨린다(규율 8이 발행 배선에서 겪은 그것).

**계산은 여기 없다.** 라벨 n >= 30 그리고 라벨률 > 50%(선택 편향이 유계) 전에는 어떤
정확도도 내지 않는다. 열린 뒤에도 낼 것은 상관계수가 아니라 `confidence`별 적중이다 —
대상이 범주형이라 Pearson r은 범주 오류다. 게이트가 닫혀 있는 동안 **맨 퍼센트를 내지
않는 것**이 요점이다: 12/40으로 낸 30%는 다음 주에 뒤집힐 숫자이고, 한 번 보고되면
사람이 그것을 기억한다.
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
    labeled_cases: int
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
    """건수와 게이트 상태. 정확도는 여기서도, 어디서도 계산하지 않는다."""
    try:
        closed_ids = {r.id for r in repo.list_by_status("closed")}   # 한 번만 읽는다
        labeled_ids = set(labels.labeled_case_ids())
    except Exception:                                              # noqa: BLE001 — 무raise
        return LabelStats(closed_total=0, labeled_cases=0, gate_open=False,
                          why="라벨 집계 실패")
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
                      gate_open=gate_open, why=why)


def label_texts(labels, case_id: str) -> list[str]:
    """푸터에 보일 라벨 표현. 저장소 장애는 빈 목록이다 — 보고서를 막지 않는다(규율 1)."""
    if labels is None:
        return []
    try:
        rows = labels.list_for(case_id)
    except Exception:                                              # noqa: BLE001
        return []
    return [row.agreement + (f" ({row.resolution})" if row.resolution else "") for row in rows]
