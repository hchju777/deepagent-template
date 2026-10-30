# 알람 메일 API — 계획

> **상태**: 합의됨(10-30 회신 — "정한 것" 아홉 가지는 전부 추천안대로, 고칠 것 셋 반영).
> 단계 하나 = 테스트 먼저(RED → GREEN) · 커밋 하나 · 푸시. 사내 검증(메일·LLM·DB·Swagger)은 평가자가
> 단계마다 사내 PC worktree에서 하고, 사내 원본과 동작을 대조해 보고한다. 단계가 끝나면 푸시만 하고
> 그 보고를 기다린다.

## 배경

사내에서 임시 점검 서버(FastAPI)를 따로 만들어 배포했고, 운영 네트워크에서 DB·LLM·메일이 다 붙는 것을
확인했다. 그 코드는 이 리포에 없다. 그 모양(A)을 `src/presentation/api/`에 새로 짓고 기능(B)을 더한다.

**최종 형태**

- 컨테이너는 uvicorn API 하나다. CMD에 `schedule` 명령이 없고, 스케줄은 앱 안에서 돈다.
- replica 1, 포트 9510, 인증 없음. **uvicorn 워커도 하나다**(`--workers`를 주지 않는다) — 작업 기록·일시정지·
  스케줄러가 프로세스 메모리에 있으므로, 워커가 둘이면 스케줄이 두 번 돌고 `/jobs`가 요청마다 다른 것을 보인다.
- **Dockerfile은 이 리포에 쓰지 않는다.** 사내 리포 루트에 사내 Dockerfile이 이미 있다(베이스 이미지,
  `INSTALL_HOME`, `WORKDIR`, 베이스 이미지의 시작 래퍼). 바꿀 것은 아래 "사내 Dockerfile에 적용할 것"으로만 남긴다.

**건드릴 곳**: `src/presentation/api/`, report, mail CLI(`src/__main__.py`의 mail·report 부분만),
`src/config/schema_report.py`, `src/config/schema_mail.py`, requirements, app.json(`mail.notify_to` 자리표시자만),
`.gitignore`, `tests/`. R3 파일(정확도 라운드가 만지는 조사 엔진 쪽 — `application/`, `knowledge/`, 걷기·판정)은
손대지 않는다. **app.json의 `llm` 칸(`headers`, `trust_env_proxy`)은 손대지 않는다** — 이미 있는 칸이고 사내
config에서 값으로 넣는다. 리포 사본을 고치면 사내 app.json 값을 덮는다.

## 사내 Dockerfile에 적용할 것

리포 루트(`src/`, `config/`가 있는 폴더)가 컨테이너의 `/`다.

```dockerfile
# 빌드 ARG — /version이 읽는다. 없으면 빈 값으로 나간다(죽지 않는다).
ARG BUILD_COMMIT=""
ARG BUILD_TIME=""
ENV BUILD_COMMIT=${BUILD_COMMIT} BUILD_TIME=${BUILD_TIME}

# 바꿀 CMD 한 줄 — 앞의 시작 래퍼는 지금 운영에서 도는 것 그대로
CMD ["<시작 래퍼>", "uvicorn", "src.presentation.api.app:app", "--host", "0.0.0.0", "--port", "9510"]
```

```bash
docker build --build-arg BUILD_COMMIT="$(git rev-parse --short HEAD)" \
             --build-arg BUILD_TIME="$(date -Iseconds)" ...
```

## 리포에서 확인한 것

- **작업 트리는 `ops-agent/`다.** [handover](handover.md)가 "원본 `src/`·`tests/`는 건드리지 않는다"고 한다.
  새 코드는 `ops-agent/src/presentation/api/`. 루트의 `src/api/`는 원본이라 손대지 않는다.
- **작업 실행기의 리포 루트**는 `src/presentation/api/`에서 세 칸 위다(`Path(__file__).resolve().parents[3]`).
  컨테이너에서는 `/`, 이 리포에서는 `ops-agent/`.
