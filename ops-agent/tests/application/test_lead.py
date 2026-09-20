"""리드 LLM — **LLM이 정하는 것과 코드가 쥐는 것의 경계가 실제로 서 있는가.**

10a는 `frame`·`integrate` 자리에 대본을 넣어 울타리를 검증했다. 여기서는 그 자리에
진짜 리드가 들어왔을 때 세 가지를 본다:

1. **실패가 조용하지 않은가** — LLM이 죽으면 `llm_error`로 끝나고 기록이 남는가.
2. **소독이 여전히 서 있는가** — 리드가 만든 태스크·가설이 State를 우회하지 않는가.
3. **프롬프트가 config에서 나오는가** — 손으로 적은 목록과 갈라지지 않는가.

대본 LLM(`ScriptedAdapter`)과 **던지는** LLM(`ExplodingAdapter`)이 둘 다 필요하다.
대본은 예외를 `status="error"` 응답으로 바꿔 주므로, 그것만으로는 `ask_json`의
최외곽 try/except를 지워도 전부 통과한다.
"""
import json

from src.application import lead
from src.application.fakes import ScriptedRunner
from src.application.graph import build_engine
from src.application.nodes import EngineDeps, make_nodes
from src.application.state import CaseState
from src.domain.case import EvidenceRef
from src.infrastructure.llm_fakes import ExplodingAdapter, ScriptedAdapter

from tests.support import set_real_config_env
from tests.application.conftest import SECRET, T0, ok, site_config, task

FRAME_PROMPT = "케이스:\n{case}\n부를 수 있는 것:\n{actions}\n"
INTEGRATE_PROMPT = ("케이스:\n{case}\n가설:\n{hypotheses}\n태스크:\n{tasks}\n"
                    "증거:\n{evidence}\n목록:\n{actions}\n라운드 {round}/{max_rounds}\n")
PROMPTS = {"frame": FRAME_PROMPT, "integrate": INTEGRATE_PROMPT}


def leads(*replies, max_rounds=3, llm=None, site=None):
    """대본 LLM을 물린 `(frame, integrate)`와 그 LLM을 함께 돌려준다."""
    llm = llm or ScriptedAdapter(list(replies), clock=lambda: T0)
    frame, integrate = lead.make_lead(llm, site_config=site or site_config(),
                                      prompts=PROMPTS, max_rounds=max_rounds)
    return frame, integrate, llm


def reply(**body) -> str:
    return json.dumps(body, ensure_ascii=False)


TASK = {"id": "t-1", "goal": "어떤 컬렉션이 있는지 본다", "role": "data_prober",
        "action": "mongo.list_collections", "params": {}}


# ── 정상 ───────────────────────────────────────────────────────────

async def test_리드가_가설과_태스크를_낸다(case):
    frame, _, _ = leads(reply(hypotheses=[{"id": "h-1", "statement": "집계가 비었다"}],
                              tasks=[TASK]))
    patch = await frame(CaseState(case=case))
    assert [h.id for h in patch["hypotheses"]] == ["h-1"]
    assert [t.action for t in patch["plan_tasks"]] == ["mongo.list_collections"]
    assert "stopped_by" not in patch


async def test_코드펜스로_감싸도_읽는다(case):
    frame, _, _ = leads("```json\n" + reply(tasks=[TASK]) + "\n```")
    patch = await frame(CaseState(case=case))
    assert [t.id for t in patch["plan_tasks"]] == ["t-1"]


async def test_integrate가_결정을_낸다(case):
    _, integrate, _ = leads(reply(decision="conclude", note="충분히 봤다"))
    patch = await integrate(CaseState(case=case))
    assert patch["decision"] == "conclude"
    assert "note" not in patch          # State에 안 들어가는 자리다


# ── 실패가 조용하지 않다 ──────────────────────────────────────────

async def test_LLM이_던져도_흡수하고_llm_error로_끝낸다(case):
    """**`ask_json`의 최외곽 try/except를 지우면 이 테스트가 실패한다.**

    LangGraph 노드에서 던지면 superstep이 죽고, 그 케이스는 `investigating`
    상태로 영원히 남는다(고아 상태).
    """
    frame, _, llm = leads(llm=ExplodingAdapter("ConnectTimeout"))
    patch = await frame(CaseState(case=case))
    assert patch["stopped_by"] == "llm_error"
    assert patch["decision"] == "conclude"
    assert "ConnectTimeout" in patch["llm_errors"][0]
    assert len(llm.prompts) == lead.RETRIES + 1     # 던져도 재시도는 한다


async def test_어댑터가_오류를_값으로_줘도_llm_error다(case):
    frame, _, _ = leads(RuntimeError("429 Too Many Requests"),
                        RuntimeError("429 Too Many Requests"))
    patch = await frame(CaseState(case=case))
    assert patch["stopped_by"] == "llm_error"
    assert "429" in patch["llm_errors"][0]


async def test_쓰레기_응답은_재시도_한_번_뒤에_포기한다(case):
    """재시도가 **있다**는 것과 **한 번뿐**이라는 것을 같이 본다.

    대본이 두 개뿐이라 세 번째 호출은 `ScriptedAdapter`가 던진다 — 재시도를 늘리면
    "대본 소진"으로 실패하고, 재시도를 지우면 호출 수가 1이 되어 실패한다.
    """
    frame, _, llm = leads("그건 제가 알 수 없습니다.", "역시 모르겠습니다.")
    patch = await frame(CaseState(case=case))
    assert len(llm.prompts) == 2
    assert patch["stopped_by"] == "llm_error"
    assert "2회 시도 실패" in patch["llm_errors"][0]


