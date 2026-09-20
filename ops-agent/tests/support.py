"""리포트 테스트의 공용 재료 — 고정 날짜, 문서 만들기, 가짜 Mongo, 픽스처.

## 왜 conftest가 아니라 보통 모듈인가

픽스처는 conftest에 있으면 같은 디렉터리 아래에서 자동으로 보이지만 **디렉터리를
넘지 않는다.** 그래서 `tests/presentation`이 `tests/report`의 픽스처를 쓰려면
import가 필요하고, 그 import를 상대 경로(`from ..report.conftest import`)로 쓰면
`tests/__init__.py` 하나가 없는 환경에서

    ImportError: attempted relative import beyond top-level package

로 **테스트 수집 자체가 실패한다**(실제로 사내에서 났다. `__init__.py`가 있는
디렉터리까지 거슬러 올라가 패키지 이름을 정하는 pytest의 규칙 때문이고, 중간에
하나라도 빠지면 `..`가 최상위를 넘는다).

보통 모듈 + **절대 import**는 그 조건에 걸리지 않는다 — `ops-agent/`가 sys.path에
있으면 `tests`는 암묵적 namespace package로도 import되기 때문이다. 그리고 그건
모든 테스트가 이미 `from src...`로 쓰고 있는 것과 같은 전제다.

`tests/test_portability.py`가 테스트 트리에 상대 import가 다시 생기지 않는지 지킨다.
"""
from datetime import date, datetime
import functools
import subprocess
import tempfile
from pathlib import Path

import pytest

from src.config.schema_report import ReportScenario, SourceSpec, Thresholds, WindowSpec
from src.domain.envelope import ProbeResult
from src.domain.ports import MongoReaderPort
from src.report.window import build_window

TODAY = date(2026, 9, 7)          # 월요일 — 어제가 금요일(2026-09-04)이 되도록


REPO_ROOT = Path(__file__).resolve().parent.parent


def env_references(root: Path) -> set[str]:
    """config 트리가 `${...}`로 참조하는 env 이름 전부.

    로더와 **같은 정규식**을 쓴다 — 여기서 패턴을 베끼면 로더가 인식하는 참조를
    테스트는 못 보는 상태가 생긴다(`${MY-KEY}`를 놓쳤던 적이 있다).
    """
    from src.config.envresolve import _REFERENCE

    names: set[str] = set()
    for path in sorted(root.rglob("*.json")):
        names |= set(_REFERENCE.findall(path.read_text(encoding="utf-8")))
    return names


def placeholder_env(names) -> dict[str, str]:
    """이름에서 형식만 맞는 가짜 값. 접속은 하지 않으므로 모양만 맞으면 된다."""
    return {name: ("https://x/v1" if name.endswith(("_URL", "URL")) else "x")
            for name in names}


def set_real_config_env(monkeypatch) -> None:
    """리포의 **실제 `config/`** 로 CLI를 돌릴 때 필요한 env를 전부 채운다.

    한 곳에 모은 이유: 이 목록이 네 파일에 복사돼 있었고, config에 참조가 하나 늘자
    **네 군데가 동시에 깨졌다.** 실패 모양이 "설정이 안 치환됐다"가 아니라
    "관계없는 CLI 테스트가 1을 돌려준다"여서 원인이 안 보였다.

    **키는 `config/`에서 뽑는다. `.env.example`이 아니다.**
    처음엔 `.env.example`을 읽었는데, 사내 트리에는 **그 파일이 없다** — 운영은
    `.env`만 둔다. 그래서 사내에서 CLI 테스트가 무더기로 깨졌고, 사람이 매번 테스트
    코드를 고쳐 쓰고 있었다.

    config가 유일한 진실 소스다. 거기 있는 참조는 거기서 읽으면 되고, 그러면
    **어떤 트리에서도 같게 돈다.**
    """
    for key, value in placeholder_env(env_references(REPO_ROOT / "config")).items():
        monkeypatch.setenv(key, value)
YESTERDAY = date(2026, 9, 4)


@pytest.fixture
def clock():
    return lambda: datetime(2026, 9, 7, 8, 0, 0)


@pytest.fixture
def source() -> SourceSpec:
    return SourceSpec(collection="alarm", date_field="occ_date")


@pytest.fixture
def window(source):
    return build_window(WindowSpec(), today=TODAY)


