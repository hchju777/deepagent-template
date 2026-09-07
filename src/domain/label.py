"""사람이 되먹이는 실제 원인 — 학습 루프의 나머지 절반(계획 15/P8, 방향 문서 §4.5).

`VerdictSnapshot`이 "기계가 뭐라 했나"라면 이것은 "실제로 뭐였나"다. 둘 다 없으면
어떤 대조도 불가능하고, 스냅샷만 남기고 이쪽을 안 열면 분자가 영원히 비어 있다.

**append-only, 케이스당 복수 허용**: 사람이 나중에 생각을 바꾸면 새 행이 쌓인다.
덮어쓰면 "처음엔 맞다고 했다가 틀렸다고 했다"는 사실이 사라지는데, 그것 자체가
캘리브레이션의 재료다.

`agreement`가 component 문자열 비교보다 중요한 이유: 자유 문자열은 절대 정확히
일치하지 않는다("plan-sync" vs "plan sync 서비스"). 자동 문자열 비교는 우리 정규화기를
측정하는 짓이고, 이 규모에서 믿을 만한 비교자는 사람이 주는 4분류뿐이다.
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal

from src.config.schema_app import StrictModel

Agreement = Literal["correct", "partially_correct", "wrong", "unknown"]
# "에이전트가 틀렸다"와 "실은 아무것도 안 고장났다"는 완전히 다른 실패다 — 요구하는
# 행동이 다르므로 한 축으로 뭉개지 않는다.
Resolution = Literal["fixed", "not_reproducible", "wont_fix", "false_positive"]


class RootCauseLabel(StrictModel):
    case_id: str
    agreement: Agreement
    labeled_at: datetime
    resolution: Resolution | None = None
    actual_root_cause_component: str | None = None
    actual_verdict_type: str | None = None
    saw_report: bool = False          # 앵커링 탐지 — 보고서를 보고 라벨했나
    labeled_by: str | None = None


class LabelStorePort(ABC):
    """라벨은 retention보다 오래 산다 — 스냅샷과 짝이라 한쪽만 지우면 대조가 불가능하다."""

    @abstractmethod
    def append(self, label: RootCauseLabel) -> None: ...

    @abstractmethod
    def list_for(self, case_id: str) -> list[RootCauseLabel]:
        """그 케이스의 라벨을 단 순서대로."""
        ...

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def labeled_case_ids(self) -> set[str]: ...


class InMemoryLabelStore(LabelStorePort):
    def __init__(self):
        self._labels: list[RootCauseLabel] = []

    def append(self, label):
        self._labels.append(label)

    def list_for(self, case_id):
        # 시각으로 정렬한 뒤 삽입 순서로 동점을 가른다 — Mongo가 `(labeled_at, _id)`로
        # 하는 것과 같은 계약이다. 정렬을 안 하면(순수 삽입 순서) 시계가 되감긴 경우에
        # 두 백엔드가 다른 "마지막 라벨"을 내고, 캘리브레이션(계획 19)이 그것을 사람의
        # 최종 믿음으로 읽는다.
        rows = [(i, l) for i, l in enumerate(self._labels) if l.case_id == case_id]
        return [l for _, l in sorted(rows, key=lambda pair: (pair[1].labeled_at, pair[0]))]

    def count(self):
        return len(self._labels)

    def labeled_case_ids(self):
        return {l.case_id for l in self._labels}
