"""대상 REST API 어댑터. **config에 등재된 항목만 호출한다.**

## 왜 경로가 아니라 항목 이름인가

`get(path)`를 두면 "임의의 경로를 호출하라"가 표현 가능해진다. 그러면 무엇을
호출할지가 config에서 코드로, 결국 LLM의 판단으로 흘러간다. `query(entry, params)`는
config가 선언한 것만 부를 수 있다 — **config가 권한이고, 코드는 그것을 집행한다.**

## POST를 열어도 읽기 전용인 이유

POST가 필요한 API가 있다(조회 조건이 복잡하면 body로 받는다). 메서드 수준에서
잃은 안전장치를 **body 수준의 닫힌 스키마**가 대신한다: 선언되지 않은 키는
소켓에 나가지 않는다. "임의의 body로 임의의 경로에 POST하라"는 여전히 표현
불가능하다.

## 명세를 런타임에 읽어 목록을 넓히지 않는다

대상의 OpenAPI를 읽어 호출 가능 목록을 자동으로 늘리는 코드는 만들지 않는다.
대상이 새 POST를 배포하면 우리 허용 범위가 자동으로 넓어지는 fail-open이 된다.
"""
from typing import Any

from src.config.schema_site import RestConfig, RestEntry
from src.domain.base import Clock
from src.domain.envelope import ProbeResult
from src.domain.ports import RestProberPort

_PY_TYPES = {"str": str, "int": int, "float": (int, float), "bool": bool, "list": list}
_SCALARS = (str, int, float)


def normalize_params(entry: RestEntry, params: dict) -> tuple[dict, list[str]]:
    """선언이 `list`인데 스칼라가 오면 **1개짜리 리스트로 감싼다.**

    사람이 CLI에서 `--params '{"line_code": "P222"}'`라고 쓰는 것이 자연스럽고,
    API는 `["P222"]`를 기대한다. 거부하는 것보다 감싸는 편이 **더 안전하다**:
    `"P222"`를 문자열로 그냥 보내면 서버가 그 필터를 무시하고 **전체를 돌려줄**
    수 있고, 그러면 "조건에 맞는 것이 이만큼 있다"는 거짓 안심이 된다.

    감싼 값이 증거에 기록되는 `request`가 된다 — **기록과 소켓에 나간 것이 같아야**
    나중에 "무엇을 물었나"를 믿을 수 있다.
    """
    normalized, wrapped = {}, []
    for key, value in params.items():
        spec = entry.params.get(key)
        if (spec is not None and spec.type == "list"
                and isinstance(value, _SCALARS) and not isinstance(value, bool)):
            normalized[key] = [value]
            wrapped.append(key)
        else:
            normalized[key] = value
    return normalized, wrapped


def param_problems(entry: RestEntry, params: dict) -> list[str]:
    """params가 등재 스키마를 만족하는가. 어댑터와 스텁이 **같은 함수**를 쓴다.

    검사를 두 벌로 만들면 둘이 갈라지고, 갈라진 쪽이 느슨하면 그 문이 열린다.
    """
    problems = []
    for key in params:
        if key not in entry.params:
            problems.append(f"선언되지 않은 파라미터 — {key}")
    for name, spec in entry.params.items():
        if name not in params:
            if spec.required:
                problems.append(f"필수 파라미터가 없다 — {name}")
            continue
        value = params[name]
        # bool은 int의 하위 타입이라 먼저 걸러야 한다 — True가 int로 통과하면 안 된다.
        if spec.type != "bool" and isinstance(value, bool):
            problems.append(f"{name}은 {spec.type}인데 bool이 왔다")
        elif not isinstance(value, _PY_TYPES[spec.type]):
            problems.append(f"{name}은 {spec.type}인데 {type(value).__name__}이 왔다")
        elif spec.type == "list":
            problems += _list_item_problems(name, value)
    return problems


def _list_item_problems(name: str, items: list) -> list[str]:
    """리스트 원소는 스칼라만. 중첩 리스트나 dict를 허용하면 body 모양이 사실상
    자유가 되고, 닫힌 스키마라는 말의 뜻이 사라진다."""
    for index, item in enumerate(items):
        if isinstance(item, bool) or not isinstance(item, _SCALARS):
            return [f"{name}[{index}]은 스칼라여야 한다 — {type(item).__name__}이 왔다"]
    return []


def prepare_params(entry: RestEntry, params: dict) -> tuple[dict, list[str], list[str]]:
    """정규화 → 검사를 한 번에. 실구현과 스텁이 **같은 경로**를 타게 하는 이음매다."""
    normalized, wrapped = normalize_params(entry, params)
    return normalized, param_problems(entry, normalized), wrapped


class RealRestProber(RestProberPort):
    def __init__(self, cfg: RestConfig, *, clock: Clock):
        self._cfg = cfg
        self._clock = clock

    async def query(self, entry: str, params: dict) -> ProbeResult:
        spec = self._cfg.entries.get(entry)
        source = f"rest:{entry}"
        if spec is None:
            known = ", ".join(sorted(self._cfg.entries)) or "(없음)"
            return ProbeResult.failed(
                f"등재되지 않은 항목 — {entry}. 호출 가능: {known}",
                source=source, clock=self._clock)

        params, problems, wrapped = prepare_params(spec, params)
        if problems:
            # 소켓에 나가기 **전에** 거부한다.
            return ProbeResult.failed("파라미터 거부 — " + "; ".join(problems),
                                      source=source, clock=self._clock)

        url = f"{self._cfg.base_url}{spec.path}"
        source = f"rest:{entry} {spec.method} {spec.path} {params}"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self._cfg.timeout_s,
                                         headers=self._cfg.headers) as client:
                if spec.method == "GET":
                    response = await client.get(url, params=params)
                else:
                    response = await client.post(url, json=params)
            if response.status_code >= 400:
                return ProbeResult.failed(
                    f"HTTP {response.status_code} — {response.text[:300]}",
                    source=source, clock=self._clock)
            request = {"method": spec.method, "path": spec.path, "params": params}
            if wrapped:
                # 감싼 사실을 남긴다 — 사람이 준 것과 나간 것이 다르면 말해야 한다.
                request["wrapped_as_list"] = wrapped
            return ProbeResult.succeeded(
                {"request": request, "status": response.status_code,
                 "response": _decode(response)},
                source=source, clock=self._clock)
        except Exception as exc:                                   # noqa: BLE001
            return ProbeResult.failed(f"{type(exc).__name__}: {exc}",
                                      source=source, clock=self._clock)


def _decode(response: Any) -> Any:
    """JSON이면 파싱하고, 아니면 본문 앞부분을 그대로 — 무엇이 왔는지 보이게."""
    try:
        return response.json()
    except Exception:                                              # noqa: BLE001
        return {"_비JSON응답": response.text[:1000]}
