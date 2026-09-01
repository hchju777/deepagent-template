"""config 레이어 deep-merge와 출처(provenance) 추적.

merge 규칙 (스펙 §4.5):
- dict끼리는 재귀 병합, 그 외는 override가 덮어쓴다.
- override 값 None은 "이 키를 삭제하라"는 마커다 (사이트별 점검 끄기 등).
"""


def record_provenance(data, *, source, provenance, prefix=""):
    """베이스 레이어 전체를 출처 dict에 시딩한다 (leaf 경로만)."""
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            record_provenance(value, source=source, provenance=provenance, prefix=path)
        else:
            provenance[path] = source


def _drop_subtree(provenance, path):
    for p in [p for p in provenance if p == path or p.startswith(path + ".")]:
        del provenance[p]


def deep_merge(base, override, *, source, provenance, prefix=""):
    out = dict(base)
    for key, value in override.items():
        path = f"{prefix}.{key}" if prefix else key
        if value is None:                       # null 마커: 삭제
            out.pop(key, None)
            _drop_subtree(provenance, path)
        elif isinstance(value, dict):
            # 기존 dict가 있든 없든 재귀 병합 (null 마커를 중첩에서도 처리)
            base_child = out.get(key)
            if not isinstance(base_child, dict):
                base_child = {}
                _drop_subtree(provenance, path)   # 스칼라였던 자리의 옛 출처 제거
            out[key] = deep_merge(base_child, value, source=source,
                                  provenance=provenance, prefix=path)
        else:
            out[key] = value
            _drop_subtree(provenance, path)
            provenance[path] = source
    return out
