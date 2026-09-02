"""서브에이전트 3종 — create_agent 유계 ReAct 루프 (스펙 §2.4 확정).

- 자유가 값하는 안쪽 루프는 라이브러리에, 규율은 이 모듈의 경계에:
  도구는 결과를 Store에 넣고 증거 id를 돌려주며(인용의 원천), 어떤 실패도
  raise 대신 error 보고로 변환된다(superstep 보호).
- LLM이 최종 JSON에 적는 evidence_ids는 echo일 뿐 신뢰하지 않는다 — 도구가
  실제로 store.put_evidence를 호출해 만든 id(created)만 보고에 남는다.
"""
import json
from datetime import datetime

from langchain_core.tools import tool

from src.application.schemas import SubagentReport, parse_structured
from src.domain.case import PlanTask
from src.infrastructure.code_repo import CodeRepoError

_PROMPTS = {
    "data_prober": (
        "너는 데이터 프로버다. 주어진 도구만으로 대상 시스템의 데이터를 조회해 조사 목표에 답하라.\n"
        "도구 결과에 [증거 ev-N]이 표시된다 — 마지막에 반드시 JSON 하나로만 보고하라:\n"
        '{"status": "ok"|"error", "summary": "발견 요약(한국어)", "evidence_ids": ["ev-N", ...]}\n'
        "추측 금지. 도구가 준 결과만 근거로 삼고, 실패했으면 status를 error로 하라."),
    "code_tracer": (
        "너는 코드 추적자다. 주어진 도구로 대상 서비스의 코드를 읽고 변환 로직을 규명하라.\n"
        "마지막에 반드시 JSON 하나로만 보고하라: "
        '{"status": ..., "summary": "로직 명세(한국어)", "evidence_ids": [...]}'),
    "recompute_verifier": (
        "너는 재계산 검증자다. get_evidence로 입력 증거를 읽고, 로직 명세대로 기대값을 도출해\n"
        "실제값과 대조하라. 마지막에 반드시 JSON 하나로만 보고하라: "
        '{"status": ..., "summary": "샘플별 일치/불일치(한국어)", "evidence_ids": [...]}'),
}


def _evidence_line(evidence_id, summary, envelope):
    eff = envelope.effective_as_of.isoformat() if envelope.effective_as_of else "-"
    return f"[증거 {evidence_id}] {summary} (complete={envelope.complete}, effective_as_of={eff})"


def _code_evidence_line(evidence_id, summary):
    # 코드 리더는 ProbeResult/Envelope가 없다(유일한 sync 포트) — complete·
    # effective_as_of를 논할 대상 자체가 없어 간략한 형식을 쓴다.
    return f"[증거 {evidence_id}] {summary}"


