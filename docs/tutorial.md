# 튜토리얼 — 새 순찰 점검 하나를 끝까지 따라가기

이 문서는 `config.example`/`knowledge.example`(리포에 실제로 들어 있고 동작이
검증된 예시 트리)를 기준으로, 점검을 하나 추가하고 그 점검이 이상을 잡아 케이스가
열리고 보고서가 나오기까지의 전체 경로를 따라간다. 먼저
[README.md의 5분 빠른 시작](../README.md#5분-빠른-시작)을 끝냈다고 가정한다.

## Part A. config만으로 점검 추가하기

`config.example/gbm/mx.json`의 `patrol.checks`에는 이미 `api.oee_range`
하나가 있다. 같은 파일에 두 번째 점검을 추가해 보자 — MongoDB `twin_state`
컬렉션이 너무 오래 갱신되지 않으면 잡아내는 신선도(freshness) 점검이다.

```json
{
  "target": { "...": "기존 내용 그대로" },
  "patrol": {
    "checks": {
      "api.oee_range": { "...": "기존 내용 그대로" },
      "twin_state.freshness": {
        "judge": "rule",
        "schedule": { "interval": "1m" },
        "target": "mongo:twin_state",
        "params": { "rule": "freshness", "field": "ts", "max_age_s": 300 }
      }
    }
  },
  "knowledge": { "root": "knowledge.example" }
}
```

이제 검증해 본다:

```bash
python -m src knowledge validate --config-root config.example --repo-root .
```

`OK`가 나오면 이 점검의 `target`(`mongo:twin_state`)이 토폴로지에서 해석되고,
프로브(`mongo_recent` — target kind가 `mongo`라 기본 선택됨)도 정상이라는
뜻이다. 실패한다면 각 에러 줄의 `[사이트]` 접두어와 메시지를 그대로 따라가면
된다 — 기동 검증은 문제를 전부 모아서 한 번에 보여준다([config 레퍼런스](config-reference.md#기동-검증-11개-항목-srcbootpy) 참고).

```bash
python -m src patrol run --for-seconds 5 --config-root config.example --repo-root .
```

이 시점에서 중요한 사실 하나: `config.example`은 `target.adapters: "stub"`을
쓰고, CLI로 띄운 순찰은 스텁에 **아무 데이터도 미리 채워 넣지 않은 채로** 점검을
돈다. 그래서 두 점검 모두 "값을 하나도 못 찾았다"는 의미로 처리되고(비어 있는
스텁 응답), 진짜로 이상 케이스를 여는 것은 보지 못한다 — 이건 버그가 아니라
스텁 어댑터에 아직 아무 데이터도 넣지 않았기 때문이다. 실제 시스템에 붙이는
방법은 [docs/going-live.md](going-live.md)를 보라.

그럼 "점검이 실제로 이상을 잡아서 케이스가 열리는 전체 과정"을 어떻게
확인할 수 있을까? 두 가지 방법이 있다:

1. 실제 대상 시스템에 `adapters: "real"`로 붙인다(운영 환경, going-live.md).
2. **오프라인으로 결정론 재현**한다 — Part B.

## Part B. 오프라인으로 전체 조사를 재현하기

v1은 스텁 어댑터에 값을 미리 심어주고(`StubSeeds`), LLM 응답도 미리 대본으로
써 두는(`ScriptedLLM`) 방식으로 "점검 → finding → 케이스 개설 → 조사 →
판정 → 보고서 발행"을 **실제 시스템도, 실제 LLM도 없이** 결정론적으로 끝까지
재현할 수 있다. 이게 바로 리포에 있는 벤치 시나리오
([tests/test_bench_scenarios.py](../tests/test_bench_scenarios.py))가 하는
일이고, 이 문서 뒷부분에 인용한 [`output/bench-a1/c-1.md`](#최종-산출물-예시)가
그 산출물이다. 새 점검을 붙였을 때 리드/서브에이전트 프롬프트가 실제로 어떤
가설·판정을 내는지 확인하고 싶다면, 이 패턴을 그대로 베껴서 새 시나리오를
하나 추가하는 게 가장 빠르다.

핵심 조립 순서(자세한 것은 파일을 직접 열어서 볼 것):

```python
# 1. 스텁에 "이상 있는" 데이터를 심는다
adapters = build_adapters(site_cfg, topology, clock=clock,
                          stub_seeds=StubSeeds(rest_responses={"/oee": {"oee": 512}}))

# 2. 점검을 한 번 실행해 finding을 얻는다 (스냅샷은 이 시점에 이미 스크래치 케이스에 박제됨)
outcome = await run_check("mx", "gumi", "api.oee_range", check_cfg,
                          adapters=adapters, store=store, clock=clock)
assert outcome.status == "finding"

# 3. 게이트에 넘겨 정식 케이스를 연다
admitted = admit_finding(outcome.finding, repo=repo, store=store, clock=clock)
assert admitted.action == "opened"

# 4. 조사 엔진을 리드/서브에이전트용 ScriptedLLM으로 끝까지 돌린다
#    (frame의 가설·계획 → integrate의 continue/ask/conclude → conclude의 Verdict,
#     이 순서대로 JSON 문자열 응답을 미리 큐잉해 둔다)
worker = InvestigationWorker(CaseQueue(), repo=repo, store=store,
                             deps_for_site=lambda g, f: deps, checkpointer=InMemorySaver(),
                             clock=clock, owner="bench",
                             # 나머지 필수 인자(max_concurrent, lease_ttl_s, ledger,
                             # knowledge_digests_for_site)와 on_event/on_closed 배선은
                             # tests/test_bench_scenarios.py의 _publish_daemon을 그대로 베낄 것
                             max_concurrent=1, lease_ttl_s=900, ledger=ledger,
                             knowledge_digests_for_site=lambda g, f: {})
result = await worker.run_once(admitted.case_id)
assert result == "closed"
```

`ScriptedLLM`에 큐잉하는 응답은 각 노드가 기대하는 구조화 출력(pydantic
스키마)을 그대로 만족하는 JSON 문자열이어야 한다 — `nodes.py`의
`FrameOutput`/`IntegrateOutput`, `domain/case.py`의 `Verdict`를 참고하라.
서브에이전트가 실제로 어떤 도구를 부르는지는 `ToolFake`(같은 파일이 import하는
`tests/application/test_subagents.py`)로 흉내 낸다.

이 패턴으로 새 점검의 시나리오를 하나 추가하면, `pytest tests/test_bench_scenarios.py`로
매번 몇 초 안에 전체 흐름(점검 → finding → 케이스 → 조사 라운드 → 판정 →
검증 → 보고서)이 여전히 원하는 결과를 내는지 회귀로 확인할 수 있다.

## 최종 산출물 예시

아래는 `tests/test_bench_scenarios.py`의 첫 번째 시나리오(OEE가 512%로
관측된 케이스)가 실제로 만들어낸 보고서다(`output/bench-a1/c-1.md`, 보고서
렌더링은 `src/presentation/report.py`). `data_prober`가 Mongo에서
`twin_state`(oee=5.12, planned_time=75)를, Redis에서 `plan:6:today`(어제
계획값)를 읽어 대조한 결과 line 7의 오늘 계획 키(`plan:7:today`)가 비어 있어
집계기가 옛 계획값으로 폴백했다는 게 원인으로 드러난다 — "원천은 정상인데
파생값만 이상하다"는 이 시스템의 핵심 시나리오를 그대로 보여준다.

```markdown
# 케이스 c-1 보고서

작성 시각: 2026-09-03T08:00:00+00:00

## 1. 요약
- 케이스 id: c-1
- 스코프: mx/gumi
- 개설 경로: patrol
- 증상: 범위 초과 — body.oee=512.0 > max(100.0)
- T0: 2026-09-03T08:00:00+00:00
- 판정: stale_data — plan-sync가 line 7의 plan:7:today 키를 못 써 aggregator가 옛 계획값을 폴백으로 썼다 — 분모가 축소돼 OEE가 폭등했다.
- 신뢰도: high
- 태스크 에러율: 0/2

## 2. 판정
- 근본 원인: plan-sync (증거: ev-2, ev-3)
- 기여 요인:
  없음
- caveat:
  없음

## 3. 조치 권고
1. plan:7:today 키 재생성
2. plan-sync 실패 로그 확인(스코프 밖)

## 4. 증거
| id | 출처 | as_of | 완전성 | effective_as_of | 요지 |
|---|---|---|---|---|---|
| ev-1 | rest:/oee | 2026-09-03T08:00:00+00:00 | 완전 | - | {'status_code': 200, 'body': {'oee': 512}} |
| ev-2 | mongo:twin_state | 2026-09-03T08:00:00+00:00 | 완전 | - | [{'line': 7, 'oee': 5.12, 'planned_time': 75}] |
| ev-3 | redis:plan:6:today | 2026-09-03T08:00:00+00:00 | 완전 | - | '480' |

## 5. 조사 경위
- 라운드: 2
- 태스크 현황:

| id | 역할 | status | 비고 |
|---|---|---|---|
| t-1 | data_prober | ok |  |
| t-2 | data_prober | ok |  |

- 기각된 가설:
  없음
- 검증 문제:
  없음
- QA 로그:
  없음
```

보고서 5절 구조(요약/판정/조치 권고/증거/조사 경위)는 모든 케이스에서
고정이다 — 자세한 필드 의미는 [docs/glossary.md](glossary.md)를 보라.

## 다음 단계

- 새 사이트를 통째로 추가하거나, `chat`으로 직접 조사를 시작해 보려면
  [docs/howto.md](howto.md).
- 스텁을 실제 Redis/Mongo/Kafka/REST/LLM에 연결하려면
  [docs/going-live.md](going-live.md).
- 모든 config 키의 의미는 [docs/config-reference.md](config-reference.md).
