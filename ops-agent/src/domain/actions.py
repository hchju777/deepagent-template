"""**우리가 대상에게 할 수 있는 읽기의 전부.** 이름으로 부르고, 목록에 없으면 문이 안 열린다.

## 왜 한 곳인가

이 표를 쓰는 곳이 둘이다 — 순찰의 프로브(4단계)와 조사의 `ProbeRunner`(10a). 각자
자기 표를 들면 언젠가 한쪽만 넓어지고, **넓은 쪽이 곧 우리 허용 범위**가 된다.
어댑터 조립을 `factory.py` 한 곳에 둔 것과 같은 이유다.

## 왜 `run(port, method, args)`가 아닌가

규율 9다. "어느 포트의 어느 메서드를 어떤 인자로 부르라"가 표현 가능해지면, 그 결정이
config에서 코드로, 결국 **LLM의 판단으로** 흘러간다(10b부터 태스크를 LLM이 만든다).
`mongo.aggregate`는 부를 수 없다 — 포트에 없기도 하지만, **여기에도 없다.**

쓰는 메서드가 하나도 없는 것은 우연이 아니다. 포트에 없으므로 여기 적을 수도 없다.

## 왜 인자를 소켓 전에 검사하는가

`redis.get`에 `{"pattern": ...}`가 오면 어댑터는 `TypeError`를 던진다. 그건 "대상
시스템의 실패"가 아니라 **우리가 잘못 부른 것**인데, 흡수해서 error로 돌려주면 보고서에
"Redis 조회 실패"라고 적힌다 — 원인이 우리라는 사실이 지워진다.

## 왜 domain에 있는가

계층 규칙이 `application → domain ← infrastructure`다. 이 표를 patrol이나
infrastructure에 두면 application이 그쪽을 import하게 되어 화살표가 하나 늘어난다.
여기 있는 것들은 포트 이름과 메서드 이름뿐이고 실구현은 모르므로 domain에 닫힌다 —
`adapters`는 덕 타이핑으로 받는다.
"""
from typing import Any

from src.domain.base import Clock
from src.domain.envelope import ProbeResult

# action 이름 → (어댑터 속성, 메서드, 필수 인자, 선택 인자)
ACTIONS: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {
    "redis.get":           ("redis", "get",           ("key",),                ()),
    "redis.scan":          ("redis", "scan",          ("pattern",),            ()),
    "redis.ttl":           ("redis", "ttl",           ("key",),                ()),
    "mongo.find":          ("mongo", "find",          ("collection", "filter"),
                            ("sort", "limit", "projection")),
    "mongo.count":         ("mongo", "count",         ("collection", "filter"), ()),
    # 발견용. 인자가 없는 것이 정상이다 — "이 DB에 무엇이 있나"에는 물을 것이 없다.
    "mongo.list_collections": ("mongo", "list_collections", (), ()),
    "kafka.list_topics":      ("kafka", "list_topics",      (), ()),
    "kafka.group_offsets": ("kafka", "group_offsets", ("group",),              ()),
    "kafka.tail":          ("kafka", "tail",          ("topic",),              ("limit",)),
    # `params`가 두 번 나오는 것은 포트 시그니처 그대로다 — `query(entry, params)`.
    # 이름을 바꾸면 표와 포트가 갈라지고, 갈라진 것을 아무도 안 본다.
    "rest.query":          ("rest",  "query",         ("entry", "params"),     ()),
    # ── 대상 코드(11a). **레포·커밋·법인은 여기 인자에 없다** — 리드가 고를 값이
    # 아니기 때문이다. 리드가 SHA를 대게 하면 모르는 것을 지어내고, 우리는 떠 있지도
    # 않은 코드를 읽는다. 서비스 이름만 받고 나머지는 `DeployedCode`가 정한다.
    "code.services":       ("code",  "services",      (),                      ()),
    # 이름이 사는 config는 **층으로 갈린다.** 하나만 읽으면 덮어쓴 값을 사실로
    # 단정하므로, 이 action은 층 전부를 합친 결과를 돌려준다(`code.read`와 다르다).
    "code.config":         ("code",  "config",        ("service",),            ()),
    "code.grep":           ("code",  "grep",          ("patterns",),           ("service",)),
    # `path`는 **`code.grep`이 돌려준 경로**다. 지어내는 자리가 아니다.
    "code.read":           ("code",  "read",          ("service", "path"),     ()),
}