def make_tools(role, *, adapters, store, case_id):
    tools = []
    created: list[str] = []     # 도구가 실제로 만든 증거 id — LLM echo와 분리해 추적한다

    def _put(source, body, envelope=None):
        if envelope is not None:
            evidence_id = store.put_evidence(case_id, source, body,
                                             as_of=envelope.observed_at,
                                             complete=envelope.complete,
                                             effective_as_of=envelope.effective_as_of)
        else:
            evidence_id = store.put_evidence(case_id, source, body)
        created.append(evidence_id)
        return evidence_id

    if role == "data_prober" and adapters.mongo is not None:
        @tool
        async def mongo_find(collection: str, filter_json: str, limit: int = 20) -> str:
            """Mongo 컬렉션을 필터로 조회한다. filter_json은 JSON 문자열."""
            try:
                filter = json.loads(filter_json)
            except json.JSONDecodeError as exc:
                return f"[오류] filter_json 파싱 실패 — {exc}"
            result = await adapters.mongo.find(collection, filter, limit=limit)
            if result.status == "error":
                return f"[오류] {result.error}"
            evidence_id = _put(f"mongo:{collection}", result.data, result.envelope)
            return _evidence_line(evidence_id, f"{collection} {len(result.data)}건", result.envelope)
        tools.append(mongo_find)

        @tool
        async def mongo_count(collection: str, filter_json: str) -> str:
            """Mongo 컬렉션의 문서 수를 필터로 센다. filter_json은 JSON 문자열."""
            try:
                filter = json.loads(filter_json)
            except json.JSONDecodeError as exc:
                return f"[오류] filter_json 파싱 실패 — {exc}"
            result = await adapters.mongo.count(collection, filter)
            if result.status == "error":
                return f"[오류] {result.error}"
            evidence_id = _put(f"mongo:{collection}", result.data, result.envelope)
            return _evidence_line(evidence_id, f"{collection} {result.data}건", result.envelope)
        tools.append(mongo_count)

    if role == "data_prober" and adapters.redis is not None:
        @tool
        async def redis_get(key: str) -> str:
            """Redis 키 하나를 조회한다(string은 값, hash는 필드 dict)."""
            result = await adapters.redis.get(key)
            if result.status == "error":
                return f"[오류] {result.error}"
            evidence_id = _put(f"redis:{key}", result.data, result.envelope)
            return _evidence_line(evidence_id, f"{key} 조회", result.envelope)
        tools.append(redis_get)

        @tool
        async def redis_scan(pattern: str) -> str:
            """glob 패턴에 매칭하는 Redis 키 목록을 조회한다(예: 'twin:*')."""
            result = await adapters.redis.scan(pattern)
            if result.status == "error":
                return f"[오류] {result.error}"
            evidence_id = _put(f"redis-scan:{pattern}", result.data, result.envelope)
            return _evidence_line(evidence_id, f"{pattern} {len(result.data)}건", result.envelope)
        tools.append(redis_scan)

        @tool
        async def redis_ttl(key: str) -> str:
            """Redis 키의 남은 TTL(초)을 조회한다(-1: 무제한, -2: 없음)."""
            result = await adapters.redis.ttl(key)
            if result.status == "error":
                return f"[오류] {result.error}"
            evidence_id = _put(f"redis-ttl:{key}", result.data, result.envelope)
            return _evidence_line(evidence_id, f"{key} ttl={result.data}", result.envelope)
        tools.append(redis_ttl)

    if role == "data_prober" and adapters.kafka is not None:
        @tool
        async def kafka_read(topic: str, start_iso: str, end_iso: str) -> str:
            """Kafka 토픽을 [start_iso, end_iso) 구간으로 읽는다(둘 다 ISO 8601 문자열)."""
            try:
                start = datetime.fromisoformat(start_iso)
                end = datetime.fromisoformat(end_iso)
            except ValueError as exc:
                return f"[오류] 시각 파싱 실패 — {exc}"
            result = await adapters.kafka.read(topic, start=start, end=end)
            if result.status == "error":
                return f"[오류] {result.error}"
            evidence_id = _put(f"kafka:{topic}", result.data, result.envelope)
            return _evidence_line(evidence_id, f"{topic} {len(result.data)}건", result.envelope)
        tools.append(kafka_read)

        @tool
        async def kafka_group_offsets(group: str) -> str:
            """Kafka 컨슈머 그룹의 파티션별 커밋 오프셋·lag를 조회한다."""
            result = await adapters.kafka.group_offsets(group)
            if result.status == "error":
                return f"[오류] {result.error}"
            evidence_id = _put(f"kafka-offsets:{group}", result.data, result.envelope)
            return _evidence_line(evidence_id, f"{group} 오프셋 조회", result.envelope)
        tools.append(kafka_group_offsets)

    if role == "data_prober" and adapters.rest is not None:
        @tool
        async def rest_get(endpoint: str) -> str:
            """REST 끝점을 GET으로 조회한다(토폴로지에 등록된 끝점만 허용)."""
            result = await adapters.rest.get(endpoint)
            if result.status == "error":
                return f"[오류] {result.error}"
            evidence_id = _put(f"rest:{endpoint}", result.data, result.envelope)
            status_code = result.data.get("status_code") if isinstance(result.data, dict) else "-"
            return _evidence_line(evidence_id, f"{endpoint} status={status_code}", result.envelope)
        tools.append(rest_get)

    if role in ("code_tracer", "recompute_verifier") and adapters.code is not None:
        @tool
        def code_show(repo: str, commit: str, path: str) -> str:
            """레포의 특정 커밋에서 파일 내용을 읽는다."""
            try:
                body = adapters.code.show(repo, commit, path)
            except CodeRepoError as exc:
                return f"[오류] {exc}"
            evidence_id = _put(f"code:{repo}@{commit}:{path}", body)
            return _code_evidence_line(evidence_id, f"{repo}@{commit[:7]}:{path} ({len(body.splitlines())}줄)")
        tools.append(code_show)

        @tool
        def code_grep(repo: str, commit: str, pattern: str) -> str:
            """레포의 특정 커밋에서 패턴을 grep한다."""
            try:
                lines = adapters.code.grep(repo, commit, pattern)
            except CodeRepoError as exc:
                return f"[오류] {exc}"
            evidence_id = _put(f"code-grep:{repo}@{commit}:{pattern}", lines)
            return _code_evidence_line(evidence_id, f"{pattern!r} {len(lines)}건 매치")
        tools.append(code_grep)

        @tool
        def code_head(repo: str) -> str:
            """레포의 HEAD 커밋 해시를 조회한다."""
            try:
                commit = adapters.code.head(repo)
            except CodeRepoError as exc:
                return f"[오류] {exc}"
            evidence_id = _put(f"code-head:{repo}", commit)
            return _code_evidence_line(evidence_id, f"{repo} HEAD={commit[:7]}")
        tools.append(code_head)

    # 입력 증거 재독은 모든 역할에 열려 있다(M4) — code_tracer도 앞선 프로브의
    # 증거를 참조해야 할 수 있고, 이 도구 자체는 새 증거를 만들지 않으니 created에
    # 넣지 않는다.
    @tool
    def get_evidence(evidence_id: str) -> str:
        """케이스 Store에 저장된 증거 본문을 JSON 문자열로 재독한다(입력 증거 읽기용)."""
        try:
            body = store.get_evidence(case_id, evidence_id)
        except KeyError:
            return f"[오류] 없는 증거 id — {evidence_id}"
        return json.dumps(body, ensure_ascii=False, default=str)
    tools.append(get_evidence)

    return tools, created


