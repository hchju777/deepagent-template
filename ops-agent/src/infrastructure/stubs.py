"""인메모리 대역 — 테스트가 실제 시스템 없이 돌게 한다.

같은 포트를 구현하므로 판정 로직은 어느 쪽이 꽂혔는지 모른다. 이게 성립하려면
스텁이 **실구현과 같은 계약**을 지켜야 한다: 키가 없으면 error가 아니라 None,
잘리면 봉투가 말한다, 필터 연산자 검사도 똑같이 한다.

계약이 갈라지면 테스트는 통과하는데 사내에서 깨진다 — 제일 나쁜 모양이다.

재생 시드(R3-5v ① — 평가자 회신 11-03: R3는 그날 뜬 스냅샷의 재생 판으로 닫는다)의 꼴. 평가자의 변환기가 이 꼴로 쓴다:
- `snapshot`: `{"id", "captured_at"(시간대 포함), "complete": [전부 뜬 시스템]}` — 이 절이 있으면 재생이다(`Snapshot`).
- `redis`: `{키: 값}` — 전부 떴으면 `complete`에 redis. `peers.<사이트>.redis`도 같은 꼴.
- `kafka`: `{토픽: {"begin": b, "end": e, "messages": [{"offset", "value", "timestamp"?, "key"?}]}}` — 실제 begin/end와 뜬 메시지만(띄엄).
- `rest`: `{항목: {"captures": [{"params": {…}, "response": …}]}}` — 인자별. 재생에서 인자 없는 응답 꼴은 안 쓴다.
- `mongo`: `{컬렉션: [문서…]}` — 뜬 컬렉션만.
"""
import json
from typing import Any, Literal

from pydantic import AwareDatetime, Field

from src.config.schema_app import StrictModel
from src.domain.base import Clock
from src.domain.case import FilterScan
from src.domain.envelope import ProbeResult
from src.domain.jsonpath import select
from src.domain.ports import (DeployedCodePort, KafkaInspectorPort, MongoReaderPort, RedisReaderPort,
                              RestProberPort)
from src.infrastructure.mongo_reader import filter_problems, to_jsonable
from src.infrastructure.rest_prober import prepare_params


class Snapshot(StrictModel):
    """재생 스냅샷의 머리 — 시드의 `snapshot` 절. 이 절이 있으면 재생이다: 스텁의 관측 시각은 `captured_at`에 고정되고(지난 스냅샷을 오늘 재생하면 지금 시각으로는
    모든 값이 낡아 보인다), `complete`에 없는 시스템은 스냅샷 밖 읽기가 "없음"이 아니라 **실패**다(안 뜬 것을 없음으로 답하면 `키가 없다`·빈 토픽 같은 틀린 사실이
    된다 — R3의 종료 조건 첫째가 틀린 사실 0이다). `complete`는 전부 뜬 시스템 — 거기서 없는 것은 진짜 없음이다."""
    id: str = Field(min_length=1)
    captured_at: AwareDatetime
    complete: list[Literal["redis", "mongo", "kafka", "rest"]] = []


def snapshot_of(seeds) -> Snapshot | None:
    """시드의 재생 머리. 없으면 None, 틀렸으면 pydantic 오류 — CLI 경계(`_replay_snapshot`)가 사람 말로 바꾼다."""
    raw = seeds.get("snapshot") if isinstance(seeds, dict) else None
    return None if raw is None else Snapshot.model_validate(raw)


def _outside(snapshot: Snapshot | None, system: str) -> bool:
    """이 시스템의 스냅샷 밖 읽기를 실패로 답하나 — 재생이고 그 시스템을 다 뜨지 않았을 때."""
    return snapshot is not None and system not in snapshot.complete


def _replay_clock(snapshot: Snapshot | None, clock: Clock) -> Clock:
    if snapshot is None:
        return clock
    at = snapshot.captured_at
    return lambda: at


