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


# `tests/support.py`가 갖는다 — CLI 테스트들도 같은 것을 쓴다. 두 벌이면 한쪽만
# 고쳐지고, 그 차이가 "관계없는 테스트가 깨진다"로 나타난다.
from tests.support import env_references, placeholder_env  # noqa: E402


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
    import pytest
    from dotenv import dotenv_values

    if not (REPO / ".env.example").exists():
        # 사내 트리에는 `.env.example`이 없다 — 운영은 `.env`만 둔다. 이건
        # **템플릿 리포의 문서가 낡았는지** 보는 검사라, 없으면 볼 것이 없다.
        pytest.skip(".env.example이 없다 — 템플릿 리포에서만 도는 검사다")

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


# ── LLM 코멘트 설정 ─────────────────────────────────────────────────

def test_프롬프트_경로는_config_안이어야_한다():
    """사람이 적는 config 값으로 config 트리 밖 파일을 읽게 하면 안 된다."""
    from src.config.schema_report import CommentSpec

    for bad in ("/etc/passwd", "../secret.txt", "a/../../b"):
        with pytest.raises(ValidationError, match="상대 경로"):
            CommentSpec(prompt_file=bad)


def test_코멘트는_기본으로_꺼져_있다():
    """LLM 호출은 돈과 시간을 쓴다 — 켜는 것이 명시적 선택이어야 한다."""
    from src.config.schema_report import CommentSpec

    assert CommentSpec().enabled is False


def write_prompt(root: Path, text: str, name: str | None = None) -> None:
    """이름을 안 주면 **스키마의 기본값 자리**에 쓴다.

    여기에 파일 이름을 또 적으면 스키마 기본값과 조용히 어긋난다 — `.txt`를 `.md`로
    바꿀 때 실제로 그럴 뻔했다.

    단, 이것만으로는 **둘이 같이 틀리는 것**을 못 잡는다(자기가 쓴 파일을 자기가
    읽으니까). 기본값이 배포되는 config의 실재 파일을 가리키는지는
    `test_스키마_기본_프롬프트가_배포되는_config에_있다`가 본다.
    """
    from src.config.schema_report import CommentSpec

    path = root / (name or CommentSpec().prompt_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_프롬프트_파일을_읽는다(tmp_path):
    from src.config.loader import load_prompt

    write(tmp_path, "daily-alarm.json", scenario(comment={"enabled": True}))
    write_prompt(tmp_path, "사실:\n{facts}\n")
    loaded = load_scenarios(tmp_path)["daily-alarm"]
    assert "{facts}" in load_prompt(tmp_path, loaded)


def test_facts_자리가_없으면_거부한다(tmp_path):
    """없으면 LLM이 숫자를 하나도 못 받고 전부 폐기된다 — "코멘트가 항상 비어
    있다"는 증상으로만 드러나서 원인을 찾기 어렵다."""
    from src.config.loader import load_prompt

    write(tmp_path, "daily-alarm.json", scenario(comment={"enabled": True}))
    write_prompt(tmp_path, "숫자 없이 잘 써 봐라")
    loaded = load_scenarios(tmp_path)["daily-alarm"]
    with pytest.raises(ConfigError, match="자리가 없다"):
        load_prompt(tmp_path, loaded)


def test_프롬프트_파일이_없으면_기동에서_막힌다(config_root):
    """밤에 "코멘트가 비어 있다"로만 드러나는 종류다."""
    write(config_root, "daily-alarm.json", scenario(comment={"enabled": True}))
    assert "프롬프트 파일이 없다" in messages(config_root)


def test_코멘트가_꺼져_있으면_프롬프트를_따지지_않는다(config_root):
    """안 쓰는 파일 때문에 기동이 막히면 안 된다."""
    write(config_root, "daily-alarm.json", scenario(comment={"enabled": False}))
    assert messages(config_root) == ""


def test_스키마_기본_프롬프트가_배포되는_config에_있다():
    """`prompt_file`을 생략한 시나리오가 없는 파일을 가리키게 되는 것을 막는다.

    기동이 거부하므로 조용한 실패는 아니다 — 그래도 프롬프트 파일 이름을 바꾸는 날
    여기서 걸리는 편이, 새 시나리오를 추가하는 사람이 기동 오류로 만나는 것보다 낫다.
    """
    from src.config.schema_report import CommentSpec

    shipped = Path(__file__).resolve().parent.parent.parent / "config"

    assert (shipped / CommentSpec().prompt_file).exists(), (
        f"스키마 기본값({CommentSpec().prompt_file})이 가리키는 파일이 config에 없다")
