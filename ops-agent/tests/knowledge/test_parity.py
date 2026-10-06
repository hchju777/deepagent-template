"""두 엔진의 대조(11d 6c-2 → 6d-3) — 오버레이의 사슬은 **인덱스**가 만들고, 11b 추적기는 대조용으로만 한 번 더 돈다.

끝점마다 인덱스 사슬의 읽기와 추적기 읽기를 (종류, 이름)으로 대조해 다르면 자원마다 원인을 적는다 — "인덱스만"은
핸들러에서 그 자원까지 인덱스가 간 경로(추정 `?→`를 거쳤는지), "추적기만"은 추적기 사슬에서 인덱스가 처음 못 닿은
걸음. 사내 150/6이 재현되면 추적기를 지운다(6d-4)."""
from src.knowledge import flow
from src.knowledge import index as ix
from src.knowledge import index_trace
from src.knowledge import parity
from src.knowledge import query as qy
from src.knowledge import trace as tr
from src.knowledge.flow import Name, Route

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
                 "def extra(db):\n    db[\"ghost\"].find({})\n    return db[\"line_state\"].find({})\n\n\n"
                 "def lost(db):\n    return db[\"audit_log\"].find({})\n"),
    # `store`는 타입을 모른다 — `fetch_rows`는 이름만 같은 두 메서드 어느 쪽으로도 갈 수 있다(추정 `?→`).
    "api/c.py": ("from api.q import extra\n\n\n"
                 "@router.get('/cand')\n"
                 "def cand(db, store):\n"
                 "    return store.fetch_rows(db)\n\n\n"
                 "def via_extra(db):\n"
                 "    return extra(db)\n\n\n"
                 "@router.get('/mixed')\n"
                 "def mixed(db, store):\n"
                 "    store.fetch_rows(db)\n"
                 "    return via_extra(db)\n"),
    "api/stores.py": ("class Lines:\n    def fetch_rows(self, db):\n        return db[\"line_state\"].find({})\n\n\n"
                      "class Alarms:\n    def fetch_rows(self, db):\n        return db[\"alarm_events\"].find({})\n"),
    "api/deep.py": "@router.get('/deep')\n" + "".join(f"def f{i}():\n    return f{i + 1}()\n\n\n" for i in range(7))
                   + "def f7(db):\n    return db[\"line_state\"].find({})\n",
}
# `ghost`는 이름 목록에는 있지만 오버레이에 노드가 없다 — `add_trace`도 안 싣는 자원이라 대조에서도 빠져야 한다.
NAMES = [Name("collection", "alarm_events", "mongodb_collection.alarm"),
         Name("collection", "line_state", "mongodb_collection.line"),
         Name("collection", "ghost", "mongodb_collection.ghost"),
         Name("collection", "audit_log", "mongodb_collection.audit")]
ROUTES = {"/badge": [Route(REPO, "POST", "/badge", "api/r.py", 4, "EXTRACTED", "")],
          "/both": [Route(REPO, "POST", "/both", "api/r.py", 4, "EXTRACTED", ""),
                    Route(REPO, "PUT", "/both", "api/r.py", 10, "EXTRACTED", "")],
          "/put": [Route(REPO, "PUT", "/put", "api/r.py", 10, "EXTRACTED", "")],
          "/cand": [Route(REPO, "GET", "/cand", "api/c.py", 4, "EXTRACTED", "")],
          "/mixed": [Route(REPO, "GET", "/mixed", "api/c.py", 13, "EXTRACTED", "")],
          "/deep": [Route(REPO, "GET", "/deep", "api/deep.py", 1, "EXTRACTED", "")],
          "/gone": [Route(REPO, "GET", "/gone", "api/gone.py", 3, "EXTRACTED", "")]}
EP = flow.endpoint_id("/badge")


class _Src:
    async def files(self):
        return sorted(FILES)

    async def read(self, path):
        return FILES.get(path)


async def _index():
    idx = await ix.build_index({REPO: _Src()}, names=NAMES, commits={REPO: "c0ffee"})
    return idx, qy.Graph(idx)


def _graph():
    return {"nodes": [{"id": EP, "label": "/badge", "type": "endpoint"},
                      *({"id": f"collection_{n}", "label": n, "type": "collection"}
                        for n in ("alarm_events", "line_state", "audit_log"))],
            "links": []}


def _tracer(reads, chain=(("api/r.py", 5, "badge", None), ("api/q.py", 1, "recent", 0))):
    """추적기 쪽 답을 손으로 — chain은 (파일, 줄, 이름, 부모 걸음), reads는 이름 또는 (이름, 파일, 줄, 걸음)."""
    reads = [(r, "api/q.py", 2, 1) if isinstance(r, str) else r for r in reads]
    return tr.Trace("/badge", REPO, "ok",
                    chain=tuple(tr.Step(f, line, q, parent=p) for f, line, q, p in chain),
                    reads=tuple(tr.Read("collection", n, "확실", f, line, step=st) for n, f, line, st in reads))


