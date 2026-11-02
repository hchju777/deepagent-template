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
  입력은 전부 **쿼리 파라미터**다(사내 원본과 같다 — Swagger에서 칸·선택으로 보인다, 11-01 회신 a). 리스트는 반복
  쿼리(`to=a&to=b`), 안 주면 기본값이다. JSON 본문은 읽지 않는다 — 본문에 `dry_run: false`를 실어도 기본값(true)으로 돈다.

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
   - API: `POST /report/run`에 `force` 추가(쿼리 체크박스 — 장부가 있고 force가 false면 409와 장부).
   - **종료 코드를 나눈다**(정한 것 4 — 11-01 사내 실측: 법인 둘의 Mongo가 안 닿아 `report aggregate`·`run`이 끝까지 돌고도
     종료 코드 1 → 작업 `failed`). 일부 법인 못 읽음 = **성공-경고** 전용 코드, 이미 보냄 = 전용 코드. `aggregate`도 같은 규칙
     (지금 둘 다 `return 1 if facts.unavailable`). **2는 못 쓴다** — argparse가 인자 오류에 2를 낸다(잘못 친 명령이 경고로
     보인다). 코드 값과 작업 상태에서의 모양(`ok` + 경고 칸인가, 상태 하나를 더하는가)은 3단계 예고에서 정한다.
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

**사내 pytest 뒤 고친 것**

- `test_TCP가_안_붙으면…`이 사내 PC(Windows)에서 실패했다 — 닫힌 포트에 Windows는 SYN을 다시 보내 2초 상한 안에 거부가
  안 오고 `시간 초과(2초)`가 된다. 코드는 맞게 TCP에서 멈췄고 테스트가 **이유**(거부)까지 단정한 것이 틀렸다. 이제 "TCP에서
  멈췄다"만 보고, 시간 초과 문구는 연결을 붙잡아 두는 테스트로 따로 본다(여기서 그 모양을 재현해 옛 단정이 거짓인 것을 확인).
- 같은 종류의 가정 둘을 같이 걷었다: `python3`이라는 이름(Windows엔 없거나 스토어 별칭), 자식 cwd의 글자 비교(→ `samefile`).
- 테스트용 로컬 서버가 받은 연결의 처리를 기다리지 않고 닫혀, 가끔 다음 테스트에서 asyncio 경고가 났다 — 처리가 끝난 뒤 닫는다.

**사내에서 볼 것(평가자)**

1. 사내 PyPI에 `fastapi`·`uvicorn`이 있는가(`pip install -r requirements.txt`).
2. 사내 Dockerfile의 CMD 한 줄·빌드 ARG(위 절) → `/version`의 `commit`·`built_at`, `/ready` 200, `/docs`.
3. 사내 원본과 대조: `report/run`(dry_run)·`mail/send`(dry_run, `to`)·`llm/check`·`llm/raw`·`doctor`·`doctor/all`·
   `env`·`net`·`net/egress`·`net/trace`. 응답 **모양**이 원본과 다른 곳이 있으면 원본 쪽 모양을 알려 달라.
4. 긴 `report/run` 중에 다른 경로 → 409와 돌고 있는 작업.

## 2단계 — 한 것 (11-01)

**파일**

| | 파일 | 무엇 |
|---|---|---|
| A | `src/presentation/api/scheduler.py` | `ApiScheduler` — 기존 `run_all`을 lifespan 태스크로. 잡은 실행기에 `report run --scenario <이름>`(1800초) |
| M | `src/presentation/api/assembly.py` | lifespan이 스케줄러를 띄우고 내린다. `sleeper`(가짜 시계용 이음매) |
| M | `src/presentation/api/routes_ops.py` | `GET /schedule`, `POST /schedule/pause`·`/resume`. 입력을 쿼리로(a) |
| M | `src/presentation/api/routes_diag.py`, `models.py` | 입력을 쿼리로(a). `to` 오류 문구가 두 번 붙던 것 |
| M | `src/presentation/api/routes_root.py`, `context.py` | `/ready`가 진짜 스케줄러 태스크를 본다 |
| M | `src/__main__.py` | `mail send` 확인 본문이 실제로 받는 쪽을 적는다(b) — mail 부분만 |
| A·M | `tests/presentation/api/test_scheduler.py` 외 | 아래 |

**정한 세부**

