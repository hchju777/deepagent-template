"""심볼 인덱스 질의(11d 6c-1) — "누가 부르나", "A에서 B로 어떻게 가나", "이 자원을 누가 쓰고 읽나".

11b의 추적기는 끝점에서 앞으로만 걸어서 이 역질문들에 답이 없었다 — 11d를 시작한 이유다."""
from src.knowledge import index as ix
from src.knowledge import query as qy
from src.knowledge.flow import Name

REPO = "svc"
FILES = {
    "app/__init__.py": "",
    "app/base.py": "class Store:\n    def save(self, x):\n        return x\n",
    "app/impl.py": ("from app.base import Store\n\n\n"
                    "class MongoStore(Store):\n"
                    "    def save(self, x):\n"
                    "        return self.write(x)\n\n"
                    "    def write(self, x):\n"
                    "        return db[\"events\"].insert_one(x)\n"),
    "app/svc.py": ("from app.base import Store\nfrom app.util import norm\n\n\n"
                   "def handle(store: Store, m):\n"
                   "    return store.save(norm(m))\n\n\n"
                   "def batch(store: Store, ms):\n"
                   "    for m in ms:\n"
                   "        handle(store, m)\n"),
    "util.py": "def norm(m):\n    return m\n",                 # 최상위 `util.norm` — `app.util.norm`의 끝부분과 같다
    "app/util.py": ("def norm(m):\n    return m\n\n\ndef unused():\n    return 1\n\n\n"
                    "def ping():\n    return pong()\n\n\ndef pong():\n    return ping()\n"),
    "app/api.py": ("from app.svc import handle\n\n\n"
                   "@router.post('/ingest')\n"
                   "def ingest(store, m):\n"
                   "    return handle(store, m)\n\n\n"
                   "def count(db):\n"
                   "    return db[\"events\"].count_documents({})\n"),
    # 부르는 쪽이 없는 베이스 메서드 — 프레임워크가 부르는 플러그인 모양.
    "app/plugin.py": ("class Plugin:\n    def run(self):\n        return 0\n\n\n"
                      "class Fancy(Plugin):\n    def run(self):\n        return helper2()\n\n\n"
                      "def helper2():\n    return 1\n"),
}
NAMES = [Name("collection", "events", "mongodb_collection.events")]


class _Src:
    def __init__(self, files):
        self.files_ = dict(files)

    async def files(self):
        return sorted(self.files_)

    async def read(self, path):
        return self.files_.get(path)


async def _graph():
    idx = await ix.build_index({REPO: _Src(FILES)}, names=NAMES, commits={REPO: "c0ffee"})
    return idx, qy.Graph(idx)


def _q(idx, sid):
    return idx.symbols[sid].qualname


async def test_이름은_qualname_끝부분_파일_레포로_찾고_정확한_qualname이_끝부분보다_앞선다():
    idx, _ = await _graph()
    assert sorted(_q(idx, s) for s in qy.find(idx, "norm")) == ["app.util.norm", "util.norm"]
    assert [_q(idx, s) for s in qy.find(idx, "util.norm")] == ["util.norm"]          # 정확한 qualname이 이긴다
    assert [_q(idx, s) for s in qy.find(idx, "app/util.py:norm")] == ["app.util.norm"]
    assert sorted(_q(idx, s) for s in qy.find(idx, "save")) == ["app.base.Store.save", "app.impl.MongoStore.save"]
    assert [_q(idx, s) for s in qy.find(idx, "app/impl.py:MongoStore.save")] == ["app.impl.MongoStore.save"]
    assert [_q(idx, s) for s in qy.find(idx, f"{REPO}:app.base.Store.save")] == ["app.base.Store.save"]
    assert [_q(idx, s) for s in qy.find(idx, "app.svc.handle")] == ["app.svc.handle"]
    assert qy.find(idx, "nope") == [] and qy.find(idx, "other-repo:norm") == []


async def test_부르는_쪽은_디스패치를_거쳐_진입점까지_가고_베이스_메서드는_진입점이_아니다():
    """`MongoStore.write` ← `MongoStore.save`(self) ⇐ `Store.save`(디스패치) ← `handle` ← `batch`·`ingest`. `Store.save`를
    부르는 쪽이 없더라도 그건 실행의 시작이 아니다 — 디스패치로만 닿은 막다른 곳은 진입점에서 뺀다."""
    idx, g = await _graph()
    got = qy.callers(g, [idx.lookup(REPO, "app.impl.MongoStore.write")])
    assert [(_q(idx, l.src), l.mark) for l in got.direct] == [("app.impl.MongoStore.save", "→")]
    roots = [_q(idx, p[0].src) for p in got.entries]
    assert sorted(roots) == ["app.api.ingest", "app.svc.batch"]
    ingest = next(p for p in got.entries if _q(idx, p[0].src) == "app.api.ingest")
    assert [(_q(idx, l.src), l.mark) for l in ingest] == [
        ("app.api.ingest", "→"), ("app.svc.handle", "→"), ("app.base.Store.save", "=>"), ("app.impl.MongoStore.save", "→")]
    assert qy.is_route(idx, idx.lookup(REPO, "app.api.ingest")) and not qy.is_route(idx, idx.lookup(REPO, "app.svc.batch"))
    assert not got.cut


