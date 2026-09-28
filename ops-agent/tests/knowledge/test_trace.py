"""코드 추적기(11b 커밋 1) — 끝점에서 DAO까지 **함수 사슬과 읽는 자원**을 순수하게.

리드에게 "어느 함수 몇 줄을 읽을지"를 주는 것이 목적이다. 조합 논리를 해석하는 것은 리드이고,
여기서는 200개 파일을 함수 서너 개로 좁힌다. 파일 읽기와 grep은 주입한다 — 테스트는 메모리
사전, 운영은 배포 커밋의 git 리더다. 이름은 전부 지어낸 것이다(decisions ⑮).
"""
import pytest

from src.knowledge import trace
from src.knowledge.flow import Hit, Name, Route

REPO = "dt-api"

FILES = {
    "api/main.py": (
        'from fastapi import FastAPI\n'
        'from api.routers import line, bad\n'
        'app = FastAPI()\n'
        'app.include_router(line.router, prefix="/api/v1")\n'
        'app.include_router(bad.router, prefix="/api/v1")\n'
        '\n'
        '@app.get("/health")\n'
        'def health():\n'
        '    return {"ok": True}\n'),
    "api/routers/line.py": (
        'from fastapi import APIRouter\n'
        'from api.deps import LineServiceDep\n'
        'router = APIRouter(prefix="/line", tags=["line"])\n'
        '\n'
        '\n'
        '@router.get("/status", response_model=list[dict])\n'
        'async def get_line_status(service: LineServiceDep):\n'
        '    rows = await service.get_line_status()\n'
        '    return rows if rows is not None else []\n'
        '\n'
        '\n'
        '@router.get("/loose")\n'
        'async def loose(service):\n'
        '    return service.get_line_status()\n'
        '\n'
        '\n'
        '@router.get("/dyn")\n'
        'async def dyn(service: LineServiceDep):\n'
        '    return getattr(service, "get_line_" + "status")()\n'),
    "api/deps.py": (
        'from typing import Annotated\n'
        'from fastapi import Depends\n'
        'from api.services.line import LineService\n'
        '\n'
        'def get_line_service():\n'
        '    return LineService()\n'
        '\n'
        'LineServiceDep = Annotated[LineService, Depends(get_line_service)]\n'),
    "api/services/line.py": (
        'from api.repos.line import LineRepo\n'
        'from api.keys import Keys\n'
        '\n'
        'class LineService:\n'
        '    def __init__(self):\n'
        '        self.repo = LineRepo()\n'
        '\n'
        '    async def get_line_status(self):\n'
        '        cached = redis.get(Keys.LINE_STATUS)\n'
        '        return cached or self.repo.latest()\n'
        '\n'
        '\n'
        'class OtherService:\n'
        '    async def get_line_status(self):\n'
        '        return consumer.tail("mx.alarm.main")\n'),
    "api/repos/line.py": (
        'class LineRepo:\n'
        '    def latest(self):\n'
        '        return list(mongo["line_state"].find({}).limit(10))\n'),
    "api/keys.py": (
        'from enum import Enum\n'
        '\n'
        'class Keys(str, Enum):\n'
        '    LINE_STATUS = "line_status"\n'),
    "api/deep.py": "".join(f"def f{i}():\n    return f{i + 1}()\n\n\n" for i in range(8))
                   + 'def f8():\n    return mongo["line_state"].count_documents({})\n',
    "api/broken.py": 'def broken(:\n    pass\n',
    "api/routers/bad.py": (
        'from fastapi import APIRouter\n'
        'from api.broken import broken\n'
        'router = APIRouter(prefix="/bad")\n'
        '\n'
        '@router.get("/x")\n'
        'def bad_x():\n'
        '    return broken()\n'),
    "tests/test_line.py": 'def latest():\n    return mongo["line_state"].find()\n',
    # ── 사내 모양(11b 커밋 2b): Protocol 포트 + Depends(provider) + 부모 클래스 + 클래스 속성 ──
    "api/ports.py": (
        'from typing import Protocol\n'
        '\n'
        'class HeatServicePort(Protocol):\n'
        '    def get_heat_status(self, line: str) -> dict: ...\n'
        '\n'
        'class GaugePort(Protocol):\n'
        '    def read(self) -> int: ...\n'
        '\n'
        'class TallyRepository(Protocol):\n'
        '    def count(self) -> int: ...\n'
        '\n'
        'class AuditServicePort(Protocol):\n'
        '    def recent(self) -> list: ...\n'),
    "api/repos/tally.py": (
        'from api.repos.base import BaseRepo\n'
        '\n'
        'class TallyRepository(BaseRepo):\n'
        '    collection = "tally_state"\n'
        '\n'
        '    async def count(self):\n'
        '        return await self.find_one({})\n'),
    "api/services/tally.py": (
        'from api.ports import TallyRepository\n'
        '\n'
        'class TallyService:\n'
        '    def __init__(self, repo: TallyRepository):\n'
        '        self.repo = repo\n'
        '\n'
        '    async def total(self):\n'
        '        return await self.repo.count()\n'),
    "api/services/audit.py": (
        'class AuditService:\n'
        '    async def recent(self):\n'
        '        return await consumer.tail("mx.alarm.main")\n'),
    "api/state_deps.py": (
        'from typing import Annotated\n'
        'from fastapi import Depends\n'
        'from api.ports import AuditServicePort\n'
        'from api.services.tally import TallyService\n'
        '\n'
        'def get_tally_service(request):\n'
        '    return request.app.state.tally\n'
        '\n'
        'def get_audit_service(request) -> AuditServicePort:\n'
        '    return request.app.state.audit\n'
        '\n'
        'TallyServiceDep = Annotated[TallyService, Depends(get_tally_service)]\n'
        'AuditServiceDep = Annotated[AuditServicePort, Depends(get_audit_service)]\n'),
    "api/gauges.py": (
        'from api.ports import GaugePort\n'
        '\n'
        'class DialGauge(GaugePort):\n'
        '    def read(self):\n'
        '        return mongo["line_state"].count()\n'
        '\n'
        'class DigitalGauge(GaugePort):\n'
        '    def read(self):\n'
        '        return consumer.lag("mx.alarm.main")\n'),
    "api/routers/furnace.py": (
        'from fastapi import APIRouter\n'
        'from api.heat_deps import HeatServiceDep, get_heat_service\n'
        'from api import settings\n'
        'from api.tables import KEY_TABLE\n'
        'from api.ports import GaugePort\n'
        'from api.state_deps import TallyServiceDep, AuditServiceDep\n'
        'router = APIRouter(prefix="/furnace")\n'
        '\n'
        '@router.get("/heat")\n'
        'async def get_heat_status(line: str, service: HeatServiceDep):\n'
        '    return await service.get_heat_status(line)\n'
        '\n'
        '@router.get("/cfg")\n'
        'async def cfg_view():\n'
        '    opts = settings.get("view")\n'
        '    return opts.get("x")\n'
        '\n'
        '@router.get("/local")\n'
        'async def local_view(line: str):\n'
        '    svc = get_heat_service()\n'
        '    return await svc.get_heat_status(line)\n'
        '\n'
        '@router.get("/table")\n'
        'async def table_view():\n'
        '    return [redis.get(k) for _, k in KEY_TABLE]\n'
        '\n'
        '@router.get("/gauge")\n'
        'async def gauge_view(g: GaugePort):\n'
        '    return g.read()\n'
        '\n'
        '@router.get("/tally")\n'
        'async def tally_view(service: TallyServiceDep):\n'
        '    return await service.total()\n'
        '\n'
        '@router.get("/audit")\n'
        'async def audit_view(svc: AuditServiceDep):\n'
        '    return await svc.recent()\n'),
    "api/heat_deps.py": (
        'from typing import Annotated\n'
        'from fastapi import Depends\n'
        'from api.ports import HeatServicePort\n'
        'from api.services.heat import HeatService\n'
        'from api.repos.heat import HeatRepo\n'
        '\n'
        'def get_heat_service() -> HeatServicePort:\n'
        '    return HeatService(repo=HeatRepo())\n'
        '\n'
        'HeatServiceDep = Annotated[HeatServicePort, Depends(get_heat_service)]\n'),
    "api/services/heat.py": (
        'from api.repos.heat import HeatRepo\n'
        '\n'
        'class HeatService:\n'
        '    def __init__(self, repo: HeatRepo):\n'
        '        self.repo = repo\n'
        '\n'
        '    async def get_heat_status(self, line: str) -> dict:\n'
        '        return await self.repo.status(line)\n'),
    "api/repos/base.py": (
        'class BaseRepo:\n'
        '    collection = ""\n'
        '\n'
        '    def __init__(self):\n'
        '        self.db = mongo\n'
        '\n'
        '    async def find_one(self, query):\n'
        '        return await mongo[self.collection].find_one(query)\n'),
    "api/repos/heat.py": (
        'from api.repos.base import BaseRepo\n'
        '\n'
        'class HeatRepo(BaseRepo):\n'
        '    collection = "heat_status"\n'
        '\n'
        '    def __init__(self):\n'
        '        super().__init__()\n'
        '        self.limit = 10\n'
        '\n'
        '    async def status(self, line: str):\n'
        '        return await self.find_one({"line": line})\n'),
    "api/tables.py": (
        'from api.keys import Keys\n'
        '\n'
        'KEY_TABLE = [("line", Keys.LINE_STATUS)]\n'),
    "api/settings.py": 'settings = load()\n',
    "api/clients/web.py": 'class WebFetcher:\n    def get(self, url):\n        return self._request("GET", url)\n',
    "api/clients/cache.py": 'class CacheHandle:\n    def get(self, key):\n        return self._conn.get(key)\n',
    "api/options.py": 'class OptionBag:\n    def get(self, key):\n        return self._data[key]\n',
}

