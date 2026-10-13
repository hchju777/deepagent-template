"""접수 끝점의 대상 행 → 대상 키 (R3-0 ①·④) — **선언 없이, 행 자신의 값으로, 정확히 하나일 때만.**

왜 있는가: 사내 측정 #5에서 흐름 줄의 키 순서를 증상 단어의 유사성(`briefing.case_words`, R2-2b-2)으로 정했더니 이름이
비슷한 다른 배지의 키가 첫 자리에 왔고, 리드가 그 키를 읽어 `None`을 유실의 증거로 삼아 high로 틀렸다. 순서는 리드가 읽을 수
없는 신호이고 증상 단어는 근거가 아니다. 여기서는 **대상 행 자신의 값**(링크·이름 같은 문자열 필드의 영숫자 토큰)이 접수
끝점이 읽는 선언 키 이름에 드는지를 보고, **정확히 하나의 키에만** 들 때 그 키를 골라 케이스 블록에 **문장으로** 적는다.

처음엔 토폴로지에 `intake: {field, token}`을 선언하게 했다(①). 10-13에 뺐다 — 끝점마다 사람이 적어야 하는 선언은 단기엔 되지만
장기 유지보수의 짐이고, 측정 뒤 정 안 되면 그때 더하는 것이 맞는 순서다. 선언이 없는 대가는 추정이 더 자주 실패하는 것이고,
그때는 틀린 키를 집지 않고 "못 골랐다"로 멈춘다(②가 확신을 medium으로 묶는다).

토큰이 키 이름에 드는지는 부분 문자열 대조다 — 상대가 증상 문장이 아니라 그 행의 값이고, 유일할 때만 고른다는 것이 #5의
실패와 다른 점이다. R3-1의 그래프 사슬(끝점 → 읽는 키 → config 자리 → 접두)이 이것을 좁힌다.
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


_TOKEN = re.compile(r"[A-Za-z0-9]+")
_MIN_TOKEN = 3


@dataclass(frozen=True)
class TargetKey:
    token: str | None = None
    where: str = ""                 # 어느 증거의 어느 행 — `t-1.e1 response[15]`
    keys: tuple[str, ...] = ()      # 행의 값이 든 선언 키들(하나면 고른 것)
    problem: str = ""               # 못 고른 이유(비면 고른 것이거나 아직 볼 것이 없다)
    field: str = ""                 # 토큰이 나온 행의 필드

    @property
    def key(self) -> str | None:
        return self.keys[0] if len(self.keys) == 1 else None

    @property
    def prefer(self) -> tuple[str, ...]:
        """흐름 줄이 앞세울 단어 — 고른 키의 토큰뿐. 못 골랐으면 아무것도 앞세우지 않는다(증상 단어는 다시 쓰지 않는다)."""
        return (self.token,) if self.key and self.token else ()

    def line(self) -> str:
        if self.key:
            return f"대상 키: {self.key} (대상 행 {self.where}의 {self.field} 값 토큰 '{self.token}'가 이 키에만 든다)"
        return f"대상 키: 못 골랐다 — {self.problem}" if self.problem else ""


def row_tokens(row: dict) -> list[tuple[str, str]]:
    """대상 행의 문자열 값에서 뽑은 `(필드, 토큰)` — 영숫자 덩어리, 3자 이상, 숫자만인 것은 뺀다(시각·건수는 이름이 아니다).
    대소문자는 그대로 — `Alarm`은 `alarm:…`에 안 든다(식별 값이 우연히 여러 키에 드는 것을 막는다)."""
    out: list[tuple[str, str]] = []
    for field, value in row.items():
        if not isinstance(value, str):
            continue
        for tok in _TOKEN.findall(value):
            if len(tok) >= _MIN_TOKEN and not tok.isdigit() and (field, tok) not in out:
                out.append((field, tok))
    return out


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


def map_target(state: CaseState, *, declared_keys: tuple[str, ...]) -> TargetKey:
    """`declared_keys`는 접수 끝점이 읽는 키 템플릿(`flow.declared_keys`). 행의 값 토큰이 **정확히 하나의 키에만** 들 때 고른다."""
    if not declared_keys:
        return TargetKey()                      # 고를 재료가 없다(흐름 그래프 없음) — 흐름 블록이 이미 "없음"을 말한다
    found = target_row(state)
    if found is None:
        return TargetKey()                      # 접수 응답을 아직 안 봤다(frame) — R3-1이 frame 전에 읽는다
    where, row = found
    if row is None:
        return TargetKey(where=where, problem=f"대상 행 {where}이 예산에서 잘려 값을 못 읽었다")
    tokens = row_tokens(row)
    hits = {key: [(f, t) for f, t in tokens if t in key] for key in declared_keys}
    hits = {key: hit for key, hit in hits.items() if hit}
    if len(hits) == 1:
        (key, hit), = hits.items()
        field, token = max(hit, key=lambda ft: len(ft[1]))                 # 가장 긴 토큰이 가장 구체적이다
        return TargetKey(token=token, where=where, keys=(key,), field=field)
    if not hits:
        return TargetKey(where=where, problem=f"대상 행 {where}의 값 중 선언 키 이름에 든 것이 없다 (선언 키: {', '.join(declared_keys)})")
    shown = "; ".join(f"{key} ← {', '.join(sorted({t for _, t in hit}))}" for key, hit in hits.items())
    return TargetKey(where=where, keys=tuple(hits),
                     problem=f"대상 행 {where}의 값이 선언 키 {len(hits)}개에 든다 — 하나로 못 좁힌다: {shown}")


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
