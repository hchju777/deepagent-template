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
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from src.knowledge.flow import Hit

_GREP_LINE = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+):(?P<text>.*)$")
_CONTEXT_TAIL = re.compile(r"^-(?P<line>\d+)-(?P<text>.*)$")
GRAPHIFY_TIMEOUT_S = 600


@dataclass(frozen=True)
class GraphMeta:
    gbm: str
    # 11e-2부터 번들은 GBM 단위라 빈 문자열이다. 옛 번들(사이트 단위)의 값을 읽을 수 있게 남긴다.
    fct: str
    commits: dict[str, str]          # repo → 실제 SHA
    built_at: str
    graphify: str                    # "0.9.65" 같은 버전, 또는 "없음 — 사유"
    notes: list[str] = field(default_factory=list)
    # 이 번들이 덮은 값 파일(`sites/<fct>.json`)을 쓴 사이트들. 옛 번들엔 없다 — 그러면 빈 목록.
    sites: list[str] = field(default_factory=list)


def parse_grep(repo: str, commit: str, text: str) -> list[Hit]:
    """`git grep -n [-C1]` 출력 → `Hit`. `# repo @ sha` 머리줄과 빈 줄은 건너뛴다.

    측정한 모양(git 2.43): 매치는 `path:줄:본문`, 문맥은 `path-줄-본문`, 묶음 사이는 `--`
    한 줄이고 문맥이 켜지면 파일이 바뀔 때도 `--`가 온다. 줄마다 `<commit>:`이 앞에 붙는다.
    `Hit.context`에는 **줄 번호로 앞뒤 한 줄**만 붙는다 — 묶음 전체를 붙이면 세 줄 떨어진
    동사가 이 이름의 것으로 읽힌다.
    """
    hits: list[Hit] = []
    prefix = f"{commit}:"
    group: list[str] = []
    for raw in text.splitlines() + ["--"]:
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
        if raw != "--":
            group.append(raw)
            continue
        hits.extend(_parse_group(repo, commit, group))
        group = []
    return hits


def _parse_group(repo: str, commit: str, group: list[str]) -> list[Hit]:
    matched = [m for raw in group if (m := _GREP_LINE.match(raw))]
    if not matched:
        return []
    # 문맥 줄의 `path-줄-`은 경로에도 `-`가 흔해(`mx-1.json`) 정규식으로 못 가른다.
    # 한 묶음은 한 파일이므로 매치 줄이 말한 경로를 앞에서 떼어내면 나머지는 애매하지 않다.
    file = matched[0].group("file")
    lines: dict[tuple[str, int], str] = {}
    for raw in group:
        m = _GREP_LINE.match(raw)
        if m:
            lines[(m.group("file"), int(m.group("line")))] = m.group("text")
        elif raw.startswith(file + "-"):
            c = _CONTEXT_TAIL.match(raw[len(file):])
            if c:
                lines[(file, int(c.group("line")))] = c.group("text")
    hits = []
    for m in matched:
        f, n = m.group("file"), int(m.group("line"))
        around = [lines[(f, k)] for k in (n - 1, n + 1) if (f, k) in lines]
        hits.append(Hit(repo, commit, f, n, m.group("text"), context="\n".join(around)))
    return hits


def grep_snapshot(repo: str, commit: str, files: dict[str, str], patterns, *,
                  fixed: bool = True, context: int = 0) -> list[Hit]:
    """스냅샷(경로 → 본문)에서 `git grep -n -I [-F] [-C1] <커밋>`과 **같은** `Hit`를 — 프로세스 없이.

    11e-1이 만든 모순의 답이다: 레포 본문을 메모리에 다 받아 놓고도 이름을 찾을 때는 `git grep <커밋>`을 레포당
    19번 띄워 git이 매번 전체 blob을 다시 풀게 했다(사내 5레포 231초). 결과는 git과 같아야 오버레이가 바이트까지
    같다 — 패리티 테스트가 진짜 git과 대조하는 것들: 파일 순서는 트리 순서(= 전체 경로의 바이트 순서, 디렉터리와
    submodule도 그 자리), 줄은 `\n`으로만 가르고 꼬리 `\r`은 뗀다(`parse_grep`이 `splitlines`로 떼던 것), NUL이 든
    파일은 이진으로 보고 뺀다(`-I` — git은 앞 8000바이트를 본다), 한 줄은 패턴이 여럿 맞아도 `Hit` 하나, 문맥은 줄
    번호로 앞뒤 한 줄(`context=0`이면 git 출력에 있던 **인접한 매치 줄**만 — `_parse_group`이 그렇게 읽는다).
    `fixed`면 부분 문자열, 아니면 파이썬 정규식(`flow.ROUTE_REGEXES` — git에 주는 BRE와 한 출처다).
    """
    if fixed:
        matchers = [(lambda line, p=p: p in line) for p in patterns]
    else:
        matchers = [(re.compile(p) if isinstance(p, str) else p).search for p in patterns]
    hits: list[Hit] = []
    for path in sorted(files):
        text = files[path]
        if "\x00" in text[:8000]:
            continue
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()                             # 끝 줄바꿈 뒤의 빈 조각은 줄이 아니다
        lines = [line[:-1] if line.endswith("\r") else line for line in lines]
        matched = [n for n, line in enumerate(lines, 1) if any(m(line) for m in matchers)]
        visible = set(range(1, len(lines) + 1)) if context else set(matched)
        for n in matched:
            around = [lines[k - 1] for k in (n - 1, n + 1) if k in visible]
            hits.append(Hit(repo, commit, path, n, lines[n - 1], context="\n".join(around)))
    return hits


