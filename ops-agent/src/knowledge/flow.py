"""데이터 흐름 그래프 — **서비스 ↔ 자원**을 배포된 코드에서 LLM 없이 뽑는다(11c).

리드가 못 한 것은 "어느 서비스, 어느 흐름을 볼까"였다. processor → 토픽 → sink →
컬렉션을 몰라 api만 팠다. 이 관계는 AST에 없다. 토픽과 컬렉션은 config의 문자열이고
생산자·소비자는 각자 그 문자열을 읽을 뿐이라, graphify의 코드 패스는 우리 측정판에서
서비스 사이 엣지를 **0개** 냈다(`STEPS/step-11c-flow.md`). 그래서 이 층만 우리가 만든다.

방법은 셋이다. ① 합친 config에서 이름을 뽑는다(`Topology.flow.name_paths`). ② 그
이름이 쓰인 줄을 `git grep`으로 찾는다 — 리터럴은 EXTRACTED, config 키 토큰
(`topics["alarm_main"]`)은 INFERRED. ③ 같은 줄의 동사로 방향을 정한다. 애매하면
AMBIGUOUS로 **남긴다** — 버리지 않는다.

출력은 graphify의 `graph.json` 스키마다(노드 `id/label/source_file/source_location`,
엣지 `source/target/relation/confidence`). 그래야 `graphify merge-graphs`로 심볼
그래프와 합치고 같은 `explain`·`path`로 읽을 수 있다(11b가 그걸 쓴다).

**엣지마다 `file:line@commit`이 있다.** 12a의 verify가 인용하는 근거는 이것이다.
그래프 노드 자체는 증거가 아니다 — "어디"는 그래프가, "무엇"은 프로브가 준다.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Iterable

from src.knowledge.schema import FLOW_FIELDS, Service, Topology

# 같은 줄의 동사로 방향을 정한다. 표는 여기 하나뿐이고 테스트가 지킨다.
READ_VERBS = ("subscribe", "consume", "poll", "find", "aggregate", "count", "distinct",
              "get", "mget", "hget", "hgetall", "scan", "lrange", "xread", "query",
              "fetch", "select", "read")
WRITE_VERBS = ("send", "produce", "publish", "emit", "insert", "update", "upsert",
               "replace", "delete", "remove", "set", "hset", "lpush", "rpush", "xadd",
               "expire", "incr", "save", "write")
# 종류마다 읽기/쓰기의 이름이 다르다 — 토픽은 소비/생산이지 읽기/쓰기가 아니다.
RELATION = {"topic": {"reads": "consumes", "writes": "produces"},
            "group": {"reads": "consumes_as", "writes": "consumes_as"},
            "collection": {"reads": "reads", "writes": "writes"},
            "rediskey": {"reads": "reads", "writes": "writes"}}
TEXT_CHARS = 160
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class Name:
    kind: str
    value: str          # config의 값 그대로 (`alarm:stats:{line}`)
    key_path: str       # `infra.kafka.consumer.topic.topic1`
    relation: str | None = None     # 출처가 방향을 말하면(`consumes`·`produces`…)
    # 이 이름을 합친 config에 가진 서비스들. 비어 있으면 모른다(테스트의 파일 뭉치 경로).
    # 같은 레포의 서비스 여럿이 환경변수로 역할만 다르면(사내: processor 5개·sink 2개) 합친
    # config도 다를 수 있어, config 엣지는 grep이 아니라 여기서 서비스 단위로 만든다.
    services: tuple[str, ...] = ()
    # `(서비스, 층 파일, 줄, 본문)` — 그 서비스가 실제로 합친 층에서 값이 적힌 자리. config
    # 엣지의 근거 줄은 이것이 먼저고, 없을 때만 레포의 config 파일 grep으로 대신한다.
    evidence: tuple[tuple[str, str, int, str], ...] = ()
    # 접두사가 붙는 키(사내: `redis_key.prefix` + `:` + 값)에서 **코드에 실제로 있는 값**. 라벨·브리핑·
    # `redis.get`은 `value`(완전한 키)를 쓰고, grep과 추적기는 이것을 쓴다. 없으면 `value`가 곧 코드의 값이다.
    code_value: str | None = None

    @property
    def literal(self) -> str:
        """grep에 쓸 리터럴 — 코드에 있는 값, 템플릿이면 `{` 앞까지."""
        return (self.code_value or self.value).split("{", 1)[0]

    @property
    def key_token(self) -> str:
        return self.key_path.rsplit(".", 1)[-1]

    @property
    def required_tokens(self) -> tuple[str, ...]:
        """키 토큰 매치에 **같은 줄에 같이 있어야 하는** 조상 키 — 마지막 둘.

        `groups["processor"]`는 맞고 `format(service="processor")`는 아니다. 그리고 사내
        config는 소비·생산 토픽이 둘 다 `topic1`이라 `["consumer"]["topic"]["topic1"]`과
        `["producer"]["topic"]["topic1"]`을 부모 하나(`topic`)로는 못 가른다 — 조부모까지 본다.
        전부를 요구하지 않는 이유: `kafka = cfg["infra"]["kafka"]`처럼 앞에서 묶어 두면
        먼 조상은 그 줄에 없다.
        """
        parts = self.key_path.split(".")
        return tuple(parts[-3:-1])

    @property
    def patterns(self) -> tuple[str, ...]:
        """리터럴과 키 토큰. 둘이 같거나 리터럴이 비면 하나만."""
        seen = []
        for p in (self.literal, self.key_token):
            if p and p not in seen:
                seen.append(p)
        return tuple(seen)


@dataclass(frozen=True)
class Hit:
    repo: str
    commit: str
    file: str
    line: int
    text: str
    # 앞뒤 한두 줄. 동사가 다음 줄에 있는 문장(`coll = …` / `coll.find(…)`)을 위해서다.
    # 비어 있으면 그 줄만 본다.
    context: str = ""


def names_from_config(merged: dict, sources) -> list[Name]:
    """합친 config에서 자원 이름을 뽑는다. 없는 경로는 조용히 건너뛴다 — 층마다
    있는 키가 다르고, 하나도 못 뽑으면 호출부가 "이름 0개"로 말한다."""
    found: list[Name] = []
    for src in sources:
        node = merged
        for key in src.path.split("."):
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        field = src.field or FLOW_FIELDS.get(src.kind, "")
        if isinstance(node, dict):
            pre = node.get(src.prefix) if src.prefix else None
            pre = pre if isinstance(pre, str) and pre else None
            for key, value in sorted(node.items()):
                if src.prefix and key == src.prefix:
                    continue                # 접두사는 키 조각이지 자원이 아니다
                # 값이 객체면(`{"collection": "…", "ttl": 3}`) 이름은 그 안의 필드다. 키 경로는
                # **맵의 키까지**(`mongodb_collection.alarm`)로 둔다 — 코드는 그 키로 꺼내고,
                # `collection`·`key` 같은 필드 이름은 어디에나 있어 토큰으로 못 쓴다.
                if isinstance(value, dict):
                    value = value.get(field)
                if isinstance(value, str) and value:
                    if pre:
                        found.append(Name(src.kind, f"{pre}{src.join}{value}", f"{src.path}.{key}", src.relation,
                                          code_value=value))
                    else:
                        found.append(Name(src.kind, value, f"{src.path}.{key}", src.relation))
        elif isinstance(node, str) and node:
            found.append(Name(src.kind, node, src.path, src.relation))
    return found


_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _parts(text: str) -> set[str]:
    """식별자를 `_`와 camelCase 경계로 쪼갠 조각들. `insert_many`·`sendMessage`에서
    `insert`·`send`를 보되, `reset`은 `set`이 아니다 — 조각 단위로만 맞춘다."""
    parts: set[str] = set()
    for word in _WORD.findall(text):
        for chunk in word.split("_"):
            for piece in _CAMEL.split(chunk):
                if piece:
                    parts.add(piece.lower())
    return parts


def direction(text: str) -> str | None:
    """`reads` / `writes` / `ambiguous` / None."""
    words = _parts(text)
    reads = any(v in words for v in READ_VERBS)
    writes = any(v in words for v in WRITE_VERBS)
    if reads and writes:
        return "ambiguous"
    return "reads" if reads else "writes" if writes else None


def config_owner(repo: str, topology: Topology) -> str | None:
    """config 층은 **레포당 하나**다(사내 확인). 서비스가 하나면 그 서비스의 것이고,
    둘 이상이 공유하면 누구 것이라고 못 한다 — None이면 레포 노드에 붙는다."""
    mine = [n for n, svc in topology.services.items() if svc.repo == repo]
    return mine[0] if len(mine) == 1 else None


def owner(file: str, repo: str, topology: Topology) -> tuple[str | None, str]:
    """이 파일은 어느 서비스의 것인가 — `(서비스 또는 None, 확신)`.

    레포에 서비스가 하나면 그것(EXTRACTED). 여럿이면 토폴로지 `path`로(EXTRACTED),
    없으면 첫 디렉터리 이름이 서비스 이름과 같을 때(INFERRED). 그래도 못 가르면
    None·AMBIGUOUS — 엣지는 레포 노드에 붙고, `code status`가 그 수를 말한다.
    """
    candidates = {n: s for n, s in topology.services.items() if s.repo == repo}
    if len(candidates) == 1:
        return next(iter(candidates)), "EXTRACTED"
    for name, svc in sorted(candidates.items()):
        if svc.path and (file == svc.path or file.startswith(svc.path.rstrip("/") + "/")):
            return name, "EXTRACTED"
    head = file.split("/", 1)[0]
    if head in candidates:
        return head, "INFERRED"
    return None, "AMBIGUOUS"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()


def _node_id(kind: str, value: str) -> str:
    return f"{kind}_{_slug(value)}"


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= TEXT_CHARS else text[:TEXT_CHARS] + "…"


def extract(*, names: Iterable[Name], topology: Topology,
            hits_for: Callable[[str], list[Hit]], commits: dict[str, str]) -> dict:
    """흐름 그래프 하나 — graphify `graph.json` 스키마.

    `hits_for(pattern)`은 배포 커밋에서의 `git grep -n` 결과다(주입 — 테스트는
    파일 뭉치로, 운영은 `RealCodeReader`로). 결정론: 이름·히트를 정렬해 처리하므로
    같은 커밋이면 같은 JSON이다(테스트가 두 번 돌려 대조한다).
    """
    nodes: dict[str, dict] = {}
    links: list[dict] = []

    def put_node(id_: str, label: str, kind: str, file: str, line: int, **extra):
        if id_ not in nodes:
            nodes[id_] = {"id": id_, "label": label, "type": kind,
                          "source_file": file, "source_location": f"L{line}", **extra}

    for svc_name, svc in sorted(topology.services.items()):
        put_node(f"service_{_slug(svc_name)}", svc_name, "service", svc.repo, 0,
                 repo=svc.repo, role=svc.role)
    for repo in sorted({s.repo for s in topology.services.values()}):
        put_node(f"repo_{_slug(repo)}", repo, "repo", repo, 0,
                 commit=commits.get(repo, ""))
    # 서비스 → 레포. 같은 코드를 여러 서비스가 띄우면(사내: processor 5개, sink 2개) 코드
    # 엣지는 레포에 붙는데, 그러면 서비스에서 출발하는 경로가 없어진다. 이 엣지가 그 다리다.
    for svc_name, svc in sorted(topology.services.items()):
        links.append({"source": f"service_{_slug(svc_name)}", "target": f"repo_{_slug(svc.repo)}",
                      "relation": "runs", "confidence": "EXTRACTED", "attributed": "service",
                      "origin": "topology", "source_file": "knowledge/topology",
                      "source_location": "L0", "repo": svc.repo,
                      "commit": commits.get(svc.repo, ""), "text": ""})

    for name in sorted(set(names), key=lambda n: (n.kind, n.value, n.key_path)):
        target = _node_id(name.kind, name.value)
        for pattern in name.patterns:
            confidence = "EXTRACTED" if pattern == name.literal else "INFERRED"
            for hit in sorted(hits_for(pattern), key=lambda h: (h.repo, h.file, h.line)):
                if confidence == "INFERRED" and any(
                        tok not in hit.text for tok in name.required_tokens
                ) and not _quoted_whole(name.key_token, hit.text):
                    continue
                if _is_config(hit.file) and name.services:
                    continue            # config 엣지는 아래에서 서비스 단위로 만든다
                if not _is_config(hit.file) and _is_noise(hit.file, hit.text):
                    continue
                if (confidence == "EXTRACTED" and not _is_config(hit.file)
                        and not _DISTINCTIVE.search(pattern) and direction(hit.text) is None):
                    # 한 단어 리터럴(`alarm`)은 같은 줄에 읽기/쓰기 동사가 있어야 자원이다 — 배지 상태값
                    # `"alarm"`이 alarm 컬렉션 읽기로 잡혔다(사내). 여러 조각짜리는 어디 있든 잡는다.
                    continue
                put_node(target, name.value, name.kind, hit.file, hit.line,
                         key_path=name.key_path)
                base = {"target": target, "confidence": confidence,
                        "source_file": hit.file, "source_location": f"L{hit.line}",
                        "repo": hit.repo, "commit": hit.commit, "text": _clip(hit.text)}
                if _is_config(hit.file):
                    if confidence != "EXTRACTED":
                        continue            # config 안의 키 토큰 매치는 선언 그 자체다
                    who = config_owner(hit.repo, topology)
                    # 출처가 방향을 말하면(`consumer.topic` 등) 선언이 곧 관계다. 공유
                    # 레포면 레포 노드에 붙는다 — "이 레포의 누군가가 소비한다"까지가 사실이다.
                    links.append({**base, "origin": "config",
                                  "source": (f"service_{_slug(who)}" if who
                                             else f"repo_{_slug(hit.repo)}"),
                                  "relation": name.relation or "declares",
                                  "attributed": "service" if who else "repo"})
                    continue
                who, sure = owner(hit.file, hit.repo, topology)
                verb = direction(hit.text)
                # 동사가 옆 줄에 있으면(`coll = …` / `mongo[coll].find(…)`) 그것을 쓰되
                # INFERRED다 — 옆 줄의 동사가 다른 자원의 것일 수 있다.
                sure_verb = "EXTRACTED" if verb else "INFERRED"
                verb = verb or direction(hit.context)
                # 동사가 없는 줄(주석·바인딩)도 **버리지 않는다** — `mentions`로 남긴다.
                # 방향은 모르지만 "이 서비스가 이 이름을 안다"는 것은 사실이고, 그게
                # 리드가 어느 서비스를 볼지 고르는 데는 충분하다.
                relation = (RELATION[name.kind][verb] if verb in ("reads", "writes")
                            else "mentions")
                links.append({**base, "origin": "code",
                              "source": (f"service_{_slug(who)}" if who
                                         else f"repo_{_slug(hit.repo)}"),
                              "relation": relation,
                              "attributed": "service" if who else "repo",
                              "confidence": _weakest(
                                  confidence, sure,
                                  sure_verb if verb in ("reads", "writes") else "AMBIGUOUS")})
        # 코드 엣지 뒤에 둔다 — 경로 탐색이 링크 순서로 첫 엣지를 고르므로, 코드 줄이 있으면
        # 그 줄이 근거로 찍힌다.
        if name.services:
            _declare_per_service(name, target, topology, hits_for, commits, put_node, links)
    return {"directed": True, "multigraph": True, "graph": {"kind": "ops-flow"},
            "nodes": list(nodes.values()), "links": links, "hyperedges": []}


def _declare_per_service(name: Name, target: str, topology: Topology, hits_for, commits,
                         put_node, links: list[dict]) -> None:
    """config 엣지를 **서비스마다** 그 서비스의 합친 config에서 만든다 — grep 없이, EXTRACTED.

    근거 줄은 있으면 붙인다: 그 레포의 config 파일에서 값 리터럴이 있는 첫 줄. 없어도
    엣지는 선다 — 합친 config에 있다는 것이 사실이고, 파일 줄은 편의다.
    """
    grepped: dict[str, Hit] = {}
    if name.literal:
        for hit in sorted(hits_for(name.literal), key=lambda h: (h.repo, h.file, h.line)):
            if _is_config(hit.file):
                grepped.setdefault(hit.repo, hit)
    own = {svc: (file, line, text) for svc, file, line, text in name.evidence}
    for svc_name in sorted(name.services):
        svc = topology.services.get(svc_name)
        if svc is None:
            continue
        # 그 서비스가 실제로 합친 층의 줄이 먼저다. 레포 grep은 이 사이트에 안 쓰이는 층
        # (`_dev`)을 먼저 집는다 — 알파벳순이라서.
        if svc_name in own:
            file, line, text = own[svc_name]
        elif svc.repo in grepped:
            hit = grepped[svc.repo]
            file, line, text = hit.file, hit.line, hit.text
        else:
            file, line, text = f"config({svc.repo})", 0, ""
        put_node(target, name.value, name.kind, file, line, key_path=name.key_path)
        links.append({"source": f"service_{_slug(svc_name)}", "target": target,
                      "relation": name.relation or "declares", "confidence": "EXTRACTED",
                      "attributed": "service", "origin": "config",
                      "source_file": file, "source_location": f"L{line}",
                      "repo": svc.repo, "commit": commits.get(svc.repo, ""),
                      "text": _clip(text)})


def _is_config(file: str) -> bool:
    return file.startswith("config/") or "/config/" in file or file.endswith((".json", ".yaml", ".yml", ".toml"))


_NOISE_DIRS = ("tests", "test", "docs", "doc", "examples")
_NOISE_SUFFIX = (".md", ".rst", ".txt")
_COMMENT = ("#", "//", "/*", "*", "<!--", '"""', "'''")


