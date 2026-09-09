"""대상 시스템에 닿는 표면. **쓰는 메서드는 존재하지 않는다.**

## 왜 ABC인가

포트는 "우리가 대상 시스템에 무엇을 할 수 있는가"의 전부다. 실구현(3단계)과
스텁(테스트용)이 같은 ABC를 구현하므로, 판정 로직은 어느 쪽이 꽂혔는지 모른 채
돌아간다. 그래서 테스트가 Redis 없이 돈다.

## 왜 여기에 post/put/delete가 없는가

"대상 시스템에 쓰지 말자"는 정책 문서로는 안 지켜진다. 급할 때 누군가 한 줄
추가하고, 리뷰에서 놓치고, 그게 프로덕션에 나간다.

메서드를 **아예 만들지 않으면** "쓰라"고 말하는 것 자체가 표현 불가능해진다.
LLM이 도구로 이 포트를 받을 때도 마찬가지다 — 존재하지 않는 메서드는
환각으로도 부를 수 없다(부르면 AttributeError로 즉시 드러난다).

`tests/domain/test_ports.py`가 이 표면을 단정한다. 산문 규율은 읽지 않으면
무력하므로 테스트가 지킨다.

## 왜 전부 ProbeResult를 반환하고 raise하지 않는가

envelope.py의 첫 절을 보라. 요약하면: 순찰 잡 하나가 raise하면 스케줄러가
그 잡을 조용히 빼버려 순찰이 죽어도 아무도 모르게 된다.
"""
from abc import ABC, abstractmethod

from src.domain.envelope import ProbeResult


class RedisReaderPort(ABC):
    """Redis 읽기. 값 하나, 키 목록, 남은 수명."""

    @abstractmethod
    async def get(self, key: str) -> ProbeResult:
        """string이면 str, hash면 dict로 돌려준다(TYPE으로 분기)."""

    @abstractmethod
    async def scan(self, pattern: str) -> ProbeResult:
        """패턴에 맞는 키 목록. 상한에 걸리면 봉투가 complete=False로 말한다.

        KEYS가 아니라 SCAN인 이유: KEYS는 대상 Redis를 블로킹한다. 우리는
        읽기 전용이지만 **성능에도 개입하지 않아야** 진짜 읽기 전용이다.
        """

    @abstractmethod
    async def ttl(self, key: str) -> ProbeResult:
        """남은 수명(초). Redis 규약 그대로 -1(무기한)/-2(키 없음)를 보존한다.

        여기서 None이나 0으로 정규화하면 "키가 없다"와 "만료가 없다"가
        같은 값이 되어 구별이 사라진다.
        """


class MongoReaderPort(ABC):
    """MongoDB 읽기."""

    @abstractmethod
    async def find(self, collection: str, filter: dict, *,
                   sort: list[tuple[str, int]] | None = None,
                   limit: int | None = None) -> ProbeResult:
        """문서를 읽는다. limit에 걸려 잘리면 봉투가 complete=False로 말한다."""

    @abstractmethod
    async def count(self, collection: str, filter: dict) -> ProbeResult:
        """조건에 맞는 문서 수. find로 세면 상한에 걸려 틀린 수가 나온다."""


class KafkaInspectorPort(ABC):
    """Kafka 관찰. **컨슈머 그룹에 참여하지 않는다.**

    `subscribe(group_id=...)`로 그룹에 들어가면 브로커가 리밸런스를 돌리고
    `__consumer_offsets`에 커밋이 기록된다. 그건 읽기가 아니라 **쓰기**이고,
    같은 group_id를 쓰는 실제 서비스가 있으면 우리가 그 서비스의 파티션을
    빼앗는다. 모니터링하러 들어가서 대상을 멈추는 셈이다.

    그래서 이 포트는 `assign()`(그룹 밖 직접 지정)과 AdminClient 조회만 쓴다.
    """

    @abstractmethod
    async def group_offsets(self, group: str) -> ProbeResult:
        """**다른** 컨슈머 그룹의 커밋 오프셋과 lag을 조회한다.

        우리가 그 그룹에 들어가는 게 아니라, AdminClient로 그 그룹의 상태를
        밖에서 들여다보는 것이다. 감시 대상 서비스가 밀리고 있는지를 본다.
        """

    @abstractmethod
    async def tail(self, topic: str, *, limit: int) -> ProbeResult:
        """토픽 끝에서 최근 메시지를 읽는다(그룹 미참여, 오프셋 커밋 없음)."""