async def test_첫_응답이_쓰레기여도_둘째가_맞으면_통과한다(case):
    """재시도가 실제로 쓸모가 있다는 것 — 없으면 멀쩡한 라운드가 버려진다."""
    frame, _, llm = leads("네, 아래와 같습니다.", reply(tasks=[TASK]))
    patch = await frame(CaseState(case=case))
    assert len(llm.prompts) == 2
    assert [t.id for t in patch["plan_tasks"]] == ["t-1"]
    assert "stopped_by" not in patch


async def test_재시도_프롬프트가_실패_사유를_실어_보낸다(case):
    """**같은 프롬프트를 한 번 더 보내는 것은 약한 모델에겐 재시도가 아니다.**

    사내 모델은 판단해서 고르는 게 아니라 주어진 틀을 채운다 — 같은 틀을 주면
    같은 답이 온다. 사유가 실려야 그게 새 입력이 된다.
    """
    _, integrate, llm = leads(reply(decision="maybe"), reply(decision="conclude"))
    await integrate(CaseState(case=case))

    first, second = llm.prompts
    assert "다시" not in first                       # 1차는 원본 그대로
    assert second.startswith(first)                 # 2차는 원본 + 사유
    assert "decision" in second and "continue" in second.split("## 다시")[1]


async def test_재시도_사유가_이미_걷어낸_키를_다시_탓하지_않는다(case):
    """수리 프롬프트는 **진짜 문제만** 말해야 한다.

    `confidence`는 우리가 조용히 걷어낼 것이므로 모델에게 그걸 고치라고 하면
    엉뚱한 곳을 보게 된다. 약한 모델일수록 마지막에 읽은 지시를 그대로 따른다.
    """
    _, integrate, llm = leads(reply(decision="maybe", confidence=0.9),
                              reply(decision="conclude"))
    await integrate(CaseState(case=case))

    repair = llm.prompts[1].split("## 다시")[1]
    assert "decision" in repair
    assert "confidence" not in repair


async def test_재시도_사유가_맨_뒤에_붙는다(case):
    """모델은 마지막에 읽은 지시를 더 따른다 — 9e의 울타리가 <사실> 뒤에도
    한 겹 있는 것과 같은 이유다."""
    from src.application.lead import repair_prompt

    made = repair_prompt("원래 프롬프트", "무엇이 틀렸는지")
    assert made.index("원래 프롬프트") < made.index("무엇이 틀렸는지")


async def test_곁다리_필드_하나로_계획이_통째로_날아가지_않는다(case):
    """약한 모델은 `"confidence"` 같은 것을 자주 붙인다. 응답을 통째로 버리면
    **멀쩡한 계획이 라운드째 날아간다.** 걷어내되 기록은 남긴다."""
    frame, _, llm = leads(reply(tasks=[TASK], confidence=0.9))
    patch = await frame(CaseState(case=case))
    assert len(llm.prompts) == 1                 # 재시도조차 안 했다
    assert [t.id for t in patch["plan_tasks"]] == ["t-1"]
    assert "stopped_by" not in patch
    assert "confidence" in patch["llm_errors"][0]


async def test_값이_틀리면_수리_재시도로_간다(case):
    """`"decision": "maybe"`는 걷어낼 수 없다 — 모델이 우리 어휘를 안 따른 것이다."""
    _, integrate, llm = leads(reply(decision="maybe"), reply(decision="conclude"))
    patch = await integrate(CaseState(case=case))
    assert len(llm.prompts) == 2
    assert patch["decision"] == "conclude"


# ── 소독 (규율 3·4) ───────────────────────────────────────────────

async def test_리드가_실은_status_ok가_pending으로_소독된다(case):
    """**`_sanitize_task`를 지우면 이 테스트가 실패한다.**

    `{"status": "ok", "result_evidence_ids": ["ev-9"]}`를 실어 보내면 그 태스크는
    실행되지 않은 채 끝난 것이 되어 select 게이트를 통째로 우회하고, 지어낸
    증거 id가 State에 들어간다.
    """
    frame, _, _ = leads(reply(tasks=[{**TASK, "status": "ok",
                                      "result_summary": "확인했다",
                                      "result_evidence_ids": ["ev-9"]}]))
    nodes = make_nodes(EngineDeps(runner=ScriptedRunner(), frame=frame,
                                  integrate=frame, max_rounds=3, parallel_width=2,
                                  max_tasks=20))
    patch = await nodes["frame"](CaseState(case=case))
    done = patch["plan_tasks"][0]
    assert done.status == "pending"
    assert done.result_summary is None and done.result_evidence_ids == []


async def test_없는_증거를_인용한_가설은_인용이_걷히고_기록이_남는다(case):
    """**규율 3.** 그냥 들이면 다음 라운드 브리핑이 그 id를 실어 보내고,
    리드는 자기가 지어낸 id를 근거로 다시 추론한다 — 복리로 불어난다."""
    _, integrate, _ = leads(reply(decision="continue", hypotheses=[
        {"id": "h-1", "statement": "집계가 비었다", "status": "supported",
         "supporting_ids": ["t-1.e1", "t-9.e1"]}]))
    nodes = make_nodes(EngineDeps(runner=ScriptedRunner(), frame=integrate,
                                  integrate=integrate, max_rounds=3,
                                  parallel_width=2, max_tasks=20))
    state = CaseState(case=case, round=1, evidence=[
        EvidenceRef(id="t-1.e1", source="mongo.find", summary="0건")])
    patch = await nodes["integrate"](state)

    kept = patch["hypotheses"][0]
    assert kept.supporting_ids == ["t-1.e1"]        # 실재하는 것만 남는다
    assert kept.status == "supported"               # 근거가 남았으므로 판정은 유지
    assert "t-9.e1" in patch["llm_errors"][0]       # 조용히 고치지 않는다