class StubRedisReader(RedisReaderPort):
    def __init__(self, values: dict[str, Any] | None = None, *, clock: Clock, snapshot: Snapshot | None = None):
        self._values = dict(values or {})
        self._snapshot = snapshot
        self._outside = _outside(snapshot, "redis")
        self._clock = _replay_clock(snapshot, clock)

    async def get(self, key: str, *, path: str | None = None) -> ProbeResult:
        source = f"stub-redis:{key}" + (f" path={path}" if path else "")
        found = self._values.get(key)
        if found is None:
            if self._outside:
                return ProbeResult.failed(f"스냅샷 밖 — 키 {key}는 안 떴다", source=source, clock=self._clock)
            return ProbeResult.succeeded(None, source=source, clock=self._clock)
        if isinstance(found, dict) and set(found) == {"__error__"}:
            # 시드가 읽기 실패를 흉내낸다(R3-5b ② 측정판) — 실서버의 WRONGTYPE·시간 초과 자리. 키 없음(None, 성공)과 다른 사실이다.
            return ProbeResult.failed(str(found["__error__"]), source=source, clock=self._clock)
        if path is None:
            return ProbeResult.succeeded({"type": "string", "value": found}, source=source, clock=self._clock)
        ok, picked = select(found, path)
        if not ok:
            return ProbeResult.failed(picked, source=source, clock=self._clock)
        return ProbeResult.succeeded({"type": "string", "path": path, "value": picked}, source=source,
                                     clock=self._clock)

    async def scan(self, pattern: str) -> ProbeResult:
        import fnmatch
        if self._outside:
            return ProbeResult.failed("스냅샷 밖 — Redis를 다 뜨지 않아 scan에 답할 수 없다", source=f"stub-redis:SCAN {pattern}", clock=self._clock)
        keys = sorted(k for k in self._values if fnmatch.fnmatch(k, pattern))
        return ProbeResult.succeeded(keys, source=f"stub-redis:SCAN {pattern}", clock=self._clock)

    async def ttl(self, key: str) -> ProbeResult:
        source = f"stub-redis:TTL {key}"
        if key in self._values:
            if self._snapshot is not None:
                # 스냅샷은 값만 뜬다 — 있는 키의 TTL을 "만료 없음(-1)"으로 답하면 지어낸 사실이다.
                return ProbeResult.failed("스냅샷에 TTL이 없다 — 값만 떴다", source=source, clock=self._clock)
            return ProbeResult.succeeded(-1, source=source, clock=self._clock)
        if self._outside:
            return ProbeResult.failed(f"스냅샷 밖 — 키 {key}는 안 떴다", source=source, clock=self._clock)
        return ProbeResult.succeeded(-2, source=source, clock=self._clock)      # Redis 규약 그대로


class _UnsupportedOperator(Exception):
    """스텁 내부 신호 — 밖으로 나가기 전에 error ProbeResult로 바뀐다."""


