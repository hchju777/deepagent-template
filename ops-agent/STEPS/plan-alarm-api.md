# 알람 메일 API — 이해한 것과 정할 것

> **상태**: 합의 전이다. 아래 "정할 것" 아홉 가지에 답을 받은 뒤 1단계를 시작한다.
> 단계 하나 = 테스트 먼저(RED → GREEN) · 커밋 하나 · 푸시. 사내 검증(메일·LLM·DB·Swagger)은 평가자가
> 단계마다 사내 PC worktree에서 하고, 사내 원본과 동작을 대조해 보고한다. 단계가 끝나면 푸시만 하고
> 그 보고를 기다린다.

## 배경

사내에서 임시 점검 서버(FastAPI)를 따로 만들어 배포했고, 운영 네트워크에서 DB·LLM·메일이 다 붙는 것을
확인했다. 그 코드는 이 리포에 없다. 그 모양(A)을 `src/presentation/api/`에 새로 짓고 기능(B)을 더한다.

**최종 형태**

- 컨테이너는 uvicorn API 하나다. Dockerfile CMD에 `schedule` 명령이 없고, 스케줄은 앱 안에서 돈다.
- replica 1, 포트 9510, 인증 없음.
- `CMD uvicorn src.presentation.api.app:app --host 0.0.0.0 --port 9510`.
- 베이스 이미지는 사내 것이라 `ARG`로 받는 자리만 둔다.
- replica가 1이므로 메모리 상태(작업 기록 50개, 일시정지 여부)로 충분하다.

**건드릴 곳**: api 폴더, report, mail CLI, Dockerfile, requirements, app.json, `.gitignore`. R3 파일은 손대지
않는다(정확도 라운드가 만지는 조사 엔진 쪽 — `application/`, `knowledge/`, 걷기·판정).

## 리포에서 확인한 것

- **작업 트리는 `ops-agent/`다.** [handover](handover.md)가 "원본 `src/`·`tests/`는 건드리지 않는다"고 한다.
  새 코드는 `ops-agent/src/presentation/api/`, Dockerfile도 `ops-agent/`에 둔다(지금은 없다). 루트의
  `src/api/`는 원본이라 손대지 않는다.
- **재사용할 것이 이미 있다.**
  - `src/schedule/`: cron 파서와 `run_job`. 다음 시각을 **지금 기준으로** 다시 계산하므로 밀린 슬롯은 이미
    건너뛴다(`runner.py` 머리 docstring).
  - `config/scenarios/daily-alarm.json`의 cron이 이미 `0 8 * * 1-5`이고, 시계는 app.json의
    `timezone`(Asia/Seoul)을 따른다(`_clock`).
  - `schema_llm`에 `trust_env_proxy`와 `headers`가 이미 있다. app.json에 값만 넣으면 된다.
- **메일 수신인 수동 지정은 어댑터를 고치지 않아도 된다.** `mail_agent.compose_input_value`가
  `cfg.recipients`만 읽으므로, `recipients`를 바꾼 `MailConfig` 사본으로 보내면 된다.
- **HTML 파일 이름의 날짜는 기준일(어제)이다.** `publish.output_path`가
  `<시나리오>-<window.yesterday>.html`로 쓰고, 제목도 기준일이다(`subject_for`).
- **`report run`은 법인 하나만 못 읽어도 종료 코드 1이다**(`cmd_report_run` 끝줄). 아래 "정할 것" 4번이
  이것 때문이다.

## A — 검증된 모양을 그대로 짓는다

### 작업 실행기

- 셸 없이 `[sys.executable, "-u", "-m", "src", *argv]`를 하위 프로세스로 띄운다. cwd는 리포 루트,
  env에 `PYTHONIOENCODING=utf-8`.
- 한 번에 하나만 돈다. 돌고 있으면 **409와 돌고 있는 작업**을 돌려준다.
- 시간을 넘기면 죽이고, 그때까지 나온 stdout/stderr를 남긴다.
- 기록: 작업 id, 명령, 상태(`running`/`ok`/`failed`/`timeout`), 시작 시각, 소요, 종료 코드, stdout,
  stderr. 최근 50개를 메모리에.
- 요청은 `wait_s`(0~55초)까지만 기다린다. 그 안에 끝나면 결과, 아니면 id를 준다. 55초인 이유는 Ingress
  상한이 60초라서다. 이후는 `GET /jobs`, `GET /jobs/{id}`로 본다.
- 시간 상한: 짧은 것 180초, doctor 60초, report 1800초.

### 엔드포인트 (1단계에서 운영용과 진단용으로 나눈다)

