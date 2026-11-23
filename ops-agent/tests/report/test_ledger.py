"""발송 장부 — 실제로 나간 것만, 기준일로, 깨져도 "있음"."""
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from src.report import ledger

DAY = date(2026, 10, 30)
AT = datetime(2026, 11, 2, 8, 3, tzinfo=ZoneInfo("Asia/Seoul"))


def record(**fields) -> ledger.SentRecord:
    base = dict(scenario="daily-alarm", day=DAY, sent_at=AT, subject="[운영리포트] 일일 알람 리포트 2026-10-30(금)",
                recipients=2, trigger="schedule", job_id="abc123")
    return ledger.SentRecord(**{**base, **fields})


def test_자리는_sent_아래_시나리오_기준일(tmp_path):
    assert ledger.path_for(tmp_path, "daily-alarm", DAY) == tmp_path / "sent" / "daily-alarm-2026-10-30.json"


def test_쓰고_읽으면_같다(tmp_path):
    assert ledger.write(tmp_path, record()) is None
    got, problem = ledger.read(tmp_path, "daily-alarm", DAY)
    assert problem is None and got == record()
    assert ledger.exists(tmp_path, "daily-alarm", DAY)
    assert not list((tmp_path / "sent").glob("*.tmp")), "임시 파일이 남았다"


def test_없으면_없음(tmp_path):
    assert ledger.read(tmp_path, "daily-alarm", DAY) == (None, None)
    assert not ledger.exists(tmp_path, "daily-alarm", DAY)


def test_깨진_장부도_있음이고_이유를_말한다(tmp_path):
    """읽을 수 없다고 없는 것으로 치면 깨진 장부 하나가 메일 두 통이 된다."""
    path = ledger.path_for(tmp_path, "daily-alarm", DAY)
    path.parent.mkdir(parents=True)
    path.write_text("{깨짐", encoding="utf-8")
    assert ledger.exists(tmp_path, "daily-alarm", DAY)
    got, problem = ledger.read(tmp_path, "daily-alarm", DAY)
    assert got is None and "daily-alarm-2026-10-30.json" in problem


def test_못_쓰면_던지지_않고_이유를_돌려준다(tmp_path):
    (tmp_path / "sent").write_text("폴더 자리에 파일", encoding="utf-8")
    problem = ledger.write(tmp_path, record())
    assert problem and "Error" in problem


def test_모아_보기는_기준일_최근_순이고_깨진_것도_보인다(tmp_path):
    ledger.write(tmp_path, record(day=date(2026, 10, 29)))
    ledger.write(tmp_path, record(day=date(2026, 10, 30), trigger="manual", job_id=None))
    (tmp_path / "sent" / "daily-alarm-2026-10-28.json").write_text("{깨짐", encoding="utf-8")
    rows = ledger.all_records(tmp_path)
    assert [row.get("day") for row in rows[:2]] == ["2026-10-30", "2026-10-29"]
    assert rows[0]["trigger"] == "manual" and rows[0]["job_id"] is None
    assert rows[2]["file"] == "daily-alarm-2026-10-28.json" and "problem" in rows[2]
    json.dumps(rows)                                      # 그대로 응답에 실을 수 있다