def _is_noise(file: str, text: str) -> bool:
    """문서·테스트·주석 줄. 사내 첫 실행에서 코드 엣지의 60%가 여기서 나왔다 — 이름이 적힌
    문서와 테스트는 "이 서비스가 이 자원을 쓴다"의 근거가 아니다."""
    parts = file.replace("\\", "/").split("/")
    base = parts[-1]
    if file.endswith(_NOISE_SUFFIX) or any(p in _NOISE_DIRS for p in parts[:-1]):
        return True
    if base.startswith("test_") or base.endswith(("_test.py", "_tests.py")):
        return True
    return text.lstrip().startswith(_COMMENT)


_DISTINCTIVE = re.compile(r"[_:.\-]")


def _quoted_whole(token: str, text: str) -> bool:
    """config 키가 **따옴표로 통째로** 코드 줄에 있다 — 조상 키가 없어도 받는다.

    사내 코드는 키를 Enum 값으로 들고(`PROD_BEFORE_WORKER_ALL = "prodcheck_before_cur_worker_all"`)
    공통 접근 함수가 런타임에 `cfg["redis_key"][key]`로 꺼낸다. 키와 `redis_key`가 같은 줄에
    오는 일이 없다. 그 Enum 줄이 코드에서 이 키를 아는 유일한 자리이고, 리드가 홉을 밟기
    시작할 곳이다. `alarm`·`processor` 같은 한 단어는 제외한다 — `format(service="processor")`처럼
    어디에나 있다. 밑줄·콜론·점·대시로 이어진 여러 조각짜리 이름만 받는다.
    """
    if not _DISTINCTIVE.search(token):
        return False
    return re.search(r"""["']""" + re.escape(token) + r"""["']""", text) is not None