NAMES = [Name("collection", "line_state", "mongodb_collection.line_state"),
         Name("collection", "heat_status", "mongodb_collection.heat"),
         Name("collection", "tally_state", "mongodb_collection.tally"),
         Name("rediskey", "line:{id}", "redis_key.line_status"),
         Name("topic", "mx.alarm.main", "infra.kafka.consumer.topic.topic1")]

ROUTES = [Route(REPO, "GET", "/api/v1/line/status", "api/routers/line.py", 6, "EXTRACTED", ""),
          Route(REPO, "GET", "/api/v1/line/loose", "api/routers/line.py", 12, "EXTRACTED", ""),
          Route(REPO, "GET", "/api/v1/line/dyn", "api/routers/line.py", 17, "EXTRACTED", ""),
          Route(REPO, "GET", "/api/v1/bad/x", "api/routers/bad.py", 5, "EXTRACTED", ""),
          Route(REPO, "GET", "/health", "api/main.py", 7, "EXTRACTED", ""),
          Route(REPO, "GET", "/furnace/heat", "api/routers/furnace.py", 9, "EXTRACTED", ""),
          Route(REPO, "GET", "/furnace/cfg", "api/routers/furnace.py", 13, "EXTRACTED", ""),
          Route(REPO, "GET", "/furnace/local", "api/routers/furnace.py", 18, "EXTRACTED", ""),
          Route(REPO, "GET", "/furnace/table", "api/routers/furnace.py", 23, "EXTRACTED", ""),
          Route(REPO, "GET", "/furnace/gauge", "api/routers/furnace.py", 27, "EXTRACTED", ""),
          Route(REPO, "GET", "/furnace/tally", "api/routers/furnace.py", 31, "EXTRACTED", ""),
          Route(REPO, "GET", "/furnace/audit", "api/routers/furnace.py", 35, "EXTRACTED", "")]


