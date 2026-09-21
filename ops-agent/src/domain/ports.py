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
                   limit: int | None = None,
                   projection: list[str] | None = None) -> ProbeResult:
        """문서를 읽는다. limit에 걸려 잘리면 봉투가 complete=False로 말한다.

        `projection`은 받아 올 필드 목록이다. **읽기를 좁히는 것이므로 쓰기
        표면이 아니다** — 리포트는 필드 8개만 쓰는데 문서 전체를 5만 건
        끌어오면 대상의 네트워크와 BSON 디코딩을 그만큼 더 쓴다. 읽기
        전용이라는 말은 "성능에도 개입하지 않는다"까지 포함한다.
        """

    # 집계 파이프라인(`aggregate`)을 두지 않는 이유는 이 포트의 성질 그 자체다.
    # 파이프라인에는 `$out`과 `$merge`가 있고, 둘은 **컬렉션에 쓴다.** 즉
    # "aggregate 하나만 허용"은 쓰기 문을 다시 여는 것이고, 막으려면 단계
    # 화이트리스트를 또 만들어야 한다(rest 어댑터의 등재제와 같은 무게다).
    # 리포트는 행을 받아 파이썬에서 센다 — 느려도 그 대가로 **쓸 방법이 없다**.

    @abstractmethod
    async def count(self, collection: str, filter: dict) -> ProbeResult:
        """조건에 맞는 문서 수. find로 세면 상한에 걸려 틀린 수가 나온다."""

    @abstractmethod
    async def list_collections(self) -> ProbeResult:
        """이 DB에 무슨 컬렉션이 있는가. **발견(discovery)용 읽기다.**

        이게 없으면 `find`를 부르려면 컬렉션 이름을 **미리 알아야** 한다. 그러면
        그 이름이 프롬프트나 config에 박히고, 조사는 우리가 적어 준 곳만 본다 —
        "우리가 아는 만큼만 조사하는" 에이전트가 된다.

        찾을 수 있으면 어느 사이트에서도 같은 방법이 통하고, **무엇을 찾아 무엇을
        골랐는지가 증거로 남는다.**
        """


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

    @abstractmethod
    async def list_topics(self) -> ProbeResult:
        """이 브로커에 무슨 토픽이 있는가. `MongoReaderPort.list_collections`와 같은 이유다."""


class RestProberPort(ABC):
    """대상 REST API 읽기.

    **경로를 인자로 받지 않는다.** `get(path)`를 두면 호출자가 임의의 경로를
    부를 수 있게 되고, 그 순간 "우리가 어디에 요청을 보내는가"가 config에서
    코드로, 결국은 LLM의 판단으로 흘러간다.

    대신 `query`는 **config에 등재된 항목 이름**만 받는다. 어떤 HTTP 메서드로
    어느 경로에 나갈지는 어댑터가 `infra.rest.entries`의 선언을 보고 정하고,
    params는 그 항목의 닫힌 스키마를 통과해야 소켓에 나간다.

    POST가 필요해도 이 성질이 유지되는 이유가 여기 있다 — POST를 허용하는 것과
    "임의의 body로 임의의 경로에 POST하라"를 허용하는 것은 다르다.
    """

    @abstractmethod
    async def query(self, entry: str, params: dict) -> ProbeResult:
        """등재 항목을 호출한다. GET이면 params가 쿼리 문자열, POST면 JSON body.

        미등재 항목·스키마 밖 키·타입 불일치는 **소켓에 나가기 전에**
        error ProbeResult로 거부한다.
        """


class CodeReaderPort(ABC):
    """대상 **코드**를 읽는다. 읽기만 한다.

    ## 왜 fetch·checkout·pull이 없는가

    서브에이전트가 fetch할 수 있으면 **조사 도중에 레포가 움직인다.** 라운드 1에서
    읽은 코드와 라운드 3에서 읽은 코드가 달라지고, 그러면 증거끼리 모순되는데
    원인을 못 찾는다(decisions ⑥). 조사 중 코드는 정지해 있어야 한다 — 잘린 표본에
    `complete=False`를 붙이는 것과 같은 계열의 규율이다.

    `sync`는 포트 밖, CLI 경계의 별도 명령이다.

    ## 왜 커밋을 항상 받는가

    "지금 체크아웃된 것"을 읽으면 **그 사이트에 떠 있는 코드가 아닌 것**을 읽을 수
    있다. 사이트가 뒤처져 있을 때 최신 코드를 읽고 확신에 찬 오답을 내는 것이
    여기서 막아야 할 사고다. 커밋은 호출부가 명시한다.
    """

    @abstractmethod
    async def show(self, repo: str, commit: str, path: str, *,
                   whole: bool = False) -> ProbeResult:
        """그 커밋의 파일 하나. 대상의 config 파일도 이걸로 읽는다."""

    @abstractmethod
    async def grep(self, repo: str, commit: str, patterns: list[str], *,
                   path: str = "") -> ProbeResult:
        """그 커밋에서 패턴을 찾는다. 패턴은 `-e`로 넘긴다(decisions ⑨) —
        `-`로 시작하는 패턴이 옵션으로 읽히는 것을 막는다."""

    @abstractmethod
    async def ls(self, repo: str, commit: str, path: str = "") -> ProbeResult:
        """그 커밋의 파일 목록. 이름을 모를 때 먼저 찾는 용도다(⑮와 같은 이유)."""


class DeployedCodePort(ABC):
    """**배포된 커밋의 코드를 서비스 이름으로** 읽는다.

    `CodeReaderPort`가 "어느 레포의 어느 커밋"을 받는 저수준이라면, 이쪽은 조사가
    실제로 부르는 표면이다. **레포도 커밋도 법인도 인자에 없다** — 리드가 고를 값이
    아니기 때문이다. 리드는 SHA를 모르고, 모르면 지어낸다. 그러면 우리는 떠 있지도
    않은 코드를 읽고 확신에 찬 오답을 낸다(규율 3과 같은 계열).

    `config`가 `read`와 따로 있는 이유: 이름이 사는 config는 **층으로 갈린다.**
    층 하나만 읽으면 위 층이 덮어쓴 값을 사실로 단정한다.

    여기에도 쓰기 동사는 없다. `RestProberPort`와 같은 규율이다 — 목록에 없으면
    문이 안 열린다.
    """

    @abstractmethod
    async def services(self) -> ProbeResult:
        """무엇을 조사할 수 있나. 인자가 없는 것이 정상이다 — 발견용이다."""

    @abstractmethod
    async def config(self, service: str) -> ProbeResult:
        """그 서비스가 배포 시점에 실제로 보는 설정. **층을 전부 합친 결과.**"""

    @abstractmethod
    async def grep(self, patterns: list[str], service: str = "") -> ProbeResult:
        """이 이름을 누가 쓰나. `service`를 안 주면 모든 레포를 본다."""

    @abstractmethod
    async def read(self, service: str, path: str) -> ProbeResult:
        """파일 하나. `path`는 `grep`이 돌려준 경로다."""
