"""R3-5k ④ — 판정 턴 A/B(측정 #21 요청): 걷기의 병합 주 줄을 넣은 판·뺀 판을 판정 LLM에 묻는 측정 명령의 순수한 부분."""
import json

from src.application.walk import run_walk
from src.domain.case import FilterScan, MergeHit
from src.infrastructure.llm_fakes import ScriptedAdapter
from tests.application.conftest import T0
from tests.application.test_walk import _Lookup, _Reads, _res, _state, _value

REPO_NOTE = "  주: PaletteLoader는 레포 2곳(api, dt-core)에 있다 — alarm:palette을 선언한 dt-core로 골랐다"


async def _join_walk():
    """병합 주 두 꼴을 **실제 걷기가** 쓰게 한다 — left 병합의 겹침 0(행은 남는다)과 양쪽 키 필드를 가진 행이 한 입력에만(못 센다)."""
    table = {("code.resources", "W0"): _res("W0", [("in:a:{line}", "rediskey"), ("ref:{line}", "rediskey")]),
             ("redis.get", "in:a:L1"): _value(rows=[{"ws": "", "n": 1}]),
             ("redis.get", "ref:L1"): _value(codes=[{"ws": "A"}, {"code": "B", "zz": None}])}
    left = MergeHit(path="w0.py", line=5, text="", left=["ws"], right=["ws"], how="left")
    only = MergeHit(path="w0.py", line=9, text="", left=["code"], right=["zz"])
    lookup = _Lookup({("W0", ()): FilterScan(files=["w0.py"], merges=[left, only])})
    return (await run_walk(_state(), runner=_Reads(table), classes=frozenset({"W0"}), lookup=lookup, clock=lambda: T0))["walk"]


def _prompt(walk) -> str:
    return "\n".join(["## 사건", *walk.lines(), REPO_NOTE, "## 모은 증거", "- t-1.e1 | redis.get key='alarm:stats:L1' | 빈 배열",
                      "- t-3.e1 | redis.get key='in:a:L1' | 행 1", "## 판정"])


async def test_병합_주의_두_꼴을_실제_걷기_출력에서_빼고_다른_주와_나머지는_그대로_둔다():
    """빼는 줄은 걷기가 쓰는 병합 주 두 꼴(`… 병합이라 행은 남는다 …`, `… — 겹침을 못 센다`)뿐 — 레포를 가른 주·홉 표·증거는 그대로. 걷기 문구가 바뀌면 이
    시험이 깨진다(도구가 문구와 함께 움직이게)."""
    from src.application import conclude_ab
    from src.application.conclude_ab import strip_join_notes
    from src.domain.case import JOIN_NOTE_MARKS

    assert conclude_ab.JOIN_NOTE_MARKS is JOIN_NOTE_MARKS                                        # 표식의 출처는 걷기 쪽 하나(R3-5l ①)
    walk = await _join_walk()
    assert len(walk.notes) == 2, walk.notes
    prompt = _prompt(walk)
    stripped, removed = strip_join_notes(prompt)
    assert removed == [f"  주: {n}" for n in walk.notes]
    assert REPO_NOTE in stripped and "## 모은 증거" in stripped and stripped.count("\n") == prompt.count("\n") - 2
    assert strip_join_notes(stripped) == (stripped, [])


def test_트레이스_파일에서_물어본_것만_꺼낸다(tmp_path):
    from src.__main__ import _make_tracer
    from src.application.conclude_ab import prompt_of

    trace, written = _make_tracer("c-1", folder=tmp_path / "c-1")
    trace("conclude", 3, "## 사건\n  주: x\n```json\n{}\n```", '{"verdict_type": "external"}', None, 9.0)
    assert prompt_of(written[0].read_text(encoding="utf-8")) == "## 사건\n  주: x\n```json\n{}\n```"
    assert prompt_of("그냥 글") is None


async def test_두_판을_번갈아_물어_유형_component_확신만_세고_판정_스키마에_그_프롬프트의_증거_id를_건다():
    """증거 본문·판정 서술은 찍지 않는다. 판정 스키마의 enum은 프롬프트에 보인 증거 id. 못 읽은 답은 그 사유 첫 줄만."""
    from src.application.conclude_ab import run_ab, summary

    walk = await _join_walk()
    prompt = _prompt(walk)

    def verdict(kind, component, confidence):
        cause = {"component": component, "evidence_ids": ["t-3.e1"]} if component else None
        return json.dumps({"verdict_type": kind, "root_cause": cause, "confidence": confidence, "narrative": "서술", "basis": [] if component else ["t-1.e1"]})

    llm = ScriptedAdapter([verdict("logic_bug", "processor", "high"), verdict("external", "collector", "medium"),
                           verdict("logic_bug", "processor", "medium"), "읽을 수 없는 글", "또 못 읽음"], clock=lambda: T0)
    rows = await run_ab(llm, prompt, runs=2)
    assert rows == [("A", 1, "logic_bug · processor · high"), ("B", 1, "external · collector · medium"),
                    ("A", 2, "logic_bug · processor · medium"), ("B", 2, rows[3][2])]
    assert rows[3][2].startswith("못 읽음 — ") and "\n" not in rows[3][2] and "읽을 수 없는 글" not in rows[3][2]       # 사유만, 답의 발췌는 없다
    assert "겹침을 못 센다" in llm.prompts[0] and "겹침을 못 센다" not in llm.prompts[1] and REPO_NOTE in llm.prompts[1]
    enum = llm.schemas[0]["properties"]["basis"]["items"]["enum"]
    assert {"t-1.e1", "t-3.e1"} <= set(enum) and all(i in prompt for i in enum) and len(enum) == len(set(enum))
    assert summary(rows) == ["A(주 있음): logic_bug · processor 2", "B(주 없음): external · collector 1 / 못 읽음 1"]
