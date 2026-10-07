# 12a 리뷰 4번 이후 계획 — R2-2a ~ R4

> **쓰임**: 라운드를 시작할 때 이 문서와 [handover.md](handover.md)를 먼저 읽고, 그 사이 알게 된 것(사내 측정·새 사실)으로
> 고칠 것이 있으면 **이 문서를 먼저 고친 뒤** 예고를 쓰고 "시작해"를 받아 진행한다. 라운드 하나 = 커밋 1~2개, 테스트 먼저
> (RED → GREEN), 스윕 케이스 추가, 전체 스위트 + 전체 스윕, 측정판 확인, 문서, 커밋·푸시. 사내 측정은
> [review-12a-4.md](review-12a-4.md)의 일곱 줄로 받는다. 배경과 진단 표는 [step-12a-verdict.md](step-12a-verdict.md)
> "사내 실측" 절, 설계 결정은 [decisions.md](decisions.md) ⑳.

## 공통 원칙 (라운드마다 다시 확인)

- **코드가 먼저 한다.** 강한 모델이 스스로 하는 것(중복 차단·열린 질문·정체 감지·도구 결과 요약·예산)을 약한 모델 대신
  하네스가 한다. LLM은 "왜"만 판단한다(규율 6).
- **잘라서 보여 주지 않는다 — 골라서 전부 보여 준다.** 증거는 전부 저장(id)하고 프롬프트에는 고른 부분을 통째로.
- **사내 어휘는 리포에 안 들어온다.** 계보 관례(metadata 필드 이름·use 플래그 키), 컬럼·컬렉션 이름은 `knowledge/topology`
  와 config의 자리표시자로. 측정판 픽스처는 **모양만** 옮긴다(실명 제거).
- **닫힌 스키마.** 새 도구는 인자가 닫혀 있고(규율 9), 계산 도구는 연산 몇 개지 식 평가가 아니다.
- **역할별 LLM.** 액션 턴은 빠른 모델·작은 `max_tokens`, 판정은 생각하는 모델. 읽는 곳이 없는 역할은 미리 두지 않는다.
- **벽은 먼저 잰다.** 숫자(8K·30초·5분)는 트레이스의 `응답 N초`·첫 조각 초·프롬프트 크기를 보고 정한다.

## 사내에서 받아야 할 것 (R3 전까지, 전부 자리표시자)

1. 요약 키 값의 JSON 모양 — `metadata` 아래 필드 이름(쓴 파이프라인·바로 위 토픽·상태·생성 시각이 어디 있나).
2. 파이프라인 config에서 use 플래그가 놓인 자리(키 경로)와 GBM 기본·사이트 덮어쓰기의 예.
3. 배지 API 응답 한 행의 필드 이름(identity 필드 둘, 수치 필드 셋).
4. 원천 키 한 행의 필드 이름(문제의 두 컬럼 포함)과 그 키가 어떤 모양으로 저장되는지(목록? 해시?).
5. 코드 줄 둘: ① batch가 kafka 명령의 target_resource로 키를 **쓰는** 줄, ② 동적 키를 읽는 함수의 읽기 줄(키 조립식 포함).
6. R2-1 재측정의 일곱 줄 + `응답 N초`·첫 조각 초(스트리밍이 벽을 넘는지).

## R2-2a — 엔진 P0 (지시서 1-3·1-4·7-1, 리뷰 C·D)

목적: 조사가 죽어도 "무엇을 봤고 무엇을 모르나"가 남고, 리드가 같은 자리를 맴돌지 않게.

