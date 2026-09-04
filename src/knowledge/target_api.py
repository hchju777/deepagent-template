"""pinned OpenAPI — 대상 API의 자기 서술을 knowledge에 박제하고 우리 config와 **대조**한다.

**방향이 이 모듈의 전부다.** 명세는 "당신이 쓴 것이 틀렸다"고 말할 수 있을 뿐,
우리 허용 범위를 넓힐 수 없다(CLAUDE.md 규율 9, 스펙 §2-N1). 대상이 새 POST를
배포했을 때 우리 등재 목록이 자동으로 따라 넓어지면 그것이 fail-open이고, 계획 8이
세운 등재제 전체가 무의미해진다. 그래서 명세는 `knowledge/`에 살고(topology·
deployment와 같은 성질 — git에 커밋되고 digest가 케이스에 박제되고 사람이 갱신한다),
권한은 `config/`에 남는다.

**원문을 pydantic으로 파싱하지 않는다.** OpenAPI 문서는 남의 것이고 `x-*` 확장 키가
자유롭게 붙는다 — `StrictModel`로 받으면 그 순간 죽고, 우리 기동이 대상 팀 손에
넘어간다. 그렇다고 규율 5를 예외 처리하지도 않는다: 평범한 dict 접근으로 **우리가
아는 것만** 뽑아내고, 그것을 담는 우리 모델은 `StrictModel`이다. 모르는 것을 담지
않으므로 `extra` 문제가 애초에 생기지 않는다.

등재 항목(`RestEntry`)을 타입으로 받지 않고 `.method`/`.path`/`.body_schema`/
`.query_schema`를 읽는 덕 타이핑으로 받는다 — `schema_site`를 import하면
config→infrastructure→knowledge→config 순환이 닫힌다(CLAUDE.md가 기록한 자리).
`schema_app.StrictModel`은 그 계열이 아니라 leaf이고, topology·deployment도 이미
같은 것을 쓴다.
"""
import json
from pathlib import Path
from typing import Any

from src.config.schema_app import StrictModel
from src.knowledge.digest import canonical_digest

_SCALARS = {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}
_REF_PREFIX = "#/components/schemas/"
_MAX_REF_DEPTH = 8          # 순환 $ref에서 무한 재귀를 끊는다


class OperationSpec(StrictModel):
    """명세가 말한 연산 하나 중 우리가 이해한 부분."""
    method: str
    path: str
    props: dict[str, str] = {}        # 필드명 → 우리 어휘의 타입("str"/"list[int]"…)
    unknown_props: list[str] = []     # 명세에 있으나 우리 어휘로 못 옮긴 것
    required: list[str] = []
    # None과 []는 다르다. None은 "명세가 응답 모양을 말하지 않았다"(우리는 아무
    # 주장도 하지 않는다), []는 "명세가 빈 객체라고 말했다". 이 구별을 잃으면
    # 응답 필드 검증이 명세가 침묵한 자리에서 거짓 오류를 낸다.
    response_props: list[str] | None = None


class TargetApi(StrictModel):
    digest: str                                # **원문 전체**의 canonical_digest
    operations: dict[str, OperationSpec] = {}  # "POST /summary/prod" → spec
    problems: list[str] = []                   # 파싱 중 포기한 것(조용한 생략 금지)


def _resolve(node, components: dict, depth: int = 0):
    """`$ref`를 따라간다. 순환·미지원 형태·과도한 깊이는 None.

    깊이 상한이 순환을 끊는다 — 명세가 `A: {$ref: A}`를 담고 있어도(실제로 있다)
    스택이 아니라 None으로 끝나야 무raise 규율이 유지된다.
    """
    if depth > _MAX_REF_DEPTH:
        return None
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if not isinstance(ref, str):
        return node
    if not ref.startswith(_REF_PREFIX):     # 외부 파일 참조 등 — 따라가지 않는다
        return None
    return _resolve(components.get(ref[len(_REF_PREFIX):]), components, depth + 1)


def _our_type(schema, components: dict) -> str | None:
    """명세의 스키마를 우리 어휘로 옮긴다. 못 옮기면 None(=모른다고 말한다)."""
    schema = _resolve(schema, components)
    if not isinstance(schema, dict):
        return None
    kind = schema.get("type")
    if kind in _SCALARS:
        return _SCALARS[kind]
    if kind == "array":
        item = _resolve(schema.get("items"), components)
        if isinstance(item, dict) and item.get("type") in _SCALARS:
            return f"list[{_SCALARS[item['type']]}]"
    return None                              # object·oneOf/anyOf·items 없는 array


def _props(schema, components: dict) -> tuple[dict[str, str], list[str], list[str]]:
    """object 스키마에서 (아는 필드, 모르는 필드, 필수 목록)."""
    schema = _resolve(schema, components)
    if not isinstance(schema, dict):
        return {}, [], []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}, [], []
    known, unknown = {}, []
    for name, sub in properties.items():
        our = _our_type(sub, components)
        if our is None:
            unknown.append(name)
        else:
            known[name] = our
    required = [r for r in schema.get("required", []) if isinstance(r, str)] \
        if isinstance(schema.get("required"), list) else []
    return known, unknown, required


