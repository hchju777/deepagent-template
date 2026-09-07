"""frame의 케이스 브리핑 — 스펙 §3.6. 전체 코퍼스 덤프 금지, 유계 슬라이스만.

상류 역추적: 증상의 끝점 locator에서 derivation(무엇이 이걸 만드나)과
writes(누가 이 데이터를 쓰나)를 따라 유계 깊이로 거슬러 올라간다(§3.1).
"""
from collections import deque

from src.knowledge.topology import Topology


def upstream_slice(topology, start_locator, *, max_depth=3):
    """시작 locator에서 상류로 유계 BFS를 수행한다.

    max_depth=0은 빈 슬라이스를 반환한다 — 시작 locator 자체도 확장하지 않는다.
    """
    services, derivations = {}, {}
    queue = deque([(start_locator, 0)])
    seen = {start_locator}
    while queue:
        locator, depth = queue.popleft()
        if depth >= max_depth:
            continue
        # 이 locator가 어느 derivation의 출력인지 확인
        deriv = topology.derivations.get(locator)
        if deriv is not None:
            derivations[locator] = deriv
            # derivation을 생성하는 서비스를 포함
            if deriv.via in topology.services:
                services[deriv.via] = topology.services[deriv.via]
            # 이 derivation의 입력 locator들을 따라간다
            for ref in deriv.inputs:
                if ref.locator not in seen:
                    seen.add(ref.locator)
                    queue.append((ref.locator, depth + 1))
        # 이 locator를 writes하는 모든 서비스를 찾는다
        for name, svc in topology.services.items():
            if any(ref.locator == locator for ref in svc.writes):
                services[name] = svc
                # 이 서비스가 reads하는 locator들을 따라간다
                for read in svc.reads:
                    if read.locator not in seen:
                        seen.add(read.locator)
                        queue.append((read.locator, depth + 1))
    return Topology(services=services, derivations=derivations)


def one_line(text):
    """개행을 접는다 — `[...]` 섹션 어휘를 쓰는 프롬프트가 이것을 **공유한다**:
    브리핑(`build_briefing`·`render_rules`·`render_deployment`), 접수(`intake._prompt`·`_turn_prompt`),
    사이트 선택(`scope._prompt`), 리드의 frame·integrate·conclude(`nodes.py`의 가설 보드·태스크
    현황·질문답변 로그·재작성 요청, 그리고 `_ask_llm` 재시도가 붙이는 검증 오류 —
    `extra="forbid"`가 LLM이 고른 **키 이름**을 열 0에 찍는다), 서브에이전트 도구 반환과 태스크 goal(`subagents.py`),
    순찰 LLM 판정(`llm_judge._build_prompt`의 점검 이름·질문). 한 벌만 있어야 한다 —
    두 벌이 생기면 언젠가 한쪽만 고쳐진다(실제로 그랬다).

    **여러 줄이 정상인 내용은 애초에 날것으로 안 들어온다**: 개행을 이스케이프하는 표현을
    거친다 — 증거 요약은 `domain.case.evidence_summary`, 순찰 스냅샷은 `runner.snapshot_text`, 서브에이전트의 본문 반환은 `subagents.get_evidence`의 `json.dumps`다.
    그래서 남은 자리는 전부 "항목 하나가 한 줄"이고 접기가 정확한 방어다.

    `history._clean_line`은 evidence id 제거를 겸한 두 번째 접기 구현이다 — 언젠가 이
    함수 위에 얹을 수 있다. **그때 id 제거까지 끌고 오면 안 된다**: 이력이 지우는 것은
    **과거 케이스**의 id이고(규율 3 — 리드가 인용하면 verify의 인용 우주에 이번 케이스의
    같은 id가 실재해 통과한다), 그래프 내부 프롬프트가 다루는 id는 **이번 케이스**의
    것이라 지우면 조사가 자기 증거를 못 가리킨다.

    한 줄이 쪼개지면 그 조각이 다음 섹션 머리말처럼 보인다: 위조된 `[적용 룰]`이
    진짜보다 **먼저** 오고, 리드는 먼저 읽은 임계값을 정상 기준으로 삼는다. 가장 강한
    벡터는 `[증상]`이다 — `Finding.summary`가 `Case.symptom`이 되고, `judge="llm"`
    점검에서 그것은 LLM이 쓴 무검증 자유 문자열이다. 토폴로지의 서비스 이름·locator·
    `via`도 사람이 쓰는 YAML이라 같은 처리를 받는다(`history.py`의 `_clean_line`과
    같은 근거: id가 안 새더라도 구조가 새는 것은 같은 문제다).
    """
    return " ".join(str(text).split())


def _slice_locators(slice_, target_locator):
    """슬라이스가 다루는 locator 전부 — derivation의 출력과 입력, 그리고 케이스 대상."""
    locators = {target_locator} if target_locator else set()
    for output, deriv in slice_.derivations.items():
        locators.add(output)
        locators.update(ref.locator for ref in deriv.inputs)
    return locators


