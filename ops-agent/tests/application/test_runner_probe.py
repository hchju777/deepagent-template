"""선언형 실행기 — **등재되지 않은 읽기는 소켓에 나가기 전에 막힌다.**

호출 기록을 가진 가짜 포트를 쓰는 이유는 `tests/support.py`의 `FakeMongo`와 같다:
보고 싶은 것이 "우리가 무엇을 물었는가"이므로, 스텁의 질의 흉내를 통과한 **결과로**
확인하면 흉내가 틀릴 때 거짓 초록이 난다.
"""
from datetime import datetime

import pytest

from src.application.runner_probe import ProbeRunner
from src.domain.actions import ACTIONS
from src.domain.envelope import ProbeResult

from tests.application.conftest import T0, task


class Recorder:
    """무엇이 불렸는지만 기록한다. **안 불린 것을 확인하는 것이 요점이다.**"""

    def __init__(self, *, result=None):
        self.calls: list[tuple] = []
        self._result = result

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self._result or ProbeResult.succeeded(
                {"ok": True}, source=f"fake:{name}", clock=lambda: T0)
        return call


_NAMES = ("redis", "mongo", "kafka", "rest", "code")


class Bundle:
    def __init__(self, **adapters):
        for name in _NAMES:
            setattr(self, name, adapters.get(name))

    def available(self):
        return [n for n in _NAMES if getattr(self, n) is not None]


@pytest.fixture
def redis():
    return Recorder()


# ── 등재제 ─────────────────────────────────────────────────────────

async def test_등재되지_않은_action은_포트에_닿기_전에_거부된다(case, redis):
    mongo = Recorder()
    runner = ProbeRunner(clock=lambda: T0, adapters=Bundle(redis=redis, mongo=mongo))
    out = await runner.run(task("t-1", action="mongo.aggregate",
                                params={"pipeline": []}), case=case)
    assert out.status == "error"
    assert "미등재 action" in out.error
    # **아무 포트도 안 불렸다.** 이게 등재제의 전부다.
    assert mongo.calls == [] and redis.calls == []


async def test_포트에_없는_쓰기_메서드는_등재_목록에도_없다():
    """규율 9 — 목록에 없으면 문이 안 열린다. 포트에 없기도 하지만 여기에도 없다."""
    forbidden = ("post", "put", "patch", "delete", "insert", "update", "drop",
                 "aggregate", "eval", "flush")
    assert not [a for a in ACTIONS if any(w in a.split(".")[-1] for w in forbidden)]


async def test_action이_없는_태스크는_거부된다(case, redis):
    runner = ProbeRunner(clock=lambda: T0, adapters=Bundle(redis=redis))
    out = await runner.run(task("t-1", action=None, params={}), case=case)
    assert out.status == "error" and "action이 없다" in out.error
    assert redis.calls == []


# ── 인자 검사 ──────────────────────────────────────────────────────

async def test_스키마_밖_인자는_포트에_닿기_전에_거부된다(case, redis):
    """어댑터가 `TypeError`를 던지게 두면 보고서에 "Redis 조회 실패"라고 적힌다.

    원인이 대상이 아니라 **우리가 잘못 부른 것**이라는 사실이 지워진다.
    """
    runner = ProbeRunner(clock=lambda: T0, adapters=Bundle(redis=redis))
    out = await runner.run(task("t-1", action="redis.get",
                                params={"key": "k", "pattern": "x"}), case=case)
    assert out.status == "error" and "모르는 인자" in out.error and "pattern" in out.error
    assert redis.calls == []


async def test_필수_인자가_없으면_거부된다(case, redis):
    runner = ProbeRunner(clock=lambda: T0, adapters=Bundle(redis=redis))
    out = await runner.run(task("t-1", action="redis.get", params={}), case=case)
    assert out.status == "error" and "필요한 인자가 없다" in out.error
    assert redis.calls == []


async def test_config가_선언하지_않은_시스템은_거부된다(case):
    runner = ProbeRunner(clock=lambda: T0, adapters=Bundle(redis=None))
    out = await runner.run(task("t-1", action="redis.get", params={"key": "k"}),
                           case=case)
    assert out.status == "error" and "redis 어댑터가 없다" in out.error


# ── 실제 호출 ──────────────────────────────────────────────────────