_RANK = {"EXTRACTED": 0, "INFERRED": 1, "AMBIGUOUS": 2}


def _weakest(*levels: str) -> str:
    return max(levels, key=lambda l: _RANK[l])


# ── 질의 — 브리핑과 `code.flow`가 쓴다. graphify CLI와 같은 뜻이다. ─────────

def _find(graph: dict, name: str) -> list[str]:
    return [n["id"] for n in graph["nodes"] if n["id"] == name or n["label"] == name]


def neighbors(graph: dict, name: str, *, depth: int = 1) -> list[dict]:
    """이름의 이웃 엣지들 — 방향 무시, `depth`단계까지, BFS 순서."""
    start = _find(graph, name)
    if not start:
        return []
    seen, frontier, out = set(start), deque((s, 0) for s in start), []
    used: set[int] = set()
    while frontier:
        node, d = frontier.popleft()
        if d >= depth:
            continue
        for i, e in enumerate(graph["links"]):
            if i in used or node not in (e["source"], e["target"]):
                continue
            used.add(i)
            out.append(e)
            other = e["target"] if e["source"] == node else e["source"]
            if other not in seen:
                seen.add(other)
                frontier.append((other, d + 1))
    return out


# 데이터가 흐르는 방향. 서비스가 자원에 **쓰면** 서비스→자원, **읽으면** 자원→서비스.
# `mentions`·`declares`는 방향이 없어 흐름 경로에 안 낀다(이웃에는 낀다).
_OUTBOUND = ("writes", "produces")
_INBOUND = ("reads", "consumes", "consumes_as")
# 서비스↔레포 다리. 자원 경로가 없을 때만 탄다 — 같은 레포의 서비스 둘은 `runs` 두 홉으로
# 항상 이어져서, 먼저 허용하면 토픽을 지나는 진짜 흐름을 가린다.
_BRIDGE = "runs"


