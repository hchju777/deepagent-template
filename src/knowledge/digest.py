"""지식 산출물의 content digest — 케이스 T0 박제(스펙 §2.5-3)에 쓴다.

파싱된 객체 기준이라 파일의 공백·키 순서 변경은 digest를 바꾸지 않는다.
"""
import hashlib
import json


def canonical_digest(obj) -> str:
    canonical = json.dumps(obj, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
