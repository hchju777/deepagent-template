"""pinned OpenAPI 파서 — 남의 JSON에서 우리가 아는 부분집합만 뽑는다.

원문을 pydantic으로 파싱하지 않는 이유: 대상의 OpenAPI에는 `x-*` 확장 키가
자유롭게 붙고, StrictModel(extra="forbid")로 받으면 그 순간 죽는다. 규율 5를
예외 처리하는 대신 **모르는 것은 담지 않는다** — 그러면 extra 문제가 생기지 않고
우리 모델은 StrictModel을 그대로 지킨다.
"""
from src.knowledge.target_api import parse_spec

RAW = {
    "openapi": "3.0.0",
    "x-vendor-extension": {"뭐든": "들어올 수 있다"},
    "paths": {
        "/summary/prod": {
            "post": {
                "operationId": "get_prod_summary",
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "required": ["date"],
                    "properties": {
                        "part_code": {"type": "array", "items": {"type": "string"}},
                        "line_code": {"type": "array", "items": {"type": "string"}},
                        "date": {"type": "string"},
                        "graph_type": {"$ref": "#/components/schemas/GraphType"},
                    }}}}},
                "responses": {"200": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "properties": {"badge": {"type": "array",
                                             "items": {"type": "integer"}}}}}}}},
            }
        },
        "/lines": {"get": {"parameters": [
            {"name": "active", "in": "query", "required": False,
             "schema": {"type": "boolean"}}]}},
    },
    "components": {"schemas": {"GraphType": {"type": "string", "enum": ["bar", "line"]}}},
}


def test_확장_키가_있어도_파싱된다():
    # 대상의 OpenAPI는 남의 문서다. x-* 하나에 죽으면 우리 기동이 대상 팀 손에 있다.
    api = parse_spec(RAW)
    assert api.problems == []
    assert set(api.operations) == {"POST /summary/prod", "GET /lines"}


def test_body_스키마를_우리_어휘로_옮긴다():
    op = parse_spec(RAW).operations["POST /summary/prod"]
    assert op.props == {"part_code": "list[str]", "line_code": "list[str]",
                        "date": "str", "graph_type": "str"}      # $ref 해석
    assert op.required == ["date"]
    assert op.response_props == ["badge"]


def test_쿼리_파라미터도_같은_어휘로_옮긴다():
    op = parse_spec(RAW).operations["GET /lines"]
    assert op.props == {"active": "bool"} and op.required == []


def test_응답_스키마가_없으면_None이지_빈_리스트가_아니다():
    # "명세가 말하지 않았다"와 "명세가 빈 객체라고 말했다"는 다르다. 섞으면
    # 침묵한 자리에서 거짓 오류가 난다.
    api = parse_spec({"paths": {"/x": {"get": {}}}})
    assert api.operations["GET /x"].response_props is None
    empty = parse_spec({"paths": {"/x": {"get": {"responses": {"200": {"content": {
        "application/json": {"schema": {"type": "object", "properties": {}}}}}}}}}})
    assert empty.operations["GET /x"].response_props == []


def test_모르는_타입은_버리지_않고_기록한다():
    # 조용히 넘기면 대조가 그 필드를 "명세에 없다"고 오판한다.
    api = parse_spec({"paths": {"/x": {"post": {"requestBody": {"content": {
        "application/json": {"schema": {"type": "object", "properties": {
            "blob": {"type": "object"},
            "택일": {"oneOf": [{"type": "string"}, {"type": "integer"}]}}}}}}}}}})
    op = api.operations["POST /x"]
    assert op.props == {} and op.unknown_props == ["blob", "택일"]


def test_순환_참조에_빠지지_않는다():
    api = parse_spec({
        "paths": {"/x": {"post": {"requestBody": {"content": {"application/json": {
            "schema": {"type": "object",
                       "properties": {"a": {"$ref": "#/components/schemas/A"}}}}}}}}},
        "components": {"schemas": {"A": {"$ref": "#/components/schemas/A"}}}})
    op = api.operations["POST /x"]
    assert op.props == {} and op.unknown_props == ["a"]


def test_망가진_명세에도_raise하지_않는다():
    for bad in (None, [], "문자열", {"paths": "문자열"}, {"paths": {"/x": None}},
                {"paths": {"/x": {"get": {"requestBody": 3}}}},
                {"paths": {"/x": {"get": {"parameters": "리스트가_아님"}}}}):
        api = parse_spec(bad)
        assert isinstance(api.problems, list)      # 죽지 않고 문제로 남긴다
        assert isinstance(api.digest, str) and api.digest


def test_digest는_원문_전체를_따른다():
    # 우리가 보는 부분집합만 해싱하면, 대상이 우리 항목 밖을 바꿨을 때 사람이
    # 확인할 기회를 잃는다. 드리프트는 넓게 잡고 영향 판정은 따로 한다.
    other = {**RAW, "info": {"version": "9.9.9"}}
    assert parse_spec(RAW).digest != parse_spec(other).digest


def test_객체가_아닌_응답에는_아무_주장도_하지_않는다():
    # 배열 응답에 response_props=[]를 주면 "명세가 빈 객체라고 말했다"가 되어,
    # body.<키>를 보는 점검이 전부 거짓 오류로 거부된다. 객체가 아니면 최상위
    # 키라는 개념 자체가 없으므로 None이 정직하다.
    api = parse_spec({"paths": {"/lines": {"get": {"responses": {"200": {"content": {
        "application/json": {"schema": {"type": "array", "items": {
            "type": "object", "properties": {"line_code": {"type": "string"}}}}}}}}}}}})
    assert api.operations["GET /lines"].response_props is None


