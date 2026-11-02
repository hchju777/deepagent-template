"""발송 장부 — 그 기준일 리포트가 **실제로 나갔는가**. `output/sent/<시나리오>-<기준일>.json` 한 파일이 한 번이다.

## 왜 필요한가

스케줄·Swagger·CLI 세 곳에서 같은 리포트를 돌릴 수 있다. 장부가 없으면 "08:00에 나갔는데 사람이 9시에 Swagger에서 또
누름"이 메일 두 통이 되고, 받는 쪽은 어느 것이 맞는지 모른다. 그래서 **실제 발송 성공 뒤에만** 쓰고, 있으면 안 보낸다.

## 왜 날짜가 기준일인가

돌린 날이 아니라 리포트가 다루는 날(어제)이다 — HTML 파일 이름(`<시나리오>-<기준일>.html`)과 같아야 재발송이 그날
파일을 바로 찾는다. 월요일에 도는 리포트의 기준일은 금요일이다.

## 왜 파일인가

replica 하나, output 폴더 하나다. 리포트 파일 옆에 두면 사람이 열어 볼 수 있고, 보관 정리(4단계)가 같은 규칙으로 지운다.

**장부가 깨져 있어도 "있음"이다** — 읽을 수 없다고 없는 것으로 치면, 깨진 장부 하나가 메일 두 통이 된다. 덮으려면 `--force`.
"""
import json
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from src.domain.base import StrictModel

SENT_DIR = "sent"


class SentRecord(StrictModel):
    scenario: str
    day: date                      # 기준일
    sent_at: datetime
    subject: str
    recipients: int                # 수 — 주소는 config에 있다
    trigger: Literal["schedule", "manual"]
    job_id: str | None = None      # 작업 실행기가 띄웠을 때만(사람이 CLI를 직접 치면 없다)


def path_for(output_dir: Path, scenario: str, day: date) -> Path:
    return output_dir / SENT_DIR / f"{scenario}-{day.isoformat()}.json"


def exists(output_dir: Path, scenario: str, day: date) -> bool:
    return path_for(output_dir, scenario, day).is_file()


def read(output_dir: Path, scenario: str, day: date) -> tuple[SentRecord | None, str | None]:
    """(장부, 문제). 없으면 (None, None), 깨졌으면 (None, 이유) — 깨진 것도 `exists`는 참이다."""
    return _load(path_for(output_dir, scenario, day))


def _load(path: Path) -> tuple[SentRecord | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        return SentRecord.model_validate_json(path.read_text(encoding="utf-8")), None
    except Exception as exc:                                       # noqa: BLE001
        return None, f"장부를 읽을 수 없다 — {path.name}: {type(exc).__name__}: {exc}"


def write(output_dir: Path, record: SentRecord) -> str | None:
    """**던지지 않는다** — 못 쓰면 이유를 돌려준다. 임시 파일에 쓰고 바꿔 끼운다(반쯤 쓴 장부가 남지 않게)."""
    path = path_for(output_dir, record.scenario, record.day)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        temp.replace(path)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def all_records(output_dir: Path) -> list[dict]:
    """장부 전부 — 기준일 최근 순. 깨진 것도 이름과 이유로 보인다."""
    directory = output_dir / SENT_DIR
    if not directory.is_dir():
        return []
    rows = []
    for path in directory.glob("*.json"):
        record, problem = _load(path)
        if record is not None:
            rows.append(json.loads(record.model_dump_json()))
        else:
            rows.append({"file": path.name, "problem": problem})
    return sorted(rows, key=lambda row: (row.get("day") or "", row.get("scenario") or row.get("file", "")),
                  reverse=True)
