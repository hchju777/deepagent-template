"""대상 시스템 읽기 전용 포트 — 스펙 §4.1. 쓰는 메서드는 존재하지 않는다.

모든 async 메서드는 ProbeResult를 반환하고 절대 raise하지 않는다(§5.4 1층).
LLM에는 이 포트가 노출하는 파라미터화된 호출만 주어진다 — 원시 쿼리 금지.
"""
from abc import ABC, abstractmethod
from datetime import datetime

from src.domain.envelope import ProbeResult


class RedisReaderPort(ABC):
    @abstractmethod
    async def get(self, key: str) -> ProbeResult: ...          # string→str, hash→dict (TYPE 분기)

    @abstractmethod
    async def scan(self, pattern: str) -> ProbeResult: ...     # 키 목록, max_rows 상한

    @abstractmethod
    async def ttl(self, key: str) -> ProbeResult: ...          # 초 단위, 없으면 -2/-1 규약 그대로


class MongoReaderPort(ABC):
    @abstractmethod
    async def find(self, collection: str, filter: dict, *,
                   sort: list[tuple[str, int]] | None = None,
                   limit: int | None = None) -> ProbeResult: ...

    @abstractmethod
    async def count(self, collection: str, filter: dict) -> ProbeResult: ...

    @abstractmethod
    async def aggregate(self, collection: str, pipeline: list[dict]) -> ProbeResult: ...


class KafkaInspectorPort(ABC):
    @abstractmethod
    async def group_offsets(self, group: str) -> ProbeResult: ...   # 파티션별 committed/end/lag

    @abstractmethod
    async def read(self, topic: str, *, start: datetime,
                   end: datetime) -> ProbeResult: ...                # 보존 내 메시지, earliest 폴백 시 봉투에 명시


class RestProberPort(ABC):
    @abstractmethod
    async def get(self, endpoint: str) -> ProbeResult: ...           # 토폴로지 등록 끝점만, GET 전용


class CodeRepoReaderPort(ABC):
    """유일한 sync 포트 — git subprocess. 읽기 명령만 노출한다."""

    @abstractmethod
    def hash_exists(self, repo: str, commit: str) -> bool: ...

    @abstractmethod
    def show(self, repo: str, commit: str, path: str) -> str: ...    # 실패 시 CodeRepoError

    @abstractmethod
    def head(self, repo: str) -> str: ...

    @abstractmethod
    def grep(self, repo: str, commit: str, pattern: str) -> list[str]: ...
