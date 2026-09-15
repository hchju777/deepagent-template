"""Mongo 어댑터 — 필터 방어와 잘림 판정.

실서버가 없어도 검증되는 부분: 서버측 JS 연산자를 막는가, 잘렸는지 아는가,
ObjectId 같은 것을 사람이 읽을 형태로 바꾸는가. **실접속 자체**는
`python -m src doctor`가 사내에서 확인한다.
"""
from datetime import UTC, datetime

from src.config.schema_site import MongoConfig
from src.infrastructure.mongo_reader import (RealMongoReader, filter_problems, to_jsonable)

CFG = MongoConfig(url="mongodb://h:27017", database="data",
                  user="dmfReadOnly", password="x", auth_source="admin")


# ── 필터 방어 ────────────────────────────────────────────────────────

def test_보통_질의는_통과한다():
    assert filter_problems({"line": "L3", "ts": {"$gte": 1000}}) == []


def test_서버측_JS_연산자를_막는다():
    # $where는 서버에서 자바스크립트를 실행한다 — 읽기 전용 계정이어도 CPU를 태운다.
    assert filter_problems({"$where": "this.oee > 100"}) == \
        ["filter: 허용되지 않은 연산자 — $where"]


def test_중첩된_곳에_숨은_연산자도_찾는다():
    problems = filter_problems({"$or": [{"a": 1}, {"$function": {"body": "..."}}]})
    assert problems == ["filter.$or[1]: 허용되지 않은 연산자 — $function"]


def test_모르는_연산자는_거부한다():
    # 금지 목록이 아니라 허용 목록인 이유: 새 연산자는 계속 생기고 금지 목록은 항상 뒤늦다.
    assert filter_problems({"x": {"$내년에나올연산자": 1}})


# ── 표시용 변환 ──────────────────────────────────────────────────────

def test_datetime과_모르는_타입을_문자열로_바꾼다():
    class _ObjectId:
        def __str__(self): return "665f1c..."

    out = to_jsonable({"_id": _ObjectId(), "ts": datetime(2026, 3, 1, tzinfo=UTC),
                       "rows": [1, {"n": _ObjectId()}]})
    assert out == {"_id": "665f1c...", "ts": "2026-03-01T00:00:00+00:00",
                   "rows": [1, {"n": "665f1c..."}]}


# ── 잘림 판정 (실서버 없이) ────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, docs): self._docs = docs
    def sort(self, *_): return self
    def limit(self, n): return _FakeCursor(self._docs[:n])
    def __aiter__(self):
        async def gen():
            for doc in self._docs:
                yield doc
        return gen()


class _FakeCollection:
    """pymongo의 `find(filter, projection)` 자리 인자를 그대로 받는다.

    투영을 받기만 하고 **적용하지 않으면** 필드를 좁히는 코드가 여기서는 통과하고
    실서버에서만 "없는 필드"로 깨진다.
    """

    def __init__(self, docs): self._docs = docs

    def find(self, filter, projection=None):
        rows = [d for d in self._docs if all(d.get(k) == v for k, v in filter.items())]
        if projection:
            keep = {k for k, v in projection.items() if v}
            rows = [{k: v for k, v in row.items() if k in keep} for row in rows]
        return _FakeCursor(rows)

    async def count_documents(self, filter): return len(self._docs)


class _FakeDatabase:
    def __init__(self, docs): self._docs = docs
    def __getitem__(self, _name): return _FakeCollection(self._docs)


def _reader(docs, clock):
    return RealMongoReader(CFG, clock=clock, database_factory=lambda: _FakeDatabase(docs))


async def test_상한에_안_걸리면_완전한_봉투다(clock):
    result = await _reader([{"i": 1}, {"i": 2}], clock).find("oee", {}, limit=5)
    assert result.status == "ok" and len(result.data) == 2
    assert result.envelope.complete is True


async def test_상한에_걸리면_잘렸다고_말한다(clock):
    # limit=2로 2건이 나왔을 때 "딱 2건"인지 "더 있는데 잘린 것"인지 구별해야 한다.
    result = await _reader([{"i": i} for i in range(10)], clock).find("oee", {}, limit=2)
    assert len(result.data) == 2
    assert result.envelope.complete is False
    assert "limit=2" in result.envelope.truncated_reason


async def test_딱_상한만큼_있으면_잘리지_않았다(clock):
    result = await _reader([{"i": 1}, {"i": 2}], clock).find("oee", {}, limit=2)
    assert result.envelope.complete is True, "limit+1을 읽지 않으면 여기서 틀린다"


async def test_필터가_거부되면_소켓에_안_나간다(clock):
    def _boom():
        raise AssertionError("거부됐어야 하는데 DB에 접근했다")

    reader = RealMongoReader(CFG, clock=clock, database_factory=_boom)
    result = await reader.find("oee", {"$where": "1"})
    assert result.status == "error" and "$where" in result.error


async def test_투영은_요청한_필드만_돌려준다(clock):
    """리포트는 필드 8개만 쓴다 — 문서 전체를 5만 건 끌어오면 대상의 네트워크와
    디코딩을 그만큼 더 쓴다. 읽기 전용은 성능에 개입하지 않는 것까지 포함한다."""
    docs = [{"_id": "x1", "occ_date": "2026-08-21 00:00:00", "line_code": "P222",
             "payload": "쓰지 않는 큰 값"}]
    result = await _reader(docs, clock).find("alarm", {}, limit=5,
                                            projection=["occ_date", "line_code"])
    assert result.status == "ok"
    assert result.data == [{"occ_date": "2026-08-21 00:00:00", "line_code": "P222"}]


async def test_투영하면_무엇을_물었는지_source에_남는다(clock):
    """`source`가 "무엇을 물었는가"를 남기지 않으면, 필드가 빈 것이 "데이터가
    없어서"인지 "투영에서 빠져서"인지 구별할 수 없다."""
    result = await _reader([{"a": 1}], clock).find("alarm", {}, projection=["b", "a"])
    assert "fields=['a', 'b']" in result.source


async def test_투영을_안_주면_문서_전체가_온다(clock):
    result = await _reader([{"_id": "x1", "a": 1}], clock).find("alarm", {})
    assert result.data == [{"_id": "x1", "a": 1}]