# 인자를 받지 않는 action들 — "이 DB에 무엇이 있나"에는 물을 것이 없다.
# 목록으로 두는 이유: "필수 인자가 없다"가 실수인지 의도인지 표가 말해야 한다.
NO_ARGS = frozenset({"mongo.list_collections", "kafka.list_topics",
                     "code.services"})

# **대상에서 찾아야 아는 이름**이 들어가는 인자. 여기 적힌 인자에 값을 대려면
# 먼저 `list_collections`·`list_topics`·`scan`으로 찾았어야 한다([decisions ⑮]).
#
# 안 찾고 찍으면 빈 결과가 오는데, 그건 "데이터가 없다"가 아니라 "질문을 잘못했다"다.
# 둘은 완전히 다른 사실이고 **판정이 둘을 구별 못 하면 없는 이상을 보고한다.**
# `nodes._accept_tasks`가 이 목록으로 "찾지 않고 댄 이름"을 기록한다.
#
# `entry`가 빠진 것은 우연이 아니다 — REST 등재 항목은 **config가 선언**하므로
# 찾을 것이 없다. `pattern`도 빠진다 — `*`가 정상적인 값이라 "찾았는가"를 물을 수 없다.
DISCOVERED_ARGS = frozenset({"collection", "topic", "key", "group"})


def action_problem(action: str, params: dict) -> str | None:
    """등재·인자 검사. 통과하면 None. **소켓에 나가기 전에 부른다.**"""
    spec = ACTIONS.get(action)
    if spec is None:
        return f"미등재 action — {action} (등재: {', '.join(sorted(ACTIONS))})"
    _, _, required, optional = spec
    unknown = sorted(set(params) - set(required) - set(optional))
    if unknown:
        return f"{action}이 모르는 인자 — {', '.join(unknown)}"
    missing = [name for name in required if name not in params]
    if missing:
        return f"{action}에 필요한 인자가 없다 — {', '.join(missing)}"
    return None


def describe(action: str, params: dict) -> str:
    """증거와 로그에 남는 한 줄 — **무엇을 물었는가**.

    응답만 보관하면 "0건"이 "현장이 멈췄다"인지 "질문을 잘못 던졌다"인지 구별할 수 없다.
    """
    rendered = " ".join(f"{k}={v!r}" for k, v in sorted(params.items()))
    return f"{action} {rendered}".strip()


async def run_action(adapters: Any, action: str, params: dict, *,
                     clock: Clock) -> ProbeResult:
    """등재된 읽기 하나를 수행한다. **절대 raise하지 않는다.**"""
    source = describe(action, params)
    problem = action_problem(action, params)
    if problem is not None:
        return ProbeResult.failed(problem, source=source, clock=clock)

    adapter_name, method_name, required, optional = ACTIONS[action]
    adapter = getattr(adapters, adapter_name, None)
    if adapter is None:
        return ProbeResult.failed(
            f"{adapter_name} 어댑터가 없다 — config가 선언하지 않았다",
            source=source, clock=clock)

    # 포트가 `find(collection, filter, *, limit=...)`처럼 키워드 전용을 쓰므로
    # 전부 키워드로 넘길 수 없다 — 이름이 맞아도 TypeError가 난다.
    args = [params[name] for name in required]
    kwargs = {name: params[name] for name in optional if name in params}
    try:
        return await getattr(adapter, method_name)(*args, **kwargs)
    except Exception as exc:                                        # noqa: BLE001
        # 포트는 던지지 않기로 돼 있다. 그래도 잡는 이유: 계약을 어기는 구현 하나가
        # 순찰 라운드나 조사 라운드 전체를 지우면 안 된다.
        return ProbeResult.failed(f"호출이 던졌다 — {type(exc).__name__}: {exc}",
                                  source=source, clock=clock)
