"""`report run`과 발송 장부·종료 코드 — 스케줄·Swagger·CLI가 같은 규칙을 지키는 자리는 여기 하나다.

메일만 가짜다(`build_mail`을 바꾼다). 집계·HTML·파일은 진짜 경로를 탄다 — 대상 데이터는 시드로.
"""
import json
from datetime import datetime

import pytest

from src.__main__ import main
from src.report import exit_codes, ledger

TODAY = "2026-09-07"            # 월요일 → 기준일은 금요일
DAY = "2026-09-04"


@pytest.fixture
def config(tmp_path):
    root = tmp_path / "config"
    (root / "gbm").mkdir(parents=True)
    for fct in ("gumi", "sevt"):
        (root / "fct" / fct).mkdir(parents=True)
    (root / "app.json").write_text(json.dumps({
        "timezone": "Asia/Seoul", "llm": {"adapter": "echo", "model": "dev"},
        "mail": {"enabled": True, "recipients": ["ops@example.com", "lead@example.com"],
                 "api_base": "https://agent.example.net/api", "agent_id": "a1", "api_key": "k"}}),
        encoding="utf-8")
    (root / "registry.json").write_text(json.dumps({"sites": [
        {"gbm": "mx", "fct": "gumi"}, {"gbm": "mx", "fct": "sevt"}]}), encoding="utf-8")
    (root / "fct" / "gumi" / "mx.json").write_text(json.dumps({"infra": {
        "redis": {"url": "redis://h:6379"}, "mongodb": {"url": "mongodb://h:27017", "database": "twin"}}}),
        encoding="utf-8")
    # sevt에는 mongodb가 없다 — 범위에 넣으면 그 법인을 못 읽는다(경고 시험용).
    (root / "fct" / "sevt" / "mx.json").write_text(json.dumps({"infra": {"redis": {"url": "redis://h:6379"}}}),
                                                   encoding="utf-8")
    (root / "scenarios").mkdir()
    scenario(root, sites=["mx/gumi"])
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps({"mx/gumi": {"mongo": {"alarm": [
        {"occ_date": f"2026-09-0{d} 09:00:00", "gbm": "mx", "plant": "gumi", "part_code": "PN100",
         "line_code": "P222", "line_name": "조립2라인", "scen_id": "S01", "scen_name": "재고 불일치", "status": 0}
        for d in (1, 2, 3, 4)]}}}), encoding="utf-8")
    return root, str(seeds), tmp_path / "out"


def scenario(root, *, sites):
    (root / "scenarios" / "daily-alarm.json").write_text(json.dumps({
        "kind": "alarm_daily", "title": "일일 알람 리포트",
        "source": {"collection": "alarm", "date_field": "occ_date"},
        "scope": {"gbms": ["mx"], "sites": sites}}), encoding="utf-8")


class Mail:
    """가짜 발송 — 보낸 횟수를 센다. `status`로 실패를 흉내 낸다."""

    def __init__(self, status="sent"):
        self.status, self.sent = status, []

    def install(self, monkeypatch):
        from src.domain.mail import MailResult
        from src.infrastructure import mail_factory
        outer = self

        class Sender:
            def __init__(self, cfg):
                self.cfg = cfg

            def full_subject(self, key):
                return f"{self.cfg.subject_prefix} {key}"

            def preview(self, subject, body):
                return {"recipients": list(self.cfg.recipients)}

            async def send(self, subject, body):
                outer.sent.append(subject)
                if outer.status == "error":
                    return MailResult(status="error", sent_at=datetime(2026, 9, 7, 8, 1), recipients=[],
                                      subject=subject, error="게이트웨이 500")
                if not self.cfg.enabled:
                    return MailResult(status="skipped", sent_at=datetime(2026, 9, 7, 8, 1), recipients=[],
                                      subject=subject, reason="mail.enabled=false")
                return MailResult(status="sent", sent_at=datetime(2026, 9, 7, 8, 1),
                                  recipients=list(self.cfg.recipients), subject=subject)

        monkeypatch.setattr(mail_factory, "build_mail", lambda cfg, *, clock, warn=None: Sender(cfg))
        return self


def run(config, *extra):
    root, seeds, out = config
    return main(["--config-root", str(root), "--env-file", "/dev/null", "report", "run", "--today", TODAY,
                 "--stub-seeds", seeds, "--out-dir", str(out), *extra])


def read_ledger(out):
    from datetime import date
    record, problem = ledger.read(out, "daily-alarm", date.fromisoformat(DAY))
    assert problem is None
    return record


def test_보내면_기준일_장부를_쓴다(config, monkeypatch):
    mail = Mail().install(monkeypatch)
    assert run(config) == exit_codes.OK
    record = read_ledger(config[2])
    assert (record.scenario, record.day.isoformat(), record.recipients) == ("daily-alarm", DAY, 2)
    assert record.subject == mail.sent[0] == "[운영리포트] 일일 알람 리포트 2026-09-04(금)"
    assert (record.trigger, record.job_id) == ("manual", None)          # 사람이 CLI를 직접 쳤다


