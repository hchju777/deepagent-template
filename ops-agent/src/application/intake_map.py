"""접수 끝점의 대상 행 → 대상 키 (R3-0 ①).

왜 있는가: 사내 측정 #5에서 흐름 줄의 키 순서를 증상 단어의 유사성(`briefing.case_words`, R2-2b-2)으로 정했더니 이름이
비슷한 다른 배지의 키가 첫 자리에 왔고, 리드가 그 키를 읽어 `None`을 유실의 증거로 삼아 high로 틀렸다. 순서는 리드가 읽을 수
없는 신호이고 유사성은 근거가 아니다. 여기서는 **데이터 자신이 선언된 필드에 싣고 있는 식별자**(링크 토큰 — 토폴로지
`intake`)로 선언 키를 고르고, 케이스 블록에 **문장으로** 적는다.

토큰이 키 이름에 드는지도 부분 문자열 대조이긴 하다 — 다만 상대가 증상 문장이 아니라 토폴로지가 선언한 링크다. R3-1의
선언된 사슬(토큰 → api config 경로 → 키 설정 → 접두+키)이 이것을 대체한다.

**고르지 못하면 고르지 않는다** — 토큰 없음·링크 필드 없음·0개·2개 이상은 전부 그 사실 그대로 적는다. 코드가 하나를 집으면
판정이 인용하지 않은 것을 코드가 인용한 것이 된다(R2-4 ⑥과 같은 규칙).
"""
import json
import re
from dataclasses import dataclass

from src.application.runner_probe import target_rows
from src.application.state import CaseState

_ROW = re.compile(r"^\s*\[(\d+)\] (.*)$", re.M)
# 증거 `source`는 우리가 만든 `describe` 문자열(`redis.get key='…' path='…'`)이라 그 모양으로 키를 되읽는다. scan은 키 하나를 읽은 것이
# 아니라 세지 않는다.
_KEY_READ = re.compile(r"^redis\.get key='((?:[^'\\]|\\.)*)'")
_VALUE_CHARS = 60


@dataclass(frozen=True)
class TargetKey:
    token: str | None = None
    where: str = ""                 # 어느 증거의 어느 행 — `t-1.e1 response[15]`
    keys: tuple[str, ...] = ()      # 토큰이 든 선언 키들
    problem: str = ""               # 못 고른 이유(비면 고른 것이거나 아직 볼 것이 없다)

    @property
    def key(self) -> str | None:
        return self.keys[0] if len(self.keys) == 1 else None

    @property
    def prefer(self) -> tuple[str, ...]:
        """흐름 줄이 앞세울 단어 — 토큰뿐. 토큰이 없으면 아무것도 앞세우지 않는다(증상 단어는 다시 쓰지 않는다)."""
        return (self.token,) if self.token else ()

    def line(self) -> str:
        if self.key:
            return f"대상 키: {self.key} (대상 행 {self.where}의 링크 토큰 '{self.token}'에서)"
        return f"대상 키: 못 골랐다 — {self.problem}" if self.problem else ""


def target_row(state: CaseState) -> tuple[str, dict | None] | None:
    """증거가 짚은 첫 대상 행 — `(어디, 행)`. 실행기가 머리줄에 적은 경로의 `[n] …` 줄을 다시 읽는다(항목당 압축 JSON 한 줄이라
    그대로 풀린다). 예산에서 잘린 줄이면 행은 None. 대상 행을 짚은 증거가 없으면 None."""
    for ref in state.evidence:
        paths = target_rows(ref.body)
        if not paths:
            continue
        where = f"{ref.id} {paths[0]}"
        n = int(paths[0].rsplit("[", 1)[1].rstrip("]"))
        for m in _ROW.finditer(ref.body):
            if int(m.group(1)) != n:
                continue
            try:
                row = json.loads(m.group(2))
            except ValueError:
                return where, None
            return where, (row if isinstance(row, dict) else None)
        return where, None
    return None


def map_target(state: CaseState, *, link, declared_keys: tuple[str, ...]) -> TargetKey:
    """`link`는 토폴로지의 `intake`(없으면 None), `declared_keys`는 접수 끝점이 읽는 키 템플릿(`flow.declared_keys`)."""
    if link is None:
        return TargetKey()
    found = target_row(state)
    if found is None:
        return TargetKey()                      # 접수 응답을 아직 안 봤다(frame) — R3-1이 frame 전에 읽는다
    where, row = found
    if row is None:
        return TargetKey(where=where, problem=f"대상 행 {where}이 예산에서 잘려 링크 필드를 못 읽었다")
    value = row.get(link.field)
    if not isinstance(value, str):
        return TargetKey(where=where, problem=f"대상 행 {where}에 링크 필드 '{link.field}'가 없다")
    match = re.search(link.token, value)
    token = match.group(1) if match else ""
    if not token:
        shown = value if len(value) <= _VALUE_CHARS else value[:_VALUE_CHARS] + "…"
        return TargetKey(where=where, problem=f"링크 값 {shown!r}에서 토큰을 못 뽑았다 (토폴로지 intake.token)")
    keys = tuple(k for k in declared_keys if token in k)
    if len(keys) == 1:
        return TargetKey(token, where, keys)
    if not keys:
        return TargetKey(token, where, (), f"토큰 '{token}'이 든 선언 키가 없다")
    return TargetKey(token, where, keys, f"토큰 '{token}'이 든 선언 키 {len(keys)}개: {', '.join(keys)} — 하나로 못 좁힌다")


def cited_keys(evidence_ids, evidence) -> list[str]:
    """인용한 증거 중 **키 읽기**(`redis.get`)의 키들 — 인용 순서대로, 같은 키는 한 번."""
    by_id = {ref.id: ref for ref in evidence}
    found: list[str] = []
    for evidence_id in evidence_ids:
        ref = by_id.get(evidence_id)
        match = _KEY_READ.match(ref.source) if ref is not None else None
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found
