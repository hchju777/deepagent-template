"""조사 사건 하나를 이루는 것들 — 케이스·가설·계획 태스크·증거 참조.

## 왜 수명주기 필드가 모델에 있는가

`PlanTask.status`는 LLM이 만드는 객체 안에 있지만 **LLM이 정하지 않는다.** frame이
`{"status": "ok", "result_summary": "확인함"}`을 실어 보내면 그 태스크는 실행되지 않은
채로 "끝난 것"이 되고, select 게이트를 통째로 우회한다. 필드를 빼면 그 구멍이
막히는 것처럼 보이지만, 그러면 코드도 상태를 못 적는다.

그래서 필드는 두고 **들어오는 길목에서 코드가 덮어쓴다**(`nodes._sanitize_task`).
경계가 어디인지를 한 곳으로 모으는 쪽이, 모델을 쪼개 놓고 "여기는 안 믿는다"를
여러 곳에 적는 것보다 안 틀린다.

## 증거 id는 왜 태스크 id에서 파생되는가

`ev-1`, `ev-2`처럼 전역 순번을 쓰려면 번호를 나눠 주는 곳이 하나 있어야 한다.
그런데 태스크는 **한 라운드에 여러 개가 동시에** 실행되고, 병렬 가지들은 서로의
State를 못 본다 — 공유 카운터를 두면 같은 번호가 두 번 나가거나, 실행 순서에 따라
번호가 달라져서 **같은 입력에 같은 결과가 안 나온다.**

`t-1.e1`처럼 태스크 id에서 파생하면 나눠 줄 것이 없어 충돌이 불가능하고, 덤으로
"이 증거가 어느 태스크에서 나왔나"가 id에 적혀 있다.
"""
from datetime import datetime
from typing import Literal

from pydantic import model_validator

from src.domain.base import StrictModel

# 서브에이전트 역할(11b). 10a는 이 값을 읽지 않지만, 태스크에 역할이 없으면
# frame이 "무엇을 시킬지"를 표현할 방법이 없어 계획이 라운드마다 달라진다.
Role = Literal["data_prober", "code_tracer", "recompute_verifier"]

TaskStatus = Literal["pending", "running", "ok", "error", "cancelled"]


class EvidenceRef(StrictModel):
    """State에 남는 증거 한 줄. **실제로 일어난 읽기만 여기 올라온다.**"""

    id: str
    source: str                       # 무엇을 물었는가 — "redis.get key=oee:L3"
    # 사람이 볼 **한 줄**. CLI의 태스크 행과 증거 행이 이걸 쓴다.
    summary: str
    # 리드가 **판단할 재료**. 둘을 한 필드로 쓰면 안 된다 — 적정 길이가 10배 다르고,
    # 짧은 쪽에 맞추면 리드가 자기가 읽은 것의 내용을 못 본다.
    #
    # 실제로 그랬다: 요약 160자에 제조 문서 한 건이 258자라, `alarm`·`caution`·
    # `normal`(조사가 확인하려던 바로 그 필드)이 잘려 나갔다. 리드는 매 라운드
    # 올바른 후속 질문을 했는데 **매번 같은 못 읽을 답**을 받아 같은 질의를 반복했다.
    body: str = ""
    as_of: datetime | None = None
    # 표본이 잘렸는가. 잘린 표본으로는 "없다"를 주장할 수 없다 — 12a의 verify가 본다.
    complete: bool = True

    @staticmethod
    def make_id(task_id: str, n: int) -> str:
        """`t-1.e1`. 모듈 맨 위 설명 참고 — 전역 카운터를 두지 않기 위해서다."""
        return f"{task_id}.e{n}"


class Hypothesis(StrictModel):
    id: str
    statement: str
    status: Literal["open", "supported", "refuted"] = "open"
    supporting_ids: list[str] = []
    refuting_ids: list[str] = []


