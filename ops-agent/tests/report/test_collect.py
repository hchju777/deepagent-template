"""팬아웃 — **한 법인의 실패가 나머지를 가리지 않는가.**

사내에서 법인 하나가 방화벽에 막히는 것은 예상 밖의 일이 아니다. 여기서 예외가
올라가면 그 한 법인 때문에 리포트가 아예 안 나가고, 나머지 법인의 숫자도 아무도
못 본다. 그리고 조용히 빼면 "전사 합계"가 말 없이 줄어 "알람이 줄었다"로 읽힌다.
"""
import asyncio
import json
from pathlib import Path

import pytest

from src.report.collect import collect
from src.report.rows import projection
from src.report.window import build_window, date_filter

from .conftest import TODAY, YESTERDAY, FakeMongo, doc, scenario


@pytest.fixture
def config_root(tmp_path) -> Path:
    (tmp_path / "app.json").write_text(json.dumps({"timezone": "Asia/Seoul"}),
                                       encoding="utf-8")
    (tmp_path / "registry.json").write_text(json.dumps({"sites": [
        {"gbm": "mx", "fct": "gumi"}, {"gbm": "mx", "fct": "sevt"}]}), encoding="utf-8")
    for fct in ("gumi", "sevt"):
        (tmp_path / "fct" / fct).mkdir(parents=True)
        (tmp_path / "fct" / fct / "mx.json").write_text(json.dumps(
            {"infra": {"mongodb": {"url": "mongodb://h:27017", "database": "d"}}}),
            encoding="utf-8")
    return tmp_path


def run(scen, *, config_root, window, clock, fakes: dict[str, FakeMongo]):
    """사이트별로 다른 가짜 Mongo를 꽂는다 — factory를 그 지점에서만 갈아끼운다."""
    import src.infrastructure.factory as factory_module

    class OneSite:
        def __init__(self, mongo): self.mongo = mongo
        async def close(self): pass

    calls = {"n": 0}
    original = factory_module.build_adapters

    def fake_build(site, *, clock, seeds=None):
        calls["n"] += 1
        return OneSite(fakes[f"{site.site.gbm}/{site.site.fct}"])

    factory_module.build_adapters = fake_build
    try:
        return asyncio.run(collect(scen, config_root=config_root, env={},
                                   window=window, clock=clock))
    finally:
        factory_module.build_adapters = original


def test_두_법인을_읽어_한_덩어리로_만든다(config_root, window, clock):
    fakes = {"mx/gumi": FakeMongo([doc(YESTERDAY, plant="gumi")] * 3),
             "mx/sevt": FakeMongo([doc(YESTERDAY, plant="sevt")] * 5)}
    facts = run(scenario(), config_root=config_root, window=window, clock=clock, fakes=fakes)
    assert facts.total(day=YESTERDAY) == 8
    assert facts.total(day=YESTERDAY, site="mx/sevt") == 5
    assert {s.site for s in facts.ok_sites} == {"mx/gumi", "mx/sevt"}


def test_나가는_질의가_정확히_그_필터다(config_root, window, clock):
    """숫자가 이상할 때 제일 먼저 봐야 하는 것이 "무엇을 물었는가"다."""
    fakes = {"mx/gumi": FakeMongo([]), "mx/sevt": FakeMongo([])}
    scen = scenario()
    run(scen, config_root=config_root, window=window, clock=clock, fakes=fakes)
    call = fakes["mx/gumi"].calls[0]
    assert call["collection"] == "alarm"
    assert call["filter"] == date_filter(scen.source, window)
    assert call["limit"] == scen.source.sample
    assert call["projection"] == projection(scen.source)


def test_한_법인이_막혀도_나머지가_집계된다(config_root, window, clock):
    fakes = {"mx/gumi": FakeMongo([doc(YESTERDAY)] * 4),
             "mx/sevt": FakeMongo(fail="ConnectionError: 연결할 수 없다")}
    facts = run(scenario(), config_root=config_root, window=window, clock=clock, fakes=fakes)
    assert facts.total(day=YESTERDAY) == 4
    blocked = facts.unavailable
    assert [s.site for s in blocked] == ["mx/sevt"]
    assert "연결할 수 없다" in blocked[0].error


def test_어댑터가_예외를_던져도_그_법인만_실패한다(config_root, window, clock):
    class Exploding(FakeMongo):
        async def find(self, *a, **k):
            raise RuntimeError("드라이버가 죽었다")

    fakes = {"mx/gumi": FakeMongo([doc(YESTERDAY)] * 2), "mx/sevt": Exploding()}
    facts = run(scenario(), config_root=config_root, window=window, clock=clock, fakes=fakes)
    assert facts.total(day=YESTERDAY) == 2
    assert "드라이버가 죽었다" in facts.unavailable[0].error