class _Source:
    """메모리 레포. grep은 **리터럴 부분 문자열**이다 — 추적기는 리터럴 패턴만 내야 한다
    (운영의 `git grep -F`와 같은 계약)."""

    def __init__(self, files=FILES):
        self.files = dict(files)
        self.reads: list[str] = []
        self.greps: list[list[str]] = []

    async def read(self, path):
        self.reads.append(path)
        return self.files.get(path)

    async def grep(self, patterns):
        self.greps.append(list(patterns))
        out = []
        for path, text in sorted(self.files.items()):
            for i, line in enumerate(text.splitlines(), 1):
                if any(p in line for p in patterns):
                    out.append(Hit(REPO, "c", path, i, line))
        return out


async def _trace(target, **kw):
    src = _Source()
    aliases = await trace.alias_index(NAMES, src)
    return await trace.trace(target, repo=REPO, source=src, names=NAMES, routes=ROUTES,
                             aliases=aliases, **kw), src


def _reads(t):
    return {(r.kind, r.name, r.grade) for r in t.reads}


async def test_끝점에서_핸들러를_찾아_DAO까지_따라가고_읽는_자원을_등급과_함께_낸다():
    """주석 `LineServiceDep`으로 LineService를 좁힌다 — OtherService의 토픽은 안 나온다.
    `self.repo = LineRepo()`로 필드를 좁혀 DAO까지 간다. 리터럴은 확실, Enum 경유는 추정."""
    t, _ = await _trace("/api/v1/line/status")
    assert t.status == "ok", t
    assert [(s.file, s.line, s.qualname) for s in t.chain][:3] == [
        ("api/routers/line.py", 7, "get_line_status"),
        ("api/services/line.py", 8, "LineService.get_line_status"),
        ("api/repos/line.py", 2, "LineRepo.latest")]
    assert _reads(t) == {("collection", "line_state", "확실"), ("rediskey", "line:{id}", "추정")}
    assert t.gaps == ()
    line_state = next(r for r in t.reads if r.name == "line_state")
    assert (line_state.file, line_state.line) == ("api/repos/line.py", 3)