def render_rules(checks, *, slice_, target_locator):
    """브리핑의 `[적용 룰]` — 슬라이스에 걸리는 점검만.

    전부 싣지 않는 이유는 스펙 §3.6("전체 코퍼스 덤프 금지, 유계 슬라이스만")이다.
    점검이 수십 개인 사이트에서 전량을 실으면 리드가 자기 케이스와 무관한 임계값을
    "정상 기준"으로 읽는다.

    무엇이 관련 있는지는 **코드가 정한다**(규율 6) — 목록을 통째로 주고 LLM에게
    고르라고 하면 그 판단이 재현되지도, 상한이 있지도 않다.

    `rest:<이름>` 표적(등재 항목 이름)은 locator가 아니라서 케이스 locator와 문자열이
    같을 때만 걸린다. 등재 항목이 어느 locator를 만드는지는 토폴로지가 말하지 않는다.
    """
    relevant = _slice_locators(slice_, target_locator)
    lines = []
    for name in sorted(checks):
        check = checks[name]
        if check.target not in relevant:
            continue
        params = ", ".join(f"{key}={value!r}" for key, value in sorted(check.params.items()))
        # 한 줄로 접는다 — 점검 이름이나 target에 개행이 있으면 [적용 룰] 블록에 가짜
        # 항목처럼 붙는다(params 값은 `!r`가 이미 이스케이프한다).
        lines.append(one_line(
            f"- {name}: judge={check.judge}, target={check.target}, {params}"))
    return "\n".join(lines)


def render_deployment(deployment, *, slice_):
    """브리핑의 `[배포 버전]` — 슬라이스 서비스만. **"없음"은 절대 안 쓴다.**

    세 상태를 가른다. ①매핑 자체가 없다 → 사이트 전체가 미검증(슬라이스보다 먼저 답한다:
    이건 서비스에 대한 진술이 아니라 사이트에 대한 진술이라 슬라이스가 비어도 참이다).
    ②슬라이스에 서비스가 없다 → "대상 서비스 없음". ③슬라이스 서비스가 매핑에 빠져 있다
    → **그 이름을 대고** 미검증.

    하나로 뭉개면 안 되는 이유: 부재는 "배포가 없다"가 아니라 "무엇이 돌고 있는지 우리가
    모른다"이고(로컬 체크아웃의 HEAD는 배포 진실이 아니다 — knowledge/deployment.py),
    리드가 코드 증거를 그만큼 깎아 읽어야 한다. **접수가 대상을 못 정하면** ②에 걸리므로
    (`open_case`는 `target_locator=None`으로 열고 접수가 채운다), ②를 "없음"으로 찍으면
    접수 포기 경로가 통째로 뭉개진다.
    """
    if deployment is None:
        return "배포 매핑 없음 — 이 사이트의 코드 증거는 배포 버전 미검증이다"
    if not slice_.services:
        return "대상 서비스 없음 — 슬라이스가 비어 있다"
    # 나가는 줄은 **전부** 접는다. repo·commit·서비스 이름 모두 검증 없는 str이라
    # 개행 하나가 이 블록 뒤에 가짜 `[유사 이력]` 항목을 만들 수 있고, 이 블록이
    # 브리핑의 마지막이라 주입된 내용이 프롬프트의 꼬리를 차지한다(검증 리뷰 F3).
    lines = [one_line(f"- {name}: {version.repo}@{version.commit}")
             for name, version in sorted(deployment.services.items())
             if name in slice_.services]
    # 빠진 서비스를 조용히 생략하면 "없음"과 구별이 안 된다. 무엇이 미검증인지 이름을
    # 대야 리드가 "이 서비스는 조사 범위인데 배포가 없다"로 오독하지 않는다(검증 리뷰 F5).
    missing = sorted(set(slice_.services) - set(deployment.services))
    if missing:
        lines.append(one_line(f"- {', '.join(missing)}: 배포 매핑에 없다 — 배포 버전 미검증"))
    return "\n".join(lines)


def _or_none(text):
    # 텍스트가 없거나 공백이면 "없음"을 반환, 그렇지 않으면 정제된 텍스트
    return text.strip() if text and text.strip() else "없음"


_CONCERN_HINT = {
    "system": "파이프라인 고장을 의심하라 — 상류 서비스·큐·캐시·스키마 드리프트.",
    "operation": "배관은 정상일 수 있다. 현장 상태와 계획 데이터의 불일치를 의심하라.",
}
"""어디를 **먼저** 볼지만 말한다.

무엇이 맞는 판단인가는 여전히 LLM이 정한다(규율 6). 힌트가 결론을 지시하면 그건
우리가 판정을 코드에 박아 놓고 LLM이 했다고 적는 것이다.
"""


def build_briefing(case, topo_slice, *, rules_text="", history_text="",
                   deployment_text=""):
    # 슬라이스의 각 derivation을 "출력 ← via ← inputs" 형식으로 표현
    chain_lines = [
        one_line(
            f"- {output} ← via {deriv.via} ← inputs: "
            + ", ".join(ref.locator for ref in deriv.inputs)
            + (f" (key: {deriv.key})" if deriv.key != "fan-in" else " (fan-in)"))
        for output, deriv in topo_slice.derivations.items()]
    services_line = one_line(", ".join(sorted(topo_slice.services))) or "없음"
    return "\n".join([
        one_line(f"[케이스] {case.id} — {case.gbm}/{case.fct}, "
                  f"접수 경로: {case.origin}"),
        f"[증상] {one_line(case.symptom)}",
        f"[관심사] {case.concern} — {_CONCERN_HINT.get(case.concern, '')}",
        f"[T0] {case.t0.isoformat()}",
        "[토폴로지 슬라이스 — 파생 사슬(상류 방향)]",
        *(chain_lines or ["없음"]),
        f"[관련 서비스] {services_line}",
        f"[적용 룰] {_or_none(rules_text)}",
        f"[유사 이력] {_or_none(history_text)}",
        f"[배포 버전] {_or_none(deployment_text)}",
    ])
