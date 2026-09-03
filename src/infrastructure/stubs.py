"""개발·테스트용 in-memory 스텁 — 전작 패턴. 봉투·읽기 전용 규칙은 실구현과 동일하다.

스텁이 규칙(query_rules)을 실구현과 공유하므로, 스텁으로 도는 테스트가
"위험 연산 거부·절단 마킹·as_of 폴백 명시"라는 계약 자체를 검증한다.
"""
import fnmatch
import re
from datetime import datetime

from src.domain.envelope import Envelope, ProbeResult
from src.domain.ports import (KafkaInspectorPort, MongoReaderPort,
                              RedisReaderPort, RestProberPort)
from src.infrastructure.query_rules import (aggregate_problems, endpoint_allowed,
                                            entry_call_problems,
                                            filter_problems)


def _ok(data, envelope):
    return ProbeResult(status="ok", envelope=envelope, data=data)


def _err(msg, clock):
    return ProbeResult(status="error", envelope=Envelope(observed_at=clock()), error=msg)


class StubRedis(RedisReaderPort):
    def __init__(self, data, ttls=None, *, max_rows, clock):
        self._data, self._ttls = data, ttls or {}
        self._max_rows, self._clock = max_rows, clock

    async def get(self, key):
        return _ok(self._data.get(key), Envelope(observed_at=self._clock()))

    async def scan(self, pattern):
        keys = sorted(k for k in self._data if fnmatch.fnmatch(k, pattern))
        truncated = len(keys) > self._max_rows
        env = Envelope(observed_at=self._clock(), complete=not truncated,
                       truncated_reason="max_rows" if truncated else None)
        return _ok(keys[: self._max_rows], env)

    async def ttl(self, key):
        ttl = self._ttls.get(key, -1 if key in self._data else -2)
        return _ok(ttl, Envelope(observed_at=self._clock()))


def _match(doc, filter):
    for field, cond in filter.items():
        if field in ("$and", "$or"):
            results = [_match(doc, c) for c in cond]
            if field == "$and" and not all(results):
                return False
            if field == "$or" and not any(results):
                return False
        elif isinstance(cond, dict):
            value = doc.get(field)
            for op, rhs in cond.items():
                if op == "$eq" and not value == rhs: return False
                if op == "$ne" and not value != rhs: return False
                if op == "$gt" and not (value is not None and value > rhs): return False
                if op == "$gte" and not (value is not None and value >= rhs): return False
                if op == "$lt" and not (value is not None and value < rhs): return False
                if op == "$lte" and not (value is not None and value <= rhs): return False
                if op == "$in" and value not in rhs: return False
                if op == "$nin" and value in rhs: return False
                if op == "$exists" and (field in doc) != bool(rhs): return False
                if op == "$regex" and not (isinstance(value, str) and re.search(rhs, value)): return False
                # $options는 query_rules의 allowlist에 있지만(표준 $regex 짝) 여기서는
                # 평가하지 않는다 — re.search에 플래그를 안 넘겨도 매치 결과 자체는
                # 안전 쪽(더 좁게 매치)이라 no-raise 계약을 깨지 않는다.
        elif doc.get(field) != cond:
            return False
    return True


