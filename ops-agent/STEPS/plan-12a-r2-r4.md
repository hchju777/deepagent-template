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

## R2-2a — 엔진 P0 (지시서 1-3·1-4·7-1, 리뷰 C·D) ✅ 10-07

했다 — 12a 문서 "R2-2a" 절. 계획과 다른 점 하나: llm_error 뒤 판정은 평소 프롬프트로 한 번 묻는다(예산 절반은
R4-1의 프롬프트 상한으로 미룸). 측정판 확인은 R2-2b와 함께(`pipeline-off` 변형이 생기면 열린 질문이 실리는 판을 본다).

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

목적: 리드가 대상 행과 대상 키를 **보게** 한다. 지금은 첫 행·첫 키에서 잘린다. 둘로 나눠 간다 — **-1 도구**(리드가 좁혀
낼 수 있는 인자와 그 결과를 통째로 싣는 것)와 **-2 증거·브리핑**(코드가 알아서 앞세우고 대조하는 것). -1이 먼저인 이유:
-2의 "앞에 통째로"도 좁힌 결과를 안 자르는 실행기 규칙(`actions.narrowed`) 위에 선다.

### R2-2b-1 — 도구 ✅ (10-07)

| 항목 | 한 것 | 자리 |
|---|---|---|
| 3-2 | `code.read(service, path, offset?, limit?)` — 범위를 주면 통째로 받아 그 줄들을 자르지 않는다. source에 `L2-L2 / 전체 3줄`. `offset`만이면 기본 400줄(봉투가 말한다). 0 이하·파일 끝 너머는 소켓 전에 error | `actions.py`, `deployed_code.read`(`show(whole=True)` + 슬라이스 — `git_reader.show(lines=)`는 안 만들었다: 리더에 또 하나의 자르기를 두지 않는다) |
| 3-3 | `redis.get(key, path?)` — `domain/jsonpath.select`(점·`[n]`, 문자열이면 JSON으로 풂, 실패하면 **있는 키·목록 길이**를 말함). 실제 리더·스텁 같은 계약: 자리 없음 error, 키 없음 None | `jsonpath.py`, `redis_reader`, `stubs`, `ports` |
| 5 | `code.config(service, key?)` — `key`면 그 자리 통째. 없이 부르고 합친 설정이 2400자를 넘으면 `{"_키_지도": {경로(두 단계) → 무엇이 있나}}`만, `complete=False`에 "key=로 읽어라". 자식 24개 넘는 부채꼴은 안 내려간다 | `deployed_code.config`, `key_map` |
| 4 | `code.grep` — 리더에서 4000줄로 받아 **코드 줄 먼저, 문서(`.md/.rst/.txt/.adoc`) 줄은 뒤에** 구분 줄 밑에, 그다음 400줄에서 자른다 | `deployed_code.grep`, `split_doc_lines` |
| 실행기 | 좁힌 읽기(`actions.narrowed` — 그 action의 **선택** 인자 중 `path`·`key`·`offset`)는 증거 한 건 예산이 아니라 전체 예산(`evidence_total_chars`)까지 싣는다. `redis.get`의 `key`는 필수 인자라 안 센다(이름이 같을 뿐) | `runner_probe`(`narrowed_chars=`), `__main__` |
| 3-4 | integrate 규칙 한 줄: "큰 것은 잘라 보지 말고 골라서 전부 봐라" — `projection`·`path`·`offset/limit`·`key` | `config/prompts/investigate-integrate.md` |
| 2-4 | integrate 예시는 사다리 칸 + 읽기 **하나**. 한 칸뿐이라 순서가 선택이다 — 안 써 본 action 먼저, 좁혀 다시 낼 수 있는 것은 뒤 | `briefing.example_block` |

스윕 +19(438). 테스트 먼저(RED 8 → GREEN): `test_jsonpath` 3, `test_actions` 1, `test_stubs` 1, `test_redis_reader` 1(새 파일 — 가짜
클라이언트로 봉투 계약만), `test_deployed_code` 3, `test_runner_probe` 1, `test_briefing` 2. 기존 넷은 "예시 읽기 하나"와
`code.config(service, key?)` 시그니처에 맞췄다. `test_400줄이_넘는_층도_통째로_읽는다`는 키 지도 + `key=`로 끝까지 읽었는지 본다.

### R2-2b-2 — 증거·브리핑 ✅ (10-07)