class StubMongoReader(MongoReaderPort):
    def __init__(self, collections: dict[str, list[dict]] | None = None, *, clock: Clock, snapshot: Snapshot | None = None):
        self._collections = {k: list(v) for k, v in (collections or {}).items()}
        self._outside = _outside(snapshot, "mongo")
        self._clock = _replay_clock(snapshot, clock)

    def _unseen(self, collection: str, source: str) -> ProbeResult | None:
        if self._outside and collection not in self._collections:
            return ProbeResult.failed(f"스냅샷 밖 — 컬렉션 {collection}은 안 떴다", source=source, clock=self._clock)
        return None

    # 스텁이 흉내내는 연산자. **여기 없는 연산자는 조용히 무시하지 않고 거부한다** —
    # 무시하면 필터가 안 걸린 채 전부 돌려주고, 테스트는 초록인데 실서버에서만
    # 다른 결과가 나온다. 거짓 초록은 스텁이 낼 수 있는 최악의 실패다.
    _OPERATORS = {
        "$eq": lambda v, want: v == want,
        "$ne": lambda v, want: v != want,
        "$gt": lambda v, want: v is not None and v > want,
        "$gte": lambda v, want: v is not None and v >= want,
        "$lt": lambda v, want: v is not None and v < want,
        "$lte": lambda v, want: v is not None and v <= want,
        "$in": lambda v, want: v in want,
        "$nin": lambda v, want: v not in want,
        "$exists": lambda v, want: (v is not None) == bool(want),
    }

    def _match_one(self, value, condition) -> bool:
        if not isinstance(condition, dict) or not any(
                isinstance(k, str) and k.startswith("$") for k in condition):
            return value == condition
        for operator, want in condition.items():
            handler = self._OPERATORS.get(operator)
            if handler is None:
                raise _UnsupportedOperator(operator)
            try:
                if not handler(value, want):
                    return False
            except TypeError:
                # 문자열과 숫자를 비교하는 등 — 실서버라면 타입별 순서 규칙이
                # 있지만 스텁은 흉내내지 않는다. 안 맞는 것으로 본다.
                return False
        return True

    def _match(self, doc: dict, filter: dict) -> bool:
        return all(self._match_one(doc.get(k), v) for k, v in filter.items())

    async def find(self, collection: str, filter: dict, *, sort=None, limit=None,
                   projection=None) -> ProbeResult:
        source = f"stub-mongo:{collection} find={filter}"
        unseen = self._unseen(collection, source)
        if unseen is not None:
            return unseen
        problems = filter_problems(filter)
        if problems:
            return ProbeResult.failed("; ".join(problems), source=source, clock=self._clock)
        try:
            rows = [to_jsonable(d) for d in self._collections.get(collection, [])
                    if self._match(d, filter)]
        except _UnsupportedOperator as exc:
            return ProbeResult.failed(f"스텁이 흉내내지 않는 연산자 — {exc}. "
                                      f"이 질의는 실서버로만 검증할 수 있다",
                                      source=source, clock=self._clock)
        if projection:
            # 실구현이 필드를 좁히는데 스텁이 전부 돌려주면, 투영에서 빠진 필드를
            # 읽는 코드가 스텁으로는 통과하고 실서버에서만 깨진다.
            keep = set(projection)
            rows = [{k: v for k, v in row.items() if k in keep} for row in rows]
        truncated = None
        if limit is not None and len(rows) > limit:
            rows, truncated = rows[:limit], f"limit={limit}에 걸림 — 더 있다"
        return ProbeResult.succeeded(rows, source=source, clock=self._clock,
                                     truncated_reason=truncated)

    async def list_collections(self) -> ProbeResult:
        if self._outside:
            return ProbeResult.failed("스냅샷 밖 — 컬렉션 목록은 안 떴다", source="stub-mongo:collections", clock=self._clock)
        return ProbeResult.succeeded(sorted(self._collections),
                                     source="stub-mongo:collections", clock=self._clock)

    async def count(self, collection: str, filter: dict) -> ProbeResult:
        source = f"stub-mongo:{collection} count={filter}"
        unseen = self._unseen(collection, source)
        if unseen is not None:
            return unseen
        problems = filter_problems(filter)
        if problems:
            return ProbeResult.failed("; ".join(problems), source=source, clock=self._clock)
        try:
            total = sum(1 for d in self._collections.get(collection, [])
                        if self._match(d, filter))
        except _UnsupportedOperator as exc:
            return ProbeResult.failed(f"스텁이 흉내내지 않는 연산자 — {exc}. "
                                      f"이 질의는 실서버로만 검증할 수 있다",
                                      source=source, clock=self._clock)
        return ProbeResult.succeeded(total, source=source, clock=self._clock)


