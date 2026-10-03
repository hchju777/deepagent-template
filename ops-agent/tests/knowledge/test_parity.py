"""추적기와 인덱스의 대조(11d 6c-2) — 엔진을 하나로 모으기 전에 둘이 어디서 다르게 말하나.

리드가 받는 `code.trace` 사슬은 11b 추적기가 만든 것이다. 인덱스가 같은 끝점에서 같은 깊이로 다른 답을 내면
그 차이가 `code.trace` 출력에 나오고 `code status`가 센다 — 패리티가 확인되면 추적기를 뺀다(decisions ⑱)."""
from src.knowledge import flow
from src.knowledge import index as ix
from src.knowledge import parity
from src.knowledge import query as qy
from src.knowledge import trace as tr
from src.knowledge.flow import Name

REPO = "svc"
FILES = {
    "api/__init__.py": "",
    "api/r.py": ("from api.q import recent, extra\n\n\n"
                 "@router.post('/badge')\n"
                 "def badge(db):\n"
                 "    recent(db)\n"
                 "    return extra(db)\n"),
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
    "api/deep.py": "".join(f"def f{i}():\n    return f{i + 1}()\n\n\n" for i in range(7))
                   + "def f7(db):\n    return db[\"line_state\"].find({})\n",
}
# `ghost`는 이름 목록에는 있지만 오버레이에 노드가 없다 — `add_trace`도 안 싣는 자원이라 대조에서도 빠져야 한다.
NAMES = [Name("collection", "alarm_events", "mongodb_collection.alarm"),
         Name("collection", "line_state", "mongodb_collection.line"),
         Name("collection", "ghost", "mongodb_collection.ghost")]


class _Src:
    async def files(self):
        return sorted(FILES)

    async def read(self, path):
        return FILES.get(path)


async def _index():
    return await ix.build_index({REPO: _Src()}, names=NAMES, commits={REPO: "c0ffee"})


def _overlay(reads: tuple[tuple[str, str], ...], first=("api/r.py", 5)):
    g = {"nodes": [{"id": flow.endpoint_id("/badge"), "label": "/badge", "type": "endpoint"},
                   {"id": "collection_alarm_events", "label": "alarm_events", "type": "collection"},
                   {"id": "collection_line_state", "label": "line_state", "type": "collection"}],
         "links": []}
    result = tr.Trace("/badge", REPO, "ok",
                      chain=(tr.Step(first[0], first[1], "badge"), tr.Step("api/q.py", 1, "recent", parent=0)),
                      reads=tuple(tr.Read("collection", n, "확실", "api/q.py", 2, step=1) for _, n in reads))
    return flow.add_trace(g, flow.endpoint_id("/badge"), result)


def _node(g):
    return next(n for n in g["nodes"] if n["type"] == "endpoint")


async def test_인덱스가_같은_깊이에서_더_본_자원은_인덱스만으로_적힌다():
    """추적기가 `extra`로 가는 가지를 놓쳤다 — 인덱스는 같은 핸들러에서 둘 다 닿는다."""
    idx = await _index()
    g = parity.check(_overlay((("collection", "alarm_events"),)), idx, qy.Graph(idx))
    got = _node(g)["index_check"]
    assert got == {"status": "diff", "handler": "api.r.badge", "only_index": ["line_state [collection]"],
                   "only_tracer": [], "why": {"line_state [collection]": "[svc] r.badge → q.extra"}}


async def test_같으면_같다고_적고_사슬에_대조_줄을_안_붙인다():
    idx = await _index()
    g = parity.check(_overlay((("collection", "alarm_events"), ("collection", "line_state"))), idx, qy.Graph(idx))
    assert _node(g)["index_check"]["status"] == "same"
    assert not any("인덱스 대조" in line for line in flow.trace_lines(g, flow.endpoint_id("/badge")))


