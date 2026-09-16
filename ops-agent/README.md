# ops-agent

운영 모니터링 에이전트를 **처음부터** 만드는 프로젝트. 대상 시스템(디지털 트윈)을
읽기만 하면서 이상을 탐지하고, 원인을 조사하고, 증거를 인용한 판정을 낸다.

단계별 문서가 [STEPS/](STEPS/)에 있다. [0단계 — 무엇을 만드는가](STEPS/step-00-overview.md)부터.

**이어서 작업한다면 [STEPS/handover.md](STEPS/handover.md)를 먼저 읽어라** — 어디까지
왔는지, 어떤 방식으로 일하기로 했는지, 그리고 이 프로젝트에서 실제로 깨졌던 것들.

## 실행

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -v
```

Windows(사내):

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -v
```

Windows에서만 터지는 지점들은 [STEPS/windows.md](STEPS/windows.md)에 모아 뒀다 —
인코딩, tz 데이터베이스, 사내 CA와 TLS, 그리고 **같은 3.11인데 argparse 동작이
갈린 사례**. 읽고 시작하는 편이 빠르다.

검증된 파이썬: 3.11.3(사내 Windows), 3.11.15(개발).

## 설정 트리

설정은 `config/` **하나**다. 리포에 들어 있고, 모든 명령의 `--config-root`
기본값이 그것이다. 복사할 예제 트리를 따로 두지 않는다 — 예제에만 파일을 넣고
실제 트리에는 안 넣는 사고가 실제로 났다(`report window`가 "시나리오가 없다"로
막혔다).

```
config/
  app.json                  시간대·출력 경로·LLM·메일 (사이트를 가로지르는 것)
  registry.json             어느 (gbm, fct) 조합이 실재하는가
  gbm/common.json           전 사업부·전 법인 공통
  gbm/<gbm>.json            사업부 공통 — redis 키 규칙, REST 등재 항목
  fct/<fct>/common.json     법인 공통
  fct/<fct>/<gbm>.json      법인 × 사업부 — 실제 접속 url
  scenarios/<이름>.json      운영 리포트 (층 병합을 타지 않는다)
```

**비밀은 `config/`에 적지 않는다** — `${REDIS_PASSWORD}`처럼 참조만 두고 값은
`.env`에 둔다(`.env`는 gitignore, 필요한 키는 [`.env.example`](.env.example)).
비밀이 **아닌** 것(url, 모델 ID, 수신자 목록)은 `config/`에 둔다 — git에 있어야
리뷰가 된다.

리포에 든 값은 접속이 안 되는 자리표시자(`redis://h:6379`)다. 사내에서는 그 값을
실제 값으로 고쳐 쓰고 커밋한다. `pytest`가 `config/`를 검증하므로 — `.env`가
있으면 그 값으로, 없으면 형식만 — 설정을 고친 뒤 `pytest` 한 번이 기동 전
점검이다.

## 사내로 옮기기 — 파일 단위로 복사하지 않는다

이 리포를 사내 git으로 옮길 때 **파일을 골라 복사하면 반드시 빠진다.** 실제로 두 번
났고, 둘 다 증상이 원인을 가렸다:

| 빠진 것 | 증상 | 왜 원인을 못 찾았나 |
|---|---|---|
| `tests/__init__.py` (빈 파일) | `ImportError: attempted relative import beyond top-level package` | 수집 자체가 실패해서 어느 파일 때문인지 안 보였다 |
| `src/__main__.py` 한 줄 | 프롬프트에 `{max_chars}`가 그대로 찍혀 LLM에게 나감 | **리포트는 정상으로 보였다.** 테스트가 잡아서야 드러났다 |

빈 파일은 눈에 안 띄고, 소스와 테스트가 **파일 단위로 섞이면** 커밋 하나만 봐서는
판단이 안 된다. 그래서 트리를 통째로 바꾼다:

```bash
# 이 리포에서 — git이 추적하는 것만, __pycache__·.venv 없이
git archive --format=zip --prefix=ops-agent/ HEAD:ops-agent -o ops-agent.zip
```

```bash
# 사내에서 — config와 .env는 사내 값이므로 먼저 빼 둔다
mv ops-agent/config ../config-backup && cp ops-agent/.env ../
rm -rf ops-agent && unzip ops-agent.zip
cp -r ../config-backup/* ops-agent/config/ && cp ../.env ops-agent/
pytest -q
```

`pytest`가 전부 통과하면 트리가 맞은 것이다 — **그게 이 테스트 묶음의 또 하나의
용도**다.

## 데이터를 하나 꺼내 보기

```bash
python -m src boot                                    # 설정이 온전한가
python -m src config show                             # 병합 결과 + 값의 출처
python -m src doctor                                  # 실제로 붙는가
python -m src peek redis --key oee:L3
python -m src peek mongo --collection oee --filter '{"line":"L3"}' --limit 5
python -m src peek kafka --lag
python -m src peek rest --entry summary_badge --params '{"line_code":"P222"}'
```