class PlanTask(StrictModel):
    """"무엇을 볼 것인가" 하나."""

    id: str
    goal: str
    role: Role
    # 실행기가 읽는 등재 항목 이름과 인자(`ProbeRunner`). `role`은 여기서 코드가 정한다
    # (`actions.role_for`) — 서브에이전트가 스스로 도구를 고르는 구조가 아니라, action의
    # 종류가 곧 레인이다(decisions ⑰).
    action: str | None = None
    params: dict = {}
    # select 게이트: 여기 적힌 id가 **전부** State에 실재해야 실행 가능하다.
    # 재계산 태스크는 앞선 라운드의 증거를 입력으로 받으므로 이게 없으면
    # frame의 1차 계획이 라운드 1에 통째로 발사돼 입력 없이 전멸한다.
    input_evidence_ids: list[str] = []
    priority: int = 100                   # 낮을수록 먼저, 동률이면 FIFO
    status: TaskStatus = "pending"
    result_summary: str | None = None
    result_evidence_ids: list[str] = []
    error: str | None = None
    # 코드가 지정한 다음 홉을 리드가 그대로 냈다(R3-1c ②) — 브리핑이 이 태스크의 증거 본문을 예산에서 빼지 않는다. 받을 때 코드가 단다;
    # 리드가 실어 보낸 값은 수명주기 필드처럼 지운다(`_sanitize_task`).
    pinned: bool = False
    # 걷기 노드의 읽기다(R3-3 ② → R3-4 ②). id는 리드와 같은 한 단계(`t-2`…) — 두 단계(`t-1-N`)였을 때 리드가 `t-1`을 다시 냈다(측정 #10 P1 5).
    # 브리핑이 이 플래그로 접고, 중복 거부가 "입구가 이미 읽었다"로 센다. 리드가 실어 보낸 값은 지운다.
    walked: bool = False


class TargetPick(StrictModel):
    """접수 끝점의 대상 행 → 대상 키 매핑 결과(R3-1 ①). **frame 전에 코드가 읽고 대조해 정한다** — LLM도, 사람의 선언도 아니다.

    고른 키가 있으면 `basis`에 근거(어느 증거의 값 어느 자리에 identity가 그대로 있었나)가 있고, 없으면 `problem`에 이유가 있다.
    둘 다 케이스 블록에 문장으로 실린다 — 순서는 리드가 못 읽는 신호다(사내 측정 #5).
    """
    key: str | None = None
    basis: str = ""
    candidates: list[str] = []
    problem: str = ""
    # 후보 읽기의 집계(R3-1b ①) — 읽은 후보 수, 없는 키 이름, 읽기 실패 수, identity가 든 키 수. 본문은 State에 없다(모듈 설명).
    read: int = 0
    absent: list[str] = []
    failed: int = 0
    hits: int = 0
    # 고른 키가 나온 선언 이름(템플릿 `alarm:stats:{line}` 또는 리터럴) — 다음 홉 `code.uses`는 인덱스가 아는 이 이름으로 묻는다(R3-1b ③).
    declared: str = ""
    # identity가 든 객체 옆의 빈 목록·빈 객체 경로들(`record[0].data`) — 코드가 보는 사실(사내 c-1의 모양). 판정은 `intake_map.empties` 하나다
    # (R3-5b ②) — 걷기의 다른 홉과 같다.
    empty: list[str] = []
    empty_objects: list[str] = []
    # 쓴 쪽(R3-1c ①-a) — 값의 문자열 잎이 인덱스의 클래스 이름과 완전 일치한 그 클래스와 자리(`metadata.event_type`). 둘 이상 다른
    # 클래스면 `writer_problem`. 인덱스가 없으면 셋 다 비어 있고 아무 말도 안 한다.
    writer: str = ""
    writer_at: str = ""
    writer_problem: str = ""
    # 클래스 이름 집합을 **받고도** 못 골랐나 — 받지 않았으면(인덱스 없음) "모른다"를 말할 자격이 없다.
    writer_searched: bool = False
    # 키를 만든 메시지의 포인터(R3-5d ④) — 후보 읽기의 전체 값 안 `{"topic", "partition", "offset", "at"}`(사내 `metadata.source`, 측정 #13 추가분 2: 법인 둘
    # 122/122키). 없으면 빈 dict — 걷기가 "포인터 없음"이라 적는다. 좁힌 다시 읽기엔 형제 자리가 빠지니 전체 값에서(쓴 쪽과 같은 이유).
    pointer: dict = {}

    def line(self) -> str:
        if self.key:
            return f"대상 키: {self.key} — {self.basis}"
        return f"대상 키: 못 골랐다 — {self.problem}" if self.problem else ""

    def writer_line(self) -> str:
        if self.writer:
            return f"쓴 쪽: {self.writer} — 값의 {self.writer_at}과 인덱스의 클래스 이름이 완전 일치"
        if self.writer_problem:
            return f"쓴 쪽: 못 골랐다 — {self.writer_problem}"
        return "쓴 쪽: 모른다 — 값에 인덱스의 클래스 이름이 없다" if self.writer_searched else ""

    def fold_line(self) -> str:
        """후보 읽기를 접은 한 줄 — 증거 29줄 대신. 후보를 하나도 안 읽었으면 빈 줄."""
        if not self.read:
            return ""
        present = self.read - len(self.absent) - self.failed
        return (f"후보 {self.read}개 읽음 — 있음 {present} · 없음 {len(self.absent)} · identity 일치 {self.hits}"
                + (f" · 읽기 실패 {self.failed}" if self.failed else "") + (f" (대상 키 {self.key})" if self.key else ""))


