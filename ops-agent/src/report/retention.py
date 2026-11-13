"""출력 보관 — 시나리오의 `keep_days`: **파일명 날짜 기준 최근 N개 날짜**만 남긴다.

## 왜 달력 N일이 아니라 최근 N개인가

평일만 도는 리포트에서 달력 3일이면, 화요일 실행 때 기준일(월)에서 3일 범위(월·일·토)만 남아 목·금 파일이 지워진다 —
주말에는 파일이 없다. 날짜를 세지 않고 **있는 날짜 중 최근 N개**를 센다.

## 왜 이 시나리오의 HTML과 장부만인가

output 폴더에는 다른 것도 산다 — `graph/`(흐름 그래프), `traces/`(조사 트레이스), `cases.json`(케이스 저장소). 이름 규칙
(`<시나리오>-<YYYY-MM-DD>.html`, `sent/<시나리오>-<YYYY-MM-DD>.json`)에 **정확히** 맞는 파일만 본다 — 앞이 같은 다른
시나리오(`daily`와 `daily-alarm`)도 안 섞인다. HTML과 장부는 같은 날짜로 함께 남고 함께 지운다.

`<시나리오>-<YYYY-MM-DD>.preview.html`(장부가 있는 날의 안 보내는 실행이 쓴 것, `publish.py`)도 같은 시나리오의 HTML이라
같은 날짜로 함께 지운다 — Swagger의 `report/run`은 dry-run이 기본이라, 빼 두면 누를 때마다 쌓인다.

## 방금 쓴 날은 안 지운다

`report run --today`로 옛 날을 다시 만들면 그 날이 최근 N개 밖이라 쓰자마자 지워진다. 방금 쓴 날은 남긴다(`keep_also`).
"""
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from src.report.ledger import SENT_DIR


@dataclass(frozen=True)
class Item:
    path: Path
    day: date
    kind: str                # "html" | "preview" | "ledger"


@dataclass(frozen=True)
class Plan:
    scenario: str
    keep_days: int | None
    keep: tuple[date, ...]
    delete: tuple[Item, ...]


def _dated(directory: Path, scenario: str, suffix: str, kind: str) -> list[Item]:
    pattern = re.compile(rf"^{re.escape(scenario)}-(\d{{4}}-\d{{2}}-\d{{2}}){re.escape(suffix)}$")
    found = []
    if not directory.is_dir():
        return found
    for path in directory.iterdir():
        match = pattern.match(path.name)
        if not match or not path.is_file():
            continue
        try:
            found.append(Item(path=path, day=date.fromisoformat(match.group(1)), kind=kind))
        except ValueError:
            continue                          # 2026-13-40처럼 날짜가 아닌 것 — 우리가 쓴 파일이 아니다
    return found


def plan(output_dir: Path, scenario: str, keep_days: int | None, *, keep_also: date | None = None) -> Plan:
    """무엇을 남기고 무엇을 지울지 — **지우지 않는다**. `keep_days`가 없으면 아무것도 안 지운다."""
    items = (_dated(output_dir, scenario, ".html", "html")
             + _dated(output_dir, scenario, ".preview.html", "preview")
             + _dated(output_dir / SENT_DIR, scenario, ".json", "ledger"))
    days = sorted({item.day for item in items}, reverse=True)
    if keep_days is None:
        return Plan(scenario=scenario, keep_days=None, keep=tuple(days), delete=())
    keep = set(days[:keep_days]) | ({keep_also} if keep_also else set())
    delete = sorted((item for item in items if item.day not in keep), key=lambda item: (item.day, item.kind))
    return Plan(scenario=scenario, keep_days=keep_days, keep=tuple(sorted(keep, reverse=True)), delete=tuple(delete))


def apply(plan: Plan) -> tuple[list[Item], list[str]]:
    """(지운 것, 못 지운 이유들). **던지지 않는다** — 하나 못 지워도 나머지는 지운다."""
    deleted, problems = [], []
    for item in plan.delete:
        try:
            item.path.unlink()
            deleted.append(item)
        except FileNotFoundError:
            deleted.append(item)              # 이미 없다 — 바라던 상태다
        except OSError as exc:
            problems.append(f"{item.path.name}: {type(exc).__name__}: {exc}")
    return deleted, problems


def describe(plan: Plan, output_dir: Path) -> dict:
    return {"scenario": plan.scenario, "keep_days": plan.keep_days,
            "keep": [day.isoformat() for day in plan.keep],
            "delete": [item.path.relative_to(output_dir).as_posix() for item in plan.delete]}
