"""토폴로지와 배포 상태의 스키마.

## 서비스는 `(레포, 선택자)`다 — 경로가 아니다

처음엔 `(레포, 경로)`로 적었다(`decisions ③`). **틀렸다.** 한 레포에 서비스가
여럿인 경우 디렉터리가 나뉜 게 아니라 **코드가 하나이고 `.env`가 역할을 고른다.**
그래서 `git grep`을 경로로 못 좁힌다 — 좁힐 경로가 없다.

대신 `selects`가 "이 `.env` 값이면 이 서비스"를 적는다. 디렉터리가 실제로 나뉜
레포만 `path`를 쓴다.

## 이름은 코드가 아니라 **대상의 config 파일**에 있다

컬렉션·토픽 이름이 소스에 리터럴로 박혀 있지 않고 대상 레포의 config 파일에 있다.
그것도 층 병합 형식이다(우리 `config/`와 같은 구조).

그래서 조사는 `git grep`으로 이름을 찾고 → **어느 config 파일의 어느 층인지**를
본다. `config_paths`가 그 층들을 가리킨다 — 없으면 리드가 레포 전체를 훑어야 하고,
그건 라운드를 태운다.

**우리가 그 층을 합쳐서 답을 주지는 않는다.** 대상의 병합 규칙이 우리와 같다는
보장이 없고, 추측으로 합친 값은 틀려도 그럴듯해 보인다. 층을 그대로 보여 준다.
"""
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from src.domain.base import StrictModel


class Service(StrictModel):
    """조사 대상 서비스 하나."""

    repo: str                                  # `target.code.repos[].name`과 맞아야 한다
    # 이 서비스가 하는 일. 리드가 "누구를 봐야 하나"를 고르는 유일한 단서다.
    role: str = ""
    # 한 코드베이스가 `.env` 값으로 역할을 고를 때, 그 값. 예: `{"SERVICE_ROLE": "processor"}`
    selects: dict[str, str] = {}
    # 디렉터리가 실제로 나뉜 레포에만. `git grep`을 좁히는 데 쓴다.
    path: str = ""

    @field_validator("path")
    @classmethod
    def _relative(cls, v: str) -> str:
        if v.startswith("/") or ".." in v:
            raise ValueError(f"path는 레포 안의 상대 경로다 — {v}")
        return v


_SLOT = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_KNOWN_SLOTS = frozenset({"gbm", "fct"})


FLOW_KINDS = ("topic", "group", "collection", "rediskey")


class FlowSpec(StrictModel):
    """흐름 그래프가 **이름을 어디서 뽑는가** — 합친 대상 config의 점 경로.

    자원 종류 → 그 종류의 이름들이 사는 키 경로. 경로 끝이 dict면 값 하나하나가
    이름이고 str이면 그것이 이름이다. 사내 config 모양이 다르면 이 표만 고친다.
    기본값은 로컬 측정판의 모양이고, 안 맞으면 `code status`가 "이름 0개"로 말한다.
    """
    name_paths: dict[str, list[str]] = {
        "topic": ["kafka.topics"], "group": ["kafka.groups"],
        "collection": ["mongo.collections"], "rediskey": ["redis.keys"]}

    @field_validator("name_paths")
    @classmethod
    def _known_kinds(cls, paths: dict[str, list[str]]) -> dict[str, list[str]]:
        unknown = sorted(set(paths) - set(FLOW_KINDS))
        if unknown:
            raise ValueError(f"모르는 자원 종류 — {', '.join(unknown)}. "
                             f"쓸 수 있는 것: {', '.join(FLOW_KINDS)}")
        return paths


