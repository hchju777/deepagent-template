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
    """`git grep -n -C1` 흉내 — 부분 문자열, 줄 번호는 1부터, 앞뒤 한 줄이 문맥."""
    out = []
    for repo, files in REPOS.items():
        for path, content in files.items():
            lines = _text(content).splitlines()
            for i, line in enumerate(lines):
                if pattern in line:
                    around = [lines[k] for k in (i - 1, i + 1) if 0 <= k < len(lines)]
                    out.append(Hit(repo, COMMITS[repo], path, i + 1, line, context="\n".join(around)))
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


def test_이름이_객체_안에_있어도_뽑는다():
    """일부 서비스는 `{"collection": "…", "ttl": 3}` / `{"key": "…", "ttl": 30}`로 쓴다(사내 확인).
    키 경로는 맵의 키까지다 — `collection`·`key`는 어디에나 있어 토큰으로 못 쓴다."""
    got = {(n.kind, n.value, n.key_path) for n in flow.names_from_config(merged("dt-api"), FlowSpec().sources)}
    assert ("collection", "alarm_events", "mongodb_collection.alarm") in got
    assert ("rediskey", "alarm:stats:{line}", "redis_key.alarm_stats") in got
    # 문자열 모양(dt-core)과 같은 이름·같은 키 경로로 나온다 — 그래서 하나로 접힌다
    core = {(n.kind, n.value, n.key_path) for n in flow.names_from_config(merged("dt-core"), FlowSpec().sources)}
    assert ("collection", "alarm_events", "mongodb_collection.alarm") in core


def test_접두사_leaf가_있으면_키는_접두사를_붙인_이름이고_코드에는_원래_값으로_찾는다():
    """사내 관례: `redis_key.prefix` + `:` + 값이 실제 키다. 선언(`prefix`)이 있으면 접두사 leaf는 자원이
    아니고, 라벨·브리핑·redis.get에는 완전한 키가, grep·추적기에는 코드에 실제로 있는 값이 나간다."""
    cfg = {"redis_key": {"prefix": "SITE", "conn": "PLC:LINK", "stats": "s:{line}"}}
    src = FlowSource(path="redis_key", kind="rediskey", prefix="prefix")
    got = {(n.value, n.key_path, n.code_value, n.literal) for n in flow.names_from_config(cfg, [src])}
    assert got == {("SITE:PLC:LINK", "redis_key.conn", "PLC:LINK", "PLC:LINK"),
                   ("SITE:s:{line}", "redis_key.stats", "s:{line}", "s:")}
    plain = {n.value for n in flow.names_from_config(cfg, [FlowSource(path="redis_key", kind="rediskey")])}
    assert plain == {"SITE", "PLC:LINK", "s:{line}"}              # 선언이 없으면 전처럼 — 접두사도 leaf다
    none = {n.value for n in flow.names_from_config({"redis_key": {"conn": "PLC:LINK"}}, [src])}
    assert none == {"PLC:LINK"}                                    # 접두사 leaf가 없는 층이면 그대로
    joined = {n.value for n in flow.names_from_config(
        cfg, [FlowSource(path="redis_key", kind="rediskey", prefix="prefix", join="_")])}
    assert joined == {"SITE_PLC:LINK", "SITE_s:{line}"}


def test_한_단어_리터럴은_같은_줄에_읽기쓰기_동사가_있어야_잡는다():
    """컬렉션 이름이 `alarm`처럼 흔한 한 단어면 배지 상태값 `"alarm"`과 구별이 안 된다 — 사내에서
    /summary 끝점이 alarm 컬렉션을 읽는다고 나왔다(실제로는 상태값을 세는 줄). 여러 조각짜리 이름은
    전처럼 어디 있든 잡는다."""
    name = Name("collection", "alarm", "mongodb_collection.alarm", services=("api",))
    lines = {3: 'counts = {"alarm": 0, "caution": 0}', 9: 'rows = await db["alarm"].find({})'}

    def hits_for(pattern):
        return [Hit("dt-api", COMMITS["dt-api"], "api/badge.py", n, t) for n, t in lines.items() if pattern in t]

    g = flow.extract(names=[name], topology=TOPOLOGY, hits_for=hits_for, commits=COMMITS)
    code = {(e["relation"], e["source_location"]) for e in g["links"]
            if e["target"] == "collection_alarm" and e.get("origin") != "config"}
    assert code == {("reads", "L9")}


def test_객체의_필드는_종류별_기본이고_바꿀_수_있다():
    cfg = {"redis_key": {"a": {"name": "k:1", "ttl": 1}, "b": {"key": "k:2"}, "c": "k:3", "d": {"ttl": 9}}}
    default = flow.names_from_config(cfg, [FlowSource(path="redis_key", kind="rediskey")])
    assert {n.value for n in default} == {"k:2", "k:3"}          # 필드 없는 객체(d)는 건너뛴다
    custom = flow.names_from_config(cfg, [FlowSource(path="redis_key", kind="rediskey", field="name")])
    assert {n.value for n in custom} == {"k:1", "k:3"}


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