async def test_디스패치로만_닿은_베이스_메서드는_진입점이_아니라_막다른_곳으로_따로_말한다():
    """`Fancy.run`을 부르는 길은 `Plugin.run`(디스패치)뿐이고 그걸 부르는 코드가 없다 — 프레임워크가 부르는 자리일 수
    있다. 진입점이라고 하면 거짓이고, 아무 말도 안 하면 사람이 "아무도 안 부른다"로 읽는다."""
    idx, g = await _graph()
    got = qy.callers(g, [idx.lookup(REPO, "app.plugin.helper2")])
    assert got.entries == []
    assert [_q(idx, p[0].src) for p in got.dead_ends] == ["app.plugin.Plugin.run"]
    assert [l.mark for l in got.dead_ends[0]] == ["=>", "→"]


async def test_부르는_쪽이_없거나_순환뿐이면_진입점이_없다고_말한다():
    idx, g = await _graph()
    none = qy.callers(g, [idx.lookup(REPO, "app.util.unused")])
    assert none.direct == [] and none.entries == []
    loop = qy.callers(g, [idx.lookup(REPO, "app.util.ping")])
    assert [_q(idx, l.src) for l in loop.direct] == ["app.util.pong"] and loop.entries == []


async def test_경로는_짧은_것부터_디스패치를_지나며_반대_방향은_없다():
    idx, g = await _graph()
    a, b = idx.lookup(REPO, "app.api.ingest"), idx.lookup(REPO, "app.impl.MongoStore.write")
    found, cut = qy.paths(g, [a], [b])
    assert [[(_q(idx, l.dst), l.mark) for l in p] for p in found] == [[
        ("app.svc.handle", "→"), ("app.base.Store.save", "→"), ("app.impl.MongoStore.save", "=>"),
        ("app.impl.MongoStore.write", "→")]]
    assert not cut and qy.paths(g, [b], [a])[0] == []


async def test_자원을_쓰고_읽는_함수를_이름으로_찾고_정확한_이름이_없으면_부분_일치다():
    idx, _ = await _graph()
    got = qy.uses(idx, "events")
    assert [(u.kind, u.direction, _q(idx, u.sid)) for u in got] == [
        ("collection", "writes", "app.impl.MongoStore.write"), ("collection", "reads", "app.api.count")]
    assert [u.name for u in qy.uses(idx, "even")] == ["events", "events"]
    assert qy.uses(idx, "nothing") == []


async def test_한_줄_표시는_짧은_이름에_표식을_붙이고_레포는_앞에_한_번이다():
    idx, g = await _graph()
    got = qy.callers(g, [idx.lookup(REPO, "app.impl.MongoStore.write")])
    ingest = next(p for p in got.entries if _q(idx, p[0].src) == "app.api.ingest")
    assert qy.render_path(idx, ingest) == (
        f"[{REPO}] api.ingest → svc.handle → base.Store.save => impl.MongoStore.save → impl.MongoStore.write")
    assert qy.display(idx, idx.lookup(REPO, "app.util.norm")) == "app.util.norm (app/util.py:L1)"


async def test_포트_타입으로_부르는_곳도_구현의_부르는_쪽으로_모인다():
    """주입 모양 셋(`= Depends`, `Annotated[…, Depends]`, 생성자에 포트 타입) 모두 구현 쪽 `callers`에 나와야 한다."""
    from tests.knowledge.test_index import PROTO
    idx = await ix.build_index({REPO: _Src(PROTO)}, names=[], commits={REPO: "c0ffee"})
    g = qy.Graph(idx)
    got = qy.callers(g, [idx.lookup(REPO, "src.services.BadgeService.get_badge")])
    assert sorted(_q(idx, p[0].src) for p in got.entries) == [
        "src.routes.Handler.run", "src.routes.by_annotated", "src.routes.by_default"]
    handler = next(p for p in got.entries if _q(idx, p[0].src) == "src.routes.Handler.run")
    assert [l.mark for l in handler] == ["→", "=>"]
    fetch = idx.lookup(REPO, "src.services.Cache.fetch")
    assert [(_q(idx, l.src), l.mark) for l in g.inc[fetch]] == [("src.ports.CachePort.fetch", "=>")]   # 한 번만


async def test_같은_디스패치가_두_길로_들어와도_그래프에는_한_번이다():
    """상속(`overrides`)과 이름 규칙(`implements`)이 같은 짝을 가리키면 `callers`에 같은 줄이 두 번 나온다 — 인덱서가
    이제 그렇게 만들지 않지만, 그래프가 스스로 지킨다."""
    idx, _ = await _graph()
    impl, base = idx.lookup(REPO, "app.impl.MongoStore.save"), idx.lookup(REPO, "app.base.Store.save")
    idx.add_edge(impl, base, "implements", "exact", line=5, via="name_rule")
    g = qy.Graph(idx)
    assert [(_q(idx, l.src), l.mark) for l in g.inc[impl]] == [("app.base.Store.save", "=>")]
