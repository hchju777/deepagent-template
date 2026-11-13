"""끝점 추적 결과의 요약 — **사내에서 한 줄로 돌리고 몇 줄만 받아 적기 위한 것.**

사내 결과는 사람이 손으로 옮긴다(에어갭). 진단이 필요할 때마다 열 줄짜리 스니펫을 불러 주는 것은
옮기는 쪽이 감당 못 한다 — 여기 한 번 넣어 두고 `python tools/trace_stats.py`만 치게 한다.
읽는 것은 `code graph`가 쓴 overlay.json뿐이고, 이름(끝점·클래스·메서드)은 사내 실물이 찍히므로
출력은 채팅에만 옮기고 리포에는 안 적는다.

    .venv/bin/python tools/trace_stats.py                # output/graph/*/overlay.json
    .venv/bin/python tools/trace_stats.py path/to/overlay.json
"""
import collections
import glob
import json
import re
import sys
from pathlib import Path


def _why(gap: str) -> str:
    """`파일:L줄 이름: 사유` → `사유`. 숫자는 N으로 — 종류별로 묶기 위해서다."""
    text = gap.split(" ", 1)[1] if " " in gap else gap
    return re.sub(r"\d+", "N", text.split(": ", 1)[-1])


def _name(gap: str) -> str | None:
    text = gap.split(" ", 1)[1] if " " in gap else gap
    return text.split(": ", 1)[0] if ": " in text else None


def _owner(step: str) -> str:
    """`파일:L줄 Cls.meth` → `Cls`, 모듈 함수면 `(모듈 함수)`."""
    qual = step.split(" ", 1)[1] if " " in step else step
    return qual.rsplit(".", 1)[0] if "." in qual else "(모듈 함수)"


def _lengths(nodes: list[dict]) -> str:
    ls = sorted(len(n.get("chain") or []) for n in nodes)
    if not ls:
        return "-"
    mid = ls[len(ls) // 2]
    top = collections.Counter(ls).most_common(3)
    return f"최소 {ls[0]} · 중간 {mid} · 최대 {ls[-1]} · 잦은 {top}"


def stats(overlay: dict) -> list[str]:
    """overlay 하나 → 사람이 옮겨 적을 줄들(여덟). 번호를 붙여 "3번 줄만" 하고 부탁할 수 있게."""
    eps = {n["id"]: n for n in overlay.get("nodes", []) if n.get("type") == "endpoint"}
    links = [e for e in overlay.get("links", []) if e.get("origin") == "trace"]
    reads = collections.Counter(e["source"] for e in links)
    traced = collections.Counter(n.get("traced") or "-" for n in eps.values())
    ok = [n for n in eps.values() if n.get("traced") == "ok"]
    blocked = [n for i, n in eps.items() if n.get("traced") == "ok" and not reads[i]]
    gaps = [w for n in ok for w in (n.get("gaps") or [])]
    kinds = collections.Counter(_why(w)[:28] for w in gaps)
    names = collections.Counter(_name(w) for w in gaps if _name(w))
    owners = collections.Counter(_owner(s) for n in ok for s in (n.get("chain") or []))
    conf = collections.Counter(f"{e.get('confidence') or '-'}/{e.get('via') or '-'}" for e in links)
    bkinds = collections.Counter(_why(w)[:28] for n in blocked for w in (n.get("gaps") or []))
    # 종류마다 원문 하나 — 4번 줄은 28자에서 잘라 "없는 것: …" 같은 뒷부분이 안 보인다.
    samples = {}
    for w in gaps:
        samples.setdefault(_why(w)[:28], w.split(" ", 1)[1] if " " in w else w)
    examples = [samples[k] for k, _ in kinds.most_common(3)]
    return [
        f"1 끝점 {len(eps)} · 추적 {dict(traced)} · 자원까지 {sum(1 for i in eps if reads[i])} · 막힘 {len(blocked)}",
        f"2 읽기 엣지 {dict(conf)}",
        f"3 사슬 길이 {_lengths(ok)}",
        f"4 gap 종류 {kinds.most_common(6)}",
        f"5 gap 이름 {names.most_common(5)}",
        f"6 사슬 클래스 {owners.most_common(8)}",
        f"7 막힌 끝점 gap {bkinds.most_common(5)} · 사슬 {_lengths(blocked)}",
        "8 gap 예시 " + (" | ".join(examples) if examples else "-"),
    ]


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else next(iter(sorted(glob.glob("output/graph/*/overlay.json"))), None)
    if path is None or not Path(path).is_file():
        print("overlay.json이 없다 — `code graph`를 먼저 돌리거나 경로를 인자로 준다")
        return 1
    overlay = json.loads(Path(path).read_text(encoding="utf-8"))
    print(f"{path}")
    for line in stats(overlay):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
