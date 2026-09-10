"""시나리오 선언 — 사람이 손으로 적는 곳이라 오타가 조용히 통과하면 안 된다.

여기서 막는 것들은 전부 "런타임에는 오류가 안 나고 **숫자만 틀리는**" 종류다:
GBM 목록에 없는 법인, 법인이 하나도 없는 GBM, 중복된 사이트 — 셋 다 리포트의
분모를 바꾸는데 아무 예외도 안 낸다.
"""
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.boot import validate_boot
from src.config.loader import ConfigError, load_scenarios
from src.config.schema_report import ReportScenario, ReportScope, SourceSpec, WindowSpec


def scenario(**overrides) -> dict:
    body = {
        "kind": "alarm_daily",
        "title": "일일 알람 리포트",
        "source": {"collection": "alarm", "date_field": "occ_date"},
        "scope": {"gbms": ["mx"], "sites": ["mx/gumi"]},
    }
    body.update(overrides)
    return body


def test_최소_선언으로_기본값이_채워진다():
    parsed = ReportScenario.model_validate(scenario())
    assert parsed.window.business_days == 7
    assert parsed.window.excluded() == {5, 6}
    assert parsed.source.fields.scenario_name == "scen_name"
    assert parsed.source.unresolved_status == [0, 10, 1]
    assert parsed.enabled is True


def test_모르는_키는_거부된다():
    """StrictModel — `bussiness_days` 같은 오타가 조용히 무시되면 기간이 바뀐다."""
    with pytest.raises(ValidationError):
        ReportScenario.model_validate(scenario(window={"bussiness_days": 3}))


# ── 기간 ────────────────────────────────────────────────────────────────

def test_모르는_요일_이름은_거부된다():
    with pytest.raises(ValidationError, match="모르는 요일"):
        WindowSpec(exclude_weekdays=["sat", "sunday"])


def test_모든_요일을_제외하면_거부된다():
    """통과시키면 `previous_business_day`가 돌 날을 못 찾는다."""
    with pytest.raises(ValidationError, match="조회할 날이 없다"):
        WindowSpec(exclude_weekdays=["mon", "tue", "wed", "thu", "fri", "sat", "sun"])


@pytest.mark.parametrize("days", [0, -1, 61])
def test_기간에_상하한이_있다(days):
    """0이면 빈 리포트가, 너무 크면 대상 Mongo를 오래 긁는다."""
    with pytest.raises(ValidationError):
        WindowSpec(business_days=days)


# ── 읽는 곳 ─────────────────────────────────────────────────────────────

def test_표본_상한은_1_이상이다():
    """pymongo에서 limit 0은 **무제한**이다 — 법인 수만큼 무제한 커서가 열린다."""
    with pytest.raises(ValidationError):
        SourceSpec(collection="alarm", date_field="occ_date", sample=0)


def test_미해제_status에_중복이_있으면_거부된다():
    with pytest.raises(ValidationError, match="중복"):
        SourceSpec(collection="alarm", date_field="occ_date", unresolved_status=[0, 0, 10])


def test_컬렉션_이름은_코드가_아니라_config가_안다():
    parsed = ReportScenario.model_validate(
        scenario(source={"collection": "event_log", "date_field": "created_at",
                         "fields": {"scenario_name": "event_name"}}))
    assert parsed.source.collection == "event_log"
    assert parsed.source.fields.scenario_name == "event_name"
    assert parsed.source.fields.line_code == "line_code", "안 적은 필드는 기본값"


# ── 대상 범위 ───────────────────────────────────────────────────────────

def test_사이트는_gbm_슬래시_fct_형식이다():
    with pytest.raises(ValidationError, match="형식"):
        ReportScope(gbms=["mx"], sites=["gumi"])


def test_gbms에_없는_GBM의_사이트는_거부된다():
    with pytest.raises(ValidationError, match="gbms 목록에 없다"):
        ReportScope(gbms=["mx"], sites=["mx/gumi", "vd/suwon"])


def test_법인이_하나도_없는_GBM은_거부된다():
    """빈 열은 "0건"과 "대상 아님"을 구분해 주지 않는다."""
    with pytest.raises(ValidationError, match="대상 법인이 하나도 없는"):
        ReportScope(gbms=["mx", "vd"], sites=["mx/gumi"])


def test_중복된_사이트는_거부된다():
    with pytest.raises(ValidationError, match="중복"):
        ReportScope(gbms=["mx"], sites=["mx/gumi", "mx/gumi"])


def test_GBM별_법인_목록을_뽑는다():
    scope = ReportScope(gbms=["mx", "vd"], sites=["mx/gumi", "vd/suwon", "mx/sevt"])
    assert scope.sites_of("mx") == ["gumi", "sevt"]
    assert scope.sites_of("vd") == ["suwon"]


# ── 로더 ────────────────────────────────────────────────────────────────

def write(root: Path, name: str, body: dict) -> None:
    (root / "scenarios").mkdir(parents=True, exist_ok=True)
    (root / "scenarios" / name).write_text(json.dumps(body), encoding="utf-8")


def test_이름은_파일_이름이_정한다(tmp_path):
    write(tmp_path, "daily-alarm.json", scenario())
    write(tmp_path, "weekly.json", scenario(title="주간"))
    loaded = load_scenarios(tmp_path)
    assert list(loaded) == ["daily-alarm", "weekly"], "정렬돼 있어야 결정론이다"
    assert loaded["weekly"].title == "주간"


