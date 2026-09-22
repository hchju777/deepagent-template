"""데이터 흐름 그래프(11c) — **서비스 ↔ 자원을 코드에서, LLM 없이, 근거 줄과 함께.**

측정판(`tools/local_case.py`)의 파일을 그대로 쓴다. 그 config는 **사내 모양**이다:
`infra`는 레포당 하나라 processor·sink가 공유하고 컨슈머 그룹도 같다, 토픽 키는 `topic1`·
`topic2`, 컬렉션·redis 키는 `infra` 밖 최상위. graphify의 코드 패스는 이 파일들에서 서비스
사이 엣지를 0개 냈다(`STEPS/step-11c-flow.md`). 여기서 나와야 하는 것은 그 0개다.
"""
import json

import pytest

from src.knowledge import flow
from src.knowledge.flow import Hit, Name
from src.knowledge.schema import FlowSource, FlowSpec, Service, Topology
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
    """`git grep -n` 흉내 — 부분 문자열, 줄 번호는 1부터."""
    out = []
    for repo, files in REPOS.items():
        for path, content in files.items():
            for i, line in enumerate(_text(content).splitlines()):
                if pattern in line:
                    out.append(Hit(repo, COMMITS[repo], path, i + 1, line))
    return out


def merged(repo: str, fct: str = "gumi") -> dict:
    layers = ["config/gbm/mx.json", f"config/factories/{fct}/common.json",
              f"config/factories/{fct}/mx.json"]
    return merge_target([(p, REPOS[repo][p]) for p in layers if p in REPOS[repo]])


def names() -> list[Name]:
    seen = {}
    for repo in REPOS:
        for n in flow.names_from_config(merged(repo), FlowSpec().sources):
            seen[(n.kind, n.value, n.key_path)] = n
    return list(seen.values())


def graph() -> dict:
    return flow.extract(names=names(), topology=TOPOLOGY, hits_for=hits_for, commits=COMMITS)


def _edges(g, relation=None):
    return {(flow.label_of(g, e["source"]), e["relation"], flow.label_of(g, e["target"]))
            for e in g["links"] if relation is None or e["relation"] == relation}


# ── 이름 ─────────────────────────────────────────────────────────────

def test_사내_모양의_config에서_이름을_뽑는다():
    got = {(n.kind, n.value, n.key_path, n.relation)
           for n in flow.names_from_config(merged("dt-core"), FlowSpec().sources)}
    assert ("topic", "mx.alarm.raw", "infra.kafka.consumer.topic.topic1", "consumes") in got
    assert ("topic", "mx.alarm.main", "infra.kafka.producer.topic.topic1", "produces") in got
    assert ("group", "gumi-mx-core", "infra.kafka.consumer.group_id", "consumes_as") in got  # gumi 층이 덮은 값
    assert ("collection", "alarm_events", "mongodb_collection.alarm", None) in got
    assert ("rediskey", "alarm:stats:{line}", "redis_key.alarm_stats", None) in got


def test_템플릿_이름은_앞부분만_찾는다():
    n = Name("rediskey", "alarm:stats:{line}", "redis_key.alarm_stats")
    assert n.literal == "alarm:stats:" and n.key_token == "alarm_stats"
    assert n.patterns == ("alarm:stats:", "alarm_stats")


def test_키_토큰은_조상_둘을_요구한다():
    """소비·생산 토픽이 둘 다 `topic1`이다. 부모(`topic`) 하나로는 못 가른다."""
    consume = Name("topic", "mx.alarm.raw", "infra.kafka.consumer.topic.topic1", "consumes")
    produce = Name("topic", "mx.alarm.main", "infra.kafka.producer.topic.topic1", "produces")
    assert consume.required_tokens == ("consumer", "topic")
    assert produce.required_tokens == ("producer", "topic")
    assert Name("collection", "alarm_events", "mongodb_collection.alarm").required_tokens == ("mongodb_collection",)


def test_없는_경로는_건너뛴다():
    assert flow.names_from_config({"infra": {}}, FlowSpec().sources) == []


def test_모르는_자원_종류나_관계는_거부한다():
    with pytest.raises(ValueError):
        FlowSource(path="mq.queues", kind="queue")
    with pytest.raises(ValueError):
        FlowSource(path="x", kind="topic", relation="touches")


# ── 방향 ─────────────────────────────────────────────────────────────

def test_동사로_방향을_정한다():
    assert flow.direction('consumer.subscribe(topics["alarm_raw"])') == "reads"
    assert flow.direction('producer.send(topics["alarm_main"], event)') == "writes"
    assert flow.direction("coll.find(q).insert_one(x)") == "ambiguous"
    assert flow.direction('coll = mongo[cfg["mongo"]["collections"]["alarm"]]') is None
    assert flow.direction("counter.reset()") is None, "`reset`은 `set`이 아니다"
    assert flow.direction("mongo[c].insert_many(batch)") == "writes", "조각으로 맞춘다"


# ── 귀속 ─────────────────────────────────────────────────────────────

def test_파일을_서비스에_붙인다():
    assert flow.owner("api/alarms.py", "dt-api", TOPOLOGY) == ("api", "EXTRACTED")
    assert flow.owner("sink/writer.py", "dt-core", TOPOLOGY) == ("sink", "INFERRED")
    assert flow.owner("common/util.py", "dt-core", TOPOLOGY) == (None, "AMBIGUOUS")
    with_path = Topology(services={"a": Service(repo="r", path="svc/a"),
                                   "b": Service(repo="r", path="svc/b")})
    assert flow.owner("svc/b/main.py", "r", with_path) == ("b", "EXTRACTED")


