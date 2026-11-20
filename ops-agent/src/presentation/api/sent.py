"""API가 장부(`src/report/ledger.py`)를 보는 자리 — 시나리오 고르기, 기준일, 오늘의 발송 상태.

기준일은 CLI와 **같은 함수**(`build_window(...).yesterday`)로 센다. 여기서 따로 세면 API는 "안 보냄"인데 CLI는 "이미 보냄"인
날이 생긴다(월요일 기준일은 금요일이다).
"""
import json
from datetime import date, datetime, timedelta

from src.config.loader import load_scenarios
from src.report import ledger
from src.report.window import build_window
from src.schedule.cron import next_fire


def pick_scenario(config_root, name: str | None):
    """(이름, 시나리오) 또는 (None, 이유). 이름이 없으면 **하나일 때만** 그것 — CLI `_pick_scenario`와 같은 규칙."""
    try:
        scenarios = load_scenarios(config_root)
    except Exception as exc:                                       # noqa: BLE001
        return None, f"시나리오를 못 읽었다 — {exc}"
    if name:
        if name not in scenarios:
            return None, f"모르는 시나리오 — {name}. 있는 것: {', '.join(scenarios) or '없음'}"
        return (name, scenarios[name]), None
    if len(scenarios) == 1:
        return next(iter(scenarios.items())), None
    return None, f"시나리오가 여럿이다 — scenario로 하나를 골라라: {', '.join(scenarios)}"


def report_day(scenario, today: date) -> date:
    return build_window(scenario.window, today=today).yesterday


def ledger_view(output_dir, name: str, day: date) -> dict | None:
    """장부가 있으면 그 내용(깨졌으면 이유), 없으면 None."""
    if not ledger.exists(output_dir, name, day):
        return None
    record, problem = ledger.read(output_dir, name, day)
    if record is None:
        return {"scenario": name, "day": day.isoformat(), "problem": problem}
    return json.loads(record.model_dump_json())


def today_slot(spec, now: datetime) -> datetime | None:
    """오늘 cron이 도는 첫 시각 — 오늘 안 도는 날(주말)이거나 interval이면 None."""
    if spec.kind != "cron":
        return None
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    slot = next_fire(spec.cron, midnight - timedelta(minutes=1))
    return slot if slot.date() == now.date() else None


def today_status(output_dir, name: str, scenario, *, now: datetime, last: dict | None, firing: bool,
                 paused: bool, started_at: datetime) -> dict:
    """오늘 기준일의 장부와, **슬롯이 지났는데 아무 일도 없었는가**(`missed`).

    08:00 근처에 파드가 다시 뜨면 그날 슬롯은 건너뛰어지고(밀린 슬롯은 몰아서 쏘지 않는다) 실패도 아니라 알림도 안 간다.
    메모리의 최근 결과는 재시작과 함께 사라지므로, "오늘 슬롯이 지났다 · 오늘 발사 기록이 없다 · 장부가 없다"가 함께면
    건너뛴 것이다 — 사람이 Swagger 한 곳(`GET /schedule`)에서 본다. 다시 보내는 것은 사람이 정한다(`report/run`).

    **장부도 재시작에 사라진다**(output은 컨테이너 안이라 남지 않는다 — 받아들인 위험, 11-02). 그러면 오늘 것이 이미
    나갔어도 `missed`가 서고, 중복 발송 막기도 같이 사라진다. 둘을 코드는 가를 수 없다 — 그래서 앱이 뜬 시각과 "오늘 슬롯
    뒤에 떴다"를 같이 보인다. 슬롯 바로 뒤에 떴으면 건너뛴 것, 한참 뒤에 떴으면 이전 파드가 이미 보냈을 수 있다 — 받은
    메일을 보고 사람이 가른다.
    """
    day = report_day(scenario, now.date())
    record = ledger_view(output_dir, name, day)
    spec = scenario.schedule
    slot = today_slot(spec, now) if scenario.enabled and spec.enabled else None
    fired_today = bool(last and slot and datetime.fromisoformat(last["scheduled"]) >= slot)
    missed = bool(slot and slot <= now and record is None and not fired_today and not firing and not paused)
    after_slot = bool(slot and started_at > slot)
    shown = {"day": day.isoformat(), "sent": record is not None, "ledger": record,
             "slot": slot.isoformat() if slot else None, "missed": missed,
             "app_started_at": started_at.isoformat(), "started_after_slot": after_slot}
    if missed and after_slot:
        shown["note"] = (f"앱이 오늘 슬롯({slot:%H:%M}) 뒤({started_at:%H:%M})에 떴다 — 그때 앱이 없어 건너뛰었거나, 이전 파드가 "
                         f"이미 보냈는데 재시작으로 장부가 사라졌다(output은 재시작에 남지 않는다). 받은 메일을 보고 정한다")
    return shown