def _json_schema(container) -> Any:
    """requestBody/response의 `content['application/json'].schema`를 꺼낸다."""
    if not isinstance(container, dict):
        return None
    content = container.get("content")
    if not isinstance(content, dict):
        return None
    media = content.get("application/json")
    return media.get("schema") if isinstance(media, dict) else None


def _operation(method: str, path: str, body, components: dict) -> OperationSpec:
    known, unknown, required = _props(_json_schema(body.get("requestBody")), components)

    # 쿼리 파라미터는 body와 같은 어휘로 옮긴다 — GET 항목은 query_schema로 대조된다.
    params = body.get("parameters")
    if isinstance(params, list):
        for param in params:
            if not isinstance(param, dict) or param.get("in") != "query":
                continue
            name = param.get("name")
            if not isinstance(name, str):
                continue
            our = _our_type(param.get("schema"), components)
            if our is None:
                unknown.append(name)
            else:
                known[name] = our
            if param.get("required"):
                required.append(name)

    # 200이 없으면 2xx 중 아무거나 — 명세마다 201/204를 쓴다. 그래도 없으면 침묵.
    responses = body.get("responses") if isinstance(body.get("responses"), dict) else {}
    ok = next((responses[code] for code in ("200", "201", "default")
               if isinstance(responses.get(code), dict)), None)
    # 객체가 아닌 응답(배열·스칼라)에는 아무 주장도 하지 않는다. `[]`를 주면
    # "명세가 빈 객체라고 말했다"가 되어 body.<키>를 보는 점검이 전부 거짓 오류로
    # 거부된다 — 최상위 키라는 개념 자체가 없는 응답이다.
    response_schema = _resolve(_json_schema(ok), components)
    response_props = None
    if isinstance(response_schema, dict) and isinstance(response_schema.get("properties"), dict):
        resp_known, resp_unknown, _ = _props(response_schema, components)
        response_props = sorted([*resp_known, *resp_unknown])

    return OperationSpec(method=method, path=path, props=known,
                         unknown_props=sorted(unknown), required=sorted(set(required)),
                         response_props=response_props)


def parse_spec(raw) -> TargetApi:
    """OpenAPI 원문에서 우리가 아는 부분집합을 뽑는다. **절대 raise하지 않는다.**

    남의 JSON을 파싱하는 자리라 무raise 규율이 특히 중요하다 — 대상이 이상한 명세를
    배포했다고 우리 데몬이 죽으면, 모니터링이 감시 대상에 종속된다.

    digest는 **원문 전체**를 따른다(부분집합이 아니라). 드리프트는 넓게 잡고
    "우리 항목에 영향이 있는가"는 따로 판정한다 — 좁게 잡으면 대상이 우리 항목
    밖을 바꿨을 때 사람이 확인할 기회를 잃는다.
    """
    digest = canonical_digest(raw)
    problems: list[str] = []
    operations: dict[str, OperationSpec] = {}
    try:
        if not isinstance(raw, dict):
            return TargetApi(digest=digest,
                             problems=[f"명세의 최상위가 객체가 아니다 ({type(raw).__name__})"])
        components = raw.get("components", {})
        components = components.get("schemas", {}) if isinstance(components, dict) else {}
        if not isinstance(components, dict):
            components = {}
        paths = raw.get("paths")
        if not isinstance(paths, dict):
            return TargetApi(digest=digest, problems=["명세에 paths 객체가 없다"])
        for path, item in paths.items():
            if not isinstance(item, dict):
                problems.append(f"경로 {path!r}의 정의가 객체가 아니다")
                continue
            for method, body in item.items():
                if method.upper() not in ("GET", "POST"):
                    continue          # 우리가 부를 수 없는 메서드는 알 필요도 없다
                if not isinstance(body, dict):
                    problems.append(f"{method.upper()} {path}의 정의가 객체가 아니다")
                    continue
                try:
                    operations[f"{method.upper()} {path}"] = _operation(
                        method.upper(), path, body, components)
                except Exception as exc:            # noqa: BLE001 — 남의 JSON(계약)
                    problems.append(f"{method.upper()} {path} 파싱 실패 — "
                                    f"{type(exc).__name__}: {exc}")
    except Exception as exc:                        # noqa: BLE001 — 최후의 그물
        problems.append(f"명세 파싱 실패 — {type(exc).__name__}: {exc}")
    return TargetApi(digest=digest, operations=operations, problems=problems)