- 발사 때 수동 작업이 돌고 있으면 **끝날 때까지 기다렸다** 들어간다(사람 요청은 여전히 409). 빈 뒤에 사람이 먼저 잡으면
  다시 기다린다.
- 일시정지 중의 발사는 건너뛰고 최근 결과에 `paused`로 남는다. 다음 시각은 계속 보인다(`on: false`). 재시작하면 켜진다.
- `GET /schedule`: `running`(태스크 생존), `paused`, `error`, 시나리오마다 `enabled`(config)·`on`(켜짐 = config && 일시정지 아님)·
  `kind`·`cron`·`next_run`·`last`(`status` ok|failed|timeout|paused — 3단계에서 "이미 보냄"이 는다, `scheduled`·`started`·`took_s`·
  `job_id`·`exit_code`·`detail`).
- `/ready`의 스케줄러 칸: 태스크가 살아 있으면 통과. 시나리오를 못 읽어 못 떴거나, 루프가 멈춤 신호 없이 끝났으면 503과 이유.
  **돌릴 시나리오가 없는 것은 죽은 것이 아니다** — 멈춤 신호까지 산다(꺼 둔 것으로 503이 나면 틀린 경보다).
- 시나리오는 뜰 때 한 번 읽는다 — config가 바뀌면 다시 띄운다.
- 내릴 때 리포트 작업을 기다리던 중이면 5초 안에 스케줄러만 끊는다. 작업 자체는 실행기 것이라 같이 죽이지 않는다(그 작업의
  상한이 정리한다).
- (b) 확인 본문: "받는 사람: `--to`로 지정한 주소(config의 mail.recipients를 덮어씀) — N명: …" 또는
  "받는 사람: config의 mail.recipients — N명: …". `--body`·`--file`을 주면 그대로.

**확인한 것**

- 테스트 134개(1단계 117 + 스케줄러 12 · 쿼리 칸/본문 무시/오류 문구 3 · 확인 본문 3 − 옮긴 1). 3.13·3.11 둘 다 초록. 전체 스위트 초록.
- 실제 uvicorn을 띄워 lifespan이 스케줄러를 띄우는 것(`GET /schedule`의 `running`·`next_run`)과 종료 신호에 10초 안에
  내려오는 것까지 본다.
- RED 스윕 17가지(lifespan 띄움·내림, 수동 기다림, 일시정지, `--scenario`, 1800초, 죽은 루프, 돌릴 것 없음, 내릴 때 끊음,
  실패 기록, 일시정지 중 다음 시각, ready, 쿼리 둘, 오류 문구, 본문 둘). 처음에 "돌릴 것 없음"이 초록이었다 — 테스트가
  스케줄러 태스크가 첫 걸음을 떼기 전에 `/ready`를 물었다(ASGI 요청은 진짜 I/O가 없어 루프를 안 돌린다). 루프에 자리를
  내준 뒤 보게 고쳐 빨개지는 것을 확인했다.

**사내 pytest 뒤(11-01) — 짐작으로 고친 세 라운드와 되돌림**

- `test_jobs.py`에서 하나가 깨졌다는 보고에 **어느 테스트인지·오류 문구를 받지 않고** 원인을 짐작했다(Windows venv 런처가
  손자를 남긴다). 그 짐작으로 만든 손자 테스트가 이후 사내에서 깨진 전부다 — 처음 깨진 테스트는 아직 모른다.
- 그 뒤 두 라운드(`taskkill /T` → Toolhelp 스냅샷 → 다시 훑기)는 여기서 돌려 볼 수 없는 Windows 코드를 쌓은 것이다.
  운영(Linux)에는 없는 경우를 막으려 했다: API가 부르는 CLI는 우리 파이프를 물려받는 손자를 띄우지 않고, venv 런처는
  Job Object로 직계 자식을 같이 죽인다. **되돌렸다** — Windows는 `proc.kill()` 하나.
- 남긴 것(여기서 재현·확인한 것만): 3.11의 `proc.wait()`가 파이프가 닫혀야 돌아오는 것에 대한 상한(죽인 뒤 기다림·파이프
  읽기, 못 닫은 transport는 직접 닫는다)과, Linux에서 새 세션으로 띄워 그룹째 죽이기. 손자 테스트는 Linux에서만 돈다.
