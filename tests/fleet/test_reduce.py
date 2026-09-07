"""점 경로 추출과 감축 6종 — 순수 함수(계획 16/P7)."""
import pytest

from src.fleet.reduce import REDUCERS, extract, reduce_values


def test_점_경로로_스칼라와_리스트를_뽑는다():
    assert extract({"summary": {"alarms": 12}}, "summary.alarms") == ([12.0], 0)
    rows = {"sites": [{"n": 1}, {"n": 2}, {"n": 3}]}
    assert extract(rows, "sites.n") == ([1.0, 2.0, 3.0], 0)      # 리스트는 각 항목에서
    assert extract({"a": 1}, "없는.경로") == ([], 1)              # 없으면 건너뛴 수로 센다


def test_숫자가_아닌_값은_조용히_버리지_않고_센다():
    # 호출부가 그 수를 보고 불완전으로 판정한다 — 조용한 스킵은 분모를 거짓으로 만든다.
    values, skipped = extract({"rows": [{"n": 1}, {"n": "많음"}, {"n": None}]}, "rows.n")
    assert values == [1.0] and skipped == 2
    assert extract({"n": True}, "n") == ([], 1)                   # bool은 수가 아니다


@pytest.mark.parametrize("how, expected", [
    ("sum", 6.0), ("avg", 2.0), ("max", 3.0), ("min", 1.0),
    ("count", 3.0), ("count_nonzero", 2.0)])
def test_감축_6종(how, expected):
    assert reduce_values([1.0, 2.0, 3.0] if how != "count_nonzero" else [0.0, 2.0, 3.0],
                         how) == expected


def test_빈_표본은_0이_아니라_None이다():
    # count도 마찬가지다 — "아무 데서도 못 읽었다"를 0건으로 적으면 거짓말이 된다.
    for how in REDUCERS:
        assert reduce_values([], how) is None


def test_알_수_없는_감축은_None이다():
    assert reduce_values([1.0], "median") is None      # DSL 금지 — 6종뿐이다


def test_인덱스_세그먼트로_리스트에_접근한다():
    # 리뷰 M-10: 리스트 팬아웃이 먼저 소진해 인덱스 분기가 죽은 코드였고, 오타난
    # 인덱스 경로가 skipped를 부풀려 불완전 사유를 조작했다.
    rows = {"rows": [{"n": 1}, {"n": 2}, {"n": 3}]}
    assert extract(rows, "rows.0.n") == ([1.0], 0)
    assert extract(rows, "rows.2.n") == ([3.0], 0)
    assert extract(rows, "rows.9.n") == ([], 1)      # 범위 밖은 한 건만 센다
    assert extract(rows, "rows.n") == ([1.0, 2.0, 3.0], 0)   # 인덱스가 없으면 팬아웃