def _step(edge: dict, node: str, *, undirected: bool, bridge: bool = False) -> str | None:
    """`node`에서 이 엣지를 타고 갈 수 있으면 건너편, 아니면 None."""
    if edge["relation"] == _BRIDGE and not (undirected or bridge):
        return None
    if undirected or edge["relation"] == _BRIDGE:
        if node == edge["source"]:
            return edge["target"]
        return edge["source"] if node == edge["target"] else None
    if edge["relation"] in _OUTBOUND and node == edge["source"]:
        return edge["target"]
    if edge["relation"] in _INBOUND and node == edge["target"]:
        return edge["source"]
    return None


def shortest_path(graph: dict, a: str, b: str, *, undirected: bool = False) -> list[dict] | None:
    """`a`에서 `b`로 **데이터가 흐르는** 최단 경로의 엣지들. 없으면 None.

    방향을 무시하면 processor와 sink가 둘 다 쓰는 하트비트 키가 2홉 경로가 된다 —
    그건 흐름이 아니다. 기본은 쓰기→자원→읽기만 통과한다. `undirected=True`는
    "관계가 있기는 한가"를 물을 때만. 자원만으로 길이 없으면 서비스↔레포 다리(`runs`)를
    허용해 한 번 더 찾는다 — 코드 엣지가 레포에 붙은 공유 레포를 지나기 위해서다.
    """
    starts, goals = _find(graph, a), set(_find(graph, b))
    if not starts or not goals:
        return None
    for bridge in (False, True):
        path = _bfs(graph, starts, goals, undirected=undirected, bridge=bridge)
        if path is not None:
            return path
    return None


def _bfs(graph: dict, starts: list[str], goals: set[str], *, undirected: bool, bridge: bool
         ) -> list[dict] | None:
    prev: dict[str, tuple[str, dict] | None] = {s: None for s in starts}
    queue = deque(starts)
    while queue:
        node = queue.popleft()
        if node in goals:
            path = []
            while prev[node] is not None:
                node, edge = prev[node]
                path.append(edge)
            return list(reversed(path))
        for e in graph["links"]:
            other = _step(e, node, undirected=undirected, bridge=bridge)
            if other is not None and other not in prev:
                prev[other] = (node, e)
                queue.append(other)
    return None


def label_of(graph: dict, node_id: str) -> str:
    return next((n["label"] for n in graph["nodes"] if n["id"] == node_id), node_id)


def describe(graph: dict, node_id: str) -> str:
    """사람용 표시. 자원에는 종류를 붙인다 — 사내에 같은 이름의 토픽과 컬렉션이 실제로 있어
    라벨만 찍으면 `consumes`와 `declares`가 같은 것을 가리키는 것처럼 보였다."""
    node = next((n for n in graph["nodes"] if n["id"] == node_id), None)
    if node is None:
        return node_id
    if node.get("type") not in RESOURCE_TYPES:
        return node["label"]
    return f"{node['label']} [{node['type']}]"


def render_path(graph: dict, edges: list[dict]) -> str:
    """`processor —produces→ mx.alarm.main ←consumes— sink` 한 줄."""
    if not edges:
        return ""
    # 첫 엣지의 시작점을 정한다 — 두 번째 엣지와 공유하지 않는 끝이 시작이다
    first = edges[0]
    if len(edges) > 1:
        shared = {first["source"], first["target"]} & {edges[1]["source"], edges[1]["target"]}
        cur = ({first["source"], first["target"]} - shared).pop() if shared else first["source"]
    else:
        cur = first["source"]
    out = [label_of(graph, cur)]
    for e in edges:
        if e["source"] == cur:
            out.append(f"—{e['relation']}→ {label_of(graph, e['target'])}")
            cur = e["target"]
        else:
            out.append(f"←{e['relation']}— {label_of(graph, e['source'])}")
            cur = e["source"]
    return " ".join(out)


RESOURCE_TYPES = ("topic", "group", "collection", "rediskey", "endpoint")


# ── 리드용 텍스트 — 브리핑의 <데이터 흐름>과 `code.flow` ─────────────────

_FLOW_ORDER = ("serves", "reads", "reads(config키)", "reads(추정)", "produces", "consumes", "consumes_as", "declares")
_KIND_RANK = {"service": 0, "endpoint": 1, "topic": 2, "collection": 3, "rediskey": 4, "group": 5}
_MIN_SEED = 3
_MAX_RESOURCE_SEEDS = 3
_MAX_NAMES = 8


def _as_token(label: str, blob: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(label)}(?![A-Za-z0-9_])", blob) is not None


def find_seeds(graph: dict, texts) -> list[str]:
    """증상·증거·가설 본문에 **토큰으로** 나오는 그래프 이름들 — 서비스 먼저(상한 없음), 자원은
    종류·이름순으로 셋까지. 식별자 안의 부분 문자열(`alarm_events` 속 `alarm`)은 안 친다 — 사내에서
    응답 필드 이름이 같은 이름의 컬렉션을 씨앗으로 만들어 첫 줄을 차지했다. 세 글자부터."""
    blob = "\n".join(t for t in texts if t)
    found = [n for n in graph["nodes"]
             if n.get("type") in _KIND_RANK and len(n["label"]) >= _MIN_SEED
             and _as_token(n["label"], blob)]
    found.sort(key=lambda n: (_KIND_RANK[n["type"]], n["label"]))
    services = [n["id"] for n in found if n["type"] == "service"]
    resources = [n["id"] for n in found if n["type"] != "service"]
    return services + resources[:_MAX_RESOURCE_SEEDS]


