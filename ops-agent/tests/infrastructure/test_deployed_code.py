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
