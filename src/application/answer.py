"""사람의 답 하나를 케이스에 넣는다 — 접수 답변과 조사 답변을 가르는 한 곳.

`awaiting_human`은 두 가지를 뜻한다: 그래프가 interrupt했거나(계획 4b), 접수가
되물었거나(계획 12). 답을 받은 쪽이 이 둘을 구별하지 못하면 접수 질문에 그래프
재개를 걸게 되는데, 그러면 아직 없는 스레드를 재개하려다 실패하고 그 실패가 F3
복구 경로를 타 **사람 눈에는 "답했는데 조사가 깨졌다"로 보인다.**

CLI(`case resume`)와 계획 13의 `POST /cases/{id}/answers`가 **이 함수 하나를**
쓴다. 분기를 각자 베끼면 언젠가 하나가 빠뜨린다 — 케이스 종결 세 경로가 발행
배선에서 겪은 그것이다(규율 8).
"""
from datetime import datetime
from typing import Any, Callable

from src.application.intake import intake_turn


async def answer_case(case_id: str, answer: str, *, repo, store, deps: Any, topology,
                      worker, clock: Callable[[], datetime],
                      max_intake_turns: int = 3,
                      interaction_policy: str = "autonomous") -> str:
    """답을 넣고 다음 단계까지 진행한다. 워커와 같은 어휘를 돌려준다.

    - 접수 질문이었으면 접수를 이어간다. 접수가 끝나면 **그대로 조사를 시작한다** —
      사람이 답한 뒤 아무 일도 안 일어나면 케이스가 `open`에 멈춰 있고, 그걸
      움직이는 것은 데몬의 주기 재큐뿐이라 CLI 사용자에게는 아무 반응이 없다.
    - 접수가 또 물으면 `awaiting_human` — 호출자가 `repo.get(...).question`을 읽는다.
    - 조사 질문이었으면(또는 계획 12 이전 레코드라 종류가 없으면) 그래프를 재개한다.
    """
    record = repo.get(case_id)
    if record is not None and record.question_kind == "intake":
        turn = await intake_turn(case_id, repo=repo, store=store, deps=deps,
                                 topology=topology, clock=clock, answer=answer,
                                 max_turns=max_intake_turns)
        if turn.status == "asking":
            return "awaiting_human"
        # done이든 error든 케이스는 조사 가능한 상태로 돌아와 있다(intake_turn 계약).
        # error도 조사에 넣는 이유: 대상 없이 조사하는 것이 기존 "이중 실패"의
        # 착지점이고, 여기서 멈추면 사람이 답한 케이스가 조용히 방치된다.
        return await worker.run_once(case_id, interaction_policy=interaction_policy)
    return await worker.resume_once(case_id, answer)