def test_config는_레포당_하나라_공유_레포면_주인이_없다():
    assert flow.config_owner("dt-api", TOPOLOGY) == "api"
    assert flow.config_owner("dt-core", TOPOLOGY) is None


# ── 끝까지 ───────────────────────────────────────────────────────────

def test_관계_있는_출처는_config_선언이_곧_엣지다():
    """`consumer.topic`에 있으면 소비, `producer.topic`에 있으면 생산. 공유 레포(dt-core)라
    레포 노드에 붙고, 단독 레포(dt-api)면 서비스에 붙는다."""
    g = graph()
    config_edges = {(flow.label_of(g, e["source"]), e["relation"], flow.label_of(g, e["target"]))
                    for e in g["links"] if e["source_file"].startswith("config/")}
    assert ("dt-core", "consumes", "mx.alarm.raw") in config_edges
    assert ("dt-core", "consumes", "mx.alarm.main") in config_edges
    assert ("dt-core", "produces", "mx.alarm.main") in config_edges
    assert ("dt-core", "consumes_as", "gumi-mx-core") in config_edges
    assert ("api", "declares", "alarm_events") in config_edges         # 관계 없는 출처는 선언, 단독 레포라 서비스에
    shared = [e for e in g["links"] if e["source_file"].startswith("config/") and e["repo"] == "dt-core"]
    assert shared and all(e["attributed"] == "repo" and e["confidence"] == "EXTRACTED" for e in shared)


def test_코드가_공유_레포의_서비스를_가른다():
    """config는 "dt-core의 누군가"까지만 안다. 어느 서비스인지는 코드 줄이 말한다 —
    graphify가 0개 낸 그 엣지들이다."""
    g = graph()
    edges = _edges(g)
    for want in [("processor", "consumes", "mx.alarm.raw"),
                 ("processor", "produces", "mx.alarm.main"),
                 ("processor", "consumes_as", "gumi-mx-core"),
                 ("sink", "consumes", "mx.alarm.main"),
                 ("sink", "consumes_as", "gumi-mx-core"),
                 ("sink", "writes", "alarm_events"),
                 ("api", "reads", "alarm_events"),
                 ("api", "reads", "alarm:stats:{line}"),
                 ("sink", "writes", "alarm:stats:{line}")]:
        assert want in edges, f"{want}가 없다 — {sorted(edges)}"
    # 소비·생산 토픽 키가 둘 다 `topic1`이지만 조상 토큰이 갈라 준다
    assert ("processor", "consumes", "mx.alarm.main") not in edges
    assert ("sink", "produces", "mx.alarm.main") not in edges


def test_엣지마다_근거_줄과_커밋이_있다():
    g = graph()
    e = next(e for e in g["links"] if e["relation"] == "writes"
             and flow.label_of(g, e["target"]) == "alarm_events")
    assert e["source_file"] == "sink/writer.py" and e["source_location"].startswith("L")
    assert e["commit"] == COMMITS["dt-core"] and "insert_many" in e["text"]
    assert e["confidence"] in ("EXTRACTED", "INFERRED", "AMBIGUOUS")


def test_키_토큰은_조상_키가_같은_줄에_있어야_한다():
    """`format(service="processor")`의 `processor`는 그룹이 아니다. 짧은 토큰은 어디에나
    있어서, 조상 키 없이 맞추면 거짓 엣지가 는다."""
    line = 'redis.set(cfg["redis_key"]["heartbeat"].format(service="processor"), now())'
    hits = lambda p: [Hit("dt-core", "c", "processor/handler.py", 9, line)] if p in line else []
    g = flow.extract(names=[Name("group", "gumi-mx-processor", "infra.kafka.consumer.groups.processor", "consumes_as")],
                     topology=TOPOLOGY, hits_for=hits, commits=COMMITS)
    assert not [e for e in g["links"] if e["source_file"] == "processor/handler.py"]


def test_동사가_없는_줄은_mentions로_남긴다():
    """방향은 몰라도 "이 서비스가 이 이름을 안다"는 사실은 남긴다."""
    hits = lambda p: [Hit("dt-api", "c", "api/alarms.py", 3, 'name = cfg["mongodb_collection"]["alarm"]')]
    g = flow.extract(names=[Name("collection", "alarm_events", "mongodb_collection.alarm")],
                     topology=TOPOLOGY, hits_for=hits, commits=COMMITS)
    assert ("api", "mentions", "alarm_events") in _edges(g)
    assert all(e["confidence"] == "AMBIGUOUS" for e in g["links"])


def test_같은_커밋이면_같은_JSON이다():
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
    """둘 다 쓰는 하트비트 키(`hb:{service}`)도 2홉이지만 그건 흐름이 아니다."""
    g = graph()
    path = flow.shortest_path(g, "processor", "sink")
    assert path is not None and len(path) == 2
    assert flow.render_path(g, path) == "processor —produces→ mx.alarm.main ←consumes— sink"


def test_흐름이_없으면_경로도_없다():
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


# ── 권고 ─────────────────────────────────────────────────────────────

def test_요약과_권고():
    g = graph()
    s = flow.summary(g)
    assert s["nodes"] > 5 and s["links"] > 5 and s["repo_level"] > 0
    advice = "\n".join(flow.advise(g, TOPOLOGY))
    assert "서비스를 못 가른 엣지" in advice and "dt-core" in advice
    assert "line_state" in advice, "선언만 되고 안 쓰는 이름을 짚어야 한다"


def test_이름이_하나도_없으면_그렇게_말한다():
    empty = flow.extract(names=[], topology=TOPOLOGY, hits_for=lambda p: [], commits=COMMITS)
    assert any("flow.sources" in line for line in flow.advise(empty, TOPOLOGY))
