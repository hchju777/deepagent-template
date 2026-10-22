"""스케줄 설정 — **언제 도는가는 config가 정한다.**

## 왜 cron과 interval 둘 다인가

운영은 cron이다("매일 8시"). 그런데 **확인할 때는 1분 간격**이 필요하다 — cron으로
바꿔 가며 시험하려면 시계를 기다리거나 식을 매번 고쳐야 하고, 그러다 운영 식을
고쳐 둔 채로 배포하는 사고가 난다. 다른 이름의 칸으로 두면 시험이 운영 값을
건드리지 않는다.

## 왜 시나리오마다인가

리포트마다 주기가 다르다(일간 8시, 주간 월요일). 앱 전역에 하나만 두면 두 번째
리포트를 추가하는 순간 못 쓴다.
"""
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, model_validator

from src.domain.base import StrictModel
from src.schedule.cron import CronError, next_fire


class ScheduleSpec(StrictModel):
    # 이 명령(`schedule`)을 부른 것 자체가 "스케줄대로 돌려라"이므로 기본은 켬이다.
    # 이 칸은 **시나리오 하나만 빼는 스위치**다.
    enabled: bool = True
    kind: Literal["cron", "interval"] = "cron"
    cron: str = "0 8 * * *"
    # 하한이 1초인 이유: 0이면 발사 사이에 잠이 없어 한 코루틴이 루프를 독점한다.
    interval_seconds: int = Field(default=60, ge=1)
    # 켜면 뜨자마자 한 번 돌고 그다음부터 스케줄을 탄다. 배선 확인용이다.
    run_on_start: bool = False

    @model_validator(mode="after")
    def _cron_is_readable(self):
        # kind가 interval이어도 검사한다. 안 하면 잘못된 식이 **바꾸는 날**에야
        # 드러나고, 그날은 보통 급할 때다.
        try:
            next_fire(self.cron, datetime(2000, 1, 1))
        except CronError as exc:
            raise ValueError(f"cron 식을 읽을 수 없다 — {exc}") from exc
        return self

    def next_after(self, now: datetime, *, anchor: datetime) -> datetime:
        """다음 발사 시각. `anchor`는 프로세스가 뜬 시각(interval의 기준점)이다.

        interval을 "직전 실행 + 간격"이 아니라 **기준점 + N×간격**으로 세는 이유:
        앞의 방식은 실행에 걸린 시간만큼 매번 밀려서, 1분 간격이 며칠 뒤에는 전혀
        다른 시각에 돈다. 기준점을 잡으면 한 번 오래 걸려도 박자가 돌아온다.
        """
        if self.kind == "cron":
            return next_fire(self.cron, now)
        step = self.interval_seconds
        elapsed = (now - anchor).total_seconds()
        # 이미 지난 슬롯은 건너뛴다 — 밀린 만큼 몰아서 쏘지 않는다.
        slots = int(elapsed // step) + 1
        return anchor + timedelta(seconds=slots * step)

    def describe(self) -> str:
        if not self.enabled:
            return "꺼짐"
        if self.kind == "cron":
            return f"cron {self.cron}"
        return f"{self.interval_seconds}초마다"