# ── 대조 판정 ────────────────────────────────────────────────────────────────
from src.config.schema_site import CheckConfig, RestEntry          # noqa: E402
from src.knowledge.target_api import response_field_problems, spec_problems   # noqa: E402


def _entry(method, path, body=None, query=None):
    return RestEntry(method=method, path=path, body_schema=body or {},
                     query_schema=query or {})


def _check(target, field):
    return CheckConfig.model_validate({
        "judge": "rule", "schedule": {"interval": "5m"}, "target": target,
        "params": {"rule": "exists", "field": field}})


def test_명세에_없는_항목을_잡는다():
    entries = {"ghost": _entry("POST", "/nowhere", body={"x": "str"})}
    problems = spec_problems(entries, parse_spec(RAW))
    assert any("ghost" in p and "/nowhere" in p for p in problems), problems


def test_명세에_없는_키와_타입_불일치를_잡는다():
    entries = {"summary_prod": _entry("POST", "/summary/prod",
                                      body={"part_code": "str", "save_as": "str",
                                            "date": "str"})}
    problems = spec_problems(entries, parse_spec(RAW))
    assert any("save_as" in p for p in problems), problems            # 명세에 없는 키
    assert any("part_code" in p and "list[str]" in p for p in problems), problems


def test_명세가_필수라_한_키가_없으면_잡는다():
    entries = {"summary_prod": _entry("POST", "/summary/prod",
                                      body={"part_code": "list[str]"})}
    assert any("date" in p and "필수" in p
               for p in spec_problems(entries, parse_spec(RAW)))


def test_명세에만_있는_키는_문제가_아니다():
    # 이것을 문제로 삼으면 명세가 우리 스키마를 넓히는 압력이 된다 — 방향이 뒤집힌다.
    entries = {"summary_prod": _entry("POST", "/summary/prod", body={"date": "str"})}
    problems = spec_problems(entries, parse_spec(RAW))
    assert not any("line_code" in p or "graph_type" in p for p in problems), problems


def test_대조는_등재_항목을_수정하지_않는다():
    # "명세로 스키마를 넓히지 않는다"를 문장이 아니라 테스트로 못 박는다.
    entries = {"summary_prod": _entry("POST", "/summary/prod", body={"date": "str"})}
    before = {k: dict(v.body_schema) for k, v in entries.items()}
    spec_problems(entries, parse_spec(RAW))
    assert {k: dict(v.body_schema) for k, v in entries.items()} == before


def test_명세가_타입을_모르는_필드는_우리를_탓하지_않는다():
    # unknown_props는 "명세가 우리 어휘 밖 타입을 썼다"이지 "우리가 틀렸다"가 아니다.
    api = parse_spec({"paths": {"/x": {"post": {"requestBody": {"content": {
        "application/json": {"schema": {"type": "object", "properties": {
            "blob": {"type": "object"}}}}}}}}}})
    entries = {"e": _entry("POST", "/x", body={"blob": "str"})}
    assert spec_problems(entries, api) == []


def test_명세가_침묵한_응답_필드는_판정하지_않는다():
    api = parse_spec({"paths": {"/x": {"get": {}}}})
    checks = {"c": _check("rest:e", "body.무엇이든")}
    assert response_field_problems(checks, {"e": _entry("GET", "/x")}, api) == []


def test_명세가_말한_응답에_없는_필드를_보면_잡는다():
    checks = {"c": _check("rest:summary_prod", "body.badgee")}       # 오타
    entries = {"summary_prod": _entry("POST", "/summary/prod")}
    assert any("badgee" in p for p in
               response_field_problems(checks, entries, parse_spec(RAW)))


def test_등재_항목이_아닌_target은_응답_판정에서_건너뛴다():
    # 토폴로지 locator(rest:/path)는 다른 이름공간이고 pin은 등재 항목만 덮는다.
    checks = {"c": _check("rest:/api/v1/oee", "body.oee"), "d": _check("mongo:x", "ts")}
    assert response_field_problems(checks, {}, parse_spec(RAW)) == []


def test_깊은_중첩_pin_파일에도_raise하지_않는다(tmp_path):
    # going-live가 `curl … > knowledge/target_api/…`를 지시하므로 파일 내용의
    # 출처는 결국 대상이다. json.loads는 깊이 한계에서 RecursionError를 던지는데
    # 그건 ValueError가 아니라, 잡지 않으면 BootError가 아니라 스택트레이스로 죽는다.
    from src.knowledge.target_api import load_target_api
    path = tmp_path / "target_api" / "mx"
    path.mkdir(parents=True)
    # 깊이 한계는 빌드마다 다르다 — 여기서는 raise가 새지 않는다는 계약만 본다.
    depth = 20000
    (path / "gumi.json").write_text("[" * depth + "]" * depth, encoding="utf-8")
    api, problems = load_target_api(tmp_path, "mx", "gumi")
    assert api is None and any("RecursionError" in p for p in problems), problems


def test_digest_계산이_실패해도_raise하지_않는다():
    # docstring이 "절대 raise하지 않는다"고 단언한다 — 계약은 절대형이다.
    cyclic = {}
    cyclic["self"] = cyclic
    api = parse_spec(cyclic)
    assert isinstance(api.problems, list) and api.problems
