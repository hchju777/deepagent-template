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
        line = f"- {name}: judge={check.judge}, target={check.target}, {params}"
        # 한 줄로 접는다 — params 값에 개행이 있으면 [적용 룰] 블록에 가짜 항목처럼
        # 붙는다(history의 _clean_line과 같은 이유: id가 안 새도 구조가 새면 같은 문제다).
        lines.append(" ".join(line.split()))
    return "\n".join(lines)


def render_deployment(deployment, *, slice_):
    """브리핑의 `[배포 버전]` — 슬라이스 서비스만.

    없을 때 "없음"이 아니라 "미검증"이라고 적는다: 배포 매핑의 부재는 "배포가 없다"가
    아니라 "무엇이 돌고 있는지 우리가 모른다"이다. 로컬 체크아웃의 HEAD는 배포 진실이
    아니므로(knowledge/deployment.py) 리드가 코드 증거를 그만큼 깎아 읽어야 한다.
    """
    if deployment is None:
        return "배포 매핑 없음 — 이 사이트의 코드 증거는 배포 버전 미검증이다"
    return "\n".join(f"- {name}: {version.repo}@{version.commit}"
                     for name, version in sorted(deployment.services.items())
                     if name in slice_.services)


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


def build_briefing(case, topo_slice, *, rules_text="", history_text="", docs_text=""):
    # 슬라이스의 각 derivation을 "출력 ← via ← inputs" 형식으로 표현
    chain_lines = [
        f"- {output} ← via {deriv.via} ← inputs: "
        + ", ".join(ref.locator for ref in deriv.inputs)
        + (f" (key: {deriv.key})" if deriv.key != "fan-in" else " (fan-in)")
        for output, deriv in topo_slice.derivations.items()]
    services_line = ", ".join(sorted(topo_slice.services)) or "없음"
    return "\n".join([
        f"[케이스] {case.id} — {case.gbm}/{case.fct}, 접수 경로: {case.origin}",
        f"[증상] {case.symptom}",
        f"[관심사] {case.concern} — {_CONCERN_HINT.get(case.concern, '')}",
        f"[T0] {case.t0.isoformat()}",
        "[토폴로지 슬라이스 — 파생 사슬(상류 방향)]",
        *(chain_lines or ["없음"]),
        f"[관련 서비스] {services_line}",
        f"[적용 룰] {_or_none(rules_text)}",
        f"[유사 이력] {_or_none(history_text)}",
        f"[관련 문서] {_or_none(docs_text)}",
    ])
