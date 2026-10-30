"""관측 하나를 담는 봉투와, 그 관측이 성공했는지 실패했는지.

## 왜 실패가 예외가 아니라 값인가

Redis가 안 붙거나 Mongo가 타임아웃 나는 것은 "예상 밖의 일"이 아니다 —
운영 중에 **정상적으로 일어나는 일**이다. 여기서 `raise`하면 그 예외는
순찰 잡을 타고 올라가 스케줄러까지 간다. APScheduler는 예외를 낸 잡을
조용히 스케줄에서 빼버릴 수 있고, 그러면 **순찰이 스스로 죽어도 아무도
모르는** 상태가 된다. 밤새 아무 케이스도 안 열리는데 그게 "이상이 없어서"인지
"순찰이 죽어서"인지 구별할 수 없다.

그래서 실패는 `status="error"`로 흡수해서 **정상적으로 반환**한다. 호출자는
`try/except`가 아니라 `if result.status == "error"`로 분기한다.

## 왜 "잘렸다"를 따로 말하는가

표본 상한(`limit=100`)에 걸려 100건만 읽은 것과, 실제로 100건뿐인 것은
**완전히 다른 사실**이다. 전자를 후자로 착각하면 "최근 1시간에 불량이
없었다"는 결론이 나오는데 실제로는 상한 밖에 있었을 뿐이다.

부정 증거("없다")는 표본이 완전할 때만 성립한다. `complete=False`면
그 봉투로는 "없다"를 주장할 수 없고, 나중에 판정 검증 단계가 이걸 본다.
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import model_validator

from src.domain.base import Clock, StrictModel


class Envelope(StrictModel):
    """관측의 메타데이터 — 언제 봤고, 본 것이 전부인가."""

    observed_at: datetime
    complete: bool = True
    truncated_reason: str | None = None

    @model_validator(mode="after")
    def _completeness_and_reason_must_agree(self):
        # 양방향으로 막는다. "잘렸는데 이유가 없다"도 문제지만,
        # "안 잘렸는데 이유가 있다"도 읽는 사람을 헷갈리게 하는 모순이다.
        if not self.complete and not self.truncated_reason:
            raise ValueError("complete=False면 truncated_reason이 필요하다")
        if self.complete and self.truncated_reason:
            raise ValueError("complete=True인데 truncated_reason이 있다 — 둘 중 하나가 틀렸다")
        return self


class ProbeResult(StrictModel):
    """대상 시스템을 한 번 읽은 결과. 성공도 실패도 이 한 타입으로 표현된다.

    `source`는 **무엇을 물었는가**를 남긴다. 응답만 보관하면 "0건"이
    "현장이 멈췄다"인지 "질문을 잘못 던졌다"인지 구별할 수 없다.
    """

    status: Literal["ok", "error"]
    envelope: Envelope
    source: str
    data: Any = None
    error: str | None = None

    @model_validator(mode="after")
    def _status_and_error_must_agree(self):
        if self.status == "error" and not self.error:
            raise ValueError("status=error면 error 원인이 필요하다")
        if self.status == "ok" and self.error:
            raise ValueError("status=ok면 error가 없어야 한다")
        return self

    # ── 생성 헬퍼 ────────────────────────────────────────────────
    # 어댑터가 매번 Envelope를 손으로 조립하면 어딘가는 반드시 빠뜨린다.
    # **올바른 길이 제일 쉬운 길**이어야 규율이 지켜진다.

    @classmethod
    def succeeded(cls, data: Any, *, source: str, clock: Clock,
                  truncated_reason: str | None = None) -> "ProbeResult":
        """읽기에 성공했다. truncated_reason을 주면 자동으로 complete=False가 된다."""
        return cls(
            status="ok", data=data, source=source,
            envelope=Envelope(observed_at=clock(),
                              complete=truncated_reason is None,
                              truncated_reason=truncated_reason))

    @classmethod
    def failed(cls, error: str, *, source: str, clock: Clock) -> "ProbeResult":
        """읽기에 실패했다 — 이것도 정상적인 반환이다. 던지지 않는다."""
        return cls(status="error", error=error, source=source,
                   envelope=Envelope(observed_at=clock()))
