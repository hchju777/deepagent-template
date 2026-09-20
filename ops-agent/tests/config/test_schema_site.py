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
        KafkaConsumerConfig(bootstrap_server=["gumi-kafka-1"], group_ids=["g"])


def test_브로커가_비면_거부한다():
    with pytest.raises(ValidationError, match="비어 있다"):
        KafkaConsumerConfig(bootstrap_server=[], group_ids=["g"])


def test_감시할_것이_없으면_거부한다():
    # 그룹도 토픽도 없는 kafka 설정은 "붙기만 하고 아무것도 안 본다"는 뜻이다.
    with pytest.raises(ValidationError, match="무의미하다"):
        KafkaConsumerConfig(bootstrap_server=["h:9092"])


def test_감시_그룹을_여러_개_받는다():
    # 한 법인의 같은 Kafka에 서비스가 여러 개 붙어 있고 각자 자기 그룹을 쓴다.
    cfg = KafkaConsumerConfig(bootstrap_server=["h:9092"],
                              group_ids=["dt-processor-mx-gumi", "dt-sink-mx-gumi"])
    assert len(cfg.group_ids) == 2


def test_group_ids의_중복을_거부한다():
    # 같은 그룹이 두 번 있으면 lag 합계가 두 번 더해진다.
    with pytest.raises(ValidationError, match="중복이 있다"):
        KafkaConsumerConfig(bootstrap_server=["h:9092"], group_ids=["dt-sink", "dt-sink"])


def test_group_ids는_감시_대상이라는_뜻이_문서에_있다():
    # 이 뜻을 놓치면 모니터링이 운영 컨슈머의 파티션을 빼앗는다.
    assert "감시할 그룹들" in KafkaConsumerConfig.__doc__


# ── 대상 코드 레포 (11a) ───────────────────────────────────────────

def test_url에_토큰이_섞이면_거부한다():
    """`https://<토큰>@호스트/…`로 클론하면 git이 그 URL을 **`.git/config`에 평문으로
    저장한다.** 토큰이 디스크에 남고, 그 파일은 백업·이미지·로그 어디로든 따라간다.

    그래서 url은 깨끗하게 두고 인증은 명령마다 헤더로 넘긴다.

    **실패하면 스스로 원인을 말한다.** 이 테스트가 사내에서 `DID NOT RAISE`로 깨졌는데
    개발 기계에서는 재현이 안 됐다. "안 걸렸다"만으로는 트리가 낡은 것인지 환경이
    다른 것인지 구분할 수 없어서, 한 번 더 물어봐야 했다 — 그 왕복이 낭비다.
    """
    import inspect

    import pytest

    from src.config.schema_site import RepoConfig

    dirty = "https://ghp_tok@git.example.com/team/dt-core"
    try:
        passed = RepoConfig(name="dt-core", url=dirty, path="/srv/dt-core")
    except ValueError as exc:
        assert "인증 정보가 섞여" in str(exc), str(exc)
        return

    # **validator 본문을 그대로 보여 준다.** 처음엔 "메시지 문자열이 소스에 있나"로
    # 봤는데, 검사를 꺼도 그 문자열은 raise 본문에 남아 있어서 **항상 "있다"가 나왔다.**
    # 검사기가 검사를 못 하는 것이 여기서 고치려던 바로 그 문제다.
    lines = inspect.getsource(RepoConfig).splitlines()
    start = next((i for i, l in enumerate(lines) if "def _clean" in l), None)
    body = "\n".join(lines[start:start + 12]) if start is not None else "(_clean이 없다)"
    pytest.fail(
        f"토큰이 섞인 url이 통과했다 — {passed.url!r}\n"
        f"  파일: {inspect.getfile(RepoConfig)}\n"
        f"  지금 돌고 있는 validator:\n{body}")


def test_ssh_형식은_받는다():
    from src.config.schema_site import RepoConfig

    assert RepoConfig(name="x", url="git@git.example.com:team/dt-core.git",
                      path="/srv/x").url.endswith("dt-core")


def test_git_꼬리를_떼어_보관한다():
    """`code status`의 origin 대조가 `.git` 유무로 실패하면 사람이 자물쇠를 끈다."""
    from src.config.schema_site import RepoConfig

    assert RepoConfig(name="x", url="https://g/team/x.git", path="/p").url == "https://g/team/x"


def test_레포_이름이_중복이면_거부한다():
    """토폴로지의 `Service.repo`가 어느 쪽을 가리키는지 알 수 없게 된다."""
    import pytest

    from src.config.schema_site import CodeConfig

    with pytest.raises(ValueError, match="중복"):
        CodeConfig(repos=[{"name": "a", "url": "https://g/a", "path": "/1"},
                          {"name": "a", "url": "https://g/b", "path": "/2"}])