async def test_내장_함수_호출은_레포를_뒤지지_않는다():
    """`list(...)`·`len(...)`마다 `def list(`를 grep하면 레포당 수십 번이 헛돈다. 모듈 안의
    `__builtins__`는 사전이라 `dir()`로 이름을 걸러지지 않는다 — `builtins` 모듈로 본다."""
    _, src = await _trace("/api/v1/line/status")
    asked = {p for patterns in src.greps for p in patterns}
    assert "def list(" not in asked and "def len(" not in asked, sorted(asked)


async def test_주석이_없으면_후보_전부를_추정으로_남기고_그렇게_적는다():
    t, _ = await _trace("/api/v1/line/loose")
    assert t.status == "ok"
    assert _reads(t) == {("collection", "line_state", "추정"), ("rediskey", "line:{id}", "추정"),
                         ("topic", "mx.alarm.main", "추정")}
    assert any("후보 2개" in g.why and "get_line_status" in g.why for g in t.gaps), t.gaps


async def test_getattr은_못_따라간다고_남긴다():
    t, _ = await _trace("/api/v1/line/dyn")
    assert t.status == "ok" and t.reads == ()
    assert any("getattr" in g.why and g.file == "api/routers/line.py" for g in t.gaps), t.gaps


async def test_깊이_상한에서_멈추고_그렇게_적는다():
    t, _ = await _trace("f0")
    assert t.status == "ok" and t.reads == ()
    assert len(t.chain) == trace.MAX_DEPTH + 1
    assert any("깊이 상한" in g.why for g in t.gaps), t.gaps
    deep, _ = await _trace("f0", max_depth=10)
    assert _reads(deep) == {("collection", "line_state", "확실")}


async def test_문법_오류_파일은_gap이고_계속_간다():
    t, _ = await _trace("/api/v1/bad/x")
    assert t.status == "ok" and [s.qualname for s in t.chain] == ["bad_x"]
    assert any(g.file == "api/broken.py" and "문법" in g.why for g in t.gaps), t.gaps


async def test_없는_끝점은_값으로_실패한다():
    t, _ = await _trace("/nope")
    assert t.status == "not_found" and "/nope" in t.reason
    t, _ = await _trace("no_such_symbol")
    assert t.status == "not_found"


