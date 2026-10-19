"""서비스 이름으로 배포된 코드를 읽는다 — **진짜 git 레포로** 검증한다.

목을 세우면 "층을 합쳤다"는 것을 우리가 정해 놓고 확인하는 꼴이 된다. 여기서
잡으려는 것은 **여러 파일을 실제로 읽어 합친 결과**라서, 커밋에 진짜로 파일이
들어 있어야 증명이 된다.
"""
import json
import shutil

import pytest

from src.config.schema_site import RepoConfig
from src.domain.envelope import ProbeResult
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
    # 상한을 넘는 덤프는 키 지도만 온다(R2-2b-1) — 끝까지 읽었는지는 `key=`로 그 자리를 골라 확인한다.
    assert not got.envelope.complete and "key=" in got.envelope.truncated_reason
    assert got.data["_키_지도"]["rules"].startswith("객체 · 키 300개")
    # 사내 측정 #5: 리드가 지도의 경로(`mongodb_collection.<이름>`)를 컬렉션 이름으로 썼다 — 지도가 스스로 말한다(실행기도 막는다).
    assert list(got.data)[0] == "_주의" and "이름이 아니다" in got.data["_주의"] and "key=" in got.data["_주의"]
    end = await code("gumi").config("processor", key="rules.r299")
    assert end.envelope.complete and end.data == {"threshold": 299}   # 끝까지 읽었다
    topic = await code("gumi").config("processor", key="kafka.topic")
    winning = json.loads((root / "config" / "factories" / "gumi" / "mx.json").read_text(encoding="utf-8"))
    assert topic.data == winning["kafka"]["topic"]                     # 덮어쓰기도 그대로 — 맨 위 층의 값


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
    # gumi 층이 그룹을 덮는다 — 근거 줄은 이긴 층이어야 한다.
    write("config/factories/gumi/mx.json",
          '{\n  "infra": {"kafka": {"consumer": {"group_id": "gumi-mx-core"}}}\n}\n')
    # 이 사이트에 안 쓰이는 층. 값이 똑같이 적혀 있고 알파벳순으로 앞선다 — 레포 grep으로
    # 근거를 집으면 여기가 찍힌다(사내 `_dev`).
    write("config/factories/_dev/mx.json",
          '{\n  "infra": {"kafka": {"consumer": {"topic": {"topic1": "mx.alarm.main"}}}}\n}\n')
    write("sink/writer.py", 'def run(cfg, consumer, mongo):\n'
          '    for m in consumer.subscribe(cfg["infra"]["kafka"]["consumer"]["topic"]["topic1"]):\n'
          '        mongo[cfg["mongodb_collection"]["alarm"]].insert_one(m)\n')
    # FastAPI 모양의 라우트 선언 — 끝점 노드(11c 커밋 5)의 재료. 테스트 파일의 데코레이터는 빠져야 한다.
    write("api/routes.py", 'router = APIRouter(prefix="/summary")\n\n\n@router.post("/badge")\n'
          'def badge():\n    return {}\n')
    write("tests/test_routes.py", '@router.get("/not-real")\ndef t():\n    pass\n')
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
        ("topic", "mx.alarm.main", "consumes"), ("group", "gumi-mx-core", "consumes_as"),
        ("collection", "alarm_events", None)}
    assert all(n.services == ("sink",) for n in names), "어느 서비스의 config인지 이름이 안다"
    where = {n.value: [(e[0], e[1], e[2]) for e in n.evidence] for n in names}
    assert where["gumi-mx-core"] == [("sink", "config/factories/gumi/mx.json", 2)]   # 이긴 층
    assert where["mx.alarm.main"] == [("sink", "config/gbm/mx.json", 1)]              # `_dev`가 아니다


async def test_code_flow는_붙인_그래프의_이웃을_주고_없으면_실패로_답한다(flow_code):
    missing = await flow_code.flow("alarm_events")
    assert missing.status == "error" and "code graph" in missing.error
    graph = {"nodes": [{"id": "service_sink", "label": "sink", "type": "service", "repo": "dt-core"},
                       {"id": "collection_alarm_events", "label": "alarm_events", "type": "collection"}],
             "links": [{"source": "service_sink", "target": "collection_alarm_events", "relation": "declares",
                        "origin": "config", "confidence": "EXTRACTED",
                        "source_file": "config/gbm/mx.json", "source_location": "L1"}]}
    flow_code.attach_flow_graph(graph)
    got = await flow_code.flow("alarm_events")
    assert got.status == "ok" and got.envelope.complete
    assert got.data == "sink —declares→ alarm_events [collection]  [config·EXTRACTED] config/gbm/mx.json:L1"
    unknown = await flow_code.flow("없는이름")
    assert unknown.status == "error" and "그래프에 없다" in unknown.error


async def test_code_trace는_붙인_그래프의_끝점_사슬을_주고_없으면_실패로_답한다(flow_code):
    """조사 중에 다시 추적하지 않는다(⑥) — `code graph`가 오버레이에 남긴 사슬을 읽는다."""
    missing = await flow_code.trace("/summary/badge")
    assert missing.status == "error" and "code graph" in missing.error
    graph = {"nodes": [{"id": "endpoint_summary_badge", "label": "/summary/badge", "type": "endpoint",
                        "traced": "ok", "chain": ["api/r.py:L6 badge", "api/q.py:L3 AlarmRepo.recent"],
                        "chain_parent": [None, 0], "gaps": []},
                       {"id": "endpoint_summary_raw", "label": "/summary/raw", "type": "endpoint"},
                       {"id": "collection_alarm_events", "label": "alarm_events", "type": "collection"}],
             "links": [{"source": "endpoint_summary_badge", "target": "collection_alarm_events",
                        "relation": "reads", "origin": "trace", "confidence": "EXTRACTED", "via": "literal",
                        "step": 1, "source_file": "api/q.py", "source_location": "L3"}]}
    flow_code.attach_flow_graph(graph)
    got = await flow_code.trace("/summary/badge")
    assert got.status == "ok" and got.envelope.complete
    assert got.data.splitlines()[0] == "api/r.py:L6 badge"
    assert "AlarmRepo.recent — reads: alarm_events [collection] 확실" in got.data
    untraced = await flow_code.trace("/summary/raw")
    assert untraced.status == "error" and "추적" in untraced.error
    unknown = await flow_code.trace("/nope")
    assert unknown.status == "error" and "끝점에 없다" in unknown.error
    # 등재 항목은 `POST /path` 모양으로 보인다 — 사내 실측에서 리드가 그대로 넘겨 거부당했다. 메서드는 떼고 맞춘다.
    assert (await flow_code.trace("POST /summary/badge")).data == got.data
    assert (await flow_code.trace("get /summary/badge")).data == got.data


async def test_흐름_히트는_배포_커밋의_git_grep이다(flow_code):
    table, notes = await flow_code.flow_hits(["alarm"])
    files = {(h.file, h.line) for h in table["alarm"]}
    assert ("sink/writer.py", 3) in files and ("config/gbm/mx.json", 1) in files
    assert all(h.repo == "dt-core" and h.commit for h in table["alarm"])
    assert notes == [] and flow_code.pinned() == {"dt-core": "main"}


def _no_snapshot(flow_code, monkeypatch):
    """스냅샷을 못 받는 리더로 — 옛 git·시간 초과의 길."""
    clock = flow_code._clock

    async def failing(repo, commit):
        return ProbeResult.failed("옛 git", source=f"code.snapshot {repo}@{commit}", clock=clock)

    monkeypatch.setattr(flow_code._reader, "snapshot", failing)
    flow_code._snapshots.clear()