- **재사용할 것이 이미 있다.**
  - `src/schedule/`: cron 파서와 `run_job`. 다음 시각을 **지금 기준으로** 다시 계산하므로 밀린 슬롯은 이미
    건너뛴다(`runner.py` 머리 docstring).
  - `config/scenarios/daily-alarm.json`의 cron이 이미 `0 8 * * 1-5`이고, 시계는 app.json의
    `timezone`(Asia/Seoul)을 따른다(`_clock`).
  - `schema_llm`에 `trust_env_proxy`와 `headers`가 이미 있다.
  - `resolve_env(raw, {})`가 돌려주는 "못 찾은 이름"이 곧 그 파일이 참조하는 `${VAR}` 전부다 — `/diag/env`가 쓴다.
- **메일 수신인 수동 지정은 어댑터를 고치지 않아도 된다.** `mail_agent.compose_input_value`가
  `cfg.recipients`만 읽으므로, `recipients`를 바꾼 `MailConfig`로 보내면 된다.
- **HTML 파일 이름의 날짜는 기준일(어제)이다.** `publish.output_path`가
  `<시나리오>-<window.yesterday>.html`로 쓰고, 제목도 기준일이다(`subject_for`).
- **`report run`은 법인 하나만 못 읽어도 종료 코드 1이다**(`cmd_report_run` 끝줄). 실패 알림 기준(정한 것 4)이
  이것 때문이다.

## A — 검증된 모양을 그대로 짓는다

### 작업 실행기

- 셸 없이 `[sys.executable, "-u", "-m", "src", *argv]`를 하위 프로세스로 띄운다. cwd는 리포 루트,
  env에 `PYTHONIOENCODING=utf-8`.
- 한 번에 하나만 돈다. 돌고 있으면 **409와 돌고 있는 작업**을 돌려준다.
- 시간을 넘기면 죽이고, 그때까지 나온 stdout/stderr를 남긴다.
- 기록: 작업 id, 명령, 상태(`running`/`ok`/`failed`/`timeout`), 시작 시각, 소요, 종료 코드, stdout,
  stderr. 최근 50개를 메모리에.
- 요청은 `wait_s`(0~55초)까지만 기다린다. 그 안에 끝나면 결과(200), 아니면 작업 id(202)를 준다. 55초인 이유는
  Ingress 상한이 60초라서다. 이후는 `GET /jobs`, `GET /jobs/{id}`로 본다.
- 시간 상한: 짧은 것 180초, doctor 60초, report(aggregate·run) 1800초.

### 경로와 메서드

- 접두는 사내 원본과 같다: 운영 `/internal/v1/ops/...`, 진단 `/internal/v1/ops/diag/...`, `/health`·`/ready`·
  `/version`은 루트. (회신의 `/interval/v1/ops`는 진단 접두와 맞춰 `/internal`의 오타로 읽었다 — 다르면 상수 하나만 고친다.)
- **메서드**: 작업 실행기에 작업을 만들거나 바깥에 요청을 내는 것은 POST, 우리 상태만 읽는 것은 GET.
  POST의 인자는 JSON 본문이고, 본문을 비우면 전부 기본값이다(Swagger에서 그대로 누를 수 있게).

| 구분 | 경로 | 방식 | 상한 |
|---|---|---|---|
| 루트 | `GET /health`, `GET /ready`, `GET /version` | 앱 안 | — |
| 운영 | `GET /jobs`, `GET /jobs/{id}` | 앱 안 | — |
| 운영 | `POST /schedule/list` → `schedule --list` | 하위 프로세스 | 180초 |
| 운영 | `POST /report/aggregate` (scenario, today) | 하위 프로세스 | 1800초 |
| 운영 | `POST /report/run` (scenario, today, dry_run 기본 true, no_mail) | 하위 프로세스 | 1800초 |
| 운영 | `POST /mail/describe` | 하위 프로세스 | 180초 |
| 운영 | `POST /mail/send` (subject, dry_run 기본 true, to[]) | 하위 프로세스 | 180초 |
| 운영 | `GET /output` 목록(최근 순), `GET /output/{name}` | 앱 안 | — |
| 진단 | `POST /diag/boot` | 하위 프로세스 | 180초 |
| 진단 | `POST /diag/doctor` (gbm, fct) | 하위 프로세스 | 60초 |
| 진단 | `POST /diag/llm/check` (role), `POST /diag/llm/ask` (prompt 2000자 이하, role) | 하위 프로세스 | 180초 |
| 진단 | `POST /diag/doctor/all`, `/diag/llm/raw`, `/diag/net`, `/diag/net/egress`, `/diag/net/trace` | 앱 안 | — |
| 진단 | `GET /diag/env` | 앱 안 | — |