| 항목 | 한 것 | 자리 |
|---|---|---|
| 1-5 / 6 | 문서 목록은 항목당 **압축 JSON 한 줄**(repr 대신 — 짧고 `filter`에 그대로 옮겨 쓴다). 케이스 `target`(식별 값을 `/`로 이은 것)의 값이 전부 든 행을 **앞에 통째로**, `[n]`은 원래 자리, 머리줄에 `대상 행 N건 먼저`. dict 안의 문서 목록(`rest.query`의 `response`)도 한 줄로 눕히지 않고 같은 모양으로 편다. "외 N행(id로 연다)"는 안 했다 — `evidence.open`이 없다(R4) | `runner_probe.detail(focus=)`, `focus_of(case)` |
| 5-4 | 발견 읽기의 이름을 `TaskOutcome.found`에 구조로 남기고, `execute`가 `deps.declared`(접수 끝점이 읽는 키 템플릿, 없으면 config의 전부 — `flow.declared_keys`)와 대조해 `선언됐는데 없는 키 N개 (scan P, t-k): …`를 **State `facts`**에, 태스크 요약 뒤에도. 잘린 scan·패턴이 안 덮는 템플릿은 말하지 않는다 | `application/facts.py`, `nodes.execute`, `EngineDeps.declared`, `state.facts`, `__main__` 배선 |
| 7 | 끝점 씨앗 줄은 맨 앞에 **예산 밖**, 관계당 여덟 이름은 증상·target 단어가 든 것 먼저(`prefer=`) | `flow.flow_text`, `briefing.case_words` |
| 측정판 | 변형 `cache-missing`(요약 키가 아예 없음 — 사내 c-1형), 배지 응답은 사내 모양의 **행 목록**(group/title, 대상 행은 16번째) | `tools/local_case.py` |

종료 판단 — 측정판에서 실제 소비자로 확인: `cache-missing`에서 r1에 `rest.query`+`redis.scan alarm:*`+`redis.scan *`를 내니 r1
integrate 프롬프트(7195자)의 증거에 `response: 18건 · … · 대상 행 1건 먼저` 다음 줄이 `[16] {"group":"L1","title":"Alarm",…}`이고,
`<열린 질문>`에 `선언됐는데 없는 키 1개 (scan alarm:*, t-2): alarm:stats:{line}`가 실렸다(conclude 프롬프트에도). 스윕 +17(455).

## R2-2c — 운영 ✅ (10-07) — 7-2·7-3. H는 미룸

| 항목 | 한 것 | 자리 |
|---|---|---|
| 7-2 | `LlmPort.close()`(기본은 할 일 없음) · `ChatModelAdapter.close()`가 자기 httpx 풀 둘을 닫음 · `case investigate`(리드·판정 둘 다)·`llm ask/check`·리포트 서술이 끝나면 닫음(`_close_llms`) · **모든 명령이 `_run`을 지남**(`asyncio.run`은 한 곳) — 루프 예외 처리기는 transport 층의 `ConnectionResetError`·`Event loop is closed`만 거르고 태스크 예외는 그대로, Windows에서는 `sys.unraisablehook`도 proactor transport의 `__del__`만 거름 | `domain/llm.py`, `llm_chat_model.py`, `__main__` |
| 7-3 | README "프록시와 NO_PROXY" — 왜 넣는지, `doctor`·조사의 "프록시 경유 의심" 줄, `llm.trust_env_proxy` | `README.md` |
| H | **미룸.** 사내 재측정은 R2-1·R2-2의 효과를 재는 자리인데 system/user 분리는 프롬프트 구조를 바꿔 효과가 섞인다. 토큰 수는 그대로고 이득(게이트웨이의 접두 캐시)은 측정 전엔 가정이다. 재측정 뒤 R4-1(프롬프트 상한)과 함께 — 그때 트레이스에 둘 다 적고 요약은 user 길이를 센다 | — |

종료 판단: Windows 종료 트레이스백은 여기서 재현 못 한다 — 거름망은 합성 context·unraisable 객체로 단위 검증(태스크 예외·다른
객체는 안 삼킨다), 닫기는 가짜 게이트웨이로 `is_closed` 확인, 닫는 배선은 CLI 테스트가 두 어댑터의 `close` 횟수로. 스윕 +5(460).
**여기서 사내 재측정** — [review-12a-4.md](review-12a-4.md)의 일곱 줄 + `응답 N초` + 프롬프트 크기 + **종료 때 트레이스백 유무**.
이 결과로 R3·R4의 숫자를 정한다.

## R2-3 — 재측정(10-08) 뒤 P0 (지시서 "최종" 2-1~2-6, 3-3(a)·3-4·3-5, 5의 kafka tail)