- 되돌린 뒤 사내 실행(11-02): `test_시간을_넘기면_죽이고_그때까지의_출력을_남긴다` — `assert '시작' in ''`. 처음(`8a3e812`)에 깨진 것도
  이것으로 본다. 여기서 가짜 CLI가 뜨는 데 1.2초 걸리게 해 **같은 모양을 재현했다**(상한 1초 → 찍기 전에 죽어 stdout `''`).
  고친 것(테스트만): 그 기계에서 자식이 뜨는 시간을 먼저 재서 상한을 그 몇 배로. 실패하면 기동·상한·stdout·stderr를 같이
  보인다 — 둘 다 비었으면 기동이 느린 것, stderr만 있으면 `-u`가 안 먹은 것이다. 덤으로 찾은 것: 개발 컨테이너에
  `PYTHONUNBUFFERED=1`이 전역으로 걸려 있어 이 시험이 여기서는 `-u`를 못 봤다(`-u`를 빼도 초록) — 시험 안에서 지운다.
- 다음부터: Windows에서만 나는 실패는 **`FAILED`·`E` 줄을 받기 전에 고치지 않는다.** 고칠 때도 운영(Linux)에서 필요한지
  먼저 따진다. handover의 "재현이 먼저다 — 짐작으로 고친 라운드는 헛일이었다"를 그대로 반복했다.

**사내에서 볼 것(평가자)**

1. 로컬 uvicorn을 띄운 채 `GET /schedule` — `running: true`, `next_run`이 다음 평일 08:00(KST).
2. 사내 시나리오를 잠깐 `kind: interval`·`interval_seconds: 60`으로(사내 사본만) — 1분마다 `report run --scenario …`가
   `/jobs`에 생기고 `last`가 바뀌는지. 그동안 Swagger에서 `report/run`을 눌러 두면 스케줄이 기다렸다 도는지. **dry_run이
   아니다** — 스케줄 발사는 진짜 발송이므로 `mail.enabled`·수신자를 확인하고(또는 `--to` 시험 주소로) 본다.
3. `pause` → 다음 발사가 `paused`로 건너뛰는지, `resume` → 다시 도는지.
4. Swagger에서 POST 경로가 전부 칸(쿼리)으로 보이는지, `to`가 여러 칸으로 들어가는지.
5. `llm/ask`의 `prompt`가 쿼리라 길이가 URL에 실린다 — 한글 2000자는 인코딩하면 약 18KB다. Ingress의 요청 줄 상한(nginx 기본
  8KB)에 걸리면 414가 난다. 사내 원본도 같은 모양이면 그대로 두고, 긴 질문이 필요하면 알려 달라.

## 3단계 — 한 것 (11-02)

회신(11-02): 정한 것 4 확정 — "이미 보냄"은 전용 종료 코드, 일부 법인을 못 읽고 메일이 나간 것은 성공(경고). `GET /schedule`의
최근 결과도 성공·실패·이미 보냄·경고를 나눈다. 덧붙인 제안: `GET /schedule`에 오늘 기준일 장부 있음/없음 — 08:00 근처에
파드가 다시 뜨면 그날 슬롯은 건너뛰어지고 실패 알림도 안 가므로, 사람이 Swagger 한 곳에서 알아채게.

**파일**

| | 파일 | 무엇 |
|---|---|---|
| A | `src/report/exit_codes.py` | 0 성공 · 1 실패 · **3 성공-경고** · **4 이미 보냄** (2는 argparse의 인자 오류라 비운다) |
| A | `src/report/ledger.py` | `output/sent/<시나리오>-<기준일>.json` — 쓰기(임시 파일 → 바꿔 끼우기)·읽기·모아 보기. 깨져도 "있음" |
| M | `src/report/publish.py` | `subject_for_day`·`report_path` — 재발송이 그날 파일에 원래와 같은 제목을 붙인다 |
| M | `src/__main__.py` | `report run`: 집계 **전에** 장부를 보고 있으면 4, 실제 발송 뒤 장부, `--force`·`--trigger`·`--job-id`, 종료 코드 표. `report aggregate`: 못 읽은 법인은 3 |
| M | `src/presentation/api/jobs.py` | 종료 코드 → 상태(ok·warning·already_sent·failed·timeout), 미리 받는 작업 id, 끝난 뒤 처리(`on_done`) |
| M | `src/presentation/api/scheduler.py` | 발사에 `--trigger schedule --job-id`, 발사 중 표시, 최근 결과는 실행기 상태 그대로 |
| A | `src/presentation/api/sent.py` | 시나리오 고르기·기준일(CLI와 같은 `build_window`)·오늘 상태 |
| M | `src/presentation/api/routes_ops.py`, `models.py` | `report/run`의 `force`·409, `report/resend`, `report/sent`, `report/sent/{date}`, `/schedule`의 `today` |