def test_옆_줄의_동사는_쓰되_INFERRED다():
    """객체 모양 config를 쓰는 코드는 이름 꺼내기와 동사가 다른 줄에 온다
    (`coll = …["collection"]` / `mongo[coll].find(…)`, 사내 확인). 옆 줄의 동사는 다른
    자원의 것일 수도 있으니 같은 줄의 동사(EXTRACTED)와 같은 무게는 아니다."""
    name = Name("collection", "alarm_events", "mongodb_collection.alarm")
    split = Hit("dt-api", "c", "api/alarms.py", 3, '    coll = "alarm_events"',
                context='def recent_alarms(cfg, mongo, since):\n    return list(mongo[coll].find({}))')
    same = Hit("dt-api", "c", "api/alarms.py", 3, '    return mongo["alarm_events"].find({})',
               context='def recent_alarms(cfg, mongo, since):\n')

    def graded(hit):
        g = flow.extract(names=[name], topology=TOPOLOGY, commits=COMMITS,
                         hits_for=lambda p: [hit] if p in hit.text else [])
        return [(e["relation"], e["confidence"]) for e in g["links"] if e["origin"] == "code"]

    assert graded(split) == [("reads", "INFERRED")]
    assert graded(same) == [("reads", "EXTRACTED")]


def test_kafka가_없는_서비스도_정상이다():
    """consumer가 모든 서비스에 있는 것은 아니다(사내 확인). 없는 출처는 이름 0개일 뿐,
    문제도 예외도 아니다. 측정판의 dt-api가 우연히 이 경우라 여기 명시해 둔다 —
    측정판을 바꾸면 그 우연은 사라진다."""
    sources = FlowSpec().sources
    no_kafka = {"infra": {"mongodb": {"database": "data"}},
                "mongodb_collection": {"alarm": "alarm_events"}}
    producer_only = {"infra": {"kafka": {"producer": {"topic": {"topic1": "mx.alarm.main"}}}},
                     "redis_key": {"hb": "hb:{service}"}}
    assert {(n.kind, n.relation) for n in flow.names_from_config(no_kafka, sources)} == {
        ("collection", None)}
    assert {(n.kind, n.relation) for n in flow.names_from_config(producer_only, sources)} == {
        ("topic", "produces"), ("rediskey", None)}

    # 그래프도 조용히 선다 — 토픽·그룹 노드가 없고, 권고가 그 서비스를 탓하지 않는다.
    topology = Topology(services={"api": Service(repo="dt-api", role="읽는다")})
    hit = Hit("dt-api", "c", "api/alarms.py", 3, 'mongo["alarm_events"].find({})')
    g = flow.extract(names=flow.names_from_config(no_kafka, sources), topology=topology,
                     hits_for=lambda p: [hit] if p in hit.text else [], commits={"dt-api": "c"})
    assert {n["type"] for n in g["nodes"]} == {"service", "repo", "collection"}
    assert [(e["relation"], e["target"]) for e in g["links"] if e["origin"] == "code"] == [
        ("reads", "collection_alarm_events")]
    assert not [a for a in flow.advise(g, topology) if a.startswith("api:")]


def test_동사가_없는_줄은_mentions로_남긴다():
    """방향은 몰라도 "이 서비스가 이 이름을 안다"는 사실은 남긴다."""
    hits = lambda p: [Hit("dt-api", "c", "api/alarms.py", 3, 'name = cfg["mongodb_collection"]["alarm"]')]
    g = flow.extract(names=[Name("collection", "alarm_events", "mongodb_collection.alarm")],
                     topology=TOPOLOGY, hits_for=hits, commits=COMMITS)
    assert ("api", "mentions", "alarm_events") in _edges(g)
    assert all(e["confidence"] == "AMBIGUOUS" for e in g["links"] if e["origin"] == "code")


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
    assert s["unreferenced"] == 1, "line_state는 config에만 있다 — 권고가 아니라 요약의 숫자다"
    advice = "\n".join(flow.advise(g, TOPOLOGY))
    # 측정판은 processor/·sink/ 디렉터리로 갈리므로 공유 코드 줄이 없다. "path를 채워라"와
    # "안 쓰는 이름"은 사내에서 둘 다 틀린 권고였다 — 다시 나오면 안 된다.
    assert "path" not in advice and "안 만진다" not in advice and "안 쓰는 이름" not in advice


def test_config_엣지는_서비스마다_그_서비스의_합친_config에서_만든다():
    """같은 레포의 sink·sink-alarm이 환경변수로 역할만 다르면 합친 config도 다를 수 있다(사내).
    grep으로 config 파일을 찾아 레포에 붙이는 대신, 이름을 가진 서비스에서 바로 만든다."""
    topology = Topology(services={"sink": Service(repo="dt-sink", role="저장"),
                                  "sink-alarm": Service(repo="dt-sink", role="알람 저장")})
    names = [Name("topic", "mx.alarm.main", "infra.kafka.consumer.topic.topic1", "consumes",
                  services=("sink",)),
             Name("topic", "mx.alarm.only", "infra.kafka.consumer.topic.topic2", "consumes",
                  services=("sink-alarm",))]
    cfg_line = Hit("dt-sink", "c", "config/gbm/mx.json", 7, '"topic1": "mx.alarm.main"')
    g = flow.extract(names=names, topology=topology, commits={"dt-sink": "c"},
                     hits_for=lambda p: [cfg_line] if p == "mx.alarm.main" else [])
    edges = _edges(g)
    assert ("sink", "consumes", "mx.alarm.main") in edges
    assert ("sink-alarm", "consumes", "mx.alarm.main") not in edges
    assert ("sink-alarm", "consumes", "mx.alarm.only") in edges
    assert ("sink", "consumes", "mx.alarm.only") not in edges
    by = {e["target"]: e for e in g["links"] if e["relation"] == "consumes"}
    assert by["topic_mx_alarm_main"]["source_location"] == "L7"
    assert by["topic_mx_alarm_only"]["source_file"] == "config(dt-sink)"      # 근거 줄이 없어도 선다
    assert all(e["origin"] == "config" and e["attributed"] == "service" for e in by.values())
    assert not [e for e in g["links"] if e["source"] == "repo_dt_sink"], "레포 노드에 config 엣지가 안 붙는다"