def _check(idx, graph, tracer, *, route="/badge"):
    """오버레이의 사슬은 인덱스가 만든 것, 대조 상대는 추적기 결과."""
    mine = index_trace.trace(idx, graph, repo=REPO, target=route, routes=ROUTES[route])
    overlay = flow.add_trace(_graph(), EP, mine)
    return parity.check(overlay, idx, graph, {EP: tracer})


def _node(g):
    return next(n for n in g["nodes"] if n["type"] == "endpoint")


def _lines(g):
    return flow.trace_lines(g, EP)


async def test_인덱스가_같은_깊이에서_더_본_자원은_인덱스만으로_적힌다():
    """추적기가 `extra`로 가는 가지를 놓쳤다 — 인덱스는 같은 핸들러에서 둘 다 닿는다."""
    idx, graph = await _index()
    g = _check(idx, graph, _tracer(["alarm_events"]))
    assert _node(g)["index_check"] == {"status": "diff", "handler": "api.r.badge", "only_index": ["line_state [collection]"],
                                       "only_tracer": [], "why": {"line_state [collection]": "[svc] r.badge → q.extra"}}
    assert "인덱스 대조: 다르다 — 인덱스만 line_state [collection]" in _lines(g)
    assert "  인덱스만 line_state [collection]: [svc] r.badge → q.extra" in _lines(g)
    s = flow.summary(g)
    assert (s["endpoints_index_same"], s["endpoints_index_diff"], s["endpoints_index_no_handler"]) == (0, 1, 0)


async def test_같으면_같다고_적고_사슬에_대조_줄을_안_붙인다():
    idx, graph = await _index()
    # 오버레이에 노드가 없는 자원(`ghost`)은 양쪽 다 안 센다 — 추적기가 봤다고 적어도 대조에 안 든다.
    g = _check(idx, graph, _tracer(["alarm_events", "line_state", "ghost"]))
    assert _node(g)["index_check"]["status"] == "same"
    assert not any("인덱스 대조" in line for line in _lines(g))


async def test_인덱스가_핸들러를_못_찾으면_끝점은_추적_안_됨이고_대조는_그렇다고_센다():
    """라우트 줄 아래 함수가 인덱스에 없다(파싱 실패·파일 없음) — 추적기가 봤던 끝점이 사슬을 잃는다. 숫자로 남긴다."""
    idx, graph = await _index()
    g = _check(idx, graph, _tracer(["alarm_events"]), route="/gone")
    assert _node(g)["traced"] == "not_found" and _node(g)["index_check"] == {"status": "no_handler"}
    assert _lines(g) is None and flow.summary(g)["endpoints_index_no_handler"] == 1


async def test_인덱스_도달은_추적기와_같은_깊이에서_멈춘다():
    """깊이를 맞추지 않으면 "인덱스만"이 대부분 더 깊이 간 몫이 되어 대조가 뜻을 잃는다."""
    idx, graph = await _index()
    start = idx.lookup(REPO, "api.deep.f0")
    near = qy.reach(graph, [start], max_hops=tr.MAX_DEPTH)
    far = qy.reach(graph, [start], max_hops=tr.MAX_DEPTH + 1)
    assert idx.lookup(REPO, "api.deep.f6") in near and idx.lookup(REPO, "api.deep.f7") not in near
    assert idx.lookup(REPO, "api.deep.f7") in far


async def test_대조도_추적기와_같은_깊이에서_멈춘다():
    """핸들러에서 7단계 아래에만 자원이 있는 끝점 — 추적기는 깊이 6에서 멈춰 못 닿는다. 인덱스가 더 깊이 가서
    "인덱스만"이라 하면 그건 엔진 차이가 아니라 깊이 차이다."""
    idx, graph = await _index()
    g = _check(idx, graph, _tracer([], chain=(("api/deep.py", 2, "f0", None),)), route="/deep")
    assert _node(g)["index_check"]["status"] == "same"
    # 추적기가 7단계 아래에서 읽었다고 하면 — 인덱스는 거기까지 안 갔으니 "못 이은 호출 f6 → f7"이어야지, 더 깊이 가서
    # "닿지만 못 본다"라 하면 대조가 같은 깊이가 아니다.
    deep = tuple(("api/deep.py", 2 + 4 * i, f"f{i}", None if i == 0 else i - 1) for i in range(8))
    g = _check(idx, graph, _tracer([("line_state", "api/deep.py", 31, 7)], chain=deep), route="/deep")
    assert "  추적기만 line_state [collection]: 인덱스가 못 이은 호출 deep.f6 → deep.f7 (api/deep.py:L30)" in _lines(g)


async def test_인덱스만_자원은_핸들러에서_닿은_경로를_말하고_추정_호출을_거치면_그렇다고_적는다():
    """사내 대조의 "인덱스만" 다섯 — 추적기가 놓친 것인지 인덱스가 이름만 같은 후보를 타고 넘어간 것인지 한 줄로 갈린다."""
    idx, graph = await _index()
    g = _check(idx, graph, _tracer(["alarm_events"], chain=(("api/c.py", 5, "cand", None),)), route="/cand")
    assert ("  인덱스만 line_state [collection]: [svc] c.cand ?→ stores.Lines.fetch_rows"
            " — 이름만 같은 후보(?→)를 거친다") in _lines(g)


