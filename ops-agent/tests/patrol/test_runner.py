"""점검 실행 — **한 사이트의 장애가 나머지 27개를 안 끈다.**

사이트가 28개다. 구미 Mongo가 안 붙는다고 SEVT 순찰이 멈추면 장애 하나가 감시
전체를 끄고, 그 상태는 조용하다 — 케이스가 안 열리는 것이 "이상이 없어서"와
구별되지 않는다.
"""
from datetime import datetime

from src.config.schema_patrol import CheckConfig
from src.domain.envelope import ProbeResult
from src.patrol.runner import run_check, run_site, run_sites

T0 = datetime(2026, 9, 16, 9, 0, 0)
CLOCK = lambda: T0                                                  # noqa: E731

CHECK_BODY = {
    "concern": "operation",
    "probes": {
        "badge":  {"action": "rest.query",
                   "params": {"entry": "summary_badge", "params": {}}},
        "status": {"action": "rest.query",
                   "params": {"entry": "prod_status", "params": {}}}},
    "rule": "items_all_zero",
    "params": {
        "items": {"probe": "badge", "path": "response"},
        "identity": ["group", "title"],
        "counts": ["alarm", "caution", "normal"],
        "only_when": {"probe": "status", "path": "response.status",
                      "equals": "In Production"}}}


class Rest:
    def __init__(self, badges):
        self._badges = badges

    async def query(self, entry, params):
        body = (self._badges if entry == "summary_badge"
                else {"status": "In Production"})
        return ProbeResult.succeeded({"request": {}, "status": 200, "response": body},
                                     source=f"rest:{entry}", clock=CLOCK)


class Adapters:
    def __init__(self, badges):
        self.redis = self.mongo = self.kafka = None
        self.rest = Rest(badges)
        self.closed = False

    async def close(self):
        self.closed = True


class Site:
    def __init__(self, name, checks):
        self.site = name
        from src.config.schema_patrol import PatrolConfig
        self.patrol = PatrolConfig.model_validate({"checks": checks})


def zero(title="Defect"):
    return {"group": "Line", "title": title, "alarm": 0, "caution": 0, "normal": 0}


class Entry:
    def __init__(self, gbm, fct):
        self.gbm, self.fct = gbm, fct

    def __str__(self):
        return f"{self.gbm}/{self.fct}"


async def test_읽고_판정까지_한다():
    check = CheckConfig.model_validate(CHECK_BODY)
    out = await run_check("badge_all_zero", check, adapters=Adapters([zero()]),
                          site="mx/gumi", clock=CLOCK)
    assert out.status == "finding" and out.findings[0].target == "Line/Defect"


async def test_비활성_점검은_안_돈다():
    site = Site("mx/gumi", {"on": CHECK_BODY, "off": {**CHECK_BODY, "enabled": False}})
    outs = await run_site(site, adapters=Adapters([zero()]), clock=CLOCK)
    assert [o.check for o in outs] == ["on"]


async def test_점검_하나만_고를_수_있다():
    site = Site("mx/gumi", {"a": CHECK_BODY, "b": CHECK_BODY})
    outs = await run_site(site, adapters=Adapters([zero()]), clock=CLOCK, only="b")
    assert [o.check for o in outs] == ["b"]


# ── 격리 ───────────────────────────────────────────────────────────

async def test_사이트를_못_열어도_나머지가_돈다():
    """`build_adapters`가 던지는 경우 — config는 통과했는데 호스트가 안 풀리는 등.

    이 방어가 없으면 앞의 무raise 두 겹이 아무리 견고해도 **한 줄에서 전부 죽는다.**
    """
    entries = [Entry("mx", "gumi"), Entry("mx", "sevt"), Entry("nw", "sev")]

    def build(cfg):
        if str(cfg.site) == "mx/sevt":
            raise OSError("호스트 이름을 못 찾는다")
        return Adapters([zero()])

    outs = await run_sites(entries, load=lambda e: Site(str(e), {"c": CHECK_BODY}),
                           build=build, clock=CLOCK)
    by_site = {o.site: o for o in outs}
    assert by_site["mx/sevt"].status == "unreachable"
    assert "호스트 이름을 못 찾는다" in by_site["mx/sevt"].reason
    # **나머지 둘은 정상적으로 판정됐다** — 이게 전부다.
    assert by_site["mx/gumi"].status == "finding"
    assert by_site["nw/sev"].status == "finding"


async def test_config를_못_읽어도_나머지가_돈다():
    entries = [Entry("mx", "gumi"), Entry("mx", "sevt")]

    def load(entry):
        if entry.fct == "gumi":
            raise ValueError("config가 깨졌다")
        return Site(str(entry), {"c": CHECK_BODY})

    outs = await run_sites(entries, load=load, build=lambda cfg: Adapters([zero()]),
                           clock=CLOCK)
    assert {o.site: o.status for o in outs} == {"mx/gumi": "unreachable",
                                                "mx/sevt": "finding"}


async def test_못_본_사이트를_조용히_건너뛰지_않는다():
    """28개 중 하나가 빠진 것을 아무도 모르면 감시가 자기 실패를 숨긴다."""
    outs = await run_sites([Entry("mx", "gumi")],
                           load=lambda e: (_ for _ in ()).throw(OSError("죽었다")),
                           build=lambda cfg: Adapters([]), clock=CLOCK)
    assert len(outs) == 1 and outs[0].site == "mx/gumi"


async def test_판정_중_예외도_흡수한다():
    class Broken(Adapters):
        async def close(self):
            raise RuntimeError("닫는 것도 실패한다")

    outs = await run_sites([Entry("mx", "gumi"), Entry("mx", "sevt")],
                           load=lambda e: Site(str(e), {"c": CHECK_BODY}),
                           build=lambda cfg: Broken([zero()]), clock=CLOCK)
    # close가 던져도 두 사이트 다 판정됐다.
    assert [o.status for o in outs] == ["finding", "finding"]


async def test_어댑터를_닫는다():
    made = []

    def build(cfg):
        adapters = Adapters([zero()])
        made.append(adapters)
        return adapters

    await run_sites([Entry("mx", "gumi")], load=lambda e: Site(str(e), {"c": CHECK_BODY}),
                    build=build, clock=CLOCK)
    assert made[0].closed


def test_CLI가_실제로_돈다(tmp_path, capsys, monkeypatch):
    """**`patrol check` 명령 자체를** 부른다.

    4단계에서 부품만 테스트하다 `__main__`의 호출부가 깨진 채 776개가 통과한 적이
    있다. 배선은 배선을 불러야 보인다.
    """
    import json
    from pathlib import Path

    from src.__main__ import main

    root = Path(__file__).resolve().parent.parent.parent
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps({"rest": {
        "summary_badge": [zero("Defect"), {**zero("OK"), "normal": 9}],
        "prod_status": {"status": "In Production"}}}), encoding="utf-8")
    from tests.support import set_real_config_env

    set_real_config_env(monkeypatch)
    monkeypatch.setattr("sys.argv", [
        "src", "--config-root", str(root / "config"), "--env-file", str(tmp_path / "none"),
        "patrol", "check", "--all-sites", "--stub-seeds", str(seeds)])

    assert main() == 1                      # finding이 있으면 0이 아니다
    out = capsys.readouterr().out
    assert "Line/Defect" in out and "전부 0" in out
    assert "mx/gumi" in out and "mx/sevt" in out      # --all-sites가 둘 다 돌았다