| 구분 | 경로 | 방식 | 상한 |
|---|---|---|---|
| 상태 | `/health`, `/ready`, `/version` | 앱 안 | — |
| 운영 | `/jobs`, `/jobs/{id}` | 앱 안 | — |
| 운영 | `/schedule/list` → `schedule --list` | 하위 프로세스 | 180초 |
| 운영 | `/report/aggregate` (today) | 하위 프로세스 | 180초 |
| 운영 | `/report/run` (today, dry_run 기본 true, no_mail) | 하위 프로세스 | 1800초 |
| 운영 | `/mail/describe` | 하위 프로세스 | 180초 |
| 운영 | `/mail/send` (subject, dry_run 기본 true, to[]) | 하위 프로세스 | 180초 |
| 운영 | `/output` 목록(최근 순), `/output/{name}` | 앱 안 | — |
| 진단 `/diag` | `boot` | 하위 프로세스 | 180초 |
| 진단 `/diag` | `doctor` (gbm, fct) | 하위 프로세스 | 60초 |
| 진단 `/diag` | `llm/check` (role: report·lead·conclude), `llm/ask` (prompt 2000자 이하) | 하위 프로세스 | 180초 |
| 진단 `/diag` | `doctor/all`, `llm/raw`, `env`, `net`, `net/egress`, `net/trace` | 앱 안 | — |

- `mail/send`의 `to`를 주면 config 수신인은 **무시하고** 입력한 주소에만 보낸다. 안 주면 config 수신인.
- `/output/{name}`은 output 폴더 밖 경로를 거부하고, HTML은 렌더된 그대로 보여 준다.
- `doctor/all`(gbm 선택)은 사이트마다 Redis·Mongo **접속만** 보고, 동시 개수를 제한한다.
- `llm/raw`는 SDK 없이 httpx로 게이트웨이에 한 번 요청한다. 문제가 SDK 쪽인지 게이트웨이 쪽인지 가르는 용도다.
- `env`는 config가 참조하는 `${VAR}`만, 있음/없음 모양으로 보여 준다. URL의 계정 부분은 가린다.
- `net`은 메일 에이전트와 LLM 게이트웨이까지 DNS → TCP → TLS를 단계별로 본다.
- `net/egress`는 Redis `CLIENT INFO`와 Mongo `whatsmyuri`로 바깥에서 보이는 우리 IP를 확인한다.
- `net/trace`는 실제 포트로 TTL 1..N인 TCP SYN을 보내고, ICMP 오류 큐(`IP_RECVERR`)에서 hop 장비 주소를
  읽는다. Linux 전용이다.

### 함께 바꿀 것

- requirements: `fastapi>=0.115,<1`, `uvicorn>=0.20`.
- app.json `llm`: `trust_env_proxy: false`, `headers`에 게이트웨이 사용자 메일 헤더(값은 env 참조).
- `.gitignore`: `.claude`.

## B — 더할 것 (단계)

1. **뼈대**
   - 앱 팩토리. 시계를 주입받는다(`datetime.now()` 직접 호출 금지).
   - `/ready`: 설정이 로드되는가, 비밀 키가 있는가, 스케줄러 태스크가 살아 있는가.
   - `/version`: 빌드 ARG의 커밋과 빌드 시각, app.json + 시나리오 해시, 시나리오, 다음 실행. 해시는
     `${}` 치환 **전** 원문으로 계산하므로 비밀이 섞이지 않는다.
   - A를 운영용(`/report`, `/mail`, `/schedule`, `/jobs`, `/output`)과 진단용(`/diag`: doctor, net,
     egress, trace, llm, env)으로 나눠 짓는다.
2. **스케줄러**
   - lifespan의 asyncio 태스크에서 기존 `src/schedule`을 그대로 쓴다(cron `0 8 * * 1-5` Asia/Seoul,
     밀린 슬롯 건너뛰기).
   - 시각이 되면 작업 실행기에서 `report run --scenario <이름>`. 수동 작업이 돌고 있으면 끝날 때까지 기다린다.
   - `GET /schedule`: 켜짐, cron, 다음 시각, 최근 결과(성공/실패/이미 보냄, 소요, 작업 id).
   - `POST /schedule/pause`, `/schedule/resume`: 메모리에만.
   - 테스트는 가짜 시계로.
3. **발송 장부**
   - CLI `report run`이 실제 발송 성공 뒤 `output/sent/<시나리오>-<날짜>.json`을 쓴다. 내용: 발송 시각,
     제목, 수신자 수, 계기(`schedule`|`manual`), 작업 id.
   - 장부가 있으면 보내지 않고 알린다. `--force`로 덮어쓴다. 스케줄·Swagger·CLI가 같은 규칙이다.
   - API: `POST /report/run`에 `force` 추가(장부가 있고 force가 false면 409와 장부).
     `POST /report/resend`(date, force)는 집계·LLM 없이 그날 HTML을 그대로 `mail send --file`로 보낸다.
     `GET /report/sent`, `GET /report/sent/{date}`.
