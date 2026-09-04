"""사람이 연 케이스의 개설 — 접수보다 **먼저** 일어난다 (스펙 §4.4).

지금 `_drive_chat`은 `await intake(...)`가 끝난 뒤에야 `repo.new_case_id()`를
부른다. CLI에서는 프로세스 하나가 처음부터 끝까지 서 있어 드러나지 않지만,
HTTP에서는 셋이 함께 깨진다 — 첫 요청이 돌려줄 case_id가 없고, 접수 중 클라이언트가
끊기거나 서버가 재시작되면 문답이 통째로 사라지고, 그 사이의 상태를 담을 곳이 없다.

**개설을 한 함수에 모으는 이유**: 케이스를 여는 코드가 지금 `gate.py`(순찰)와
`__main__.py`(chat) 둘에 있고 계획 13이 세 번째를 만든다. 케이스 **종결**의 세
경로가 발행 배선을 각자 베껴 하나가 빠뜨렸던 것(규율 8)과 같은 구조가 개설 쪽에서
반복되기 직전이다. 순찰은 finding에서 열고 사람은 증상에서 여는 등 재료가 달라
`gate.py`까지 합치지는 않지만, **사람이 여는 경로는 CLI든 HTTP든 여기 하나다.**
"""
from datetime import datetime
from typing import Callable

from src.application.events import case_status_event
from src.domain.cases import CaseRecord, CaseRepositoryPort
from src.domain.concern import Concern
from src.domain.patrol import fingerprint
from src.domain.store import CaseStorePort


def open_case(*, repo: CaseRepositoryPort, store: CaseStorePort, symptom: str,
              gbm: str, fct: str, concern: Concern, requested_by: str | None,
              clock: Callable[[], datetime], on_event: Callable) -> CaseRecord:
    """확정된 스코프와 원문 증상만으로 케이스를 연다. `target_locator`는 접수가 채운다.

    지문에 `case_id`가 들어가 **사람이 연 케이스는 서로 절대 같은 지문을 갖지
    않는다** — 이력 매칭·중복 억제가 human 케이스에는 사실상 동작하지 않는다는 뜻이고,
    알려진 결함이다. 여기서 고치려면 개설 시점에 아직 없는 `target_locator`를 재료로
    써야 해서 더 나빠진다. P8(이력 검색)이 함께 갚는다 — **그 전에 이력 매칭을 얹으면
    조용히 안 맞는다.**
    """
    case_id = repo.new_case_id()
    now = clock()
    record = CaseRecord(
        id=case_id, gbm=gbm, fct=fct,
        fingerprint=fingerprint(gbm, fct, "chat", case_id),
        symptom=symptom, t0=now, target_locator=None,
        origin="human", concern=concern, requested_by=requested_by,
        status="open", created_at=now, updated_at=now)
    repo.save(record)

    # 접수가 증상을 다듬어도 사람이 처음 쓴 문장이 남아야 한다 — 판정이 "무엇을
    # 물었나"를 되짚을 유일한 근거다. gate가 finding 스냅샷을 복사하는 것과 같은 자리.
    store.put_evidence(case_id, "human:symptom", {"symptom": symptom}, as_of=now)

    try:
        on_event(case_status_event(case_id, "open", clock=clock))
    except Exception:                       # noqa: BLE001 — 발행은 개설을 뒤집을 수 없다
        pass
    return record