def test_디렉터리가_없으면_빈_결과다(tmp_path):
    """리포트를 안 쓰는 배포도 있다 — 없는 것은 오류가 아니다."""
    assert load_scenarios(tmp_path) == {}


def test_스키마_오류는_파일_이름과_함께_보고된다(tmp_path):
    write(tmp_path, "daily-alarm.json", scenario(scope={"gbms": ["mx"], "sites": ["gumi"]}))
    with pytest.raises(ConfigError, match="scenarios/daily-alarm.json"):
        load_scenarios(tmp_path)


# ── 기동 검증 ───────────────────────────────────────────────────────────

@pytest.fixture
def config_root(tmp_path) -> Path:
    (tmp_path / "app.json").write_text(json.dumps({"timezone": "Asia/Seoul"}),
                                       encoding="utf-8")
    (tmp_path / "registry.json").write_text(json.dumps({"sites": [
        {"gbm": "mx", "fct": "gumi"},
        {"gbm": "mx", "fct": "sevt", "enabled": False}]}), encoding="utf-8")
    (tmp_path / "fct" / "gumi").mkdir(parents=True)
    (tmp_path / "fct" / "gumi" / "mx.json").write_text(
        json.dumps({"infra": {"redis": {"url": "redis://h:6379"}}}), encoding="utf-8")
    return tmp_path


def messages(root: Path) -> str:
    return "\n".join(str(e) for e in validate_boot(root, env={}))


def test_사전순이_깨지는_날짜_형식은_기동에서_막힌다(config_root):
    """런타임에는 오류 없이 **틀린 구간**을 읽는다 — 새벽 메일이 나간 뒤에 발견된다."""
    write(config_root, "daily-alarm.json", scenario(
        source={"collection": "alarm", "date_field": "occ_date",
                "date_format": "%d/%m/%Y"}))
    assert "사전순이 시간순과 다르다" in messages(config_root)


def test_registry에_없는_사이트는_기동에서_막힌다(config_root):
    """오타는 런타임에 고칠 수 없다."""
    write(config_root, "daily-alarm.json", scenario(
        scope={"gbms": ["mx"], "sites": ["mx/gumi", "mx/gumii"]}))
    assert "registry.json에 없는 사이트 — mx/gumii" in messages(config_root)


def test_registry에서_꺼둔_사이트는_기동을_막지_않는다(config_root):
    """"지금 못 붙는다"는 정상 상태다. 리포트는 그 법인을 제외 목록에 이름으로 남긴다
    — 분모에서 조용히 사라지는 것만 아니면 된다."""
    write(config_root, "daily-alarm.json", scenario(
        scope={"gbms": ["mx"], "sites": ["mx/gumi", "mx/sevt"]}))
    assert messages(config_root) == ""


def test_시나리오가_없어도_기동은_통과한다(config_root):
    assert messages(config_root) == ""


# cwd가 아니라 파일 위치 기준 — 다른 디렉터리에서 pytest를 돌려도 같아야 한다.
REPO = Path(__file__).resolve().parents[2]

# 예제 트리의 `${...}`를 채우는 가짜 값. 접속은 하지 않으므로 형식만 맞으면 된다.
EXAMPLE_ENV = {"LLM_BASE_URL": "https://x", "LLM_PASS_KEY": "a", "LLM_CLIENT_KEY": "b",
               "MAIL_AGENT_ID": "c", "MAIL_AGENT_API_KEY": "d",
               "REDIS_PASSWORD": "e", "MONGO_PASSWORD": "f"}


def test_예제_config가_실제로_통과한다():
    """리포에 든 예제가 스스로 깨져 있으면 아무도 그것을 본보기로 못 쓴다.

    `config.example`을 보는 이유: 리포에 **들어 있는** 트리라 갓 클론한 곳에서도
    돈다. 실제로 쓰는 `config/`는 사람이 만들고 리포에 없으므로, 이 단정을
    거기로 옮기면 클론 직후 pytest가 빨갛게 뜬다.
    """
    assert (REPO / "config.example").is_dir(), "예제 트리가 사라졌다"
    assert validate_boot(REPO / "config.example", env=EXAMPLE_ENV) == []


def test_실제_config가_있으면_그것도_통과한다():
    """`config/`가 있으면 그것도 본다 — 사내에서 pytest가 곧 기동 전 점검이 된다.

    env는 CLI와 같은 출처(`.env`)를 쓴다. 가짜 값으로 보면 "사내 트리는 통과했다"가
    거짓이 된다 — 실제로 비어 있는 env 참조를 못 잡기 때문이다.
    `.env`를 읽기만 하고 `os.environ`에 심지는 않는다(테스트가 전역을 오염시키면
    순서에 따라 다른 테스트 결과가 바뀐다).
    """
    root = REPO / "config"
    if not root.is_dir():
        pytest.skip("config/는 사람이 만든다 — 리포에는 config.example만 들어 있다")

    env = dict(os.environ)
    env_file = REPO / ".env"
    if env_file.exists():
        from dotenv import dotenv_values
        env = {**{k: v for k, v in dotenv_values(env_file).items() if v}, **env}

    errors = validate_boot(root, env=env)
    assert errors == [], "config/ 기동 검증 실패:\n" + "\n".join(str(e) for e in errors)