async def run_subagent(task: PlanTask, *, adapters, store, llm, budget, case_id) -> SubagentReport:
    from langchain.agents import create_agent   # 지연 import

    tools, created = make_tools(task.role, adapters=adapters, store=store, case_id=case_id)
    goal = task.goal
    if task.input_evidence_ids:
        goal += f"\n입력 증거 id: {', '.join(task.input_evidence_ids)}"
    try:
        agent = create_agent(model=llm, tools=tools, system_prompt=_PROMPTS[task.role])
        result = await agent.ainvoke({"messages": [("user", goal)]},
                                     config={"recursion_limit": budget})
        final = result["messages"][-1].text   # .content 대신 — 문자열을 보장한다(I2)
        report, err = parse_structured(final, SubagentReport)
        if report is None:
            return SubagentReport(status="error", summary="",
                                  error=f"보고 JSON 파싱 실패 — {err}")
        # LLM이 적은 evidence_ids는 신뢰하지 않는다 — 도구가 실제로 만든 id(created)로
        # 통째로 교체한다: 지어낸 인용은 사라지고, 인용을 빠뜨린 실측 id는 보충된다.
        report.evidence_ids = created
        return report
    except Exception as exc:   # 예산 초과(GraphRecursionError) 포함 — raise 금지 계약
        return SubagentReport(status="error", summary="",
                              error=f"서브에이전트 실행 실패 — {type(exc).__name__}: {exc}")