async def test_근거가_전부_환각이면_판정이_open으로_되돌아간다(case):
    """근거가 하나도 안 남은 "supported"는 근거 없는 단정이고, 그게 12a의 재료다."""
    _, integrate, _ = leads(reply(decision="continue", hypotheses=[
        {"id": "h-1", "statement": "집계가 비었다", "status": "refuted",
         "refuting_ids": ["t-9.e1"]}]))
    nodes = make_nodes(EngineDeps(runner=ScriptedRunner(), frame=integrate,
                                  integrate=integrate, max_rounds=3,
                                  parallel_width=2, max_tasks=20))
    patch = await nodes["integrate"](CaseState(case=case, round=1))
    assert patch["hypotheses"][0].status == "open"
    assert "open으로 되돌렸다" in patch["llm_errors"][0]


# ── 찾지 않고 댄 이름 (decisions ⑮ 계측) ──────────────────────────

def _deps(frame, integrate=None, **extra):
    from src.application.fakes import ScriptedRunner
    body = {"runner": ScriptedRunner(), "frame": frame, "integrate": integrate or frame,
            "max_rounds": 3, "parallel_width": 2, "max_tasks": 20}
    body.update(extra)
    return EngineDeps(**body)


async def test_증거에_없는_이름을_대면_기록된다(case):
    """**⑮ 설계가 실제로 먹히는지 재는 계측기다.**

    안 찾고 찍으면 빈 결과가 오는데, 그건 "데이터가 없다"가 아니라 "질문을
    잘못했다"다. 둘을 구별 못 하면 판정이 **없는 이상을 보고한다.**
    """
    _, integrate, _ = leads(reply(decision="continue", tasks=[
        {"id": "t-5", "goal": "읽는다", "role": "data_prober", "action": "mongo.find",
         "params": {"collection": "안_찾아본_이름", "filter": {}}}]))
    nodes = make_nodes(_deps(integrate))
    patch = await nodes["integrate"](CaseState(case=case, round=1, evidence=[
        EvidenceRef(id="t-1.e1", source="mongo.list_collections",
                    summary="2건 ['aa_events', 'bb_state']")]))

    assert "찾지 않고 이름을 댔다" in patch["llm_errors"][0]
    assert "안_찾아본_이름" in patch["llm_errors"][0]
    # **막지는 않는다** — 빈도를 봐야 막을지 정할 수 있고, 증상이 이름을 담은
    # 정당한 경우도 있다.
    assert [t.id for t in patch["plan_tasks"]] == ["t-5"]


async def test_증거에서_본_이름이면_안_남는다(case):
    _, integrate, _ = leads(reply(decision="continue", tasks=[
        {"id": "t-5", "goal": "읽는다", "role": "data_prober", "action": "mongo.find",
         "params": {"collection": "bb_state", "filter": {}}}]))
    nodes = make_nodes(_deps(integrate))
    patch = await nodes["integrate"](CaseState(case=case, round=1, evidence=[
        EvidenceRef(id="t-1.e1", source="mongo.list_collections",
                    summary="2건 ['aa_events', 'bb_state']")]))
    assert patch["llm_errors"] == []


async def test_리드가_본_이름을_찍었다고_적지_않는다(case):
    """**사내에서 실제로 난 거짓 양성이다.**

    `summary`/`body`를 나눌 때 `_seen`을 안 고쳤다. 리드는 `body`에 실린 토픽 78개를
    보고 골랐는데, 우리는 `summary`(9개)로 판정해서 "찾지 않고 댔다"고 적었다.

    계측기가 거짓 양성을 내면 그 숫자로 **"막을지 말지"를 정할 수 없다** — ④를
    기록만 하기로 한 이유가 통째로 사라진다.
    """
    from src.application.runner_probe import _summarize, detail

    topics = [f"GUMI_TOPIC_{i:03d}" for i in range(179)]
    topics[49] = "GUMI_ALARM_EVENT_MAIN"
    ref = EvidenceRef(id="t-2.e1", source="kafka.list_topics",
                      summary=_summarize(topics),
                      body="\n".join(detail(topics, limit=1200)))
    assert "GUMI_ALARM_EVENT_MAIN" in ref.body          # 리드는 봤다
    assert "GUMI_ALARM_EVENT_MAIN" not in ref.summary   # 요약에는 없다

    _, integrate, _ = leads(reply(decision="continue", tasks=[
        {"id": "t-5", "goal": "읽는다", "role": "data_prober", "action": "kafka.tail",
         "params": {"topic": "GUMI_ALARM_EVENT_MAIN", "limit": 10}}]))
    patch = await make_nodes(_deps(integrate))["integrate"](
        CaseState(case=case, round=1, evidence=[ref]))
    assert patch["llm_errors"] == []


