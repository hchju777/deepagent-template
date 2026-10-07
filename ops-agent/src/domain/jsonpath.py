"""JSON 값 안의 **한 자리를 고른다** — `redis.get(key, path)`가 쓴다.

왜 있는가: 사내 실측(12a 리뷰 4번)에서 요약 키의 값이 커서 증거 예산 안에 첫 항목도 다 안 들어갔다. 큰 값을
잘라 보여 주면 리드는 조각을 전부로 알거나 같은 키를 또 읽는다. 자르는 대신 **고른 부분을 통째로** 준다 —
`mongo.find`의 `projection`이 읽기를 좁히듯, 이것도 쓰기 표면이 아니라 읽기를 좁히는 것이다.

문법은 `recompute`의 `expect.path`와 같다 — dict 키는 점, 목록은 `[n]`. 그 이상(와일드카드·필터)은 두지 않는다:
리드가 "모든 record의 data"를 원하면 그건 좁힌 것이 아니라 전부다.

실패하면 **있는 것을 말한다.** "없다"만 돌려주면 리드는 경로를 지어내 다시 시도한다 — 그 자리에 어떤 키가
있는지, 목록이 몇 개인지를 적어 주면 다음 시도가 맞는다.
"""
import json
import re
from typing import Any

_TOKEN = re.compile(r"\[(\d+)\]|([^.\[\]]+)")
_JUNK = re.compile(r"\[\d+\]|[^.\[\]]+|\.")
_SHOW_KEYS = 12


def select(value: Any, path: str) -> tuple[bool, Any]:
    """`(True, 고른 값)` 또는 `(False, 왜 못 골랐나)`. 빈 경로는 전부다.

    `value`가 문자열이면 먼저 JSON으로 푼다 — Redis string 값은 늘 문자열로 오고, 그 안에 JSON이 들어 있는 것이
    경로를 댈 이유다. JSON이 아니면 고를 자리가 없으므로 실패다.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return False, "값이 JSON이 아니라 경로로 고를 수 없다 — path 없이 읽어라"
    if not path:
        return True, value
    # 리드가 적은 값이라 숫자·목록이 올 수 있다 — 여기서 거르지 않으면 정규식이 던지고, 실행기는 "호출이 던졌다"로만 적는다.
    if not isinstance(path, str):
        return False, f"경로는 문자열이어야 한다 — {path!r}"
    if _JUNK.sub("", path):
        return False, f"경로 문법이 아니다 — {path!r}. 점으로 키를, [n]으로 목록 자리를 댄다(예: record[0].data)"
    cur, walked = value, ""
    for m in _TOKEN.finditer(path):
        idx, key = m.group(1), m.group(2)
        where = walked or "(맨 위)"
        if key is not None:
            if not isinstance(cur, dict):
                return False, f"{where}는 객체가 아니라 {_kind(cur)}라 키 {key!r}를 고를 수 없다"
            if key not in cur:
                return False, f"{where}에 {key!r}가 없다 — 있는 키: {_keys(cur)}"
            cur, walked = cur[key], f"{walked}.{key}" if walked else key
        else:
            n = int(idx)
            if not isinstance(cur, list):
                return False, f"{where}는 목록이 아니라 {_kind(cur)}라 [{n}]을 고를 수 없다"
            if n >= len(cur):
                return False, f"{where}는 {len(cur)}개라 [{n}]이 없다"
            cur, walked = cur[n], f"{walked}[{n}]"
    return True, cur


def _kind(value: Any) -> str:
    return {dict: "객체", list: "목록", str: "문자열", bool: "불리언", int: "숫자", float: "숫자",
            type(None): "null"}.get(type(value), type(value).__name__)


def _keys(mapping: dict) -> str:
    keys = list(mapping)
    shown = ", ".join(repr(k) for k in keys[:_SHOW_KEYS])
    return shown + (f" 외 {len(keys) - _SHOW_KEYS}개" if len(keys) > _SHOW_KEYS else "")
