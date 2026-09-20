"""LLM 텍스트에서 구조를 꺼내는 관용 범위 — **어디까지 받아 주고 어디서 거부하는가.**

관용이 너무 좁으면 멀쩡한 답이 "형식 오류"로 버려지고, 너무 넓으면 모델이 지어낸
필드가 State에 조용히 들어간다. 그 선이 여기 적혀 있다.
"""
from src.application.schemas import parse_object, strip_fence, validate
from src.domain.base import StrictModel


class Reply(StrictModel):
    decision: str
    count: int = 0


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

def test_모델이_지어낸_필드는_거부된다():
    """`extra="forbid"`가 여기서 값을 한다(규율 5).

    받아 주면 `{"decision": "continue", "confidence": 0.9}`의 `confidence`가
    State에 섞여 들어가고, 아무도 안 쓰는데 보고서에는 실릴 수 있다.
    """
    checked = validate({"decision": "continue", "confidence": 0.9}, Reply)
    assert not checked.ok and "confidence" in checked.error


def test_검증_실패는_예외가_아니라_값이다():
    checked = validate({"count": "셋"}, Reply)
    assert not checked.ok
    # 어느 필드가 왜 틀렸는지 사람이 읽을 수 있어야 프롬프트를 고칠 수 있다.
    assert "decision" in checked.error and "count" in checked.error


def test_통과하면_기본값까지_채워서_돌려준다():
    checked = validate({"decision": "conclude"}, Reply)
    assert checked.ok and checked.data == {"decision": "conclude", "count": 0}
