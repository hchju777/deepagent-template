"""REST 실구현 — httpx.AsyncClient. GET 전용, 토폴로지 등록 끝점만 허용."""
import httpx

from src.domain.envelope import Envelope, ProbeResult
from src.domain.ports import RestProberPort
from src.infrastructure.guards import guarded_call
from src.infrastructure.query_rules import endpoint_allowed


class RealRest(RestProberPort):
    def __init__(self, base_url, allowed, *, guards, semaphore, clock):
        self._client = httpx.AsyncClient(base_url=base_url)
        self._allowed = allowed
        self._guards, self._sem, self._clock = guards, semaphore, clock

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
