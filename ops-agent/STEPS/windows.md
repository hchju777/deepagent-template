# 사내(Windows)에서 돌리기

개발은 Linux/macOS에서 하더라도 **운영은 Windows**다. 그 차이에서 실제로 터지는
지점만 모았다. 각 항목은 "왜 터지는가"와 "무엇으로 막았는가"를 함께 적는다.

## 실행 명령

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -v
```

`.venv\Scripts\Activate.ps1`(활성화)은 PowerShell 실행 정책에 막히는 일이 잦다.
`python.exe`를 직접 부르면 **활성화 자체가 필요 없어** 그 문제를 통째로 우회한다.
Linux에서 `.venv/bin/python -m pytest`를 권하는 것과 같은 이유다 — 활성화를 잊고
시스템 파이썬으로 도는 사고도 같이 막힌다.

| | Linux/macOS | Windows |
|---|---|---|
| 파이썬 | `.venv/bin/python` | `.venv\Scripts\python.exe` |
| pip | `.venv/bin/pip` | `.venv\Scripts\pip.exe` |

---

## 함정 ①: 파일 인코딩 — 제일 위험하다

Windows에서 파이썬의 `open()` 기본 인코딩은 **로케일**이다. 한국어 Windows면
`cp949`. 우리 config에도 보고서에도 한국어가 들어가므로, `encoding="utf-8"`을
빠뜨린 곳은 **사내에서만** 터진다.

```python
path.read_text()                    # Linux: 통과   Windows: UnicodeDecodeError
path.read_text(encoding="utf-8")    # 어디서나 통과
```

이 실패는 제일 나쁜 모양을 한다 — Linux 테스트는 전부 초록인데 사내에 배포하면
깨진다. 사람 눈에만 기대면 반드시 샌다.

**막은 방법**: `tests/test_portability.py`가 `src/`와 `tests/`의 모든 파이썬
파일을 **AST로 파싱**해서 `open`/`read_text`/`write_text` 호출에 `encoding=`이
없으면 실패시킨다. 파일과 줄 번호를 짚어 준다:

```
AssertionError: Windows(cp949)에서만 깨질 파일 접근이 있다 — encoding="utf-8"을 붙여라:
  src/domain/_violation_demo.py:5 — read_text() 에 encoding= 이 없다