async def test_body가_없는_증거는_summary로_본다(case):
    """`EvidenceRef`는 어디서나 만들 수 있다 — 11b의 서브에이전트가 곧 만든다."""
    _, integrate, _ = leads(reply(decision="continue", tasks=[
        {"id": "t-5", "goal": "읽는다", "role": "data_prober", "action": "mongo.find",
         "params": {"collection": "bb_state", "filter": {}}}]))
    patch = await make_nodes(_deps(integrate))["integrate"](CaseState(
        case=case, round=1, evidence=[EvidenceRef(
            id="t-1.e1", source="mongo.list_collections", summary="2건 ['bb_state']")]))
    assert patch["llm_errors"] == []


async def test_이름이_안_들어가는_읽기는_검사하지_않는다(case):
    """`pattern="*"`·`entry=...`는 찾을 것이 없다 — REST 항목은 config가 선언한다."""
    frame, _, _ = leads(reply(tasks=[
        TASK,
        {"id": "t-2", "goal": "훑는다", "role": "data_prober", "action": "redis.scan",
         "params": {"pattern": "*"}},
        {"id": "t-3", "goal": "부른다", "role": "data_prober", "action": "rest.query",
         "params": {"entry": "summary_badge", "params": {}}}]))
    patch = await make_nodes(_deps(frame))["frame"](CaseState(case=case))
    assert patch["llm_errors"] == []


async def test_사람이_쓴_대본에는_이_검사를_안_건다(case):
    """`case dryrun`의 계획 파일은 **시스템을 아는 사람이 이름을 알고 적은 것**이다.
    켜 두면 기록이 거짓 양성으로만 차고, 그러면 아무도 그 필드를 안 본다."""
    frame, _, _ = leads(reply(tasks=[
        {"id": "t-1", "goal": "읽는다", "role": "data_prober", "action": "redis.get",
         "params": {"key": "oee:L3"}}]))
    patch = await make_nodes(_deps(frame, check_discovery=False))["frame"](
        CaseState(case=case))
    assert patch["llm_errors"] == []


# ── 태스크 id 재사용 (사내에서 실제로 난 버그) ─────────────────────

async def test_완료된_태스크가_같은_id로_되살아나지_않는다(case):
    """**사내에서 실제로 났다.**

    리드가 예시의 `t-4`를 매 라운드 그대로 베꼈고, 소독이 수명주기를 초기화한 뒤
    리듀서가 완료된 태스크를 덮어써서 같은 읽기가 **세 번** 돌았다. 증거는 따로
    쌓이므로 살아남아, 최종 State에 **"실행 안 됐는데 증거가 있는"** 모순이 남았다.

    12b의 보고서가 그 State를 그대로 쓰면 **안 한 일을 했다고, 한 일을 안 했다고**
    적는다.
    """
    _, integrate, _ = leads(reply(decision="continue", tasks=[
        {"id": "t-1", "goal": "또 읽는다", "role": "data_prober",
         "action": "mongo.find", "params": {"collection": "bb_state", "filter": {}}}]))
    nodes = make_nodes(_deps(integrate, check_discovery=False))

    done = task("t-1", status="ok", result_summary="이미 읽었다",
                result_evidence_ids=["t-1.e1"])
    patch = await nodes["integrate"](CaseState(case=case, round=1, plan_tasks=[done]))

    assert patch["plan_tasks"] == []                  # 안 받는다
    assert "이미 있는 태스크 id" in patch["llm_errors"][0]


async def test_한_응답_안의_중복_id도_하나만_받는다(case):
    frame, _, _ = leads(reply(tasks=[TASK, {**TASK, "goal": "다른 목표"}]))
    patch = await make_nodes(_deps(frame))["frame"](CaseState(case=case))
    assert [t.id for t in patch["plan_tasks"]] == ["t-1"]
    assert "이미 있는 태스크 id" in patch["llm_errors"][0]


async def test_같은_읽기가_라운드마다_반복되지_않는다(case):
    """**소비자로 직접 확인한다** — 그래프를 끝까지 돌려서 실행기가 무엇을 돌렸는지 본다.

    노드 단위로만 보면 "id를 안 받는다"까지밖에 안 보이고, 정작 아팠던 것은
    **같은 읽기가 세 번 실행된 것**이었다.
    """
    from src.application.fakes import ScriptedRunner
    from src.domain.investigation import TaskOutcome

    frame, _, _ = leads(reply(tasks=[TASK]))
    _, integrate, _ = leads(*[reply(decision="continue", tasks=[TASK])] * 6)
    runner = ScriptedRunner({"t-1": TaskOutcome(
        task_id="t-1", status="ok", summary="읽었다",
        evidence=[EvidenceRef(id="t-1.e1", source="mongo.list_collections", summary="…")])})
    deps = EngineDeps(runner=runner, frame=frame, integrate=integrate, max_rounds=4,
                      parallel_width=3, max_tasks=24, check_discovery=False)
    final = await build_engine(deps).ainvoke(CaseState(case=case))

    assert runner.ran == ["t-1"]                      # 딱 한 번
    assert final["plan_tasks"][0].status == "ok"      # 되살아나지 않았다
    assert final["stopped_by"] == "no_runnable"       # 낼 것이 없으면 정직하게 끝난다


# ── 같은 질의 반복 (사내에서 실제로 난 것) ─────────────────────────