def graphify_width(n: int) -> int:
    """동시에 돌릴 레포 수 — 코드가 정한다(규율 6). CPU 수와 4 중 작은 쪽. graphify는 CPU를 먹는 파싱이라 코어보다
    많이 띄우면 느려질 뿐이고, 4는 사내 PC(백신이 프로세스마다 붙는다)를 생각한 상한이다."""
    return max(1, min(4, os.cpu_count() or 1, n))


def run_graphify_many(jobs, binary: str | None, *, width: int,
                      progress: Callable[[str, str], None] | None = None) -> list[tuple]:
    """레포마다 `run_graphify_at`을 스레드 풀에서 겹쳐 돌리고 결과는 **준 순서**로 — 끝난 순서로 합치면 같은 입력에
    다른 번들이 나온다. `jobs`는 `[(이름, 레포 경로, sha, scratch)]`. graphify는 자식 프로세스라 GIL 밖이고 레포마다
    worktree가 따로라 서로 안 건드린다. 사내 5레포 순차 296초의 답."""
    from concurrent.futures import ThreadPoolExecutor

    def one(job):
        name, repo_dir, sha, scratch = job
        return run_graphify_at(repo_dir, sha, binary, scratch,
                               progress=(lambda m, r=name: progress(r, m)) if progress else None)

    with ThreadPoolExecutor(max_workers=max(1, width)) as pool:
        return list(pool.map(one, jobs))


def find_graphify() -> str | None:
    """`GRAPHIFY_BIN` → 실행 중인 python 옆(`.venv/Scripts`·`bin`) → PATH. 없으면 None — 오류가 아니다.

    python 옆을 PATH보다 먼저 보는 이유: `requirements-graph.txt`로 venv에 넣은 것이 우리가
    고정한 버전이고, activate 없이 `python -m src`를 돌리면 PATH에는 그게 없다(사내). 설치만
    하면 CLI와 pytest가 같은 것을 찾아야 한다.
    """
    forced = os.environ.get("GRAPHIFY_BIN", "").strip()
    if forced:
        return forced if Path(forced).exists() else None
    beside = Path(sys.executable).parent / ("graphify.exe" if os.name == "nt" else "graphify")
    if beside.exists():
        return str(beside)
    return shutil.which("graphify")


def run_graphify(repo_dir: Path, binary: str | None, *,
                 progress: Callable[[str], None] | None = None) -> tuple[str, str, Path | None]:
    """`(상태, 설명, graph.json 경로)`. 상태는 `ok` / `skipped` / `failed`. 던지지 않는다.

    `progress`는 단계마다 한 줄 — 큰 레포는 파싱이 분 단위고 타임아웃이 10분이라, 말없이
    기다리게 두면 사람은 멈춘 줄 안다(사내에서 그랬다).
    """
    if not binary:
        return "skipped", "graphify가 없다 — 오버레이만 만든다 (GRAPHIFY_BIN 또는 PATH)", None
    out = repo_dir / "graphify-out" / "graph.json"
    for args in (["extract", ".", "--code-only"], ["cluster-only", ".", "--no-label"]):
        if progress:
            progress(f"graphify {args[0]} 중 (최대 {GRAPHIFY_TIMEOUT_S // 60}분)")
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


