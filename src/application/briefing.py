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


def _or_none(text):
    # 텍스트가 없거나 공백이면 "없음"을 반환, 그렇지 않으면 정제된 텍스트
    return text.strip() if text and text.strip() else "없음"


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
        f"[T0] {case.t0.isoformat()}",
        "[토폴로지 슬라이스 — 파생 사슬(상류 방향)]",
        *(chain_lines or ["없음"]),
        f"[관련 서비스] {services_line}",
        f"[적용 룰] {_or_none(rules_text)}",
        f"[유사 이력] {_or_none(history_text)}",
        f"[관련 문서] {_or_none(docs_text)}",
    ])