class StubKafkaInspector(KafkaInspectorPort):
    def __init__(self, topics: dict[str, Any] | None = None,
                 lags: dict[str, int | dict[str, int]] | None = None, *, clock: Clock, snapshot: Snapshot | None = None):
        # 토픽 시드는 메시지 목록(오프셋 = 차례, 파티션 0 하나)이거나 `{"begin": n, "messages": […]}` — 보존이 n부터(그 앞은 지워진 것). 측정판이
        # "키를 만든 메시지가 토픽에 없다"(R3-5d ④)를 심는 자리다. `end`가 있으면 재생 꼴(R3-5v ①): 실제 begin/end와 오프셋 달린 뜬 메시지만(띄엄) —
        # 범위 안인데 안 뜬 오프셋은 "없음"이 아니라 실패다.
        self._begins: dict[str, int] = {}
        self._ends: dict[str, int] = {}
        self._caught: dict[str, dict[int, dict]] = {}
        for name, seed in (topics or {}).items():
            if isinstance(seed, dict) and "end" in seed:
                self._begins[name], self._ends[name] = int(seed.get("begin") or 0), int(seed["end"])
                self._caught[name] = {int(m["offset"]): m for m in seed.get("messages") or [] if isinstance(m, dict) and "offset" in m}
                continue
            begin = int(seed.get("begin") or 0) if isinstance(seed, dict) else 0
            messages = list(seed.get("messages") or []) if isinstance(seed, dict) else list(seed)
            self._begins[name], self._ends[name] = begin, begin + len(messages)
            self._caught[name] = {begin + i: {"value": m} for i, m in enumerate(messages)}
        self._lags = dict(lags or {})
        self._outside = _outside(snapshot, "kafka")
        self._clock = _replay_clock(snapshot, clock)

    async def list_topics(self) -> ProbeResult:
        if self._outside:
            return ProbeResult.failed("스냅샷 밖 — 토픽 목록은 안 떴다", source="stub-kafka:topics", clock=self._clock)
        return ProbeResult.succeeded(sorted(self._ends),
                                     source="stub-kafka:topics", clock=self._clock)

    async def group_offsets(self, group: str) -> ProbeResult:
        # seed가 토픽별로 주면 실어댑터처럼 토픽 행을 낸다. 총량만 주면 행을 **비운다** —
        # 예전엔 `topic="stub"` 행을 지어냈는데, 3b 측정에서 리드가 그걸 "stub 토픽을
        # 구독하고 있다"로 읽고 결론을 그 위에 세웠다. 리드는 스텁인 줄 모른다.
        lag = self._lags.get(group)
        if lag is None and self._outside:
            return ProbeResult.failed(f"스냅샷 밖 — 그룹 {group}의 오프셋은 안 떴다", source=f"stub-kafka:group_offsets {group}", clock=self._clock)
        if lag is None:
            # 모르는 그룹에 lag 0을 돌려주면 지어낸 이름이 "정상"으로 읽힌다(3b off-1). 실어댑터와
            # 같은 모양으로 "커밋된 오프셋이 없다"고 답한다.
            return ProbeResult.succeeded(
                {"group": group, "partitions": [],
                 "note": "커밋된 오프셋이 없다 — 그룹이 없거나 아직 아무것도 안 읽었다"},
                source=f"stub-kafka:group_offsets {group}", clock=self._clock)
        if isinstance(lag, dict):
            rows = [{"topic": topic, "partition": 0, "committed": 0, "end": n, "lag": n}
                    for topic, n in sorted(lag.items())]
            total = sum(lag.values())
        else:
            rows, total = [], lag
        return ProbeResult.succeeded(
            {"group": group, "total_lag": total, "partitions": rows},
            source=f"stub-kafka:group_offsets {group}", clock=self._clock)

    async def tail(self, topic: str, *, limit: int = 10) -> ProbeResult:
        source = f"stub-kafka:tail {topic}"
        if topic not in self._ends:
            if self._outside:
                return ProbeResult.failed(f"스냅샷 밖 — 토픽 {topic}은 안 떴다", source=source, clock=self._clock)
            return ProbeResult.succeeded({"topic": topic, "messages": []}, source=source, clock=self._clock)
        caught = self._caught[topic]
        want = range(max(self._begins[topic], self._ends[topic] - max(limit, 0)), self._ends[topic])
        missing = [o for o in want if o not in caught]
        if missing:
            return ProbeResult.failed(f"스냅샷 밖 — {topic}의 끝 {len(want)}건 중 {len(missing)}건이 안 떴다(뜬 오프셋 {len(caught)}개)",
                                      source=source, clock=self._clock)
        return ProbeResult.succeeded({"topic": topic, "messages": [caught[o]["value"] for o in want]},
                                     source=source, clock=self._clock)

    async def message_at(self, topic: str, partition: int, offset: int) -> ProbeResult:
        source = f"stub-kafka:message_at {topic}[{partition}]@{offset}"
        if topic not in self._ends:
            return ProbeResult.failed(f"토픽이 없거나 파티션을 못 찾았다 — {topic}", source=source, clock=self._clock)
        if int(partition) != 0:
            return ProbeResult.failed(f"파티션 {partition}이 없다 — 스텁 토픽은 파티션 0 하나다", source=source, clock=self._clock)
        begin, end = self._begins[topic], self._ends[topic]
        head = {"topic": topic, "partition": 0, "offset": int(offset), "begin": begin, "end": end}
        if not begin <= int(offset) < end:
            return ProbeResult.succeeded({**head, "found": False, "message": None, "bytes": 0,
                                          "note": f"보존 범위 밖 — 남은 것은 {begin}부터 {end} 전까지"}, source=source, clock=self._clock)
        caught = self._caught[topic].get(int(offset))
        if caught is None:
            return ProbeResult.failed(f"스냅샷 밖 — {topic}[0]@{int(offset)}는 안 떴다(뜬 오프셋 {len(self._caught[topic])}개)",
                                      source=source, clock=self._clock)
        value = caught["value"]
        return ProbeResult.succeeded({**head, "found": True, "bytes": len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")),
                                      "message": {"partition": 0, "offset": int(offset), "timestamp": caught.get("timestamp"),
                                                  "key": caught.get("key"), "value": value}},
                                     source=source, clock=self._clock)

    async def offsets(self, topic: str) -> ProbeResult:
        source = f"stub-kafka:offsets {topic}"
        if topic not in self._ends:
            return ProbeResult.failed(f"토픽이 없거나 파티션을 못 찾았다 — {topic}", source=source, clock=self._clock)
        first, last = self._begins[topic], self._ends[topic]
        return ProbeResult.succeeded({"topic": topic, "partitions": [{"partition": 0, "begin": first, "end": last}], "messages": last - first},
                                     source=source, clock=self._clock)


