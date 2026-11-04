"""코드 호출 흐름 사람용 html(11d 6c-1b) — 외부 참조 0, 데이터 안전, 실린 데이터가 질의와 같다.

그림 자체는 브라우저에서 사람이 본다. 여기서 지키는 것은 **무엇을 실었나** — 판정(진입점·경로·디스패치)은 파이썬
질의가 한 것과 같아야 한다. 브라우저에서 같은 판정을 새로 짜면 시험이 그걸 못 지킨다(flow.html과 같은 원칙)."""
import dataclasses
import json
import re

from src.knowledge import index as ix
from src.knowledge import query as qy
from src.presentation import calls_html
from tests.knowledge.test_query import FILES, NAMES, REPO, _Src


async def _built():
    return await ix.build_index({REPO: _Src(FILES)}, names=NAMES, commits={REPO: "c0ffee"})


def _data(page: str) -> dict:
    blob = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1)
    return json.loads(blob.replace("<\\/", "</"))


def _page(idx, title="mx/gumi"):
    return calls_html.render(idx, title=title, built_at="2026-10-03T10:00", commits={REPO: "c0ffee"},
                             service_of=lambda s: "ingest" if s.file == "app/api.py" else None)


async def test_외부_참조가_없고_데이터_속_닫는_태그와_제목이_문서를_못_끊는다():
    idx = await _built()
    w = idx.lookup(REPO, "app.impl.MongoStore.write")
    sym = idx.symbols[w]
    idx.symbols[w] = dataclasses.replace(sym, resources=sym.resources + (
        ix.Resource("collection", "x</script><b>", "writes", 9, "key"),))
    page = _page(idx, title='mx/gumi <"x">')
    assert not re.search(r'(src|href)="https?://', page)
    assert "<script" in page and "<style>" in page
    body = page.split('<script id="data"', 1)[1]
    assert "</script><b>" not in body.split("</script>", 1)[0]
    assert "<\\/script><b>" in page
    assert "<title>mx/gumi &lt;&quot;x&quot;&gt; · 코드 흐름</title>" in page


async def test_인접_목록은_질의_그래프와_같다():
    """호출(확실·추정)과 디스패치(베이스 => 구현) — `code callers`·`code path`가 걷는 것과 같은 길이어야 한다."""
    idx = await _built()
    data = _data(_page(idx))
    q = [n["q"] for n in data["nodes"]]
    got = sorted((q[a], q[b], m) for a, b, m, _ in data["links"])
    g = qy.Graph(idx)
    want = sorted((idx.symbols[l.src].qualname, idx.symbols[l.dst].qualname, l.mark)
                  for links in g.out.values() for l in links)
    assert got == want and ("app.base.Store.save", "app.impl.MongoStore.save", "=>") in got


async def test_자원마다_쓰고_읽는_함수와_진입점_경로를_질의로_미리_계산해_싣는다():
    """자원 화면은 "진입점 → … → 쓰는 함수 → [자원] → 읽는 함수 → … → 진입점"이다. 그 경로를 질의가 계산한다."""
    idx = await _built()
    data = _data(_page(idx))
    q = [n["q"] for n in data["nodes"]]
    res = next(r for r in data["res"] if r["n"] == "events")
    assert res["k"] == "collection"
    (w,) = res["w"]
    assert q[w["n"]] == "app.impl.MongoStore.write"
    roots = sorted(q[p[0][0]] for p in w["p"])
    assert roots == ["app.api.ingest", "app.svc.batch"]
    ingest = next(p for p in w["p"] if q[p[0][0]] == "app.api.ingest")
    assert [(q[a], q[b], m) for a, b, m in ingest] == [
        ("app.api.ingest", "app.svc.handle", "→"), ("app.svc.handle", "app.base.Store.save", "→"),
        ("app.base.Store.save", "app.impl.MongoStore.save", "=>"), ("app.impl.MongoStore.save", "app.impl.MongoStore.write", "→")]
    (r,) = res["r"]
    assert q[r["n"]] == "app.api.count" and r["p"] == []           # 부르는 쪽이 없다 — 이 함수가 진입점


async def test_진입점_라우트_베이스와_함수마다_쓰고_읽는_자원을_표시한다():
    idx = await _built()
    data = _data(_page(idx))
    by_q = {n["q"]: n for n in data["nodes"]}
    assert by_q["app.svc.batch"]["e"] == "entry" and by_q["app.svc.batch"]["rt"] == ""
    assert by_q["app.api.ingest"]["e"] == "entry" and by_q["app.api.ingest"]["rt"] == "router.post('/ingest')"
    assert by_q["app.plugin.Plugin.run"]["e"] == "base"              # 부르는 쪽 없는 베이스 — 진입점이 아니다
    assert by_q["app.svc.handle"]["e"] == ""
    assert by_q["app.api.ingest"]["v"] == "ingest" and by_q["app.svc.handle"]["v"] == ""
    write = by_q["app.impl.MongoStore.write"]
    assert [(data["res"][i]["n"], d) for i, d, _ in write["u"]] == [("events", "w")]
    assert by_q["app.impl.MongoStore.write"]["s"] == "impl.MongoStore.write"
