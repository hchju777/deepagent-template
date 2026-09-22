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
from typing import Callable, Iterable

from src.knowledge.schema import Service, Topology

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

    @property
    def literal(self) -> str:
        """grep에 쓸 리터럴 — 템플릿이면 `{` 앞까지."""
        return self.value.split("{", 1)[0]

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
        if isinstance(node, dict):
            for key, value in sorted(node.items()):
                if isinstance(value, str) and value:
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

    for name in sorted(set(names), key=lambda n: (n.kind, n.value, n.key_path)):
        target = _node_id(name.kind, name.value)
        for pattern in name.patterns:
            confidence = "EXTRACTED" if pattern == name.literal else "INFERRED"
            for hit in sorted(hits_for(pattern), key=lambda h: (h.repo, h.file, h.line)):
                if confidence == "INFERRED" and any(
                        tok not in hit.text for tok in name.required_tokens):
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
                    links.append({**base,
                                  "source": (f"service_{_slug(who)}" if who
                                             else f"repo_{_slug(hit.repo)}"),
                                  "relation": name.relation or "declares",
                                  "attributed": "service" if who else "repo"})
                    continue
                who, sure = owner(hit.file, hit.repo, topology)
                verb = direction(hit.text) or direction(hit.context)
                # 동사가 없는 줄(주석·바인딩)도 **버리지 않는다** — `mentions`로 남긴다.
                # 방향은 모르지만 "이 서비스가 이 이름을 안다"는 것은 사실이고, 그게
                # 리드가 어느 서비스를 볼지 고르는 데는 충분하다.
                relation = (RELATION[name.kind][verb] if verb in ("reads", "writes")
                            else "mentions")
                links.append({**base,
                              "source": (f"service_{_slug(who)}" if who
                                         else f"repo_{_slug(hit.repo)}"),
                              "relation": relation,
                              "attributed": "service" if who else "repo",
                              "confidence": _weakest(
                                  confidence, sure,
                                  "EXTRACTED" if verb in ("reads", "writes") else "AMBIGUOUS")})
    return {"directed": True, "multigraph": True, "graph": {"kind": "ops-flow"},
            "nodes": list(nodes.values()), "links": links, "hyperedges": []}


def _is_config(file: str) -> bool:
    return file.startswith("config/") or "/config/" in file or file.endswith((".json", ".yaml", ".yml", ".toml"))


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


def _step(edge: dict, node: str, *, undirected: bool) -> str | None:
    """`node`에서 이 엣지를 타고 갈 수 있으면 건너편, 아니면 None."""
    if undirected:
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
    "관계가 있기는 한가"를 물을 때만.
    """
    starts, goals = _find(graph, a), set(_find(graph, b))
    if not starts or not goals:
        return None
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
            other = _step(e, node, undirected=undirected)
            if other is not None and other not in prev:
                prev[other] = (node, e)
                queue.append(other)
    return None


def label_of(graph: dict, node_id: str) -> str:
    return next((n["label"] for n in graph["nodes"] if n["id"] == node_id), node_id)


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


def summary(graph: dict) -> dict:
    """`code status`가 찍을 숫자들."""
    links = graph["links"]
    kinds = {}
    for n in graph["nodes"]:
        kinds[n.get("type", "?")] = kinds.get(n.get("type", "?"), 0) + 1
    return {"nodes": len(graph["nodes"]), "links": len(links), "kinds": kinds,
            "repo_level": sum(1 for e in links if e.get("attributed") == "repo"),
            "ambiguous": sum(1 for e in links if e.get("confidence") == "AMBIGUOUS")}


def advise(graph: dict, topology: Topology) -> list[str]:
    """토폴로지·config를 고칠 사람에게 주는 권고. **막지 않는다** — 적기만 한다."""
    out = []
    resources = [n for n in graph["nodes"] if n.get("type") in ("topic", "group", "collection", "rediskey")]
    if not resources:
        out.append("이름을 하나도 못 뽑았다 — knowledge/topology의 flow.sources가 config 모양과 안 맞는다")
        return out
    used = {e["target"] for e in graph["links"] if e["relation"] != "declares"}
    idle = sorted(n["label"] for n in resources if n["id"] not in used)
    if idle:
        out.append(f"config에 선언됐지만 코드 어디서도 안 쓰는 이름 {len(idle)}개 — {', '.join(idle[:5])}"
                   + (" …" if len(idle) > 5 else ""))
    shared = sum(1 for e in graph["links"] if e.get("attributed") == "repo")
    if shared:
        repos = sorted({e["repo"] for e in graph["links"] if e.get("attributed") == "repo"})
        out.append(f"서비스를 못 가른 엣지 {shared}개 (공유 레포 {', '.join(repos)}) — "
                   f"토폴로지의 서비스 path를 채우면 코드 쪽은 갈린다")
    for name, svc in sorted(topology.services.items()):
        sid = f"service_{_slug(name)}"
        if not any(e["source"] == sid for e in graph["links"]):
            out.append(f"{name}: 코드에서 자원을 하나도 안 만진다 — 레포·역할 선언을 의심하라")
    return out
