"""사이트 스키마 — 오타와 모순을 기동에서 잡는다."""
import pytest
from pydantic import ValidationError

from src.config.schema_site import KafkaConsumerConfig, MongoConfig, RedisConfig, SiteConfig

MINIMAL = {"site": {"gbm": "mx", "fct": "gumi"},
           "infra": {"redis": {"url": "redis://h:6379"}}}


def test_최소_설정이_통과한다():
    assert str(SiteConfig.model_validate(MINIMAL).site) == "mx/gumi"


def test_오타난_키를_거부한다():
    bad = {"site": {"gbm": "mx", "fct": "gumi"},
           "infra": {"redis": {"url": "redis://h:6379", "passwrod": "x"}}}
    with pytest.raises(ValidationError, match="passwrod"):
        SiteConfig.model_validate(bad)


def test_대상_시스템이_하나도_없으면_거부한다():
    with pytest.raises(ValidationError, match="무엇을 조사하라는 것인가"):
        SiteConfig.model_validate({"site": {"gbm": "mx", "fct": "gumi"}, "infra": {}})


# ── Redis ────────────────────────────────────────────────────────────

def test_redis_스킴이_틀리면_거부한다():
    with pytest.raises(ValidationError, match="redis://"):
        RedisConfig(url="http://h:6379")


def test_redis_db_범위를_벗어나면_거부한다():
    with pytest.raises(ValidationError, match="0~15"):
        RedisConfig(url="redis://h:6379", db=16)


def test_비밀번호는_repr에_안_찍힌다():
    cfg = RedisConfig(url="redis://h:6379", password="hunter2")
    assert "hunter2" not in repr(cfg)
    assert "hunter2" not in str(cfg)
    assert cfg.password.get_secret_value() == "hunter2"   # 꺼낼 때만 명시적으로


# ── Mongo ────────────────────────────────────────────────────────────

def test_user만_있고_비밀번호가_없으면_거부한다():
    # 런타임에 죽으면 "권한 없음"으로 보여 계정 문제로 오해하게 된다.
    with pytest.raises(ValidationError, match="password도 있어야 한다"):
        MongoConfig(url="mongodb://h:27017", database="data", user="dmfReadOnly")


def test_비밀번호만_있고_user가_없어도_거부한다():
    with pytest.raises(ValidationError, match="user가 없다"):
        MongoConfig(url="mongodb://h:27017", database="data", password="x")


def test_auth_source_기본값은_admin이다():
    # `use admin` 후 db.createUser로 만든 계정은 admin DB에 산다.
    cfg = MongoConfig(url="mongodb://h:27017", database="data",
                      user="dmfReadOnly", password="x")
    assert cfg.auth_source == "admin"


# ── Kafka ────────────────────────────────────────────────────────────

def test_브로커_주소_형식을_검사한다():
    with pytest.raises(ValidationError, match="host:port"):
        KafkaConsumerConfig(bootstrap_server=["gumi-kafka-1"], group_id="g",
                            topic={"topic1": "T"})


def test_브로커가_비면_거부한다():
    with pytest.raises(ValidationError, match="비어 있다"):
        KafkaConsumerConfig(bootstrap_server=[], group_id="g", topic={"topic1": "T"})


def test_토픽이_비면_거부한다():
    with pytest.raises(ValidationError, match="무의미하다"):
        KafkaConsumerConfig(bootstrap_server=["h:9092"], group_id="g", topic={})


def test_group_id는_감시_대상이라는_뜻이_문서에_있다():
    # 이 뜻을 놓치면 모니터링이 운영 컨슈머의 파티션을 빼앗는다.
    assert "감시할 그룹" in KafkaConsumerConfig.__doc__