def test_config_엣지의_근거는_그_서비스가_실제로_합친_층이다():
    """레포의 config 파일을 grep해서 첫 파일을 붙이면 알파벳순으로 앞서는 `_dev` 층이 찍힌다
    (사내 첫 실행). 서비스가 실제로 읽은 층의 줄이 있으면 그것이 먼저다."""
    topology = Topology(services={"sink": Service(repo="dt-sink", role="저장")})
    name = Name("topic", "mx.alarm.main", "infra.kafka.consumer.topic.topic1", "consumes",
                services=("sink",),
                evidence=(("sink", "config/factories/gumi/mx.json", 9, '"topic1": "mx.alarm.main"'),))
    decoy = Hit("dt-sink", "c", "config/factories/_dev/_dev.json", 3, '"topic1": "mx.alarm.main"')
    g = flow.extract(names=[name], topology=topology, commits={"dt-sink": "c"},
                     hits_for=lambda p: [decoy] if p == "mx.alarm.main" else [])
    edge = next(e for e in g["links"] if e["relation"] == "consumes")
    assert (edge["source_file"], edge["source_location"]) == ("config/factories/gumi/mx.json", "L9")
    assert flow.describe(g, edge["target"]) == "mx.alarm.main [topic]"
    assert flow.describe(g, edge["source"]) == "sink"


def _lead_graph():
    """사내 모양: config 엣지는 서비스 단위, 코드 엣지는 api의 reads 하나."""
    topology = Topology(services={"processor": Service(repo="dt-core", role="가공"),
                                  "sink": Service(repo="dt-core", role="저장"),
                                  "api": Service(repo="dt-api", role="읽기")})
    names = [Name("topic", "mx.alarm.main", "infra.kafka.producer.topic.topic1", "produces",
                  services=("processor",)),
             Name("topic", "mx.alarm.main", "infra.kafka.consumer.topic.topic2", "consumes",
                  services=("sink",)),
             Name("topic", "mx.alarm.raw", "infra.kafka.consumer.topic.topic1", "consumes",
                  services=("processor",)),
             Name("collection", "alarm_events", "mongodb_collection.alarm", services=("api", "sink"))]
    # 코드 층: api가 alarm_events를 읽고, sink가 쓰고, sink 코드가 mx.alarm.raw를 구독한다.
    # 마지막 것은 config에 없는 관계다 — 코드 층이 새면 sink 줄의 consumes에 raw가 끼어든다.
    hits = {"alarm_events": [Hit("dt-api", "c", "api/q.py", 3, 'mongo["alarm_events"].find({})'),
                             Hit("dt-core", "c", "sink/w.py", 10, 'mongo["alarm_events"].insert_many(b)')],
            "mx.alarm.raw": [Hit("dt-core", "c", "sink/w.py", 4, 'consumer.subscribe("mx.alarm.raw")')]}
    return flow.extract(names=names, topology=topology, commits={},
                        hits_for=lambda p: hits.get(p, []))


def test_씨앗은_본문에_글자_그대로_나온_이름이고_서비스가_먼저다():
    g = _lead_graph()
    seeds = flow.find_seeds(g, ["gumi 라인 sink가 안 받는 듯, mx.alarm.main lag 1830", ""])
    assert seeds == ["service_sink", "topic_mx_alarm_main"]
    assert flow.find_seeds(g, ["api"]) == ["service_api"]          # 세 글자는 된다
    assert flow.find_seeds(g, ["아무 이름도 없다"]) == []


def test_흐름_텍스트는_config_층만_씨앗의_이웃_그리고_닿은_서비스의_토픽():
    """코드 층(api —reads→ alarm_events)은 안 실린다 — 사내에서 소음으로 확인됐고 홉을 밟는 것은
    11b의 일이다. 씨앗 줄 다음에 씨앗 자원에 닿은 서비스의 토픽 줄(2단계)이 온다."""
    g = _lead_graph()
    text = flow.flow_text(g, ["service_sink", "topic_mx_alarm_main"])
    assert text.splitlines() == [
        "sink [service · dt-core 공유 config]: consumes: mx.alarm.main · declares: alarm_events",
        "mx.alarm.main [topic]: produces: processor · consumes: sink",
        "processor [service · dt-core 공유 config]: produces: mx.alarm.main · consumes: mx.alarm.raw",
    ]
    assert "reads" not in text and "writes" not in text and "api" not in text
    assert "mx.alarm.raw" not in text.splitlines()[0], "sink의 raw 구독은 코드 층이다 — config에 없다"


