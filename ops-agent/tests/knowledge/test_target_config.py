"""대상 config 층 병합 — **우리 규칙이 아니라 대상 규칙**으로 합치는가.

이 파일에서 제일 중요한 것은 마지막 테스트다: 우리 `deep_merge`와 **다르다는 것
자체**를 단정한다. 둘이 비슷하게 생겼다고 나중에 누가 합치면, 그 순간 대상의
`null` 값이 "그 키는 없다"로 둔갑한다 — 조용하고, 그럴듯하고, 틀린 값이 된다.
"""
import pytest

from src.knowledge.target_config import merge_target, parse_layer


def test_나중_층이_덮는다():
    """사내 확인: "순서대로 보면서 나중에 본 파일의 데이터로 덮어씌운다"."""
    merged = merge_target([("config/gbm/mx.json", {"topic": "BASE"}),
                           ("config/factories/gumi/mx.json", {"topic": "GUMI"})])
    assert merged["topic"] == "GUMI"


def test_dict는_재귀로_합친다():
    """층마다 **전체를 다시 적지 않는다.** 아래 층의 형제 키가 살아남아야 한다 —
    안 그러면 위 층에 안 적힌 이름을 리드가 "없다"고 읽는다."""
    merged = merge_target([("a", {"kafka": {"topic": "T", "group": "G"}}),
                           ("b", {"kafka": {"topic": "T2"}})])
    assert merged["kafka"] == {"topic": "T2", "group": "G"}


def test_리스트는_교체다():
    merged = merge_target([("a", {"hosts": ["h1", "h2"]}), ("b", {"hosts": ["h3"]})])
    assert merged["hosts"] == ["h3"]


def test_층_하나만_있어도_된다():
    """층은 **선택이다** — 없는 층은 목록에 안 들어온다."""
    assert merge_target([("a", {"k": 1})]) == {"k": 1}


def test_아래_층을_바꾸지_않는다():
    """입력을 제자리에서 고치면, 같은 층을 두 사이트가 나눠 쓸 때 **먼저 본 쪽의
    결과가 나중 쪽에 샌다.** 캐시를 넣는 순간 조용히 터지는 자리다."""
    base = {"kafka": {"topic": "T"}}
    merge_target([("a", base), ("b", {"kafka": {"topic": "T2"}})])
    assert base == {"kafka": {"topic": "T"}}


def test_층이_하나뿐이어도_입력과_공유하지_않는다():
    """**층이 하나면 결과가 입력을 그대로 가리키기 쉽다.**

    두 층짜리 테스트는 이걸 못 잡는다 — 재귀 쪽이 어차피 새 dict를 만들기 때문이다.
    그런데 층을 캐시해 두고(같은 `config/gbm/mx.json`을 gumi도 sevt도 본다) 결과를
    누가 손대면, 그 순간 **다음 사이트의 밑바닥이 오염된다.**
    """
    layer = {"kafka": {"topic": "T"}, "hosts": ["h1"]}
    merged = merge_target([("a", layer)])
    merged["kafka"]["topic"] = "CHANGED"
    merged["hosts"].append("h2")
    assert layer == {"kafka": {"topic": "T"}, "hosts": ["h1"]}


def test_null은_값이다_우리_로더와_다르다():
    """**이 파일에서 제일 중요한 테스트다.**

    우리 config는 `null`이 삭제 마커다(`src/config/merge.py`). 대상은 그냥
    덮어쓴다. 둘을 같은 함수로 합치면 대상의 `null` 값이 **"그 키는 없다"로
    둔갑한다** — 리드는 "설정이 안 돼 있다"는 틀린 사실을 증거로 삼는다.
    """
    from src.config.merge import deep_merge

    layers = [("a", {"topic": "T"}), ("b", {"topic": None})]
    ours = deep_merge({"topic": "T"}, {"topic": None}, source="b", provenance={})

    assert merge_target(layers) == {"topic": None}, "대상 규칙: null도 값이다"
    assert "topic" not in ours, "우리 규칙: null은 삭제 마커다"


def test_json이_아니면_이유를_돌려준다():
    """대상 파일이 우리 기대와 다른 것은 **운영 중 정상적으로 일어난다.**
    던지면 조사가 죽는다(규율 1)."""
    value, why = parse_layer("config/gbm/mx.json", "{not json")
    assert value is None and "JSON이 아니다" in why
    assert "config/gbm/mx.json" in why, "어느 파일인지 안 적히면 사람이 못 찾는다"


def test_최상위가_객체가_아니면_거부한다():
    """배열을 dict에 합치면 그 뒤의 모든 병합이 뜻을 잃는다."""
    value, why = parse_layer("x.json", '["a"]')
    assert value is None and "최상위가 객체가 아니다" in why


def test_정상_JSON은_그대로_읽는다():
    value, why = parse_layer("x.json", '{"topic": "T"}')
    assert value == {"topic": "T"} and why == ""