4. **출력 보관**
   - 시나리오 config에 `keep_days`(예: 3).
   - 파일을 쓴 직후, 같은 시나리오 이름 패턴의 HTML과 장부만 **파일명 날짜로** 지운다. `graph/`, `traces/`,
     `cases.json`은 건드리지 않는다.
   - `GET /output`에 정책과 지울 예정인 파일. `POST /output/cleanup`(기본 dry_run).
5. **메일**
   - `GET /mail/recipients`: 주소를 가리고 수만.
   - `POST /mail/test`(to 한 주소, date): 그날 리포트가 있으면 그것을, 없으면 짧은 본문을 그 한 사람에게만.
   - CLI `mail send --to`.
6. **실패 알림**
   - 스케줄 실행이 실패하면(종료 코드, 시간 초과, 메일 실패) config `notify_to`로 짧은 메일 1통.
     `notify_to`는 지금 수신인과 같은 값이고 env 자리로 둔다.
   - 내용: 시나리오, 날짜, 실패 종류, stderr 끝 30줄, "/jobs/{id} 보고 /report/resend".
   - 하루 한 통. 자동 재시도는 없다.

## 요청문의 오타를 이렇게 읽었다

| 원문 | 읽은 뜻 |
|---|---|
| `exist` | 종료 코드(exit code) |
| `PYTHONIENCODING` | `PYTHONIOENCODING` |
| `Ingress 60ch` | Ingress 60초 |
| `force - wlqrP.` | force — 집계·LLM 없이 |
| `ㅏㅇ부` | 장부 |
| `output ㅗㄹ더` | output 폴더 |
| `/report/send/{date}` | `/report/sent/{date}` |
| `recipinets` | recipients |
| `keep_datys` | `keep_days` |

## 정할 것 (추천안 포함)

1. **브랜치.** 요청은 `feature/alarm-api`였다. 지금 작업 브랜치는 `claude/alarm-mail-api-w1zf74`이고, 현재
   HEAD(4258fda)에서 갈라져 있다. **추천: 이 브랜치 그대로.** 이름을 바꿔야 하면 알려 달라.
2. **허용 목록 밖이지만 꼭 손대야 하는 파일.**
   - `src/config/schema_report.py`(`keep_days`), `src/config/schema_mail.py`(`notify_to`): 모델이
     `extra="forbid"`라서 스키마에 칸을 더하지 않으면 config 로드가 실패한다.
   - `src/__main__.py`: R3 명령과 같은 파일이라 mail·report 파서와 핸들러만 고친다.
   - `tests/`.

   이 범위로 진행해도 되는가?
3. **장부의 날짜.** 돌린 날이 아니라 **기준일(어제)**. HTML 파일 이름(`daily-alarm-<기준일>.html`)과 같아서
   `resend(date)`가 그 파일을 바로 찾는다.
4. **실패 알림 기준 (중요).** 지금 `report run`은 법인 하나만 못 읽어도 종료 코드 1이다. 사이트 28개 중 하나는
   거의 매일 안 붙으므로([handover](handover.md) "대상 환경의 규모"), 이대로면 **매일 실패 메일이 간다.**
   - **추천**: "이미 보냄"은 전용 종료 코드로 분리한다. 알림은 시간 초과이거나 이번 실행이 장부를 못 썼을
     때(메일이 안 나감)만 보낸다. 메일은 나갔는데 일부 법인을 못 읽은 경우는 "성공(경고)"로 둔다.
5. **`keep_days`의 뜻.** 달력 기준 N일이면 주말 때문에 화요일 실행 때 목·금 파일이 지워진다(N=3이면
   월·일·토 범위만 남는다). **추천: 파일명 날짜 기준 최근 N개를 남긴다.**
6. **장부가 있을 때의 `report run`.** 집계·LLM보다 **먼저** 장부를 보고 바로 멈춘다. LLM 비용이 들지 않고,
   이미 보낸 HTML을 덮어쓰지도 않는다. dry_run과 no_mail은 발송하지 않으므로 장부를 무시하고 돈다. 계기와
   작업 id는 `report run`에 `--trigger`, `--job-id` 플래그로 넘긴다.
7. **resend 뒤 장부.** `mail send` CLI에는 장부 개념이 없다. 그래서 resend가 성공(종료 코드 0)하면
   **API가 장부를 덮어쓴다**(계기 `manual`).
8. **세부.**
   - 게이트웨이 사용자 메일 헤더 이름은 자리표시자로 넣고, 값은 `${LLM_USER_EMAIL}` 같은 env 참조로 둔다.
   - HTTP 메서드는 실행하는 것은 POST, 읽는 것은 GET. 평가자 대조가 사내 원본 기준이므로, 원본의 경로나
     메서드가 다르면 알려 달라.
9. **진행 방식.** 한 단계를 끝내고 푸시한 뒤 **멈춰서 평가자 대조 보고를 기다린다**
   ([handover](handover.md) "한 단계 → 리뷰 요청 → 수정 → 다음 단계"와 같다).
