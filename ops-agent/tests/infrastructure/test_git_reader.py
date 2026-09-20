"""**진짜 git 레포를 만들어서** 검증한다.

가짜로는 증명이 안 되는 자리다. 이 어댑터의 핵심 계약이 `git show <옛 커밋>:경로`가
**그 커밋 시점의 내용**을 준다는 것인데, 목을 세우면 목이 그렇게 답하도록 우리가
정해 놓고 "된다"고 확인하는 꼴이 된다 — 이 리포가 `ScriptedAdapter`로 이미 당한
거짓 초록이다.

git이 없는 환경에서는 건너뛴다. **조용히는 아니다** — skip 사유가 찍힌다.
"""
import shutil
import subprocess

import pytest

from src.config.schema_site import RepoConfig
from src.infrastructure.git_reader import RealCodeReader

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git이 없다")


def _run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """커밋 두 개짜리 레포. **옛 커밋과 새 커밋의 내용이 다르다** — 그게 요점이다."""
    root = tmp_path / "dt-core"
    (root / "config").mkdir(parents=True)
    # **레포 안에서** init한다. 부모에서 하면 `.git`이 한 층 위에 생기고, 그러면
    # 어댑터가 ".git이 없다"로 실패하는데 **다른 테스트가 그걸 통과로 읽는다** —
    # 처음에 그렇게 썼고, "없는 커밋" 테스트가 엉뚱한 이유로 초록이었다.
    _run("git", "init", "-q", "-b", "main", cwd=root)
    _run("git", "config", "user.email", "t@t", cwd=root)
    _run("git", "config", "user.name", "t", cwd=root)

    (root / "config" / "common.json").write_text(
        '{"mongo": {"source": "OLD_NAME"}}\n', encoding="utf-8")
    (root / "app.py").write_text("def handle():\n    pass\n", encoding="utf-8")
    _run("git", "add", "-A", cwd=root)
    _run("git", "commit", "-qm", "first", cwd=root)
    old = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                         capture_output=True, text=True).stdout.strip()

    (root / "config" / "common.json").write_text(
        '{"mongo": {"source": "NEW_NAME"}}\n', encoding="utf-8")
    _run("git", "add", "-A", cwd=root)
    _run("git", "commit", "-qm", "second", cwd=root)
    return root, old


@pytest.fixture
def reader(repo, clock):
    root, _ = repo
    return RealCodeReader([RepoConfig(name="dt-core", url="https://git.example.com/dt-core",
                                      path=str(root))], clock=clock)


async def test_옛_커밋의_내용을_읽는다(reader, repo):
    """**이 어댑터가 존재하는 이유다.**

    사이트가 뒤처져 있는데 최신 코드를 읽으면, 떠 있지도 않은 코드로 확신에 찬
    오답을 낸다. 배포 커밋을 지정해 읽을 수 있어야 그게 막힌다.
    """
    _, old = repo
    got = await reader.show("dt-core", old, "config/common.json")
    assert got.status == "ok", got.error
    assert "OLD_NAME" in got.data and "NEW_NAME" not in got.data

    fresh = await reader.show("dt-core", "main", "config/common.json")
    assert "NEW_NAME" in fresh.data


async def test_대상의_config_파일에서_이름을_찾는다(reader):
    """이름은 코드가 아니라 **대상의 config 파일**에 있다(사내 확인).
    그래서 `git grep`의 실제 쓸모가 여기다."""
    got = await reader.grep("dt-core", "main", ["NEW_NAME"])
    assert got.status == "ok", got.error
    assert "config/common.json" in got.data


async def test_결과가_없는_것은_오류가_아니다(reader):
    """`git grep`은 못 찾으면 1로 끝난다. 그걸 오류로 삼으면 **"없다"가 "못 봤다"가
    되고**, 둘은 완전히 다른 사실이다(5단계의 `unreachable`과 같은 계열)."""
    got = await reader.grep("dt-core", "main", ["존재하지_않는_문자열"])
    assert got.status == "ok", got.error
    assert got.data.strip() == ""


