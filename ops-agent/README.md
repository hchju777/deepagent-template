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

### graphify (선택)

흐름 그래프의 심볼 층은 [graphify](https://github.com/Graphify-Labs/graphify)가 만든다.
**없어도 된다** — 없으면 `code graph`가 흐름 오버레이만 만들고 그렇게 적는다. 심볼 층은 11b의
code_tracer부터 쓴다. `requirements.txt`에 넣지 않은 이유는 사내 반입 심사가 따로 필요해서다
(PyPI `graphifyy` 0.9.65, Apache-2.0, Python 3.10+, 의존성 32개: tree-sitter 코어와 언어 문법 휠 26개,
networkx, numpy, rapidfuzz. LLM SDK는 없고 우리가 부르는 `--code-only`·`--no-label` 경로는
네트워크를 안 쓴다).

온라인:

```bash
.venv/bin/pip install -r requirements-graph.txt
```

폐쇄망 — 밖에서 휠을 받아 반입한다. 받은 휠 파일 목록이 곧 심사 목록이다:

```powershell
# 인터넷 되는 곳에서 (문법 휠이 바이너리라 플랫폼·파이썬을 맞춘다)
py -3.11 -m pip download -r requirements-graph.txt --only-binary=:all: --platform win_amd64 --python-version 311 -d wheelhouse
# 사내에서
.venv\Scripts\python.exe -m pip install --no-index --find-links wheelhouse -r requirements-graph.txt
```

설치만 하면 된다. `code graph`와 테스트는 `GRAPHIFY_BIN` → 실행 중인 python 옆(`.venv\Scripts`)
→ PATH 순서로 찾으므로 activate 여부와 무관하다. 확인은
`python -m pytest tests/knowledge/test_graph_build.py -v`에서 `test_진짜_graphify로_코드만_추출한다`가
스킵이 아니라 통과하는 것.

`code graph`가 남기는 사람용 파일(`output/graph/<gbm>-<fct>/`): `flow.html`은 graphify 없이도
만들어지고 외부 참조가 없어 팀원에게 그 파일 하나만 주면 된다. graphify가 있으면 레포별
`reports/<레포>/GRAPH_REPORT.md`와 `wiki/index.md`(md 묶음)도 생긴다. graphify 자체의
`graph.html`은 만들지 않는다 — 그림 라이브러리를 CDN에서 받아 오게 돼 있어 사내망에서 안 열린다.

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
python -m src peek mongo --collections          # 이 DB에 무슨 컬렉션이 있나
python -m src peek kafka --topics               # 이 브로커에 무슨 토픽이 있나
python -m src peek kafka --lag
python -m src peek rest --entry summary_badge --params '{"line_code":"P222"}'
```

`--collections`·`--topics`는 **이름을 모르고도 찾기 위한 것**이다. 이게 없으면
`--collection`을 부르려면 이름을 미리 알아야 하고, 그러면 조사는 우리가 적어 준 곳만
본다 — "우리가 아는 만큼만 조사하는" 에이전트가 된다. 리드 LLM도 같은 읽기로
이름을 **스스로 찾는다**(`mongo.list_collections`·`kafka.list_topics`·`redis.scan`).

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

```bash
python -m src patrol list                        # 어느 점검이 어느 주기로 도는가
python -m src patrol open --all-sites --dry-run  # 열릴 케이스를 보여만 준다
python -m src patrol open --all-sites            # 실제로 연다
python -m src case list
```

같은 문제가 계속되면 **새 케이스를 열지 않고 있는 케이스에 첨부한다**(`2회째 · 9시간째`).
무시하면 "언제부터 이랬나"가 아무 데도 안 남는다. 닫힌 케이스는 **그 대상이 한 번이라도
정상으로 관측돼야** 다시 열린다 — 안 그러면 안 고쳐진 문제로 3시간마다 케이스가 쌓인다.
자세한 것은 [6a단계 문서](STEPS/step-06a-gate.md).

열린 케이스는 `case investigate`로 조사한다(아래). 판정과 보고서는 아직 없다(12).

## 대상 코드

```bash
python -m src code status     # 읽을 수 있는 상태인가 (네트워크 없음)
python -m src code plan       # 사람이 직접 칠 git 명령 (네트워크 없음)
python -m src code sync       # clone/fetch — **사내에서만**
```

조사가 "데이터가 이상하다"를 넘어 "왜 그런가"로 가려면 대상 서비스의 코드를 읽어야
한다. 코드는 **GBM 단위로 같고**, 어느 커밋이 떠 있는지는
`knowledge/deployment/<gbm>.json`이 말한다. 선언이 없으면 `main` 최신이라고
**가정**하고, 그렇게 읽었다는 사실이 출력에 `(가정)`으로 남는다 — 확인한 것과
가정한 것을 같은 모양으로 찍으면 뒤처진 사이트에서 떠 있지도 않은 코드를 읽는다.

`code status`는 넷을 본다: 경로가 있나 · `.git`이 있나 · **`origin`이 config와 같나** ·
배포가 가리키는 커밋이 로컬에 실재하나. 세 번째가 자물쇠다 — 누가 포크를 그 경로에
클론하면 우리는 그럴듯한 코드를 읽고 **그럴듯하게 틀린 판정**을 낸다.

토큰은 `url`이 아니라 `token` 칸에 적는다(`${GIT_TOKEN}`). url에 넣으면 git이
`.git/config`에 평문으로 저장한다. 자세한 것은
[11a단계 문서](STEPS/step-11a-code.md).

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

### 리드 LLM으로 실제 조사

```bash
python -m src case investigate c-1                    # 대상에 붙는다
python -m src case investigate c-1 --trace            # 프롬프트·날것 응답을 남긴다
python -m src case investigate c-1 --stub-seeds examples/stub-seeds.json
```

같은 울타리 안에서 `frame`·`integrate` 자리만 LLM으로 바뀐다.

```
  라운드 2 — 끝난 이유: decision
  가설
    h-1 [refuted] 파생 집계가 비어 있다  (t-2.e1)
  태스크
    ✅ t-1 어떤 컬렉션이 있는지 본다 — mongo.list_collections → 1건 ['bb_state']
    ✅ t-2 찾은 컬렉션을 읽는다 — mongo.find collection='bb_state' filter={} limit=3 → …
```

**리드는 컬렉션 이름을 모르는 채로 시작한다.** 먼저 목록을 찾고, 찾은 것을 읽는다 —
우리가 이름을 적어 주면 조사는 우리가 아는 곳만 보기 때문이다.

부를 수 있는 목록은 **config에서 생성한다**(손으로 적으면 config와 갈라지고, 갈라진
쪽이 곧 LLM이 믿는 세계가 된다). 프롬프트에 접속 정보는 안 섞이고, 리드가 적은 증거
id는 **State에 실재하는 것만** 남는다 — 환각한 인용을 그냥 들이면 다음 라운드가 그걸
근거로 다시 추론한다. LLM이 죽으면 `stopped_by="llm_error"`로 끝나고 종료 코드 1이다
("조사할 게 없었다"와 절대 같은 모양이 되면 안 된다).

프롬프트는 `config/prompts/investigate-{frame,integrate}.md`에 있다 — 운영이 직접
고치는 파일이라 코드에 안 박았다. **`{example}` 자리의 예시는 코드가 만든다** —
사내 모델로 재 보니 리드는 판단해서 고르는 게 아니라 **예시의 틀을 채운다**(`id`·
`action`·`params`를 그대로 베끼고 `goal`만 바꿨다). 그러면 예시가 곧 출력이므로,
Kafka 없는 사이트에 `kafka.list_topics`가 예시로 박혀 있으면 그걸 그대로 부른다.

조사가 끝나면 **스스로 잰 진단**이 붙는다 — 읽기 몇 회 · 서로 다른 질의 몇 개 ·
**중복 몇 회** · 잘린 증거 · 리드가 어긴 계약. 사람이 출력을 옮겨 적고 손으로 세던
것이고, 실제로 그렇게 세다가 버그 둘을 찾았다.

`--trace`는 라운드마다 **프롬프트·날것 응답·결과**를 `output/traces/<케이스>/`에
남기고, 진단도 `summary.md`로 함께 쓴다. 파싱된 결과만 봐서는 모델이 무엇을 했는지
안 보인다 — 위 사실도 트레이스를 보고서야 알았다. 자세한 것은 [10b단계 문서](STEPS/step-10b-lead.md).

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
| 6a | 케이스 개설과 게이트 | ✅ |
| 6b | 점검별 주기와 순찰 데몬 | ⬜ |
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
| 10b | frame·integrate (리드 LLM) | ✅ |
| 11a | 코드 레포 확보와 지식 층 | ⬜ |
| 11b | 서브에이전트 3종 | ⬜ |
| 11c | 데이터 흐름 그래프 (graphify 엔진 + 우리 오버레이) | 🔧 |
| 12a | conclude + verify | ⬜ |
| 12b | 조사 보고서와 이벤트 | ⬜ |
| 13 | 사람 개입 (질문·재개) | ⬜ |
| 14 | 데몬·워커 (lease·resume) | ⬜ |
| 15 | 실데이터 E2E·운영 | ⬜ |
