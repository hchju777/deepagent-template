"""config가 참조하는 `${VAR}` — 있음/없음과 **모양**만.

값을 보여 주지 않는 이유는 당연하고, 이름 목록을 env 전체가 아니라 **config가 참조하는 것**으로 좁히는 이유:
컨테이너 env에는 베이스 이미지·플랫폼이 넣은 것이 섞여 있다. 전부 보이면 "설정한 것이 들어왔나"를 그 사이에서
찾아야 하고, 우리와 상관없는 비밀의 이름까지 밖에 나간다.
"""
import json
from pathlib import Path

from src.config.envresolve import resolve_env


def referenced(config_root: Path) -> dict[str, list[str]]:
    """이름 → 그것을 참조하는 config 파일들(config 루트 기준 상대 경로).

    참조를 찾는 규칙은 로더와 **같은 함수**다(`resolve_env`에 빈 env를 주면 못 찾은 이름 = 참조 전부). 여기서
    정규식을 따로 두면 로더는 참조로 보는데 여기서는 안 보이는 이름이 생긴다.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(config_root.rglob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue                       # 깨진 파일은 /ready와 boot가 말한다
        _, names = resolve_env(raw, {})
        for name in names:
            found.setdefault(name, []).append(path.relative_to(config_root).as_posix())
    return found


def mask_url(url: str) -> str:
    """`scheme://계정@호스트…`의 계정 부분만 `***`로. 호스트·경로는 남긴다 — 어디로 붙으려는지가 진단의 절반이다.

    계정의 끝은 **권한부(authority) 안의 마지막 `@`**다. 비밀번호에 `@`가 들어도, 경로·쿼리에 `@`가 있어도 맞게 자른다.
    """
    head, sep, rest = url.partition("://")
    if not sep:
        return url
    cut = min((i for i in (rest.find("/"), rest.find("?"), rest.find("#")) if i >= 0), default=len(rest))
    authority, tail = rest[:cut], rest[cut:]
    if "@" not in authority:
        return url
    return f"{head}://***@{authority.rsplit('@', 1)[1]}{tail}"


def shape(value: str) -> str:
    if "://" in value:
        return mask_url(value)
    return f"{len(value)}자"


def env_report(config_root: Path, env: dict[str, str]) -> dict:
    rows = []
    for name, where in sorted(referenced(config_root).items()):
        value = env.get(name) or ""
        # 빈 값도 없는 것으로 친다 — 로더와 같은 규칙(`.env`에 `KEY=`만 적힌 상태가 제일 흔하다).
        rows.append({"name": name, "set": bool(value), "shape": shape(value) if value else None,
                     "where": where})
    return {"vars": rows, "missing": [row["name"] for row in rows if not row["set"]]}