def test_흐름_텍스트는_예산에서_끊고_끊었다고_적는다():
    g = _lead_graph()
    text = flow.flow_text(g, ["service_sink", "topic_mx_alarm_main"], budget=100)
    lines = text.splitlines()
    assert len(lines) == 2                                   # 첫 줄은 예산이 작아도 실린다
    assert lines[0].startswith("sink [service") and lines[1].startswith("… (+2줄")


def test_같은_config를_쓰는_서비스_전부면_레포로_접는다():
    """사내: processor 5개가 한 config를 쓴다. 나열하면 "sink도 생산한다"로 읽힌다 — config는
    레포까지만 안다. 레포의 서비스 전부가 같은 관계면 `레포{a,b}`, 일부면 그대로 이름."""
    topology = Topology(services={"processor": Service(repo="dt-core", role="가공"),
                                  "sink": Service(repo="dt-core", role="저장"),
                                  "api": Service(repo="dt-api", role="읽기")})
    names = [Name("topic", "mx.alarm.main", "infra.kafka.producer.topic.topic1", "produces",
                  services=("processor", "sink")),
             Name("topic", "mx.alarm.main", "infra.kafka.consumer.topic.topic2", "consumes",
                  services=("sink",)),
             Name("collection", "alarm_events", "mongodb_collection.alarm",
                  services=("api", "processor", "sink"))]
    g = flow.extract(names=names, topology=topology, commits={}, hits_for=lambda p: [])
    assert flow.flow_text(g, ["topic_mx_alarm_main", "collection_alarm_events"]).splitlines()[:2] == [
        "mx.alarm.main [topic]: produces: dt-core{processor,sink} · consumes: sink",
        "alarm_events [collection]: declares: api, dt-core{processor,sink}",
    ]


def test_씨앗이_없으면_토픽_골격을_준다():
    text = flow.flow_text(_lead_graph(), [])
    assert text.splitlines() == ["mx.alarm.main [topic]: produces: processor · consumes: sink",
                                 "mx.alarm.raw [topic]: consumes: processor"]


def test_code_flow_본문은_config_먼저_코드는_확신_순_runs는_빼고():
    g = _lead_graph()
    lines, left = flow.neighbor_lines(g, flow.neighbors(g, "alarm_events"))
    assert left == 0 and "runs" not in "\n".join(lines)
    assert lines[0].startswith("api —declares→ alarm_events [collection]  [config·EXTRACTED]")
    assert lines[1].startswith("sink —declares→ alarm_events [collection]  [config·EXTRACTED]")
    assert lines[2].startswith("api —reads→ alarm_events [collection]  [code·EXTRACTED] api/q.py:L3")
    assert lines[3].startswith("sink —writes→ alarm_events [collection]  [code·INFERRED] sink/w.py:L10")   # 디렉터리로 가른 귀속은 INFERRED
    short, left = flow.neighbor_lines(g, flow.neighbors(g, "alarm_events"), limit=2)
    assert len(short) == 2 and left == 2


def test_같은_코드를_띄우는_서비스는_레포를_거쳐_경로가_난다():
    """코드 엣지가 레포에 붙으면 서비스에서 출발하는 경로가 없다. `runs`가 다리다 —
    단, 자원 경로가 있으면 그쪽이 먼저다(같은 레포의 서비스 둘은 `runs` 두 홉으로 늘 이어진다)."""
    topology = Topology(services={"sink": Service(repo="dt-sink", role="저장"),
                                  "sink-alarm": Service(repo="dt-sink", role="알람 저장"),
                                  "api": Service(repo="dt-api", role="읽기")})
    name = Name("collection", "alarm_events", "mongodb_collection.alarm")
    hits = {"alarm_events": [Hit("dt-sink", "c", "core/store.py", 3, 'mongo["alarm_events"].insert_many(b)'),
                             Hit("dt-api", "c", "api/q.py", 3, 'mongo["alarm_events"].find({})')]}
    g = flow.extract(names=[name], topology=topology, hits_for=lambda p: hits.get(p, []),
                     commits={"dt-sink": "c", "dt-api": "c"})
    assert ("dt-sink", "writes", "alarm_events") in _edges(g)           # core/는 어느 서비스도 아니다
    path = flow.shortest_path(g, "sink-alarm", "api")
    assert flow.render_path(g, path) == "sink-alarm —runs→ dt-sink —writes→ alarm_events ←reads— api"
    assert flow.shortest_path(g, "sink", "sink-alarm") is not None        # 같은 코드 — 다리로만 이어진다
    advice = "\n".join(flow.advise(g, topology))
    assert "dt-sink: 서비스 2개(sink, sink-alarm)가 코드를 공유한다" in advice
    assert "sink-alarm:" not in advice and "path" not in advice


def test_문서_테스트_주석_줄은_코드_엣지가_아니다():
    """사내 첫 실행에서 코드 엣지의 60%가 md·테스트·주석에서 나왔다. 이름이 적힌 문서는
    "이 서비스가 이 자원을 쓴다"의 근거가 아니다."""
    topology = Topology(services={"api": Service(repo="dt-api", role="읽기")})
    name = Name("collection", "alarm_events", "mongodb_collection.alarm")
    noise = [Hit("dt-api", "c", "README.md", 3, "alarm_events 컬렉션을 읽는다"),
             Hit("dt-api", "c", "tests/test_q.py", 3, 'mongo["alarm_events"].find({})'),
             Hit("dt-api", "c", "api/q_test.py", 3, 'mongo["alarm_events"].find({})'),
             Hit("dt-api", "c", "api/q.py", 1, '# alarm_events에서 읽는다'),
             Hit("dt-api", "c", "api/q.py", 2, '"""alarm_events를 읽는 모듈."""')]
    real = Hit("dt-api", "c", "api/q.py", 9, 'mongo["alarm_events"].find({})')
    g = flow.extract(names=[name], topology=topology, commits={"dt-api": "c"},
                     hits_for=lambda p: noise + [real] if p == "alarm_events" else [])
    code = [e for e in g["links"] if e["origin"] == "code"]
    assert [(e["source_file"], e["source_location"]) for e in code] == [("api/q.py", "L9")]


