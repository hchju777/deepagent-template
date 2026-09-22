"""데이터 흐름 그래프(11c) — **서비스 ↔ 자원을 코드에서, LLM 없이, 근거 줄과 함께.**

측정판(`tools/local_case.py`)의 파일을 그대로 쓴다. graphify의 코드 패스는 이 파일들에서
서비스 사이 엣지를 0개 냈다(`STEPS/step-11c-flow.md`). 여기서 나와야 하는 것은 그 0개다.
"""
import json

import pytest

from src.knowledge import flow
from src.knowledge.flow import Hit, Name
from src.knowledge.schema import FlowSpec, Service, Topology
from src.knowledge.target_config import merge_target
from tools.local_case import API_FILES, CORE_FILES

REPOS = {"dt-core": CORE_FILES, "dt-api": API_FILES}
COMMITS = {"dt-core": "ab12cd34ef56", "dt-api": "9876fedcba01"}
TOPOLOGY = Topology(services={
    "processor": Service(repo="dt-core", role="원천 이벤트를 받아 가공한다",
                         selects={"SERVICE_ROLE": "processor"}),
    "sink": Service(repo="dt-core", role="가공된 결과를 저장한다",
                    selects={"SERVICE_ROLE": "sink"}),
    "api": Service(repo="dt-api", role="저장된 것을 API 응답으로 바꾼다")})


def _text(content) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)


def hits_for(pattern: str) -> list[Hit]:
    """`git grep -n` 흉내 — 부분 문자열, 줄 번호는 1부터, 앞뒤 한 줄이 context."""
    out = []
    for repo, files in REPOS.items():
        for path, content in files.items():
            lines = _text(content).splitlines()
            for i, line in enumerate(lines):
                if pattern in line:
                    ctx = "\n".join(lines[max(0, i - 1):i + 2])
                    out.append(Hit(repo, COMMITS[repo], path, i + 1, line, ctx))
    return out


def merged(repo: str, fct: str = "gumi") -> dict:
    layers = [f"config/gbm/mx.json", f"config/factories/{fct}/common.json",
              f"config/factories/{fct}/mx.json"]
    return merge_target([(p, REPOS[repo][p]) for p in layers if p in REPOS[repo]])


def names() -> list[Name]:
    spec = FlowSpec()
    seen = {}
    for repo in REPOS:
        for n in flow.names_from_config(merged(repo), spec.name_paths):
            seen[(n.kind, n.value, n.key_path)] = n
    return list(seen.values())


def graph() -> dict:
    return flow.extract(names=names(), topology=TOPOLOGY, hits_for=hits_for, commits=COMMITS)


def _edges(g, relation=None):
    return {(flow.label_of(g, e["source"]), e["relation"], flow.label_of(g, e["target"]))
            for e in g["links"] if relation is None or e["relation"] == relation}


# ── 이름 ─────────────────────────────────────────────────────────────

def test_합친_config에서_이름을_뽑는다():
    got = {(n.kind, n.value, n.key_path) for n in flow.names_from_config(merged("dt-core"),
                                                                        FlowSpec().name_paths)}
    assert ("topic", "mx.alarm.main", "kafka.topics.alarm_main") in got
    assert ("group", "gumi-mx-sink", "kafka.groups.sink") in got      # gumi 층이 덮은 값
    assert ("collection", "alarm_events", "mongo.collections.alarm") in got
    assert ("rediskey", "alarm:stats:{line}", "redis.keys.alarm_stats") in got


def test_템플릿_이름은_앞부분만_찾는다():
    n = Name("rediskey", "alarm:stats:{line}", "redis.keys.alarm_stats")
    assert n.literal == "alarm:stats:" and n.key_token == "alarm_stats" and n.parent_token == "keys"
    assert n.patterns == ("alarm:stats:", "alarm_stats")


def test_없는_경로는_건너뛴다():
    assert flow.names_from_config({"kafka": {}}, {"topic": ["kafka.topics"], "group": ["nope.x"]}) == []


def test_모르는_자원_종류는_거부한다():
    with pytest.raises(ValueError, match="모르는 자원 종류"):
        FlowSpec(name_paths={"queue": ["mq.queues"]})


# ── 방향 ─────────────────────────────────────────────────────────────

def test_동사로_방향을_정한다():
    assert flow.direction('consumer.subscribe(topics["alarm_raw"])') == "reads"
    assert flow.direction('producer.send(topics["alarm_main"], event)') == "writes"
    assert flow.direction("coll.find(q).insert_one(x)") == "ambiguous"
    assert flow.direction('coll = mongo[cfg["mongo"]["collections"]["alarm"]]') is None
    assert flow.direction("counter.reset()") is None, "`reset`은 `set`이 아니다"


# ── 귀속 ─────────────────────────────────────────────────────────────