def doc(day: date, *, hour: int = 9, plant: str = "gumi", gbm: str = "mx",
        line: str = "P222", line_name: str = "조립2라인", scen: str = "S01",
        scen_name: str = "재고 불일치", status: int = 0, **extra) -> dict:
    body = {"occ_date": datetime(day.year, day.month, day.day, hour).strftime(
                "%Y-%m-%d %H:%M:%S"),
            "gbm": gbm, "plant": plant, "part_code": "PN100",
            "line_code": line, "line_name": line_name,
            "scen_id": scen, "scen_name": scen_name, "status": status}
    body.update(extra)
    return body


class FakeMongo(MongoReaderPort):
    """호출부가 **무엇을 물었는지** 기록하는 가짜.

    `StubMongoReader`를 쓰지 않는 이유: 스텁은 대상 시스템을 흉내내는 물건이고,
    여기서 보고 싶은 것은 "우리가 무엇을 물었는가"다. 필터가 정확히 나가는지를
    스텁의 질의 흉내를 통과한 **결과로** 확인하면, 흉내가 틀릴 때 테스트가
    거짓 초록을 낸다.
    """

    def __init__(self, documents=None, *, fail: str | None = None,
                 truncated: str | None = None, clock=None):
        self.documents = documents or []
        self.fail = fail
        self.truncated = truncated
        self.calls: list[dict] = []
        self._clock = clock or (lambda: datetime(2026, 9, 7, 8, 0, 0))

    async def find(self, collection, filter, *, sort=None, limit=None, projection=None):
        self.calls.append({"collection": collection, "filter": filter,
                           "limit": limit, "projection": projection})
        source = f"fake-mongo:{collection}"
        if self.fail:
            return ProbeResult.failed(self.fail, source=source, clock=self._clock)
        rows = self.documents
        if projection:
            keep = set(projection)
            rows = [{k: v for k, v in row.items() if k in keep} for row in rows]
        return ProbeResult.succeeded(rows, source=source, clock=self._clock,
                                     truncated_reason=self.truncated)

    async def list_collections(self):
        return ProbeResult.succeeded(sorted(self.documents and ["alarm"] or []),
                                     source="fake-mongo:collections", clock=self._clock)

    async def count(self, collection, filter):
        return ProbeResult.succeeded(len(self.documents), source="fake-mongo",
                                     clock=self._clock)


def scenario(**overrides) -> ReportScenario:
    body = {"kind": "alarm_daily", "title": "일일 알람 리포트",
            "source": {"collection": "alarm", "date_field": "occ_date"},
            "scope": {"gbms": ["mx"], "sites": ["mx/gumi", "mx/sevt"]}}
    body.update(overrides)
    return ReportScenario.model_validate(body)


def facts_from(rows, *, window, source, thresholds=None, sites=(), gbms=None):
    from src.report.facts import Facts, SiteOutcome
    if not sites:
        sites = (SiteOutcome(gbm="mx", fct="gumi", status="ok", kept=len(rows)),)
    if gbms is None:
        # 선언하지 않으면 사이트에서 뽑는다 — 테스트가 매번 적지 않아도 되게.
        # 프로덕션에서는 config가 선언한다(`collect`가 scope.gbms를 싣는다).
        seen = []
        for site in sites:
            if site.gbm not in seen:
                seen.append(site.gbm)
        gbms = tuple(seen)
    return Facts(window=window, source=source,
                 thresholds=thresholds or Thresholds(),
                 rows=tuple(rows), sites=tuple(sites), gbms=tuple(gbms))


def running_source(obj, *, marker: str = "") -> str:
    """**그 트리에서 실제로 돌고 있는** 코드를 보여 준다.

    재현이 안 되는 실패에서 "안 걸렸다"만으로는 **트리가 낡은 것인지 환경이 다른
    것인지** 구분할 수 없다. 그러면 사람이 출력을 손으로 옮겨 오고 한 번 더 묻는
    왕복이 생기는데, 이 리포에서 그게 반복됐다.

    `marker`를 주면 그 문자열이 소스에 있는지도 함께 적는다. **marker는 검사 자체를
    가리키는 표현이어야 한다** — 두 번 틀렸다:

    - `raise` 본문의 메시지 문자열을 썼더니, 검사를 꺼도 문자열은 남아서 항상 "있다"
    - 함수 안 다른 분기에도 있는 말을 썼더니, 고쳐야 할 분기가 낡아도 "있다"

    확신이 없으면 marker를 주지 마라. **소스 전문이 이미 답을 담고 있다.**
    """
    import inspect

    try:
        source = inspect.getsource(obj)
        where = inspect.getfile(obj)
    except (OSError, TypeError) as exc:
        return f"(소스를 읽을 수 없다 — {exc})"
    head = f"  파일: {where}\n"
    if marker:
        head += f"  '{marker}' 있는가: {marker in source}\n"
    return head + "\n".join(f"    {line}" for line in source.splitlines())