def test_표본이_잘리면_전체가_불완전하다고_말한다(config_root, window, clock):
    fakes = {"mx/gumi": FakeMongo([doc(YESTERDAY)], truncated="limit=1에 걸림 — 더 있다"),
             "mx/sevt": FakeMongo([])}
    facts = run(scenario(), config_root=config_root, window=window, clock=clock, fakes=fakes)
    assert facts.complete is False
    assert facts.ok_sites[0].truncated_reason == "limit=1에 걸림 — 더 있다"


def test_registry에서_꺼둔_법인은_이름으로_남는다(config_root, window, clock):
    (config_root / "registry.json").write_text(json.dumps({"sites": [
        {"gbm": "mx", "fct": "gumi"},
        {"gbm": "mx", "fct": "sevt", "enabled": False}]}), encoding="utf-8")
    fakes = {"mx/gumi": FakeMongo([doc(YESTERDAY)]), "mx/sevt": FakeMongo([])}
    facts = run(scenario(), config_root=config_root, window=window, clock=clock, fakes=fakes)
    skipped = [s for s in facts.unavailable if s.status == "skipped"]
    assert [s.site for s in skipped] == ["mx/sevt"]
    assert "enabled=false" in skipped[0].reason
    assert fakes["mx/sevt"].calls == [], "꺼진 법인에는 붙지 않는다"


def test_설정이_깨진_법인도_나머지를_막지_않는다(config_root, window, clock):
    (config_root / "fct" / "sevt" / "mx.json").write_text("{ 깨진 JSON",
                                                          encoding="utf-8")
    fakes = {"mx/gumi": FakeMongo([doc(YESTERDAY)] * 2), "mx/sevt": FakeMongo([])}
    facts = run(scenario(), config_root=config_root, window=window, clock=clock, fakes=fakes)
    assert facts.total(day=YESTERDAY) == 2
    assert "설정을 읽을 수 없다" in facts.unavailable[0].error


def test_mongo가_없는_법인은_건너뛴다(config_root, window, clock):
    (config_root / "fct" / "sevt" / "mx.json").write_text(json.dumps(
        {"infra": {"redis": {"url": "redis://h:6379"}}}), encoding="utf-8")
    fakes = {"mx/gumi": FakeMongo([doc(YESTERDAY)]), "mx/sevt": FakeMongo([])}
    facts = run(scenario(), config_root=config_root, window=window, clock=clock, fakes=fakes)
    assert [s.reason for s in facts.unavailable] == ["이 사이트에 mongodb 설정이 없다"]


def test_행_순서는_결정론이다(config_root, window, clock):
    """Mongo의 자연 순서(삽입 순서)에 기대는 코드가 생겨도 결과가 흔들리지 않게."""
    documents = [doc(YESTERDAY, hour=h, line=f"P{h}") for h in (14, 9, 20, 11)]
    first = run(scenario(), config_root=config_root, window=window, clock=clock,
                fakes={"mx/gumi": FakeMongo(documents), "mx/sevt": FakeMongo([])})
    second = run(scenario(), config_root=config_root, window=window, clock=clock,
                 fakes={"mx/gumi": FakeMongo(list(reversed(documents))),
                        "mx/sevt": FakeMongo([])})
    assert [r.occurred_at for r in first.rows] == [r.occurred_at for r in second.rows]
    assert [r.occurred_at for r in first.rows] == sorted(r.occurred_at for r in first.rows)


def test_동시_실행_상한을_넘지_않는다(config_root, window, clock):
    """법인 20개에 동시에 붙으면 우리가 읽기 전용이어도 대상 쪽 커넥션과 디스크를
    그만큼 동시에 쓴다 — "성능에 개입하지 않는다"가 깨지는 지점이다."""
    peak = {"now": 0, "max": 0}

    class Counting(FakeMongo):
        async def find(self, *a, **k):
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
            await asyncio.sleep(0)
            peak["now"] -= 1
            return await super().find(*a, **k)

    scen = scenario(scope={"gbms": ["mx"], "sites": ["mx/gumi", "mx/sevt"],
                           "max_parallel_sites": 1})
    run(scen, config_root=config_root, window=window, clock=clock,
        fakes={"mx/gumi": Counting([]), "mx/sevt": Counting([])})
    assert peak["max"] == 1
