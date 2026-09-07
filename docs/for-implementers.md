# 구현자를 위한 절차서 — 무엇을 어떤 순서로 만지는가

[CLAUDE.md](../CLAUDE.md)가 **하지 말 것**(규율)을 적는다면, 이 문서는 **할 것**(절차)을
적는다. [file-map.md](file-map.md)에서 파일을 찾고, 여기서 순서를 찾아라.

각 레시피는 **만질 파일과 순서**와 **빠뜨리면 조용히 깨지는 것**을 준다(테스트 위치가
자명하지 않은 곳은 함께 적는다). 마지막 항목이 요점이다 — 여기 적힌 것들은 전부 실제로
깨졌던 자리다.

**테스트 트리는 `src/`와 미러링돼 있다** — `src/patrol/rules.py`를 고치면
`tests/patrol/test_rules.py`다. 스키마 검증은 `tests/config/`, 저장소 계약은
`tests/infrastructure/`, CLI 배선은 `tests/test_cli.py`, 기동 검증은 `tests/test_boot.py`.

건드리려는 자리가 이미 알려진 부채인지 [backlog.md](backlog.md)에서 먼저 확인하라 —
계획 문서에 흩어져 있던 인계 99개를 거기 모아 분류했고, 그중 **14개는 이미 갚혔다**
(일부는 계획 문서에 갚음 표시가 없어 열린 것처럼 읽힌다).

## 시작 전 30초

```bash
rm -rf output/; .venv/bin/python -B -m pytest tests/ -q -p no:cacheprovider
```
전부 통과해야 시작이다(정확한 수는 여기 안 적는다 — 커밋마다 낡는다).

돌연변이 확인이 필요하면 **반드시 `python -B`** 를 쓰거나 `__pycache__`를 지워라.
같은 길이의 변조를 같은 초 안에 되돌리면 낡은 바이트코드가 남아 "안 잡혔다"는 거짓
결론이 난다.

---

## 1. 새 프로브 추가

**만질 파일(순서대로)**
1. `src/patrol/probes.py` — 함수를 쓰고 `PROBES` dict에 이름을 등록한다.
2. `src/boot.py` — 그 프로브가 config에서 요구하는 것을 기동에서 검증한다.
3. `src/patrol/probes.py` — 필요하면 `<probe>_problems(params, resolve)`를 함께 만든다
   (`mongo_find_problems`가 본보기다).

**먼저 쓸 테스트**: `tests/patrol/test_probes.py`에 정상 1건 + 오류 1건, 그리고
`tests/test_boot.py`에 기동 거부 1건.

**빠뜨리면 조용히 깨지는 것**
- **`resolved.truncated`를 봉투에 접지 않으면**, 잘린 해석기 표본으로 좁힌 질의가
  "완전한 증거"로 기록되고 verify의 불완전 증거 규칙을 우회한다.
- **`ProbeResult.source`를 안 채우면** 증거가 "무엇을 물었는지"를 잃는다 — `0건`이
  "멈췄다"인지 "질문을 잘못했다"인지 구별 불가.
- **집계(`MetricSpec`) 경로에도 같은 검증을 붙여야 한다.** 지표도 같은 프로브에
  실린다 — 점검에만 붙이면 지표가 무방비다(실제로 그래서 POST가 나갔다).

## 2. 새 rule 추가

**만질 파일(순서대로)**
1. `src/patrol/rules.py` — 판정 함수를 쓰고 `_RULES` dict에 등록한다.
2. `src/config/schema_site.py` — 그 rule이 **concern 축 위에서만 뜻이 있으면**
   `_AXIS_SPECIFIC_RULES`에 더한다(그러면 사람이 concern을 명시해야 통과한다).
3. **rule 종수를 적은 문서를 전부 고친다.** 지금 여섯이라 적힌 곳:
   `CLAUDE.md`의 코드 지도, `docs/config-reference.md`(설명과 rule 표), `docs/glossary.md`,
   `docs/howto.md`, `docs/architecture.md`, `docs/file-map.md`의 `rules.py` 행.
   ```bash
   grep -rn "rule 판정 6종\|6종" docs/ CLAUDE.md      # 고칠 자리를 먼저 센다
   ```

**먼저 쓸 테스트**: `tests/patrol/test_rules.py`에 ok/finding/error 세 갈래.
`_AXIS_SPECIFIC_RULES`에 더했다면 `tests/config/test_schema_site.py`에도 한 건.

**빠뜨리면 조용히 깨지는 것**
- **`KnownRuleError`는 설정 오류 전용이다.** 데이터가 이상한 것(필드 부재, NaN)은
  finding으로 돌려라 — 설정 실수를 finding으로 삼키면 매 순찰이 "이상 탐지"가 된다.
- rule은 concern 축에 **중립**이다. rule 이름으로 concern을 추측하지 마라.

## 3. 새 REST 등재 항목 · 해석기 추가