async def test_흐름_히트는_스냅샷에서_찾고_git_grep은_안_띄운다(flow_code, monkeypatch):
    """11e-3. 패턴 20개씩 묶어도 사내는 레포당 19번 × 5레포 = 231초였다 — 본문은 이미 스냅샷에 있다. 라우트 선언도
    같다. 스냅샷을 못 받으면 전처럼 묶어 묻는다(패턴마다 한 번이면 600번이던 그 길 그대로)."""
    calls = []
    real = flow_code._reader.grep

    async def spy(*a, **kw):
        calls.append(kw)
        return await real(*a, **kw)

    flow_code._reader.grep = spy
    table, notes = await flow_code.flow_hits(["alarm", "topic1", "없는것"])
    routes, _ = await flow_code.route_hits()
    assert calls == [] and notes == []
    assert {(h.file, h.line) for h in table["topic1"]} == {
        ("config/gbm/mx.json", 1), ("config/factories/_dev/mx.json", 2), ("sink/writer.py", 2)}
    assert table["없는것"] == []
    assert all(p in h.text for p, hits in table.items() for h in hits)
    # 테스트 파일의 데코레이터도 줄로는 온다 — 빼는 것은 조립(`routes_from_hits`)의 일이고 git grep도 그랬다.
    assert [(h.file, h.line) for h in routes] == [("api/routes.py", 1), ("api/routes.py", 4), ("tests/test_routes.py", 1)]
    _no_snapshot(flow_code, monkeypatch)
    again, _ = await flow_code.flow_hits(["alarm", "topic1", "없는것"])
    again_routes, _ = await flow_code.route_hits()
    assert len(calls) == 2 and calls[0]["fixed"] is True and calls[0]["context"] == 1
    assert again == table and again_routes == routes                  # 두 길의 답이 같다


async def test_잘린_코드_찾기는_버리지_않고_사유로_남는다(flow_code, monkeypatch):
    """git grep으로 내려간 길의 상한. 스냅샷 길은 상한이 없다 — 봉투를 안 거친다."""
    from src.infrastructure import deployed_code as dc
    _no_snapshot(flow_code, monkeypatch)
    monkeypatch.setattr(dc, "FLOW_MAX_LINES", 2)
    _, notes = await flow_code.flow_hits(["alarm"])
    assert notes and "잘렸다" in notes[0] and "엣지가 빠졌을 수" in notes[0]


async def test_흐름_히트는_스냅샷이_못_본_submodule을_사유로_남긴다(clock):
    """`git grep --recurse-submodules`가 안 채워진 submodule을 조용히 건너뛸 때 봉투가 말하던 것 — 스냅샷 길에서도
    같은 말이 나와야 한다(그래프에 엣지가 빠졌을 수 있다)."""
    class Reader:
        async def snapshot(self, repo, commit):
            return ProbeResult.succeeded({"a.py": "alarm = 1\n", "api/r.py": "x = APIRouter()\n"}, source="s", clock=clock,
                                         truncated_reason="submodule vendor/libs을 못 봤다(안 채워져 있다) — 그 안은 이 결과에 없다")

        async def grep(self, *a, **k):
            raise AssertionError("스냅샷이 있으면 grep을 안 부른다")

    code = DeployedCode(Reader(), Topology(services={"sink": Service(repo="dt-core", role="저장한다")}, config_paths=LAYERS),
                        Deployment(), gbm="mx", fct="gumi", clock=clock)
    table, notes = await code.flow_hits(["alarm"])
    assert [(h.file, h.line) for h in table["alarm"]] == [("a.py", 1)]
    assert len(notes) == 1 and "vendor/libs" in notes[0] and "엣지가 빠졌을 수" in notes[0]
    routes, route_notes = await code.route_hits()
    assert [(h.file, h.line) for h in routes] == [("api/r.py", 1)]
    assert len(route_notes) == 1 and "vendor/libs" in route_notes[0] and "끝점이 빠졌을 수" in route_notes[0]


async def test_흐름_히트에는_앞뒤_한_줄이_실려_온다(flow_code):
    """진짜 git → `-C1` → 파서까지 한 줄로. 이름 줄 옆의 동사를 흐름 추출이 읽는 근거다."""
    table, _ = await flow_code.flow_hits(["alarm"])
    hits = {(h.file, h.line): h for h in table["alarm"]}
    assert hits[("sink/writer.py", 3)].context == \
        '    for m in consumer.subscribe(cfg["infra"]["kafka"]["consumer"]["topic"]["topic1"]):'
    assert hits[("config/gbm/mx.json", 1)].context == ""      # 한 줄짜리 파일



async def test_라우트_히트는_배포_커밋의_git_grep이고_테스트_파일은_뺀다(flow_code):
    from src.knowledge import flow

    hits, notes = await flow_code.route_hits()
    assert notes == []
    assert {("api/routes.py", 1), ("api/routes.py", 4)} <= {(h.file, h.line) for h in hits}
    routes = flow.routes_from_hits(hits)        # 테스트 파일의 데코레이터는 여기서 빠진다
    assert [(r.method, r.path, r.confidence, r.file, r.line) for r in routes] == \
        [("POST", "/summary/badge", "EXTRACTED", "api/routes.py", 4)]


async def test_source_for는_배포_커밋의_파일과_리터럴_grep을_준다(flow_code):
    """추적기의 `Source` — 실제 git으로. grep은 `-F`라 패턴이 리터럴이다."""
    src = flow_code.source_for("dt-core")
    text = await src.read("api/routes.py")
    assert text is not None and 'APIRouter(prefix="/summary")' in text
    assert await src.read("no/such.py") is None
    hits = await src.grep(["def badge("])
    assert [(h.file, h.line) for h in hits] == [("api/routes.py", 5)]
    assert all(h.repo == "dt-core" and h.commit for h in hits)


class _Src:
    """인덱스 재료 — 같은 이름 `run`이 둘(sink·jobs)이라 모호함을, 라우트 `badge → recent` 사슬로 진입점 경로를 본다."""
    FILES = {
        "sink/__init__.py": "", "api/__init__.py": "", "jobs/__init__.py": "",
        "sink/writer.py": "def run(cfg, mongo):\n    mongo[cfg[\"mongodb_collection\"][\"alarm\"]].insert_one({})\n",
        "api/q.py": "def recent(db):\n    return db[\"alarm_events\"].find({})\n",
        "api/r.py": "from api.q import recent\n\n\n@router.post('/badge')\ndef badge(db):\n    return recent(db)\n",
        "jobs/run.py": "def run():\n    pass\n",
    }

    async def files(self):
        return sorted(self.FILES)

    async def read(self, path):
        return self.FILES.get(path)