class StubDeployedCode(DeployedCodePort):
    """`case dryrun`의 코드 자리. **seed의 `trace`·`uses`·`callers`만 안다** — 본문은 사람이 적은 그대로
    돌려주고, 나머지(services·config·grep·flow·read)는 지어내지 않고 없다고 한다. 대본이 rest → code.trace →
    recompute → code.uses → code.callers 사다리를 대상 레포 없이 밟아 보기 위한 것이지, 코드 읽기를 흉내내는
    것이 아니다 — 지어낸 grep 결과는 리드(대본)에게 증거다."""

    def __init__(self, seeds: dict[str, Any] | None, *, clock: Clock):
        seeds = seeds or {}
        self._trace = dict(seeds.get("trace") or {})
        self._uses = dict(seeds.get("uses") or {})
        self._callers = dict(seeds.get("callers") or {})
        self._resources = dict(seeds.get("resources") or {})
        self._clock = clock

    def _none(self, source: str) -> ProbeResult:
        return ProbeResult.failed("스텁에 준비된 응답이 없다 — seeds의 code 절은 trace·uses·callers·resources만 받는다",
                                  source=source, clock=self._clock)

    def _seeded(self, table: dict, key: str, source: str, what: str) -> ProbeResult:
        if key not in table:
            return ProbeResult.failed(f"{key}: 스텁에 준비된 {what}이 없다", source=source, clock=self._clock)
        return ProbeResult.succeeded(str(table[key]), source=source, clock=self._clock)

    async def callers(self, name: str) -> ProbeResult:
        return self._seeded(self._callers, name, f"stub-code:callers {name}", "부르는 곳")

    async def uses(self, name: str, service: str = "") -> ProbeResult:
        return self._seeded(self._uses, name, f"stub-code:uses {name}", "쓰는 곳")

    async def resources(self, name: str, service: str = "", repo: str = "") -> ProbeResult:
        return self._seeded(self._resources, name, f"stub-code:resources {name}", "읽는 자원")

    async def filter_lines(self, name: str, *, fields: dict[str, str] | None = None, service: str = "", repo: str = "") -> FilterScan:
        return FilterScan(problem="스텁에 준비된 조회가 없다")

    async def services(self) -> ProbeResult:
        return self._none("stub-code:services")

    async def config(self, service: str, *, key: str | None = None) -> ProbeResult:
        return self._none(f"stub-code:config {service}")

    async def grep(self, patterns: list[str], service: str = "") -> ProbeResult:
        return self._none(f"stub-code:grep {patterns}")

    async def flow(self, name: str) -> ProbeResult:
        return self._none(f"stub-code:flow {name}")

    async def read(self, service: str, path: str, *, offset: int | None = None,
                   limit: int | None = None, line: int | None = None) -> ProbeResult:
        return self._none(f"stub-code:read {service} {path}")

    async def trace(self, endpoint: str) -> ProbeResult:
        return self._seeded(self._trace, endpoint, f"stub-code:trace {endpoint}", "사슬")