def test_따옴표로_통째_적힌_config_키는_조상_없이도_잡는다():
    """사내 코드는 키를 Enum 값으로 든다: `PROD_BEFORE_WORKER_ALL = "prodcheck_before_cur_worker_all"`.
    `redis_key`는 다른 파일의 공통 접근 함수에 있다. 그 Enum 줄이 코드에서 이 키를 아는
    유일한 자리다 — 리드가 홉을 밟기 시작할 곳. 짧은 한 단어(`alarm`)는 여전히 조상이 필요하다."""
    topology = Topology(services={"batch": Service(repo="dt-batch", role="배치")})
    long_key = Name("rediskey", "BATCH:PRODCHECK:BEFORE:WORKER:ALL",
                    "redis_key.prodcheck_before_cur_worker_all")
    short_key = Name("collection", "alarm_events", "mongodb_collection.alarm")
    lines = {"prodcheck_before_cur_worker_all": [
                 Hit("dt-batch", "c", "common/storage_keys.py", 12,
                     '    PROD_BEFORE_WORKER_ALL = "prodcheck_before_cur_worker_all"')],
             "alarm": [Hit("dt-batch", "c", "common/names.py", 4, '    ALARM = "alarm"')]}
    g = flow.extract(names=[long_key, short_key], topology=topology, commits={"dt-batch": "c"},
                     hits_for=lambda p: lines.get(p, []))
    got = [(e["relation"], e["confidence"], e["source_file"]) for e in g["links"] if e["origin"] == "code"]
    assert got == [("mentions", "AMBIGUOUS", "common/storage_keys.py")]


def test_이름이_하나도_없으면_그렇게_말한다():
    empty = flow.extract(names=[], topology=TOPOLOGY, hits_for=lambda p: [], commits=COMMITS)
    assert any("flow.sources" in line for line in flow.advise(empty, TOPOLOGY))


def test_known_names는_그래프의_이름_전부이고_없으면_빈다():
    """엔진의 "찾지 않고 이름을 댔다" 검사가 이 목록을 증거와 합쳐 본다 — 브리핑이 준
    이름은 찾은 것이다. 그래프가 없으면 빈 문자열이라 검사가 예전과 같다."""
    g = {"nodes": [{"id": "service_sink", "label": "sink", "type": "service"},
                   {"id": "topic_a", "label": "a.b", "type": "topic"}], "links": []}
    assert flow.known_names(g).splitlines() == ["a.b", "sink"]
    assert flow.known_names(None) == ""


# ── 블록 조정 (11c 커밋 4) — 사내 블록은 토픽 15개가 한 줄에 늘어서고 같은 config의 서비스가
# 2단계에서 다섯 줄로 반복됐다(+4줄 절단). 공유 config는 produces·consumes를 못 가른다.

def _shared_graph(*, topics=2, code=False):
    nodes = [{"id": "service_processor", "label": "processor", "type": "service", "repo": "dt-core"},
             {"id": "service_sink", "label": "sink", "type": "service", "repo": "dt-core"},
             {"id": "repo_dt-core", "label": "dt-core", "type": "repo"}]
    links = []
    for i in range(topics):
        nodes.append({"id": f"topic_{i}", "label": f"t.{i:02d}", "type": "topic"})
        for svc in ("service_processor", "service_sink"):
            for rel in ("produces", "consumes"):
                links.append({"source": svc, "target": f"topic_{i}", "relation": rel,
                              "origin": "config", "confidence": "EXTRACTED"})
    if code:
        links += [{"source": "service_processor", "target": "topic_0", "relation": "produces",
                   "origin": "code", "confidence": "INFERRED", "source_file": "processor/h.py", "source_location": "L8"},
                  {"source": "service_sink", "target": "topic_0", "relation": "consumes",
                   "origin": "code", "confidence": "INFERRED", "source_file": "sink/w.py", "source_location": "L7"}]
    return {"nodes": nodes, "links": links}


def test_관계당_여덟_개까지만_적고_나머지는_센다():
    text = flow.flow_text(_shared_graph(topics=11), ["service_sink"], budget=10_000)
    first = text.splitlines()[0]
    assert "t.07" in first and "t.08" not in first and "외 3개" in first