async def test_심볼_이름으로도_시작하고_테스트_파일의_정의는_뺀다():
    t, _ = await _trace("latest")
    assert t.status == "ok" and [s.qualname for s in t.chain] == ["LineRepo.latest"]
    assert _reads(t) == {("collection", "line_state", "확실")}


async def test_alias_index는_Enum_정의_줄에서_식별자를_이름에_잇는다():
    aliases = await trace.alias_index(NAMES, _Source())
    assert set(aliases) == {"LINE_STATUS"} and aliases["LINE_STATUS"].kind == "rediskey"


async def test_같은_입력이면_같은_결과다():
    a, _ = await _trace("/api/v1/line/status")
    b, _ = await _trace("/api/v1/line/status")
    assert a == b


async def test_레포가_다른_라우트는_안_본다():
    src = _Source()
    t = await trace.trace("/api/v1/line/status", repo="dt-core", source=src, names=NAMES,
                          routes=ROUTES, aliases={})
    assert t.status == "not_found"


async def test_Tracer는_레포_단위로_파싱과_grep을_캐시하고_gap은_새지_않는다():
    """끝점 156개(사내)를 한 레포에서 돌리면 파일 읽기와 `def 이름(` grep이 겹친다 — 캐시가 없으면
    git 호출 수천 번이다. 대신 문법 오류 같은 파싱 gap이 다른 끝점의 결과로 새면 안 된다."""
    src = _Source()
    tracer = trace.Tracer(REPO, src, names=NAMES, routes=ROUTES)
    await tracer.prepare()
    bad = await tracer.trace("/api/v1/bad/x")
    assert any("문법" in g.why for g in bad.gaps)
    ok = await tracer.trace("/api/v1/line/status")
    assert ok.status == "ok" and ok.gaps == () and _reads(ok) == {
        ("collection", "line_state", "확실"), ("rediskey", "line:{id}", "추정")}
    reads_before, greps_before = len(src.reads), len(src.greps)
    again = await tracer.trace("/api/v1/line/status")
    assert again == ok
    assert len(src.reads) == reads_before and len(src.greps) == greps_before, "두 번째는 캐시로 끝나야 한다"


# ── 사내 모양 (11b 커밋 2b) — 사내 첫 추적: 156개 전부 깊이 상한, "후보" gap 3,032개(그중 `get` 2,564).
# 주석이 Protocol 포트라 추상 메서드에서 끝났고, 받는 쪽을 모르는 `.get(`마다 레포의 `def get(` 후보를
# 다 따라가 옆으로 퍼졌다. 이름은 전부 지어낸 것이다.

async def test_Protocol_포트는_provider의_반환_클래스로_뚫고_부모_메서드와_클래스_속성까지_간다():
    """덤으로: 시그니처의 `line: str`은 템플릿 `line:{id}`의 리터럴 `line:`과 겹치지만 따옴표 밖이라 읽기가 아니다."""
    t, _ = await _trace("/furnace/heat")
    assert t.status == "ok", t
    assert [s.qualname for s in t.chain][:4] == [
        "get_heat_status", "HeatService.get_heat_status", "HeatRepo.status", "BaseRepo.find_one"]
    assert _reads(t) == {("collection", "heat_status", "확실")}
    assert t.gaps == (), t.gaps


async def test_받는_쪽을_모르는_호출은_후보가_여럿이면_안_따라가고_gap_하나만_남긴다():
    """`settings.get(...)`·`opts.get(...)` — 레포에 `def get(`이 셋(WebFetcher·CacheHandle·OptionBag).
    전부 따라가면 사슬이 20줄로 퍼지고 깊이 예산이 잡음에 먹힌다(사내). 인자에 주석이 없는 경우
    (`loose`)는 전처럼 후보 전부를 추정으로 따라간다."""
    t, _ = await _trace("/furnace/cfg")
    assert [s.qualname for s in t.chain] == ["cfg_view"]
    assert t.reads == ()
    gaps = [g.why for g in t.gaps]
    assert any("get" in g and "후보 3개" in g and "안 따라간다" in g for g in gaps), gaps
    assert len(gaps) == 1, gaps                     # 같은 이름은 한 번만


