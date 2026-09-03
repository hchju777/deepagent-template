import inspect

from src.domain.ports import RestProberPort


def test_REST_포트에_쓰기_메서드가_없다():
    # v1의 "완전 읽기 전용"은 포트에 get밖에 없다는 물리적 사실이었다. POST를
    # 열면서 그 자리를 등재제가 대신하지만, 제네릭 쓰기 메서드가 생기는 순간
    # 등재제를 우회할 수 있다. 산문 규율은 읽지 않으면 무력하므로 테스트가 지킨다.
    surface = {name for name in vars(RestProberPort) if not name.startswith("_")}
    assert surface == {"get", "query"}
    for forbidden in ("post", "put", "patch", "delete", "request", "send"):
        assert not hasattr(RestProberPort, forbidden)


def test_query는_항목_이름을_받지_메서드를_받지_않는다():
    # 메서드가 인자면 호출자가 정하게 된다 — 등재 항목이 정해야 한다.
    params = list(inspect.signature(RestProberPort.query).parameters)
    assert params == ["self", "entry", "params"]