def flow_text(graph: dict, seeds: list[str], *, budget: int = 800) -> str:
    """`<데이터 흐름>` 본문. **config 층 엣지만** — 코드 층은 사내에서 소음으로 확인됐고 홉을
    밟는 것은 11b의 일이다. 씨앗의 이웃 1단계, 그다음 닿은 서비스의 토픽(2단계). 씨앗이 없으면
    토픽 골격(누가 내고 누가 받나). `budget`자에서 끊고 끊었다고 적는다.
    `레포{a,b}`는 같은 config를 쓰는 서비스 전부 — 어느 쪽인지는 config가 모른다."""
    by_id = {n["id"]: n for n in graph["nodes"]}
    # `serves`는 코드에서 왔지만 배선이다(라우트 선언은 이름 매칭이 아니라 구문이다) — 끝점 줄이 서야
    # 접수 경로의 path에서 서빙 서비스로 첫 홉이 이어진다.
    links = [e for e in graph["links"]
             if (e.get("origin") in ("config", "trace") or e["relation"] == "serves")
             and e["source"] in by_id and e["target"] in by_id]
    if not links:
        return "(config에서 뽑은 흐름이 없다)"

    members: dict[str, list[str]] = {}
    for n in graph["nodes"]:
        if n.get("type") == "service":
            members.setdefault(n.get("repo", ""), []).append(n["id"])

    def fold(service_ids: set[str]) -> list[str]:
        # 같은 코드를 띄우는 서비스들(사내: processor 5개)은 config도 같아서 전부 같은 관계를
        # 갖는다. 그대로 나열하면 "sink도 생산한다"처럼 읽힌다 — config는 레포까지만 안다.
        # 레포의 서비스 전부가 있으면 `레포{a,b}`로 접어 그 사실을 드러낸다.
        out = []
        for repo, ids in members.items():
            if len(ids) > 1 and set(ids) <= service_ids:
                out.append(f"{repo}{{{','.join(sorted(by_id[i]['label'] for i in ids))}}}")
                service_ids = service_ids - set(ids)
        return sorted(out + [by_id[i]["label"] for i in service_ids])

    def clip(names) -> str:
        # 관계당 여덟 개까지 — 사내에서 토픽 15개가 한 줄에 늘어서 예산 800자를 거의 다 먹었다.
        names = sorted(names)
        if len(names) > _MAX_NAMES:
            names = names[:_MAX_NAMES] + [f"외 {len(names) - _MAX_NAMES}개"]
        return ", ".join(names)

    def service_parts(node_id: str, *, topics_only: bool) -> list[str]:
        groups: dict[str, set[str]] = {}
        for e in links:
            if e["source"] != node_id:
                continue
            if topics_only and by_id[e["target"]]["type"] != "topic":
                continue
            groups.setdefault(e["relation"], set()).add(by_id[e["target"]]["label"])
        return [f"{rel}: {clip(groups[rel])}" for rel in _FLOW_ORDER if rel in groups]

    def code_direction(node_id: str, rels: tuple[str, ...]) -> str:
        # config가 produces·consumes를 같은 서비스들에 붙였으면(공유 config) 방향을 모른다. 코드 층이
        # 서비스까지 짚은 엣지가 있을 때만 그 한 조각을 보탠다 — 3b 측정에서 이 자리가 비어 리드가
        # 생산자와 소비자를 못 갈랐다. 레포에만 붙은 코드 엣지는 아무것도 더해 주지 않으므로 뺀다.
        by_rel: dict[str, set[str]] = {}
        for e in graph["links"]:
            if (e.get("origin") == "code" and e["target"] == node_id and e["relation"] in rels
                    and by_id.get(e["source"], {}).get("type") == "service"):
                by_rel.setdefault(e["relation"], set()).add(by_id[e["source"]]["label"])
        if not by_rel:
            return ""
        return " · 코드로는 " + " · ".join(f"{rel}: {clip(by_rel[rel])}" for rel in rels if rel in by_rel)

    def line_for(node_id: str, *, topics_only: bool = False, with_code: bool = False) -> str:
        node = by_id[node_id]
        if node["type"] == "service":
            shared = len(members.get(node.get("repo", ""), [])) > 1
            head = f"{node['label']} [service · {node.get('repo', '')}{' 공유 config' if shared else ''}]"
            parts = service_parts(node_id, topics_only=topics_only)
            return f"{head}: {' · '.join(parts)}" if parts else f"{head}: (config 엣지 없음)"
        groups: dict[str, set[str]] = {}
        for e in links:
            if e["target"] == node_id and e.get("origin") != "trace":
                groups.setdefault(e["relation"], set()).add(e["source"])
        head = f"{node['label']} [{node['type']}]"
        parts = [f"{rel}: {clip(fold(groups[rel]))}" for rel in _FLOW_ORDER if rel in groups]
        if node["type"] == "endpoint":
            # 추적기가 짚은 읽기 — 확실·config키·추정을 갈라 적는다(등급은 그대로, 표시만 가른다 — 사내 읽기의
            # 대부분이 config 키 경유라 "추정" 하나로 묶으면 리드가 전부를 확인하러 간다). 자원엔 종류를 붙인다.
            reads: dict[str, set[str]] = {}
            for e in links:
                if e["source"] == node_id and e.get("origin") == "trace":
                    rel = "reads" + _READ_MARK[read_mark(e)]
                    reads.setdefault(rel, set()).add(f"{by_id[e['target']]['label']} [{by_id[e['target']]['type']}]")
            parts += [f"{rel}: {clip(reads[rel])}" for rel in ("reads", "reads(config키)", "reads(추정)") if rel in reads]
        directions = ("produces", "consumes") if node["type"] == "topic" else ("writes", "reads")
        ambiguous = "produces" in groups and groups["produces"] == groups.get("consumes")
        tail = code_direction(node_id, directions) if (with_code or ambiguous) else ""
        return f"{head}: {' · '.join(parts)}{tail}" if parts else f"{head}: (config 엣지 없음){tail}"

    def touched_lines(service_ids, *, topics_only: bool) -> list[str]:
        # 2단계의 서비스 줄 — 같은 레포의 서비스들이 같은 줄을 가지면 한 줄로 접는다. 사내에서
        # processor 다섯이 같은 토픽 15개를 다섯 번 반복해 절단(+4줄)을 불렀다. 보여 줄 관계가 없는
        # 서비스는 줄을 안 낸다 — "(config 엣지 없음)"은 토픽만 걸렀다는 뜻인데 없는 것처럼 읽힌다.
        by_key: dict[tuple, list[str]] = {}
        for sid in sorted(service_ids, key=lambda s: by_id[s]["label"]):
            parts = tuple(service_parts(sid, topics_only=topics_only))
            if not parts:
                continue
            by_key.setdefault((by_id[sid].get("repo", ""), parts), []).append(sid)
        out = []
        for (repo, parts), ids in by_key.items():
            if len(ids) > 1:
                out.append(f"{repo}{{{','.join(by_id[i]['label'] for i in ids)}}} "
                           f"[service · {repo} 공유 config]: {' · '.join(parts)}")
            else:
                out.append(line_for(ids[0], topics_only=topics_only))
        return out

    lines: list[str] = []
    if seeds:
        # 자원 씨앗에 닿은 서비스는 토픽만(15개 토픽이 declares 수십 개와 같이 오면 예산이 끝난다).
        # 끝점 씨앗에 닿은 서비스는 전부 — 사다리의 다음 칸이 "그 코드가 읽는 데이터"이고, 그게
        # declares에 있다.
        via_topics: list[str] = []
        via_endpoint: list[str] = []
        via_reads: list[str] = []
        for sid in seeds:
            if sid in by_id:
                lines.append(line_for(sid))
                if by_id[sid]["type"] == "endpoint":
                    via_endpoint += [e["source"] for e in links if e["target"] == sid]
                    via_reads += [e["target"] for e in links if e["source"] == sid and e.get("origin") == "trace"]
                elif by_id[sid]["type"] != "service":
                    via_topics += [e["source"] for e in links if e["target"] == sid]
        lines += touched_lines(set(via_endpoint) - set(seeds), topics_only=False)
        # 끝점이 읽는 자원 — 사다리의 셋째 칸 "그 데이터를 쓰는 서비스"가 코드 층에 있다(config는 declares뿐).
        for rid in sorted(set(via_reads) - set(seeds), key=lambda i: by_id[i]["label"]):
            lines.append(line_for(rid, with_code=True))
        lines += touched_lines(set(via_topics) - set(seeds) - set(via_endpoint), topics_only=True)
    else:
        topics = [n for n in graph["nodes"] if n.get("type") == "topic"]
        degree = {n["id"]: sum(1 for e in links if e["target"] == n["id"]) for n in topics}
        for n in sorted(topics, key=lambda n: (-degree[n["id"]], n["label"])):
            lines.append(line_for(n["id"]))

    out, used = [], 0
    for line in lines:
        if used + len(line) + 1 > budget and out:
            out.append(f"… (+{len(lines) - len(out)}줄, code.flow(name)으로 더 본다)")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