async def test_같은_함수의_지역_변수는_provider의_반환_클래스로_좁힌다():
    t, _ = await _trace("/furnace/local")
    assert [s.qualname for s in t.chain][:3] == ["local_view", "get_heat_service", "HeatService.get_heat_status"]
    assert _reads(t) == {("collection", "heat_status", "확실")}
    assert t.gaps == ()


async def test_함수가_참조하는_모듈_상수표의_이름도_읽기다():
    """사내 config 키는 `MAPPING = [(Enum.A, Keys.A), …]` 표에 있고 함수는 표를 돈다 — 함수 본문에는
    식별자가 없다. 표의 줄이 근거이고 등급은 추정이다."""
    t, _ = await _trace("/furnace/table")
    assert _reads(t) == {("rediskey", "line:{id}", "추정")}
    read = t.reads[0]
    assert (read.file, read.line) == ("api/tables.py", 3)


async def test_상속한_구현체가_여럿이면_전부_추정으로_따라가고_gap_하나를_남긴다():
    """provider가 없는 포트 — `class X(Port)`로 상속한 구현체를 grep으로 찾는다. 둘 이상이면 어느 것이
    실행되는지 코드로는 모르니 전부 따라가되 읽기는 추정이다."""
    t, _ = await _trace("/furnace/gauge")
    assert [s.qualname for s in t.chain] == ["gauge_view", "DialGauge.read", "DigitalGauge.read"]
    assert _reads(t) == {("collection", "line_state", "추정"), ("topic", "mx.alarm.main", "추정")}
    assert [g.why for g in t.gaps] == ["read: GaugePort 구현체 2개 — 전부 따라가되 읽기는 추정"]


# ── 사내 두 번째 추적(커밋 2b 뒤, 137/15) — 남은 gap의 모양 셋. 이름은 전부 지어낸 것이다.

async def test_포트와_같은_이름의_클래스가_다른_모듈에_있으면_그것이_구현체다():
    """포트 `TallyRepository(Protocol)`와 구현 `TallyRepository(BaseRepo)` — 상속하지 않고 이름만 같다(사내
    저장소 모양). provider는 app.state에서 꺼내 주므로 반환 클래스도 없다. 하나뿐이면 확실이다."""
    t, _ = await _trace("/furnace/tally")
    assert [s.qualname for s in t.chain][:4] == [
        "tally_view", "TallyService.total", "TallyRepository.count", "BaseRepo.find_one"]
    assert _reads(t) == {("collection", "tally_state", "확실")}
    assert t.gaps == (), t.gaps


async def test_상속도_같은_이름도_없으면_이름_규약으로_구현체를_고르고_추정이라고_적는다():
    """`AuditServicePort` → `AuditService`. 코드가 보증하는 연결이 아니라 등급은 추정이고 gap에 남긴다 —
    같은 이름 메서드 전부로 퍼지는 것보다는 좁고, 확실이라고 속이지는 않는다."""
    t, _ = await _trace("/furnace/audit")
    assert [s.qualname for s in t.chain][:2] == ["audit_view", "AuditService.recent"]
    assert _reads(t) == {("topic", "mx.alarm.main", "추정")}
    assert [g.why for g in t.gaps] == [
        "recent: AuditServicePort 구현체를 이름 규약으로 골랐다 — AuditService, 읽기는 추정"]


async def test_super_호출은_부모의_메서드로_가고_실행_시점_클래스는_자식_그대로다():
    """`HeatRepo.__init__`의 `super().__init__()` — 받는 쪽이 `super()` 호출이라 전에는 레포의 `def __init__(`
    전부가 후보였다(사내: 후보 61개, 끝점마다). 부모의 것 하나로 간다."""
    t, _ = await _trace("/furnace/local")
    names = [s.qualname for s in t.chain]
    assert "HeatRepo.__init__" in names and "BaseRepo.__init__" in names, names
    assert t.gaps == (), t.gaps
