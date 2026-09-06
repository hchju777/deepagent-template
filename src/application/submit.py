"""명령 채널 — 사람의 답을 레코드에 싣는다. 실행은 워커의 몫이다 (스펙 §3.6).

v1 인계 노트가 "데몬이 파킹 케이스를 자동으로 재개하려면 사람의 답을 실어 나를
프로세스 밖 명령 채널이 필요한데 그것이 아직 없다"고 적어 뒀다. 이것이 그 채널이다.
새 큐나 컬렉션이 아니라 레코드 위의 필드 둘(`pending_answer`, `answer_key`)이다 —
`patrol`이 단일 인스턴스라 소비 경합이 없고, 저장소가 이미 내구성을 준다.

`api`는 이 함수만 부른다. `run_once`/`resume_once`를 부르지 않는다 — 부르는 순간
`api` 풀 전체가 실행자가 되고 lease가 그 사이를 중재해야 한다(스펙 §3.1).
"""
from datetime import datetime
from typing import Callable, Literal

SubmitResult = Literal["accepted", "duplicate", "pending", "not_waiting", "not_found"]


def submit_answer(case_id: str, answer: str, *, key: str, repo,
                  clock: Callable[[], datetime]) -> SubmitResult:
    """답을 싣는다. 절대 raise하지 않는다.

    - `duplicate`: 같은 키가 이미 왔다(소비됐든 아니든). 클라이언트 재시도가 답을
      두 번 넣어 F3 복구를 두 번 태우는 것을 막는다. `answer_key`를 소비 뒤에도
      지우지 않는 이유다.
    - `pending`: 아직 소비되지 않은 다른 답이 있다. 덮어쓰지 않는다 — 어느 쪽이
      사람의 뜻인지 여기서 알 수 없고, 워커가 먼저 것을 집어 가면 그때 다시 온다.
    """
    try:
        record = repo.get(case_id)
    except KeyError:
        return "not_found"
    if record.answer_key == key:
        return "duplicate"
    if record.status != "awaiting_human":
        return "not_waiting"
    if record.pending_answer is not None:
        return "pending"
    repo.save(record.model_copy(update={"pending_answer": answer, "answer_key": key,
                                        "updated_at": clock()}))
    return "accepted"
