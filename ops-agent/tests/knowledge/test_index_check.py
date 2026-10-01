"""인덱스 검증 하네스(11d 6a) — 정확도를 숫자로. 이게 없으면 인덱스의 숫자는 믿을 수 없는 숫자다."""
import dataclasses

from src.knowledge import index as ix
from src.knowledge import index_check as chk
from tests.knowledge.test_index import FILES, NAMES, REPO, _Src


async def _built(files=FILES):
    src = _Src(files)
    idx = await ix.build_index({REPO: src}, names=NAMES, commits={REPO: "c0ffee"})
    counts = {(REPO, p): len(t.splitlines()) for p, t in files.items()}
    return idx, src, {REPO: set(files)}, counts


async def _read_of(src):
    async def read(repo, path):
        return await src.read(path)
    return read


async def test_구조_불변식은_만든_인덱스에서_전부_통과한다():
    idx, src, files, counts = await _built()
    assert chk.invariants(idx, files=files, line_counts=counts) == []


async def test_불변식은_id_엣지_class_id_줄_범위_커버리지_위반을_각각_잡는다():
    idx, src, files, counts = await _built()
    broken = ix.Index.from_dict(idx.to_dict())
    broken.symbols[3] = dataclasses.replace(broken.symbols[3], id=99)
    assert any("id" in p for p in chk.invariants(broken, files=files, line_counts=counts))
    broken = ix.Index.from_dict(idx.to_dict())
    broken.edges.append(ix.Edge(src=0, dst=10_000, type="calls", certainty="exact", line=1))
    broken.edges.append(ix.Edge(src=1, dst=1, type="calls", certainty="exact", line=1))
    broken.edges.append(ix.Edge(src=1, dst=2, type="loves", certainty="maybe", line=1))
    problems = chk.invariants(broken, files=files, line_counts=counts)
    assert sum("엣지" in p for p in problems) >= 3
    broken = ix.Index.from_dict(idx.to_dict())
    m = next(s for s in broken.symbols if s.kind == "method")
    broken.symbols[m.id] = dataclasses.replace(m, class_id=0)          # 0은 모듈이지 클래스가 아니다
    assert any("class_id" in p for p in chk.invariants(broken, files=files, line_counts=counts))
    broken = ix.Index.from_dict(idx.to_dict())
    broken.symbols[m.id] = dataclasses.replace(m, end_line=10_000)
    assert any("줄" in p for p in chk.invariants(broken, files=files, line_counts=counts))
    more = dict(files); more[REPO] = set(files[REPO]) | {"app/ghost.py"}
    assert any("커버리지" in p for p in chk.invariants(idx, files=more, line_counts=counts))


async def test_정밀도_표본은_exact_calls의_대상_이름이_호출자_본문이나_데코레이터에_있는지_본다():
    files = dict(FILES)
    files["app/deco.py"] = (
        'def cached(ttl):\n'
        '    return lambda f: f\n'
        '\n'
        '@cached(30)\n'
        'def view():\n'
        '    return 1\n')
    idx, src, _, _ = await _built(files)
    ok, total, failures = await chk.precision_sample(idx, await _read_of(src), n=500, seed=1)
    assert total > 0 and ok == total, failures
    # 데코레이터 호출은 본문 밖에 있다 — 심볼의 decorators를 안 보면 멀쩡한 엣지가 거짓 실패다.
    view = idx.lookup(REPO, "app.deco.view")
    assert any(e.src == view and idx.symbols[e.dst].qualname == "app.deco.cached" for e in idx.edges)
    tampered = ix.Index.from_dict(idx.to_dict())
    tampered.edges.append(ix.Edge(src=idx.lookup(REPO, "app.util.unused"), dst=idx.lookup(REPO, "app.util.now"),
                                  type="calls", certainty="exact", line=8))
    ok2, total2, failures2 = await chk.precision_sample(tampered, await _read_of(src), n=500, seed=1)
    assert ok2 == total2 - 1 and failures2 and "unused" in failures2[0]


async def test_재현율_표본은_같은_모듈_최상위_호출이_전부_엣지로_있는지_본다():
    """정밀도만 재면 아무것도 안 잇는 인덱서가 100%다."""
    idx, src, _, _ = await _built()
    found, total, misses = await chk.recall_sample(idx, await _read_of(src))
    assert total >= 1 and found == total, misses
    pruned = ix.Index.from_dict(idx.to_dict())
    helper, now = idx.lookup(REPO, "app.util.helper"), idx.lookup(REPO, "app.util.now")
    pruned.edges = [e for e in pruned.edges if not (e.src == helper and e.dst == now)]
    found2, total2, misses2 = await chk.recall_sample(pruned, await _read_of(src))
    assert found2 == total2 - 1 and misses2 and "helper" in misses2[0]


async def test_보고는_여덟_줄_이내다():
    idx, src, files, counts = await _built()
    lines = chk.report(idx, chk.invariants(idx, files=files, line_counts=counts),
                       await chk.precision_sample(idx, await _read_of(src), n=50, seed=1),
                       await chk.recall_sample(idx, await _read_of(src)))
    assert 3 <= len(lines) <= 8
    assert any("커버리지" in l for l in lines) and any("정밀도" in l for l in lines) and any("재현율" in l for l in lines)
