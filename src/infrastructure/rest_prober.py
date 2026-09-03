"""REST 실구현 — httpx.AsyncClient. GET 전용, 토폴로지 등록 끝점만 허용."""
import httpx

from src.domain.envelope import Envelope, ProbeResult
from src.domain.ports import RestProberPort
from src.infrastructure.guards import guarded_call
from src.infrastructure.query_rules import endpoint_allowed, entry_call_problems


class RealRest(RestProberPort):
    def __init__(self, base_url, allowed, entries=None, auth=None, *,
                 guards, semaphore, clock):
        headers = {auth.header: auth.value.get_secret_value()} if auth is not None else {}
        # follow_redirects를 명시적으로 끈다 — 따라가면 인증 헤더가 리다이렉트
        # 대상(다른 호스트일 수 있다)으로 나가고, 그 호스트는 등재 목록에 없다.
        # httpx 기본값과 같지만 기본값에 기대면 누가 바꿔도 아무도 모른다.
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers,
                                         follow_redirects=False)
        self._allowed = allowed
        self._entries = entries or {}
        self._guards, self._sem, self._clock = guards, semaphore, clock

    def _reject(self, message: str) -> ProbeResult:
        return ProbeResult(status="error", envelope=Envelope(observed_at=self._clock()),
                           error=message)

    def _call(self, op):
        return guarded_call(op, timeout_s=self._guards.timeout_s,
                            semaphore=self._sem, clock=self._clock)

    async def get(self, endpoint):
        if not endpoint_allowed(endpoint, self._allowed):
            # 위반 시 네트워크에 나가기 전에 error ProbeResult — guarded_call 미경유.
            return ProbeResult(status="error", envelope=Envelope(observed_at=self._clock()),
                               error=f"끝점 {endpoint!r}는 토폴로지에 등록돼 있지 않다")

        async def op():
            response = await self._client.get(endpoint)
            try:
                body = response.json()
            except ValueError:
                body = response.text
            # status_code를 폐기하지 않는다 — 4xx/5xx도 status="ok"로 유지한 채
            # status_code로 판별하게 한다. 이 프로버는 모니터링 목적이라 오류
            # 응답 자체가 유효한 관측 증거이지, 어댑터 실패(guarded_call의 error)가
            # 아니다.
            data = {"status_code": response.status_code, "body": body}
            return data, Envelope(observed_at=self._clock())
        return await self._call(op)

    async def query(self, entry, params):
        entry_spec = self._entries.get(entry)
        if entry_spec is None:
            return self._reject(f"항목 {entry!r}는 등재돼 있지 않다")
        problems = entry_call_problems(entry_spec, params)
        if problems:
            return self._reject("; ".join(problems))

        async def op():
            # 메서드는 항목이 정한다 — 호출자가 넘긴 값이 아니다.
            if entry_spec.method == "GET":
                response = await self._client.get(entry_spec.path, params=params)
            else:
                response = await self._client.post(entry_spec.path, json=params)
            try:
                body = response.json()
            except ValueError:
                body = response.text
            # request를 함께 싣는다(§2-N4) — 응답만 남기면 0/0/0이 "멈췄다"인지
            # "잘못 물었다"인지 보고서를 읽는 사람이 구별할 수 없다.
            data = {"status_code": response.status_code, "body": body,
                    "request": {"method": entry_spec.method, "path": entry_spec.path,
                                "params": params}}
            return data, Envelope(observed_at=self._clock())
        return await self._call(op)