def test_2단계_서비스도_같은_config면_접는다():
    """씨앗 토픽에 닿은 processor와 sink의 토픽 줄이 같으면 한 줄 `dt-core{processor,sink}`다."""
    lines = flow.flow_text(_shared_graph(), ["topic_0"], budget=10_000).splitlines()
    assert lines[0].startswith("t.00 [topic]:")
    assert len(lines) == 2 and lines[1].startswith("dt-core{processor,sink} [service · dt-core 공유 config]:")
    # 보여 줄 관계가 없는 2단계 서비스는 줄을 안 낸다("(config 엣지 없음)"이 없는 것처럼 읽혔다).
    g = _shared_graph()
    g["nodes"].append({"id": "collection_c", "label": "c_only", "type": "collection"})
    g["links"] = [e for e in g["links"] if not (e["source"] == "service_sink" and e["target"].startswith("topic_"))]
    g["links"].append({"source": "service_sink", "target": "collection_c", "relation": "declares",
                       "origin": "config", "confidence": "EXTRACTED"})
    assert flow.flow_text(g, ["collection_c"], budget=10_000).splitlines() == ["c_only [collection]: declares: sink"]


def test_config가_못_가른_방향은_코드_층_한_줄로_보탠다():
    """3b 측정에서 본 것 — produces·consumes가 둘 다 `dt-core{processor,sink}`로 접히면 리드는 방향을
    모른다. 코드 층이 서비스까지 짚었을 때만(INFERRED 이상) 그 한 줄을 보탠다."""
    with_code = flow.flow_text(_shared_graph(code=True), ["topic_0"], budget=10_000).splitlines()[0]
    assert "코드로는 produces: processor · consumes: sink" in with_code
    without = flow.flow_text(_shared_graph(), ["topic_0"], budget=10_000).splitlines()[0]
    assert "코드로는" not in without


def test_씨앗은_토큰_단위로_맞추고_자원은_셋까지다():
    """`alarm`이 `alarm_events`나 응답 필드 `alarm`에 글자로 걸려 씨앗이 됐다(사내). 식별자 안의
    부분 문자열은 안 친다. 자원 씨앗은 셋까지 — 서비스는 상한이 없다."""
    g = _shared_graph(topics=6)
    g["nodes"].append({"id": "collection_alarm", "label": "alarm", "type": "collection"})
    assert flow.find_seeds(g, ["alarm_events가 비었다"]) == []
    assert flow.find_seeds(g, ["alarm 컬렉션이 비었다"]) == ["collection_alarm"]
    many = flow.find_seeds(g, ["t.00 t.01 t.02 t.03 t.04 sink processor"])
    assert many[:2] == ["service_processor", "service_sink"] and len(many) == 5


# ── 끝점 노드 (11c 커밋 5) — 사람이 적지 않는다. 우리 rest.entries의 path와 api 레포의 라우트 선언에서.

def _route_hits():
    return [Hit("dt-api", "c", "api/routers/line.py", 1, 'router = APIRouter(prefix="/line", tags=["line"])'),
            Hit("dt-api", "c", "api/routers/line.py", 5, '@router.get("/status", response_model=list[LineStatus])'),
            Hit("dt-api", "c", "api/routers/line.py", 9, '@router.post("/status/{line_id}/ack")'),
            Hit("dt-api", "c", "api/routers/orphan.py", 1, 'router = APIRouter()'),
            Hit("dt-api", "c", "api/routers/orphan.py", 3, '@router.post("/x")'),
            Hit("dt-api", "c", "api/main.py", 7, 'app.include_router(line.router, prefix="/api/v1")'),
            Hit("dt-api", "c", "api/main.py", 9, '@app.get("/health")'),
            Hit("dt-api", "c", "tests/test_line.py", 2, '@router.get("/not-real")')]


def test_라우트_줄에서_끝점을_조립한다():
    """같은 파일의 `APIRouter(prefix)` + 데코레이터 꼬리, 그 위에 앱 조립부의 `include_router(mod.router,
    prefix)`가 모듈 이름으로 이어지면 한 겹 더(EXTRACTED). include_router가 레포에 있는데 이 파일로 못
    이었으면 INFERRED — prefix 한 겹이 빠졌을 수 있다. 테스트 파일은 뺀다."""
    routes = {(r.method, r.path): r for r in flow.routes_from_hits(_route_hits())}
    assert set(routes) == {("GET", "/api/v1/line/status"), ("POST", "/api/v1/line/status/{line_id}/ack"),
                           ("POST", "/x"), ("GET", "/health")}
    assert routes[("GET", "/api/v1/line/status")].confidence == "EXTRACTED"
    assert routes[("GET", "/api/v1/line/status")].file == "api/routers/line.py"
    assert routes[("GET", "/api/v1/line/status")].line == 5
    assert routes[("POST", "/x")].confidence == "INFERRED"
    assert routes[("GET", "/health")].confidence == "EXTRACTED"