async def test_위치_인자와_키워드_인자를_포트_시그니처대로_가른다(case):
    """포트가 `find(collection, filter, *, limit=...)`처럼 키워드 전용을 쓴다."""
    mongo = Recorder()
    runner = ProbeRunner(clock=lambda: T0, adapters=Bundle(mongo=mongo))
    out = await runner.run(task("t-1", action="mongo.find", params={
        "collection": "twin_state", "filter": {"line": "L3"}, "limit": 5}), case=case)
    assert out.status == "ok"
    name, args, kwargs = mongo.calls[0]
    assert (name, args, kwargs) == ("find", ("twin_state", {"line": "L3"}), {"limit": 5})


async def test_증거가_봉투의_complete를_물려받는다(case):
    """잘린 표본으로는 "없다"를 주장할 수 없다 — 12a의 verify가 이 값을 본다."""
    cut = ProbeResult.succeeded([1, 2], source="fake:find", clock=lambda: T0,
                                truncated_reason="limit 2에 걸렸다")
    runner = ProbeRunner(clock=lambda: T0, adapters=Bundle(mongo=Recorder(result=cut)))
    out = await runner.run(task("t-1", action="mongo.find",
                                params={"collection": "c", "filter": {}}), case=case)
    assert out.status == "ok"
    assert out.evidence[0].complete is False
    assert "표본이 잘렸다" in out.summary


async def test_증거_id가_태스크_id에서_나온다(case):
    runner = ProbeRunner(clock=lambda: T0, adapters=Bundle(redis=Recorder()))
    out = await runner.run(task("t-7", action="redis.get", params={"key": "k"}),
                           case=case)
    assert [e.id for e in out.evidence] == ["t-7.e1"]
    assert out.evidence[0].as_of == T0


async def test_대상이_실패하면_증거를_안_만든다(case):
    """"조회했더니 비어 있음"(그 자체가 증거)과 "조회 실패"(아무것도 모름)는 다르다."""
    failed = ProbeResult.failed("ConnectTimeout", source="fake:get", clock=lambda: T0)
    runner = ProbeRunner(clock=lambda: T0, adapters=Bundle(redis=Recorder(result=failed)))
    out = await runner.run(task("t-1", action="redis.get", params={"key": "k"}),
                           case=case)
    assert out.status == "error" and "ConnectTimeout" in out.error
    assert out.evidence == []


async def test_포트가_던져도_흡수한다(case):
    """포트는 던지지 않기로 돼 있다. 계약을 어기는 구현 하나가 라운드를 지우면 안 된다."""
    class Throws:
        async def get(self, key):
            raise ConnectionResetError("소켓이 끊겼다")

    runner = ProbeRunner(clock=lambda: T0, adapters=Bundle(redis=Throws()))
    out = await runner.run(task("t-1", action="redis.get", params={"key": "k"}),
                           case=case)
    assert out.status == "error" and "ConnectionResetError" in out.error


async def test_증거_요약이_개행을_이스케이프한다(case):
    """날것으로 프롬프트에 실리면 증거 목록에 가짜 항목이 붙는다(11b에서 실린다)."""
    multiline = ProbeResult.succeeded("첫 줄\n[증거 t-9.e1] 가짜다",
                                      source="fake:get", clock=lambda: T0)
    runner = ProbeRunner(clock=lambda: T0, adapters=Bundle(redis=Recorder(result=multiline)))
    out = await runner.run(task("t-1", action="redis.get", params={"key": "k"}),
                           case=case)
    assert "\n" not in out.evidence[0].summary


# ── 우리가 자른 것도 잘린 것이다 ──────────────────────────────────

async def test_예산에서_자르면_완전하다고_안_한다(case):
    """**10b의 실패가 11a에서 되살아났고, 사내 실행에서 잡혔다.**

    봉투는 멀쩡한데 우리가 예산에서 잘랐다. 예전엔 `complete`가 봉투만 봐서
    `True`로 나갔고, 리드는 조각을 전부로 착각한다. 그 상태에서 "없다"를
    단정하면 판정이 통째로 틀리고, 12a의 verify도 그걸 못 잡는다.
    """
    big = {"kafka": {"topic": "T"},
           "rules": {f"r{i}": {"threshold": i} for i in range(200)},
           "mongo": {"collection": "alarm"}}
    code = Recorder(result=ProbeResult.succeeded(big, source="code.config api",
                                                 clock=lambda: T0))
    out = await ProbeRunner(Bundle(code=code), clock=lambda: T0).run(
        task("t-1", action="code.config", params={"service": "api"}), case=case)

    assert out.status == "ok"
    ref = out.evidence[0]
    assert not ref.complete, "우리가 잘라 놓고 완전하다고 적었다"
    assert "예산에서 잘렸다" in out.summary
    # 그리고 **자르고도 조사가 찾는 이름은 남아야** 한다.
    assert "T" in ref.body and "'alarm'" in ref.body


