"""포트 표면을 단정한다 — 여기에 쓰기 메서드가 생기면 실패한다.

"대상 시스템에 쓰지 않는다"는 이 시스템의 제일 중요한 약속이다. 그런데 산문
규율은 급할 때 깨진다 — 누군가 한 줄 추가하고, 리뷰에서 놓치고, 프로덕션에
나간다. 그래서 **테스트가 표면을 지킨다.**
"""
import inspect

from src.domain.ports import (CodeReaderPort, DeployedCodePort, KafkaInspectorPort,
                              MongoReaderPort,
                              RedisReaderPort)

ALL_PORTS = (RedisReaderPort, MongoReaderPort, KafkaInspectorPort, CodeReaderPort,
             DeployedCodePort)

# 대상 시스템의 상태를 바꾸는 동사들. 이름만으로도 표면에 나타나선 안 된다.
WRITE_VERBS = {
    "post", "put", "patch", "delete", "set", "insert", "insert_one", "insert_many",
    "update", "update_one", "update_many", "replace", "remove", "drop", "create",
    "write", "save", "append", "push", "produce", "send", "commit", "flush",
    "expire", "rename", "incr", "decr", "lpush", "rpush", "setex",
    # 코드 레포를 움직이는 것들. 조사 중에 레포가 움직이면 라운드끼리
    # 증거가 모순되는데 원인을 못 찾는다(decisions ⑥).
    "fetch", "pull", "clone", "checkout", "reset", "sync", "apply", "merge",
}


def _public_methods(cls) -> set[str]:
    return {name for name, _ in inspect.getmembers(cls, callable) if not name.startswith("_")}


def test_포트에_쓰기_메서드가_없다():
    offenders = {}
    for port in ALL_PORTS:
        found = _public_methods(port) & WRITE_VERBS
        if found:
            offenders[port.__name__] = sorted(found)
    assert not offenders, (
        f"대상 시스템에 쓰는 메서드가 포트 표면에 생겼다: {offenders}\n"
        "읽기 전용은 정책이 아니라 코드의 성질이어야 한다 — 메서드가 없으면 "
        "'쓰라'고 말하는 것 자체가 표현 불가능하다.")


def test_포트의_모든_메서드는_추상이다():
    """구현을 깜빡한 어댑터가 조용히 None을 돌려주는 일이 없도록."""
    for port in ALL_PORTS:
        for name in _public_methods(port):
            method = getattr(port, name)
            assert getattr(method, "__isabstractmethod__", False), (
                f"{port.__name__}.{name} 이 추상이 아니다")


def test_포트의_모든_메서드는_async다():
    """어댑터 하나가 sync면 그 하나가 이벤트 루프를 막아 순찰 전체가 멈춘다."""
    for port in ALL_PORTS:
        for name in _public_methods(port):
            assert inspect.iscoroutinefunction(getattr(port, name)), (
                f"{port.__name__}.{name} 이 async가 아니다")


def test_포트는_구현_없이_인스턴스화할_수_없다():
    for port in ALL_PORTS:
        try:
            port()
        except TypeError:
            continue
        raise AssertionError(f"{port.__name__} 이 추상 메서드 없이 인스턴스화됐다")


# ── 검사기가 실제로 잡는지 ─────────────────────────────────────────────

def test_검사기가_쓰기_메서드를_실제로_잡는다():
    from abc import ABC, abstractmethod

    class _BadPort(ABC):
        @abstractmethod
        async def get(self, key: str): ...

        @abstractmethod
        async def set(self, key: str, value: str): ...   # ← 이게 잡혀야 한다

    assert _public_methods(_BadPort) & WRITE_VERBS == {"set"}


# ── 발견용 읽기 ──────────────────────────────────────────────────────

def test_이름을_모르고도_찾을_수_있다():
    """**이게 없으면 조사가 우리가 적어 준 곳만 본다.**

    `find(collection, ...)`는 컬렉션 이름을 미리 알아야 부를 수 있다. 그 이름이
    프롬프트나 config에 박히면, 리드는 사람이 알려 준 곳만 뒤지고 새 사이트·새 대상
    에서는 아무것도 못 찾는다 — "우리가 아는 만큼만 조사하는" 에이전트가 된다.

    찾을 수 있으면 어느 사이트에서도 같은 방법이 통하고, **무엇을 찾아 무엇을
    골랐는지가 증거로 남는다.**
    """
    assert "scan" in _public_methods(RedisReaderPort)              # 키
    assert "list_collections" in _public_methods(MongoReaderPort)  # 컬렉션
    assert "list_topics" in _public_methods(KafkaInspectorPort)    # 토픽


def test_발견용도_쓰기가_아니다():
    """`list_*`는 목록을 **읽는다.** 이름에 write 동사가 없다는 위 테스트와 별개로,
    실수로 `create_*`류가 발견용인 척 들어오는 것을 막는다."""
    for port in ALL_PORTS:
        for name in _public_methods(port):
            assert not name.startswith(("create", "make", "ensure")), \
                f"{port.__name__}.{name} — 발견용이라도 만드는 동사는 안 된다"