async def test_다르면_code_trace_출력에_대조_줄이_나오고_status가_센다():
    idx = await _index()
    g = parity.check(_overlay((("collection", "alarm_events"),)), idx, qy.Graph(idx))
    lines = flow.trace_lines(g, flow.endpoint_id("/badge"))
    assert "인덱스 대조: 다르다 — 인덱스만 line_state [collection]" in lines
    s = flow.summary(g)
    assert (s["endpoints_index_same"], s["endpoints_index_diff"], s["endpoints_index_no_handler"]) == (0, 1, 0)


async def test_사슬_첫_걸음을_품는_함수가_인덱스에_없으면_핸들러를_못_찾았다고_적는다():
    idx = await _index()
    g = parity.check(_overlay((("collection", "alarm_events"),), first=("api/gone.py", 3)), idx, qy.Graph(idx))
    assert _node(g)["index_check"] == {"status": "no_handler"}
    assert "인덱스 대조: 핸들러를 인덱스에서 못 찾았다(api/gone.py:L3)" in flow.trace_lines(g, flow.endpoint_id("/badge"))


async def test_인덱스_도달은_추적기와_같은_깊이에서_멈춘다():
    """깊이를 맞추지 않으면 "인덱스만"이 대부분 더 깊이 간 몫이 되어 대조가 뜻을 잃는다."""
    idx = await _index()
    g = qy.Graph(idx)
    start = idx.lookup(REPO, "api.deep.f0")
    near = qy.reach(g, [start], max_hops=tr.MAX_DEPTH)
    far = qy.reach(g, [start], max_hops=tr.MAX_DEPTH + 1)
    assert idx.lookup(REPO, "api.deep.f6") in near and idx.lookup(REPO, "api.deep.f7") not in near
    assert idx.lookup(REPO, "api.deep.f7") in far


async def test_대조도_추적기와_같은_깊이에서_멈춘다():
    """핸들러에서 7단계 아래에만 자원이 있는 끝점 — 추적기는 깊이 6에서 멈춰 못 닿는다. 인덱스가 더 깊이 가서
    "인덱스만"이라 하면 그건 엔진 차이가 아니라 깊이 차이다."""
    idx = await _index()
    g = parity.check(_overlay((), first=("api/deep.py", 1)), idx, qy.Graph(idx))
    assert _node(g)["index_check"]["status"] == "same"


async def test_다른_끝점은_path와_차이를_한_줄씩_열_개까지_나열한다():
    """사내 첫 대조: 같음 149 · 다름 7. 숫자만 찍고 어느 끝점인지 안 찍어서 `code trace`로 볼 끝점을 고를 수 없었다."""
    idx = await _index()
    g = parity.check(_overlay((("collection", "alarm_events"),)), idx, qy.Graph(idx))
    assert flow.index_diff_lines(g) == ["/badge — 인덱스만 line_state [collection]"]
    same = parity.check(_overlay((("collection", "alarm_events"), ("collection", "line_state"))), idx, qy.Graph(idx))
    assert flow.index_diff_lines(same) == []
    many = {"nodes": [{"id": f"endpoint_e{i:02d}", "label": f"/e{i:02d}", "type": "endpoint",
                       "index_check": {"status": "diff", "only_index": [], "only_tracer": ["x [topic]"]}}
                      for i in reversed(range(12))], "links": []}
    lines = flow.index_diff_lines(many)
    assert len(lines) == 11 and lines[0] == "/e00 — 추적기만 x [topic]" and lines[-1] == "… 외 2"


def _traced(chain, reads):
    """추적기 사슬을 손으로 — chain은 (파일, 줄, 이름, 부모 걸음), reads는 (이름, 파일, 줄, 걸음)."""
    g = {"nodes": [{"id": flow.endpoint_id("/badge"), "label": "/badge", "type": "endpoint"},
                   *({"id": f"collection_{n}", "label": n, "type": "collection"}
                     for n in ("alarm_events", "line_state", "audit_log"))],
         "links": []}
    result = tr.Trace("/badge", REPO, "ok",
                      chain=tuple(tr.Step(f, line, q, parent=p) for f, line, q, p in chain),
                      reads=tuple(tr.Read("collection", n, "확실", f, line, step=st) for n, f, line, st in reads))
    return flow.add_trace(g, flow.endpoint_id("/badge"), result)