_CONF_RANK = {"EXTRACTED": 0, "INFERRED": 1, "AMBIGUOUS": 2}


def neighbor_lines(graph: dict, edges: list[dict], *, limit: int = 40) -> tuple[list[str], int]:
    """`code.flow`의 본문 — config 엣지 먼저, 그다음 코드 엣지를 확신 순으로. `(줄들, 못 실은 수)`."""
    ranked = sorted((e for e in edges if e["relation"] != _BRIDGE),
                    key=lambda e: (0 if e.get("origin") == "config" else 1,
                                   _CONF_RANK.get(e.get("confidence"), 9), e["relation"],
                                   label_of(graph, e["source"]), label_of(graph, e["target"])))
    lines = [f"{describe(graph, e['source'])} —{e['relation']}→ {describe(graph, e['target'])}"
             f"  [{e.get('origin', 'code')}·{e.get('confidence', '?')}] "
             f"{e.get('source_file', '?')}:{e.get('source_location', '?')}"
             for e in ranked[:limit]]
    return lines, max(0, len(ranked) - limit)


def summary(graph: dict) -> dict:
    """`code status`가 찍을 숫자들."""
    links = graph["links"]
    kinds = {}
    for n in graph["nodes"]:
        kinds[n.get("type", "?")] = kinds.get(n.get("type", "?"), 0) + 1
    # config에만 보이고 코드 줄에서 직접 못 찾은 이름. 권고가 아니라 숫자다 — 사내 코드는
    # 키를 Enum·공통 헬퍼 뒤에 두어 "안 쓴다"가 아니라 "텍스트로는 못 찾는다"가 맞다.
    resources = {n["id"] for n in graph["nodes"]
                 if n.get("type") in RESOURCE_TYPES and n.get("type") != "endpoint"}
    coded = {e["target"] for e in links if e.get("origin", "code") == "code"}
    endpoints = [n for n in graph["nodes"] if n.get("type") == "endpoint"]
    served = {e["target"] for e in links if e["relation"] == "serves"}
    traced = {e["source"] for e in links if e.get("origin") == "trace"}
    return {"nodes": len(graph["nodes"]), "links": len(links), "kinds": kinds,
            "repo_level": sum(1 for e in links if e.get("attributed") == "repo"),
            "ambiguous": sum(1 for e in links if e.get("confidence") == "AMBIGUOUS"),
            "unreferenced": len(resources - coded),
            "endpoints": len(endpoints),
            "endpoints_registered": sum(1 for n in endpoints if n.get("entry")),
            "endpoints_unserved": sum(1 for n in endpoints if n["id"] not in served),
            # 자원까지 이어진 끝점 / 추적은 됐는데 읽기 없이 gap만 남은 끝점(막힘)
            "endpoints_traced": sum(1 for n in endpoints if n["id"] in traced),
            "endpoints_blocked": sum(1 for n in endpoints if n["id"] not in traced
                                     and n.get("traced") == "ok" and n.get("gaps")),
            # 추적기와 인덱스의 대조(11d 6c-2) — 패리티가 서면 추적기를 뺀다
            **{f"endpoints_index_{k}": sum(1 for n in endpoints if (n.get("index_check") or {}).get("status") == s)
               for k, s in (("same", "same"), ("diff", "diff"), ("no_handler", "no_handler"))}}


def advise(graph: dict, topology: Topology) -> list[str]:
    """토폴로지·config를 고칠 사람에게 주는 권고. **막지 않는다** — 적기만 한다."""
    out = []
    links = graph["links"]
    resources = [n for n in graph["nodes"] if n.get("type") in RESOURCE_TYPES]
    if not resources:
        out.append("이름을 하나도 못 뽑았다 — knowledge/topology의 flow.sources가 config 모양과 안 맞는다")
        return out
    # 같은 코드를 여러 서비스가 띄우는 레포(사내: processor 5개, sink 2개). 코드 엣지는 레포에
    # 붙는 것이 맞고 "path를 채워라"는 틀린 권고였다 — 파일로는 원리상 못 가른다. 사실만 적는다.
    shared_code: dict[str, int] = {}
    for e in links:
        if e.get("attributed") == "repo" and e.get("origin", "code") == "code":
            shared_code[e["repo"]] = shared_code.get(e["repo"], 0) + 1
    for repo, n in sorted(shared_code.items()):
        members = sorted(name for name, s in topology.services.items() if s.repo == repo)
        out.append(f"{repo}: 서비스 {len(members)}개({', '.join(members)})가 코드를 공유한다 — "
                   f"코드 엣지 {n}개는 레포 단위다")
    # 서비스에 자원이 하나도 없다는 말은 레포에 서비스가 하나뿐일 때만 뜻이 있다 — 공유
    # 레포에서는 위 한 줄이 이미 설명이고, 서비스마다 같은 말을 되풀이하면 사람이 서비스를 의심한다.
    for name, svc in sorted(topology.services.items()):
        if sum(1 for s in topology.services.values() if s.repo == svc.repo) > 1:
            continue
        sid = f"service_{_slug(name)}"
        if not any(e["source"] == sid and e["relation"] != _BRIDGE for e in links):
            out.append(f"{name}: config에도 코드에도 자원이 없다 — 레포·역할 선언을 의심하라")
    s = summary(graph)
    if s["endpoints"]:
        out.append(f"끝점 {s['endpoints']}개 중 등재 {s['endpoints_registered']}개 · "
                   f"서빙 서비스를 못 찾은 {s['endpoints_unserved']}개")
    if s["endpoints_traced"] or s["endpoints_blocked"]:
        out.append(f"끝점 추적: 자원까지 이어진 {s['endpoints_traced']}개 · 막힌 {s['endpoints_blocked']}개")
    return out


def add_trace(graph: dict, endpoint_id: str, result) -> dict:
    """추적기(`trace.Trace`)의 결과를 오버레이에 싣는다 — `endpoint —reads→ resource` 엣지(origin
    `trace`, 확실→EXTRACTED, 추정→INFERRED, file:line)와 끝점 노드의 사슬·gap. 이름 목록에 있는 자원은
    전부 노드가 있으므로 없는 이름은 조용히 지나간다(자원이 아니라 다른 것이 잡힌 경우)."""
    nodes = {n["id"]: dict(n) for n in graph["nodes"]}
    links = list(graph["links"])
    node = nodes.get(endpoint_id)
    if node is None:
        return graph
    node["traced"] = result.status
    node["trace_repo"] = result.repo         # 인덱스 대조(6c-2)가 핸들러를 찾는 레포
    node["chain"] = [f"{s.file}:L{s.line} {s.qualname}" for s in result.chain]
    node["chain_parent"] = [s.parent for s in result.chain]
    node["gaps"] = [f"{g.file}:L{g.line} {g.why}" for g in result.gaps]
    for r in result.reads:
        target = _node_id(r.kind, r.name)
        if target not in nodes:
            continue
        links.append({"source": endpoint_id, "target": target, "relation": "reads",
                      "confidence": "EXTRACTED" if r.grade == "확실" else "INFERRED", "grade": r.grade, "via": r.via,
                      "step": r.step,
                      "attributed": "endpoint", "origin": "trace", "source_file": r.file,
                      "source_location": f"L{r.line}", "repo": result.repo, "commit": "", "text": ""})
    return {**graph, "nodes": list(nodes.values()), "links": links}