| 항목 | 할 것 | 자리 | 테스트 |
|---|---|---|---|
| 1-3 | `stopped_by == llm_error`여도 `state.evidence`가 있으면 conclude를 **한 번** 시도(판정 LLM, 증거 예산 절반). 실패하면 degraded | `nodes.make_verdict_nodes.conclude`, `lead.conclude(compact=)`, `briefing.conclude_fields(budget)` | `test_verdict`: llm_error+증거 → 1회 묻고 성공/실패 두 길, 증거 0 → 안 묻는다 |
| 1-4 | degraded 서술을 사실로: `N라운드 뒤 중단(llm_error) · 읽기 M회(성공 K) · 증거 E건 · 마지막 가설 h-1[open] …` | `nodes._stopped_summary(state)` → `degraded()` 호출 셋 | 서술 문자열, `case investigate` 출력 |
| 7-1 | 끝까지 `pending`인 태스크를 `미실행 N개: t-4(이유: 우선순위 밀림) …`로 diagnose와 trace 요약에 | `diagnose.py`, `trace_digest` | `test_diagnose` |
| C | 중복 질의 거부 사유에 **기존 증거 id**: `이미 한 읽기 — 결과는 t-3.e1` | `nodes._accept_tasks(... done_ids=)` 호출부가 `{질의: 증거 id들}`을 넘김 | `test_nodes` 거부 문구 |
| D | `<열린 질문>` 블록(코드가 유지): 잘린 증거(`complete=False`)·실패한 읽기(사유)·못 붙은 시스템·거부된 중복·(R2-2b부터) 선언됐는데 없는 키 | `briefing.open_questions_block`, 프롬프트 템플릿 integrate·conclude에 `{open}` | `test_briefing`, 템플릿 슬롯 검사(`slots_in`) |

종료 판단: 대본(`case dryrun`)으로 llm_error 길이 판정을 내는 것을 보고, 측정판 한 판에서 `<열린 질문>`이 r1부터 실린다.
스윕 +8 안팎. 사내 측정은 R2-2c 뒤 한 번에.

## R2-2b — 증거 모양 (지시서 1-5·5-4·3-2·3-3·3-4·2-4, 앞선 R2 4~7)

목적: 리드가 대상 행과 대상 키를 **보게** 한다. 지금은 첫 행·첫 키에서 잘린다.

| 항목 | 할 것 | 자리 |
|---|---|---|
| 1-5 / 6 | `rest.query` 목록 응답: 항목당 한 줄(압축 JSON), 케이스 `target`·점검 `identity` 값이 든 행을 **앞에 통째로**, 나머지는 `외 N행(id로 연다)` | 증거 렌더(`runner_probe`/evidence 생성 자리), `CaseRecord.target`·`check.params.identity` |
| 5-4 | `redis.scan` 결과가 오면 코드가 **끝점이 읽는 키 목록**(`flow.traced_reads(kind="rediskey")`)과 대조해 `선언됐는데 없는 키 N: …`를 사실로(열린 질문에, 증거 요약에) | execute 뒤 후처리(`nodes.execute` 또는 `briefing`), `flow.py` |
| 3-2 | `code.read(service, path, offset?, limit?)` — 범위를 주면 그 범위 전부, 봉투에 `L120-L220 / 전체 410줄` | `actions.py`, `deployed_code.read`, `git_reader.show(lines=)` |
| 3-3 | `redis.get(key, path?)` — JSON 경로(`a.b[0].c`)로 고른 부분은 자르지 않는다(절대 상한만) | `actions.py`, `redis_reader`/stub, 증거 렌더 |
| 3-4 | 프롬프트 규칙 한 줄: "`⚠ 표본이 잘렸다`를 보면 projection·limit·path로 **좁혀** 다시 읽어라 — 같은 질의를 반복하지 마라" | `config/prompts/investigate-*.md` |
| 2-4 | integrate 예시는 지금 단계의 사다리 칸 **하나**만. frame의 접수 읽기는 그대로(설계) | `briefing.example_block` |
| 4 | `code.grep` 결과: 코드 파일 줄 먼저, 문서(`.md/.rst/.txt`) 줄 뒤, 상한은 줄 수 | `deployed_code.grep` |
| 5 | `code.config(service, key?)` — `key`면 그 부분 통째, 없이 부르면 상한 넘을 때 **키 지도**(키·크기) | `actions.py`, `deployed_code.config` |
| 7 | `<데이터 흐름>`: 접수 끝점 줄을 예산 밖에 고정, `_MAX_NAMES` 안에 증상·케이스 단어와 겹치는 이름 먼저 | `briefing.flow_block`, `flow.flow_text` |

종료 판단: 측정판 c-1형 픽스처(`tools/local_case.py`에 `pipeline-off` 변형 추가 — 요약 키 없음)에서 r1 브리핑에 대상 행과
`선언됐는데 없는 키`가 실린다. `code read --offset`·`redis.get path`는 CLI와 리드가 같은 조립. 스윕 +10 안팎.

