# 테스트

```bash
pytest                    # 전체
pytest tests/patrol/      # 계층 하나
pytest tests/test_bench_scenarios.py   # 벤치 회귀
```

`pytest.ini`가 `asyncio_mode = auto`를 켜 둔다 — `async def test_*`를
`@pytest.mark.asyncio` 없이 그대로 쓸 수 있다.

## 철학

- **실제 시스템도, 실제 LLM도 요구하지 않는다.** 모든 테스트는 로컬에서,
  네트워크 없이, 몇 초 안에 끝난다.
- **결정론이 최우선이다.** 시계는 항상 고정값(`clock=lambda: T`)을 주입하고,
  LLM 응답은 대본으로 미리 정한다. 같은 테스트는 언제 돌려도 같은 결과를 낸다.
- **가짜 구현도 진짜 계약을 진다.** 예를 들어 프로브는 스텁이든 실구현이든
  절대 raise하지 않는다는 계약을 스텁도 똑같이 지킨다 — 스텁이 편의를 위해
  계약을 느슨하게 지키면 그 계약을 검증하는 테스트 자체가 무의미해진다.
- **텍스트 매칭이 아니라 구조 매칭.** 보고서 텍스트나 프롬프트 문자열을
  정규식으로 채점하지 않는다. 채점 대상은 구조화 필드(`Verdict.verdict_type`,
  `CauseLink.component` 등)다 — 템플릿 문구를 바꿀 때마다 테스트가 깨지는
  것을 피하기 위해서다.

## 무엇으로 실제 시스템을 대신하는가

| 실제 것 | 테스트 대역 | 어디 |
|---|---|---|
| Redis/Mongo/Kafka/REST 어댑터 | `StubRedis`/`StubMongo`/`StubKafka`/`StubRest` | `src/infrastructure/stubs.py` |
| Mongo 케이스 저장소(계약 테스트만) | `mongomock` | `tests/infrastructure/test_mongo_store.py` |
| 리드/서브에이전트 LLM | `ScriptedLLM`(예약된 응답을 순서대로 재생) | `src/infrastructure/llm.py` |
| 서브에이전트 도구 호출 | `ToolFake` | `tests/application/test_subagents.py` |
| LangGraph 체크포인터 | `InMemorySaver` | langgraph 제공 |

`StubSeeds`(`src/infrastructure/factory.py`)로 스텁 어댑터에 미리 값을
심는다 — `StubSeeds(rest_responses={"/oee": {"oee": 512}})`처럼. 실제
시스템 없이 "이런 데이터가 관측됐다"를 재현하는 유일한 방법이다.

## 테스트 트리 구조

`tests/`는 `src/`와 계층별로 미러링돼 있다: `tests/domain/`,
`tests/config/`, `tests/knowledge/`, `tests/infrastructure/`,
`tests/patrol/`, `tests/application/`, `tests/presentation/`. 최상위에
`test_boot.py`(기동 검증 통합)와 `test_bench_scenarios.py`(E2E 벤치)가 있다.

## 벤치 시나리오(E2E 회귀)

`tests/test_bench_scenarios.py`는 스펙 부록 A의 두 간판 시나리오를
"점검 실행 → finding → 게이트(케이스 개설) → 조사 엔진 전 라운드 →
판정 → verify → 실제 발행 배선(데몬·chat·case resume과 동일한
`on_closed`)"까지 전 구간으로 돈다. 이 파일이 실제로 어떻게 조립하는지는
[docs/tutorial.md의 Part B](../docs/tutorial.md#part-b-오프라인으로-전체-조사를-재현하기)에
풀어서 설명해 뒀다 — 새 점검이나 새 조사 시나리오를 오프라인으로 검증하고
싶다면 이 파일의 패턴을 그대로 베끼는 게 가장 빠르다.

파일 docstring에 있는 두 가지 함정에 특히 주의:

- 서브에이전트가 응답에 적은 evidence id는 실제 인용을 결정하지 않는다 —
  `run_subagent`이 도구가 실제로 만든 id로 통째로 교체하기 때문
  (`CLAUDE.md`의 "LLM이 인용한 evidence id를 신뢰하지 않는다" 참고). 실제로
  맞아야 하는 건 `Verdict.root_cause.evidence_ids`뿐이고, `InMemoryCaseStore`는
  케이스당 증거 id를 1부터 순번(`ev-1`, `ev-2`, ...)으로 매기므로 이 번호를
  미리 예측해서 시나리오에 적어 둔다.
- 두 시나리오 모두 자기만의 `InMemoryCaseRepository`를 쓰므로 첫 케이스
  id가 둘 다 `"c-1"`이 된다 — 출력 디렉터리를 시나리오별로 나누지 않으면
  나중에 도는 시나리오가 먼저 도는 시나리오의 보고서 파일을 덮어쓴다.

## 평가 모드(실 LLM, 참고용)

벤치 시나리오는 회귀 모드(스텁+스크립트)로만 CI에서 돈다. 조사 품질 자체를
사람이 판단하고 싶다면(실 LLM 응답이 실제로 말이 되는 가설·판정을 내는지),
같은 시나리오 조립부(사이트·시드·finding)는 그대로 두고 `lead_llm`/
`subagent_llm`/judge llm만 `build_chat_model`로 바꿔 별도 스크립트에서 수동
실행한다 — CI에는 포함하지 않는다(비결정론이라 회귀로 쓸 수 없다).

## 새 기능을 테스트할 때

- RED(실패가 재현되는 테스트)를 먼저 쓰고, GREEN(픽스 후 통과)을 확인하라.
  둘 다 실제로 돌려서 확인하지 않으면 "고쳤다고 생각했지만 안 고쳐졌다"는
  일이 실제로 있었다.
- 무raise 계약이 있는 함수라면, 예외를 던지는 대신 상태로 흡수하는지
  검증하는 테스트를 반드시 포함하라(`monkeypatch`로 내부 호출이 예외를
  던지게 만들고, 반환값이 `status="error"` 등으로 흡수되는지 확인).
- 시계가 필요한 테스트는 항상 `clock=lambda: T`처럼 고정값을 주입하라.
