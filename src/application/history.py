"""과거 종결 케이스 검색 — 벡터 없이 결정론 tier로(계획 15/P8, 방향 문서 §4.5).

포트가 아니라 평범한 함수인 이유: 두 기존 포트(케이스 저장소·판정 스냅샷) 위의 순수
조합이고, `upstream_slice`·`evidence_refs_for_case`가 이미 그 모양이다.

판정 재료를 `store.get_verdict`가 아니라 `VerdictSnapshot`에서 읽는 이유: retention이
90일에 Verdict를 지우므로, Store를 보면 이력이 시간이 지나며 조용히 비어 간다. 스냅샷은
그보다 오래 산다.

**절대 규율**: 결과에도 렌더에도 evidence id가 없다. 과거 증거도 `ev-2` 형태이고 이번
케이스에도 `ev-2`가 있어, 리드가 과거 id를 인용하면 verify의 인용 우주(state.evidence)를
그대로 통과한다 — 결정론 가드레일이 무력화된다.
"""
import re

from src.application.briefing import upstream_slice
from src.config.schema_app import StrictModel
from src.domain.case import HistoryHit


class HistoryRead(StrictModel):
    """이력 조회의 결과 — 실패는 raise가 아니라 error다(규율 1).

    부분 결과는 유지한다: tier는 강한 순으로 걷고 실패는 뒤쪽 tier에서 나므로, 부분
    결과는 정답의 **접두사**이지 오염이 아니다. 다만 부분성이 보여야 한다 — 안 보이면
    리드가 그것을 전부로 읽는다(조용한 생략).
    """
    hits: list[HistoryHit] = []
    error: str | None = None

_MAX_ERROR_CHARS = 160    # 검증 오류 하나가 [유사 이력] 블록을 차지하지 않게

_TIER_REASON = {
    1: "같은 점검이 같은 대상에서 전에도",
    2: "다른 점검이 같은 대상을",
    3: "같은 대상이 다른 공장에서",
    4: "상류에서 전에",
}


def _upstream_locators(topology, locator: str) -> list[str]:
    """이번 대상의 상류 locator들 — tier 4의 후보. 자기 자신은 뺀다(그건 tier 2·3이다)."""
    sliced = upstream_slice(topology, locator)
    found = set(sliced.derivations)
    for deriv in sliced.derivations.values():
        found.update(ref.locator for ref in deriv.inputs)
    found.discard(locator)
    return sorted(found)


def _usable(record, snapshots) -> HistoryHit | None:
    """이력으로 쓸 만한가 — 아니면 None.

    degraded 판정과 요약 없는 케이스는 워커 실패로 닫힌 케이스의 잔해다. 그걸 이력으로
    먹이면 리드가 남의 실패를 이번 조사의 단서로 읽는다(순수 잡음).
    """
    if not record.verdict_summary:
        return None
    snapshot = snapshots.get(record.id) if snapshots is not None else None
    verdict_type = snapshot.verdict_type if snapshot is not None else None
    if verdict_type in ("degraded", None):
        return None
    return HistoryHit(case_id=record.id, tier=0, reason="",
                      verdict_type=verdict_type,
                      component=snapshot.root_cause_component,
                      summary=record.verdict_summary)


def find_history(record, *, repo, snapshots, topology, limit: int = 3) -> list[HistoryHit]:
    """tier 1→4 순으로 걸으며 최신순 K건에서 멈춘다. 절대 raise하지 않는다.

    같은 케이스가 여러 tier에 걸리면 **가장 낮은 tier로 한 번만** 담는다 — 같은 케이스가
    두 줄로 보이면 리드가 그 케이스를 두 배로 신뢰한다.
    """
    return read_history(record, repo=repo, snapshots=snapshots, topology=topology,
                        limit=limit).hits


def read_history(record, *, repo, snapshots, topology, limit: int = 3) -> HistoryRead:
    """find_history와 같되 실패 사유까지 돌려준다 — 브리핑이 "못 읽었다"를 말할 수 있게."""
    hits: list[HistoryHit] = []
    seen = {record.id}
    try:
        for tier, records in _candidates(record, repo=repo, topology=topology):
            for candidate in records:
                if len(hits) >= limit:
                    return HistoryRead(hits=hits)
                if candidate.id in seen:
                    continue
                seen.add(candidate.id)
                hit = _usable(candidate, snapshots)
                if hit is None:
                    continue
                hits.append(hit.model_copy(update={"tier": tier, "reason": _TIER_REASON[tier]}))
            # **다음 tier를 요청하기 전에** 확인한다. `_candidates`는 제너레이터라 다음
            # 항목을 요청하는 순간 그 tier의 저장소 질의가 이미 돈다 — 안쪽 루프의 검사만
            # 두면 K를 정확히 채웠을 때 한 tier를 헛되이 더 묻는다.
            if len(hits) >= limit:
                return HistoryRead(hits=hits)
    except Exception as exc:                                       # noqa: BLE001 — 무raise
        return HistoryRead(hits=hits[:limit], error=f"{type(exc).__name__}: {exc}")
    return HistoryRead(hits=hits[:limit])