```

`grep`이 아니라 AST인 이유: `grep "open("`은 주석과 문자열 안의 `open(`도 잡고,
줄바꿈으로 이어진 호출은 놓친다. 바이너리 모드(`open(p, "rb")`)와
`read_bytes()`는 인코딩 개념이 없으므로 검사에서 빠진다.

## 함정 ②: 콘솔 리다이렉트

콘솔에 직접 출력하는 것은 괜찮지만(파이썬이 `WriteConsoleW`를 쓴다), 파일로
넘기면 로케일 인코딩을 타서 한글이 깨지거나 `UnicodeEncodeError`가 난다.

```powershell
.venv\Scripts\python.exe -m pytest > result.txt      # 한글 테스트 이름에서 터질 수 있다
set PYTHONUTF8=1                                     # 이 셸에서 UTF-8 모드
```

`PYTHONUTF8=1`은 ①까지 같이 해결한다. 그래도 코드의 `encoding=` 명시를 빼지
않는다 — **환경 변수에 기대는 안전은 그 변수를 안 건 사람에게만 유효**하고,
운영 서비스로 등록하면 그 셸 설정이 안 따라간다.

## 함정 ③: `zoneinfo`가 tz 데이터베이스를 못 찾는다

Windows에는 `/usr/share/zoneinfo`가 없어서 `ZoneInfo("Asia/Seoul")`이
`ZoneInfoNotFoundError`로 죽는다.

**막은 방법**: `requirements.txt`의 `tzdata` — 순수 파이썬 tz 데이터베이스다.
Linux에서는 안 쓰이고 Windows에서만 쓰인다.

## 함정 ④: config JSON 안의 Windows 경로

```json
{ "ca_bundle": "C:\certs\ca.pem" }      ← \c 가 JSON 이스케이프로 깨진다
{ "ca_bundle": "C:\\certs\\ca.pem" }    ← 맞지만 사람이 반드시 \ 하나로 쓴다
{ "ca_bundle": "C:/certs/ca.pem" }      ← 권장. Windows API가 / 를 받는다
```

코드 쪽은 `pathlib`만 쓴다. `"a" + "/" + "b"` 같은 문자열 조립을 하지 않으면
경로 구분자 문제는 애초에 생기지 않는다.

## 함정 ⑤: 사내 CA와 TLS — 7단계에서 크게 걸린다

Windows 인증서 저장소에 사내 루트 CA가 들어 있어 **브라우저와 사내 도구는 잘
된다.** 그런데 **파이썬은 그 저장소를 안 본다** — `certifi` 번들만 본다. 그래서
브라우저로는 열리는 게이트웨이가 파이썬에서는 `SSLCertVerificationError`로 죽는다.

검색하면 나오는 처방이 이것인데:

```python
ssl._create_default_https_context = ssl._create_unverified_context   # 쓰지 마라
```

이건 **프로세스 전역**을 끈다. LLM 하나 붙이려고 Redis·Mongo·Kafka·대상 REST의
인증서 검증까지 같이 꺼진다.

Windows에서는 더 나은 답이 있다:

```python
import ssl, truststore, httpx

ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)   # Windows 인증서 저장소를 그대로
httpx.AsyncClient(verify=ctx)                          # 이 커넥션 하나에만 적용
```

`truststore`는 Windows CryptoAPI를 통해 **OS 신뢰 저장소**를 본다. 사내 CA가
이미 거기 있으니 `.pem` 내보내기가 아예 불필요해진다. 전역 주입용
`inject_into_ssl()`도 있지만 쓰지 않는다 — 라이브러리 문서 자체가 "앱에서만
쓰고 라이브러리에서는 쓰지 말라"고 경고하고, 전역을 건드리지 않는다는 우리
규율에도 어긋난다.

7단계에서 세 가지를 config로 고르게 만든다: `truststore`(권장) /
`ca_bundle`(.pem 경로) / `tls_verify: false`(번들을 구하기 전 임시, 켜지면
경고를 찍는다).

## 함정 ⑥: 같은 3.11인데 동작이 다르다

사내가 **3.11.3**, 개발 환경이 **3.11.15**였고 argparse가 서로 다르게 동작했다.
하위 파서(`peek`)에 단 옵션이 전역 옵션의 값을 덮어쓰는지가 갈려서, 여기서는
통과하는 테스트 다섯 개가 사내에서 깨졌다.

교훈은 argparse에 한정되지 않는다: **표준 라이브러리의 "이렇게 하면 되는" 트릭은
마이너 버전이 같아도 믿을 수 없다.** 의도를 코드로 명시하면 버전에 상관없이
같게 동작한다.

```python
# 믿지 않는다 — argparse가 하위 파서 결과를 합치는 방식에 기댄다
target.add_argument("--gbm", default=argparse.SUPPRESS)

# 명시한다 — dest를 분리하고 직접 합친다
target.add_argument("--gbm", dest="gbm_sub", default=None)
args.gbm = args.gbm_sub or args.gbm
```

`tests/test_cli.py`가 "하위 명령이 전역과 같은 dest를 쓰지 않는다"를 구조로
단정한다 — 나중에 누가 "단순화"하려고 되돌리는 것을 막는다.

> 이 사고가 드러난 방식도 기록해 둔다. 테스트가 `build_parser().parse_args()`를
> 직접 불러서 **프로덕션의 병합 단계를 건너뛰고 있었다.** 그래서 실제 명령줄이
> 깨지는 조합이 테스트에서는 통과했다. 지금은 `parse_args()` 하나가 유일한
> 입구이고 테스트도 그것을 쓴다 — **테스트는 프로덕션과 같은 문을 지나야 한다.**

## 걸리지 않는 것

Redis·MongoDB **서버**를 Windows에 설치할 필요는 없다. 우리에게 필요한 것은
클라이언트뿐이고(`redis-py`·`pymongo`·`httpx` 전부 Windows에서 정상), 서버는
사내 대상 시스템이다.
