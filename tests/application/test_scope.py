"""사이트 축 해석 — 웹 사용자는 --gbm/--fct를 주지 않는다.

후보 목록은 **코드가 registry에서 만들고** LLM은 그 안에서만 고른다. 목록 밖 값을
돌려주면 미확정이다 — 이 축은 어느 법인의 Redis/Mongo/소스 저장소를 읽을지를
정하므로, 증상 문자열 하나가 그것을 자유 서술로 바꾸게 두면 안 된다(규율 6).
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from src.application.scope import resolve_scope
from src.infrastructure.llm import ScriptedLLM

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _deps(*responses, spy=None):
    llm = ScriptedLLM(list(responses) or ["{}"])
    if spy is not None:
        original = llm.ainvoke

        async def watched(messages):
            spy.append(messages)
            return await original(messages)

        llm.ainvoke = watched
    return SimpleNamespace(lead_llm=llm)


async def test_호출자가_준_스코프는_LLM을_부르지_않는다():
    # CLI의 현재 동작이다. 사람이 이미 답한 것을 다시 묻지 않는다.
    calls = []
    out = await resolve_scope("증상", sites=[("mx", "gumi"), ("mx", "suwon")],
                              deps=_deps(spy=calls), clock=lambda: T, gbm="mx", fct="suwon")
    assert (out.status, out.gbm, out.fct) == ("resolved", "mx", "suwon")
    assert calls == []


async def test_호출자가_준_스코프도_후보_밖이면_미확정이다():
    # 접근 술어가 붙기 전이라도, 없는 사이트로 케이스를 열면 조립이 뒤에서 깨진다.
    out = await resolve_scope("증상", sites=[("mx", "gumi")], deps=_deps(),
                              clock=lambda: T, gbm="mx", fct="없는곳")
    assert out.status == "unresolved" and out.candidates == [("mx", "gumi")]


async def test_후보가_하나뿐이면_LLM_없이_확정한다():
    # 사이트가 하나인 설치에서 매번 LLM을 부르는 것은 낭비이자 실패 지점이다.
    calls = []
    out = await resolve_scope("증상", sites=[("mx", "gumi")], deps=_deps(spy=calls),
                              clock=lambda: T)
    assert (out.status, out.gbm, out.fct) == ("resolved", "mx", "gumi")
    assert calls == []


async def test_LLM이_고른_후보는_확정된다():
    out = await resolve_scope("수원 라인이 멈췄다", sites=[("mx", "gumi"), ("mx", "suwon")],
                              deps=_deps('{"gbm": "mx", "fct": "suwon"}'), clock=lambda: T)
    assert (out.status, out.gbm, out.fct) == ("resolved", "mx", "suwon")


async def test_후보_밖의_답은_미확정이다():
    # 증상 문자열 하나가 다른 법인의 Redis/Mongo를 읽는 조사를 열 수 있다.
    # 후보가 둘 이상이어야 LLM 경로를 탄다(하나면 지름길로 확정된다).
    out = await resolve_scope("증상", sites=[("mx", "gumi"), ("mx", "suwon")],
                              deps=_deps('{"gbm": "다른법인", "fct": "어딘가"}'),
                              clock=lambda: T)
    assert out.status == "unresolved"
    assert out.candidates == [("mx", "gumi"), ("mx", "suwon")] and out.questions


async def test_LLM_실패는_미확정이지_예외가_아니다():
    for reply in ("파싱 불가", "", '{"gbm": "mx"}'):          # 마지막은 fct 누락
        out = await resolve_scope("증상", sites=[("mx", "g"), ("mx", "s")],
                                  deps=_deps(reply), clock=lambda: T)
        assert out.status == "unresolved" and out.problems, reply


async def test_LLM_호출_자체가_터져도_미확정이다():
    class _Boom:
        async def ainvoke(self, messages):
            raise RuntimeError("게이트웨이 없음")

    out = await resolve_scope("증상", sites=[("mx", "g"), ("mx", "s")],
                              deps=SimpleNamespace(lead_llm=_Boom()), clock=lambda: T)
    assert out.status == "unresolved" and any("게이트웨이" in p for p in out.problems)


async def test_활성_사이트가_없으면_미확정이다():
    out = await resolve_scope("증상", sites=[], deps=_deps(), clock=lambda: T)
    assert out.status == "unresolved" and any("활성 사이트" in p for p in out.problems)


async def test_질문은_후보를_함께_보여준다():
    # 질문만 주면 호출자가 유효한 답의 집합을 모른다.
    out = await resolve_scope("증상", sites=[("mx", "gumi"), ("mx", "suwon")],
                              deps=_deps("파싱 불가"), clock=lambda: T)
    joined = " ".join(out.questions)
    assert "mx/gumi" in joined and "mx/suwon" in joined