def test_파일을_서비스에_붙인다():
    assert flow.owner("api/alarms.py", "dt-api", TOPOLOGY) == ("api", "EXTRACTED")
    assert flow.owner("sink/writer.py", "dt-core", TOPOLOGY) == ("sink", "INFERRED")
    assert flow.owner("common/util.py", "dt-core", TOPOLOGY) == (None, "AMBIGUOUS")
    with_path = Topology(services={"a": Service(repo="r", path="svc/a"),
                                   "b": Service(repo="r", path="svc/b")})
    assert flow.owner("svc/b/main.py", "r", with_path) == ("b", "EXTRACTED")


# ── 끝까지 ───────────────────────────────────────────────────────────

def test_측정판_코드에서_흐름이_나온다():
    """**graphify가 0개 낸 그 엣지들이다.**"""
    g = graph()
    edges = _edges(g)
    for want in [("processor", "consumes", "mx.alarm.raw"),
                 ("processor", "produces", "mx.alarm.main"),
                 ("sink", "consumes", "mx.alarm.main"),
                 ("sink", "consumes_as", "gumi-mx-sink"),
                 ("sink", "writes", "alarm_events"),
                 ("api", "reads", "alarm_events"),
                 ("dt-core", "declares", "mx.alarm.main")]:
        assert want in edges, f"{want}가 없다 — {sorted(edges)}"


def test_엣지마다_근거_줄과_커밋이_있다():
    g = graph()
    e = next(e for e in g["links"] if e["relation"] == "writes"
             and flow.label_of(g, e["target"]) == "alarm_events")
    assert e["source_file"] == "sink/writer.py" and e["source_location"].startswith("L")
    assert e["commit"] == COMMITS["dt-core"] and "insert_many" in e["text"]
    assert e["confidence"] in ("EXTRACTED", "INFERRED", "AMBIGUOUS")


def test_키_토큰은_부모_키가_같은_줄에_있어야_한다():
    """`format(service="processor")`의 `processor`는 그룹 `gumi-mx-processor`가 아니다.
    짧은 토큰은 어디에나 있어서, 부모 키(`groups`) 없이 맞추면 거짓 엣지가 는다."""
    g = graph()
    heartbeat_lines = [e for e in g["links"]
                       if flow.label_of(g, e["target"]) == "gumi-mx-processor"
                       and "heartbeat" in e["text"]]
    assert heartbeat_lines == []


def test_동사가_없는_줄은_mentions로_남긴다():
    """방향은 몰라도 "이 서비스가 이 이름을 안다"는 사실은 남긴다 — 어느 서비스를 볼지
    고르는 데는 그것으로 충분하다."""
    hits = lambda p: [Hit("dt-api", "c", "api/alarms.py", 3, 'name = cfg["mongo"]["collections"]["alarm"]')]
    g = flow.extract(names=[Name("collection", "alarm_events", "mongo.collections.alarm")],
                     topology=TOPOLOGY, hits_for=hits, commits=COMMITS)
    assert ("api", "mentions", "alarm_events") in _edges(g)
    assert all(e["confidence"] == "AMBIGUOUS" for e in g["links"])


def test_같은_커밋이면_같은_JSON이다():
    """결정론. graphify가 약속하는 것과 같은 성질을 우리 층도 지킨다."""
    assert json.dumps(graph(), sort_keys=True) == json.dumps(graph(), sort_keys=True)


def test_graphify_스키마다():
    """`graphify merge-graphs`가 그대로 먹어야 한다 — 실험에서 이 키들로 합쳐 `path`가 났다."""
    g = graph()
    assert {"directed", "multigraph", "nodes", "links"} <= set(g)
    assert all({"id", "label", "source_file", "source_location"} <= set(n) for n in g["nodes"])
    assert all({"source", "target", "relation", "confidence"} <= set(e) for e in g["links"])
    ids = {n["id"] for n in g["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in g["links"])


# ── 질의 ─────────────────────────────────────────────────────────────

def test_processor에서_sink까지_경로는_토픽을_지난다():
    """둘 다 쓰는 하트비트 키(`hb:{service}`)도 2홉이지만 그건 흐름이 아니다.
    쓰기→자원→읽기만 통과해야 데이터가 실제로 가는 길이 나온다."""
    g = graph()
    path = flow.shortest_path(g, "processor", "sink")
    assert path is not None and len(path) == 2
    assert flow.render_path(g, path) == "processor —produces→ mx.alarm.main ←consumes— sink"


def test_흐름이_없으면_경로도_없다():
    """api는 읽기만 한다 — api에서 processor로 가는 데이터는 없다. 방향을 무시하면
    "관계는 있다"가 나오는데, 그건 다른 질문이다."""
    g = graph()
    assert flow.shortest_path(g, "api", "processor") is None
    assert flow.shortest_path(g, "api", "processor", undirected=True) is not None


def test_이웃은_읽고_쓰는_서비스를_보여준다():
    g = graph()
    near = flow.neighbors(g, "alarm_events", depth=1)
    who = {(flow.label_of(g, e["source"]), e["relation"]) for e in near}
    assert ("sink", "writes") in who and ("api", "reads") in who


def test_없는_이름은_빈_답이다():
    g = graph()
    assert flow.neighbors(g, "없는것") == [] and flow.shortest_path(g, "processor", "없는것") is None
