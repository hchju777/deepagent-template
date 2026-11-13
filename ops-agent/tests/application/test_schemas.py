"""LLM 텍스트에서 구조를 꺼내는 관용 범위 — **어디까지 받아 주고 어디서 거부하는가.**

관용이 너무 좁으면 멀쩡한 답이 "형식 오류"로 버려지고, 너무 넓으면 모델이 지어낸
필드가 State에 조용히 들어간다. 그 선이 여기 적혀 있다.
"""
from typing import Literal

from src.application.schemas import parse_object, strip_fence, validate
from src.domain.base import StrictModel


class Reply(StrictModel):
    decision: Literal["continue", "conclude"]
    count: int = 0


class Item(StrictModel):
    name: str


class Nested(StrictModel):
    decision: str
    items: list[Item] = []


# ── 울타리 ────────────────────────────────────────────────────────

def test_코드펜스로_감싼_JSON도_파싱된다():
    """모델에게 "JSON만"이라고 해도 울타리를 두르는 일이 흔하다.

    거부하면 **내용은 정확한 답**이 형식 때문에 버려지고, 그 라운드는 빈손이 된다.
    """
    parsed = parse_object('```json\n{"decision": "continue"}\n```')
    assert parsed.ok and parsed.data == {"decision": "continue"}


def test_언어_표시가_없는_코드펜스도_벗긴다():
    assert parse_object('```\n{"a": 1}\n```').data == {"a": 1}


def test_울타리가_아니면_그대로_둔다():
    assert strip_fence('  {"a": 1}  ') == '{"a": 1}'


def test_울타리_벗기기_자체를_따로_고정한다():
    """`parse_object`의 "첫 `{`부터 마지막 `}`까지" 재시도가 울타리도 같이
    구해 주기 때문에, **위 테스트만으로는 `strip_fence`를 지울 수 있다.**
    RED 확인에서 실제로 그랬다 — 둘은 독립된 두 겹이므로 각각 고정한다.
    """
    assert strip_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fence('```\n설명만 있고 JSON이 아니다\n```') == '설명만 있고 JSON이 아니다'


def test_앞뒤에_말이_붙어도_객체를_꺼낸다():
    """"네, 아래와 같습니다:"는 형식 위반이지 답이 틀린 것이 아니다."""
    parsed = parse_object('네, 아래와 같습니다:\n{"decision": "conclude"}\n이상입니다.')
    assert parsed.ok and parsed.data == {"decision": "conclude"}


# ── 거부 ──────────────────────────────────────────────────────────

def test_빈_응답은_실패다():
    parsed = parse_object("   ")
    assert not parsed.ok and parsed.error


def test_JSON이_아니면_실패지만_던지지는_않는다():
    """여기서 던지면 LangGraph superstep이 죽는다 — 실패는 값이다."""
    parsed = parse_object("그건 제가 알 수 없습니다.")
    assert not parsed.ok and "JSON" in parsed.error


def test_객체가_아닌_JSON은_거부한다():
    """배열이 오면 `data["tasks"]`가 TypeError로 죽는다 — 여기서 잡는 게 낫다."""
    parsed = parse_object("[1, 2, 3]")
    assert not parsed.ok and "list" in parsed.error


# ── 검증 ──────────────────────────────────────────────────────────

def test_모델이_지어낸_필드는_걷히고_나머지는_산다():
    """규율 5의 **좁은 예외**다 — LLM 응답 봉투만.

    약한 모델은 `"confidence": 0.9` 같은 곁다리를 자주 붙인다. 응답 전체를 버리면
    **멀쩡한 조사 계획이 라운드째 날아간다.** 곁다리는 형식 실수이지 계획이 틀린
    것이 아니다. config·도메인 모델의 `extra="forbid"`는 그대로다.
    """
    checked = validate({"decision": "continue", "confidence": 0.9}, Reply)
    assert checked.ok and checked.data == {"decision": "continue", "count": 0}
    assert checked.dropped == ("confidence",)      # 조용히 고치지 않는다


def test_중첩된_곁다리도_걷힌다():
    """태스크 하나에 `"reason"`을 붙이는 것이 실제로 흔한 모양이다."""
    checked = validate({"decision": "continue",
                        "items": [{"name": "t-1", "reason": "왜냐하면"}]}, Nested)
    assert checked.ok and checked.dropped == ("items.0.reason",)
    assert checked.data["items"] == [{"name": "t-1"}]


def test_값이_틀린_것은_여전히_거부한다():
    """**걷어내기가 여기까지 넓어지면 안 된다.**

    `"decision": "maybe"`는 형식 실수가 아니라 모델이 우리 어휘를 안 따른 것이고,
    걷어낼 수도 없다. 그 실패는 수리 재시도로 가야 한다.
    """
    checked = validate({"decision": "maybe"}, Reply)
    assert not checked.ok and "continue" in checked.error


def test_곁다리와_값_오류가_섞이면_거부한다():
    """걷어내기로 구할 수 있는 응답이 아니다 — 하나라도 다른 오류가 있으면 전부 거부."""
    checked = validate({"decision": "maybe", "confidence": 1}, Reply)
    assert not checked.ok


def test_검증_실패는_예외가_아니라_값이다():
    checked = validate({"count": "셋"}, Reply)
    assert not checked.ok
    # 어느 필드가 왜 틀렸는지 사람이 읽을 수 있어야 프롬프트를 고칠 수 있다.
    assert "decision" in checked.error and "count" in checked.error


def test_통과하면_기본값까지_채워서_돌려준다():
    checked = validate({"decision": "conclude"}, Reply)
    assert checked.ok and checked.data == {"decision": "conclude", "count": 0}
