"""서비스 이름으로 배포된 코드를 읽는다 — **진짜 git 레포로** 검증한다.

목을 세우면 "층을 합쳤다"는 것을 우리가 정해 놓고 확인하는 꼴이 된다. 여기서
잡으려는 것은 **여러 파일을 실제로 읽어 합친 결과**라서, 커밋에 진짜로 파일이
들어 있어야 증명이 된다.
"""
import json
import shutil

import pytest

from src.config.schema_site import RepoConfig
from src.infrastructure.deployed_code import DeployedCode
from src.infrastructure.git_reader import RealCodeReader
from src.knowledge.schema import Deployment, Service, Topology
from tests.support import git, make_git_repo

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git이 없다")

LAYERS = ["config/gbm/{gbm}.json", "config/factories/{fct}/common.json",
          "config/factories/{fct}/{gbm}.json"]


@pytest.fixture
def code(tmp_path, clock):
    """사내 확인대로 층 셋. **gumi와 sevt가 밑바닥 층을 공유한다.**"""
    root = make_git_repo(tmp_path / "dt-core",
                         origin="https://git.example.com/team/dt-core")
    def write(path, value):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    write("config/gbm/mx.json", {"kafka": {"topic": "BASE_TOPIC", "group": "G"},
                                 "mongo": {"collection": "base_docs"}})
    write("config/factories/gumi/common.json", {"mongo": {"collection": "gumi_docs"}})
    write("config/factories/gumi/mx.json", {"kafka": {"topic": "GUMI_ALARM_EVENT"}})
    write("config/factories/sevt/common.json", {"mongo": {"collection": "sevt_docs"}})
    (root / "handler.py").write_text(
        'TOPIC = "GUMI_ALARM_EVENT"\ndef handle(): pass\n', encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-qm", "config", cwd=root)

    topology = Topology(services={"processor": Service(repo="dt-core", role="가공한다")},
                        config_paths=LAYERS)
    def build(fct: str) -> DeployedCode:
        reader = RealCodeReader([RepoConfig(name="dt-core", path=str(root),
                                            url="https://git.example.com/team/dt-core")],
                                clock=clock)
        return DeployedCode(reader, topology, Deployment(), gbm="mx", fct=fct,
                            clock=clock)
    return build


async def test_층을_합친_값을_돌려준다(code):
    """**이 파일의 존재 이유다.**

    `config/factories/gumi/mx.json`만 읽으면 토픽은 보이지만 컬렉션은 안 보이고,
    밑바닥만 읽으면 둘 다 틀린다. 합쳐야 "이 사이트가 실제로 보는 값"이 된다.
    """
    got = await code("gumi").config("processor")
    assert got.status == "ok", got.error
    assert got.data["kafka"]["topic"] == "GUMI_ALARM_EVENT"   # 맨 위 층이 덮었다
    assert got.data["kafka"]["group"] == "G"                  # 밑바닥이 살아남았다
    assert got.data["mongo"]["collection"] == "gumi_docs"     # 가운데 층이 덮었다


async def test_법인이_다르면_다른_값이_나온다(code):
    """`fct`는 **케이스의 사이트**에서 온다 — 리드가 고르는 값이 아니다.
    같은 커밋인데 답이 갈린다는 것이 층이 실제로 먹혔다는 증거다."""
    gumi = await code("gumi").config("processor")
    sevt = await code("sevt").config("processor")
    assert gumi.data["mongo"]["collection"] == "gumi_docs"
    assert sevt.data["mongo"]["collection"] == "sevt_docs"
    # sevt에는 mx 층이 없다 — 층은 **선택이다**. 밑바닥 값이 그대로 보여야 한다.
    assert sevt.data["kafka"]["topic"] == "BASE_TOPIC"
    assert sevt.status == "ok" and sevt.envelope.complete


async def test_어느_층을_읽었는지_증거에_남는다(code):
    """나중에 "그 값이 어디서 왔냐"를 되짚을 수 없으면 판정을 검증할 수 없다."""
    got = await code("gumi").config("processor")
    assert "config/gbm/mx.json" in got.source
    assert "config/factories/gumi/mx.json" in got.source


async def test_깨진_층이_있으면_완전하다고_안_한다(code, tmp_path):
    """합친 값이 **틀렸는데** 완전하다고 적으면 리드가 그걸 단정한다."""
    broken = tmp_path / "dt-core" / "config" / "factories" / "gumi" / "mx.json"
    broken.write_text("{ not json", encoding="utf-8")
    git("add", "-A", cwd=tmp_path / "dt-core")
    git("commit", "-qm", "broken", cwd=tmp_path / "dt-core")

    got = await code("gumi").config("processor")
    assert got.status == "ok"                    # 나머지 층은 읽혔다
    assert not got.envelope.complete
    assert "JSON이 아니다" in got.envelope.truncated_reason


async def test_없는_서비스는_아는_것을_알려준다(code):
    got = await code("gumi").config("없는서비스")
    assert got.status == "error"
    assert "processor" in got.error


async def test_이름을_누가_쓰는지_찾는다(code):
    """11a가 존재하는 이유. **서비스를 안 줘도** 된다 — 어느 서비스 것인지
    모르는 상태가 정상이다(decisions ⑮)."""
    got = await code("gumi").grep(["GUMI_ALARM_EVENT"])
    assert got.status == "ok", got.error
    assert "handler.py" in got.data


async def test_grep이_읽은_커밋을_적는다(code):
    got = await code("gumi").grep(["GUMI_ALARM_EVENT"])
    assert "dt-core @" in got.data


async def test_파일_하나를_읽는다(code):
    got = await code("gumi").read("processor", "handler.py")
    assert got.status == "ok", got.error
    assert "def handle" in got.data


async def test_서비스_목록에_역할과_커밋이_있다(code):
    """리드가 "누구를 봐야 하나"를 고르는 유일한 단서가 `role`이다."""
    got = await code("gumi").services()
    assert got.status == "ok"
    row = got.data[0]
    assert row["service"] == "processor" and row["repo"] == "dt-core"
    assert row["role"] == "가공한다" and row["commit"]


async def test_400줄이_넘는_층도_통째로_읽는다(tmp_path, clock, code):
    """**사내에서 실제로 난 일이다.**

    리더의 기본 상한은 400줄이다. 소스 파일은 앞부분만 봐도 쓸모가 있지만
    **config는 다르다** — 잘린 JSON은 파싱이 실패하고, 그 실패가
    "대상 파일이 깨졌다"로 읽힌다. 사람을 **멀쩡한 파일** 고치러 보낸다.
    """
    root = tmp_path / "dt-core"
    big = {"kafka": {"topic": "BASE_TOPIC", "group": "G"},
           "mongo": {"collection": "base_docs"},
           "rules": {f"r{i}": {"threshold": i} for i in range(300)}}
    (root / "config" / "gbm" / "mx.json").write_text(
        json.dumps(big, ensure_ascii=False, indent=2), encoding="utf-8")
    assert len((root / "config" / "gbm" / "mx.json")
               .read_text(encoding="utf-8").splitlines()) > 400
    git("add", "-A", cwd=root)
    git("commit", "-qm", "big", cwd=root)

    got = await code("gumi").config("processor")
    assert got.status == "ok", got.error
    assert got.envelope.complete, got.envelope.truncated_reason
    assert got.data["rules"]["r299"] == {"threshold": 299}   # 끝까지 읽었다
    assert got.data["kafka"]["topic"] == "GUMI_ALARM_EVENT"  # 덮어쓰기도 그대로


class _Truncating:
    """**항상 잘라서** 돌려주는 리더. 잘린 층을 만나면 어떻게 말하는지만 본다.

    여기서 진짜 git을 쓸 수 없다 — `whole=True`의 상한(1MB)을 넘기려면 그만한
    파일을 만들어야 하고, 그건 이 테스트가 재려는 것이 아니다. 재려는 것은
    **봉투가 불완전하다고 할 때 우리가 어떻게 분기하는가**다.
    """

    def __init__(self, clock):
        self._clock = clock

    async def show(self, repo, commit, path, *, whole=False):
        from src.domain.envelope import ProbeResult
        return ProbeResult.succeeded('{"kafka": {"topic": "GUMI', source=f"x:{path}",
                                     clock=self._clock, truncated_reason="400줄에서 끊음")


async def test_우리가_자른_것을_대상_탓으로_돌리지_않는다(clock):
    """**이게 이번 버그의 핵심이다.**

    봉투가 "불완전하다"고 말하는데 그걸 안 보고 파싱하면 `JSONDecodeError`가 나고,
    메시지가 "JSON이 아니다"가 된다. 원인은 우리인데 대상이 지목된다 —
    `unreachable`을 `finding`으로 적는 것과 같은 종류의 거짓말이다(⑪).
    """
    from src.infrastructure.deployed_code import DeployedCode
    from src.knowledge.schema import Deployment, Service, Topology

    topology = Topology(services={"processor": Service(repo="dt-core")},
                        config_paths=["config/gbm/{gbm}.json"])
    code = DeployedCode(_Truncating(clock), topology, Deployment(), gbm="mx",
                        fct="gumi", clock=clock)
    got = await code.config("processor")

    assert got.status == "error"          # 쓸 수 있는 층이 하나도 없다
    assert "JSON이 아니다" not in got.error, "우리가 자른 것을 대상 탓으로 돌렸다"
    assert "잘라서" in got.error, "왜 못 읽었는지가 안 적혀 있다"
    assert "경로" not in got.error, "경로를 고치라고 하면 맞는 경로를 고치러 간다"


def test_역할은_이름_옆에_붙일_수_있게_따로_준다(code):
    """`Service.role`은 "리드가 누구를 봐야 하나를 고르는 유일한 단서"인데, `code.services`를
    불러야만 보였다. 브리핑이 이름 옆에 붙이려면 호출부가 토폴로지를 안 뒤지고 받을 수
    있어야 한다. 역할이 빈 서비스는 뺀다 — 빈 괄호는 정보가 아니다."""
    assert code("gumi").service_roles() == {"processor": "가공한다"}


@pytest.fixture
def flow_code(tmp_path, clock):
    """사내 모양의 config를 든 진짜 레포 — 흐름 그래프 재료를 실제 git으로 뽑는다."""
    root = make_git_repo(tmp_path / "dt-core", origin="https://git.example.com/team/dt-core")

    def write(path, value):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                          encoding="utf-8")

    write("config/gbm/mx.json", {"infra": {"kafka": {"consumer": {"group_id": "mx-core",
                                                                   "topic": {"topic1": "mx.alarm.main"}}}},
                                 "mongodb_collection": {"alarm": "alarm_events"}})
    write("sink/writer.py", 'def run(cfg, consumer, mongo):\n'
          '    for m in consumer.subscribe(cfg["infra"]["kafka"]["consumer"]["topic"]["topic1"]):\n'
          '        mongo[cfg["mongodb_collection"]["alarm"]].insert_one(m)\n')
    git("add", "-A", cwd=root)
    git("commit", "-qm", "flow", cwd=root)
    topology = Topology(services={"sink": Service(repo="dt-core", role="저장한다")},
                        config_paths=LAYERS)
    reader = RealCodeReader([RepoConfig(name="dt-core", path=str(root),
                                        url="https://git.example.com/team/dt-core")], clock=clock)
    return DeployedCode(reader, topology, Deployment(), gbm="mx", fct="gumi", clock=clock)