def read_mark(edge: dict) -> str:
    """추적 읽기 엣지의 표시 — 등급이 확실이면 `확실`, 추정인데 이름이 config 키 경유면 `config키`, 그 밖은
    `추정`. 등급(EXTRACTED/INFERRED)은 안 건드린다 — 표시만 가른다(step-11b 2f 뒤의 결정)."""
    if edge.get("confidence") == "EXTRACTED":
        return "확실"
    return "config키" if edge.get("via") == "key" else "추정"


_READ_MARK = {"확실": "", "config키": "(config키)", "추정": "(추정)"}
_TRACE_MAX_GAPS = 3
_TRACE_HEAD = 4          # 읽기가 하나도 없는 끝점은 앞 걸음 이만큼만


def endpoint_id(path: str) -> str:
    return _node_id("endpoint", path)


def trace_lines(graph: dict, endpoint_id: str, *, max_gaps: int = _TRACE_MAX_GAPS) -> list[str] | None:
    """리드에게 보여 줄 사슬 — **읽기로 이어진 걸음의 조상만** 남긴 트리. 사내 사슬은 28~30걸음이고 대부분이
    저장소 부모의 헬퍼와 로거라, 통째로 주면 함수 서너 개로 좁혀 준다는 약속이 깨진다. 읽기는 걸음 옆에
    확실·config키·추정으로 붙고, gap은 셋까지, 꼬리에 "걸음 N 중 M". 추적이 안 된 끝점이면 None."""
    node = next((n for n in graph.get("nodes", []) if n["id"] == endpoint_id), None)
    if node is None or node.get("traced") != "ok" or not node.get("chain"):
        return None
    chain: list[str] = list(node["chain"])
    parents: list = list(node.get("chain_parent") or [None] * len(chain))
    by_id = {n["id"]: n for n in graph.get("nodes", [])}
    reads_at: dict[int, list[str]] = {}
    for e in graph.get("links", []):
        if e.get("source") == endpoint_id and e.get("origin") == "trace" and e.get("target") in by_id:
            target = by_id[e["target"]]
            reads_at.setdefault(int(e.get("step", -1)), []).append(
                f"{target['label']} [{target['type']}] {read_mark(e)}")
    keep: set[int] = set()
    for i in reads_at:
        cur = i if 0 <= i < len(chain) else None
        while cur is not None and cur not in keep:
            keep.add(cur)
            cur = parents[cur]
    if not keep:
        keep = set(range(min(_TRACE_HEAD, len(chain))))

    def depth(i: int) -> int:
        d, cur = 0, parents[i]
        while cur is not None and d < 64:
            d, cur = d + 1, parents[cur]
        return d

    lines = []
    for i in sorted(keep):
        head = ("  " * depth(i) + "→ " if parents[i] is not None else "") + chain[i]
        lines.append(head + (f" — reads: {' · '.join(reads_at[i])}" if i in reads_at else ""))
    gaps = list(node.get("gaps") or [])
    if gaps:
        tail = f" 외 {len(gaps) - max_gaps}개" if len(gaps) > max_gaps else ""
        lines.append(f"못 따라감 {len(gaps)}: {' · '.join(gaps[:max_gaps])}{tail}")
    # 인덱스 대조(11d 6c-2) — 같으면 안 적는다(소음). 다르면 리드가 "인덱스만" 자원을 다음 읽기 후보로 쓸 수 있다.
    check = node.get("index_check") or {}
    if check.get("status") == "diff":
        parts = [f"{label} {_few(check[key])}" for key, label in (("only_index", "인덱스만"), ("only_tracer", "추적기만"))
                 if check.get(key)]
        lines.append("인덱스 대조: 다르다 — " + " / ".join(parts))
    elif check.get("status") == "no_handler":
        lines.append(f"인덱스 대조: 핸들러를 인덱스에서 못 찾았다({chain[0].split(' ', 1)[0]})")
    if len(keep) < len(chain):
        lines.append(f"걸음 {len(chain)} 중 읽기로 이어진 {len(keep)}만 적었다 — 나머지는 code.read로 본다"
                     if reads_at else f"걸음 {len(chain)} 중 {len(keep)}만 적었다 — 읽기로 이어진 걸음이 없다")
    return lines


def _few(items: list[str], k: int = 3) -> str:
    return " · ".join(items[:k]) + (f" 외 {len(items) - k}" if len(items) > k else "")


_MARK_ORDER = {"확실": 0, "config키": 1, "추정": 2}


def traced_reads(graph: dict, endpoint_id: str, *, kind: str | None = None) -> list[str]:
    """그 끝점의 추적 읽기 대상 이름들 — 확실 → config키 → 추정, 같은 표시면 걸음 순. 브리핑의 recompute 예시가
    "그 끝점이 읽는 컬렉션"을 여기서 고른다 — 추정을 앞세우면 리드가 대조할 컬렉션이 그 코드가 읽지도 않는
    것이 될 수 있다."""
    by_id = {n["id"]: n for n in graph.get("nodes", [])}
    found: list[tuple[int, int, str]] = []
    for e in graph.get("links", []):
        if e.get("source") != endpoint_id or e.get("origin") != "trace" or e.get("target") not in by_id:
            continue
        target = by_id[e["target"]]
        if kind is not None and target.get("type") != kind:
            continue
        found.append((_MARK_ORDER[read_mark(e)], int(e.get("step", -1)), target["label"]))
    names: list[str] = []
    for _, _, label in sorted(found):
        if label not in names:
            names.append(label)
    return names


# ── 끝점 (11c 커밋 5) ──────────────────────────────────────────────────────────
# 사람이 적지 않는다. 우리 `rest.entries`의 path와 api 레포의 라우트 선언에서 만든다. 끝점에서
# 자원으로 가는 reads 엣지는 여기서 만들지 않는다 — 핸들러를 따라가는 것은 11b 추적기의 일이다.

# `git grep -e` 기본 정규식(BRE)이라 `|`를 안 쓴다 — 패턴마다 한 줄. `(`는 BRE에서 리터럴이다.
ROUTE_PATTERNS = ("APIRouter(", "include_router(",
                  "@[A-Za-z_.]*\\.get(", "@[A-Za-z_.]*\\.post(", "@[A-Za-z_.]*\\.put(",
                  "@[A-Za-z_.]*\\.patch(", "@[A-Za-z_.]*\\.delete(", "@[A-Za-z_.]*\\.api_route(")