**만질 파일**
1. `config/gbm/<gbm>.json`(또는 사이트 층 `config/factories/<fct>/<gbm>.json`)의 `target.rest.entries` — `method`·`path`·닫힌 스키마.
2. `knowledge/target_api/<gbm>/<fct>.json` — pinned 명세에도 그 항목이 있어야 한다.
3. 점검·지표의 `resolve`에 해석기를 선언한다.

**빠뜨리면 조용히 깨지는 것**
- **`params.body`에 `${ENV}` 참조를 쓰지 마라.** body는 증거로 박제되고 프롬프트에
  렌더된다.
- 해석기가 가리키는 항목은 **GET이어야 한다** — 값을 얻으려고 부수효과 가능한 메서드를
  쓰지 않는다. 기동이 거부한다.
- **명세는 증거이고 config가 권한이다.** 런타임에 명세를 읽어 허용 범위를 넓히는 코드를
  만들지 마라(fail-open).

## 4. 새 config 키 추가

**만질 파일**
1. `src/config/schema_{app,site,scenario}.py` — `StrictModel`을 상속한 모델에 필드.
2. `src/boot.py` — 그 값이 다른 것과 모순되는지 검증.
3. `docs/config-reference.md` — 항목과 기본값.

**빠뜨리면 조용히 깨지는 것**
- `BaseModel`을 직접 상속하면 오타 키가 조용히 통과한다.
- 비밀값은 `SecretStr` + `${ENV}` 참조. 그리고 **`load_*_config`에 `env`를 넘겨야**
  치환이 실제로 일어난다 — 안 넘기면 "설정했는데 안 켜진다".

## 5. 새 기동 검증 추가

**만질 파일**: `src/boot.py` + `docs/config-reference.md`의 번호 목록.

**빠뜨리면 조용히 깨지는 것**
- **하나 실패해도 계속 진행하고 마지막에 전부 보고한다.** 즉시 return하면 뒤의 문제가
  가려진다(시나리오 절이 실제로 그랬다).
- 점검에 붙인 검증은 **집계 지표에도** 붙여야 한다. 공유 함수를 쓰는 것이 답이다 —
  각자 베끼면 하나가 빠뜨린다.

## 6. 새 API 엔드포인트 추가

**만질 파일**
1. `src/api/routes_{cases,reads}.py` — 라우트.
2. `src/api/models.py` — 응답 모델(dict로 돌려주지 않는다).
3. `docs/howto.md` — curl 예시.

**먼저 쓸 테스트**: `tests/api/test_routes_*.py`에 정상 1건 + **미인가 주체 1건**.

**빠뜨리면 조용히 깨지는 것**
- **`src/api/`는 어댑터·워커·그래프를 import하지 않는다.** `tests/api/test_boundary.py`가
  import 그래프로 지킨다.
- **접근 좁히기를 직접 짜지 마라** — `app.py`의 `visible_record`·`hidden`과
  `AccessPolicy.sites_for`를 쓴다. 이 리포가 실제로 겪은 사고가 "`sites_for`에 프로덕션
  소비자가 0이었다"(읽기 필터를 안 붙여 접수만 막히고 읽기는 열렸다)다.
- 미인가 주체에게는 **404**로 숨긴다(403은 존재 오라클이다).
- 상태 코드를 정할 때 **본문 모양도 정하라** — 같은 409가 두 모양이면 클라이언트가
  두 벌을 짠다.

## 7. 새 이벤트 종류 추가

**대개 답은 "추가하지 마라"다.** 어휘는 6종이고, 늘리기 전에 성질 시험을 통과해야 한다:

> **이 이름이 그래프를 다시 배선해도 그대로 유효한가?**

`verdict_formed`는 도메인 사실이라 유효하다. `node_entered`·`state_patch`는 그래프
모양이 바뀌면 뜻이 사라져 무효다. 기각된 것들: `round_finished`(경계로 유도 가능),
`evidence_added`(`task_finished.evidence_ids`에 이미 있다), `hypothesis_updated`.

정말 필요하면 **스펙 문서를 먼저 갱신한 뒤** `src/domain/events.py`를 고쳐라.

## 8. 저장소 포트에 메서드 추가

**만질 파일(순서대로)**
1. `src/domain/{cases,store,label,snapshot,rollup}.py` — ABC와 **인메모리 구현**.
2. `src/infrastructure/mongo_store.py` — Mongo 구현.
3. `tests/infrastructure/test_mongo_store.py` — **두 백엔드 계약 테스트**.

**빠뜨리면 조용히 깨지는 것**
- **두 구현이 같은 답을 내야 한다.** 이 계약이 계획 20에서만 세 번 깨졌다 — 정렬 동점,
  라벨 순서, 집계 `latest()`.
- **시각을 DB 정렬에 맡길 때는 폭을 맞춰라**(`_fixed_width_iso`). ISO 문자열은 마이크로초가
  0이면 소수부가 없어지고 `Z`가 `.`보다 커서 **정각이 그 초의 최신으로 뒤집힌다**.