async def test_흐름_이름은_합친_config에서_나온다(flow_code):
    names, problems = await flow_code.flow_names()
    assert problems == []
    assert {(n.kind, n.value, n.relation) for n in names} == {
        ("topic", "mx.alarm.main", "consumes"), ("group", "mx-core", "consumes_as"),
        ("collection", "alarm_events", None)}


async def test_흐름_히트는_배포_커밋의_git_grep이다(flow_code):
    table, notes = await flow_code.flow_hits(["alarm"])
    files = {(h.file, h.line) for h in table["alarm"]}
    assert ("sink/writer.py", 3) in files and ("config/gbm/mx.json", 1) in files
    assert all(h.repo == "dt-core" and h.commit for h in table["alarm"])
    assert notes == [] and flow_code.pinned() == {"dt-core": "main"}


async def test_흐름_히트는_레포마다_묶어_묻고_패턴별로_나눈다(flow_code):
    """패턴마다 git 프로세스 하나면 이름 100개·레포 3개에 600번이다(사내에서 분 단위로 조용히
    기다렸다). 한 번에 묻고, 줄에 든 패턴으로 나눈다 — 없는 패턴은 빈 채로 돌아온다."""
    calls = []
    real = flow_code._reader.grep

    async def spy(*a, **kw):
        calls.append(kw)
        return await real(*a, **kw)

    flow_code._reader.grep = spy
    table, _ = await flow_code.flow_hits(["alarm", "topic1", "없는것"])
    assert len(calls) == 1 and calls[0]["fixed"] is True and calls[0]["context"] == 1
    assert {(h.file, h.line) for h in table["topic1"]} == {("config/gbm/mx.json", 1), ("sink/writer.py", 2)}
    assert table["없는것"] == []
    assert all(p in h.text for p, hits in table.items() for h in hits)


async def test_잘린_코드_찾기는_버리지_않고_사유로_남는다(flow_code, monkeypatch):
    from src.infrastructure import deployed_code as dc
    monkeypatch.setattr(dc, "FLOW_MAX_LINES", 2)
    _, notes = await flow_code.flow_hits(["alarm"])
    assert notes and "잘렸다" in notes[0] and "엣지가 빠졌을 수" in notes[0]


async def test_흐름_히트에는_앞뒤_한_줄이_실려_온다(flow_code):
    """진짜 git → `-C1` → 파서까지 한 줄로. 이름 줄 옆의 동사를 흐름 추출이 읽는 근거다."""
    table, _ = await flow_code.flow_hits(["alarm"])
    hits = {(h.file, h.line): h for h in table["alarm"]}
    assert hits[("sink/writer.py", 3)].context == \
        '    for m in consumer.subscribe(cfg["infra"]["kafka"]["consumer"]["topic"]["topic1"]):'
    assert hits[("config/gbm/mx.json", 1)].context == ""      # 한 줄짜리 파일

