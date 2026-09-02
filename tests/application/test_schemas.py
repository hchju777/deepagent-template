from src.application.schemas import FrameOutput, IntegrateOutput, parse_structured


def test_json_펜스와_생짜_JSON_모두_파싱():
    fenced = '설명...\n```json\n{"status": "ok", "summary": "s"}\n```'
    from src.application.schemas import SubagentReport
    obj, err = parse_structured(fenced, SubagentReport)
    assert err is None and obj.status == "ok"
    raw = '{"status": "error", "summary": "s", "error": "boom"}'
    obj2, _ = parse_structured(raw, SubagentReport)
    assert obj2.status == "error"


def test_파싱_실패는_raise가_아니라_원인_반환():
    from src.application.schemas import SubagentReport
    obj, err = parse_structured("JSON 없음", SubagentReport)
    assert obj is None and err is not None
    obj2, err2 = parse_structured('{"status": "ghost"}', SubagentReport)
    assert obj2 is None and "ghost" in err2


def test_ask_결정에는_question_필수():
    import pytest
    from pydantic import ValidationError
    IntegrateOutput(decision="ask", question="계획 변경이 있었나요?")
    with pytest.raises(ValidationError):
        IntegrateOutput(decision="ask")