재측정 요약(10-08, sevt): 역할 지정(lead 빠른 모델·conclude 생각 모델·`stream`)으로 리드 턴 4~14초, 판정 턴은 226초가 걸려도
성공 — 180초 벽은 유휴 끊김이었고 stream이 풀었다. 새 문제: 빠른 모델의 JSON 문법 오류(따옴표 누락), 분당 할당량(429, 모델 공유).
모델과 무관하게 남는 것: 입구 매핑 오판(이름 유사성), 예시 따라 하기, 결정적 읽기가 폭에 밀림, verify가 오판을 통과시킴.
**지난 리뷰의 c-1 원인("use:false")은 오판** — 이름이 비슷한 다른 배지였다. 실제 c-1은 요약 키 data 0건 ← 도메인 키 0건 ←
입력 5개 중 원천 키 하나 부재(TTL -2).

| 항목 | 할 것 | 자리 |
|---|---|---|
| 2-5 ✅ | `llm check`가 질문마다 닫아 둘째부터 Connection error — 끝에서 한 번만(R2-2c 회귀) | `__main__.cmd_llm_check` |
| 2-6 ✅ | stderr "이 조사는 안 돌았다"가 판정 뒤에도 찍힘 → `diagnose.llm_error_line`: `LLM 오류 N건 — 조사 중단(llm_error) · 판정 <종류>` / `리드가 계약을 어겼다` | `diagnose.py`, `__main__` |
| 5 ✅ | `kafka tail --limit 300`이 2건: `getmany`를 한 번만 불러 첫 배치만 받는다 → 다 채우거나 더 안 올 때까지 루프 | `kafka_inspector.tail` |
| 2-2 ✅ | 429: 본문의 `nextAccessTime`(ISO·epoch)까지 기다린 뒤 한 번 재시도, 실패로 안 셈. `llm.min_interval_s`를 **base_url 단위 pacer**로 공유(할당량이 모델을 안 가림). 대기 초·횟수는 `LlmReply.waited_s`·`rate_limited`로 트레이스·다이제스트에 | `llm_pacing.py`(새), 어댑터 둘, `trace_digest`, `_make_tracer` |
| 2-1 ✅ | `response_format`: `LlmPort.ask(prompt, *, schema=None)`; `ask_json`이 답 모델의 JSON 스키마를 넘김. `json_schema`로 보내되 **`strict`는 스키마가 닫힐 때만 true** — 태스크 `params`·`filter`가 자유형이라 lead 쪽은 false. `llm.response_format: json_schema \| json_object \| none` | `domain/llm.py`, 어댑터 넷, `lead.ask_json`, `schema_llm` |
| 2-3 ✅ | `stream` 기본값 true(chat_model). 브리프·`config/app.json`의 `llm_roles` 예시 확정(lead max_tokens 1500, conclude timeout 600) | `schema_llm`, `review-12a-4.md`, `app.json` |
| 2-4 ✅ | 프롬프트 상한 `integrate_prompt_chars 8000`·`conclude_prompt_chars 10000`: 넘으면 오래된 증거부터 `id \| 질의 \| 한 줄`로 접고, 그래도 넘으면 끝난 태스크 줄을 접는다. 좁혀 읽은 증거도 같다 | `schema_app`, `lead.make_lead`, `briefing` |
| 3-3(a) ✅ | `component`는 토폴로지 서비스 이름만(`external` 판정만 예외) — 지금은 증거에 나온 이름도 통과해 Redis 키 이름이 통과했다 | `nodes._component_ok` |
| 3-4 △ | 규칙 한 줄 "인자는 그 읽기의 것만" ✅. 예시의 자유 칸을 **자리표시자**로 바꾸는 것은 **R3-0으로** — 입구 매핑이 결정적 읽기를 frame 앞으로 옮기면 frame 예시가 줄어 그때 한 번에 | 템플릿 (→ `briefing.example_block`은 R3-0) |
| 3-5 ✅ | integrate 규칙 한 줄: "값이 비었으면 다음은 그 키를 쓰는 쪽(`code.uses`)과 그 파이프라인의 입력 키" | 템플릿 |

종료 판단: 가짜 게이트웨이로 429→대기→성공, `response_format`이 소켓에 나가는 것, 측정판 r2 integrate 프롬프트가 상한 안.
**그다음 사내 측정 #3**(같은 두 케이스, 2-3 설정 그대로) — 브리프에 리드 턴 JSON 실패 횟수·429 횟수와 대기 초를 더한다.

## R2-4 — 측정 #3(10-08) 뒤 (사내 결론 P0 1~3, P1 4~7)

