"""config 안의 ${ENV_KEY} 참조 해석.

- 문자열 전체가 참조일 때만 치환한다. 보간("a${X}b")은 지원하지 않는다 —
  비밀값이 더 큰 문자열에 섞여 로그로 새는 것을 구조적으로 막는다.
- 부재/빈 값은 모아서 반환한다. 호출자(기동 검증)가 전부 나열해 거부한다.
"""
import re

_ENV_REF = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")


def resolve_env_refs(data, *, env):
    missing: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            m = _ENV_REF.match(node)
            if m:
                key = m.group(1)
                value = env.get(key, "")
                if value == "":
                    missing.append(key)
                    return node
                return value
        return node

    return walk(data), missing
