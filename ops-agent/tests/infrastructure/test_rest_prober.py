"""REST 어댑터 — 등재제와 닫힌 파라미터 스키마.

여기서 검증하는 것은 **소켓에 나가기 전에 거부되는가**다. 실제 호출은
`python -m src peek rest`가 사내에서 확인한다.
"""
from src.config.schema_site import RestConfig
from src.infrastructure.rest_prober import RealRestProber, param_problems

CFG = RestConfig(
    base_url="http://twin.example.net:8080",
    entries={
        "oee_summary": {"method": "POST", "path": "/api/v1/oee/summary",
                        "params": {"line": {"type": "str", "required": True},
                                   "shift": {"type": "int", "required": False}}},
        "lines": {"method": "GET", "path": "/api/v1/lines"},
    })
ENTRY = CFG.entries["oee_summary"]


def test_선언된_파라미터는_통과한다():
    assert param_problems(ENTRY, {"line": "L3", "shift": 2}) == []


def test_선택_파라미터는_없어도_된다():
    assert param_problems(ENTRY, {"line": "L3"}) == []


def test_선언되지_않은_키를_거부한다():
    assert param_problems(ENTRY, {"line": "L3", "drop": 1}) == \
        ["선언되지 않은 파라미터 — drop"]


def test_필수_파라미터_누락을_잡는다():
    assert param_problems(ENTRY, {"shift": 1}) == ["필수 파라미터가 없다 — line"]


def test_타입이_다르면_거부한다():
    assert param_problems(ENTRY, {"line": "L3", "shift": "2"}) == \
        ["shift은 int인데 str이 왔다"]


def test_bool을_int로_통과시키지_않는다():
    # 파이썬에서 bool은 int의 하위 타입이라 isinstance(True, int)가 참이다.
    # True가 shift=1로 조용히 나가면 요청과 기록이 어긋난다.
    assert param_problems(ENTRY, {"line": "L3", "shift": True}) == \
        ["shift은 int인데 bool이 왔다"]


async def test_등재되지_않은_항목은_호출_자체를_안_한다(clock):
    result = await RealRestProber(CFG, clock=clock).query("delete_line", {})
    assert result.status == "error"
    assert "등재되지 않은 항목" in result.error
    assert "lines, oee_summary" in result.error       # 무엇을 부를 수 있는지 알려준다


async def test_파라미터가_틀리면_네트워크를_타지_않는다(clock):
    # base_url이 존재하지 않는 호스트인데도 연결 오류가 아니라 파라미터 거부가 나와야 한다.
    result = await RealRestProber(CFG, clock=clock).query("oee_summary", {"drop": 1})
    assert result.status == "error"
    assert "파라미터 거부" in result.error
    assert "Connect" not in result.error


def test_포트에_get_path가_없다():
    # 경로를 인자로 받는 메서드가 생기면 "임의의 경로를 부르라"가 다시 표현 가능해진다.
    from src.domain.ports import RestProberPort
    surface = {n for n in dir(RestProberPort) if not n.startswith("_")}
    assert surface == {"query"}, f"REST 포트 표면이 넓어졌다 — {surface}"