def _why(g):
    return flow.trace_lines(g, flow.endpoint_id("/badge"))


async def test_인덱스만_자원은_핸들러에서_닿은_경로를_말하고_추정_호출을_거치면_그렇다고_적는다():
    """사내 대조의 "인덱스만" 다섯 — 추적기가 놓친 것인지 인덱스가 이름만 같은 후보를 타고 넘어간 것인지 한 줄로 갈린다."""
    idx = await _index()
    g = parity.check(_overlay((("collection", "alarm_events"),), first=("api/c.py", 5)), idx, qy.Graph(idx))
    assert ("  인덱스만 line_state [collection]: [svc] c.cand ?→ stores.Lines.fetch_rows"
            " — 이름만 같은 후보(?→)를 거친다") in _why(g)


async def test_인덱스만_경로는_확실한_호출로_닿는_길이_있으면_더_길어도_그것을_고른다():
    """추정 한 걸음짜리 길과 확실한 두 걸음짜리 길이 둘 다 있으면 — 짧은 쪽을 보이면 "후보를 거쳐서만 닿는다"로 읽힌다."""
    idx = await _index()
    g = parity.check(_overlay((("collection", "alarm_events"),), first=("api/c.py", 14)), idx, qy.Graph(idx))
    assert "  인덱스만 line_state [collection]: [svc] c.mixed → c.via_extra → q.extra" in _why(g)


async def test_추적기만_자원은_인덱스가_그_함수에_닿았는데_못_본_것이다():
    idx = await _index()
    g = parity.check(_traced([("api/r.py", 5, "badge", None), ("api/q.py", 1, "recent", 0)],
                             [("alarm_events", "api/q.py", 2, 1), ("line_state", "api/q.py", 2, 1),
                              ("audit_log", "api/q.py", 2, 1)]), idx, qy.Graph(idx))
    assert _node(g)["index_check"]["only_tracer"] == ["audit_log [collection]"]
    assert ("  추적기만 audit_log [collection]: 인덱스는 q.recent에 닿지만 거기서 이 이름을 못 본다"
            " (추적기: api/q.py:L2)") in _why(g)


async def test_추적기만_자원은_인덱스가_못_이은_호출을_짚는다():
    """추적기 사슬을 뿌리부터 내려가며 인덱스가 처음 못 닿은 걸음 — 그 부모에서 그 걸음으로 가는 호출이 인덱스에 없다."""
    idx = await _index()
    g = parity.check(_traced([("api/r.py", 5, "badge", None), ("api/q.py", 1, "recent", 0),
                              ("api/q.py", 10, "lost", 1)],
                             [("alarm_events", "api/q.py", 2, 1), ("line_state", "api/q.py", 2, 1),
                              ("audit_log", "api/q.py", 11, 2)]), idx, qy.Graph(idx))
    assert "  추적기만 audit_log [collection]: 인덱스가 못 이은 호출 q.recent → q.lost (api/q.py:L10)" in _why(g)


async def test_추적기만_자원의_걸음을_품는_함수가_인덱스에_없으면_그렇게_적는다():
    idx = await _index()
    g = parity.check(_traced([("api/r.py", 5, "badge", None), ("api/gone.py", 3, "gone", 0)],
                             [("alarm_events", "api/q.py", 2, 0), ("line_state", "api/q.py", 2, 0),
                              ("audit_log", "api/gone.py", 4, 1)]), idx, qy.Graph(idx))
    assert "  추적기만 audit_log [collection]: 추적기 걸음 api/gone.py:L3 gone을 품는 함수가 인덱스에 없다" in _why(g)
