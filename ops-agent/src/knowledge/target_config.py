"""**대상 시스템의** config 층을 하나의 값으로 합친다.

## 왜 층을 합쳐야 하는가

컬렉션·토픽 이름은 대상의 config 파일에 산다(decisions ③-2). 그런데 그 파일은
하나가 아니라 **층으로 갈린다** — 사내 확인:

```
config/gbm/mx.json  →  config/factories/gumi/common.json  →  config/factories/gumi/mx.json
```

층 하나만 읽으면 **위 층이 덮어쓴 값을 사실로 단정한다.** 리드는 그걸 증거로
판정을 쓰고, 보고서는 확신에 차 있는데 틀렸다 — 이 리포가 제일 싫어하는 모양이다.
그래서 "파일 하나 읽기"(`code.read`)와 "이름이 무엇인가"(`code.config`)는 **다른
물음**이고, 후자는 층 전부를 봐야 답이 된다.

## 왜 우리 로더(`src/config/`)를 재사용하지 않는가

**규칙이 다르다.**

| | 우리 config | 대상 config |
|---|---|---|
| `null` | **삭제 마커**(앞 층의 키를 지운다) | 그냥 값 — null로 덮는다 |
| 리스트 | 교체 | 교체 |
| dict | 재귀 병합 | 재귀 병합 |

사내 설명은 "순서대로 보면서 **나중에 본 파일의 데이터로 덮어씌우는** 것"이었다.
거기에 삭제 마커 이야기는 없었고, 없는 규칙을 가정하면 **키가 조용히 사라진다.**

두 병합기가 비슷하게 생겼다고 나중에 누가 합치면, 그 순간 이 차이가 사라지고
대상의 `null` 값이 "그 키는 없다"로 둔갑한다. 그래서 `tests/knowledge/
test_target_config.py`가 **둘이 다르다는 것 자체를** 단정한다.

## 형식

JSON만 읽는다. 파싱 실패는 **던지지 않고** 이유를 돌려준다 — 대상의 파일이
우리 기대와 다른 것은 운영 중에 정상적으로 일어나는 일이고, 그때 조사가 죽으면
안 된다(규율 1).
"""
import json
from copy import deepcopy
from typing import Any


def parse_layer(path: str, text: str) -> tuple[dict | None, str]:
    """층 하나를 읽는다. 실패하면 `(None, 이유)`.

    최상위가 객체가 아니면 병합할 수 없다 — 배열이나 스칼라를 dict에 합치면
    그 뒤의 모든 병합이 뜻을 잃는다. 그것도 이유로 돌려준다.
    """
    try:
        value = json.loads(text)
    except Exception as exc:                                        # noqa: BLE001
        return None, f"{path}: JSON이 아니다 — {type(exc).__name__}: {exc}"
    if not isinstance(value, dict):
        return None, f"{path}: 최상위가 객체가 아니다 — {type(value).__name__}"
    return value, ""


def merge_target(layers: list[tuple[str, dict]]) -> dict[str, Any]:
    """선언된 순서대로 합친다. **나중 층이 덮는다.**

    `layers`는 `(경로, 내용)` 쌍의 목록이고 순서가 곧 우선순위다 — 앞이 밑바닥.
    없는 층은 애초에 이 목록에 없다(층은 선택이다).
    """
    merged: dict[str, Any] = {}
    for _, layer in layers:
        _overlay(merged, layer)
    return merged


def _overlay(into: dict[str, Any], top: dict[str, Any]) -> None:
    """`into`에 `top`을 얹는다 — **`into`를 제자리에서 고친다.**

    `into`는 `merge_target`이 만든 누적기라 고쳐도 된다. 고치면 **안 되는** 것은
    `top`(층의 원본)이다. 같은 층을 두 사이트가 나눠 보기 때문이다 —
    `config/gbm/mx.json`은 gumi도 sevt도 본다. 먼저 본 쪽이 그걸 고치면
    나중 쪽의 밑바닥이 오염되고, 층을 캐시하는 순간 조용히 터진다.
    그건 dict/list를 **복사해서** 넣는 것으로 막고, 테스트가 그걸 단정한다.

    처음엔 함수형으로 써서 `dict(base)`로 한 겹 더 복사했다. RED를 걸어 보니
    **지워도 아무 테스트가 안 깨졌다** — deepcopy가 이미 전부 막고 있었다.
    방어처럼 생긴 무동작은 두지 않는다. 대신 이름이 제자리 수정을 말하게 했다.
    """
    for key, value in top.items():
        if isinstance(value, dict) and isinstance(into.get(key), dict):
            _overlay(into[key], value)
        else:
            # **`null`도 여기로 온다.** 우리 로더라면 키를 지우는 자리다. 대상은
            # 그냥 덮어쓰므로 null이 값으로 남는다 — 그 차이가 이 함수의 전부다.
            into[key] = deepcopy(value) if isinstance(value, (dict, list)) else value
