"""`mail send --to` — 입력한 주소에**만** 보낸다.

config 수신자에 **더하지** 않는다. 더하면 "시험 삼아 나한테만"이 수신자 전원에게 간다 — 그 사고는 되돌릴 수 없다.
"""
import json

import pytest

from src.__main__ import main

ENV = {"MAIL_AGENT_API_KEY": "k"}


@pytest.fixture
def mail_config(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    (root / "app.json").write_text(json.dumps({
        "timezone": "Asia/Seoul",
        "mail": {"enabled": True, "recipients": ["all-1@example.com", "all-2@example.com"],
                 "api_base": "https://agent.example.net/api/v1/run", "agent_id": "a1",
                 "api_key": "${MAIL_AGENT_API_KEY}"}}), encoding="utf-8")
    (root / "registry.json").write_text('{"sites": []}', encoding="utf-8")
    return root


def run(config_root, *argv, monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    return main(["--config-root", str(config_root), "--env-file", "/dev/null", "mail", "send", *argv])


def test_to를_주면_그_주소들만(mail_config, monkeypatch, capsys):
    code = run(mail_config, "--dry-run", "--to", "me@example.com", "--to=you@example.com",
               monkeypatch=monkeypatch)
    assert code == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["recipients"] == ["me@example.com", "you@example.com"]
    assert preview["body"]["input_value"].startswith("to_email : me@example.com, you@example.com\n")


def test_to가_없으면_config_수신자(mail_config, monkeypatch, capsys):
    assert run(mail_config, "--dry-run", monkeypatch=monkeypatch) == 0
    assert json.loads(capsys.readouterr().out)["recipients"] == ["all-1@example.com", "all-2@example.com"]


@pytest.mark.parametrize("bad", ["주소아님", "a@example.com\nsubject : x", "a@example.com, b@example.com"])
def test_to도_config와_같은_규칙으로_거른다(mail_config, monkeypatch, capsys, bad):
    assert run(mail_config, "--dry-run", f"--to={bad}", monkeypatch=monkeypatch) == 1
    assert "--to" in capsys.readouterr().err


def test_실제_발송도_to로_바뀐_설정으로_간다(mail_config, monkeypatch, capsys):
    """미리보기만 바뀌고 발송은 config로 가는 갈림이 없어야 한다 — 조립 자리가 하나인지 본다."""
    from src.domain.mail import MailResult
    from src.infrastructure import mail_factory

    used = []

    class Sender:
        def __init__(self, cfg):
            self.cfg = cfg

        def full_subject(self, key):
            return key

        async def send(self, subject, body):
            return MailResult(status="sent", sent_at="2026-10-30T09:00:00+09:00",
                              recipients=list(self.cfg.recipients), subject=subject)

    def build(cfg, *, clock, warn=None):
        used.append(cfg)
        return Sender(cfg)
    monkeypatch.setattr(mail_factory, "build_mail", build)
    assert run(mail_config, "--to", "me@example.com", monkeypatch=monkeypatch) == 0
    assert [cfg.recipients for cfg in used] == [["me@example.com"]]
    assert json.loads(capsys.readouterr().out)["recipients"] == ["me@example.com"]
