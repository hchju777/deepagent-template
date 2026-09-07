"""시나리오는 사이트 계층이 아니라 자기 파일에서 단독 검증된다(계획 16/P7).

`patrol.checks` 확장이 아닌 이유(방향 문서 §4.3): ①전역 시나리오를 사이트 층에 넣으면
사이트마다 잡이 등록돼 같은 집계가 N번 돌고 메일도 N통 간다 ②`CheckConfig.judge`가
필수인데 집계엔 판정이 없다 ③한 dict에 두 모양을 섞으면 다섯 소비자가 kind 분기를 한다.
"""
import json

import pytest

from src.config.loader import ConfigError, load_scenarios
from src.config.schema_scenario import ScenarioConfig

_MIN = {"kind": "aggregate", "concern": "operation", "title": "알람 추세",
        "schedule": {"cron": "0 7 * * *"},
        "metrics": {"alarms": {"target": "rest:alarm_count", "extract": "summary.alarms",
                               "reduce": "sum"}}}


def _write(root, name, data):
    (root / "scenarios").mkdir(parents=True, exist_ok=True)
    (root / "scenarios" / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


def test_시나리오는_파일_하나로_읽힌다(tmp_path):
    _write(tmp_path, "alarm_trend", _MIN)
    scenarios = load_scenarios(tmp_path, env={})
    assert list(scenarios) == ["alarm_trend"]
    s = scenarios["alarm_trend"]
    assert s.kind == "aggregate" and s.concern == "operation" and s.enabled is True
    assert s.metrics["alarms"].reduce == "sum" and s.scope.sites == "all"
    assert s.scope.max_parallel_sites == 4 and s.output.format == "html"


def test_시나리오_디렉터리가_없으면_빈_목록이다(tmp_path):
    # 집계는 선택 기능이다 — 안 쓰는 배치가 기동에서 죽으면 안 된다.
    assert load_scenarios(tmp_path, env={}) == {}


def test_오류는_파일명과_함께_보고된다(tmp_path):
    _write(tmp_path, "broken", {**_MIN, "kind": "unknown"})
    with pytest.raises(ConfigError) as exc:
        load_scenarios(tmp_path, env={})
    assert any("broken.json" in p for p in exc.value.problems)


def test_알_수_없는_키와_빈_지표는_거부된다(tmp_path):
    with pytest.raises(Exception):
        ScenarioConfig.model_validate({**_MIN, "이상한키": 1})
    with pytest.raises(Exception):
        ScenarioConfig.model_validate({**_MIN, "metrics": {}})


def test_병렬_상한은_양수여야_한다(tmp_path):
    # 상한이 있어야 하는 것은 코드가 쥔다(규율 6) — 0이면 팬아웃이 영원히 멈춘다.
    with pytest.raises(Exception):
        ScenarioConfig.model_validate({**_MIN, "scope": {"max_parallel_sites": 0}})


def test_스케줄은_기존_검증을_그대로_쓴다(tmp_path):
    with pytest.raises(Exception):
        ScenarioConfig.model_validate({**_MIN, "schedule": {"cron": "0 7 * * *",
                                                            "interval": "5m"}})


def test_env_참조가_실제로_치환된다(tmp_path):
    # "설정했는데 안 켜진다"는 형태의 조용한 고장이 이 리포의 반복 실패 유형이다.
    _write(tmp_path, "alarm_trend", {**_MIN, "output": {"output_dir": "${FLEET_OUT}"}})
    scenarios = load_scenarios(tmp_path, env={"FLEET_OUT": "/tmp/fleet"})
    assert scenarios["alarm_trend"].output.output_dir == "/tmp/fleet"
    with pytest.raises(ConfigError):
        load_scenarios(tmp_path, env={})


def test_사이트는_시나리오를_옵트아웃할_수_있다():
    # 지표 재정의는 하지 않는다 — 그러면 "같은 시나리오"가 사이트마다 다른 것을 재고
    # 집계가 무의미해진다. 켜고 끄는 것만 사이트의 몫이다.
    from src.config.schema_site import SitePatrol
    patrol = SitePatrol.model_validate({"scenarios": {"alarm_trend": {"enabled": False}}})
    assert patrol.scenarios["alarm_trend"].enabled is False
    with pytest.raises(Exception):
        SitePatrol.model_validate({"scenarios": {"alarm_trend": {"metrics": {}}}})
