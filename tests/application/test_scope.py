"""사이트 축 해석 — 웹 사용자는 --gbm/--fct를 주지 않는다.

후보 목록은 **코드가 registry에서 만들고** LLM은 그 안에서만 고른다. 목록 밖 값을
돌려주면 미확정이다 — 이 축은 어느 법인의 Redis/Mongo/소스 저장소를 읽을지를
정하므로, 증상 문자열 하나가 그것을 자유 서술로 바꾸게 두면 안 된다(규율 6).
"""

from types import SimpleNamespace

from src.application.scope import resolve_scope
from src.infrastructure.llm import ScriptedLLM




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
                              deps=_deps(spy=calls), gbm="mx", fct="suwon")
    assert (out.status, out.gbm, out.fct) == ("resolved", "mx", "suwon")
    assert calls == []


async def test_호출자가_준_스코프도_후보_밖이면_미확정이다():
    # 접근 술어가 붙기 전이라도, 없는 사이트로 케이스를 열면 조립이 뒤에서 깨진다.
    out = await resolve_scope("증상", sites=[("mx", "gumi")], deps=_deps(),
                              gbm="mx", fct="없는곳")
    assert out.status == "unresolved" and out.candidates == [("mx", "gumi")]


async def test_후보가_하나뿐이면_LLM_없이_확정한다():
    # 사이트가 하나인 설치에서 매번 LLM을 부르는 것은 낭비이자 실패 지점이다.
    calls = []
    out = await resolve_scope("증상", sites=[("mx", "gumi")], deps=_deps(spy=calls))
    assert (out.status, out.gbm, out.fct) == ("resolved", "mx", "gumi")
    assert calls == []


async def test_LLM이_고른_후보는_확정된다():
    out = await resolve_scope("수원 라인이 멈췄다", sites=[("mx", "gumi"), ("mx", "suwon")],
                              deps=_deps('{"gbm": "mx", "fct": "suwon"}'))
    assert (out.status, out.gbm, out.fct) == ("resolved", "mx", "suwon")


async def test_후보_밖의_답은_미확정이다():
    # 증상 문자열 하나가 다른 법인의 Redis/Mongo를 읽는 조사를 열 수 있다.
    # 후보가 둘 이상이어야 LLM 경로를 탄다(하나면 지름길로 확정된다).
    out = await resolve_scope("증상", sites=[("mx", "gumi"), ("mx", "suwon")],
                              deps=_deps('{"gbm": "다른법인", "fct": "어딘가"}'))
    assert out.status == "unresolved"
    assert out.candidates == [("mx", "gumi"), ("mx", "suwon")] and out.questions


async def test_LLM_실패는_미확정이지_예외가_아니다():
    for reply in ("파싱 불가", "", '{"gbm": "mx"}'):          # 마지막은 fct 누락
        out = await resolve_scope("증상", sites=[("mx", "g"), ("mx", "s")],
                                  deps=_deps(reply))
        assert out.status == "unresolved" and out.problems, reply


async def test_LLM_호출_자체가_터져도_미확정이다():
    class _Boom:
        async def ainvoke(self, messages):
            raise RuntimeError("게이트웨이 없음")

    out = await resolve_scope("증상", sites=[("mx", "g"), ("mx", "s")],
                              deps=SimpleNamespace(lead_llm=_Boom()))
    assert out.status == "unresolved" and any("게이트웨이" in p for p in out.problems)


async def test_활성_사이트가_없으면_미확정이다():
    out = await resolve_scope("증상", sites=[], deps=_deps())
    assert out.status == "unresolved" and any("활성 사이트" in p for p in out.problems)


async def test_질문은_후보를_함께_보여준다():
    # 질문만 주면 호출자가 유효한 답의 집합을 모른다.
    out = await resolve_scope("증상", sites=[("mx", "gumi"), ("mx", "suwon")],
                              deps=_deps("파싱 불가"))
    joined = " ".join(out.questions)
    assert "mx/gumi" in joined and "mx/suwon" in joined


async def test_한쪽만_준_스코프가_후보를_좁힌다():
    # `if gbm is not None and fct is not None`이면 한쪽만 준 지정이 통째로 무시돼
    # LLM이 **다른 법인**을 고를 수 있다 — 이 모듈이 지키겠다고 선언한 그 축이다.
    out = await resolve_scope("수원", sites=[("mx", "gumi"), ("mx", "suwon"), ("g2", "x")],
                              deps=_deps('{"gbm": "mx", "fct": "suwon"}'),
                              gbm="mx")
    assert (out.status, out.gbm, out.fct) == ("resolved", "mx", "suwon")


async def test_한쪽만_준_지정이_하나로_좁혀지면_LLM_없이_확정한다():
    calls = []
    out = await resolve_scope("증상", sites=[("mx", "gumi"), ("g2", "x")],
                              deps=_deps(spy=calls), fct="gumi")
    assert (out.status, out.gbm, out.fct) == ("resolved", "mx", "gumi")
    assert calls == []


async def test_한쪽만_준_지정이_아무것도_안_남기면_미확정이다():
    out = await resolve_scope("증상", sites=[("mx", "gumi")], deps=_deps(),
                              gbm="없는사업부")
    assert out.status == "unresolved" and any("없는사업부" in p for p in out.problems)


async def test_한쪽만_준_지정_밖을_LLM이_고르면_미확정이다():
    out = await resolve_scope("증상", sites=[("mx", "gumi"), ("mx", "suwon"), ("g2", "x")],
                              deps=_deps('{"gbm": "g2", "fct": "x"}'),
                              gbm="mx")
    # 후보를 mx로 좁혔으므로 g2는 목록 밖이다.
    assert out.status == "unresolved" and out.candidates == [("mx", "gumi"), ("mx", "suwon")]
