"""끝점 사슬을 심볼 인덱스에서 만든다(11d 6d-3·6d-4) — 11b 추적기가 내던 `Trace` 모양 그대로.

decisions ⑱의 "엔진 하나". 사내 대조(같음 150 · 다름 6)로 인덱스가 추적기보다 못한 지점이 없다는 것이 두 번 섰고,
추적기는 지웠다. 이 테스트들이 추적기와 맞춰 둔 성질(뿌리·깊이·등급·gap)의 유일한 기록이다."""
from src.knowledge import index as ix
from src.knowledge import index_trace
from src.knowledge import query as qy
from src.knowledge.flow import Name, Route
from tools.local_case import API_FILES

REPO = "svc"
FILES = {
    "api/__init__.py": "",
    "api/r.py": ("from api.q import recent, extra\n\n\n"
                 "@router.post('/badge')\n"
                 "def badge(db):\n"
                 "    recent(db)\n"
                 "    return extra(db)\n\n\n"
                 "@router.put('/badge')\n"
                 "def update_badge(db):\n"
                 "    return db[\"audit_log\"].insert_one({})\n"),
    "api/q.py": ("def recent(db):\n    return db[\"alarm_events\"].find({})\n\n\n"
                 "def extra(db):\n    return db[\"line_state\"].find({})\n"),
    # `store`는 타입을 모른다 — `fetch_rows`는 이름만 같은 두 메서드 어느 쪽으로도 갈 수 있다(추정 `?→`).
    "api/c.py": ("@router.get('/cand')\n"
                 "def cand(db, store):\n"
                 "    return store.fetch_rows(db)\n"),
    "api/stores.py": ("class Lines:\n    def fetch_rows(self, db):\n        return db[\"line_state\"].find({})\n\n\n"
                      "class Alarms:\n    def fetch_rows(self, db):\n        return db[\"alarm_events\"].find({})\n"),
    "api/deep.py": "@router.get('/deep')\n" + "".join(f"def f{i}():\n    return f{i + 1}()\n\n\n" for i in range(7))
                   + "def f7(db):\n    return db[\"line_state\"].find({})\n",
    # 포트 메서드가 깊이 6에 있고 구현은 디스패치로 간다 — 디스패치가 깊이를 먹으면 구현의 읽기가 빠진다.
    "api/port.py": ("class P:\n    def run(self, db):\n        raise NotImplementedError\n\n\n"
                    "class Impl(P):\n    def run(self, db):\n        return db[\"audit_log\"].find({})\n\n\n"
                    "@router.get('/port')\n"
                    + "".join(f"def g{i}(p: P, db):\n    return g{i + 1}(p, db)\n\n\n" for i in range(5))
                    + "def g5(p: P, db):\n    return p.run(db)\n"),
}
NAMES = [Name("collection", "alarm_events", "mongodb_collection.alarm"),
         Name("collection", "line_state", "mongodb_collection.line"),
         Name("collection", "audit_log", "mongodb_collection.audit")]
ROUTES = [Route(REPO, "POST", "/badge", "api/r.py", 4, "EXTRACTED", ""),
          Route(REPO, "PUT", "/badge", "api/r.py", 10, "EXTRACTED", ""),
          Route(REPO, "GET", "/cand", "api/c.py", 1, "EXTRACTED", ""),
          Route(REPO, "GET", "/deep", "api/deep.py", 1, "EXTRACTED", ""),
          Route(REPO, "GET", "/port", "api/port.py", 11, "EXTRACTED", "")]


class _Src:
    def __init__(self, files):
        self.files_ = files

    async def files(self):
        return sorted(self.files_)

    async def read(self, path):
        return self.files_.get(path)

    async def grep(self, patterns):
        return []


async def _index(files=FILES, names=NAMES, repo=REPO):
    idx = await ix.build_index({repo: _Src(files)}, names=names, commits={repo: "c0ffee"})
    return idx, qy.Graph(idx)


def _steps(t):
    return [(s.file, s.line, s.qualname, s.parent) for s in t.chain]


def _reads(t):
    return {(r.kind, r.name, r.grade, r.via) for r in t.reads}


