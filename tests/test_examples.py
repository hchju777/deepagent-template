"""예시 트리(config.example)와 그것을 설명하는 문서가 서로 어긋나지 않는지 본다.

README의 "5초만 띄워본다 — 실제로 한 번 돈다"가 300초 간격 위에서 한동안 거짓이었다.
스케줄러의 첫 발화는 t+interval이므로 창보다 간격이 크면 아무것도 안 돈다. 그 사실은
어떤 단위 테스트에도 안 걸렸다 — 데몬을 스파이로 대체하는 CLI 테스트는 트리거를
지나가지 않기 때문이다. 그래서 문서가 약속한 창과 예시 간격을 직접 대조한다.
"""
import json
import re
from pathlib import Path

from src.config.schema_site import Schedule
from src.patrol.scheduler import interval_seconds

ROOT = Path(__file__).resolve().parent.parent


def _documented_window() -> int:
    """README 빠른 시작이 약속한 `patrol run --for-seconds N`의 N."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    found = re.findall(r"patrol run --for-seconds (\d+)", readme)
    assert found, "README에서 patrol run 빠른 시작 명령을 못 찾았다"
    return min(int(n) for n in found)


def test_예시_점검은_README가_약속한_창_안에_발화한다():
    window = _documented_window()
    for path in (ROOT / "config.example").rglob("*.json"):
        checks = json.loads(path.read_text(encoding="utf-8")).get("patrol", {}).get("checks", {})
        for name, check in checks.items():
            schedule = Schedule.model_validate(check["schedule"])
            if schedule.interval is None:
                continue          # cron은 "언제 도는가"를 창으로 약속할 수 없다
            seconds = interval_seconds(schedule.interval)
            assert seconds < window, (
                f"{path.name}의 점검 {name!r} 간격 {schedule.interval}이 README가 약속한 "
                f"{window}초 창보다 크다 — 빠른 시작이 아무것도 안 보여준다")


def test_튜토리얼이_추가하라는_점검도_같은_창_안에_발화한다():
    # 같은 버그가 README에서 고쳐지고 tutorial에는 남아 있었다 — 문서대로 따라 하면
    # 아무 일도 안 일어난다. 두 문서를 같은 기준으로 묶는다.
    window = _documented_window()
    text = (ROOT / "docs" / "tutorial.md").read_text(encoding="utf-8")
    intervals = re.findall(r'"interval":\s*"(\d+[smh])"', text)
    assert intervals, "튜토리얼에서 점검 간격 예시를 못 찾았다"
    for spec in intervals:
        assert interval_seconds(spec) < window, (
            f"튜토리얼이 적은 간격 {spec}이 README가 약속한 {window}초 창보다 크다")


def test_README_빠른_시작이_시드_파일을_실제로_가리킨다():
    # 시드를 플래그로 옮기면 README 명령이 플래그 없이는 아무것도 안 보여준다 —
    # 그 결합을 테스트가 잡는다(전에는 간격이 그랬다).
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"patrol run [^\n]*--stub-seeds (\S+)", readme)
    assert match, "README 빠른 시작에 --stub-seeds가 없다"
    assert (ROOT / match.group(1)).exists(), match.group(1)
