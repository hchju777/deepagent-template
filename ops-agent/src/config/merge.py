"""config 계층 deep-merge와 **출처 추적**.

## 계층

```
gbm/common.json        전 사업부·전 법인 공통          ← 제일 약함
gbm/mx.json            mx 사업부 공통 (redis 키 규칙 등)
fct/gumi/common.json   구미 법인 공통 (전 사업부)
fct/gumi/mx.json       구미 × mx (실제 url 등)         ← 제일 강함
```

아래로 갈수록 이긴다. 같은 키를 여러 층이 말하면 더 구체적인 층이 이긴다.

## 병합 규칙

- dict끼리는 **재귀 병합** — 아래 층이 키 하나만 말해도 위 층의 나머지가 남는다.
- 그 외(스칼라·리스트)는 아래 층이 덮어쓴다. 리스트를 이어붙이지 않는 이유:
  "브로커 목록에 하나 추가"와 "브로커 목록을 이걸로 교체"를 구별할 문법이 없고,
  그 둘을 헷갈리면 다른 법인의 브로커에 붙는다.
- **`null`은 "이 키를 지워라"는 마커다.** 위 층이 켠 것을 아래 층이 끌 수 있어야 한다.

## 흔한 실수: 빈 층 지름길

"앞 계층이 비어 있으면 재귀를 건너뛴다"는 최적화를 넣으면 **null 마커를 못 지우고
지나친다.** 지우려는 키가 아직 없는 자리에도 마커는 유효해야 하고(나중에 다른
경로로 들어올 수 있다), 무엇보다 그 자리의 **출처 기록**을 정리해야 한다.
deep-merge는 항상 전체 경로를 탄다.

## 출처를 왜 추적하는가

`config show`가 "이 값이 어느 파일에서 왔는가"를 말할 수 있어야 한다. 층이 넷이면
"분명히 바꿨는데 안 먹는다"가 반드시 생기고, 그때 답은 **아래 층이 덮고 있다**이다.
출처가 없으면 네 파일을 다 열어 봐야 안다.
"""


def record_provenance(data: dict, *, source: str, provenance: dict, prefix: str = "") -> None:
    """층 하나의 모든 잎(leaf) 경로를 출처 dict에 기록한다."""
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            record_provenance(value, source=source, provenance=provenance, prefix=path)
        else:
            provenance[path] = source


def _drop_subtree(provenance: dict, path: str) -> None:
    """그 경로와 그 아래 전부의 출처를 지운다 — 값이 사라졌거나 모양이 바뀌었다."""
    for stale in [p for p in provenance if p == path or p.startswith(path + ".")]:
        del provenance[stale]


def deep_merge(base: dict, override: dict, *, source: str,
               provenance: dict, prefix: str = "") -> dict:
    """base 위에 override를 얹는다. provenance는 제자리에서 갱신된다."""
    merged = dict(base)
    for key, value in override.items():
        path = f"{prefix}.{key}" if prefix else key

        if value is None:                       # 삭제 마커
            merged.pop(key, None)
            _drop_subtree(provenance, path)
            continue

        if isinstance(value, dict):
            # 앞 층에 이 자리가 없거나 dict가 아니어도 **재귀는 탄다** —
            # 중첩된 null 마커와 옛 출처가 그 안에 있을 수 있다.
            child = merged.get(key)
            if not isinstance(child, dict):
                child = {}
                _drop_subtree(provenance, path)   # 스칼라였던 자리의 옛 출처 제거
            merged[key] = deep_merge(child, value, source=source,
                                     provenance=provenance, prefix=path)
            continue

        merged[key] = value
        _drop_subtree(provenance, path)          # 리스트→스칼라 같은 모양 변화 대비
        provenance[path] = source
    return merged