def load_target_api(knowledge_root: Path, gbm: str, fct: str) -> tuple[TargetApi | None, list[str]]:
    """`knowledge/target_api/{gbm}/{fct}.json`을 읽는다. 없으면 `(None, [])`.

    **없는 것은 오류가 아니다** — 명세를 얻을 수 없는 대상도 있고, pin은 선택이다.
    깨진 채 있는 것이 오류다. `load_deployment`가 `None`만 돌려주는 것과 달리
    문제 목록을 함께 돌려주는 이유가 그것이다.
    """
    path = knowledge_root / "target_api" / gbm / f"{fct}.json"
    if not path.exists():
        return None, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [f"pinned 명세를 읽을 수 없다 ({path}): {exc}"]
    api = parse_spec(raw)
    return api, [f"pinned 명세: {p}" for p in api.problems]


def _declared_schema(entry) -> dict:
    """등재 항목이 실제로 검증에 쓰는 스키마. `query_rules.entry_schema`와 같은 식.

    같은 판정을 두 곳에서 다르게 쓰면 갈라진다(계획 7에서 claim의 두 구현이 갈라져
    프로덕션 버그를 테스트가 못 잡은 일이 실제로 있었다). 여기서 import하지 않는
    이유는 방향뿐이다 — `query_rules`는 infrastructure이고 이 모듈은 knowledge다.
    """
    return entry.query_schema if entry.method == "GET" else entry.body_schema


def spec_problems(entries: dict, api: TargetApi) -> list[str]:
    """등재 항목을 pinned 명세와 대조한다. I/O 없는 순수 함수.

    기동 검증과 `--live` 드리프트가 **같은 함수를 공유한다** — 판정이 두 곳에서
    갈리면 프로덕션 버그를 테스트가 못 잡는다(계획 8의 `entry_call_problems`가
    세운 형태).

    **명세에만 있는 키는 문제가 아니다.** 우리는 필요한 것만 등재한다. 그것을
    문제로 삼는 순간 명세가 우리 스키마를 넓히는 압력이 되고, 이 모듈의 방향이
    통째로 뒤집힌다. 이 함수는 `entries`를 읽기만 하고 절대 수정하지 않는다.
    """
    problems: list[str] = []
    for name in sorted(entries):
        entry = entries[name]
        key = f"{entry.method} {entry.path}"
        op = api.operations.get(key)
        if op is None:
            problems.append(f"등재 항목 {name!r}({key})가 pinned 명세에 없다 — "
                            f"오타이거나 대상이 제거했다")
            continue
        schema = _declared_schema(entry)
        for field in sorted(schema):
            if field in op.unknown_props:
                # 명세가 우리 어휘 밖 타입을 썼다는 뜻이지 우리가 틀렸다는 뜻이
                # 아니다. 검증을 건너뛸 뿐 사람을 탓하지 않는다.
                continue
            declared = op.props.get(field)
            if declared is None:
                problems.append(f"등재 항목 {name!r}의 스키마 키 {field!r}가 "
                                f"명세({key})에 없다")
            elif declared != schema[field]:
                problems.append(f"등재 항목 {name!r}의 {field!r} 타입이 명세와 다르다 "
                                f"(우리: {schema[field]}, 명세: {declared})")
        for field in op.required:
            if field not in schema:
                problems.append(f"명세가 필수라고 한 {field!r}가 등재 항목 "
                                f"{name!r}의 스키마에 없다 — 보낼 수단이 없다")
    return problems


def response_field_problems(checks: dict, entries: dict, api: TargetApi) -> list[str]:
    """rule 점검이 보는 응답 필드가 명세에 실재하는지 확인한다.

    **최상위 키만 본다.** `body.badge.0`처럼 깊이 들어가는 경로는 명세의 중첩
    스키마를 다 따라가야 하고, 그 정확도를 확보하기 전에는 거짓 오류가 더 비싸다.

    **명세가 침묵한 자리에서는 아무 말도 하지 않는다**(`response_props is None`).
    필드가 런타임에 없는 것은 데이터 이상이라 finding이 맞고, 여기서 잡는 것은
    **오타**다 — 명세가 "그런 필드는 원래 없다"고 말할 때만 config 오류로 올린다.
    """
    problems: list[str] = []
    for name in sorted(checks):
        check = checks[name]
        target = check.target or ""
        kind, _, rest = target.partition(":")
        if kind != "rest" or not rest or rest.startswith("/"):
            continue                      # 토폴로지 locator는 pin이 덮지 않는다
        entry = entries.get(rest)
        if entry is None:
            continue                      # 미등재 참조는 boot의 다른 검사가 잡는다
        op = api.operations.get(f"{entry.method} {entry.path}")
        if op is None or op.response_props is None:
            continue
        field = check.params.get("field") if isinstance(check.params, dict) else None
        if not isinstance(field, str) or not field.startswith("body."):
            continue
        top = field[len("body."):].split(".")[0]
        if top and top not in op.response_props:
            problems.append(f"점검 {name!r}이 보는 {field!r}가 명세의 응답에 없다 "
                            f"(명세가 말한 최상위 키: {op.response_props})")
    return problems