- `mail/send`의 `to`를 주면 config 수신인은 **무시하고** 입력한 주소에만 보낸다. 안 주면 config 수신인.
  CLI에 `mail send --to`가 있어야 하므로 **B5의 `--to`를 1단계로 당긴다**(A가 그것을 요구한다).
- `/output/{name}`은 output 폴더 밖 경로를 거부하고, HTML은 렌더된 그대로 보여 준다.
- `doctor/all`(gbm 선택)은 사이트마다 Redis·Mongo **접속만**(ping) 보고, 동시 개수를 제한한다.
- `llm/raw`는 SDK 없이 httpx로 게이트웨이에 한 번 요청한다. 문제가 SDK 쪽인지 게이트웨이 쪽인지 가르는 용도다.
- `env`는 config가 참조하는 `${VAR}`만, 있음/없음 모양으로 보여 준다. URL의 계정 부분은 가린다.
- `net`은 메일 에이전트와 LLM 게이트웨이까지 DNS → TCP → TLS를 단계별로 본다.
- `net/egress`는 Redis `CLIENT INFO`와 Mongo `whatsmyuri`로 바깥에서 보이는 우리 IP를 확인한다.
- `net/trace`는 실제 포트로 TTL 1..N인 TCP SYN을 보내고, ICMP 오류 큐(`IP_RECVERR`)에서 hop 장비 주소를
  읽는다. Linux 전용이다.
- 진단이 Redis·Mongo에 직접 여는 연결(`doctor/all`·`egress`)은 포트를 거치지 않는다 — 포트는 등재된 읽기만
  열고(규율 9) `CLIENT INFO`·`whatsmyuri`·`ping`은 등재 대상이 아니다. 고정된 명령 셋만, 짧게 열고 닫는다.

### 함께 바꿀 것

- requirements: `fastapi>=0.115,<1`, `uvicorn>=0.20`.
- `.gitignore`: `.claude`.

## B — 더할 것 (단계)

1. **뼈대**
   - 앱 팩토리. 시계를 주입받는다(`datetime.now()` 직접 호출 금지 — 진짜 시계는 `app.py` 경계에서 한 번).
   - `/ready`: 설정이 로드되는가, 비밀 키가 있는가, 스케줄러 태스크가 살아 있는가(스케줄러는 2단계라 1단계에서는
     "아직 없음"으로 통과).
   - `/version`: 빌드 ARG의 커밋과 빌드 시각, app.json + 시나리오 해시, 시나리오, 다음 실행. 해시는
     `${}` 치환 **전** 원문으로 계산하므로 비밀이 섞이지 않는다.
   - A를 운영용과 진단용으로 나눠 짓는다.
   - **종료 판단**: 위 표의 경로가 전부 Swagger(`/docs`)에 보이고, 작업 실행기의 409·시간 초과·부분 출력·
     한글이 테스트로 확인되고, 실행기가 넘기는 argv가 경로마다 테스트로 고정된다. 사내 대조는 평가자.
2. **스케줄러**
   - lifespan의 asyncio 태스크에서 기존 `src/schedule`을 그대로 쓴다(cron `0 8 * * 1-5` Asia/Seoul,
     밀린 슬롯 건너뛰기).
   - 시각이 되면 작업 실행기에서 `report run --scenario <이름>`. 수동 작업이 돌고 있으면 끝날 때까지 기다린다.
   - `GET /schedule`: 켜짐, cron, 다음 시각, 최근 결과(성공/실패/이미 보냄, 소요, 작업 id).
   - `POST /schedule/pause`, `/schedule/resume`: 메모리에만.
   - 테스트는 가짜 시계로.
3. **발송 장부**
   - CLI `report run`이 실제 발송 성공 뒤 `output/sent/<시나리오>-<기준일>.json`을 쓴다. 내용: 발송 시각,
     제목, 수신자 수, 계기(`schedule`|`manual`), 작업 id. 계기와 작업 id는 `--trigger`, `--job-id`로 받는다.
   - 장부가 있으면 집계·LLM **전에** 멈추고 알린다("이미 보냄" 전용 종료 코드). `--force`로 덮어쓴다.
     스케줄·Swagger·CLI가 같은 규칙이다. dry_run·no_mail은 발송하지 않으므로 장부를 무시하고 돈다.
   - API: `POST /report/run`에 `force` 추가(장부가 있고 force가 false면 409와 장부).
     `POST /report/resend`(date, force)는 집계·LLM 없이 그날 HTML을 그대로 `mail send --file`로 보내고,
     성공하면 **API가** 장부를 덮어쓴다(계기 `manual`). `GET /report/sent`, `GET /report/sent/{date}`.