def run_graphify_at(repo_dir: Path, sha: str, binary: str | None, scratch: Path, *,
                    progress: Callable[[str], None] | None = None
                    ) -> tuple[str, str, dict | None, str | None]:
    """**배포 커밋의** 코드로 graphify를 돌린다 — 작업 트리가 아니라.

    작업 트리는 `main` 최신일 수 있고, 그래프는 배포된 것이어야 한다. `git worktree`를
    잠깐 만들어 거기서 돌리고 지운다. 체크아웃의 HEAD는 건드리지 않는다.
    `(상태, 설명, graph.json 내용, GRAPH_REPORT.md 원문)`. 던지지 않는다.
    """
    if not binary:
        return "skipped", "graphify가 없다 — 오버레이만 만든다 (GRAPHIFY_BIN 또는 PATH)", None, None
    # 절대 경로로 바꾼다. `git -C <레포>`는 상대 경로를 **레포 기준**으로 풀어서, `output`
    # 같은 상대 output_dir이면 worktree가 대상 레포 안에 생기고 우리는 없는 자리에서
    # graphify를 돌리게 된다(사내 첫 실행: WinError 267, 리눅스: FileNotFoundError).
    repo_dir, scratch = Path(os.path.abspath(repo_dir)), Path(os.path.abspath(scratch))
    scratch.parent.mkdir(parents=True, exist_ok=True)
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    if progress:
        progress(f"worktree 만드는 중 @ {sha[:12]}")
    try:
        made = subprocess.run(["git", "-C", str(repo_dir), "worktree", "add", "--detach",
                               str(scratch), sha], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "failed", f"worktree: {type(exc).__name__}: {exc}", None, None
    if made.returncode != 0:
        return "failed", f"worktree add 실패 — {(made.stderr or made.stdout).strip()[-200:]}", None, None
    try:
        status, detail, path = run_graphify(scratch, binary, progress=progress)
        if status != "ok" or path is None:
            return status, detail, None, None
        # 사람용 리포트(커뮤니티·연결 많은 노드·토큰 비용)는 worktree와 함께 지워지던 것이다.
        # 여기서 읽어 번들로 옮긴다 — finally가 디렉터리를 지우기 전에.
        report = _read_optional(path.parent / "GRAPH_REPORT.md")
        try:
            return "ok", detail, json.loads(path.read_text(encoding="utf-8")), report
        except (OSError, ValueError) as exc:
            return "failed", f"graph.json을 못 읽었다 — {exc}", None, report
    finally:
        subprocess.run(["git", "-C", str(repo_dir), "worktree", "remove", "--force", str(scratch)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120)
        shutil.rmtree(scratch, ignore_errors=True)


def _read_optional(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def run_wiki(binary: str | None, graph_json: Path, *,
             progress: Callable[[str], None] | None = None) -> tuple[str, str]:
    """합친 그래프의 사람용 md 묶음 — `graphify export wiki`. `(상태, 설명)`. 던지지 않는다.

    `<graph.json의 디렉터리>/wiki/`에 생긴다 — 자리는 graphify가 정한다(`--dir`이 없다).
    LLM은 안 부른다(있는 라벨만 쓴다). 사내망에서 그대로 열리는 것은 md뿐이라 html 대신 이것이다.
    """
    if not binary:
        return "skipped", "graphify가 없다"
    graph_json = Path(os.path.abspath(graph_json))
    target = graph_json.parent / "wiki"
    shutil.rmtree(target, ignore_errors=True)          # 지난 그래프의 문서가 섞이지 않게
    if progress:
        progress("wiki 만드는 중 (graphify export wiki)")
    try:
        done = subprocess.run([binary, "export", "wiki", "--graph", str(graph_json)],
                              cwd=str(graph_json.parent), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=GRAPHIFY_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "failed", f"wiki: {type(exc).__name__}: {exc}"
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["(출력 없음)"]
        return "failed", f"wiki 종료코드 {done.returncode} — {tail[0]}"
    if not (target / "index.md").exists():
        return "failed", f"graphify가 {target / 'index.md'}를 안 만들었다"
    return "ok", str(target / "index.md")


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


def write_index(out_dir: Path, index) -> None:
    """심볼 인덱스(11d) — 심볼과 엣지를 따로 둔다. 둘 다 배포 커밋의 것이고 `meta.json`의 커밋과 같이 간다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    data = index.to_dict()
    edges = data.pop("edges")
    (out_dir / "symbols.json").write_text(json.dumps({**data, "summary": index.summary()}, ensure_ascii=False),
                                          encoding="utf-8")
    (out_dir / "edges.json").write_text(json.dumps({"edges": edges}, ensure_ascii=False), encoding="utf-8")


def read_index(out_dir: Path):
    """`Index` 또는 None(없거나 깨짐). 던지지 않는다."""
    from src.knowledge.index import Index
    try:
        data = json.loads((out_dir / "symbols.json").read_text(encoding="utf-8"))
        data["edges"] = json.loads((out_dir / "edges.json").read_text(encoding="utf-8"))["edges"]
        return Index.from_dict(data)
    except (OSError, ValueError, TypeError, KeyError):
        return None


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


def bundle_dir(output_dir: Path, gbm: str) -> Path:
    """번들은 **GBM 단위**다(11e-2). 코드는 GBM 단위로 같고(decisions ②) 사이트 층은 값 몇 개를 덮을 뿐이라,
    사이트마다 만들면 같은 인덱스를 28번 만든다(사내 사이트당 20분)."""
    return Path(output_dir) / "graph" / gbm


def site_file(bundle: Path, fct: str) -> Path:
    """그 사이트 층이 GBM 값을 **덮은 것만** — `[{kind, key_path, value, services, relation}]`. 조사 시작 때
    `flow.apply_site`가 오버레이 사본에 입힌다. 보통 몇 줄이다(측정판: `group_id` 하나)."""
    return Path(bundle) / "sites" / f"{fct}.json"


def write_site(bundle: Path, fct: str, rows: list[dict]) -> None:
    path = site_file(bundle, fct)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def read_site(bundle: Path, fct: str) -> list[dict] | None:
    """없으면 None — "덮은 값이 없다"(빈 목록)와 "그 사이트 파일을 안 만들었다"는 다른 사실이다."""
    try:
        rows = json.loads(site_file(bundle, fct).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else None


def now_text(clock) -> str:
    when = clock()
    return when.isoformat(timespec="seconds") if isinstance(when, datetime) else str(when)