async def test_goal만_바꾼_같은_질의는_받지_않는다(case):
    """**사내에서 실제로 났다.** id를 막았더니 새 id로 같은 질의를 다시 냈다 —
    `goal`만 "데이터가 있나" → "값이 정상인가" → "값이 진짜 0인가"로 바뀌고
    나가는 것은 전부 `mongo.find collection='alarm' filter={} limit=5`였다.

    **말이 아니라 나가는 것으로 세야** 중복이 보인다.
    """
    read = {"action": "mongo.find", "params": {"collection": "alarm", "filter": {}}}
    _, integrate, _ = leads(reply(decision="continue", tasks=[
        {"id": "t-9", "goal": "값이 진짜 0인지 확인한다", "role": "data_prober", **read}]))
    nodes = make_nodes(_deps(integrate, check_discovery=False))

    done = task("t-4", goal="데이터가 있는지 확인한다", status="ok", **read)
    patch = await nodes["integrate"](CaseState(case=case, round=1, plan_tasks=[done]))

    assert patch["plan_tasks"] == []
    assert "이미 한 읽기를 또 냈다" in patch["llm_errors"][0]


async def test_인자가_하나라도_다르면_받는다(case):
    """`limit`이 다르면 다른 질문이다 — 너무 넓게 막으면 정당한 후속 읽기가 막힌다."""
    _, integrate, _ = leads(reply(decision="continue", tasks=[
        {"id": "t-9", "goal": "더 본다", "role": "data_prober", "action": "mongo.find",
         "params": {"collection": "alarm", "filter": {}, "limit": 50}}]))
    nodes = make_nodes(_deps(integrate, check_discovery=False))
    done = task("t-4", status="ok", action="mongo.find",
                params={"collection": "alarm", "filter": {}, "limit": 5})
    patch = await nodes["integrate"](CaseState(case=case, round=1, plan_tasks=[done]))
    assert [t.id for t in patch["plan_tasks"]] == ["t-9"]


async def test_같은_질의로는_라운드를_태우지_않는다(case):
    """**소비자로 확인한다** — 그래프를 끝까지 돌려 실행기가 몇 번 돌았는지 본다.

    고치기 전: 4라운드 중 셋이 같은 두 질의의 반복이었다.
    """
    from src.application.fakes import ScriptedRunner
    from src.domain.investigation import TaskOutcome

    read = {"action": "mongo.find",
            "params": {"collection": "alarm", "filter": {}}, "role": "data_prober"}
    frame, _, _ = leads(reply(tasks=[{"id": "t-1", "goal": "읽는다", **read}]))
    _, integrate, _ = leads(*[reply(decision="continue", tasks=[
        {"id": f"t-{n}", "goal": f"다시 읽는다 {n}", **read}]) for n in range(2, 8)])
    runner = ScriptedRunner({"t-1": TaskOutcome(
        task_id="t-1", status="ok", summary="5건",
        evidence=[EvidenceRef(id="t-1.e1", source="mongo.find", summary="5건")])})
    deps = EngineDeps(runner=runner, frame=frame, integrate=integrate, max_rounds=4,
                      parallel_width=3, max_tasks=24, check_discovery=False)
    final = await build_engine(deps).ainvoke(CaseState(case=case))

    assert runner.ran == ["t-1"]
    # 고치기 전이면 여기서 4라운드를 돌며 같은 질의를 네 번 실행했다.
    assert final["round"] == 1
    assert final["stopped_by"] == "no_runnable"


# ── 프롬프트 ───────────────────────────────────────────────────────

async def test_프롬프트의_등재_목록이_config에서_나온다(case):
    """**손으로 적은 목록이면 실패한다** — config에 항목을 더해도 안 따라온다."""
    site = site_config(rest={"base_url": "https://h/api", "entries": {
        "summary_prod_status": {"method": "POST", "path": "/summary/prod_status"}}})
    frame, _, llm = leads(reply(tasks=[TASK]), site=site)
    await frame(CaseState(case=case))
    assert 'entry="summary_prod_status"' in llm.prompts[0]


async def test_프롬프트에_접속_정보가_안_섞인다(case):
    frame, _, llm = leads(reply(tasks=[TASK]))
    await frame(CaseState(case=case))
    for leak in (SECRET, "redis://h:6379", "mongodb://h:27017", "dmfReadOnly",
                 "https://h/api", "h:9092"):
        assert leak not in llm.prompts[0], f"접속 정보가 샜다 — {leak}"


async def test_치환되지_않은_자리가_남지_않는다(case):
    """`{max_chars}`가 그대로 LLM에게 나간 9e의 사고. 응답은 그럴듯해 보인다."""
    _, integrate, llm = leads(reply(decision="conclude"))
    await integrate(CaseState(case=case, round=2))
    assert lead.slots_in(llm.prompts[0]) == set()


def test_JSON_예시의_중괄호는_자리로_안_센다():
    """`str.format`이 여기서 `KeyError`로 죽었다. 자리는 **식별자 모양**만이다."""
    example = '{\n  "decision": "continue",\n  "params": {}\n}\n라운드 {round}'
    assert lead.slots_in(example) == {"round"}


def test_fill은_같은_자리를_여러_번_채운다():
    assert lead.fill("{a}와 {a}", {"a": "x"}) == "x와 x"


# ── 트레이스 ───────────────────────────────────────────────────────

