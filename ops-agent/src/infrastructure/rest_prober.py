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

_PY_TYPES = {"str": str, "int": int, "float": (int, float), "bool": bool}


def param_problems(entry: RestEntry, params: dict) -> list[str]:
    """params가 등재 스키마를 만족하는가. 어댑터와 기동 검증이 **같은 함수**를 쓴다.

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
        expected = _PY_TYPES[spec.type]
        value = params[name]
        # bool은 int의 하위 타입이라 먼저 걸러야 한다 — True가 int로 통과하면 안 된다.
        if spec.type != "bool" and isinstance(value, bool):
            problems.append(f"{name}은 {spec.type}인데 bool이 왔다")
        elif not isinstance(value, expected):
            problems.append(f"{name}은 {spec.type}인데 {type(value).__name__}이 왔다")
    return problems


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

        problems = param_problems(spec, params)
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
            return ProbeResult.succeeded(
                {"request": {"method": spec.method, "path": spec.path, "params": params},
                 "status": response.status_code,
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
