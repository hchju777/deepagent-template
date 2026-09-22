"""그래프 번들 — graphify 심볼 그래프 + 우리 흐름 오버레이를 **커밋에 박아** 둔다(11c).

`code sync`가 만들고 `code status`가 대조한다. 훅은 없다 — 배포 커밋이 바뀌면 sync가
다시 만든다. graphify가 없으면 오버레이만 만든다(조사는 돈다, 심볼 그래프만 없다).

graphify는 `extract --code-only`와 `cluster-only --no-label`만 부른다. 의미 패스와 LLM
라벨링은 **절대 안 부른다** — 대상 소스가 게이트웨이로 나가고, 여기서 실험했을 때
`cluster-only`가 라벨을 지으려고 4만 토큰을 썼다.

git에 안 들어간다. 실제 토픽·컬렉션 이름이 든다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from src.knowledge.flow import Hit

_GREP_LINE = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+):(?P<text>.*)$")
GRAPHIFY_TIMEOUT_S = 600


@dataclass(frozen=True)
class GraphMeta:
    gbm: str
    fct: str
    commits: dict[str, str]          # repo → 실제 SHA
    built_at: str
    graphify: str                    # "0.9.65" 같은 버전, 또는 "없음 — 사유"
    notes: list[str] = field(default_factory=list)


def parse_grep(repo: str, commit: str, text: str) -> list[Hit]:
    """`git grep -n` 출력(`path:line:text`) → `Hit`. `# repo @ sha` 머리줄과 빈 줄은 건너뛴다."""
    hits = []
    prefix = f"{commit}:"
    for raw in text.splitlines():
        # `git grep -n <pattern> <commit>`은 줄마다 `<commit>:`을 앞에 붙인다.
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
        m = _GREP_LINE.match(raw)
        if m is None:
            continue
        hits.append(Hit(repo, commit, m.group("file"), int(m.group("line")), m.group("text")))
    return hits


def find_graphify() -> str | None:
    """`GRAPHIFY_BIN`이 있으면 그것, 아니면 PATH. 없으면 None — 오류가 아니다."""
    forced = os.environ.get("GRAPHIFY_BIN", "").strip()
    if forced:
        return forced if Path(forced).exists() else None
    return shutil.which("graphify")


def run_graphify(repo_dir: Path, binary: str | None) -> tuple[str, str, Path | None]:
    """`(상태, 설명, graph.json 경로)`. 상태는 `ok` / `skipped` / `failed`. 던지지 않는다."""
    if not binary:
        return "skipped", "graphify가 없다 — 오버레이만 만든다 (GRAPHIFY_BIN 또는 PATH)", None
    out = repo_dir / "graphify-out" / "graph.json"
    for args in (["extract", ".", "--code-only"], ["cluster-only", ".", "--no-label"]):
        try:
            done = subprocess.run([binary, *args], cwd=str(repo_dir), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  timeout=GRAPHIFY_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "failed", f"graphify {args[0]}: {type(exc).__name__}: {exc}", None
        if done.returncode != 0:
            tail = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["(출력 없음)"]
            return "failed", f"graphify {args[0]} 종료코드 {done.returncode} — {tail[0]}", None
    if not out.exists():
        return "failed", f"graphify가 {out}을 안 만들었다", None
    return "ok", str(out), out


def run_graphify_at(repo_dir: Path, sha: str, binary: str | None, scratch: Path
                    ) -> tuple[str, str, dict | None]:
    """**배포 커밋의** 코드로 graphify를 돌린다 — 작업 트리가 아니라.

    작업 트리는 `main` 최신일 수 있고, 그래프는 배포된 것이어야 한다. `git worktree`를
    잠깐 만들어 거기서 돌리고 지운다. 체크아웃의 HEAD는 건드리지 않는다.
    `(상태, 설명, graph.json 내용)`. 던지지 않는다.
    """
    if not binary:
        return "skipped", "graphify가 없다 — 오버레이만 만든다 (GRAPHIFY_BIN 또는 PATH)", None
    scratch.parent.mkdir(parents=True, exist_ok=True)
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    try:
        made = subprocess.run(["git", "-C", str(repo_dir), "worktree", "add", "--detach",
                               str(scratch), sha], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "failed", f"worktree: {type(exc).__name__}: {exc}", None
    if made.returncode != 0:
        return "failed", f"worktree add 실패 — {(made.stderr or made.stdout).strip()[-200:]}", None
    try:
        status, detail, path = run_graphify(scratch, binary)
        if status != "ok" or path is None:
            return status, detail, None
        try:
            return "ok", detail, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return "failed", f"graph.json을 못 읽었다 — {exc}", None
    finally:
        subprocess.run(["git", "-C", str(repo_dir), "worktree", "remove", "--force", str(scratch)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120)
        shutil.rmtree(scratch, ignore_errors=True)


def graphify_version(binary: str | None) -> str:
    if not binary:
        return "없음"
    try:
        done = subprocess.run([binary, "--version"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30)
        return (done.stdout or done.stderr).strip().split()[-1] or "?"
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return "?"


def merge_graphs(overlay: dict, symbol_graphs: list[dict]) -> dict:
    """노드는 id로 합치고(먼저 온 것이 이긴다), 엣지는 이어 붙인다. graphify 노드 id
    (`dt_core_sink_writer_run`)와 우리 id(`service_sink`)는 모양이 달라 안 겹친다."""
    nodes: dict[str, dict] = {}
    links: list[dict] = []
    for g in [overlay, *symbol_graphs]:
        for n in g.get("nodes", []):
            nodes.setdefault(n["id"], n)
        links.extend(g.get("links", []))
    return {"directed": True, "multigraph": True,
            "graph": {"kind": "ops-flow+graphify", "parts": 1 + len(symbol_graphs)},
            "nodes": list(nodes.values()), "links": links, "hyperedges": []}


def write_bundle(out_dir: Path, *, overlay: dict, merged: dict, meta: GraphMeta) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "overlay.json").write_text(json.dumps(overlay, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "graph.json").write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "meta.json").write_text(json.dumps(asdict(meta), ensure_ascii=False, indent=1), encoding="utf-8")


def read_bundle(out_dir: Path) -> tuple[dict, GraphMeta] | None:
    """`(graph, meta)` 또는 None(없거나 깨짐). 던지지 않는다."""
    try:
        graph = json.loads((out_dir / "graph.json").read_text(encoding="utf-8"))
        meta = GraphMeta(**json.loads((out_dir / "meta.json").read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None
    return graph, meta


def check_bundle(meta: GraphMeta, commits: dict[str, str]) -> list[str]:
    """그래프가 **지금 배포 커밋**의 것인가. 다르면 낡은 것이고, 브리핑은 싣지 않는다."""
    problems = []
    for repo, sha in sorted(commits.items()):
        have = meta.commits.get(repo)
        if have is None:
            problems.append(f"{repo}: 그래프에 이 레포가 없다")
        elif have != sha:
            problems.append(f"{repo}: 그래프는 {have[:12]}, 배포는 {sha[:12]} — `code sync`로 다시 만든다")
    return problems


def bundle_dir(output_dir: Path, gbm: str, fct: str) -> Path:
    return Path(output_dir) / "graph" / f"{gbm}-{fct}"


def now_text(clock) -> str:
    when = clock()
    return when.isoformat(timespec="seconds") if isinstance(when, datetime) else str(when)