4. **출력 보관**
   - 시나리오 config에 `keep_days`(예: 3) — **파일명 날짜 기준 최근 N개 날짜**를 남긴다(달력 N일이 아니다 —
     주말이 끼면 화요일 실행에서 목·금 파일이 지워진다).
   - 파일을 쓴 직후, 같은 시나리오 이름 패턴의 HTML과 장부만 지운다. `graph/`, `traces/`, `cases.json`은
     건드리지 않는다.
   - `GET /output`에 정책과 지울 예정인 파일. `POST /output/cleanup`(기본 dry_run).
5. **메일**
   - `GET /mail/recipients`: 주소를 가리고 수만.
   - `POST /mail/test`(to 한 주소, date): 그날 리포트가 있으면 그것을, 없으면 짧은 본문을 그 한 사람에게만.
   - (CLI `mail send --to`는 1단계로 당겼다.)
6. **실패 알림**
   - 스케줄 실행이 실패하면 `mail.notify_to`로 짧은 메일 1통. **실패**는 시간 초과이거나 이번 실행이 장부를
     못 쓴 것(메일이 안 나감)이다. 메일은 나갔는데 일부 법인을 못 읽은 경우는 "성공(경고)"이고 알리지 않는다
     — 28개 중 하나는 거의 매일 안 붙으므로, 종료 코드 1을 그대로 실패로 세면 매일 알림이 간다.
   - `notify_to`는 `mail` 아래 `recipients` 옆의 리스트다: `"notify_to": ["<운영자 주소>"]`. config 값 그대로
     (사내 config에 실제 값, 이 리포 사본엔 자리표시자).
   - 내용: 시나리오, 날짜, 실패 종류, stderr 끝 30줄, "/jobs/{id} 보고 /report/resend".
   - 하루 한 통. 자동 재시도는 없다.

## 1단계 — 한 것 (10-30)

**파일**

| | 파일 | 무엇 |
|---|---|---|
| A | `src/presentation/api/jobs.py` | 작업 실행기 — `python -u -m src …`, 한 번에 하나, 시간 초과에 죽이고 출력의 **끝**을 남김(스트림당 512KB) |
| A | `src/presentation/api/assembly.py` | `create_app` — 시계·env·루트를 받는다. 접두 `OPS`·`DIAG` |
| A | `src/presentation/api/app.py` | uvicorn이 찾는 `app` — 진짜 시계(app.json 시간대)·env(`.env` 있으면)·빌드 ARG를 여기서만 |
| A | `src/presentation/api/context.py`, `models.py` | 경로가 나눠 쓰는 것, 요청 본문(StrictModel — 모르는 키 422) |
| A | `src/presentation/api/routes_root.py`, `routes_ops.py`, `routes_diag.py` | 루트·운영·진단 |
| A | `src/presentation/api/diag_env.py`, `diag_net.py`, `diag_sites.py`, `diag_llm.py` | 앱 안 진단 |
| M | `src/__main__.py` | `mail send --to`(B5에서 당김) — mail 부분만 |
| M | `requirements.txt`, `.gitignore` | fastapi·uvicorn, `.claude` |
| A | `tests/presentation/api/*`, `tests/test_cli_mail.py` | 아래 |

**정한 세부**

- 응답: 실행기 경로는 200(끝남)·202(아직 — `GET /jobs/{id}?wait_s=`로 기다릴 수 있다)·409(`running`에 돌고 있는 작업)·
  422(본문). 앱 안 진단은 실패도 본문의 값으로 200 — config가 안 읽히면 `error` 한 칸.
- `wait_s` 기본은 55초(상한). `GET /jobs`는 출력 없이 가볍게, 출력은 `/jobs/{id}`.
- 자유 글은 옵션으로 읽히지 않게 넘긴다: 제목은 `--subject=값`, 질문은 `--` 뒤. 이름(scenario·gbm·fct)은 영숫자로
  시작하는 좁은 모양만. `to`는 config와 같은 검증(`MailConfig`)을 API에서도 탄다.
