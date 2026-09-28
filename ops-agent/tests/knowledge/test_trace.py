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
}

NAMES = [Name("collection", "line_state", "mongodb_collection.line_state"),
         Name("rediskey", "line:{id}", "redis_key.line_status"),
         Name("topic", "mx.alarm.main", "infra.kafka.consumer.topic.topic1")]

ROUTES = [Route(REPO, "GET", "/api/v1/line/status", "api/routers/line.py", 6, "EXTRACTED", ""),
          Route(REPO, "GET", "/api/v1/line/loose", "api/routers/line.py", 12, "EXTRACTED", ""),
          Route(REPO, "GET", "/api/v1/line/dyn", "api/routers/line.py", 17, "EXTRACTED", ""),
          Route(REPO, "GET", "/api/v1/bad/x", "api/routers/bad.py", 5, "EXTRACTED", ""),
          Route(REPO, "GET", "/health", "api/main.py", 7, "EXTRACTED", "")]


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