async def test_code_callers와_uses는_붙인_인덱스로_답하고_없거나_모호하면_실패로_답한다(flow_code):
    """11d 6d-1 — 리드가 "이 컬렉션에 누가 쓰나"·"이 함수를 누가 부르나"를 묻는다. 출력은 사람이 CLI로 보는 것과
    같은 조립이고, 이름이 여럿이면 코드가 고르지 않고 후보를 돌려준다(사람이 CLI에서 받는 것과 같다)."""
    from src.knowledge import index as ix
    from src.knowledge.flow import Name

    missing = await flow_code.uses("alarm_events")
    assert missing.status == "error" and "code graph" in missing.error
    idx = await ix.build_index({"dt-core": _Src()}, names=[Name("collection", "alarm_events", "mongodb_collection.alarm")],
                               commits={"dt-core": "c0ffee"})
    flow_code.attach_index(idx)
    uses = await flow_code.uses("alarm_events")
    assert uses.status == "ok" and uses.envelope.complete
    assert "alarm_events [collection]" in uses.data and "쓰기 1" in uses.data and "읽기 1" in uses.data
    assert "sink.writer.run (sink/writer.py:L2) [dt-core · sink]" in uses.data        # 서비스 이름이 붙는다
    assert "api.q.recent (api/q.py:L2) [dt-core" in uses.data
    assert "진입점: r.badge(라우트 router.post('/badge'))" in uses.data           # 읽는 함수가 어느 끝점에서 오나
    none = await flow_code.uses("없는이름")
    assert none.status == "error" and "인덱스에 없다" in none.error

    callers = await flow_code.callers("badge")
    assert callers.status == "ok" and callers.data.splitlines()[0].startswith("대상 api.r.badge (api/r.py:L")
    assert "부르는 곳이 없다" in callers.data and "라우트" in callers.data
    chain = await flow_code.callers("recent")
    assert "바로 부르는 곳 1" in chain.data and "api.r.badge (api/r.py:L6)" in chain.data   # 줄은 부르는 자리
    assert "진입점 1:" in chain.data and "[dt-core] r.badge → q.recent   (라우트 router.post('/badge'))" in chain.data
    many = await flow_code.callers("run")
    assert many.status == "error" and "여럿" in many.error
    assert "sink.writer.run" in many.error and "jobs.run.run" in many.error
    unknown = await flow_code.callers("nope")
    assert unknown.status == "error" and "인덱스에 없다" in unknown.error
    flow_code.attach_index(None)
    assert (await flow_code.callers("badge")).status == "error"


# ── 인덱스 소스는 스냅샷을 먼저 쓴다 (11e) ──────────────────────────

class _Counting:
    """스냅샷을 주거나(`ok`) 못 주는(`fail`) 리더. 어느 길을 탔는지 호출 수로 본다."""

    def __init__(self, clock, *, snapshot_ok: bool):
        from src.domain.envelope import ProbeResult
        self._clock, self._ok, self._P = clock, snapshot_ok, ProbeResult
        self.calls: list[str] = []
        self.files = {"a.py": "def a():\n    pass\n", ".gitmodules": ""}

    async def snapshot(self, repo, commit):
        self.calls.append("snapshot")
        if not self._ok:
            return self._P.failed("옛 git — archive 없음", source="s", clock=self._clock)
        return self._P.succeeded(dict(self.files), source="s", clock=self._clock)

    async def ls(self, repo, commit, path="", *, max_names=400):
        self.calls.append("ls")
        return self._P.succeeded(sorted(self.files), source="l", clock=self._clock)

    async def show(self, repo, commit, path, *, whole=False):
        self.calls.append("show")
        text = self.files.get(path)
        return (self._P.succeeded(text, source="w", clock=self._clock) if text is not None
                else self._P.failed("없다", source="w", clock=self._clock))

    async def grep(self, *a, **k):
        raise AssertionError("안 부른다")


def _source_with(reader):
    from src.infrastructure.deployed_code import _GitSource
    return _GitSource(reader, "dt-core", "c0ffee")


async def test_인덱스_소스는_스냅샷_한_번으로_목록과_내용을_다_답한다(clock):
    reader = _Counting(clock, snapshot_ok=True)
    src = _source_with(reader)
    assert await src.files() == [".gitmodules", "a.py"]
    assert await src.read("a.py") == "def a():\n    pass\n"
    assert await src.read(".gitmodules") == ""
    assert await src.read("없다.py") is None
    assert reader.calls == ["snapshot"]                 # ls도 show도 없다


async def test_스냅샷을_못_받으면_예전처럼_파일별로_읽는다(clock):
    """옛 git·시간 초과에서 느려질 뿐 결과는 같아야 한다 — 스냅샷은 지름길이지 새 계약이 아니다."""
    reader = _Counting(clock, snapshot_ok=False)
    src = _source_with(reader)
    assert await src.files() == [".gitmodules", "a.py"]
    assert await src.read("a.py") == "def a():\n    pass\n"
    assert reader.calls == ["snapshot", "ls", "show"]   # 스냅샷은 한 번만 시도한다


# ── 11e-2 — 사이트 층은 스냅샷에서, GBM 기준과의 차이만 ──────────────────

async def test_names_for는_층을_스냅샷에서_읽고_GBM_층만으로도_뽑는다(flow_code):
    """`code graph --gbm mx`가 사이트 28개의 층을 읽을 때 git을 다시 띄우면 안 된다(28×서비스×층). 레포 스냅샷
    (11e-1)에서 병합한다. `fct=""`는 `{fct}`가 든 층을 빼고 GBM 층만 — 그래프의 기준값이다."""
    base, problems = await flow_code.names_for("")
    gumi, _ = await flow_code.names_for("gumi")
    key = "infra.kafka.consumer.group_id"
    assert problems == []
    assert {n.value for n in base if n.key_path == key} == {"mx-core"}
    assert {n.value for n in gumi if n.key_path == key} == {"gumi-mx-core"}
    assert {(n.kind, n.value) for n in base if n.kind != "group"} == {(n.kind, n.value) for n in gumi if n.kind != "group"}


async def test_site_overrides는_GBM_값과_다른_것만_적는다(flow_code):
    base, _ = await flow_code.names_for("")
    rows = await flow_code.site_overrides("gumi", base)
    # `base`는 그 키의 GBM 값 — 인덱스의 함수별 자원은 key_path를 모르므로 "무엇이 무엇으로"가 있어야 입힌다.
    assert rows == [{"kind": "group", "key_path": "infra.kafka.consumer.group_id", "value": "gumi-mx-core",
                     "base": "mx-core", "services": ["sink"], "relation": "consumes_as"}]


async def test_스냅샷이_없으면_사이트_층도_통째로_읽는다(tmp_path, flow_code, monkeypatch, clock):
    """`_layers_in`의 되돌아가는 길도 `_layers`와 같은 규칙이다 — 리더 기본 상한 400줄에 잘린 config는 파싱이
    실패하고, 그 실패가 "대상 파일이 깨졌다"로 읽힌다(사내에서 실제로 났던 일, `test_400줄이_넘는_층도_통째로_읽는다`)."""
    root = tmp_path / "dt-core"
    big = {"infra": {"kafka": {"consumer": {"group_id": "mx-core", "topic": {"topic1": "mx.alarm.main"}}}},
           "mongodb_collection": {"alarm": "alarm_events"},
           "rules": {f"r{i}": {"threshold": i} for i in range(300)}}
    (root / "config" / "gbm" / "mx.json").write_text(json.dumps(big, indent=2), encoding="utf-8")
    assert len((root / "config" / "gbm" / "mx.json").read_text(encoding="utf-8").splitlines()) > 400
    git("add", "-A", cwd=root)
    git("commit", "-qm", "big", cwd=root)

    async def no_snapshot(repo, commit):
        return ProbeResult.failed("옛 git — archive 없음", source=f"code.snapshot {repo}@{commit}", clock=clock)

    monkeypatch.setattr(flow_code._reader, "snapshot", no_snapshot)
    base, problems = await flow_code.names_for("")
    assert problems == []
    assert {n.value for n in base if n.kind == "group"} == {"mx-core"}