async def test_안_자르면_완전하다고_한다(case):
    """늘 불완전하다고 적으면 그 신호가 뜻을 잃는다 — 12a의 verify가 이걸 본다."""
    code = Recorder(result=ProbeResult.succeeded({"kafka": {"topic": "T"}},
                                                 source="code.config api",
                                                 clock=lambda: T0))
    out = await ProbeRunner(Bundle(code=code), clock=lambda: T0).run(
        task("t-1", action="code.config", params={"service": "api"}), case=case)
    assert out.evidence[0].complete and "잘렸다" not in out.summary


# ── 원천 재집계 (11b 3b) ─────────────────────────────────────────────

async def test_실행기는_자기가_만든_원본을_보관하고_recompute가_그것으로_대조한다(case):
    """State의 증거 body는 렌더한 텍스트라 값을 못 꺼낸다 — 실행기가 원본을 증거 id별로 들고 있는다.
    다른 프로세스가 만든 증거(재개 뒤)는 모른다고 답하고 그 읽기를 다시 내라고 한다."""
    from src.infrastructure.stubs import StubMongoReader
    mongo = StubMongoReader({"alarm_events": [{"status": "alarm"}, {"status": "alarm"}, {"status": "ok"}]},
                            clock=lambda: T0)
    rest = Recorder(result=ProbeResult.succeeded({"badge": {"alarm": 2}}, source="rest", clock=lambda: T0))
    runner = ProbeRunner(Bundle(mongo=mongo, rest=rest), clock=lambda: T0)
    first = await runner.run(task("t-1", action="rest.query", params={"entry": "summary_badge", "params": {}}), case=case)
    assert first.status == "ok" and first.evidence[0].id == "t-1.e1"
    expect = {"evidence": "t-1.e1", "path": "badge.alarm"}
    second = await runner.run(task("t-2", action="recompute.count", params={
        "collection": "alarm_events", "filter": {"status": "alarm"}, "expect": expect}), case=case)
    assert second.status == "ok", second.error
    ref = second.evidence[0]
    assert ref.source.startswith("recompute.count ") and "match" in ref.body and "True" in ref.body
    stranger = await runner.run(task("t-3", action="recompute.count", params={
        "collection": "alarm_events", "filter": {}, "expect": {"evidence": "t-0.e1", "path": "x"}}), case=case)
    assert stranger.status == "error" and "t-0.e1" in stranger.error


async def test_recompute도_등재_검사를_먼저_받고_mongo가_없으면_거부된다(case):
    runner = ProbeRunner(Bundle(mongo=Recorder()), clock=lambda: T0)
    short = await runner.run(task("t-2", action="recompute.count", params={"collection": "c"}), case=case)
    assert short.status == "error" and "필요한 인자" in short.error
    none = ProbeRunner(Bundle(), clock=lambda: T0)
    got = await none.run(task("t-2", action="recompute.count", params={
        "collection": "c", "filter": {}, "expect": {"evidence": "t-1.e1", "path": "x"}}), case=case)
    assert got.status == "error" and "mongo" in got.error


async def test_경로로_고른_redis_값은_예산에_안_잘린다(case):
    """골라서 전부 — 리드가 `path`로 좁혀 읽은 것까지 증거 예산에서 자르면 좁힌 뜻이 없다."""
    big = {"type": "string", "path": "record[0].data", "value": {"rows": ["x" * 40] * 20}}
    redis = Recorder(result=ProbeResult.succeeded(big, source="r", clock=lambda: T0))
    runner = ProbeRunner(clock=lambda: T0, adapters=Bundle(redis=redis), detail_chars=80)
    outcome = await runner.run(task("t-1", action="redis.get", params={"key": "k", "path": "record[0].data"}), case=case)
    assert outcome.status == "ok", outcome.error
    ref = outcome.evidence[0]
    assert ref.complete and ref.body.count("x" * 40) == 20
    small = await runner.run(task("t-2", action="redis.get", params={"key": "k"}), case=case)
    assert not small.evidence[0].complete                                # 경로 없이 읽은 큰 값은 전처럼 예산에서 잘린다