## R2-2c — 운영·구조 (지시서 7-2·7-3, 리뷰 H)

| 항목 | 할 것 | 자리 |
|---|---|---|
| 7-2 | Windows 종료 시 `ConnectionResetError(10054)` 트레이스백: 어댑터의 httpx 클라이언트를 닫고(`adapters.close`에 LLM도), 그래도 남는 proactor 소음은 루프 예외 처리기에서 걸러 한 줄로 | `llm_chat_model`(close), `__main__`(`asyncio.run` 감싸기) |
| 7-3 | README에 NO_PROXY: 대상 도메인·LLM 게이트웨이를 넣는 이유와 `doctor`의 "프록시 경유 의심" 줄 | `README.md` |
| H | system/user 분리: `LlmPort.ask(prompt, *, system=None)`(이름 표면은 그대로), 템플릿을 `규칙`(고정)과 `상태`(가변)로 나눠 고정 부분은 system으로. 트레이스 파일은 둘 다 적고 요약은 user 길이를 센다 | 어댑터 넷, `lead.fill`, `config/prompts/*`, `_make_tracer`, `trace_digest` |

종료 판단: 측정판 한 판의 트레이스에 system/user가 나뉘어 남고 결과(판정)는 전과 같다. **여기서 사내 재측정** —
일곱 줄 + `응답 N초` + 프롬프트 크기. 이 결과로 R3·R4의 숫자를 정한다.

## R3 — 결정적 triage (지시서 4·5·6, 리뷰 J). 사내에서 받을 것 1~5가 전제

목적: LLM 없이 30초 안에 "첫 나쁜 홉"을 코드가 찾고, 리드는 거기서 "왜"만 판단한다. 8-4: 아무것도 모르는 대역 LLM으로도
첫 나쁜 홉이 맞아야 한다.

- **R3-1 그래프·config 확장**: (a) 쓰는 쪽 엣지 — 받은 코드 줄 모양으로 `flow` 동사표·`index` 자원 귀속 보강(kafka 명령
  target_resource는 `writes`), (b) 파이프라인 use 플래그 — 토폴로지에 `pipelines: {config_path, enable_key}`를 **패턴**으로
  선언하고 번들에 `pipelines.json`(GBM 기본 + 사이트 덮어쓰기 = `sites/<fct>.json`와 같은 방식), (c) 파이프라인 입력 키
  목록 — 인덱스의 함수별 자원에서, (d) 계보 관례 — `knowledge/topology`에 `lineage: {written_by, upstream_topic, status,
  created_at}` 필드 **경로**를 선언(리포엔 자리표시자). 동적 키 과대 귀속은 템플릿 매칭을 "선언된 키 목록과 교집합"으로.
- **R3-2 계보 걷기**: `src/application/triage.py` — 입구(대상 행) → 매핑된 키 → 홉마다 공통 점검(있나·비었나·신선한가·
  status·쓰는 쪽이 켜져 있나·핵심 필드 분포) → 상류(① metadata의 쓴 파이프라인·위 토픽 ② 그래프 쓰는 쪽 엣지 ③ config
  선언) → **처음 나쁜 홉에서 멈춤**(입력이 정상이면 그 홉이 원인, 입력도 나쁘면 한 홉 더). 출력 `TriageReport`(StrictModel):
  홉별 사실(증거 id)·첫 나쁜 홉·그 홉의 코드 경로·입력/출력 표본. 읽기는 전부 기존 어댑터(읽기 전용), 예산 30초.
  `case investigate` 시작 때 frame 앞에서 돌고 케이스 블록·열린 질문에 실린다. `patrol open`에서도 돌려 케이스에 저장
  (12b의 보고서가 쓴다).
- **R3-3 입구 매핑**: 역할 `mapping`(여기서 추가) — 작은 LLM 호출 한 번: 대상 identity + `code.trace`의 후보 키 + 쓰는 쪽
  코드의 group/title 리터럴 → 키 하나. 코드가 확인(존재·identity 일치), 커밋 기준 캐시(`output/graph/<gbm>/mappings/<fct>.json`),
  그래프를 다시 만들면 재확인. 확인 안 되면 `inconclusive` + 사람에게(13의 ask가 생기기 전엔 열린 질문으로).