async def test_트레이스가_시도마다_날것을_건넨다(case):
    """**파싱된 결과만 봐서는 모델이 무엇을 했는지 안 보인다.**

    사내에서 "모델이 예시를 그대로 베낀다"는 것을 알아낸 경로가 이것이고, 그건 최종
    State에는 흔적이 없는 사실이었다. 실패한 시도도 남아야 한다 — 고칠 근거가 거기 있다.
    """
    seen = []
    llm = ScriptedAdapter(["쓰레기", reply(tasks=[TASK])], clock=lambda: T0)
    frame, _ = lead.make_lead(llm, site_config=site_config(), prompts=PROMPTS,
                              max_rounds=3,
                              trace=lambda *row: seen.append(row))
    await frame(CaseState(case=case, round=2))

    assert len(seen) == 2                       # 실패한 1차도 남는다
    (node, round_no, prompt, text, error) = seen[0]
    assert (node, round_no, text) == ("frame", 2, "쓰레기")
    assert error and "JSON" in error
    assert "다시" in seen[1][2]                  # 2차는 수리 프롬프트
    assert seen[1][4] is None                   # 2차는 성공


async def test_트레이스가_던져도_조사는_계속된다(case):
    """트레이스는 편의다. 디스크가 차서 못 쓰는 것 때문에 조사가 멈추면 안 된다."""
    def explode(*_):
        raise OSError("No space left on device")

    frame, _ = lead.make_lead(
        ScriptedAdapter([reply(tasks=[TASK])], clock=lambda: T0),
        site_config=site_config(), prompts=PROMPTS, max_rounds=3, trace=explode)
    patch = await frame(CaseState(case=case))
    assert [t.id for t in patch["plan_tasks"]] == ["t-1"]


# ── 그래프까지 합쳐서 ──────────────────────────────────────────────

async def test_frame이_죽으면_라운드를_시작하지_않는다(case):
    """**`route_after_frame`을 지우면 이 테스트가 실패한다.**

    흘려보내면 integrate가 LLM을 **또** 부르고(대본이 없어 던진다), 끝난 이유가
    `no_runnable`로 덮여 "조사할 게 없었다"가 된다 — LLM이 안 붙은 것과 볼 게
    없는 것은 완전히 다른 사실이다.
    """
    frame, integrate, llm = leads(llm=ExplodingAdapter())
    deps = EngineDeps(runner=ScriptedRunner(), frame=frame, integrate=integrate,
                      max_rounds=3, parallel_width=2, max_tasks=20)
    final = await build_engine(deps).ainvoke(CaseState(case=case))

    assert final["stopped_by"] == "llm_error"
    assert final["round"] == 1 and final["plan_tasks"] == []
    # frame 두 번(재시도)뿐 — integrate는 아예 안 불렸다.
    assert len(llm.prompts) == lead.RETRIES + 1


async def test_integrate가_죽으면_상한이_그_이유를_덮지_않는다(case):
    """`stopped_by`가 `max_rounds`로 둔갑하면 12a가 "미확정"과 "조사 실패"를
    구별할 근거를 잃는다."""
    frame, _, _ = leads(reply(tasks=[TASK]))
    _, integrate, _ = leads(llm=ExplodingAdapter())
    deps = EngineDeps(runner=ScriptedRunner({"t-1": ok("t-1")}), frame=frame,
                      integrate=integrate, max_rounds=1, parallel_width=2,
                      max_tasks=20)
    final = await build_engine(deps).ainvoke(CaseState(case=case))
    assert final["stopped_by"] == "llm_error"
    assert final["llm_errors"]


async def test_리드가_찾고_읽는_두_라운드가_돈다(case):
    """**10b가 만든 것의 요약이다.**

    1라운드: 무엇이 있는지 찾는다 → 2라운드: 찾은 것을 읽는다. 두 번째 태스크는
    `input_evidence_ids`로 첫 증거를 기다리므로, 게이트가 라운드 경계를 만든다.
    """
    from src.domain.investigation import TaskOutcome

    frame, _, _ = leads(reply(tasks=[TASK]))
    _, integrate, _ = leads(
        reply(decision="continue", tasks=[
            {"id": "t-2", "goal": "찾은 컬렉션을 읽는다", "role": "data_prober",
             "action": "mongo.find", "params": {"collection": "bb_state", "filter": {}},
             "input_evidence_ids": ["t-1.e1"]}]),
        reply(decision="conclude", hypotheses=[
            {"id": "h-1", "statement": "집계가 비었다", "status": "refuted",
             "refuting_ids": ["t-2.e1"]}]))

    runner = ScriptedRunner({
        "t-1": TaskOutcome(task_id="t-1", status="ok", summary="2건",
                           evidence=[EvidenceRef(id="t-1.e1", source="mongo.list_collections",
                                                 summary="['aa_events', 'bb_state']")]),
        "t-2": TaskOutcome(task_id="t-2", status="ok", summary="1건",
                           evidence=[EvidenceRef(id="t-2.e1", source="mongo.find",
                                                 summary="[{'m': 'x'}]")])})
    deps = EngineDeps(runner=runner, frame=frame, integrate=integrate,
                      max_rounds=4, parallel_width=3, max_tasks=20)
    final = await build_engine(deps).ainvoke(CaseState(case=case))

    assert runner.ran == ["t-1", "t-2"]
    assert final["stopped_by"] == "decision"
    assert final["llm_errors"] == []
    assert final["hypotheses"][0].status == "refuted"      # 인용이 실재하므로 유지
    assert [e.id for e in final["evidence"]] == ["t-1.e1", "t-2.e1"]


# ── CLI ────────────────────────────────────────────────────────────