def test_계기와_작업_id를_장부에_적는다(config, monkeypatch):
    Mail().install(monkeypatch)
    assert run(config, "--trigger", "schedule", "--job-id", "job42") == exit_codes.OK
    record = read_ledger(config[2])
    assert (record.trigger, record.job_id) == ("schedule", "job42")


def test_장부가_있으면_집계_전에_멈추고_이미_보냄으로_끝난다(config, monkeypatch, capsys):
    mail = Mail().install(monkeypatch)
    assert run(config) == exit_codes.OK
    html = config[2] / f"daily-alarm-{DAY}.html"
    before = html.read_text(encoding="utf-8")

    def no_collect(*args, **kwargs):
        raise AssertionError("이미 보냈는데 집계를 다시 돌렸다")
    monkeypatch.setattr("src.report.collect.collect", no_collect)
    capsys.readouterr()
    assert run(config) == exit_codes.ALREADY_SENT
    out = capsys.readouterr().out
    assert "이미 보냈다" in out and "--force" in out
    assert len(mail.sent) == 1
    assert html.read_text(encoding="utf-8") == before                    # 보낸 HTML을 덮어쓰지 않았다


def test_force면_다시_보내고_장부를_덮는다(config, monkeypatch):
    mail = Mail().install(monkeypatch)
    run(config, "--job-id", "first")
    assert run(config, "--force", "--job-id", "second") == exit_codes.OK
    assert len(mail.sent) == 2
    assert read_ledger(config[2]).job_id == "second"


@pytest.mark.parametrize("flag", ["--dry-run", "--no-mail"])
def test_보내지_않는_실행은_장부를_안_본다(config, monkeypatch, flag):
    mail = Mail().install(monkeypatch)
    run(config, "--job-id", "first")
    assert run(config, flag) == exit_codes.OK
    assert len(mail.sent) == 1
    assert read_ledger(config[2]).job_id == "first"


def test_메일이_실패하면_장부가_없고_실패다(config, monkeypatch):
    Mail(status="error").install(monkeypatch)
    assert run(config) == exit_codes.FAILED
    assert read_ledger(config[2]) is None


def test_메일이_꺼져_있으면_장부가_없다(config, monkeypatch):
    Mail().install(monkeypatch)
    app = json.loads((config[0] / "app.json").read_text(encoding="utf-8"))
    app["mail"]["enabled"] = False
    (config[0] / "app.json").write_text(json.dumps(app), encoding="utf-8")
    assert run(config) == exit_codes.OK
    assert read_ledger(config[2]) is None


def test_일부_법인을_못_읽고_보냈으면_경고이고_장부는_쓴다(config, monkeypatch, capsys):
    """사내 11-01: 법인 둘의 Mongo가 안 닿아 리포트는 나갔는데 종료 코드 1 → 작업 failed. 나간 것은 실패가 아니다."""
    Mail().install(monkeypatch)
    scenario(config[0], sites=["mx/gumi", "mx/sevt"])
    assert run(config) == exit_codes.WARNING
    assert read_ledger(config[2]) is not None
    assert "mx/sevt" in capsys.readouterr().err


def test_보냈는데_장부를_못_쓰면_실패다(config, monkeypatch, capsys):
    """다음에 누가 다시 돌리면 두 통이 된다 — 사람이 봐야 한다."""
    mail = Mail().install(monkeypatch)
    config[2].mkdir(parents=True)
    (config[2] / "sent").write_text("폴더 자리에 파일", encoding="utf-8")
    assert run(config) == exit_codes.FAILED
    assert len(mail.sent) == 1
    assert "메일은 나갔는데 장부를 못 썼다" in capsys.readouterr().err


def test_깨진_장부도_있음으로_보고_안_보낸다(config, monkeypatch, capsys):
    mail = Mail().install(monkeypatch)
    path = config[2] / "sent" / f"daily-alarm-{DAY}.json"
    path.parent.mkdir(parents=True)
    path.write_text("{깨짐", encoding="utf-8")
    assert run(config) == exit_codes.ALREADY_SENT
    assert mail.sent == []
    assert "장부를 읽을 수 없다" in capsys.readouterr().err


def test_aggregate도_못_읽은_법인은_경고다(config, capsys):
    root, seeds, _ = config
    scenario(root, sites=["mx/gumi", "mx/sevt"])
    code = main(["--config-root", str(root), "--env-file", "/dev/null", "report", "aggregate", "--today", TODAY,
                 "--stub-seeds", seeds])
    assert code == exit_codes.WARNING, capsys.readouterr().err
    scenario(root, sites=["mx/gumi"])
    assert main(["--config-root", str(root), "--env-file", "/dev/null", "report", "aggregate", "--today", TODAY,
                 "--stub-seeds", seeds]) == exit_codes.OK


def test_종료_코드_2는_비워_둔다():
    """argparse가 인자 오류에 2를 낸다 — 잘못 친 명령이 경고·이미 보냄으로 보이면 안 된다."""
    assert 2 not in {exit_codes.OK, exit_codes.FAILED, exit_codes.WARNING, exit_codes.ALREADY_SENT}
    with pytest.raises(SystemExit) as exit_:
        main(["report", "run", "--trigger", "모름"])
    assert exit_.value.code == 2