class Topology(StrictModel):
    """GBM 하나의 서비스 지도. **사이트 단위가 아니다** — 코드는 GBM별로 같다."""

    services: dict[str, Service] = {}
    # 흐름 그래프(11c)가 이름을 뽑는 자리. 없으면 기본 표.
    flow: FlowSpec = FlowSpec()
    # 대상 레포 안에서 **이름이 사는 곳**. 층 순서대로 적는다(앞이 밑바닥).
    #
    # `{gbm}`·`{fct}`를 쓸 수 있다 — 대상의 config가 법인별로도 갈리기 때문이다
    # (`config/factories/{fct}/{gbm}.json`). 우리 `SITE_LAYERS`와 같은 문법이다.
    #
    # **KafkaConsumerConfig에서 템플릿을 기각한 것과 다른 경우다.** 거기서 문제는
    # 오타 난 템플릿이 **존재하지 않는 그룹을 조용히 감시**하는 것이었다 — lag 0이
    # 정상과 구별되지 않는다. 여기는 오타가 "그 커밋에 그 파일이 없다"로 **시끄럽게**
    # 드러나고(`code status`), 게다가 아는 자리 이름만 허용한다.
    config_paths: list[str] = []

    @model_validator(mode="after")
    def _something_to_look_at(self):
        if not self.services:
            raise ValueError("services가 비어 있다 — 조사할 서비스가 없으면 토폴로지가 무의미하다")
        return self

    @field_validator("config_paths")
    @classmethod
    def _known_slots_only(cls, paths: list[str]) -> list[str]:
        """모르는 자리는 **치환되지 않은 채** 경로가 된다. 그러면 영원히 못 찾는다."""
        for path in paths:
            unknown = sorted(set(_SLOT.findall(path)) - _KNOWN_SLOTS)
            if unknown:
                raise ValueError(
                    f"{path}에 모르는 자리가 있다 — {', '.join(unknown)}. "
                    f"쓸 수 있는 것: {', '.join(sorted(_KNOWN_SLOTS))}")
        return paths

    def resolved_config_paths(self, gbm: str, fct: str) -> list[str]:
        """이 사이트에서 실제로 볼 경로들."""
        return [path.replace("{gbm}", gbm).replace("{fct}", fct)
                for path in self.config_paths]


class Pin(StrictModel):
    """이 서비스가 지금 어느 커밋으로 떠 있는가.

    `how`가 핵심이다. **"확인했다"와 "그럴 것이다"는 다른 사실이고**, 가정으로
    읽은 코드에서 나온 증거에는 그 사실이 따라붙어야 한다 — 잘린 표본에
    `complete=False`가 붙는 것과 같은 규율이다.

    가정을 확인처럼 다루면 어느 사이트가 뒤처져 있을 때 **떠 있지도 않은 코드를
    읽고 확신에 찬 오답**을 낸다.
    """

    commit: str = ""                # 비면 `Deployment.default_ref`를 쓴다
    how: Literal["declared", "assumed"] = "assumed"
    # 언제 배포됐나. **"증상이 11일부터인데 이게 10일에 배포됐다"는 한 줄이 가설
    # 순위를 통째로 바꾼다.** `code status`가 찍는다 — 읽는 코드 없이 칸을 만들지 않는다.
    deployed_at: str = ""
    note: str = ""

    @model_validator(mode="after")
    def _declared_needs_a_commit(self):
        if self.how == "declared" and not self.commit:
            raise ValueError("how=declared인데 commit이 없다 — 무엇을 확인했다는 것인가")
        return self


class Deployment(StrictModel):
    """GBM 하나의 배포 상태.

    **GBM 단위인 이유**: 코드가 GBM별로 같다(사내 확인). 사이트 28개로 쪼개면
    같은 내용을 28번 손으로 유지해야 하고, 손으로 유지하는 것은 썩는다.

    사이트 예외는 `sites`로 적는다 — 한 법인만 롤백했다 같은 일이 있다.
    """

    default_ref: str = Field(default="main", min_length=1)
    # 서비스별 확정 커밋. 여기 없으면 `default_ref`를 가정으로 쓴다.
    pins: dict[str, Pin] = {}
    # 사이트별 예외. `{"sevt": {"processor": {...}}}`
    sites: dict[str, dict[str, Pin]] = {}

    def pin_for(self, service: str, *, fct: str = "") -> Pin:
        """이 사이트에서 이 서비스가 떠 있다고 **우리가 믿는** 상태."""
        site = self.sites.get(fct, {})
        if service in site:
            return site[service]
        if service in self.pins:
            return self.pins[service]
        return Pin(commit=self.default_ref, how="assumed",
                   note=f"선언이 없어 {self.default_ref} 최신이라고 가정한다")