사이트가 여러 개면 `--gbm mx --fct gumi`를 붙인다 — **하위 명령 앞뒤 아무 데나** 된다.

## LLM에 물어보기

```bash
python -m src llm describe            # 무엇에 붙어 있는지 (호출은 안 한다)
python -m src llm check               # 붙는가 · 한국어 · JSON
python -m src llm ask "질문"
```

사내 게이트웨이 접속은 [7단계 문서](STEPS/step-07-llm.md). 실제로 붙는지는
사내에서 `pytest tests/live -m live_llm -v`가 확인한다.

## 메일 보내기

```bash
python -m src mail describe                          # 누구에게 보내게 돼 있는지
python -m src mail send --subject "연결 테스트" --dry-run   # 나갈 요청만 확인
python -m src mail send --subject "연결 테스트"             # 실제 발송
python -m src mail send --subject "mx/gumi" --file output/report.md
```

**수신자는 config가 정한다** — 본문이 바꿀 수 없다. 왜 그게 중요한지는
[8단계 문서](STEPS/step-08-mail.md).

## 운영 리포트

```bash
python -m src report scenarios                      # 시나리오 목록
python -m src report window                         # 집계 대상 날짜와 나갈 Mongo 필터
python -m src report window --today 2026-09-07      # 그날 돌았다면 어떻게 되는가
python -m src report aggregate                      # 읽어서 숫자를 낸다(팩트시트)
python -m src report aggregate --stub-seeds s.json  # 대상에 안 붙고 돌려 본다
python -m src report prompt --gbm-only mx               # LLM에게 나갈 프롬프트
python -m src report render --out output/report.html   # 메일 본문 HTML만
python -m src report run                            # 집계→HTML→파일→메일 (스케줄용)
python -m src report run --dry-run                  # 나갈 메일만 보여준다
python -m src report run --no-mail                  # 파일만 만든다
```

한 번 돌리는 것은 **`report run` 하나**다. `render`와 `mail send`를 두 줄로 걸면
앞줄이 실패해도 뒷줄이 돌아서 **어제 파일이 오늘 제목으로** 나간다.

## 순찰

```bash
python -m src patrol probe                          # 점검이 읽는 것을 실제로 읽어 본다
python -m src patrol probe --check badge_all_zero   # 하나만
```

점검은 `gbm/common.json`의 `patrol.checks`가 선언한다 — **점검 하나가 프로브 여러 개를
묶는다**("생산 중일 때만 0/0/0이 이상"을 판정하려면 두 응답이 필요하다). 프로브들은
**동시에** 읽는다: 시점이 벌어지면 "생산 중이었는데 그 사이에 멈춤"이 "생산 중인데
0/0/0"으로 보인다.

```bash
python -m src patrol check              # 판정까지 — 지금 뭐가 걸리나
python -m src patrol check --all-sites  # 28사이트 전부 (하나가 터져도 나머지가 돈다)
```

```
❌ mx/gumi  badge_all_zero [operation] — 5개 중 2건
    Line/Defect     전부 0 — alarm·caution·normal이 모두 0이다
    Line/Downtime   필드 부재 — caution
⬜ mx/sevt  badge_all_zero [operation] — 판정 안 함 — status가 'Idle'다
```

`ok`가 아닌 값이 셋이다 — `finding`(이상) · `skipped`(생산 중이 아니라 **안** 함) ·
`unreachable`(**못** 함). 못 한 것을 "이상 없음"으로 접으면 감시가 자기 실패를 숨긴다.

왜 `concern`이 원인이 아니라 "먼저 물어볼 곳"인지, 왜 오타를 기동이 막는지는
[4단계 문서](STEPS/step-04-probes.md). 판정 규칙 전부와 왜 화이트리스트를 안 두는지는
[5단계 문서](STEPS/step-05-rules.md).

N회 연속·중복 케이스 방지·케이스 개설은 아직 없다(6단계).

## 조사 엔진

```bash
python -m src case dryrun --plan examples/case-dryrun.json --stub-seeds examples/stub-seeds.json
```

**LLM 없이** 라운드를 돌려 본다. 대본 파일이 `frame`·`integrate` 자리를 대신하고,
보는 것은 조사의 내용이 아니라 **울타리**다 — 라운드가 상한에서 멈추는가, 한 라운드에
병렬 폭만큼만 도는가, 입력 증거가 없는 태스크가 걸러지는가.

```
  라운드 2 — 끝난 이유: no_runnable
    ✅ t-1 [data_prober] 파생값(현재 OEE)을 읽는다 — redis.get key='oee:L3' → …
    ❌ t-3 [data_prober] 집계 파이프라인을 돌려 본다 — 미등재 action — mongo.aggregate
    ✅ t-9 [recompute_verifier] 재계산 대조 — t-1의 증거를 입력으로 받는다 — …
```

