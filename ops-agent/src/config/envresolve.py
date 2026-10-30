"""config 안의 `${VAR}` 참조를 실제 env 값으로 바꾼다.

## 왜 치환을 따로 떼어 놨는가

비밀번호는 git에 못 올린다. 그렇다고 어댑터가 `os.environ`을 몰래 읽으면
config만 봐서는 "이 사이트를 띄우는 데 무엇이 필요한지" 알 수 없다.
`${MX_GUMI_REDIS_PASSWORD}`라고 **config에 적혀 있어야** 기동 검증이
"그 키가 비어 있다"고 미리 말해 줄 수 있다.

## 빠뜨리기 쉬운 함정

이 함수를 만들어 놓고 **부르는 것을 한 군데서 빠뜨리는** 사고가 흔하다.
그러면 그 경로만 `"${MX_GUMI_REDIS_PASSWORD}"`라는 **문자열 그대로**를
비밀번호로 써서 인증에 실패하는데, 에러 메시지는 그냥 "인증 실패"라
원인이 안 보인다. 그래서 loader의 `env` 인자에 **기본값을 두지 않는다** —
기본값이 있으면 누군가 반드시 안 넘긴다.
"""
import re
from typing import Any

# ${NAME} — 문자열 중간에 박혀 있어도 된다("mongodb://u:${PW}@host" 같은 형태).
#
# 이름 부분을 `[A-Za-z_][A-Za-z0-9_]*`로 좁게 잡지 **않는** 이유: 오타나 하이픈이
# 섞인 `${MY-KEY}`가 참조로 인식조차 안 되고 문자열 그대로 비밀번호가 되어
# 소켓에 나간다. 그 실패는 "인증 실패"로만 보여 원인이 안 보인다. 넓게 잡아서
# env에 없으면 **누락으로 보고**하는 편이 항상 낫다.
_REFERENCE = re.compile(r"\$\{([^{}]+)\}")


def resolve_env(value: Any, env: dict[str, str]) -> tuple[Any, list[str]]:
    """치환된 값과, 값을 못 찾은 변수 이름들을 함께 돌려준다.

    **못 찾은 것을 예외로 던지지 않는 이유**: 하나 던지면 사람은 그것만 고치고
    다시 돌린다. 3개가 비어 있으면 3번 반복해야 한다. 전부 모아서 한 번에
    보여 주는 것이 기동 검증의 철학이다(`src/boot.py`).

    빈 문자열도 "없음"으로 친다 — `.env`에 `KEY=`라고만 적힌 상태는
    "설정했다고 믿는데 실제로는 안 된" 제일 흔한 모양이다.
    """
    missing: list[str] = []

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            return _REFERENCE.sub(_substitute, node)
        if isinstance(node, dict):
            return {key: walk(item) for key, item in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    def _substitute(match: re.Match) -> str:
        name = match.group(1)
        found = env.get(name)
        if not found:
            missing.append(name)
            return match.group(0)      # 원문 유지 — 무엇이 안 채워졌는지 보이게
        return found

    return walk(value), sorted(set(missing))