_DECORATOR = re.compile(r"@([A-Za-z_][\w.]*)\.(get|post|put|patch|delete|api_route)\(\s*['\"]([^'\"]*)['\"]")
_ROUTER_DEF = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*APIRouter\(")
_PREFIX = re.compile(r"prefix\s*=\s*['\"]([^'\"]*)['\"]")
_INCLUDE = re.compile(r"include_router\(\s*([A-Za-z_][\w.]*)")


@dataclass(frozen=True)
class Route:
    repo: str
    method: str
    path: str
    file: str
    line: int
    confidence: str      # EXTRACTED: prefix를 다 이었다 / INFERRED: 앱 조립부의 prefix 한 겹을 못 이었을 수 있다
    text: str


def _join_path(*parts: str) -> str:
    body = "/".join(p.strip("/") for p in parts if p and p.strip("/"))
    return "/" + body if body else "/"


def routes_from_hits(hits: Iterable[Hit]) -> list[Route]:
    """라우트 선언 줄들에서 끝점 경로를 조립한다 — 사내 FastAPI 모양(확인됨).

    같은 파일의 `router = APIRouter(prefix=…)`에 데코레이터의 꼬리를 붙이고, 앱 조립부의
    `include_router(mod.router, prefix=…)`가 **모듈 이름 = 파일 이름**으로 이어지면 한 겹 더 붙인다
    (EXTRACTED). 레포에 include_router가 있는데 이 파일로 못 이었으면 INFERRED — prefix 한 겹이 빠졌을
    수 있어 `add_endpoints`가 등재 path의 꼬리로 다시 맞춘다. 파일에 APIRouter가 없으면 앱에 직접 단
    것(`@app.get`)이라 그대로다. 문서·테스트·주석 줄은 뺀다(`_is_noise`).
    """
    routers: dict[tuple[str, str], dict[str, str]] = {}
    includes: dict[str, list[tuple[str, str]]] = {}
    decorators: list[tuple[Hit, str, str, str]] = []
    for hit in sorted(hits, key=lambda h: (h.repo, h.file, h.line)):
        if _is_noise(hit.file, hit.text):
            continue
        m = _ROUTER_DEF.search(hit.text)
        if m:
            pm = _PREFIX.search(hit.text)
            routers.setdefault((hit.repo, hit.file), {})[m.group(1)] = pm.group(1) if pm else ""
            continue
        m = _INCLUDE.search(hit.text)
        if m:
            expr = m.group(1)
            module = expr.rsplit(".", 1)[0].rsplit(".", 1)[-1] if "." in expr else expr.removesuffix("_router")
            pm = _PREFIX.search(hit.text)
            includes.setdefault(hit.repo, []).append((module, pm.group(1) if pm else ""))
            continue
        for m in _DECORATOR.finditer(hit.text):
            decorators.append((hit, m.group(1), m.group(2), m.group(3)))
    out: list[Route] = []
    seen: set[tuple[str, str, str]] = set()
    for hit, var, verb, tail in decorators:
        local = routers.get((hit.repo, hit.file), {})
        if var in local:
            prefix, on_router = local[var], True
        elif len(local) == 1:
            prefix, on_router = next(iter(local.values())), True
        else:
            prefix, on_router = "", False
        confidence = "EXTRACTED"
        if on_router:
            outer = [p for mod, p in includes.get(hit.repo, []) if mod == PurePosixPath(hit.file).stem]
            if outer:
                prefix = _join_path(outer[0], prefix)
            elif includes.get(hit.repo):
                confidence = "INFERRED"
        method = "ANY" if verb == "api_route" else verb.upper()
        path = _join_path(prefix, tail)
        key = (hit.repo, method, path)
        if key in seen:
            continue
        seen.add(key)
        out.append(Route(hit.repo, method, path, hit.file, hit.line, confidence, hit.text.strip()))
    return out


def add_endpoints(graph: dict, *, routes: Iterable[Route], entries: dict[str, tuple[str, str]]) -> dict:
    """끝점 노드와 `serves` 엣지를 오버레이에 더한 새 그래프.

    `entries`는 우리가 부를 수 있는 등재 항목 `{이름: (method, path)}` — 코드에서 못 찾아도 노드는
    선다(등재가 곧 존재의 증거) 단 serves 엣지가 없다. 코드 라우트는 그 레포에 서비스가 하나면 그
    서비스가, 여럿이면 레포가 serves한다(코드 엣지와 같은 규칙, `runs` 엣지로 안다). prefix를 못 이은
    라우트(INFERRED)는 등재 path의 꼬리와 정확히 하나만 같을 때 그 항목에 붙는다.
    """
    nodes = {n["id"]: dict(n) for n in graph["nodes"]}
    links = list(graph["links"])
    runs: dict[str, list[str]] = {}
    for e in links:
        if e["relation"] == _BRIDGE:
            runs.setdefault(e["target"], []).append(e["source"])

    def put(path: str, **extra) -> str:
        nid = _node_id("endpoint", path)
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": path, "type": "endpoint",
                          "source_file": extra.pop("source_file", ""),
                          "source_location": extra.pop("source_location", "L0"), **extra}
        else:
            for k, v in extra.items():
                nodes[nid].setdefault(k, v)
        return nid

    by_path = {path: name for name, (_, path) in entries.items()}
    for name, (method, path) in sorted(entries.items()):
        put(path, method=method, entry=name, declared="entries")
    for r in sorted(routes, key=lambda r: (r.repo, r.file, r.line, r.method, r.path)):
        target = r.path
        if r.path not in by_path and r.confidence == "INFERRED":
            tails = [p for p in by_path if p.endswith(r.path) and p != r.path]
            if len(tails) == 1:
                target = tails[0]
        nid = put(target, method=r.method, source_file=r.file, source_location=f"L{r.line}")
        repo_id = f"repo_{_slug(r.repo)}"
        services = sorted(runs.get(repo_id, []))
        source, attributed = (services[0], "service") if len(services) == 1 else (repo_id, "repo")
        if source not in nodes:
            continue                                  # 토폴로지에 없는 레포 — 걸 데가 없다
        links.append({"source": source, "target": nid, "relation": "serves",
                      "confidence": r.confidence, "attributed": attributed, "origin": "code",
                      "source_file": r.file, "source_location": f"L{r.line}", "repo": r.repo,
                      "commit": "", "text": r.text})
    return {**graph, "nodes": list(nodes.values()), "links": links}


def serving_services(graph: dict, path: str) -> list[str]:
    """그 끝점을 serves하는 **서비스** 이름들(레포는 아니다) — 브리핑 예시의 `code.grep`이 service를 채운다."""
    by_id = {n["id"]: n for n in graph["nodes"]}
    targets = {n["id"] for n in graph["nodes"] if n.get("type") == "endpoint" and n["label"] == path}
    return sorted({by_id[e["source"]]["label"] for e in graph["links"]
                   if e["relation"] == "serves" and e["target"] in targets
                   and by_id.get(e["source"], {}).get("type") == "service"})


def known_names(graph: dict | None) -> str:
    """그래프의 이름 전부, 한 줄에 하나. 엔진의 "찾지 않고 이름을 댔다" 검사가 증거와 합쳐
    본다 — 그래프에 있는 이름은 지어낸 것이 아니다. None이면 빈 문자열이라 검사가 예전과 같다."""
    if not graph:
        return ""
    return "\n".join(sorted({n["label"] for n in graph.get("nodes", []) if n.get("label")}))
