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
CONFIG = REPO / "config"


def env_references(root: Path) -> set[str]:
    """config 트리가 `${...}`로 참조하는 env 이름 전부.

    로더와 **같은 정규식**을 쓴다 — 여기서 따로 패턴을 베끼면 로더가 인식하는
    참조를 테스트는 못 보는 상태가 생긴다(`${MY-KEY}`를 놓쳤던 적이 있다).
    """
    from src.config.envresolve import _REFERENCE

    names: set[str] = set()
    for path in sorted(root.rglob("*.json")):
        names |= set(_REFERENCE.findall(path.read_text(encoding="utf-8")))
    return names


def placeholder_env(names: set[str]) -> dict[str, str]:
    """이름에서 형식만 맞는 가짜 값을 만든다. 접속은 하지 않으므로 모양만 맞으면 된다.

    이름을 손으로 적은 목록을 두지 않는 이유: config에 새 참조가 생기면 그 목록이
    조용히 낡고, 테스트는 "env가 비어 있다"로 실패해 **새 참조가 문제인지 설정이
    문제인지** 구분이 안 된다.
    """
    return {name: ("https://x" if name.endswith("_URL") else "dummy") for name in names}


def real_env() -> dict[str, str] | None:
    """`.env`가 있으면 그 값. CLI와 같은 출처여야 "통과했다"가 거짓이 아니다.

    `os.environ`에 심지 않는다 — 테스트가 전역을 오염시키면 실행 순서에 따라
    다른 테스트의 결과가 바뀐다.
    """
    env_file = REPO / ".env"
    if not env_file.exists():
        return None
    from dotenv import dotenv_values
    return {**{k: v for k, v in dotenv_values(env_file).items() if v}, **os.environ}


def test_config_트리가_실제로_통과한다():
    """리포에 든 설정이 스스로 깨져 있으면 아무도 그것을 본보기로 못 쓴다.

    `.env`가 있으면 **그것으로** 본다 — 사내에서 pytest가 곧 기동 전 점검이 된다.
    없으면(갓 클론한 트리, CI) 가짜 값으로 형식만 본다.
    """
    env = real_env() or placeholder_env(env_references(CONFIG))
    errors = validate_boot(CONFIG, env=env)
    assert errors == [], "config/ 기동 검증 실패:\n" + "\n".join(str(e) for e in errors)


def test_env_참조와_env_example이_어긋나지_않는다():
    """`.env.example`은 "어떤 키가 필요한가"의 문서다. 문서가 낡으면 사내에서
    배포할 때 **어떤 키를 채워야 하는지** 알 방법이 없다(config 전체를 grep하는
    수밖에 없다).
    """
    from dotenv import dotenv_values

    documented = set(dotenv_values(REPO / ".env.example"))
    referenced = env_references(CONFIG)
    assert referenced - documented == set(), \
        f"config가 참조하는데 .env.example에 없다 — {sorted(referenced - documented)}"
    assert documented - referenced == set(), \
        f".env.example에 있는데 config가 안 쓴다 — {sorted(documented - referenced)}"


def test_시나리오_파일이_리포에_들어_있다():
    """`report window`가 "시나리오가 없다"로 막히던 적이 있다 — 예제 트리에만
    넣고 실제 트리에는 없었기 때문이다. 트리가 하나가 된 뒤에도 그 파일이
    사라지지 않는지 본다."""
    assert load_scenarios(CONFIG), f"{CONFIG / 'scenarios'}가 비어 있다"