def _candidates(record, *, repo, topology):
    """(tier, 후보 레코드들) 순서열. 저장소 호출은 tier가 실제로 필요할 때만 일어난다."""
    yield 1, repo.closed_by_fingerprint(record.fingerprint, exclude_case_id=record.id)
    locator = record.target_locator
    if not locator:
        return                       # 대상이 없으면 tier 2~4의 재료가 없다
    # tier 2와 3을 **따로** 질의한다. 한 질의를 나눠 쓰면 저장소 절단(기본 20건)이
    # 분리보다 **먼저** 일어나, 같은 locator의 최신 20건이 전부 다른 사이트일 때 더 강한
    # 신호인 tier 2가 조용히 0건이 되고 K를 tier 3이 채운다(사다리 역전, 계획 22).
    # 이 함수가 제너레이터라 tier 2가 K를 채우면 tier 3 질의는 아예 일어나지 않는다.
    site = (record.gbm, record.fct)
    yield 2, repo.closed_by_locators([locator], exclude_case_id=record.id, site=site)
    yield 3, repo.closed_by_locators([locator], exclude_case_id=record.id,
                                     exclude_site=site)
    yield 4, repo.closed_by_locators(_upstream_locators(topology, locator),
                                     exclude_case_id=record.id)


def render_history(hits: list[HistoryHit], *, error: str | None = None) -> str:
    """브리핑의 `[유사 이력]` 자리에 들어갈 문자열. evidence id는 나가지 않는다.

    tier 사유를 행마다 싣는다 — 없으면 리드가 tier 4(상류에서 전에)를 tier 1(같은 점검이
    같은 대상에서)처럼 과신하고, 스펙 §3.2가 이력을 사다리 최하위에 둔 이유가 무력해진다.
    """
    lines = []
    for hit in hits:
        cause = hit.component or "원인 미상"
        # 세척은 **조립된 줄 전체**에 건다. 필드별로 걸면 언젠가 새 필드가 빠진다 —
        # 실제로 그랬다(검증 리뷰 B1: component는 LLM이 쓴 자유 문자열이 스냅샷을 거쳐
        # 온 것이라 `plan-sync (ev-2 참조)`가 그대로 리드 프롬프트에 실렸다).
        line = (f"- {hit.case_id}: {hit.verdict_type or '판정 미상'} / {cause}"
                f" (tier {hit.tier} — {hit.reason}) {hit.summary or ''}")
        lines.append(_clean_line(line))
    if error:
        # 오류 줄도 같은 처리를 받는다 — pydantic ValidationError는 input_value를 그대로
        # 싣고 `root_cause_component`가 정확히 그 자리다(재검증 N1). 길이도 자른다:
        # 네 줄짜리 검증 오류가 [유사 이력] 블록을 통째로 차지하면 안 된다.
        lines.append(_clean_line(f"- (이력 조회 실패: {error[:_MAX_ERROR_CHARS]}"
                                 " — 아래 목록이 전부가 아닐 수 있다)"))
    return "\n".join(lines)


def _clean_line(text: str) -> str:
    """한 줄로 접고 evidence id를 지운다.

    개행을 접는 이유: 과거 케이스의 `component`·`summary`는 LLM이 쓴 자유 문자열이라
    개행이 들어올 수 있고, 그러면 조립된 줄이 쪼개져 브리핑의 [유사 이력] 블록에 **새
    항목처럼** 붙는다 — 리드가 tier 4 케이스의 문자열을 tier 1 매칭으로 읽는다(재검증 N2).
    id가 안 새더라도 구조가 새는 것은 같은 문제다(규율 3·6).
    """
    return _strip_evidence_ids(" ".join(text.split()))


def _strip_evidence_ids(text: str) -> str:
    """과거 evidence id를 지운다 — 어느 필드에서 왔든.

    모델에 필드를 안 뒀다고 안전한 게 아니다: `component`·`verdict_type`·`summary`가 전부
    LLM이 쓴 자유 문자열이고, 문자열을 통해 새는 경로가 실재한다(검증 리뷰 B1).

    끝에 \b를 쓰면 안 된다 — "ev-2와"의 "와"는 유니코드 단어 문자라 경계가 아니고,
    한국어 산문에서 id가 조사에 붙어 나오는 것이 정상이다. 대소문자를 무시하는 이유는
    리드가 브리핑의 `EV-2`를 보고 판정에 `ev-2`라 적을 수 있기 때문이다.
    """
    # 앞뒤 모두 \b를 쓰면 안 된다: 한글은 유니코드 단어 문자라 "증거ev-2"·"ev-2와" 어느
    # 쪽에도 경계가 없고, 한국어 LLM 산문은 명사 뒤 라틴 토큰의 공백을 자주 생략한다.
    # 앞쪽은 라틴·숫자만 부정 후방탐색해 `rev-2`·`preview-3` 오탐을 피한다.
    return re.sub(r"(?i)(?<![A-Za-z0-9])ev-\d+", "(증거 생략)", text)