class StubRestProber(RestProberPort):
    def __init__(self, cfg, responses: dict[str, Any] | None = None, *, clock: Clock, snapshot: Snapshot | None = None):
        self._cfg = cfg
        self._responses = dict(responses or {})
        self._outside = _outside(snapshot, "rest")
        self._clock = _replay_clock(snapshot, clock)

    async def query(self, entry: str, params: dict) -> ProbeResult:
        source = f"stub-rest:{entry}"
        spec = self._cfg.entries.get(entry) if self._cfg else None
        if spec is None:
            return ProbeResult.failed(f"등재되지 않은 항목 — {entry}",
                                      source=source, clock=self._clock)
        params, problems, _ = prepare_params(spec, params)
        if problems:
            return ProbeResult.failed("파라미터 거부 — " + "; ".join(problems),
                                      source=source, clock=self._clock)
        if entry not in self._responses:
            if self._outside:
                return ProbeResult.failed(f"스냅샷 밖 — {entry}는 안 떴다", source=source, clock=self._clock)
            return ProbeResult.failed("HTTP 404 — 스텁에 준비된 응답이 없다",
                                      source=source, clock=self._clock)
        seeded = self._responses[entry]
        if isinstance(seeded, dict) and set(seeded) == {"captures"}:
            # 인자별 캡처(R3-5v ①) — REST는 인자마다 다른 사실이다. 같은 정규화를 거친 인자끼리 대조한다.
            asked = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
            for capture in seeded["captures"] or []:
                theirs, _, _ = prepare_params(spec, dict(capture.get("params") or {}))
                if json.dumps(theirs, sort_keys=True, ensure_ascii=False, default=str) == asked:
                    seeded = capture.get("response")
                    break
            else:
                return ProbeResult.failed(f"스냅샷 밖 — {entry}는 이 인자로 안 떴다({params})", source=source, clock=self._clock)
        elif self._outside:
            # 재생에서 인자 없는 응답은 안 쓴다 — 다른 라인을 물은 질의에 이 라인의 행이 가면 틀린 사실이다.
            return ProbeResult.failed(f"스냅샷 형식 — {entry}의 응답에 인자가 없다(captures 꼴로 떠야 인자를 대조한다)",
                                      source=source, clock=self._clock)
        return ProbeResult.succeeded(
            {"request": {"method": spec.method, "path": spec.path, "params": params},
             "status": 200, "response": seeded},
            source=source, clock=self._clock)
