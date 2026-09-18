"""**선언만 되고 아무도 안 읽는 config 항목**을 잡는다.

## 왜 이 파일이 있는가

`app.json`의 `timezone`이 그랬다. 스키마에 있고, 기동이 값을 검증까지 하는데,
시계는 그 값을 **안 읽고** 기계의 시스템 TZ를 따랐다. 사내가 KST라 우연히 맞아서
한동안 아무도 몰랐고, UTC 컨테이너로 옮기는 순간 리포트가 한 평일씩 밀렸을 것이다.

그 하나를 고치고 나서 같은 규칙으로 전체를 훑으니 둘이 더 나왔다 —
`app.output_dir`과 `comment.per_gbm`. 사람이 config에 적어도 **아무 일이 일어나지
않는** 칸이고, 그 실패는 조용하다.

죽은 칸은 잘못된 안전감도 준다. `read_only: true` 같은 체크박스를 두지 않는 이유와
같다(CLAUDE.md 규율 9) — 아무도 생각하지 않는 칸의 가치는 0이 아니라 음수다.

## 어떻게 보는가

`src/` 어딘가에서 `.필드명`으로 읽는 곳이 있는지 본다. 선언부(`이름: 타입 = 기본값`)
에는 점이 없으므로 자기 자신에는 안 걸리고, `self.필드`로 읽는 같은 파일 안의
메서드(`SourceSpec.status_label`, `WindowSpec.excluded`)에는 제대로 걸린다.

**정적 검사의 한계는 정직하게 적는다**: `getattr(cfg, name)`처럼 동적으로 읽으면
못 본다. 그때는 아래 허용 목록에 이유와 함께 적어라 — 목록에 적는 행위 자체가
"이건 생각해 봤다"는 기록이다.
"""
import inspect
import re
from pathlib import Path

import pydantic

from src.config import (schema_app, schema_llm, schema_mail, schema_report,
                        schema_schedule, schema_site)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 동적으로 읽혀서 정적으로는 안 보이는 것들. **비워 두는 것이 정상**이다.
ALLOWED: dict[str, str] = {}


def _models() -> list[type[pydantic.BaseModel]]:
    found = []
    for module in (schema_app, schema_report, schema_schedule, schema_llm,
                   schema_mail, schema_site):
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (issubclass(obj, pydantic.BaseModel)
                    and obj.__module__ == module.__name__):
                found.append(obj)
    return sorted(set(found), key=lambda m: m.__name__)


_source_cache: list[str] | None = None


def _source_lines() -> list[str]:
    """`src/`의 모든 .py 줄. 한 번 읽어 재사용한다.

    **`grep`을 부르지 않는 이유**: 사내는 Windows이고 거기엔 `grep`이 없다. git-bash가
    PATH에 있으면 우연히 돌고 없으면 `FileNotFoundError`로 죽는데, 둘 다 "이 테스트가
    무엇을 지키는가"와 아무 상관이 없다. 그리고 `subprocess.run`은 기본이
    `stdout=None`이라 `capture_output`을 빠뜨리면 `'NoneType' object has no attribute
    'splitlines'`로 죽는다 — 실제로 그렇게 죽은 복사본이 있었다.

    파이썬으로 읽으면 셋 다 사라진다. 트리가 작아서 비용도 없다.
    """
    global _source_cache
    if _source_cache is None:
        _source_cache = [line
                         for path in sorted((PROJECT_ROOT / "src").rglob("*.py"))
                         for line in path.read_text(encoding="utf-8").splitlines()]
    return _source_cache


def _is_read(field: str) -> bool:
    """`src/` 어딘가에서 `.필드명`으로 읽는가. **import 줄은 빼고 본다.**

    빼지 않으면 모듈 이름과 같은 필드가 자기도 모르게 통과한다. `AppConfig.investigation`을
    추가했을 때 실제로 그랬다 — 읽는 코드가 하나도 없는데 `from src.domain.investigation
    import ...` 세 줄이 매치해서 초록이었다. **죽은 칸을 잡는 테스트가 죽은 칸을
    놓치는 것**이라 그냥 버그보다 나쁘다(이 테스트가 있다는 이유로 아무도 다시 안 본다).
    """
    pattern = re.compile(rf"\.{re.escape(field)}\b")
    for line in _source_lines():
        code = line.lstrip()
        if code.startswith(("import ", "from ")):
            continue
        if pattern.search(code):
            return True
    return False


def test_읽는_곳이_없는_설정_항목은_없다():
    dead = [f"{model.__name__}.{field}"
            for model in _models() for field in model.model_fields
            if not _is_read(field)
            and f"{model.__name__}.{field}" not in ALLOWED]

    assert not dead, (
        f"선언만 되고 읽는 곳이 없는 설정 항목 — {dead}. "
        "배선하거나 빼라. 사람이 적어도 아무 일이 안 일어나는 칸은 조용히 거짓말을 한다"
        " (`timezone`이 실제로 그랬다). 동적으로 읽는다면 ALLOWED에 이유와 함께 적어라")


def test_모델을_실제로_찾는다():
    """검사기가 빈 목록을 훑고 초록불을 내는 것을 막는다."""
    names = {model.__name__ for model in _models()}

    assert {"AppConfig", "ReportScenario", "ScheduleSpec", "MailConfig"} <= names
    assert len(names) >= 10, names
