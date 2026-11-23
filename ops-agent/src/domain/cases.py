"""케이스 — 순찰이 찾은 이상 하나가 사건이 된 것.

## 지문(fingerprint)이 문자열이 아니라 튜플인 이유

`f"{site}|{check}|{target}"`로 만들면 짧고 보기 좋은데, **`target`에 구분자가 들어갈
수 있다.** `target`은 대상 응답의 필드를 이어 붙인 것이라(`"Line/Target Rate"`) 우리가
고르지 않은 문자열이고, 언젠가 `|`가 섞이면 서로 다른 두 대상이 **같은 지문으로
접힌다.** 그러면 케이스 하나가 조용히 사라진다.

튜플로 비교하면 이스케이프 문제가 아예 없다. 저장소가 작아서(사이트당 점검 몇 개 ×
대상 몇십 개) 선형 탐색으로 충분하다.

## 왜 "첨부"가 있는가

3시간마다 도는데 문제가 안 고쳐지면 하루에 케이스 8개가 쌓인다. 그렇다고 무시하면
**"언제부터 이랬나"가 아무 데도 안 남는다.**

첨부하면 케이스가 `관측 4회 · 9시간째`를 들고 있고, 그게 조사와 보고서의 재료가 된다.
"지금 0/0/0이다"와 "9시간째 0/0/0이다"는 다른 사실이다.
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal

from src.domain.base import StrictModel
from src.domain.concern import Concern

Fingerprint = tuple[str, str, str]      # (site, check, target)


def fingerprint(site: str, check: str, target: str) -> Fingerprint:
    return (site, check, target)


class CaseRecord(StrictModel):
    """순찰이 연 케이스 하나. **조사 엔진의 `Case`와 다르다** — 이건 저장되는 쪽이다."""

    id: str
    site: str
    check: str
    target: str
    concern: Concern
    status: Literal["open", "closed"] = "open"
    symptom: str
    opened_at: datetime
    # 마지막으로 이 이상이 **관측된** 시각. 첨부할 때마다 갱신된다.
    last_seen_at: datetime
    observations: int = 1
    # 처음 본 값. **게이트 통과 시점에 다시 읽지 않는다** — 그 사이 값이 바뀌면
    # 판정과 케이스가 어긋나서, 보고서가 "0/0/0이라 열었다"는데 케이스엔 다른 값이 실린다.
    observed: dict[str, Any] = {}
    closed_at: datetime | None = None
    # 닫힌 **뒤에** 이 대상이 정상으로 관측된 시각. 재개설의 유일한 조건이다 —
    # 이게 없으면 안 고쳐진 문제로 3시간마다 케이스가 다시 쌓인다.
    cleared_at: datetime | None = None

    @property
    def fingerprint(self) -> Fingerprint:
        return fingerprint(self.site, self.check, self.target)

    def sustained_for(self, now: datetime) -> str:
        """사람이 읽을 지속 시간. "지금 이상"과 "9시간째 이상"은 다른 사실이다."""
        minutes = int((now - self.opened_at).total_seconds() // 60)
        if minutes < 60:
            return f"{minutes}분째"
        return f"{minutes // 60}시간 {minutes % 60}분째"


class CaseRepositoryPort(ABC):
    """케이스 저장소. **절대 조용히 비우지 않는다** — 구현 설명 참고."""

    @abstractmethod
    def latest(self, key: Fingerprint) -> CaseRecord | None:
        """그 지문의 가장 최근 케이스(열렸든 닫혔든). 없으면 None."""

    @abstractmethod
    def add(self, record: CaseRecord) -> None:
        """새 케이스를 넣는다."""

    @abstractmethod
    def update(self, record: CaseRecord) -> None:
        """있는 케이스를 갱신한다."""

    @abstractmethod
    def all(self) -> list[CaseRecord]:
        """전부. 최근에 열린 것부터."""

    @abstractmethod
    def next_id(self) -> str:
        """다음 케이스 id. 저장소가 센다 — 호출부가 세면 재시작할 때 되돌아간다."""
