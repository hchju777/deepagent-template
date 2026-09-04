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
    """대상 REST API 읽기 전용 접근.

    쓰기 메서드(post/put/patch/delete)를 **의도적으로 두지 않는다**. v1에서는
    get 하나뿐이라 쓰기가 물리적으로 불가능했고, POST가 필요해진 뒤에도 그 성질을
    잃지 않으려면 "임의의 메서드로 임의의 경로를 호출하라"가 표현 불가능해야 한다.
    query는 **등재 항목 이름**만 받고, 어떤 HTTP 메서드로 나갈지는 어댑터가 그
    항목의 선언(target.rest.entries)을 보고 정한다.
    """

    @abstractmethod
    async def get(self, endpoint: str) -> ProbeResult: ...           # 토폴로지 등록 끝점만, GET 전용

    @abstractmethod
    async def query(self, entry: str, params: dict) -> ProbeResult:
        """등재 항목을 호출한다. GET 항목이면 params가 쿼리 문자열, POST면 body다.

        미등재 항목·스키마 밖 필드·허용되지 않은 쿼리 키는 소켓에 나가기 전에
        error ProbeResult로 거부한다. 반환 data에는 무엇을 물었는지도 실어
        (`request`) 호출부가 증거 출처를 만들 수 있게 한다(스펙 §2-N4).
        """
        ...

    @abstractmethod
    async def fetch_spec(self) -> ProbeResult:
        """대상의 OpenAPI 문서를 받아 온다 — 드리프트 점검용(`knowledge validate --live`).

        **인자가 없는 것이 이 메서드의 설계다.** 경로를 인자로 두면 호출자가 정하게
        되고, "임의의 경로를 GET하라"가 다시 표현 가능해져 `get(endpoint)`의 토폴로지
        등재 제약을 우회하는 문이 열린다. 어느 경로로 나갈지는 어댑터가
        `target.rest.openapi_path`(config 선언)를 보고 정한다.

        받아 온 문서는 **증거일 뿐 권한이 아니다** — 이것으로 등재 목록을 넓히는
        코드를 만들면 안 된다(CLAUDE.md 규율 9). 용도는 박제해 둔 pin과 견주어
        "사람이 확인해야 할 변화가 있었나"를 말하는 것뿐이다.
        """
        ...


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