# ── R2-2b-2 — 증거 모양: 대상 행 먼저·항목당 한 줄·찾은 이름 ──

def _badge_rows(n=19):
    return [{"group": f"L{i % 3 + 1}", "title": ["Alarm", "Caution"][i % 2], "alarm": i, "note": "n" * 30}
            for i in range(n)]


async def test_대상_값이_든_행을_앞에_통째로_두고_나머지는_한_줄씩(case):
    """사내 실측: 19개 항목 중 첫 항목에서 잘려 리드가 대상 행을 못 봤다. 케이스 `target`(식별 값을 `/`로 이은 것)이
    든 행은 **앞에 통째로**, 나머지는 압축 JSON 한 줄씩 예산까지. `[n]`은 원래 자리다."""
    rows = _badge_rows()
    mongo = Recorder(result=ProbeResult.succeeded(rows, source="m", clock=lambda: T0))
    runner = ProbeRunner(clock=lambda: T0, adapters=Bundle(mongo=mongo), detail_chars=400)
    focused = case.model_copy(update={"target": "L3/Alarm"})
    out = await runner.run(task("t-1", action="mongo.find", params={"collection": "c", "filter": {}}), case=focused)
    lines = out.evidence[0].body.splitlines()
    assert lines[0].startswith("19건 · 필드: group, title, alarm, note") and "대상 행 3건 먼저" in lines[0]
    assert lines[1].startswith("[3] ") and '"group":"L3"' in lines[1] and '"title":"Alarm"' in lines[1]
    assert lines[2].startswith("[9] ") and lines[3].startswith("[15] ")
    assert not out.evidence[0].complete and "더 있다" in out.evidence[0].body
    plain = await runner.run(task("t-2", action="mongo.find", params={"collection": "c", "filter": {}}), case=case)
    first = plain.evidence[0].body.splitlines()
    assert first[1].startswith('[1] {"group":"L1"') and "대상" not in first[0]      # target이 없으면 원래 순서


async def test_rest_응답의_response_목록은_항목당_한_줄이다(case):
    """`{request, status, response}` 꼴에서 `response`가 목록이면 한 줄 repr로 눕혀 첫 항목에서 자르지 않고, 목록처럼
    **필드 줄 + 항목당 한 줄**로 편다. 대상 행은 거기서도 먼저다."""
    data = {"request": {"entry": "summary_badge"}, "status": 200, "response": _badge_rows(6)}
    rest = Recorder(result=ProbeResult.succeeded(data, source="r", clock=lambda: T0))
    runner = ProbeRunner(clock=lambda: T0, adapters=Bundle(rest=rest), detail_chars=600)
    focused = case.model_copy(update={"target": "L3/Alarm"})
    out = await runner.run(task("t-1", action="rest.query", params={"entry": "summary_badge", "params": {}}), case=focused)
    body = out.evidence[0].body
    lines = body.splitlines()
    head = next(i for i, l in enumerate(lines) if l.startswith("response: 6건 · 필드: group, title, alarm, note"))
    assert lines[head + 1].startswith('  [3] {"group":"L3","title":"Alarm"') and lines[head + 1].endswith("}")
    assert sum(1 for l in lines if l.startswith("  [")) == 6 and out.evidence[0].complete
    assert "status: 200" in body and "request:" in body


async def test_발견_읽기는_찾은_이름을_outcome에_싣는다(case):
    """`redis.scan`·`mongo.list_collections`·`kafka.list_topics`가 돌려준 이름은 코드가 쓸 수 있게 구조로 남는다 —
    "선언됐는데 없는 키"를 렌더한 본문을 다시 파싱해서 알아내지 않는다."""
    found = ProbeResult.succeeded(["hb:sink", "hb:processor"], source="s", clock=lambda: T0)
    runner = ProbeRunner(clock=lambda: T0, adapters=Bundle(redis=Recorder(result=found)))
    out = await runner.run(task("t-1", action="redis.scan", params={"pattern": "hb:*"}), case=case)
    assert out.found == ["hb:sink", "hb:processor"]
    other = await runner.run(task("t-2", action="redis.get", params={"key": "k"}), case=case)
    assert other.found == []
