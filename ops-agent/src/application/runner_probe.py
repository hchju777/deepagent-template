"""태스크가 **선언한** 읽기 하나를 실제 포트로 수행한다. LLM이 없다.

## 왜 action이 닫힌 목록인가

규율 9와 같은 이유다. `run(port, method, args)` 같은 표면을 두면 "어느 포트의 어느
메서드를 어떤 인자로 부르라"가 표현 가능해지고, 그 결정이 config에서 코드로,
결국 **LLM의 판단으로** 흘러간다. 10b에서 태스크를 만드는 것은 LLM이다.

목록에 없으면 문이 안 열린다. `mongo.aggregate`는 부를 수 없다 — 포트에 없기도
하지만, 여기에도 없다.

## 왜 인자를 소켓 전에 검사하는가

`redis.get`에 `{"pattern": "oee:*"}`가 오면 어댑터는 `TypeError`를 던진다. 그건
"대상 시스템의 실패"가 아니라 **우리가 잘못 부른 것**인데, 흡수해서 `status="error"`로
돌려주면 보고서에 "Redis 조회 실패"라고 적힌다. 원인이 우리라는 사실이 지워진다.

그래서 **포트에 닿기 전에** 거부하고, 이유를 그대로 적는다.

## 증거는 실제 결과에서만 만든다

`ProbeResult`의 봉투를 그대로 물려받는다 — 특히 `complete`. 표본이 잘렸는데 완전한
척하면 12a의 verify가 "없음"을 근거로 한 결론을 못 걸러낸다.
"""
from typing import Any

from src.domain.case import Case, EvidenceRef, PlanTask
from src.domain.envelope import ProbeResult
from src.domain.investigation import TaskOutcome, TaskRunnerPort

# action 이름 → (어댑터 이름, 메서드, 필수 인자, 선택 인자)
#
# 쓰는 메서드가 하나도 없는 것은 우연이 아니다 — 포트에 없으므로 여기 적을 수도 없다.
ACTIONS: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {
    "redis.get":           ("redis", "get",           ("key",),               ()),
    "redis.scan":          ("redis", "scan",          ("pattern",),           ()),
    "redis.ttl":           ("redis", "ttl",           ("key",),               ()),
    "mongo.find":          ("mongo", "find",          ("collection", "filter"),
                            ("sort", "limit", "projection")),
    "mongo.count":         ("mongo", "count",         ("collection", "filter"), ()),
    "kafka.group_offsets": ("kafka", "group_offsets", ("group",),             ()),
    "kafka.tail":          ("kafka", "tail",          ("topic",),             ("limit",)),
    "rest.query":          ("rest",  "query",         ("entry", "params"),    ()),
}


def _reject(task: PlanTask, reason: str) -> TaskOutcome:
    return TaskOutcome(task_id=task.id, status="error", error=reason)


class ProbeRunner(TaskRunnerPort):
    """사이트 하나의 어댑터 묶음에 붙는다."""

    def __init__(self, adapters):
        self._adapters = adapters

    def describe(self) -> str:
        return f"probe({', '.join(self._adapters.available()) or '없음'})"

    async def run(self, task: PlanTask, *, case: Case) -> TaskOutcome:
        if not task.action:
            return _reject(task, "action이 없다 — ProbeRunner는 태스크가 선언한 읽기만 한다")
        spec = ACTIONS.get(task.action)
        if spec is None:
            return _reject(task, f"미등재 action — {task.action} "
                                 f"(등재: {', '.join(sorted(ACTIONS))})")
        adapter_name, method_name, required, optional = spec

        unknown = sorted(set(task.params) - set(required) - set(optional))
        if unknown:
            return _reject(task, f"{task.action}이 모르는 인자 — {', '.join(unknown)}")
        missing = [name for name in required if name not in task.params]
        if missing:
            return _reject(task, f"{task.action}에 필요한 인자가 없다 — {', '.join(missing)}")

        adapter = getattr(self._adapters, adapter_name, None)
        if adapter is None:
            return _reject(task, f"{case.site}에 {adapter_name} 어댑터가 없다 — config가 선언하지 않았다")

        args, kwargs = self._split_args(method_name, required, optional, task.params)
        try:
            result: ProbeResult = await getattr(adapter, method_name)(*args, **kwargs)
        except Exception as exc:                                    # noqa: BLE001
            # 포트는 던지지 않기로 돼 있다. 그래도 잡는 이유는 nodes.execute와 같다 —
            # 계약을 어기는 구현 하나가 라운드 전체를 지우면 안 된다.
            return _reject(task, f"{task.action} 호출이 던졌다 — {type(exc).__name__}: {exc}")

        source = f"{task.action} {self._describe_params(task.params)}".strip()
        if result.status == "error":
            return _reject(task, f"{source} — {result.error}")

        ref = EvidenceRef(
            id=EvidenceRef.make_id(task.id, 1),
            source=source,
            summary=_summarize(result.data),
            as_of=result.envelope.observed_at,
            complete=result.envelope.complete)
        note = "" if result.envelope.complete else f" (표본이 잘렸다: {result.envelope.truncated_reason})"
        return TaskOutcome(task_id=task.id, status="ok",
                           summary=f"{source} → {ref.summary}{note}", evidence=[ref])

    @staticmethod
    def _split_args(method: str, required, optional, params: dict):
        """포트의 시그니처대로 위치 인자와 키워드 인자를 가른다.

        포트가 `find(collection, filter, *, sort=...)`처럼 키워드 전용을 쓰므로
        전부 키워드로 넘길 수 없다 — 이름이 맞아도 `TypeError`가 난다.
        """
        args = [params[name] for name in required]
        kwargs = {name: params[name] for name in optional if name in params}
        return args, kwargs

    @staticmethod
    def _describe_params(params: dict) -> str:
        return " ".join(f"{k}={v!r}" for k, v in sorted(params.items()))


_SUMMARY_CHARS = 160


def _summarize(data: Any) -> str:
    """증거 한 줄에 실을 요약.

    `repr`인 이유는 **개행을 이스케이프하기 위해서**다. 대상 데이터는 여러 줄이
    정상인데, 날것으로 프롬프트에 실리면 증거 목록 블록에 가짜 항목이 붙는다
    (9e의 `fenced()`와 같은 위험이고, 여기는 11b에서 프롬프트에 들어간다).
    """
    if isinstance(data, list):
        return f"{len(data)}건 {repr(data)[:_SUMMARY_CHARS]}"
    return repr(data)[:_SUMMARY_CHARS]