- **상한이 있으면 픽스처를 상한보다 크게** 잡아라. 작으면 절단이 안 일어나 절단 순서
  결함이 안 보인다 — 그것이 tier 사다리 역전을 여덟 라운드 숨긴 이유다.
- CAS 술어의 직렬화는 `save`와 **같아야** 한다(`to_jsonable_python`). `.isoformat()`을
  섞으면 Mongo에서 100% 지고 인메모리 테스트에는 안 보인다.

## 9. 케이스 종결 경로 추가

**반드시 `src/__main__.py`의 `_build_publisher`가 조립하는 `(on_event, on_closed)` 쌍을
재사용하라.** 종결 세 경로(데몬·`chat`·`case resume`)가 "보고서 파일을 먼저 쓰고,
`report_ready`를 내고, 메일이 켜져 있으면 발송한다"는 계약을 지켜야 한다 — 각자 베끼면
언젠가 하나가 빠뜨린다(`case resume`이 실제로 한동안 그랬다).

## 10. 프롬프트에 새 줄 추가

**`src/application/briefing.py`의 `one_line`을 지나게 하라.** `[...]` 섹션 어휘를 쓰는
프롬프트가 전부 공유한다 — 브리핑·접수·사이트 선택·리드의 frame/integrate/conclude·
서브에이전트 도구 반환과 goal·순찰 LLM 판정.

**빠뜨리면 조용히 깨지는 것**: 한 줄이 쪼개지면 그 조각이 다음 섹션 머리말처럼 보이고
위조된 블록이 진짜보다 **먼저** 온다. 여러 줄이 정상인 내용(증거 본문·스냅샷)은
개행을 이스케이프하는 표현을 거친다 — `evidence_summary`·`snapshot_text`·`json.dumps`.

---

## 집행 전 점검표

이 리포에서 **실제로 깨졌던** 것들이다. 커밋 전에 자문하라.

- [ ] **함수는 만들었는데 호출부가 안 넘기지 않았나?** `resume_once`가 아무 데서도 안
      불렸고, `patrol run`이 시나리오를 데몬에 안 넘겨 집계가 프로덕션에서 한 번도 안 돌았고,
      브리핑의 적용 룰이 작성 이래 한 번도 안 실렸다. **`grep -rn "<함수명>" src/`로 호출부를
      확인하라.**
- [ ] **문서가 주장하는 배선을 확인했나?** `architecture.md`가 "데몬이 파킹 케이스를 자동
      재개한다"고 적었지만 데몬은 그 함수를 한 번도 안 불렀다. 문서를 근거로 코드를 얹기 전에
      grep하라.
- [ ] **테스트가 이름이 주장하는 것을 실제로 지키나?** 고친 코드를 되돌려 보고 빨개지는지
      확인하라. 최근 웨이브에서 반복된 함정: 정렬 테스트가 동점 키에 업혀 우연히 통과,
      `!r`로 렌더되는 자리에 개행을 넣어 접기를 시험(이미 이스케이프됐다), 컨테이너 본문으로
      `str` vs `repr`을 비교(같다), 픽스처가 상한보다 작아 절단이 안 일어남.
- [ ] **`inspect.getsource`로 구현 표현을 지키고 있지 않나?** 정당한 자리는 "어느 함수를
      부르는가"라는 **배선 사실**뿐이다. 표현을 지키면 등가 리팩터를 오탐하고 주석 한 줄에
      뚫린다.
- [ ] **"이 자리는 예외"라고 논증하고 있지 않나?** 그 논증은 자주 틀렸다. 예외를 논증하는
      것보다 규칙을 넓히는 쪽이 대개 맞다.
- [ ] **실패를 반환값의 상태로 흡수했나?**(규율 1) 새 예외를 만들었다면 허용된 셋과 같은
      수준의 계약인지 의심하라.
- [ ] **`datetime.now()`를 `src/__main__.py` 밖에서 부르지 않았나?**(규율 2)
- [ ] **LLM이 만든 객체의 수명주기 필드를 소독했나?**(규율 4)
- [ ] **문서를 고쳤다면 결과를 다시 읽었나?** 편집 스크립트가 앵커 하나가 안 맞아 중간에서
      죽고 앞부분만 반영된 적이 있다.
- [ ] **"grep 0건"을 안전 주장으로 쓰고 있지 않나?** 범위와 패턴이 둘 다 맞아야 참이다.
      실제로 있었던 일: 같은 거짓 문장의 사본 다섯을 지우면서 `docs/ src/`만 훑어
      루트의 `CLAUDE.md`를 놓쳤고, 패턴에 조사(`이/가`)를 붙여 다른 표현을 놓쳤다.
      **범위에 리포 루트를 넣고, 조사·어미를 뺀 최소 어간으로 찾아라.**