**`t-9`를 보라.** 우선순위가 제일 앞인데 1라운드에 안 돌고 2라운드에 돌았다 —
`t-1`이 증거를 만들 때까지 select 게이트가 붙잡은 것이다.

대본은 [`examples/case-dryrun.json`](examples/case-dryrun.json)을 고쳐 쓴다. 손으로
쓰는 파일이라 **모르는 키는 거부한다**(`"round"`처럼 s가 빠지면 조용히 무시되는 대신
시끄럽게 죽는다). 설명을 적고 싶으면 키 이름을 `_`로 시작하라 — 주석으로 걷어 낸다.
왜 울타리를 코드가 쥐는지는 [10a단계 문서](STEPS/step-10a-graph.md).

```bash
python -m src schedule --list       # 무엇이 언제 도는지 (돌리지는 않는다)
python -m src schedule              # 스케줄대로 계속 돈다 (상주 프로세스)
```

주기는 시나리오의 `schedule`이 정한다 — `cron`(기본 `0 8 * * *`)과 `interval` 둘 다
쓸 수 있고, **"8시"는 `app.json`의 `timezone` 기준**이다(기계의 TZ가 아니다).
왜 그렇게 나눴는지는 [9g단계 문서](STEPS/step-09g-schedule.md).

시나리오는 `config/scenarios/<이름>.json` 한 파일이 하나다 — 리포트는 사이트를
가로지르므로 사이트 층 병합을 타지 않는다. 기간 계산이 왜 따로 떨어져 있고
날짜 형식을 왜 기동에서 거부하는지는 [9a단계 문서](STEPS/step-09a-window.md).
숫자를 어떻게 세고 실패한 법인을 어떻게 다루는지는
[9b단계 문서](STEPS/step-09b-aggregate.md) — **LLM은 숫자에 관여하지 않는다.**
왜 table 레이아웃과 인라인 스타일만 쓰고 다크모드를 지원하지 않는지는
[9c단계 문서](STEPS/step-09c-render.md). 차트가 왜 라이브러리 없이
직접 만든 PNG인지는 [9d단계 문서](STEPS/step-09d-chart.md).
LLM이 숫자를 만들지 못하게 어떻게 막는지는 [9e단계 문서](STEPS/step-09e-comment.md).
왜 파일을 메일보다 먼저 쓰고 종료 코드로 실패를 말하는지는
[9f단계 문서](STEPS/step-09f-run.md).

자세한 것은 [3b단계 문서](STEPS/step-03b-adapters.md).

열려 있는 것과 **왜 지금은 괜찮은지**는 [STEPS/backlog.md](STEPS/backlog.md).

## 진행 상황

단계 번호와 이름의 단일 진실 소스는 [step-00의 로드맵 표](STEPS/step-00-overview.md)다 —
여기는 같은 목록에 상태 칸만 더한 것이고, `tests/test_docs.py`가 둘이 갈리지 않는지
검사한다. 실행 순서는 번호 순이 아니다(엔진이 순찰보다 먼저다).

| 단계 | 만드는 것 | 상태 |
|---|---|---|
| 1 | 뼈대와 규율 (StrictModel, 시계 주입) | ✅ |
| 2 | 도메인 모델과 포트 | ✅ |
| 3a | config 스키마·로더·기동 검증 | ✅ |
| 3b | 어댑터 (스텁 + 실구현) | ✅ |
| 4 | 순찰 프로브 | ✅ |
| 5 | rule 판정과 finding | ✅ |
| 6 | 케이스 저장소와 게이트 | ⬜ |
| 7 | **사내 LLM 게이트웨이 연결** | ✅ |
| 8 | 메일 발송 Agent API | ✅ |
| 9a | 리포트 기간과 시나리오 | ✅ |
| 9b | 수집과 집계 | ✅ |
| 9c | 블록 렌더링(HTML) | ✅ |
| 9d | 추이 차트(표 막대) | ✅ |
| 9e | LLM 서술 | ✅ |
| 9f | `report run` + 메일 배선 | ✅ |
| 9g | 스케줄러(cron·interval) | ✅ |
| 10a | 조사 State와 그래프 배선 | ✅ |
| 10b | frame·integrate (리드 LLM) | ⬜ |
| 11a | 코드 레포 확보와 지식 층 | ⬜ |
| 11b | 서브에이전트 3종 | ⬜ |
| 11c | 코드 지식 그래프 (graphify) | ⬜ |
| 12a | conclude + verify | ⬜ |
| 12b | 조사 보고서와 이벤트 | ⬜ |
| 13 | 사람 개입 (질문·재개) | ⬜ |
| 14 | 데몬·워커 (lease·resume) | ⬜ |
| 15 | 실데이터 E2E·운영 | ⬜ |