async def test_같은_path의_라우트_선언_전부가_뿌리이고_확실_호출을_따라_읽기를_모은다():
    idx, g = await _index()
    t = index_trace.trace(idx, g, repo=REPO, target="/badge", routes=ROUTES)
    assert t.status == "ok" and t.repo == REPO
    assert _steps(t) == [("api/r.py", 5, "badge", None), ("api/r.py", 11, "update_badge", None),
                         ("api/q.py", 1, "recent", 0), ("api/q.py", 5, "extra", 0)]
    assert _reads(t) == {("collection", "alarm_events", "확실", "literal"), ("collection", "line_state", "확실", "literal"),
                         ("collection", "audit_log", "확실", "literal")}
    assert {(r.name, r.step, r.file, r.line) for r in t.reads} == {
        ("audit_log", 1, "api/r.py", 12), ("alarm_events", 2, "api/q.py", 2), ("line_state", 3, "api/q.py", 6)}
    assert t.gaps == ()


async def test_이름만_같은_후보를_거친_읽기는_추정이고_gap에_후보_수가_남는다():
    idx, g = await _index()
    t = index_trace.trace(idx, g, repo=REPO, target="/cand", routes=ROUTES)
    assert _reads(t) == {("collection", "line_state", "추정", "literal"), ("collection", "alarm_events", "추정", "literal")}
    assert [f"{x.file}:L{x.line} {x.why}" for x in t.gaps] == [
        "api/c.py:L3 fetch_rows: 받는 쪽을 못 좁혀 후보 2개 — 전부 따라가되 읽기는 추정"]


async def test_깊이_상한에서_멈추고_그렇다고_적는다():
    """추적기와 같은 깊이(6) — 다르면 두 엔진의 답이 깊이 차이로 갈라진다."""
    idx, g = await _index()
    t = index_trace.trace(idx, g, repo=REPO, target="/deep", routes=ROUTES)
    assert [s.qualname for s in t.chain] == [f"f{i}" for i in range(7)] and t.reads == ()
    assert [x.why for x in t.gaps] == ["깊이 상한 6에서 멈춤: f6 → f7"]
    deeper = index_trace.trace(idx, g, repo=REPO, target="/deep", routes=ROUTES, max_depth=7)
    assert _reads(deeper) == {("collection", "line_state", "확실", "literal")} and deeper.gaps == ()


async def test_디스패치는_깊이를_안_먹는다():
    """추적기는 호출자에서 구현으로 바로 갔다 — 베이스·포트 메서드 걸음이 사슬에 보이되 깊이는 그대로여야 같은 깊이다."""
    idx, g = await _index()
    t = index_trace.trace(idx, g, repo=REPO, target="/port", routes=ROUTES)
    assert [s.qualname for s in t.chain] == [f"g{i}" for i in range(6)] + ["P.run", "Impl.run"]
    assert _reads(t) == {("collection", "audit_log", "확실", "literal")} and t.gaps == ()


async def test_라우트_선언이_없거나_그_줄_아래_함수가_인덱스에_없으면_not_found다():
    idx, g = await _index()
    assert index_trace.trace(idx, g, repo=REPO, target="/nope", routes=ROUTES).status == "not_found"
    gone = [Route(REPO, "GET", "/x", "api/gone.py", 3, "EXTRACTED", "")]
    missing = index_trace.trace(idx, g, repo=REPO, target="/x", routes=gone)
    assert missing.status == "not_found" and "api/gone.py:L3" in missing.reason


async def test_측정판_핸들러는_캐시_키를_지나_컬렉션까지_닿고_getattr_gap_하나를_남긴다():
    """측정판(`tools/local_case.py`)의 api — 캐시 키를 먼저 읽고 비면 컬렉션에서 세고 형식은 getattr로 고른다. 11b 추적기가
    내던 것과 같은 사슬·읽기·gap이다(6d-3에서 두 엔진이 같음을 확인하고 추적기를 지웠다)."""
    files = {k: v for k, v in API_FILES.items() if k.endswith(".py")}
    names = [Name("collection", "alarm_events", "mongodb_collection.alarm.collection"),
             Name("rediskey", "alarm:stats:{line}", "redis_key.alarm_stats.key")]
    routes = [Route("dt-api", "POST", "/summary/badge", "api/alarms.py", 7, "EXTRACTED", "")]
    idx, g = await _index(files, names, repo="dt-api")
    t = index_trace.trace(idx, g, repo="dt-api", target="/summary/badge", routes=routes)
    assert t.status == "ok"
    assert {s.qualname for s in t.chain} == {"summary_badge", "badge", "count_recent", "window_start"}
    assert _reads(t) == {("rediskey", "alarm:stats:{line}", "추정", "key"), ("collection", "alarm_events", "추정", "key")}
    assert [(x.line, x.why) for x in t.gaps] == [(15, "getattr로 고른 대상은 못 따라간다 — 리드가 code.read로 본다")]
