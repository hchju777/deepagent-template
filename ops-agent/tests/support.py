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


# ── submodule 픽스처 — **허락이 필요 없게** 만든다 ─────────────────────
#
# 처음엔 `git submodule add <로컬 경로>`로 만들었다. 그건 git 2.38.1부터 막힌
# 유일한 명령이고(CVE-2022-39253), 그래서 `-c protocol.file.allow=always`·
# `GIT_CONFIG_*`·능력 검사를 차례로 덧대게 됐다 — **내가 만든 문제를 내가 막는
# 코드**였다. 더 나쁜 것은 그걸 전역 `protocol.file.allow=always`가 켜진 기계에서
# 검증했다는 점이다. 그러면 여기선 늘 초록이고 남의 기계에서만 깨진다.
#
# `.gitmodules`는 그냥 파일이고 gitlink는 트리 항목(mode 160000)이다. 둘 다 손으로
# 만들 수 있고, 그러면 **어떤 허락도 필요 없다.** 채우는 것도 평범한 `git clone`이면
# 된다 — 막히는 것은 submodule 전송이지 사람이 직접 하는 클론이 아니다.


def git(*args, cwd, check: bool = True) -> subprocess.CompletedProcess:
    """테스트용 git 호출. **실패하면 git이 한 말을 그대로 들고 죽는다.**

    `check=True`의 `CalledProcessError`는 종료코드만 말하고 stderr를 삼킨다.
    남의 기계에서 실패했을 때 "왜"가 안 보이면 사람이 그걸 타이핑해 옮겨야 한다.
    """
    # 로캘이 아니라 **UTF-8**로 읽는다. 한국어 Windows는 cp949로 디코딩하는데
    # git은 UTF-8로 뱉어서, 경로에 한글이 있으면 여기가 먼저 죽는다(재현함).
    done = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if check and done.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} (cwd={cwd}) 가 {done.returncode}로 실패했다\n"
            f"  stderr: {(done.stderr or '').strip() or '(없음)'}\n"
            f"  stdout: {(done.stdout or '').strip() or '(없음)'}\n"
            f"  {_git_version()}")
    return done


@functools.lru_cache(maxsize=1)
def _git_version() -> str:
    done = subprocess.run(["git", "--version"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return (done.stdout or "").strip() or "git --version이 아무 말도 안 했다"


def make_git_repo(root: Path, *, origin: str = "") -> Path:
    """커밋 하나짜리 레포. 사용자 이름은 **로컬로** 박는다 — 전역 설정이 없는
    기계에서 `git commit`이 그냥 죽는다."""
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


def git_config_value(path) -> str:
    """git config 값에 넣을 수 있는 모양으로. **`\\`는 이스케이프 문자다.**

    Windows 경로를 그대로 적으면 `C:\\Users\\t\\libs`의 `\\t`가 탭이 되고
    git이 `fatal: bad config line`으로 죽는다(재현함). 증상은 `git submodule init`
    실패다 — 원인이 `.gitmodules` 한 줄에 있다는 게 안 보인다.
    git은 Windows에서도 `/`를 받으므로 구분자를 바꾼다.
    """
    return str(path).replace("\\", "/")


def declare_submodule_at(parent: Path, sub: Path, at: str, *, path: str,
                         message: str = "add submodule") -> str:
    """`.gitmodules` + gitlink를 **손으로** 만들어 커밋한다.

    `git submodule add`가 하는 일이 정확히 이 둘이고, 우리 코드가 읽는 것도 이
    둘뿐이다(`git show <커밋>:.gitmodules`, `git ls-tree <커밋> -- <경로>`).
    전송이 일어나지 않으므로 file 프로토콜 허락이 필요 없다.
    """
    (parent / ".gitmodules").write_text(
        f'[submodule "{path}"]\n\tpath = {path}\n\turl = {git_config_value(sub)}\n',
        encoding="utf-8")
    git("add", ".gitmodules", cwd=parent)
    git("update-index", "--add", "--cacheinfo", f"160000,{at},{path}", cwd=parent)
    git("commit", "-qm", message, cwd=parent)
    return path


def populate_submodule(checkout: Path, sub: Path, path: str) -> None:
    """submodule 자리를 실제로 채운다 — 평범한 clone + 로컬 등록.

    **등록(`git submodule init`)이 빠지면 안 된다.** `git grep --recurse-submodules`는
    등록된 것만 들여다보고, 안 된 것은 `.git`이 있어도 **조용히 건너뛴다**(측정함).
    """
    git("clone", "-q", str(sub), str(checkout / path), cwd=checkout)
    git("submodule", "init", cwd=checkout)


@pytest.fixture
def local_submodules_allowed(monkeypatch):
    """**딱 한 테스트만** 이게 필요하다 — `sync`가 진짜로 채우는지 보는 것.

    거기서 도는 것은 제품 코드라 `-c`를 끼울 자리가 없고, 픽스처의 submodule url이
    로컬 경로라 2.38.1의 기본 거부에 걸린다. `GIT_CONFIG_COUNT/KEY/VALUE`는
    **하위 프로세스까지 따라가는** 설정이라 이 자리에 맞는다.

    나머지 submodule 테스트는 이게 필요 없다 — `declare_submodule_at`이 전송을
    일으키지 않기 때문이다. **이 픽스처가 다른 테스트로 번지면 그때부터 그
    테스트들은 우리 코드가 아니라 환경을 검사하는 것이 된다.**
    """
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")