def test_등재_path와_코드_끝점을_잇는다():
    """등재 항목은 코드에 없어도 노드다(등재가 곧 존재의 증거) — 단 serves 엣지가 없다. 코드 라우트는
    레포에 서비스가 하나면 그 서비스가, 여럿이면 레포가 serves한다. prefix를 못 이은 라우트(INFERRED)가
    등재 path의 꼬리와 같으면 그 항목에 붙는다."""
    g = _lead_graph()
    routes = [flow.Route("dt-api", "POST", "/summary/badge", "api/r.py", 5, "EXTRACTED", '@router.post("/badge")'),
              flow.Route("dt-api", "GET", "/lines", "api/l.py", 2, "INFERRED", '@router.get("/lines")'),
              flow.Route("dt-core", "GET", "/internal/ping", "shared/ping.py", 1, "EXTRACTED", '@app.get("/internal/ping")')]
    entries = {"summary_badge": ("POST", "/summary/badge"), "lines": ("GET", "/api/v1/lines"),
               "oee": ("POST", "/api/v1/oee/summary")}
    g = flow.add_endpoints(g, routes=routes, entries=entries)
    ep = {n["label"]: n for n in g["nodes"] if n["type"] == "endpoint"}
    assert set(ep) == {"/summary/badge", "/api/v1/lines", "/api/v1/oee/summary", "/internal/ping"}
    assert ep["/summary/badge"]["entry"] == "summary_badge" and ep["/summary/badge"]["method"] == "POST"
    serves = {(e["source"], e["target"], e["confidence"]) for e in g["links"] if e["relation"] == "serves"}
    assert ("service_api", ep["/summary/badge"]["id"], "EXTRACTED") in serves
    assert ("service_api", ep["/api/v1/lines"]["id"], "INFERRED") in serves      # 꼬리로 이었다
    assert ("repo_dt_core", ep["/internal/ping"]["id"], "EXTRACTED") in serves    # 공유 레포는 레포가
    assert not any(t == ep["/api/v1/oee/summary"]["id"] for _, t, _ in serves)    # 서빙 미상
    s = flow.summary(g)
    assert (s["endpoints"], s["endpoints_registered"], s["endpoints_unserved"]) == (4, 3, 1)
    assert any("끝점 4개 중 등재 3개" in line and "못 찾은 1개" in line for line in flow.advise(g, _lead_graph_topology()))
    assert flow.serving_services(g, "/summary/badge") == ["api"]
    assert flow.serving_services(g, "/internal/ping") == []                       # 레포는 서비스가 아니다


def _lead_graph_topology():
    return Topology(services={"processor": Service(repo="dt-core", role="가공"),
                              "sink": Service(repo="dt-core", role="저장"),
                              "api": Service(repo="dt-api", role="읽기")})


def test_끝점_씨앗은_serves_줄이_맨_앞이고_서비스_다음_순위다():
    g = flow.add_endpoints(_lead_graph(), routes=[
        flow.Route("dt-api", "POST", "/summary/badge", "api/r.py", 5, "EXTRACTED", "")],
        entries={"summary_badge": ("POST", "/summary/badge")})
    seeds = flow.find_seeds(g, ["판정이 본 읽기 rest.query entry='summary_badge' (POST /summary/badge) · mx.alarm.main"])
    assert seeds[0].startswith("endpoint_") and seeds[1] == "topic_mx_alarm_main"
    text = flow.flow_text(g, seeds[:1], budget=10_000)
    # 끝점 씨앗에 닿은 서비스는 토픽만이 아니라 전부 — 다음 칸이 "그 코드가 읽는 데이터"다.
    assert text.splitlines() == ["/summary/badge [endpoint]: serves: api",
                                 "api [service · dt-api]: serves: /summary/badge · declares: alarm_events"]
    # api 서비스 줄에도 serves가 보인다 — config 엣지가 아니지만 배선이다.
    assert "serves: /summary/badge" in flow.flow_text(g, ["service_api"], budget=10_000)


# ── 끝점 → 자원 (11b 커밋 2) — 추적기의 결과를 그래프에 싣는다.

def _traced_graph():
    from src.knowledge import trace as tr
    g = flow.add_endpoints(_lead_graph(), routes=[
        flow.Route("dt-api", "POST", "/summary/badge", "api/r.py", 5, "EXTRACTED", "")],
        entries={"summary_badge": ("POST", "/summary/badge")})
    ep = next(n["id"] for n in g["nodes"] if n["type"] == "endpoint")
    # 걸음 넷 — 로거(`Log.info`)는 읽기로 이어지지 않는 가지다. 읽기 셋은 표시 셋(확실·config키·추정)이다.
    result = tr.Trace("/summary/badge", "dt-api", "ok",
                      chain=(tr.Step("api/r.py", 6, "badge"),
                             tr.Step("api/q.py", 3, "AlarmRepo.recent", parent=0),
                             tr.Step("api/log.py", 2, "Log.info", parent=0),
                             tr.Step("api/q.py", 20, "AlarmRepo._q", parent=1)),
                      reads=(tr.Read("collection", "alarm_events", "확실", "api/q.py", 3, step=1),
                             tr.Read("topic", "mx.alarm.main", "추정", "api/q.py", 21, "key", step=3),
                             tr.Read("topic", "mx.alarm.raw", "추정", "api/q.py", 22, step=3)),
                      gaps=(tr.Gap("api/q.py", 12, "getattr로 고른 대상은 못 따라간다"),
                            tr.Gap("api/q.py", 30, "get: 받는 쪽 미상, 후보 4개 — 안 따라간다"),
                            tr.Gap("api/q.py", 31, "깊이 상한 6에서 멈춤: AlarmRepo._deep → x"),
                            tr.Gap("api/q.py", 32, "put: 받는 쪽 미상, 후보 2개 — 안 따라간다")))
    return flow.add_trace(g, ep, result), ep