def _cli_tree(tmp_path, monkeypatch):
    """실제 `config/`를 복사하고 케이스 저장소만 tmp로 돌린다.

    **심볼릭 링크를 쓰지 않는다** — Windows에서 `symlink_to`는 관리자 권한이
    필요하고, 없으면 개발 기계에서만 도는 테스트가 된다(windows.md 함정 ⑥-c).
    """
    import shutil
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent
    config_root = tmp_path / "config"
    shutil.copytree(root / "config", config_root)
    app = json.loads((root / "config" / "app.json").read_text(encoding="utf-8"))
    app["case_store"] = str(tmp_path / "cases.json")
    (config_root / "app.json").write_text(json.dumps(app, ensure_ascii=False),
                                          encoding="utf-8")
    set_real_config_env(monkeypatch)
    return config_root


def test_CLI가_실제로_돈다(tmp_path, capsys, monkeypatch):
    """**`patrol open` → `case investigate` 배선 전체를 부른다.**

    부품만 테스트하면 배선이 안 보인다 — 이 리포에서 실제로 났다: `ProbeRunner`에
    `clock`을 필수로 올렸는데 `__main__`의 호출부가 안 따라갔고, **776개가 전부
    통과했다.** 명령을 돌려 보고서야 `TypeError`가 나왔다.

    LLM만 대본으로 갈아끼운다. 그 위(인자 파싱·config·프롬프트 검사·케이스 조회·
    어댑터 조립·엔진·출력)는 전부 진짜다.
    """
    from src.__main__ import main

    config_root = _cli_tree(tmp_path, monkeypatch)
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps({
        "rest": {"summary_badge": [
            {"group": "Operator", "title": "Check", "alarm": 0, "caution": 0, "normal": 0}],
            "prod_status": {"status": "In Production"}},
        "mongo": {"bb_state": [{"m": "x"}]}}), encoding="utf-8")

    monkeypatch.setattr("sys.argv", [
        "src", "--config-root", str(config_root), "--env-file", str(tmp_path / "none"),
        "patrol", "open", "--gbm", "mx", "--fct", "gumi", "--stub-seeds", str(seeds)])
    assert main() == 0
    case_id = "c-1"
    assert case_id in capsys.readouterr().out

    replies = [reply(hypotheses=[{"id": "h-1", "statement": "파생 집계가 비어 있다"}],
                     tasks=[TASK]),
               reply(decision="conclude", hypotheses=[
                   {"id": "h-1", "statement": "파생 집계가 비어 있다",
                    "status": "refuted", "refuting_ids": ["t-1.e1"]}])]
    monkeypatch.setattr("src.infrastructure.llm_factory.build_llm",
                        lambda cfg, *, clock, warn=None: ScriptedAdapter(
                            replies, clock=clock))
    monkeypatch.setattr("sys.argv", [
        "src", "--config-root", str(config_root), "--env-file", str(tmp_path / "none"),
        "case", "investigate", case_id, "--stub-seeds", str(seeds)])

    assert main() == 0                       # LLM 오류가 없으면 0
    out = capsys.readouterr().out
    assert "끝난 이유: decision" in out
    assert "h-1 [refuted]" in out
    assert "t-1" in out and "t-1.e1" in out


def test_CLI가_돈_조사와_못_돈_조사를_같은_등급으로_보지_않는다(tmp_path, capsys, monkeypatch):
    """리드가 없는 증거를 인용했지만 조사 자체는 끝까지 돌았다.

    이것까지 종료 코드 1로 주면 "빨간불이 원래 그렇다"가 되고, 그러면 **진짜
    빨간불도 안 보이게 된다.** 알리되 등급은 나눈다.
    """
    from src.__main__ import main

    config_root = _cli_tree(tmp_path, monkeypatch)
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps({"rest": {"summary_badge": [
        {"group": "Operator", "title": "Check", "alarm": 0, "caution": 0, "normal": 0}],
        "prod_status": {"status": "In Production"}}}), encoding="utf-8")

    monkeypatch.setattr("sys.argv", [
        "src", "--config-root", str(config_root), "--env-file", str(tmp_path / "none"),
        "patrol", "open", "--gbm", "mx", "--fct", "gumi", "--stub-seeds", str(seeds)])
    assert main() == 0
    capsys.readouterr()

    replies = [reply(tasks=[TASK]),
               reply(decision="conclude", hypotheses=[
                   {"id": "h-1", "statement": "집계가 비었다", "status": "refuted",
                    "refuting_ids": ["t-1.e1", "없는-증거.e1"]}])]
    monkeypatch.setattr("src.infrastructure.llm_factory.build_llm",
                        lambda cfg, *, clock, warn=None: ScriptedAdapter(replies, clock=clock))
    monkeypatch.setattr("sys.argv", [
        "src", "--config-root", str(config_root), "--env-file", str(tmp_path / "none"),
        "case", "investigate", "c-1", "--stub-seeds", str(seeds)])

    assert main() == 0                       # 조사는 돌았다
    captured = capsys.readouterr()
    assert "끝난 이유: decision" in captured.out
    assert "h-1 [refuted]" in captured.out   # 남은 근거가 있으므로 판정은 유지
    assert "계약을 어겼다" in captured.err    # 그래도 조용히 넘어가지 않는다
    assert "없는-증거.e1" in captured.err