**정한 세부**

- 장부는 `mail.status == "sent"`일 때만 쓴다(꺼짐·실패·dry-run·no-mail은 안 쓴다). 메일은 나갔는데 장부를 못 쓰면 **실패**
  (다음에 누가 다시 돌리면 두 통이 된다).
- `report/run`의 409는 **보내는 실행**(dry_run·no_mail 아님)이고 force가 아닐 때만. CLI도 같은 장부를 보고 4로 멈추므로 API의
  확인은 30분짜리 작업을 안 띄우고 바로 답하려는 것이다.
- `report/resend`(date — 기준일, scenario, force): 그날 HTML이 없으면 404, `mail.enabled=false`면 409(`mail send`가 건너뛰고도
  0으로 끝나 그걸 발송으로 알면 안 된다), 장부가 있으면 409. 보낸 뒤 장부는 **API가** 쓴다(작업이 끝날 때 — 202로 먼저
  돌아갔어도). 쿼리 이름은 `date`(필드는 `day` — pydantic에서 타입 이름과 부딪혀 별칭).
- `GET /schedule`의 시나리오마다 `today`: `day`(오늘 돌면 쓸 기준일) · `sent` · `ledger` · `slot`(오늘 cron 시각, 주말·interval이면
  null) · **`missed`** = 슬롯이 지났고 · 장부가 없고 · 오늘 발사 기록이 없고 · 지금 발사 중이 아니고 · 일시정지가 아닐 때.
  오늘 발사했는데 실패한 것은 `missed`가 아니라 최근 결과의 `failed`다(6단계 알림). 건너뛴 날 다시 보내는 것은 사람이 정한다.
- 손대지 않은 것: CLI `schedule`(옛 상주 명령)은 장부를 안 본다 — 컨테이너는 API의 스케줄러만 띄운다. `report render`의 종료
  코드도 그대로(API가 부르지 않는다).

**확인한 것**

- 테스트 추가: 장부 6, CLI 장부·종료 코드 13, API(409·resend·sent·최근 결과 나눔·today) 23, 실행기(종료 코드 표·미리 받는 id·
  뒤처리 순서) 7. 바뀐 모양에 맞춘 것: 기존 CLI 테스트 둘(못 읽은 법인 → 3), 경로·스케줄 테스트의 argv.
- RED 스윕 27가지 전부 빨개진다(장부를 안 봄·dry-run도 봄·force 무시·안 씀·실패에도 씀·계기 무시·못 써도 성공·법인 실패를
  실패로(run·aggregate)·경고를 2로·깨진 장부를 없음으로·돌린 날로·실행기 경고를 실패로·뒤처리 순서·스케줄 계기·API 409 셋·
  resend 넷·missed 넷·주말 슬롯·API 기준일).

**사내에서 볼 것(평가자)**

1. `report/run`(dry_run=false, 시험 수신인으로) → `output/sent/`에 장부, 다시 누르면 409와 장부, force면 다시 나가고 장부가 바뀐다.
2. 법인 일부가 안 붙는 날 `report/run`·`report/aggregate` → 작업 상태 `warning`(종료 코드 3), 메일은 나간다.
3. `report/resend?date=<기준일>` → 그날 HTML이 원래 제목으로 나가고 장부의 계기 `manual`.
4. 08:00 슬롯이 지난 뒤 파드를 다시 띄우고 `GET /schedule` → 그날 장부가 없으면 `today.missed: true`.

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

## 고친 것 (11-01 — 1단계 검증 회신, 2단계에 붙여서)

- a) POST 입력을 JSON 본문이 아니라 쿼리 파라미터로(사내 원본과 같게). dry_run 기본 true 유지, `to`는 반복 쿼리. 3단계 `force`도
  쿼리. `wait_s` 기본 55는 그대로.
- b) `mail send` 확인 본문의 "수신자는 config의 mail.recipients가 정합니다(본문이 바꿀 수 없습니다)" — `--to`를 쓰면 틀린 말이다.
  실제로 받는 쪽(덮어씀/설정)을 적는다.
