"""조사 사건 하나를 이루는 것들 — 케이스·가설·계획 태스크·증거 참조.

## 왜 수명주기 필드가 모델에 있는가

`PlanTask.status`는 LLM이 만드는 객체 안에 있지만 **LLM이 정하지 않는다.** frame이
`{"status": "ok", "result_summary": "확인함"}`을 실어 보내면 그 태스크는 실행되지 않은
채로 "끝난 것"이 되고, select 게이트를 통째로 우회한다. 필드를 빼면 그 구멍이
막히는 것처럼 보이지만, 그러면 코드도 상태를 못 적는다.

그래서 필드는 두고 **들어오는 길목에서 코드가 덮어쓴다**(`nodes._sanitize_task`).
경계가 어디인지를 한 곳으로 모으는 쪽이, 모델을 쪼개 놓고 "여기는 안 믿는다"를
여러 곳에 적는 것보다 안 틀린다.

## 증거 id는 왜 태스크 id에서 파생되는가

`ev-1`, `ev-2`처럼 전역 순번을 쓰려면 번호를 나눠 주는 곳이 하나 있어야 한다.
그런데 태스크는 **한 라운드에 여러 개가 동시에** 실행되고, 병렬 가지들은 서로의
State를 못 본다 — 공유 카운터를 두면 같은 번호가 두 번 나가거나, 실행 순서에 따라
번호가 달라져서 **같은 입력에 같은 결과가 안 나온다.**

`t-1.e1`처럼 태스크 id에서 파생하면 나눠 줄 것이 없어 충돌이 불가능하고, 덤으로
"이 증거가 어느 태스크에서 나왔나"가 id에 적혀 있다.
"""
from datetime import datetime
from typing import Literal

from src.domain.base import StrictModel

# 서브에이전트 역할(11b). 10a는 이 값을 읽지 않지만, 태스크에 역할이 없으면
# frame이 "무엇을 시킬지"를 표현할 방법이 없어 계획이 라운드마다 달라진다.
Role = Literal["data_prober", "code_tracer", "recompute_verifier"]

TaskStatus = Literal["pending", "running", "ok", "error", "cancelled"]


class EvidenceRef(StrictModel):
    """State에 남는 증거 한 줄. **실제로 일어난 읽기만 여기 올라온다.**"""

    id: str
    source: str                       # 무엇을 물었는가 — "redis.get key=oee:L3"
    summary: str
    as_of: datetime | None = None
    # 표본이 잘렸는가. 잘린 표본으로는 "없다"를 주장할 수 없다 — 12a의 verify가 본다.
    complete: bool = True

    @staticmethod
    def make_id(task_id: str, n: int) -> str:
        """`t-1.e1`. 모듈 맨 위 설명 참고 — 전역 카운터를 두지 않기 위해서다."""
        return f"{task_id}.e{n}"


class Hypothesis(StrictModel):
    id: str
    statement: str
    status: Literal["open", "supported", "refuted"] = "open"
    supporting_ids: list[str] = []
    refuting_ids: list[str] = []


class PlanTask(StrictModel):
    """"무엇을 볼 것인가" 하나."""

    id: str
    goal: str
    role: Role
    # 실행기가 읽는 등재 항목 이름과 인자(`ProbeRunner`). 11b의 서브에이전트는
    # 스스로 도구를 고르므로 이 둘을 안 본다 — 실행기마다 필요한 것이 다르다.
    action: str | None = None
    params: dict = {}
    # select 게이트: 여기 적힌 id가 **전부** State에 실재해야 실행 가능하다.
    # 재계산 태스크는 앞선 라운드의 증거를 입력으로 받으므로 이게 없으면
    # frame의 1차 계획이 라운드 1에 통째로 발사돼 입력 없이 전멸한다.
    input_evidence_ids: list[str] = []
    priority: int = 100                   # 낮을수록 먼저, 동률이면 FIFO
    status: TaskStatus = "pending"
    result_summary: str | None = None
    result_evidence_ids: list[str] = []
    error: str | None = None


class Case(StrictModel):
    """조사 사건 하나.

    `origin`이 두 입구(사람·순찰)를 가르는 **유일한** 필드다. 나머지 경로가 같아야
    원인 판정 로직이 한 벌로 유지된다.
    """

    id: str
    gbm: str
    fct: str
    origin: Literal["human", "patrol"]
    symptom: str
    t0: datetime

    @property
    def site(self) -> str:
        return f"{self.gbm}/{self.fct}"