def test_CLI가_LLM_실패를_0으로_숨기지_않는다(tmp_path, capsys, monkeypatch):
    """조용히 성공한 척하면 아무도 안 본다 — `no_runnable`과 같은 모양이 된다."""
    from src.__main__ import main

    config_root = _cli_tree(tmp_path, monkeypatch)
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps({"rest": {"summary_badge": [
        {"group": "Operator", "title": "Check", "alarm": 0, "caution": 0, "normal": 0}],
        "prod_status": {"status": "In Production"}}}), encoding="utf-8")

    monkeypatch.setattr("sys.argv", [
        "src", "--config-root", str(config_root), "--env-file", str(tmp_path / "none"),
        "patrol", "open", "--gbm", "mx", "--fct", "gumi", "--stub-seeds", str(seeds)])
    assert main() == 0
    capsys.readouterr()

    monkeypatch.setattr("src.infrastructure.llm_factory.build_llm",
                        lambda cfg, *, clock, warn=None: ExplodingAdapter("ConnectTimeout"))
    monkeypatch.setattr("sys.argv", [
        "src", "--config-root", str(config_root), "--env-file", str(tmp_path / "none"),
        "case", "investigate", "c-1", "--stub-seeds", str(seeds)])

    assert main() == 1
    captured = capsys.readouterr()
    assert "끝난 이유: llm_error" in captured.out
    assert "ConnectTimeout" in captured.err        # 사유는 stderr에 그대로 남는다
    assert "안 돌았다" in captured.err


def test_우리가_안_채우는_자리가_있으면_기동을_막는다(tmp_path, monkeypatch):
    """**이 검사를 지우면 `{max_round}`가 치환 안 된 채 LLM에게 나간다.**

    9e에서 실제로 났고, 리포트는 정상으로 보여서 아무도 못 봤다.
    """
    import pytest

    from src.__main__ import _load_lead_prompt
    from src.application import briefing

    bad = tmp_path / "p.md"
    bad.write_text("{case}\n{actions}\n라운드 {max_round}\n", encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        _load_lead_prompt(tmp_path, "p.md", slots=briefing.INTEGRATE_SLOTS)
    assert "max_round" in str(caught.value) and "max_rounds" in str(caught.value)


def test_필요한_자리가_없으면_기동을_막는다(tmp_path):
    """`{actions}`가 없으면 리드는 있지도 않은 것을 계속 지어낸다."""
    import pytest

    from src.__main__ import _load_lead_prompt
    from src.application import briefing

    bad = tmp_path / "p.md"
    bad.write_text("무엇이든 조사하라.\n", encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        _load_lead_prompt(tmp_path, "p.md", slots=briefing.FRAME_SLOTS)
    assert "{case}" in str(caught.value) and "{actions}" in str(caught.value)


def test_예시_자리가_없으면_기동을_막는다(tmp_path):
    """**사내 모델은 예시의 틀을 채우는 식으로 답한다** — 예시가 없으면 베낄 것이
    없어 형식이 매번 달라지고, 그건 매 라운드 파싱 실패로 나타난다."""
    import pytest

    from src.__main__ import _load_lead_prompt
    from src.application import briefing

    (tmp_path / "p.md").write_text("{case}\n{actions}\n", encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        _load_lead_prompt(tmp_path, "p.md", slots=briefing.FRAME_SLOTS)
    assert "{example}" in str(caught.value)


def test_CLI_트레이스가_프롬프트와_날것_응답을_남긴다(tmp_path, capsys, monkeypatch):
    """**최종 State만 봐서는 모델이 예시를 베꼈는지 알 수 없다.**

    10b를 끝낼 때 이게 없어서 프롬프트 설계가 전부 추측 위에 있었다.
    """
    from src.__main__ import main

    config_root = _cli_tree(tmp_path, monkeypatch)
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps({"rest": {"summary_badge": [
        {"group": "Operator", "title": "Check", "alarm": 0, "caution": 0, "normal": 0}],
        "prod_status": {"status": "In Production"}}}), encoding="utf-8")

    monkeypatch.setattr("sys.argv", [
        "src", "--config-root", str(config_root), "--env-file", str(tmp_path / "none"),
        "patrol", "open", "--gbm", "mx", "--fct", "gumi", "--stub-seeds", str(seeds)])
    assert main() == 0
    capsys.readouterr()

    replies = ["설명을 먼저 드리자면", reply(tasks=[TASK]), reply(decision="conclude")]
    monkeypatch.setattr("src.infrastructure.llm_factory.build_llm",
                        lambda cfg, *, clock, warn=None: ScriptedAdapter(replies, clock=clock))
    monkeypatch.setattr("sys.argv", [
        "src", "--config-root", str(config_root), "--env-file", str(tmp_path / "none"),
        "case", "investigate", "c-1", "--stub-seeds", str(seeds),
        "--trace", str(tmp_path / "traces")])
    assert main() == 0

    folder = tmp_path / "traces" / "c-1"
    files = sorted(f for f in folder.glob("*.md") if f.name != "summary.md")
    assert len(files) == 3                       # frame 2회(재시도) + integrate 1회
    # 진단도 파일로 남는다 — 사람이 터미널에서 옮겨 적지 않아도 되게.
    assert "진단" in (folder / "summary.md").read_text(encoding="utf-8")
    first = files[0].read_text(encoding="utf-8")
    assert "설명을 먼저 드리자면" in first          # 날것이 남는다
    assert "못 읽었다" in first
    assert "mongo.list_collections" in first     # 물어본 프롬프트도 통째로
    assert "트레이스 3건" in capsys.readouterr().out