async def test_사이트_층_읽기는_스냅샷_뒤로는_git을_안_띄운다(flow_code, monkeypatch):
    reader = flow_code._reader
    calls = []
    real = reader._git_bytes

    async def counting(repo, args, **kw):
        calls.append(list(args))
        return await real(repo, args, **kw)

    monkeypatch.setattr(reader, "_git_bytes", counting)
    base, _ = await flow_code.names_for("")
    first = len(calls)
    assert sum(1 for a in calls if a[:2] == ["cat-file", "--batch"]) == 1
    # 스냅샷 자체는 `ls-tree`·`.gitmodules`를 더 묻는다(blob 목록·submodule 규칙). 층 파일(`config/…`)을
    # `show`로 읽으면 안 된다 — 그게 사이트 28개 × 서비스 × 층의 프로세스였다.
    assert [a for a in calls if a[0] == "show" and "config/" in a[-1]] == []
    await flow_code.names_for("gumi")
    await flow_code.site_overrides("gumi", base)
    assert len(calls) == first                                     # 두 번째부터는 메모리에서


# ── R2-2b-1 — 골라서 전부: code.read 범위 · code.config key · grep은 코드 줄 먼저 ──

async def test_code_read는_범위를_주면_그_범위를_전부_준다(flow_code):
    """사내 실측: 라우터 파일이 400줄에서 잘려 핸들러를 못 봤다. `offset`·`limit`을 주면 그 줄들을 자르지 않고 준다."""
    whole = await flow_code.read("sink", "sink/writer.py")
    assert whole.status == "ok" and len(whole.data.splitlines()) == 3
    part = await flow_code.read("sink", "sink/writer.py", offset=2, limit=1)
    assert part.status == "ok" and part.data == whole.data.splitlines()[1]
    assert "L2-L2 / 전체 3줄" in part.source and part.envelope.complete
    tail = await flow_code.read("sink", "sink/writer.py", offset=3)
    assert tail.data == whole.data.splitlines()[2] and "L3-L3 / 전체 3줄" in tail.source
    beyond = await flow_code.read("sink", "sink/writer.py", offset=9)
    assert beyond.status == "error" and "전체 3줄" in beyond.error
    bad = await flow_code.read("sink", "sink/writer.py", offset=0)
    assert bad.status == "error" and "1 이상" in bad.error


async def test_code_config는_key로_좁히고_크면_키_지도를_준다(flow_code, monkeypatch):
    """통째 덤프는 첫 키에서 잘린다(사내 실측). `key`면 그 부분을 통째로, 없이 부르면 상한을 넘을 때 키 지도로 — 리드가
    어디를 좁혀 물을지 보이게."""
    from src.infrastructure import deployed_code as dc

    part = await flow_code.config("sink", key="infra.kafka.consumer.group_id")
    assert part.status == "ok" and part.data == "gumi-mx-core" and "key=infra.kafka.consumer.group_id" in part.source
    missing = await flow_code.config("sink", key="infra.nope")
    assert missing.status == "error" and "nope" in missing.error and "kafka" in missing.error
    whole = await flow_code.config("sink")
    assert whole.status == "ok" and whole.data["mongodb_collection"] == {"alarm": "alarm_events"}
    monkeypatch.setattr(dc, "_CONFIG_INLINE_CHARS", 20)
    big = await flow_code.config("sink")
    assert big.status == "ok" and "_키_지도" in big.data
    assert any(path.startswith("infra.") for path in big.data["_키_지도"])      # 두 단계 경로 — key=에 그대로 쓴다
    assert not big.envelope.complete and "key=" in big.envelope.truncated_reason