async def test_패턴이_대시로_시작해도_옵션으로_안_읽힌다(reader):
    """`-e`로 넘기는 이유다(decisions ⑨). 안 그러면 git이 죽거나 엉뚱하게 돈다."""
    got = await reader.grep("dt-core", "main", ["-NEW_NAME"])
    assert got.status == "ok", got.error


async def test_없는_커밋은_값으로_실패한다(reader):
    """배포 커밋 선언이 오래되면 **일상적으로** 일어난다 — 던지면 조사가 죽는다."""
    got = await reader.show("dt-core", "0" * 40, "app.py")
    assert got.status == "error"
    # **이유가 맞는지까지 본다.** 그냥 error면 ".git이 없다"로 실패해도 통과한다 —
    # 실제로 그랬다(픽스처가 부모에서 init했고 이 테스트만 초록이었다).
    assert ".git이 없다" not in got.error


async def test_등재되지_않은_레포는_포트에_닿기_전에_거부된다(reader):
    got = await reader.show("남의레포", "main", "app.py")
    assert got.status == "error"
    assert "등재되지 않은" in got.error


async def test_git이_아닌_디렉터리는_이유를_말한다(tmp_path, clock):
    """작업 트리만 복사되면 **우리 설계의 전제가 통째로 무너진다**(⑤-2).
    증상이 "코드 증거가 조용히 안 나온다"가 되면 안 된다."""
    plain = tmp_path / "plain"
    plain.mkdir()
    reader = RealCodeReader([RepoConfig(name="x", url="https://git.example.com/x",
                                        path=str(plain))], clock=clock)
    got = await reader.show("x", "main", "a.py")
    assert got.status == "error" and ".git이 없다" in got.error


async def test_파일_목록을_돌려준다(reader):
    got = await reader.ls("dt-core", "main")
    assert got.status == "ok" and "app.py" in got.data


# ── submodule 경계 ────────────────────────────────────────────────

@pytest.fixture
def nested(tmp_path):
    """부모 + 진짜 submodule. **안 채워진 클론**과 채워진 클론을 둘 다 준다.

    사내에서 기본으로 나오는 모양이 안 채워진 쪽이다(`git clone`에
    `--recurse-submodules`가 없으면). 그 상태에서 git이 하는 말을 목으로 흉내 내면
    이 테스트는 아무것도 증명하지 못한다 — 우리가 잡으려는 것이 정확히
    **git의 실제 행동**이기 때문이다.
    """
    lib = tmp_path / "libs"
    lib.mkdir()
    _run("git", "init", "-q", "-b", "main", cwd=lib)
    _run("git", "config", "user.email", "t@t", cwd=lib)
    _run("git", "config", "user.name", "t", cwd=lib)
    (lib / "kafka.json").write_text('{"topic": "ALARM_EVENT"}\n', encoding="utf-8")
    _run("git", "add", "-A", cwd=lib)
    _run("git", "commit", "-qm", "topic", cwd=lib)

    parent = tmp_path / "parent"
    parent.mkdir()
    _run("git", "init", "-q", "-b", "main", cwd=parent)
    _run("git", "config", "user.email", "t@t", cwd=parent)
    _run("git", "config", "user.name", "t", cwd=parent)
    (parent / "app.py").write_text("import libs\n", encoding="utf-8")
    _run("git", "add", "-A", cwd=parent)
    _run("git", "commit", "-qm", "first", cwd=parent)
    _run("git", "-c", "protocol.file.allow=always", "submodule", "add", "-q",
         str(lib), "vendor/libs", cwd=parent)
    _run("git", "commit", "-qm", "add submodule", cwd=parent)

    blind = tmp_path / "blind"
    _run("git", "-c", "protocol.file.allow=always", "clone", "-q", str(parent),
         str(blind), cwd=tmp_path)
    full = tmp_path / "full"
    _run("git", "-c", "protocol.file.allow=always", "clone", "-q",
         "--recurse-submodules", str(parent), str(full), cwd=tmp_path)
    return blind, full


