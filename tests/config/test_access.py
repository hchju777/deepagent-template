"""접근 술어 — 필드 1개, 포트 1개, 검사 1곳(스펙 §3.5).

읽기 전용이라고 폭발 반경이 작지 않다. 인증 없는 `POST /cases {gbm:"mx",
fct:"suwon"}`은 실질적으로 "수원 법인의 Redis/Mongo/Kafka와 소스 저장소에 읽기
권한을 가진 LLM 에이전트를 돌리고 결과를 메일로 보내라"는 요청이다.
"""
import pytest
from pydantic import ValidationError

from src.config.schema_app import AccessPolicy


def test_선언이_없으면_전부_허용한다():
    # 단일 팀 설치에 인증 설정을 강요하지 않는다 — 빈 테이블은 "제한 없음"이다.
    policy = AccessPolicy()
    assert policy.can_access("alice", "mx", "gumi")
    assert policy.can_access(None, "mx", "gumi")


def test_선언이_있으면_목록_밖은_거부한다():
    policy = AccessPolicy(allow={"alice": ["mx/gumi"]})
    assert policy.can_access("alice", "mx", "gumi")
    assert not policy.can_access("alice", "mx", "suwon")
    assert not policy.can_access("bob", "mx", "gumi")          # 선언 없는 주체


def test_사업부_와일드카드():
    policy = AccessPolicy(allow={"alice": ["mx/*"]})
    assert policy.can_access("alice", "mx", "suwon")
    assert not policy.can_access("alice", "gbm2", "suwon")


def test_주체가_없으면_거부한다():
    # 익명 요청이 선언된 테이블을 통과하면 인증이 없는 것과 같다.
    assert not AccessPolicy(allow={"alice": ["mx/gumi"]}).can_access(None, "mx", "gumi")
    assert not AccessPolicy(allow={"alice": ["mx/gumi"]}).can_access("", "mx", "gumi")


def test_sites_for는_읽기_필터의_근거다():
    # 접수만 막고 읽기를 안 막으면 무의미하다 — 같은 술어를 목록 API도 쓴다.
    policy = AccessPolicy(allow={"alice": ["mx/gumi", "mx/suwon"]})
    assert policy.sites_for("alice") == [("mx", "gumi"), ("mx", "suwon")]
    assert policy.sites_for("bob") == []                       # 선언 없는 주체는 아무것도 못 본다
    # None과 []는 다르다: None은 "제한 없음", []는 "아무것도 못 봄".
    assert AccessPolicy().sites_for("alice") is None


def test_와일드카드_주체는_구체_사이트로_펼칠_수_없다():
    # sites_for가 목록을 돌려줘야 하는데 mx/*는 어느 fct들인지 모른다 — registry를
    # 모르는 이 계층이 지어내면 안 된다. None(제한 없음)으로 뭉개는 것도 위험하다.
    policy = AccessPolicy(allow={"alice": ["mx/*"]})
    assert policy.sites_for("alice", known=[("mx", "gumi"), ("g2", "x")]) == [("mx", "gumi")]
    with pytest.raises(ValueError):
        policy.sites_for("alice")                              # known 없이는 펼칠 수 없다


def test_모양이_틀린_선언은_거부한다():
    for bad in ({"alice": ["mx"]}, {"alice": ["mx/gumi/extra"]}, {"alice": ["/gumi"]},
                {"alice": ["*/gumi"]}, {"": ["mx/gumi"]}):
        with pytest.raises(ValidationError):
            AccessPolicy(allow=bad)
