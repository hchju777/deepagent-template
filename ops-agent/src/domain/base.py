"""모든 모델의 공통 조상과, 시간을 다루는 유일한 방법.

## StrictModel

pydantic의 기본값은 모르는 키를 **조용히 버린다**. config에 `tls_verfiy`라고
오타를 내면 기본값(검증 켬)이 그대로 돌고 아무도 모른다 — 사람은 자기가 껐다고
믿는다. `extra="forbid"`는 그 순간을 검증 오류로 바꾼다.

## Clock

`datetime.now()`를 함수 안에서 직접 부르면 그 함수는 테스트할 수 없다.
"3분 지난 데이터를 stale로 볼 것인가"를 판정하는 코드는 실행 시각에 따라
답이 달라지므로, 시각은 **인자로 받는다**. CLI 진입점 한 곳만 진짜 시계를
주입하고, 나머지 전부는 받아 쓴다.
"""
from datetime import datetime
from typing import Callable

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# "지금 몇 시인가"를 묻는 유일한 표면. 전역도 기본 인자도 아니고 인자다.
Clock = Callable[[], datetime]