class Hop(StrictModel):
    """걷기의 표 한 행(R3-3 ②) — 코드가 읽은 키 하나와 그 상태. `writer`는 그 키 값의 잎이 댄 클래스(`writer_of`), 없으면 빈 문자열.
    `bad`는 없음·빈 배열·0행뿐(사람 결정 10-14 — 손 규칙 없음)."""
    key: str
    kind: str = "rediskey"                  # rediskey·collection·topic, 포인터 메시지 홉은 "message"(키는 `토픽[파티션]@오프셋`, R3-5d ④)
    depth: int = 0
    writer: str = ""
    service: str = ""
    # 판정의 component 후보 서비스들(R3-4 ①) — 걷기가 가른 레포의 서비스 전부(한 레포를 여러 서비스가 공유하면 여럿). `service`는 표에 적는
    # 글자(`<레포>(서비스 N개 공유)`)라 거기서 서비스를 되읽지 않는다.
    candidates: list[str] = []
    status: str = ""
    bad: bool = False
    evidence_id: str = ""
    basis: str = ""
    # 여기로 오게 한 홉의 증거 id(R3-4 ②) — 멈춘 홉까지의 사슬(`Walk.chain_ids`)을 되짚는다. 대상 키 홉은 빈 문자열.
    parent: str = ""

    def line(self) -> str:
        # 메시지 홉(R3-5d ④)은 쓴 클래스가 없고 발행 쪽이 config 선언이라 `service`만 있다 — 그걸 쓴 쪽 칸에 적는다.
        who = (self.writer + (f" [{self.service}]" if self.service else "")) if self.writer else (self.service or "—")
        return f"  {self.key} | {who} | {self.status} | {self.evidence_id or '—'}"


class FilterHit(StrictModel):
    """코드 줄 하나(R3-4 ③) — 필드와 값이 한 비교식의 양쪽에 리터럴로 든 줄(`x["f"] != "v"`; `field`·`value`가 그 짝), 또는 이은 컴포넌트 **클래스**의
    자리(R3-5 ③ — 등록 줄이 아니라 등록이 가리키는 클래스 정의 줄; 그 줄을 `code.read line=`에 주면 클래스 본문이 온다)."""
    path: str
    line: int
    text: str
    repo: str = ""
    field: str = ""
    value: str = ""


