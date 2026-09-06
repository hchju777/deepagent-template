"""`api` 조립 — 어댑터 없이 (스펙 §3.1: api는 대상 시스템에 붙지 않는다)."""
from datetime import datetime, timezone

from src.api.assembly import assemble_api
from tests.test_boot import ENV, _tree

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def test_조립에_어댑터가_없다(tmp_path):
    _tree(tmp_path)
    rt = assemble_api(tmp_path / "config", tmp_path, dict(ENV), clock=lambda: T,
                      llm_factory=lambda name: object())
    site = rt.sites[0]
    assert (site.gbm, site.fct) == ("mx", "gumi")
    assert not hasattr(site, "adapters") and not hasattr(site, "deps")
    assert site.topology is not None            # 접수의 locator 후보용
    assert site.lead_llm is not None            # 접수 LLM — 조사 LLM(subagent)은 없다


def test_비활성_사이트는_조립하지_않는다(tmp_path):
    _tree(tmp_path)                             # mx/off는 disabled
    rt = assemble_api(tmp_path / "config", tmp_path, dict(ENV), clock=lambda: T,
                      llm_factory=lambda name: object())
    assert [(s.gbm, s.fct) for s in rt.sites] == [("mx", "gumi")]


def test_저장소와_시계를_들고_있다(tmp_path):
    _tree(tmp_path)
    rt = assemble_api(tmp_path / "config", tmp_path, dict(ENV), clock=lambda: T,
                      llm_factory=lambda name: object())
    assert rt.repo is not None and rt.store is not None and rt.events is not None
    assert rt.clock() == T
