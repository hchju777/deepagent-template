"""계층 병합 — 아래 층이 이기고, null은 지우고, 출처가 남는가."""
from src.config.merge import deep_merge


def _merge(*layers):
    """(merged, provenance)를 돌려준다. 층 이름은 L0, L1, ..."""
    merged, provenance = {}, {}
    for index, layer in enumerate(layers):
        merged = deep_merge(merged, layer, source=f"L{index}", provenance=provenance)
    return merged, provenance


def test_아래_층이_이긴다():
    merged, _ = _merge({"url": "gbm기본"}, {"url": "구미실제"})
    assert merged["url"] == "구미실제"


def test_dict은_재귀_병합돼_말하지_않은_키가_남는다():
    # 아래 층이 url만 말해도 위 층의 db가 사라지면 안 된다.
    merged, _ = _merge({"redis": {"url": "u1", "db": 0}}, {"redis": {"url": "u2"}})
    assert merged["redis"] == {"url": "u2", "db": 0}


def test_리스트는_이어붙이지_않고_교체한다():
    # "하나 추가"와 "이걸로 교체"를 구별할 문법이 없다 — 헷갈리면 다른 법인 브로커에 붙는다.
    merged, _ = _merge({"brokers": ["a:9092", "b:9092"]}, {"brokers": ["c:9092"]})
    assert merged["brokers"] == ["c:9092"]


# ── null 마커 ────────────────────────────────────────────────────────

def test_null은_키를_지운다():
    merged, _ = _merge({"kafka": {"consumer": {}}}, {"kafka": None})
    assert "kafka" not in merged


def test_중첩된_null도_지운다():
    merged, _ = _merge({"redis": {"url": "u", "password": "p"}},
                       {"redis": {"password": None}})
    assert merged["redis"] == {"url": "u"}


def test_없던_자리의_null은_아무_일도_안_한다():
    merged, _ = _merge({"a": 1}, {"b": None})
    assert merged == {"a": 1}


def test_빈_층_위에서도_중첩_null이_처리된다():
    """'앞 층이 비었으면 재귀를 건너뛴다'는 최적화가 여기서 틀린다."""
    merged, _ = _merge({}, {"redis": {"password": None, "url": "u"}})
    assert merged == {"redis": {"url": "u"}}


# ── 출처 ─────────────────────────────────────────────────────────────

def test_출처가_이긴_층을_가리킨다():
    _, provenance = _merge({"redis": {"url": "u1", "db": 0}}, {"redis": {"url": "u2"}})
    assert provenance["redis.url"] == "L1", "덮어쓴 층이 출처여야 한다"
    assert provenance["redis.db"] == "L0", "덮이지 않은 값은 원래 층이 출처다"


def test_지워진_키의_출처도_사라진다():
    _, provenance = _merge({"redis": {"password": "p"}}, {"redis": {"password": None}})
    assert "redis.password" not in provenance


def test_스칼라가_dict으로_바뀌면_옛_출처가_남지_않는다():
    _, provenance = _merge({"rest": "http://old"}, {"rest": {"base_url": "http://new"}})
    assert "rest" not in provenance
    assert provenance["rest.base_url"] == "L1"


def test_네_층이_섞여도_제일_구체적인_층이_이긴다():
    merged, provenance = _merge(
        {"redis": {"key_prefix": "twin:", "db": 0}},          # gbm/common
        {"redis": {"key_prefix": "mx:twin:"}},                # gbm/mx
        {"guards": {"max_rows": 500}},                        # fct/gumi/common
        {"redis": {"url": "redis://gumi:6379"}})              # fct/gumi/mx
    assert merged == {"redis": {"key_prefix": "mx:twin:", "db": 0, "url": "redis://gumi:6379"},
                      "guards": {"max_rows": 500}}
    assert provenance["redis.key_prefix"] == "L1"
    assert provenance["redis.url"] == "L3"
    assert provenance["redis.db"] == "L0"
