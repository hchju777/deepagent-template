"""판정 턴 A/B(R3-5k ④, 측정 #21 요청) — 판정 트레이스 파일 하나의 프롬프트로, 걷기의 병합 주 줄을 넣은 판(A)과 뺀 판(B)을 판정 LLM에 번갈아 N번씩 묻고
유형·component·확신만 센다. 사내 모델은 리포 밖에서만 돌아 사람이 사내에서 돌린다 — 이 컨테이너의 측정판 LLM은 파일 대역이라 여기서 받은 답은 사내 모델의
반응이 아니다. 증거 본문·판정 서술은 찍지 않는다(`case trace`와 같은 선)."""
import re
from collections import Counter

from src.application.lead import ConcludeReply, ask_json, conclude_schema
# 걷기가 쓰는 병합 주의 두 꼴의 표식 — 걷기가 같은 표식으로 주를 쓴다(R3-5l ①부터 판정 블록도 이 표식으로 뺀다 — 이 도구는 그 전의 트레이스에만 뺄 줄이 있다).
from src.domain.case import JOIN_NOTE_MARKS, WALK_LEGEND

_NOTE = "  주: "
# 케이스 블록의 걷기 표 머리(`Walk.lines`) — 끼울 자리를 찾는 표식. 시험이 실제 걷기 출력으로 댄다.
_WALK_HEAD = "걷기(코드가 상류를 걸었다)"
_EVIDENCE_ID = re.compile(r"(?<![A-Za-z0-9_.\-])t-\d+\.e\d+(?![A-Za-z0-9_\-])")
_FENCE = "````\n"


def prompt_of(trace_text: str) -> str | None:
    """트레이스 파일(`__main__._make_tracer`)의 `## 물어본 것` 칸 — 없으면 None."""
    head = trace_text.find("## 물어본 것")
    start = trace_text.find(_FENCE, head) if head >= 0 else -1
    end = trace_text.find("\n" + _FENCE, start + len(_FENCE)) if start >= 0 else -1
    return trace_text[start + len(_FENCE):end] if end >= 0 else None


def strip_join_notes(prompt: str) -> tuple[str, list[str]]:
    """→ (병합 주 줄을 뺀 프롬프트, 뺀 줄들). 다른 주·홉 표·증거는 그대로."""
    kept, removed = [], []
    for line in prompt.split("\n"):
        (removed if line.startswith(_NOTE) and any(m in line for m in JOIN_NOTE_MARKS) else kept).append(line)
    return "\n".join(kept), removed


def join_notes_of(prompt: str) -> list[str]:
    """frame 턴 프롬프트의 병합 주 줄들 — `strip_join_notes`가 빼는 것과 같은 줄(R3-5m ③: R3-5l ① 뒤 판정 턴엔 그 줄이 없다)."""
    return strip_join_notes(prompt)[1]


def add_join_notes(prompt: str, notes: list[str]) -> str | None:
    """병합 주 줄을 판정 프롬프트의 걷기 표 아래(행·있는 주 뒤, 다음 절 앞)에 끼운다 — R3-5l ①이 뺀 자리 그대로. 걷기 표가 없으면 None(끼울 자리가 없다)."""
    lines = prompt.split("\n")
    head = next((i for i, line in enumerate(lines) if line.startswith(_WALK_HEAD)), None)
    if head is None:
        return None
    end = head + 1
    while end < len(lines) and lines[end].startswith("  "):
        end += 1
    return "\n".join([*lines[:end], *notes, *lines[end:]])


def add_legend(prompt: str) -> str | None:
    """걷기 머리 아래에 뜻 줄(`WALK_LEGEND`)을 끼운다 — 판정 블록이 두는 자리 그대로(R3-5p ②). 이미 있으면 그대로, 걷기 표가 없으면 None."""
    lines = prompt.split("\n")
    if WALK_LEGEND in lines:
        return prompt
    head = next((i for i, line in enumerate(lines) if line.startswith(_WALK_HEAD)), None)
    if head is None:
        return None
    return "\n".join([*lines[:head + 1], WALK_LEGEND, *lines[head + 1:]])


def _line(got) -> str:
    if not got.ok:
        # 사유만 — 못 읽은 답의 발췌(`… — '<답 머리>'`)는 자른다. 답에 증거 본문이 섞일 수 있다(`case trace`와 같은 선).
        reason = re.split(r"\s—\s['\"]", ((got.error or "").splitlines() or [""])[0], maxsplit=1)[0]
        return f"못 읽음 — {reason[:120]}"
    data = got.data
    cause = (data.get("root_cause") or {}).get("component") or "—"
    return f"{data['verdict_type']} · {cause} · {data['confidence']}"


async def run_ab(llm, prompt: str, *, runs: int, notes: list[str] | None = None, legend: bool = False) -> list[tuple[str, int, str]]:
    """→ `(판, 회차, 한 줄)` — A는 병합 주가 있는 판, B는 없는 판. 프롬프트에 주가 있으면 B가 그것을 뺀 것이고, `notes`를 주면(판정 턴에 주가 없는 트레이스 —
    R3-5l ① 뒤) A가 그 주를 걷기 표 아래 끼운 것이다(R3-5m ③). `legend`면 다른 비교다 — A는 지금 그대로, B는 걷기 머리 아래 뜻 줄을 끼운 것(R3-5p ②).
    회차마다 A·B를 번갈아(시간에 따른 쏠림을 나눈다). 판정 스키마는 실제 판정 턴과 같게 그 프롬프트에 보인 증거 id의 enum(R3-5i ②). 던지지 않는다(`ask_json`)."""
    if legend:
        with_notes, without = prompt, add_legend(prompt) or prompt
    else:
        with_notes, without = (add_join_notes(prompt, notes) or prompt, prompt) if notes else (prompt, strip_join_notes(prompt)[0])
    schema = conclude_schema(list(dict.fromkeys(_EVIDENCE_ID.findall(prompt))))
    rows = []
    for i in range(1, runs + 1):
        for arm, text in (("A", with_notes), ("B", without)):
            rows.append((arm, i, _line(await ask_json(llm, text, ConcludeReply, schema=schema))))
    return rows


def summary(rows, labels: tuple[str, str] = ("주 있음", "주 없음")) -> list[str]:
    """판마다 `유형 · component`별 수(많은 순)와 못 읽은 수. 라벨은 비교의 종류대로(`--legend`면 지금·뜻 줄)."""
    out = []
    for arm, label in (("A", labels[0]), ("B", labels[1])):
        lines = [line for a, _, line in rows if a == arm]
        counts = Counter(" · ".join(line.split(" · ")[:2]) for line in lines if not line.startswith("못 읽음"))
        failed = sum(1 for line in lines if line.startswith("못 읽음"))
        parts = [f"{k} {v}" for k, v in counts.most_common()] + ([f"못 읽음 {failed}"] if failed else [])
        out.append(f"{arm}({label}): " + (" / ".join(parts) or "없음"))
    return out