- `/ready`의 비밀은 **앱 전역(app.json·시나리오)만** 본다 — 한 사이트의 키가 비었다고 API 전체가 준비 안 됨이면,
  그 사이트를 고칠 진단까지 막힌다. 사이트 비밀은 `/diag/env`·`/diag/boot`.
- `/diag/net/trace`의 대상은 `mail`·`llm`·`redis`·`mongo`(사이트 하나) — 닫힌 목록. 예산 50초.
- `/diag/llm/raw`는 config의 주소·헤더·TLS·`trust_env_proxy`만 싣고 상한 50초. 보낸 헤더는 이름만 돌려준다.

**확인한 것**

- 새 테스트 116개(실행기 15 · 경로 51 · 진단 42 · 경계 2 · CLI `--to` 6 — 경계 둘 중 하나는 uvicorn을 **다른 폴더에서**
  실제로 띄워 이 리포의 `schedule --list`를 부른다). 3.13·3.11 둘 다 초록. 전체 스위트 초록.
- RED 스윕: 지키는 성질 29가지(`-u`·cwd·`PYTHONIOENCODING`·한 번에 하나·죽이기·출력 끝·50개·상한 셋·dry_run 기본·
  `--subject=`·`--`·이름 모양·`to` 검증·output 밖·HTML inline·ready 셋·해시·접두·URL 가림·빈 값·trace 멈춤·hop 주소
  위치·동시 수·꺼진 사이트·헤더 값·`--to` 대신/발송)를 하나씩 깨서 전부 빨개지는 것을 봤다.
- output 밖을 가리키는 심볼릭 링크 거부는 손으로 확인했다(테스트로는 못 둔다 — 테스트가 심볼릭 링크를 만들면 사내
  Windows에서 깨진다, `test_portability`).

**사내에서 볼 것(평가자)**

1. 사내 PyPI에 `fastapi`·`uvicorn`이 있는가(`pip install -r requirements.txt`).
2. 사내 Dockerfile의 CMD 한 줄·빌드 ARG(위 절) → `/version`의 `commit`·`built_at`, `/ready` 200, `/docs`.
3. 사내 원본과 대조: `report/run`(dry_run)·`mail/send`(dry_run, `to`)·`llm/check`·`llm/raw`·`doctor`·`doctor/all`·
   `env`·`net`·`net/egress`·`net/trace`. 응답 **모양**이 원본과 다른 곳이 있으면 원본 쪽 모양을 알려 달라.
4. 긴 `report/run` 중에 다른 경로 → 409와 돌고 있는 작업.

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
| `/interval/v1/ops` | `/internal/v1/ops` |

## 정한 것 (10-30 회신 — 전부 추천안)

1. **브랜치**: `claude/alarm-mail-api-w1zf74` 그대로(사내에서는 main이 아닌 별도 브랜치로 받는다).
2. **허용 범위**: `schema_report`, `schema_mail`, `__main__`의 mail·report 부분, `tests/`.
3. **장부의 날짜**: 기준일(어제) — HTML 파일 이름과 같다.
4. **실패 알림 기준**: B6에 적은 대로.
5. **`keep_days`**: 파일명 날짜 기준 최근 N개.
6. **장부가 있을 때의 `report run`**: 집계·LLM 전에 멈춘다. dry_run·no_mail은 장부를 무시한다.
7. **resend 뒤 장부**: API가 덮어쓴다(계기 `manual`).
8. **세부**: 메서드는 위 "경로와 메서드"의 규칙. 게이트웨이 사용자 메일 헤더는 사내 config가 넣는다(리포는 안 건드린다).
9. **진행**: 단계 하나 → 푸시 → 평가자 대조 보고를 기다린다.

## 고친 것 (10-30 회신)

- a) `/report/aggregate` 상한 180초 → **1800초**(28개 법인 집계, 사내 원본도 1800).
- b) Dockerfile을 쓰지 않는다 — CMD 한 줄과 빌드 ARG만 위 절로. 실행기의 리포 루트는 새 파일 위치에서 다시 계산.
- c) 경로 접두를 사내 원본과 같게.
