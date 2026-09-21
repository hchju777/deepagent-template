"""조사가 **스스로 건강 상태를 잰다.**

이 파일이 있는 이유: 여기 있는 숫자들을 지금까지 사람이 CLI 출력을 옮겨 적고
손으로 셌다. 그렇게 해서 버그 둘을 찾았고(같은 id 재사용 / id만 다른 같은 질의),
둘 다 **표를 그려 봐야** 보였다. 손으로 셀 수 있는 것은 도구가 센다.
"""
from src.application.diagnose import diagnose
from src.application.state import CaseState
from src.domain.case import EvidenceRef, Hypothesis

from tests.application.conftest import task


def block(state: CaseState) -> str:
    return "\n".join(diagnose(state))


def test_중복_질의를_실제로_센다(case):
    """**사람이 손으로 세던 것이다.** id도 goal도 다르지만 나가는 질의가 같다 —
    사내 모델이 실제로 이렇게 했고, 표를 그려 봐야 보였다."""
    same = {"action": "mongo.find", "params": {"collection": "alarm", "filter": {}}}
    state = CaseState(case=case, round=4, stopped_by="max_rounds", plan_tasks=[
        task("t-4", goal="데이터가 있는지 확인", status="ok", **same),
        task("t-6", goal="값이 정상인지 확인", status="ok", **same),
        task("t-8", goal="값이 진짜 0인지 확인", status="ok", **same)])
    assert "읽기 3회 · 서로 다른 질의 1개 · **중복 2회**" in block(state)


def test_서로_다르면_중복이_아니다(case):
    state = CaseState(case=case, plan_tasks=[
        task("t-1", action="mongo.list_collections", params={}, status="ok"),
        task("t-2", action="kafka.list_topics", params={}, status="ok")])
    assert "중복 없음" in block(state)


def test_실행되지_않은_태스크는_읽기로_안_센다(case):
    """상한에 걸려 못 돈 태스크를 "읽었다"고 세면 조사가 실제보다 깊어 보인다."""
    state = CaseState(case=case, plan_tasks=[
        task("t-1", status="ok"), task("t-2", status="pending")])
    assert "읽기 1회" in block(state)


def test_계약_위반을_종류별로_묶는다(case):
    """같은 종류가 열 번 나면 **열 줄이 아니라 `10×` 한 줄**이어야 읽힌다."""
    state = CaseState(case=case, llm_errors=[
        "t-3: 이미 한 읽기를 또 냈다 — 받지 않는다 (mongo.find …)",
        "t-5: 이미 한 읽기를 또 냈다 — 받지 않는다 (kafka.tail …)",
        "h-1: 없는 증거를 인용했다 — t-9.e1"])
    text = block(state)
    assert "리드 계약 위반 3건" in text
    assert "2× 이미 한 읽기를 또 냈다" in text
    assert "1× 없는 증거를 인용했다" in text


def test_잘린_증거를_센다(case):
    """잘린 표본으로는 "없다"를 주장할 수 없다 — 몇 건이 그런지가 판정의 재료다."""
    state = CaseState(case=case, evidence=[
        EvidenceRef(id="t-1.e1", source="redis.scan", summary="200건", complete=False),
        EvidenceRef(id="t-2.e1", source="mongo.find", summary="5건")])
    assert "증거 2건 · 잘린 것 1건" in block(state)


def test_가설을_상태별로_센다(case):
    state = CaseState(case=case, hypotheses=[
        Hypothesis(id="h-1", statement="가", status="supported"),
        Hypothesis(id="h-2", statement="나", status="refuted"),
        Hypothesis(id="h-3", statement="다")])
    assert "가설 3개 (open 1 · refuted 1 · supported 1)" in block(state)


def test_질의_목록이_goal이_아니라_나간_것을_적는다(case):
    """`goal`과 `params`가 따로 논다 — 모델이 goal은 창작하고 params는 베낀다.
    **실제로 나간 것**을 적어야 무엇을 했는지가 사실대로 남는다."""
    state = CaseState(case=case, plan_tasks=[task(
        "t-6", goal="전체 레코드 수를 센다", status="ok",
        action="mongo.find", params={"collection": "alarm", "filter": {}, "limit": 5})])
    text = block(state)
    assert "mongo.find collection='alarm' filter={} limit=5" in text
    assert "전체 레코드 수를 센다" not in text


def test_무엇이_잘렸는지_말한다(case):
    """**"잘린 것 2건"만으로는 다음 라운드가 짐작이 된다.**

    사내 실행에서 실제로 그랬다 — 리드가 이름을 못 본 건지 아닌지 알 수 없어서,
    무엇이 잘렸는지 확인하는 데 왕복이 한 번 더 들었다.
    """
    state = CaseState(case=case, evidence=[
        EvidenceRef(id="t-1.e1", source="code.config api", summary="…", complete=False),
        EvidenceRef(id="t-2.e1", source="mongo.find collection='alarm'", summary="…"),
    ])
    text = "\n".join(diagnose(state))
    assert "잘린 것 1건" in text
    assert "✂ code.config api" in text
    assert text.count("✂") == 1, "안 잘린 것까지 적으면 신호가 뜻을 잃는다"