def test_add_trace는_읽기_엣지를_등급과_함께_싣고_노드에_사슬을_남긴다():
    g, ep = _traced_graph()
    edges = {(e["target"], e["confidence"], e["source_location"]) for e in g["links"]
             if e["source"] == ep and e.get("origin") == "trace"}
    assert edges == {("collection_alarm_events", "EXTRACTED", "L3"), ("topic_mx_alarm_main", "INFERRED", "L21"),
                     ("topic_mx_alarm_raw", "INFERRED", "L22")}
    node = next(n for n in g["nodes"] if n["id"] == ep)
    assert node["traced"] == "ok" and node["chain"][:2] == ["api/r.py:L6 badge", "api/q.py:L3 AlarmRepo.recent"]
    assert node["gaps"][0] == "api/q.py:L12 getattr로 고른 대상은 못 따라간다"
    s = flow.summary(g)
    assert (s["endpoints_traced"], s["endpoints_blocked"]) == (1, 0)
    assert any("자원까지 이어진 1개" in line for line in flow.advise(g, _lead_graph_topology()))


def test_끝점_줄에_reads가_붙고_2단계로_그_자원과_쓰는_서비스가_온다():
    """사다리의 셋째 칸 — "그 데이터를 쓰는 서비스". 추정 읽기는 따로 표시한다."""
    g, ep = _traced_graph()
    lines = flow.flow_text(g, [ep], budget=10_000).splitlines()
    assert lines[0] == ("/summary/badge [endpoint]: serves: api · reads: alarm_events [collection]"
                        " · reads(config키): mx.alarm.main [topic] · reads(추정): mx.alarm.raw [topic]")
    assert lines[1] == "api [service · dt-api]: serves: /summary/badge · declares: alarm_events"
    assert "alarm_events [collection]: declares: api, sink · 코드로는 writes: sink · reads: api" in lines
    assert any(l.startswith("mx.alarm.main [topic]:") for l in lines)


def test_막힌_끝점을_센다():
    from src.knowledge import trace as tr
    g = flow.add_endpoints(_lead_graph(), routes=[
        flow.Route("dt-api", "GET", "/dyn", "api/r.py", 9, "EXTRACTED", "")], entries={})
    ep = next(n["id"] for n in g["nodes"] if n["type"] == "endpoint")
    g = flow.add_trace(g, ep, tr.Trace("/dyn", "dt-api", "ok", chain=(tr.Step("api/r.py", 10, "dyn"),),
                                       gaps=(tr.Gap("api/r.py", 11, "getattr"),)))
    s = flow.summary(g)
    assert (s["endpoints_traced"], s["endpoints_blocked"]) == (0, 1)


# ── 11b 커밋 3a — 리드에게 보여 줄 사슬. 사내 사슬은 28~30걸음이고 대부분이 저장소 부모의 헬퍼와 로거다.

def test_add_trace는_걸음의_부모와_읽기의_걸음을_싣는다():
    g, ep = _traced_graph()
    node = next(n for n in g["nodes"] if n["id"] == ep)
    assert node["chain_parent"] == [None, 0, 0, 1]
    steps = {e["target"]: e["step"] for e in g["links"] if e["source"] == ep and e.get("origin") == "trace"}
    assert steps == {"collection_alarm_events": 1, "topic_mx_alarm_main": 3, "topic_mx_alarm_raw": 3}


def test_trace_lines는_읽기로_이어진_걸음만_남기고_표시_셋으로_적는다():
    """로거 걸음(`Log.info`)이 빠지고, 읽기는 확실·config키·추정으로 갈려 걸음 옆에 붙는다. gap은 셋까지."""
    g, ep = _traced_graph()
    assert flow.trace_lines(g, ep) == [
        "api/r.py:L6 badge",
        "  → api/q.py:L3 AlarmRepo.recent — reads: alarm_events [collection] 확실",
        "    → api/q.py:L20 AlarmRepo._q — reads: mx.alarm.main [topic] config키 · mx.alarm.raw [topic] 추정",
        "못 따라감 4: api/q.py:L12 getattr로 고른 대상은 못 따라간다 · api/q.py:L30 get: 받는 쪽 미상, 후보 4개 — 안 따라간다"
        " · api/q.py:L31 깊이 상한 6에서 멈춤: AlarmRepo._deep → x 외 1개",
        "걸음 4 중 읽기로 이어진 3만 적었다 — 나머지는 code.read로 본다"]


def test_trace_lines는_읽기가_없으면_앞_걸음_넷과_gap을_적고_추적_안_된_끝점이면_None이다():
    from src.knowledge import trace as tr
    g = flow.add_endpoints(_lead_graph(), routes=[
        flow.Route("dt-api", "POST", "/summary/badge", "api/r.py", 5, "EXTRACTED", "")],
        entries={"summary_badge": ("POST", "/summary/badge")})
    ep = next(n["id"] for n in g["nodes"] if n["type"] == "endpoint")
    assert flow.trace_lines(g, ep) is None                      # 아직 추적이 안 됐다
    chain = tuple(tr.Step("api/r.py", 6 + i, f"f{i}", parent=None if i == 0 else i - 1) for i in range(6))
    g2 = flow.add_trace(g, ep, tr.Trace("/summary/badge", "dt-api", "ok", chain=chain, reads=(),
                                        gaps=(tr.Gap("api/r.py", 40, "getattr로 고른 대상은 못 따라간다"),)))
    lines = flow.trace_lines(g2, ep)
    assert lines[:2] == ["api/r.py:L6 f0", "  → api/r.py:L7 f1"] and len(lines) == 6
    assert lines[-2].startswith("못 따라감 1:") and lines[-1] == "걸음 6 중 4만 적었다 — 읽기로 이어진 걸음이 없다"