async def test_code_grep은_코드_줄을_먼저_문서_줄을_뒤에_둔다(tmp_path, flow_code):
    """사내 실측: 끝점 path로 grep하니 README만 걸려 증거 예산을 다 먹었다. 문서 줄을 버리지는 않는다 — 뒤로 보낸다."""
    root = tmp_path / "dt-core"
    # 리더의 기본 상한(400줄)보다 많은 문서 줄 — 리더가 먼저 자르면 트리 순서상 앞서는 README가 전부를 먹고 config 줄은
    # 영영 안 온다. 그래서 어댑터가 더 크게 받아 코드 줄을 앞세운 뒤 자기 상한(400줄)에서 자른다.
    (root / "README.md").write_text("alarm_events 컬렉션 설명\n" * 500, encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-qm", "readme", cwd=root)
    got = await flow_code.grep(["alarm_events"])
    assert got.status == "ok"
    paths = [line.split(":", 2)[1] for line in got.data.splitlines() if line.startswith("main:")]   # <커밋>:경로:줄:…
    code_lines = [i for i, path in enumerate(paths) if path.startswith("config/")]
    doc_lines = [i for i, path in enumerate(paths) if path == "README.md"]
    assert code_lines and doc_lines and max(code_lines) < min(doc_lines)
    assert "문서 줄" in got.data and len(got.data.splitlines()) <= 400
    assert not got.envelope.complete and "코드 줄 먼저" in got.envelope.truncated_reason


# ── R3-0 ③ — 서비스 이름으로 낸 code.flow·code.uses ──

async def test_code_flow에_서비스_이름을_주면_엣지_나열_대신_관계별_요약이다(flow_code):
    """사내 측정 #5: 서비스 하나의 이웃이 684줄이라 40줄에서 잘렸고 리드는 잘린 조각을 전부로 봤다. 서비스 단위에서 리드가 묻는 것은
    "무엇을 갖나"지 엣지의 출처가 아니다 — 관계별 개수와 이름 몇 개, 그리고 자원 이름으로 다시 내라는 안내."""
    graph = {"nodes": [{"id": "service_sink", "label": "sink", "type": "service", "repo": "dt-core"},
                       {"id": "topic_main", "label": "mx.alarm.main", "type": "topic"}]
             + [{"id": f"collection_c{i:02d}", "label": f"c{i:02d}", "type": "collection"} for i in range(12)],
             "links": [{"source": "service_sink", "target": "topic_main", "relation": "consumes", "origin": "config",
                        "confidence": "EXTRACTED", "source_file": "config/gbm/mx.json", "source_location": "L1"}]
             + [{"source": "service_sink", "target": f"collection_c{i:02d}", "relation": "declares", "origin": "config",
                 "confidence": "EXTRACTED", "source_file": "config/gbm/mx.json", "source_location": f"L{i + 2}"} for i in range(12)]}
    flow_code.attach_flow_graph(graph)
    got = await flow_code.flow("sink")
    assert got.status == "ok" and got.envelope.complete, got.error
    lines = got.data.splitlines()
    assert lines[0] == "sink [service · dt-core]: 관계 13개 — 엣지 나열 대신 관계별 요약"
    assert "  declares: 컬렉션 12 — c00, c01, c02, c03, c04, c05, c06, c07 외 4개" in lines
    assert "  consumes: 토픽 1 — mx.alarm.main" in lines
    assert lines[-1].startswith("  자원 하나의 배선") and "code.flow" in lines[-1]
    assert "—declares→" not in got.data                                           # 엣지 줄은 없다
    resource = await flow_code.flow("c00")
    assert resource.data.startswith("sink —declares→ c00 [collection]")             # 자원은 전처럼 엣지


async def test_code_uses에_서비스_이름을_주면_code_flow로_안내한다(flow_code):
    """사내 측정 #5: `code.uses name=<서비스>`가 "인덱스에 없다"로만 실패해 리드가 같은 이름으로 또 냈다."""
    from src.knowledge import index as ix
    from src.knowledge.flow import Name

    flow_code.attach_index(await ix.build_index({"dt-core": _Src()}, names=[Name("collection", "alarm_events", "mongodb_collection.alarm")],
                                                commits={"dt-core": "c0ffee"}))
    got = await flow_code.uses("sink")
    assert got.status == "error" and "서비스 이름이다" in got.error and "code.flow name='sink'" in got.error
    assert (await flow_code.uses("alarm_events")).status == "ok"


# ── R3-1c ①-a — code.resources ──

class _WithClass(_Src):
    FILES = {**_Src.FILES, "batch/__init__.py": "", "batch/job.py": (
        "class Refresh:\n"
        "    def _run(self, cfg, redis, producer):\n"
        "        src = redis.get(cfg[\"redis_key\"][\"alarm_source\"].format(line=\"L1\"))\n"
        "        producer.send(cfg[\"infra\"][\"kafka\"][\"producer\"][\"topic\"][\"topic1\"], src)\n"),
        "jobs/again.py": "class Refresh:\n    def go(self):\n        return 1\n"}


class _WithBase(_Src):
    """읽기는 부모(믹스인)의 `_run`에, 쓰기는 자식에 — 사내 처리기의 모양(측정 #13 추가분 3: 상속 38파일, 공용 필터 믹스인 셋)."""
    FILES = {**_Src.FILES, "batch/__init__.py": "",
             "batch/base.py": ("class Base:\n"
                               "    def _run(self, cfg, redis):\n"
                               "        src = redis.get(cfg[\"redis_key\"][\"alarm_source\"].format(line=\"L1\"))\n"
                               "        return self.handle(cfg, src)\n"),
             "batch/mid.py": "from batch.base import Base\n\n\nclass Mid(Base):\n    pass\n",
             "batch/sub.py": ("from batch.mid import Mid\n\n\n"
                              "class Sub(Mid):\n"
                              "    def handle(self, cfg, src):\n"
                              "        self.producer.send(cfg[\"infra\"][\"kafka\"][\"producer\"][\"topic\"][\"topic1\"], src)\n"),
             "batch/loop.py": "class A(B):\n    pass\n\n\nclass B(A):\n    pass\n"}


async def test_code_resources는_클래스가_읽고_쓰는_자원을_주고_없거나_여럿이면_실패로_답한다(flow_code):
    """입구가 값에서 고른 쓴 쪽 클래스의 **입력**이 다음 홉이다(R3-1c ①-a). 이름이 여럿이면 코드가 고르지 않는다(`callers`와 같다)."""
    from src.knowledge import index as ix
    from src.knowledge.flow import Name

    assert (await flow_code.resources("Refresh")).status == "error"                                   # 인덱스 없음
    idx = await ix.build_index({"dt-core": _WithClass()}, names=[Name("rediskey", "alarm:source:{line}", "redis_key.alarm_source"),
                                                                 Name("topic", "mx.alarm.main", "infra.kafka.producer.topic.topic1")],
                               commits={"dt-core": "c0ffee"})
    flow_code.attach_index(idx)
    many = await flow_code.resources("Refresh")
    assert many.status == "error" and "여럿" in many.error and "batch.job.Refresh" in many.error
    one = await flow_code.resources("batch.job.Refresh")
    assert one.status == "ok" and one.data.splitlines()[0].startswith("batch.job.Refresh (batch/job.py:L1) [dt-core")
    assert "  읽기 1:" in one.data and "alarm:source:{line} [rediskey] — batch.job.Refresh._run (batch/job.py:L3)" in one.data
    none = await flow_code.resources("Nope")
    assert none.status == "error" and "인덱스에 없다" in none.error
    assert flow_code.class_names() == frozenset({"Refresh"})
    flow_code.attach_index(None)
    assert flow_code.class_names() == frozenset()


# ── R3-1c ③ — code.read에 다른 서비스의 경로 ──

async def test_code_read에_다른_서비스의_경로를_주면_그_경로가_어느_서비스에_있는지_안내한다(clock):
    """측정 #8 P1 5: 리드가 `code.uses` 증거의 경로를 다른 서비스 이름으로 읽어 git의 `fatal: path`만 받았다. 인덱스가 그 경로를 **다른 레포**의
    모듈로 알면 그 서비스를 말한다 — 같은 레포에서 없는 경로는 git 말 그대로(인덱스와 배포 커밋이 다를 수 있어 꾸미지 않는다)."""
    from src.domain.envelope import ProbeResult
    from src.infrastructure.deployed_code import DeployedCode
    from src.knowledge import index as ix
    from src.knowledge.schema import Deployment, Service, Topology

    class Reader:
        async def show(self, repo, commit, path, *, whole=False):
            return ProbeResult.failed(f"fatal: path '{path}' does not exist in '{commit}'", source="w", clock=clock)

    topology = Topology(services={"api": Service(repo="dt-api", role="화면"), "sink": Service(repo="dt-core", role="저장한다")},
                        config_paths=LAYERS)
    code = DeployedCode(Reader(), topology, Deployment(), gbm="mx", fct="gumi", clock=clock)
    plain = await code.read("api", "sink/writer.py")
    assert plain.status == "error" and plain.error.startswith("fatal: path") and "이 경로는" not in plain.error    # 인덱스 없으면 git 말 그대로
    code.attach_index(await ix.build_index({"dt-core": _Src()}, commits={"dt-core": "c0ffee"}))
    got = await code.read("api", "sink/writer.py")
    assert got.status == "error" and got.error.startswith("fatal: path")
    assert "이 경로는 sink(dt-core)에 있다 — service='sink'로 다시 내라" in got.error
    same = await code.read("sink", "sink/nope.py")
    assert same.status == "error" and "이 경로는" not in same.error


# ── R3-3 ① — code.uses에 클래스 이름 ──

async def test_code_uses에_클래스_이름을_주면_code_resources로_안내한다(flow_code):
    """측정 #9 P1 6: 케이스 블록의 `쓴 쪽: <클래스>`를 보고 리드가 `code.uses <클래스>`를 냈다(r2·r3) — 클래스는 자원이 아니다. 어디로
    가야 하는지까지 말한다(서비스 이름 때와 같은 이유)."""
    from src.knowledge import index as ix

    class Src:
        FILES = {"batch/__init__.py": "",
                 "batch/refresh.py": "class SummaryRefresh:\n    def _run(self, redis):\n        return redis.get('alarm:source')\n"}

        async def files(self):
            return sorted(self.FILES)

        async def read(self, path):
            return self.FILES.get(path)

    flow_code.attach_index(await ix.build_index({"dt-core": Src()}, commits={"dt-core": "c0ffee"}))
    got = await flow_code.uses("SummaryRefresh")
    assert got.status == "error" and "클래스 이름이다" in got.error and "code.resources name='SummaryRefresh'" in got.error
    assert "인덱스에 없다" in (await flow_code.uses("없는이름")).error


# ── R3-3 ② — 클래스 → 서비스 ──

async def test_class_services는_클래스_이름을_그_파일의_서비스에_잇는다(flow_code):
    from src.knowledge import index as ix

    class Src:
        FILES = {"batch/__init__.py": "", "batch/refresh.py": "class SummaryRefresh:\n    pass\n\n\nclass Other:\n    pass\n"}

        async def files(self):
            return sorted(self.FILES)

        async def read(self, path):
            return self.FILES.get(path)

    assert flow_code.class_services() == {}
    flow_code.attach_index(await ix.build_index({"dt-core": Src()}, commits={"dt-core": "c0ffee"}))
    assert flow_code.class_services() == {"SummaryRefresh": ("sink",), "Other": ("sink",)}        # flow_code 토폴로지는 dt-core에 sink 하나


# ── R3-3 ②-b — code.resources service= ──

async def test_code_resources는_service로_레포를_좁히고_같은_이름이_두_서비스에_있으면_그렇게_안내한다(clock):
    """측정 #9 P1 4: 같은 클래스 이름이 두 레포에 있고 `파일:qualname`도 같아 그 안내로는 못 골랐다. `service=`로 레포를 좁힌다 — 걷기가
    선언 서비스로 가른 뒤 그 인자로 묻고, 사람은 CLI `--service`로. R3-4 ①(측정 #10 P0 2·P1 6·7): 한 레포를 여러 서비스가 `path` 없이
    공유하면 `owner`가 못 가른다 — `class_services`는 그 파일을 버리지 않고 **그 레포의 서비스 전부**로 잇고, 안내는 레포 단위로 세며,
    `repo=`로도 좁힌다. `code.uses`도 `service=`를 받는다(리드가 `code.resources`의 인자를 여기에도 냈다가 거부당했다)."""
    from src.domain.actions import action_problem
    from src.infrastructure.deployed_code import DeployedCode
    from src.knowledge import index as ix
    from src.knowledge.flow import Name
    from src.knowledge.schema import Deployment, Service, Topology

    class Src:
        def __init__(self, body):
            self.FILES = {"pipe/__init__.py": "", "pipe/job.py": body}

        async def files(self):
            return sorted(self.FILES)

        async def read(self, path):
            return self.FILES.get(path)

    body = "class Refresh:\n    def _run(self, redis):\n        return redis.get('alarm:source')\n"
    # dt-proc은 서비스 둘(processor·p2)이 `path` 없이 공유한다 — 사내 처리기 레포의 모양(서비스 5개)
    topology = Topology(services={"collector": Service(repo="dt-col", role="모은다"), "processor": Service(repo="dt-proc", role="가공한다"),
                                  "p2": Service(repo="dt-proc", role="가공한다")}, config_paths=LAYERS)
    code = DeployedCode(object(), topology, Deployment(), gbm="mx", fct="gumi", clock=clock)
    assert code.service_repos() == {"collector": "dt-col", "processor": "dt-proc", "p2": "dt-proc"}
    code.attach_index(await ix.build_index({"dt-col": Src(body), "dt-proc": Src(body)}, commits={"dt-col": "c1", "dt-proc": "c2"},
                                           names=[Name("rediskey", "alarm:source", "redis_key.alarm_source")]))
    assert code.class_services() == {"Refresh": ("collector", "p2", "processor")}               # 못 가른 파일 → 그 레포의 서비스 전부
    many = await code.resources("Refresh")
    assert many.status == "error" and "여럿" in many.error
    assert "같은 이름이 레포 2곳에 있다 — repo= 인자(또는 그 레포의 서비스로 service=)로 고른다: dt-col(collector), dt-proc(p2, processor)" in many.error
    one = await code.resources("Refresh", service="processor")
    assert one.status == "ok" and one.data.splitlines()[0].startswith("pipe.job.Refresh (pipe/job.py:L1) [dt-proc]")
    assert "service=processor" in one.source
    bad = await code.resources("Refresh", service="nope")
    assert bad.status == "error" and "없는 서비스" in bad.error
    byrepo = await code.resources("Refresh", repo="dt-proc")
    assert byrepo.status == "ok" and byrepo.data == one.data and "repo=dt-proc" in byrepo.source
    assert "없는 레포 — nope. 아는 것: dt-col, dt-proc" in (await code.resources("Refresh", repo="nope")).error
    assert action_problem("code.resources", {"name": "Refresh", "service": "processor"}) is None          # 등재 목록의 선택 인자
    assert action_problem("code.resources", {"name": "Refresh", "repo": "dt-proc"}) is None
    # code.uses service= — 그 서비스의 레포 안에서만
    everywhere = await code.uses("alarm:source")
    assert everywhere.status == "ok" and "[dt-col" in everywhere.data and "[dt-proc" in everywhere.data
    narrowed = await code.uses("alarm:source", service="collector")
    assert narrowed.status == "ok" and "[dt-col" in narrowed.data and "[dt-proc" not in narrowed.data and "service=collector" in narrowed.source
    assert "없는 서비스" in (await code.uses("alarm:source", service="nope")).error
    assert action_problem("code.uses", {"name": "alarm:source", "service": "collector"}) is None


# ── R3-3 ④-a — code.read line= : 그 줄을 품은 함수 전체 ──

async def test_code_read에_line을_주면_그_줄을_품은_가장_안쪽_함수_전체를_준다(tmp_path, flow_code):
    """걷기가 잡은 리터럴 줄(③)의 다음 홉은 "그 줄을 품은 함수"다(사람 결정). 인덱스의 심볼 줄 범위로 가장 안쪽 함수·메서드·클래스를
    통째로 — 줄 둘레 40줄은 함수가 길면 잘리고 짧으면 옆 함수를 섞는다. 인덱스에 그 줄을 품은 심볼이 없으면 줄 둘레로."""
    from src.domain.actions import action_problem
    from src.knowledge import index as ix

    text = "class Job:\n    def a(self):\n        return 1\n\n    def b(self):\n        x = 1\n        return x\n\n\ndef top():\n    pass\n"
    root = tmp_path / "dt-core"
    (root / "jobs").mkdir(exist_ok=True)
    (root / "jobs" / "run2.py").write_text(text, encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-qm", "fn", cwd=root)

    class Src:
        FILES = {"jobs/__init__.py": "", "jobs/run2.py": text}

        async def files(self):
            return sorted(self.FILES)

        async def read(self, path):
            return self.FILES.get(path)

    blind = await flow_code.read("sink", "jobs/run2.py", line=6)
    assert blind.status == "ok" and "줄 둘레" in blind.source and blind.data == text.rstrip("\n")                 # 인덱스 없음 → 둘레(파일이 짧다)
    flow_code.attach_index(await ix.build_index({"dt-core": Src()}, commits={"dt-core": "c0ffee"}))
    got = await flow_code.read("sink", "jobs/run2.py", line=6)
    assert got.status == "ok" and got.data == "    def b(self):\n        x = 1\n        return x"
    assert "L5-L7" in got.source and "jobs.run2.Job.b 전체" in got.source and got.envelope.complete
    whole = await flow_code.read("sink", "jobs/run2.py", line=1)
    assert whole.data.startswith("class Job:") and whole.data.endswith("return x") and "jobs.run2.Job 전체" in whole.source  # 클래스 줄은 클래스
    between = await flow_code.read("sink", "jobs/run2.py", line=9)
    assert between.status == "ok" and "품은 함수가 인덱스에 없어 줄 둘레" in between.source
    bad = await flow_code.read("sink", "jobs/run2.py", line=0)
    assert bad.status == "error" and "1 이상" in bad.error
    assert action_problem("code.read", {"service": "sink", "path": "jobs/run2.py", "line": 6}) is None       # 등재 목록의 선택 인자


# ── R3-4 ③ → R3-5 ③ — 단일값 검사의 범위와 짝 ──

REGISTRY = (
    'import pipe.filters as flt\n'
    'from pipe.filters import StateFilter\n'
    'from pipe.shapes import ShapeFilter\n'
    '\n'
    'register(component="state_filter")(StateFilter)\n'                                        # (a) 한 줄 단순 이름
    'register(component="state_filter", gbm="mx", factory="gumi")(flt.SiteFilter)\n'            # (b) 사이트 인자 + (c) 점 표기
    'register(component="state_filter", gbm="mx", factory="other")(flt.OtherFilter)\n'           # (b) 다른 사이트 — 후보 아님
    'register(\n'
    '    component="shape_filter",\n'
    ')(\n'
    '    ShapeFilter\n'                                                                           # (d) 여러 줄
    ')\n'
    'HELP = """예시: @register(component="doc_only")(Cls) — 문서 문자열, 등록 아님"""\n'
    'register(component="ghost")(GhostFilter)\n'                                                 # 인덱스에 없는 클래스 — 못 이음
)
FILES = {"pipe/__init__.py": "",
         "pipe/job.py": 'class Job:\n    def _run(self, rows):\n        self.components.get("shape_filter")\n'
                        '        rows = self.components.get("state_filter").apply(rows)\n        self.components.get("ghost")\n        kind = self.cfg.get("kind")\n'
                        '        if "-" == rows[0].get("state"):\n            return []\n        return rows\n',
         "pipe/filters.py": 'class StateFilter:\n    def apply(self, rows):\n        return [r for r in rows if r["state"] != "-"]\n\n\n'
                            'class SiteFilter:\n    def apply(self, rows):\n        return [r for r in rows if r.get("state") != "-" and r["kind"] != "x"]\n\n\n'
                            'class OtherFilter:\n    def apply(self, rows):\n        return [r for r in rows if r["other"] == "-"]\n',
         "pipe/shapes.py": 'class ShapeFilter:\n    def apply(self, rows):\n        return rows\n',
         "pipe/registry.py": REGISTRY,
         "pipe/other.py": 'DEFAULTS = {"state": "-"}\n\n\ndef lookup(r):\n    return r.get("state")\n',                 # 사전 리터럴 — 비교식이 아니다
         "config/gbm/mx.json": '{"state": "-", "k": {"state": "-"}}\n',                                                  # config — 범위 밖
         "pipe/var.py": 'class Var:\n    def _run(self, name):\n        self.components.get("state_filter")\n        return self.components.get(name)\n'}


class _FilterSrc:
    async def files(self):
        return sorted(FILES)

    async def read(self, path):
        return FILES.get(path)


def test_registrations는_AST로_네_모양의_등록을_읽고_문서_문자열_속_예시는_등록으로_안_센다():
    """사람 결정(10-15): 등록 모음 파일의 모양은 (a) 한 줄 단순 이름, (b) 사이트 인자, (c) 점 표기(import 별칭), (d) 여러 줄 — 정규식은 (c)(d)를 놓치고
    문서 문자열 속 `@register` 예시를 등록으로 센다. AST: `register` 호출의 `component` 상수 + 바깥 호출 인자(Name, 또는 Attribute의 끝 이름 — 별칭은 모듈)."""
    from src.infrastructure.deployed_code import registrations
    got = [(r.component, r.cls, r.module, r.site, r.line) for r in registrations("pipe/registry.py", REGISTRY)]
    assert got == [("state_filter", "StateFilter", None, {}, 5),
                   ("state_filter", "SiteFilter", "pipe.filters", {"gbm": "mx", "factory": "gumi"}, 6),
                   ("state_filter", "OtherFilter", "pipe.filters", {"gbm": "mx", "factory": "other"}, 7),
                   ("shape_filter", "ShapeFilter", None, {}, 8),
                   ("ghost", "GhostFilter", None, {}, 14)]
    deco = "@register(component=\"x\")\nclass Deco:\n    pass\n"
    assert [(r.component, r.cls, r.line) for r in registrations("d.py", deco)] == [("x", "Deco", 2)]        # 데코레이터 형도 같은 모양
    assert registrations("bad.py", "def (:\n") == []                                                      # 못 읽는 파일은 등록 없음(던지지 않는다)


async def test_filter_lines는_이_사이트의_등록이_가리키는_클래스_파일을_범위로_비교식을_먼저_뽑아_단일값_전부와_맞춘다(clock):
    """측정 #10 P0 1 → #11 P0 3: 범위는 등록 **줄**의 파일이 아니라 `register(component="X")(<클래스>)`의 클래스를 인덱스로 푼 파일. 사이트 인자가 전부
    이 사이트 값과 같은 등록만 후보이고 그중 명시 인자가 많은 것(같으면 여럿). 비교식을 먼저 뽑아 단일값 전부와 맞춘다(상한 없음). 못 이은 것(변수·
    등록 없음·인덱스에 없음)은 `unlinked`. 던지지 않는다 — 조회가 안 되면 `problem`."""
    from src.domain.envelope import ProbeResult
    from src.infrastructure.deployed_code import DeployedCode, comparison_lines, comparison_pairs, component_names
    from src.knowledge import index as ix
    from src.knowledge.schema import Deployment, Service, Topology

    class Reader:
        def __init__(self, ok=True):
            self.ok = ok

        async def snapshot(self, repo, commit):
            return (ProbeResult.succeeded(dict(FILES), source="s", clock=clock) if self.ok
                    else ProbeResult.failed("옛 git", source="s", clock=clock))

    topology = Topology(services={"processor": Service(repo="dt-proc", role="가공한다")})
    code = DeployedCode(Reader(), topology, Deployment(), gbm="mx", fct="gumi", clock=clock)
    assert (await code.filter_lines("Job", fields={"state": "-"})).problem == "심볼 인덱스가 없다"
    index = await ix.build_index({"dt-proc": _FilterSrc()}, commits={"dt-proc": "c2"})
    code.attach_index(index)
    scan = await code.filter_lines("Job", fields={"state": "-", "kind": "x"})
    assert scan.problem == "" and scan.checked == ["state", "kind"]
    assert scan.files == ["pipe/job.py", "pipe/shapes.py", "pipe/filters.py"] and scan.linked == ["shape_filter", "state_filter"]
    # 범위는 **파일**(사람 결정) — 사이트 인자가 맞는 등록(SiteFilter)의 파일에 (a)의 StateFilter도 있어 그 줄(L3)도 든다. 두 필드가 한 줄의 비교식에 각각
    assert [(h.path, h.line, h.field, h.value) for h in scan.hits] == [("pipe/job.py", 7, "state", "-"), ("pipe/filters.py", 3, "state", "-"),
                                                                       ("pipe/filters.py", 8, "state", "-"), ("pipe/filters.py", 8, "kind", "x")]   # 파일 차례: 클래스 → 이은 것
    assert scan.hits[2].text == 'return [r for r in rows if r.get("state") != "-" and r["kind"] != "x"]' and scan.hits[0].repo == "dt-proc"
    # 뒤 칸의 자리는 등록 줄이 아니라 **클래스**의 자리 — 나쁜 필드 이름이 리터럴로 든 파일 먼저
    assert [(h.path, h.line) for h in scan.linked_at] == [("pipe/filters.py", 6), ("pipe/shapes.py", 1)] and "register(component='state_filter')" in scan.linked_at[0].text
    assert scan.unlinked == ["ghost: GhostFilter(인덱스에 없음)"]                                                 # 사전 get("kind")은 등록이 없다 — 못 이음 아님
    plain = await code.filter_lines("Job", fields={})
    assert plain.hits == [] and plain.checked == [] and [(h.path, h.line) for h in plain.linked_at] == [("pipe/shapes.py", 1), ("pipe/filters.py", 6)]   # 필드 없으면 부른 차례
    assert (await code.filter_lines("Job", fields={"state": "x"})).hits == []
    var = await code.filter_lines("Var", fields={"state": "-"})
    assert var.files == ["pipe/var.py", "pipe/filters.py"] and var.unlinked == ["변수 name"] and [h.line for h in var.hits] == [3, 8]
    assert "인덱스에 없다" in (await code.filter_lines("Nope", fields={})).problem
    assert "없는 서비스" in (await code.filter_lines("Job", fields={}, service="nope")).problem
    assert "없는 레포" in (await code.filter_lines("Job", fields={}, repo="nope")).problem
    assert (await code.filter_lines("Job", fields={}, service="processor")).files[0] == "pipe/job.py"
    assert (await code.filter_lines("Job", fields={}, repo="dt-proc")).files[0] == "pipe/job.py"
    broken = DeployedCode(Reader(ok=False), topology, Deployment(), gbm="mx", fct="gumi", clock=clock)
    broken.attach_index(index)
    assert (await broken.filter_lines("Job", fields={"state": "-"})).problem == "dt-proc의 스냅샷을 못 받았다"
    # 다른 사이트면 (b)의 다른 등록 — 후보가 없으면 "이 사이트의 등록 없음"
    other = DeployedCode(Reader(), topology, Deployment(), gbm="mx", fct="other", clock=clock)
    other.attach_index(index)
    assert [(h.path, h.line) for h in (await other.filter_lines("Job", fields={})).linked_at] == [("pipe/shapes.py", 1), ("pipe/filters.py", 11)]
    third = DeployedCode(Reader(), topology, Deployment(), gbm="mx", fct="third", clock=clock)
    third.attach_index(index)
    scan3 = await third.filter_lines("Job", fields={})
    assert [(h.path, h.line) for h in scan3.linked_at] == [("pipe/shapes.py", 1), ("pipe/filters.py", 1)]        # 사이트 인자 없는 (a)만 후보
    # 순수 조각 — 짝 전부, 특정 짝, 컴포넌트 이름(같은 수신자의 변수만 "못 이음")
    text = 'x = {"f": "v"}\n# r["f"] != "v"\nok = r["f"] != "v"\nrev = "v" == r.get("f", None)\nnm = r[f] != "v"\nraw = r["f"] != v\nat = getattr(r, "f") == "v"\n'
    assert comparison_pairs(text) == [(3, "f", "v", 'ok = r["f"] != "v"'), (4, "f", "v", 'rev = "v" == r.get("f", None)'), (7, "f", "v", 'at = getattr(r, "f") == "v"')]
    assert comparison_lines(text, "f", "v") == [(3, 'ok = r["f"] != "v"'), (4, 'rev = "v" == r.get("f", None)'), (7, 'at = getattr(r, "f") == "v"')]
    assert component_names('a = reg.get("x")\nb = reg.get(\'y\', None)\nc = reg.get(key)\nd = reg.get("x")\ne = cfg.get(other)\n') == (["x", "y"], ["key"])



# ── R3-5d ③ — 상속: 부모 클래스의 자원과 파일 ──

async def test_code_resources는_부모_클래스의_멤버_자원까지_세고_어느_부모의_것인지_적는다(flow_code):
    """측정 #13 추가분 3: 읽기가 부모(믹스인)의 `_run`에 있는 클래스는 "인덱스 자원이 자기 출력뿐"이었다(`<BMS 배지>` 5종). 인덱스는 처음부터
    `inherits` 엣지를 저장하니 그 엣지로 부모의 멤버까지 — 줄의 메서드 자리가 부모의 것이라 어느 부모인지 보인다. 순환은 한 번만."""
    from src.knowledge import index as ix
    from src.knowledge import query as qy
    from src.knowledge.flow import Name

    idx = await ix.build_index({"dt-core": _WithBase()}, names=[Name("rediskey", "alarm:source:{line}", "redis_key.alarm_source"),
                                                                Name("topic", "mx.alarm.main", "infra.kafka.producer.topic.topic1")],
                               commits={"dt-core": "c0ffee"})
    flow_code.attach_index(idx)
    sub = await flow_code.resources("Sub")
    assert sub.status == "ok" and sub.data.splitlines()[0].startswith("batch.sub.Sub (batch/sub.py:L4)")
    assert "  부모 클래스의 자원 포함: batch.mid.Mid, batch.base.Base" in sub.data.splitlines()[1]
    assert "  읽기 1:" in sub.data and "alarm:source:{line} [rediskey] — batch.base.Base._run (batch/base.py:L3)" in sub.data
    assert "  쓰기 1:" in sub.data and "mx.alarm.main [topic] — batch.sub.Sub.handle (batch/sub.py:L6)" in sub.data
    base = await flow_code.resources("Base")
    assert "부모 클래스" not in base.data and "  읽기 1:" in base.data                                       # 부모가 없으면 줄도 없다
    sid = next(s.id for s in idx.symbols if s.kind == "class" and s.name == "Sub")
    assert [idx.symbols[a].name for a in qy.ancestors(idx, sid)] == ["Mid", "Base"]                       # 가까운 것부터
    loop = next(s.id for s in idx.symbols if s.kind == "class" and s.name == "A")
    assert [idx.symbols[a].name for a in qy.ancestors(idx, loop)] == ["B"]                                   # 순환은 한 번만


async def test_filter_lines의_범위와_컴포넌트_링크에_부모_클래스_파일이_든다(clock):
    """측정 #13 추가분 3: 단일값 검사 범위에 부모 믹스인 파일이 없었다 — 비교식이 거기 있으면 못 잡는다. 부모의 `_run`이 부르는 `get("X")`도 링크다."""
    from src.domain.envelope import ProbeResult
    from src.infrastructure.deployed_code import DeployedCode
    from src.knowledge import index as ix
    from src.knowledge.schema import Deployment, Service, Topology

    files = {**FILES, "pipe/base.py": 'class JobBase:\n    def _run(self, rows):\n        rows = self.components.get("shape_filter").apply(rows)\n'
                                       '        return [r for r in rows if r["state"] != "-"]\n',
             "pipe/child.py": "from pipe.base import JobBase\n\n\nclass Child(JobBase):\n    pass\n"}

    class Src:
        async def files(self):
            return sorted(files)

        async def read(self, path):
            return files.get(path)

    class Reader:
        async def snapshot(self, repo, commit):
            return ProbeResult.succeeded(dict(files), source="s", clock=clock)

    code = DeployedCode(Reader(), Topology(services={"processor": Service(repo="dt-proc", role="가공한다")}), Deployment(), gbm="mx", fct="gumi", clock=clock)
    code.attach_index(await ix.build_index({"dt-proc": Src()}, commits={"dt-proc": "c2"}))
    scan = await code.filter_lines("Child", fields={"state": "-"})
    assert scan.files == ["pipe/child.py", "pipe/base.py", "pipe/shapes.py"] and scan.linked == ["shape_filter"]
    assert [(h.path, h.line) for h in scan.hits] == [("pipe/base.py", 4)]