def _reader_at(path, clock):
    return RealCodeReader([RepoConfig(name="dt-core", url="https://git.example.com/dt-core",
                                      path=str(path))], clock=clock)


async def test_안_채워진_submodule의_파일은_없다고_하지_않는다(nested, clock):
    """git은 여기서 `does not exist in 'main'`이라고 한다 — **거짓말이다.** 있다.

    그 말을 그대로 흘리면 리드는 "그 파일은 없다"를 사실로 삼는다. 우리는
    "**우리가 못 보는 것**"이라고 말해야 한다(5단계의 `unreachable`과 같은 구별).
    """
    blind, _ = nested
    got = await _reader_at(blind, clock).show("dt-core", "main", "vendor/libs/kafka.json")
    assert got.status == "error"
    assert "submodule" in got.error and "안 채워져" in got.error


async def test_안_채워진_submodule이면_grep이_조용히_0건을_안_준다(nested, clock):
    """**이 파일에서 제일 위험한 자리다.**

    측정한 사실(git 2.43): 안 채워진 submodule을 두고 `git grep`은 종료코드 1에
    stdout도 stderr도 비어 있다. `--recurse-submodules`를 붙여도 똑같이 조용하다.
    우리 `_git`은 grep의 1을 "결과 없음"으로 읽으므로, 봉투가 말하지 않으면
    2차의 판정이 **"코드에 그런 게 없다"**를 단정하게 된다.
    """
    blind, _ = nested
    got = await _reader_at(blind, clock).grep("dt-core", "main", ["ALARM_EVENT"])
    assert got.status == "ok"
    assert got.data.strip() == ""          # git은 실제로 아무것도 안 준다
    assert not got.envelope.complete       # 그러나 "없다"고 주장할 수는 없다
    assert "submodule" in got.envelope.truncated_reason


async def test_채워진_submodule_안을_실제로_읽는다(nested, clock):
    """`git show <부모커밋>:서브/경로`는 채워져 있어도 안 들어간다 —
    gitlink를 직접 풀어야 한다. 그게 되는지 **내용으로** 확인한다."""
    _, full = nested
    got = await _reader_at(full, clock).show("dt-core", "main", "vendor/libs/kafka.json")
    assert got.status == "ok", got.error
    assert "ALARM_EVENT" in got.data


async def test_읽은_submodule_버전이_증거에_남는다(nested, clock):
    """**최신이 아니라 그 배포가 쓴 버전**을 읽었다는 것이 증거에 있어야 한다.
    `source`가 안 말하면 나중에 아무도 어느 코드를 본 건지 되짚을 수 없다."""
    _, full = nested
    got = await _reader_at(full, clock).show("dt-core", "main", "vendor/libs/kafka.json")
    assert "submodule vendor/libs@" in got.source


async def test_채워진_submodule_안까지_grep한다(nested, clock):
    _, full = nested
    got = await _reader_at(full, clock).grep("dt-core", "main", ["ALARM_EVENT"])
    assert got.status == "ok", got.error
    assert "vendor/libs/kafka.json" in got.data
    assert got.envelope.complete


async def test_submodule이_없으면_봉투가_멀쩡하다(reader):
    """경고를 오탐으로 남발하면 **진짜 경고가 묻힌다.** 대부분의 레포가 이 경우다."""
    got = await reader.grep("dt-core", "main", ["NEW_NAME"])
    assert got.envelope.complete and got.envelope.truncated_reason is None


async def test_목록도_submodule_안은_못_봤다고_말한다(nested, clock):
    """`ls-tree -r`는 submodule 안으로 안 들어간다 — 이름 하나만 나온다.
    그걸 "그 밑에 파일이 없다"로 읽으면 안 된다."""
    blind, _ = nested
    got = await _reader_at(blind, clock).ls("dt-core", "main")
    assert "vendor/libs" in got.data
    assert not got.envelope.complete and "submodule" in got.envelope.truncated_reason