class FilterScan(StrictModel):
    """걷기의 단일값 검사 — 어댑터 안에서 한 조회의 결과(R3-4 ③, 사람 결정 10-14). 범위 = 그 입력을 읽는 클래스의 파일 + `get("X")`(리터럴)↔
    `register(component="X")`로 이은 컴포넌트 파일(config 제외). runner를 안 거치므로 읽기 상한·증거 밖이다 — 표에 적히는 것은 줄의 자리와 본문."""
    hits: list[FilterHit] = []               # 필드 값이 든 비교식 줄들(파일 차례) — 어느 필드·값인지 함께
    checked: list[str] = []                  # 대조한 필드들(N) — 조회가 안 되면 비어 있다(일부만 검사 0/M)
    files: list[str] = []                    # 본 파일들 — 클래스 파일 먼저, 그 뒤 이은 컴포넌트 클래스 파일
    linked: list[str] = []                   # 이은 컴포넌트 이름(리터럴)
    linked_at: list[FilterHit] = []          # 이은 컴포넌트 **클래스**의 자리(나쁜 필드 이름이 리터럴로 든 파일 먼저) — 뒤 칸
    unlinked: list[str] = []                 # 못 이은 것 — `변수 x`·`X: Cls(인덱스에 없음)`·`X(이 사이트의 등록 없음)`. 하나라도 있으면 "일부만 검사"
    problem: str = ""                        # 조회 자체가 안 됐을 때(인덱스·클래스·스냅샷) — "없음"과 다르다


class Rung(StrictModel):
    """걷기가 멈춘 자리의 다음 칸 하나(R3-4 ②) — 리드가 낼 질의와 그 입력 증거. 사다리가 한 라운드에 한 칸씩 밟는다(`briefing._ladder_step`)."""
    action: str
    params: dict = {}
    evidence: str = ""


class Walk(StrictModel):
    """코드가 상류를 걸은 결과(R3-3 ②) — 홉 표, 멈춘 이유, 멈춘 자리의 다음 칸들. 브리핑의 다음 홉과 중복 거부의 홉은 입구 홉이 아니라 **이것**을
    본다(측정 #9: 입구 홉에 고정돼 있어 리드가 코드가 이미 읽은 자리로 또 갔다)."""
    hops: list[Hop] = []
    reads: int = 0
    seconds: float = 0.0
    stopped: str = ""
    # 멈춘 자리 — **가장 깊은 나쁜 홉**의 증거 id(R3-4 ②; 측정 #10 P0 3: "마지막 실패"는 다른 가지의 일이었다). 나쁜 홉이 없으면 대상 키 홉.
    stop_at: str = ""
    # 멈춘 자리의 다음 칸들, 차례대로 — 리터럴 줄의 함수 → 그 자리의 질의(`code.uses`·`code.resources`·읽는 메서드 전체). 늘 하나는 두려 한다(없으면
    # frame 예시가 네 칸으로 돌아간다 — 측정 #10 P0 3).
    rungs: list[Rung] = []
    # 표 아래 주 — 같은 클래스 이름이 두 레포일 때 어느 서비스로 갈랐나(근거), 못 갈랐으면 둘 다 걸었다는 사실(R3-3 ②-b).
    notes: list[str] = []

    def next_hop(self) -> tuple[str, dict] | None:
        return (self.rungs[0].action, dict(self.rungs[0].params)) if self.rungs else None

    @property
    def next_evidence(self) -> str:
        return self.rungs[0].evidence if self.rungs else ""

    def bad_ids(self) -> frozenset[str]:
        return frozenset(h.evidence_id for h in self.hops if h.bad and h.evidence_id)

    def chain_ids(self) -> frozenset[str]:
        """멈춘 홉에서 대상 키 홉까지의 증거 id들 — pinned 본문은 이 사슬만(측정 #10 P0 4: 나쁜 홉 전부를 고정하니 r1이 22~28K였다)."""
        by_id: dict[str, Hop] = {}
        for h in self.hops:
            by_id.setdefault(h.evidence_id, h)
        ids: set[str] = set()
        cur = by_id.get(self.stop_at) if self.stop_at else None
        while cur is not None and cur.evidence_id and cur.evidence_id not in ids:
            ids.add(cur.evidence_id)
            cur = by_id.get(cur.parent) if cur.parent else None
        return frozenset(ids)

    def lines(self) -> list[str]:
        if not self.hops and not self.stopped:
            return []
        head = [f"걷기(코드가 상류를 걸었다): 홉 {len(self.hops)} · 읽기 {self.reads} · {self.seconds:.1f}초 — 멈춤: {self.stopped}"]
        if not self.hops:
            # 던진 걷기(홉 0)도 보인다(측정 #11 P0 2) — 사유 한 줄이 없으면 리드도 사람도 걷기가 있었는지 모른다.
            return head
        return head + ["  키 | 쓴 쪽 | 상태 | 증거"] + [h.line() for h in self.hops] + [f"  주: {note}" for note in self.notes]