- **R3-4 판정 기준**: `Verdict.first_bad_hop`, conclude 프롬프트에 "증상 → 첫 나쁜 홉 → 원인(꺼진 config/비정상 입력/코드)
  까지 사슬이 증거로 끊김 없이 이어지면 conclude", verify는 고리마다 인용 확인(12a의 `CauseLink` 그대로).
- **R3-5 픽스처**: `tools/local_case.py`에 `pipeline-off`(c-1형)·`source-blank`(c-2형) 변형 — 모양만. 벤치(`test_bench_scenarios`)
  에 triage 단독으로 첫 나쁜 홉이 맞는 것을 추가(8-4).

종료 판단: 두 픽스처에서 LLM 없이 첫 나쁜 홉이 맞고(30초 안), 아무것도 모르는 대역으로 판정 component가 맞는다(8-1·8-2).
사내 측정 한 판. 하지 말 것: 점검별 체크리스트·키 매핑 표(지시서 9).

## R4 — ReAct 루프·도구 경계 요약 (지시서 2·3·1-1·1-2, 리뷰 B·E·G·I)

목적: 한 턴 = 액션 하나, 프롬프트 ≤ 8K(코드가 강제), 케이스당 5분·호출당 60초(8-3).

- **R4-1 도구 경계 요약(B)**: 모든 도구 결과는 저장소에 통째(id), 리드가 받는 것은 도구별 정해진 요약(코드가 만든다 —
  LLM 요약 금지). `evidence.open(id, path?)` 액션으로 원문. 증거별 글자 상한(`evidence_chars`)은 요약으로 대체.
  `investigation.prompt_chars`(기본 8000)를 코드가 강제하고 넘으면 오래된 증거를 `id | 질의 | 한 줄`로 접는다(1-1).
- **R4-2 루프**: 그래프 노드 `act`(한 줄 thought + 액션 하나 또는 conclude) → `run` → `act` …; 상한은 액션 수와 총 시간 둘 다
  (어느 쪽이든 넘으면 그때까지의 증거로 conclude, 2-6); 가설 갱신은 conclude 직전 한 번; 정체 감지(E: 연속 두 액션이 새
  증거 0 → 힌트 하나 또는 conclude); 승급 사다리(G: 빠른 모델이 한 턴에서 스키마를 두 번 못 맞추면 그 턴만 생각 모델);
  재시도는 1회이고 프롬프트를 더 접어서(1-2), 시간 초과 뒤엔 안 한다(R1). `parallel_width`는 triage에서만. 대본(`dryrun`)
  형식 v2, `trace_digest`·`diagnose` 맞춤. `frame/select/execute/integrate`는 R4 끝까지 나란히 두고 벤치로 비교한 뒤 걷는다.
- **R4-3 도구**: 3-1 `code.grep` 전 레포 `레포:파일:줄 | 내용`, 개수 상한만; 3-5 `data.*` 닫힌 연산(`value_counts(field)`·
  `count(where)`·`filter(where)`)을 증거 id 위에서(표준 라이브러리, pandas 없음); 3-6 모든 결과에 id(이미 그렇다).
- **R4-4 실험(I)**: 네이티브 함수 호출 어댑터 옵션 — 게이트웨이가 받으면 JSON 수리 재시도가 준다. 사내 `llm check`로 확인 뒤 결정.

종료 판단: 측정판 두 픽스처에서 8-3 전부(5분·60초·8K·계약 위반 0·인용 id 전부 존재), 벤치 전 구간 통과, 사내 한 판.

## 되돌리기와 측정 규약

- 라운드마다 커밋 하나. 사내 측정이 나빠지면 그 커밋을 되돌리고 원인을 적는다.
- 사내에서 받는 것은 브리프의 일곱 줄 + `응답 N초`·첫 조각 초·프롬프트 크기. 더 필요하면 그때 한 줄씩.
- 숫자(8K·30초·5분·액션 15)는 R2-2c 재측정 뒤 이 문서에서 고친다 — 지금 값은 지시서의 제안이다.
