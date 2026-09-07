"""Fleet 집계의 도메인 — 정직성을 **타입이** 강제한다(계획 16/P7, 방향 문서 §4.2).

집계는 `Case`가 아니다. 증상도 조사도 판정도 없고, `Case`에 밀어넣으면 requeue가
집계 레코드마다 LLM 그래프를 돌리고 `Verdict`가 전부 "조사 실패" 낙인을 찍는다.

이 모듈의 validator 넷이 이 계획의 요점이다: **"30개 중 3개 누락인데 숫자만
내보내는" 상태를 표현할 수 없게** 만든다. `Envelope._incomplete_needs_reason`과 같은
관용구다 — 정직성을 산문으로 부탁하지 않고 생성자에서 거부한다.
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal

from pydantic import model_validator

from src.config.schema_app import StrictModel
from src.domain.concern import Concern

Reduce = Literal["sum", "avg", "max", "min", "count", "count_nonzero"]


def fold_complete(flags: list[bool]) -> bool:
    """표본의 완전성을 AND로 접는다. 빈 표본은 완전하다고 말할 수 없다."""
    return bool(flags) and all(flags)


class SiteCoverage(StrictModel):
    """사이트 하나가 이 집계에 기여했는가 — 아니면 왜 못 했는가.

    `fallback`은 값은 왔는데 `effective_as_of`가 요청 창보다 오래된 경우다. 숫자에
    포함되지만 보고서가 그 사실을 적는다 — 조용히 섞으면 "어제 값으로 오늘을 말한다".
    """
    gbm: str
    fct: str
    status: Literal["covered", "missing", "fallback"]
    reason: str | None = None
    last_success_at: datetime | None = None

    @model_validator(mode="after")
    def _gap_needs_reason(self):
        if self.status != "covered" and not self.reason:
            raise ValueError("missing/fallback 사이트는 사유가 필요하다")
        return self


class MetricRollup(StrictModel):
    metric: str
    value: float | None
    reduce: Reduce
    expected_sites: int
    covered_sites: int
    complete: bool
    coverage_note: str | None = None
    unit: str | None = None

    @model_validator(mode="after")
    def _honesty(self):
        # ① 누락이 있으면 complete는 False다 — 호출부가 True를 넣어도 강제한다.
        # `<`가 아니라 `!=`다 — 5/3도 정직한 상태가 아니다(사이트 중복 등).
        if self.covered_sites != self.expected_sites and self.complete:
            object.__setattr__(self, "complete", False)
        # ③ 아무 데서도 못 읽었으면 값이 없다. 0은 "전부 0이었다"는 다른 주장이다.
        if self.covered_sites == 0:
            if self.value is not None:
                raise ValueError("covered_sites=0이면 value는 None이어야 한다")
            object.__setattr__(self, "complete", False)
        # ② 불완전은 사유를 요구한다 — 이유 없는 불완전은 읽는 사람이 무시한다.
        if not self.complete and not self.coverage_note:
            raise ValueError("complete=False면 coverage_note가 필요하다")
        # ⑤ "완전"과 "—"가 나란히 서면 대시를 설명할 문장이 없다(검증 리뷰 M-8).
        if self.complete and self.value is None and not self.coverage_note:
            raise ValueError("값이 없는 완전한 집계에는 coverage_note가 필요하다")
        return self


class FleetReport(StrictModel):
    scenario: str
    title: str
    concern: Concern
    scenario_digest: str
    window_from: datetime
    window_to: datetime
    coverage: list[SiteCoverage] = []
    rollups: list[MetricRollup] = []
    groups: dict[str, list[MetricRollup]] = {}
    previous_digest: str | None = None
    trend: dict[str, float | None] = {}
    trend_caveat: str | None = None
    generated_at: datetime


class DigestStorePort(ABC):
    """집계 실행 기록 — 추세 비교의 유일한 재료다(방향 문서 §204)."""

    @abstractmethod
    def put(self, report: FleetReport) -> None: ...

    @abstractmethod
    def latest(self, scenario: str) -> FleetReport | None: ...

    @abstractmethod
    def list(self, scenario: str, limit: int = 20) -> list[FleetReport]:
        """최신순."""
        ...

    @abstractmethod
    def prune_before(self, before: datetime) -> int: ...


class InMemoryDigestStore(DigestStorePort):
    def __init__(self):
        self._reports: list[FleetReport] = []

    def put(self, report):
        self._reports.append(report)

    def _for(self, scenario):
        # 동점은 **삽입 역순**이다 — Mongo가 `_id` 내림차순으로 가르는 것과 같은 계약이고,
        # `latest()`의 뜻("가장 마지막에 들어온 것")과도 맞는다. `reverse=True`만 걸면
        # 파이썬 안정 정렬이 동점을 삽입 **정순**으로 남겨 두 백엔드가 정반대 답을 낸다.
        rows = [(i, r) for i, r in enumerate(self._reports) if r.scenario == scenario]
        return [r for _, r in sorted(rows, key=lambda pair: (pair[1].generated_at, pair[0]),
                                     reverse=True)]

    def latest(self, scenario):
        rows = self._for(scenario)
        return rows[0] if rows else None

    def list(self, scenario, limit=20):
        return self._for(scenario)[:limit] if limit > 0 else []

    def prune_before(self, before):
        kept = [r for r in self._reports if r.generated_at >= before]
        deleted = len(self._reports) - len(kept)
        self._reports = kept
        return deleted