측정 #3: 판정 턴(생각 모델)이 `json_schema`에서 4/4 JSON 깨짐(c-1 2·c-2 2) → 두 케이스 degraded. 같은 판정 프롬프트를 스키마 없이
보내면 121초에 정상. 리드 턴(빠른 모델) JSON 실패 0. 429는 케이스마다 2회, 매번 정확히 60.0초. integrate 10.4~14.5K인데 고정부만
7.1~7.9K라 증거가 1.6~4.5K로 깎였다. 10054 트레이스백 재발. kafka tail(파티션 1개) limit 300에 0건(8초)/168건(44초).
사내 답: nextAccessTime은 429 본문 **최상위**(code·message·description과 같은 층), 값은 `2026-Oct-08 02:09:00+0000 UTC`
(ISO 아님 — 그래서 매번 기본값 60초). 대안 component의 키 이름은 `llm ask`로 손으로 보낸 답(verify를 안 거침)이었다 —
엔진의 검사는 ⑥에서 이미 대안까지 본다. 사내 트리는 ⑥ 위에 `llm_roles` 설정 커밋 하나(report·lead 339, conclude 581).

| 순서 | 항목 | 할 것 |
|---|---|---|
| ① ✅ | 4 10054 | 콜백 실패(context에 `handle`만, `transport` 키 없음 — asyncio `Handle._run`)도 거른다: 콜백이 `_ProactorBasePipeTransport._call_connection_lost`이고 예외가 연결 리셋일 때만 |
| ② ✅ | 5 429 | `2026-Oct-08 02:09:00+0000 UTC` 모양을 읽는다(월 약어는 로캘과 무관하게 표로). SDK가 남긴 본문과 응답 원문을 둘 다 본다. 트레이스 대기 줄에 근거(`nextAccessTime`·`Retry-After`·`기본값`). 브리프 `min_interval_s` 시작값 15 |
| ③ ✅ | 3 kafka | 빈 배치 한 번에 멈추지 않는다 — 모든 파티션이 끝 오프셋에 닿거나, 빈 배치가 연속 3번이거나, 시간 상한까지 |
| ④ | 1 판정 형식 | 판정 역할은 따로 안 적으면 `response_format: none`(역할 기본값). 스키마를 건 시도가 **파싱**에 실패하면 재시도는 스키마 없이(모든 턴) |
| ⑤ | 2 고정부 | 읽기 목록의 서비스 설명은 이름 + 첫 구절 40자, 해당 없는 규칙 줄은 뺀다(템플릿의 `{?블록}` 표지), 끝점 줄도 흐름 예산 안, 증거 바닥 4K(상한보다 이김), 요약에 읽기 목록·흐름·케이스·열린 질문 크기 |
| ⑥ | 6·7 | 판정 예시의 caveats를 문장으로. 태스크 id 인용은 그 태스크의 증거가 하나면 코드가 바꾸고 caveat에 적는다(둘 이상이면 전처럼 되묻기) |

측정 #4: 같은 두 케이스, 같은 `llm_roles` + `min_interval_s`. 브리프에 판정 턴 JSON 실패 수, 고정부/증거 글자 수, 429 대기 근거.

## R3 — 정확도: 선언형 입구 매핑과 frame 전 읽기 (지시서 "최종" 3-1·3-2·3-3(b)(c)·4, 사내 정보 6)

사내 어휘는 리포에 못 들어간다(⑮). 사슬 — 대상 행의 링크 필드 → 이름 토큰 → api config 경로(`use`·설정 블록) → 키 설정
경로 → 접두 + 키 → 값 안의 identity 경로 — 를 **토폴로지 config가 선언**하고 코드가 걷는다. 이름 유사성은 쓰지 않는다.
사슬이 닫히면 매핑 키와 확인 결과를 케이스 블록에 싣고 그 키 읽기(`redis.get`, 필요하면 `path`)를 **frame 전에** 코드가 돌려
증거로 싣는다. 끊길 때만 후보를 리드에게. frame이 낸 태스크는 폭과 무관하게 전부 돈다(생성 라운드 → 우선순위 순).
verify (b) 인용한 설정 항목이 매핑된 것인지, (c) 인용 증거에 대상 항목의 반대 사실(`use:true`)이 있으면 강등.
픽스처: `source-missing`(c-1형), `source-blank`(c-2형), `pipeline-off`는 이름이 비슷한 `use:false` 미끼. 측정 #3에서 더해진 것:
`code.read`에 "그 줄이 든 함수 전체" 모드(심볼 인덱스의 시작·끝 줄 — 리드가 `limit=20`을 골라 필터 로직을 못 봤다), 파이프라인
클래스 이름으로 쓰는 쪽 엣지와 입력 키 목록(R3-1 (a)(c)). 8-3 숫자는 역할 지정
기준(리드 턴 ≤15초, 판정 ≤600초 stream, 케이스 ≤5분 — 429 대기 별도 표시).

## (옛) R3 — 결정적 triage (지시서 4·5·6, 리뷰 J). 사내에서 받을 것 1~5가 전제

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