class StubMongo(MongoReaderPort):
    def __init__(self, collections, *, max_rows, clock):
        self._cols, self._max_rows, self._clock = collections, max_rows, clock

    async def find(self, collection, filter, *, sort=None, limit=None):
        problems = filter_problems(filter)
        if problems:
            return _err("; ".join(problems), self._clock)
        try:
            docs = [d for d in self._cols.get(collection, []) if _match(d, filter)]
            # sort도 try 안에서: 정렬 필드가 문서마다 없거나 타입이 섞이면 None과
            # 다른 타입 비교로 TypeError가 날 수 있다 — no-raise 계약(§5.4)을 지키려면
            # 여기서 잡아 error 결과로 돌려야 한다.
            if sort:
                for field, direction in reversed(sort):
                    docs.sort(key=lambda d: d.get(field), reverse=direction < 0)
        except Exception as exc:
            return _err(f"filter 구조 오류 — {type(exc).__name__}: {exc}", self._clock)
        cap = min(limit, self._max_rows) if limit else self._max_rows
        truncated = len(docs) > cap
        env = Envelope(observed_at=self._clock(), complete=not truncated,
                       truncated_reason="max_rows" if truncated else None)
        return _ok(docs[:cap], env)

    async def count(self, collection, filter):
        problems = filter_problems(filter)
        if problems:
            return _err("; ".join(problems), self._clock)
        try:
            n = sum(1 for d in self._cols.get(collection, []) if _match(d, filter))
        except Exception as exc:
            return _err(f"filter 구조 오류 — {type(exc).__name__}: {exc}", self._clock)
        return _ok(n, Envelope(observed_at=self._clock()))

    async def aggregate(self, collection, pipeline):
        problems = aggregate_problems(pipeline)
        if problems:
            return _err("; ".join(problems), self._clock)
        docs = list(self._cols.get(collection, []))
        for stage in pipeline:                      # 최소 평가: $match·$count만
            if "$match" in stage:
                try:
                    docs = [d for d in docs if _match(d, stage["$match"])]
                except Exception as exc:
                    return _err(f"filter 구조 오류 — {type(exc).__name__}: {exc}", self._clock)
            elif "$count" in stage:
                docs = [{stage["$count"]: len(docs)}]
        truncated = len(docs) > self._max_rows
        env = Envelope(observed_at=self._clock(), complete=not truncated,
                       truncated_reason="max_rows" if truncated else None)
        return _ok(docs[: self._max_rows], env)


class StubKafka(KafkaInspectorPort):
    def __init__(self, messages, offsets=None, *, max_rows, clock):
        self._msgs, self._offsets = messages, offsets or {}
        self._max_rows, self._clock = max_rows, clock

    async def group_offsets(self, group):
        return _ok(self._offsets.get(group, {}), Envelope(observed_at=self._clock()))

    async def read(self, topic, *, start, end):
        msgs = sorted(self._msgs.get(topic, []), key=lambda m: m["ts"])
        effective = None
        if msgs and start < msgs[0]["ts"]:          # 보존 밖 → earliest 폴백 명시
            effective = msgs[0]["ts"]
        effective_start = effective or start
        window = [m for m in msgs if effective_start <= m["ts"] < end]
        truncated = len(window) > self._max_rows
        env = Envelope(observed_at=self._clock(), complete=not truncated,
                       truncated_reason="max_rows" if truncated else None,
                       requested_as_of=start, effective_as_of=effective)
        return _ok(window[: self._max_rows], env)


class StubRest(RestProberPort):
    def __init__(self, responses, allowed, entries=None, *, clock):
        self._responses, self._allowed, self._clock = responses, allowed, clock
        self._entries = entries or {}

    async def query(self, entry, params):
        # RealRest와 **같은 거부 규칙**을 쓴다(entry_call_problems 공유). 테스트가
        # 전부 스텁이므로 여기서 느슨해지면 그 계약을 검증하는 테스트가 무의미해진다.
        entry_spec = self._entries.get(entry)
        if entry_spec is None:
            return _err(f"항목 {entry!r}는 등재돼 있지 않다", self._clock)
        problems = entry_call_problems(entry_spec, params)
        if problems:
            return _err("; ".join(problems), self._clock)
        key = f"{entry_spec.method} {entry_spec.path}"
        if key not in self._responses:
            return _err("404: 스텁에 등록되지 않은 항목", self._clock)
        data = {"status_code": 200, "body": self._responses[key],
                "request": {"method": entry_spec.method, "path": entry_spec.path,
                            "params": params}}
        return _ok(data, Envelope(observed_at=self._clock()))

    async def get(self, endpoint):
        if not endpoint_allowed(endpoint, self._allowed):
            return _err(f"끝점 {endpoint!r}는 토폴로지에 등록돼 있지 않다", self._clock)
        if endpoint not in self._responses:
            return _err("404: 스텁에 등록되지 않은 끝점", self._clock)
        # RealRest와 동형 — {"status_code", "body"} 구조로 반환한다(status_code를
        # 폐기하지 않는다). 스텁은 등록된 성공 응답만 흉내 내므로 status_code는
        # 항상 200으로 고정한다.
        data = {"status_code": 200, "body": self._responses[endpoint]}
        return _ok(data, Envelope(observed_at=self._clock()))