# ── 진짜 submodule을 만드는 픽스처 ────────────────────────────────────
#
# 로컬 경로를 submodule로 붙이는 것은 git 2.38.1부터 **기본으로 막혀 있다**
# (CVE-2022-39253). 그래서 `-c protocol.file.allow=always`가 필요한데, 이게
# 빠지면 실패 메시지가 `fatal: transport 'file' not allowed`라 **테스트가 뭘
# 검증하다 실패했는지 안 보인다.** 그래서 한 군데서만 만든다 — 세 파일이 각자
# 베끼면 언젠가 한 곳만 고쳐지고, 그때 그 파일만 사내에서 빨개진다.

GIT_LOCAL_SUBMODULE = ("-c", "protocol.file.allow=always")


def git(*args, cwd, check: bool = True) -> subprocess.CompletedProcess:
    """테스트용 git 호출. **실패하면 git이 한 말을 그대로 들고 죽는다.**

    `check=True`의 `CalledProcessError`는 종료코드만 말하고 stderr는 삼킨다.
    사내에서 실패했을 때 "왜"가 안 보이면 사람이 그걸 타이핑해 옮겨야 한다 —
    이 리포가 이미 두 번 그렇게 시간을 썼다.
    """
    done = subprocess.run(["git", *GIT_LOCAL_SUBMODULE, *args], cwd=str(cwd),
                          capture_output=True, text=True)
    if check and done.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} (cwd={cwd}) 가 {done.returncode}로 실패했다\n"
            f"  stderr: {(done.stderr or '').strip() or '(없음)'}\n"
            f"  stdout: {(done.stdout or '').strip() or '(없음)'}\n"
            f"  {_git_version()}")
    return done


@functools.lru_cache(maxsize=1)
def _git_version() -> str:
    done = subprocess.run(["git", "--version"], capture_output=True, text=True)
    return (done.stdout or "").strip() or "git --version이 아무 말도 안 했다"


@functools.lru_cache(maxsize=1)
def local_submodule_support() -> str:
    """이 환경이 **로컬 경로를 submodule로 붙일 수 있나.** 되면 빈 문자열.

    되는지 자체가 환경의 능력이지 우리 코드의 성질이 아니다. 못 하는 환경에서
    빨간불을 내면 사람은 "내 코드가 깨졌나"를 먼저 의심하고, 그게 정확히 이번에
    일어난 일이다. 그래서 **건너뛰되 이유를 들고** 건너뛴다.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            lib = make_git_repo(root / "lib")
            parent = make_git_repo(root / "parent")
            git("submodule", "add", "-q", str(lib), "vendor/lib", cwd=parent)
        except AssertionError as exc:
            return str(exc)
    return ""


@pytest.fixture
def needs_local_submodules():
    """로컬 submodule을 못 만드는 환경에서는 **이유를 찍고** 건너뛴다."""
    why = local_submodule_support()
    if why:
        pytest.skip(f"이 환경은 로컬 경로를 submodule로 못 붙인다 —\n{why}")


@pytest.fixture
def local_submodules_allowed(monkeypatch):
    """제품 코드가 부르는 git에도 같은 허락을 넘긴다.

    `sync()`는 `-c`를 받을 자리가 없다(제품 코드에 테스트 사정을 넣을 수는 없다).
    `GIT_CONFIG_COUNT`/`KEY`/`VALUE`는 **하위 프로세스까지 따라가는** 설정이라
    이 자리에 맞는다 — 클론 안에 `git config`를 써 두는 것보다 환경에 덜 기댄다.
    """
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")


def make_git_repo(root: Path, *, origin: str = "") -> Path:
    """커밋 하나짜리 레포. 사용자 이름은 **로컬로** 박는다 — 전역 설정이 없는
    CI/사내 PC에서 `git commit`이 그냥 죽는다."""
    root.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "t@t", cwd=root)
    git("config", "user.name", "t", cwd=root)
    if origin:
        git("remote", "add", "origin", origin, cwd=root)
    (root / "a.py").write_text("x\n", encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-qm", "first", cwd=root)
    return root