async def test_인덱스만_경로는_확실한_호출로_닿는_길이_있으면_더_길어도_그것을_고른다():
    """추정 한 걸음짜리 길과 확실한 두 걸음짜리 길이 둘 다 있으면 — 짧은 쪽을 보이면 "후보를 거쳐서만 닿는다"로 읽힌다."""
    idx, graph = await _index()
    g = _check(idx, graph, _tracer(["alarm_events"], chain=(("api/c.py", 14, "mixed", None),)), route="/mixed")
    assert "  인덱스만 line_state [collection]: [svc] c.mixed → c.via_extra → q.extra" in _lines(g)


async def test_추적기만_자원은_인덱스가_그_함수에_닿았는데_못_본_것이다():
    idx, graph = await _index()
    g = _check(idx, graph, _tracer(["alarm_events", "line_state", "audit_log"]))
    assert _node(g)["index_check"]["only_tracer"] == ["audit_log [collection]"]
    assert ("  추적기만 audit_log [collection]: 인덱스는 q.recent에 닿지만 거기서 이 이름을 못 본다"
            " (추적기: api/q.py:L2)") in _lines(g)


async def test_추적기만_자원은_인덱스가_못_이은_호출을_짚는다():
    """추적기 사슬을 뿌리부터 내려가며 인덱스가 처음 못 닿은 걸음 — 그 부모에서 그 걸음으로 가는 호출이 인덱스에 없다."""
    idx, graph = await _index()
    g = _check(idx, graph, _tracer(["alarm_events", "line_state", ("audit_log", "api/q.py", 11, 2)],
                                   chain=(("api/r.py", 5, "badge", None), ("api/q.py", 1, "recent", 0),
                                          ("api/q.py", 10, "lost", 1))))
    assert "  추적기만 audit_log [collection]: 인덱스가 못 이은 호출 q.recent → q.lost (api/q.py:L10)" in _lines(g)


async def test_추적기만_자원의_걸음을_품는_함수가_인덱스에_없으면_그렇게_적는다():
    idx, graph = await _index()
    g = _check(idx, graph, _tracer(["alarm_events", "line_state", ("audit_log", "api/gone.py", 4, 1)],
                                   chain=(("api/r.py", 5, "badge", None), ("api/gone.py", 3, "gone", 0))))
    assert "  추적기만 audit_log [collection]: 추적기 걸음 api/gone.py:L3 gone을 품는 함수가 인덱스에 없다" in _lines(g)


async def test_같은_path의_핸들러가_여럿이면_전부에서_출발한다():
    """추적기는 같은 path에 걸린 라우트 선언 전부(GET·PUT …)를 뿌리로 삼는다 — 인덱스 사슬도 그렇다. 첫 핸들러에서만
    출발하면 둘째 핸들러의 자원이 전부 "추적기만"이 된다(사내 첫 대조에서 토픽을 쓰는 PUT 쪽이 그랬다)."""
    idx, graph = await _index()
    g = _check(idx, graph, _tracer(["alarm_events", "line_state", ("audit_log", "api/r.py", 12, 2)],
                                   chain=(("api/r.py", 5, "badge", None), ("api/q.py", 1, "recent", 0),
                                          ("api/r.py", 11, "update_badge", None))), route="/both")
    assert _node(g)["index_check"]["status"] == "same"
    # 원인 줄도 핸들러 전부에서 되짚는다 — 둘째 핸들러가 직접 건드린 자원을 첫 핸들러에서만 찾으면 길을 못 찾는다.
    g = _check(idx, graph, _tracer(["alarm_events", "line_state"]), route="/both")
    assert "  인덱스만 audit_log [collection]: [svc] r.update_badge — 핸들러가 직접" in _lines(g)


async def test_핸들러가_직접_건드린_자원은_그_핸들러를_적는다():
    idx, graph = await _index()
    g = _check(idx, graph, _tracer([], chain=(("api/r.py", 11, "update_badge", None),)), route="/put")
    assert "  인덱스만 audit_log [collection]: [svc] r.update_badge — 핸들러가 직접" in _lines(g)


async def test_다른_끝점은_path와_차이를_한_줄씩_열_개까지_나열한다():
    """사내 첫 대조: 같음 149 · 다름 7. 숫자만 찍고 어느 끝점인지 안 찍어서 `code trace`로 볼 끝점을 고를 수 없었다."""
    idx, graph = await _index()
    assert flow.index_diff_lines(_check(idx, graph, _tracer(["alarm_events"]))) == ["/badge — 인덱스만 line_state [collection]"]
    assert flow.index_diff_lines(_check(idx, graph, _tracer(["alarm_events", "line_state"]))) == []
    many = {"nodes": [{"id": f"endpoint_e{i:02d}", "label": f"/e{i:02d}", "type": "endpoint",
                       "index_check": {"status": "diff", "only_index": [], "only_tracer": ["x [topic]"]}}
                      for i in reversed(range(12))], "links": []}
    lines = flow.index_diff_lines(many)
    assert len(lines) == 11 and lines[0] == "/e00 — 추적기만 x [topic]" and lines[-1] == "… 외 2"