class Case(StrictModel):
    """조사 사건 하나.

    `origin`이 두 입구(사람·순찰)를 가르는 **유일한** 필드다. 나머지 경로가 같아야
    원인 판정 로직이 한 벌로 유지된다.
    """

    id: str
    gbm: str
    fct: str
    origin: Literal["human", "patrol"]
    symptom: str
    t0: datetime
    # 순찰이 연 케이스의 출발점 — 어느 점검, 어느 대상에서 왔나. 사람이 연 케이스는 None.
    # 브리핑이 config에서 점검 → 판정이 본 프로브 → REST 항목 → path를 되짚어 리드에게 "증상이
    # 관찰된 자리"(사다리의 첫 칸)를 준다. 여기엔 이름만 두고 해석은 briefing이 한다.
    check: str | None = None
    target: str | None = None

    @property
    def site(self) -> str:
        return f"{self.gbm}/{self.fct}"


# ── 판정 (12a) ──────────────────────────────────────────────────────
#
# `degraded`는 **코드만 찍는 낙인**이다 — "조사가 안 돌았다"(리드 LLM 실패·증거 0건·판정을 못
# 받음)를 "조사했는데 못 가렸다"(`inconclusive`)와 가르기 위한 값이라, 리드가 고르면 뜻이
# 사라진다. 리드가 낼 수 있는 집합은 `lead.ConcludeReply`가 이 하나를 뺀 것이다.
VerdictType = Literal["logic_bug", "data_loss", "config_error", "stale_data",
                      "external", "inconclusive", "degraded"]
Confidence = Literal["high", "medium", "low"]


class CauseLink(StrictModel):
    """인과 사슬의 다리 하나 — 부품과 그 근거.

    `component`는 토폴로지의 서비스 이름이거나 **리드가 본 증거에 나온 자원 이름**이어야 한다.
    verify가 그걸 검사한다 — 태스크의 "찾지 않고 이름을 댔다"가 판정에서는 "없는 부품을
    가리켰다"가 되고, 보고서가 그 이름을 그대로 싣기 때문이다.
    """

    component: str
    evidence_ids: list[str]
    # 기여 요인: 근본 원인과의 관계 / 후보: 왜 후보이고 왜 최상위가 아닌가.
    relation: str | None = None
    # 후보(alternates) 전용. 최상위의 신뢰도는 `Verdict.confidence` 하나다 — 둘 다 두면
    # 보고서가 두 값을 보인다. root_cause·contributing에서는 코드가 None으로 만든다.
    confidence: Confidence | None = None


class Verdict(StrictModel):
    verdict_type: VerdictType
    root_cause: CauseLink | None = None
    alternates: list[CauseLink] = []          # 최상위 다음의 후보들, 유력한 순
    contributing: list[CauseLink] = []
    confidence: Confidence
    recommendations: list[str] = []
    caveats: list[str] = []
    narrative: str

    @model_validator(mode="after")
    def _conclusive_needs_root_cause(self):
        if self.verdict_type not in ("inconclusive", "degraded") and self.root_cause is None:
            raise ValueError("결론이 있는 판정에는 root_cause가 필요하다")
        return self
