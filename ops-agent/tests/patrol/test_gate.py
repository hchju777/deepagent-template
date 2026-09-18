"""게이트 — finding이 케이스가 되거나, 있는 케이스에 붙는다."""
from datetime import datetime, timedelta

import pytest

from src.domain.cases import CaseRecord, CaseRepositoryPort, fingerprint
from src.domain.patrol import CheckOutcome, Finding
from src.patrol.gate import admit, process

T0 = datetime(2026, 9, 18, 9, 0, 0)


class Memory(CaseRepositoryPort):
    def __init__(self, records=()):
        self.records = list(records)
        # 실제 저장소는 다음 번호를 **파일에 저장한다**. 대역이 1부터 세면 이미
        # 있는 케이스와 id가 겹쳐서, 테스트가 실제와 다른 것을 보게 된다.
        self._next = len(self.records) + 1

    def latest(self, key):
        found = [c for c in self.records if c.fingerprint == key]
        return max(found, key=lambda c: c.opened_at) if found else None

    def add(self, record):
        self.records.append(record)

    def update(self, record):
        self.records = [record if c.id == record.id else c for c in self.records]

    def all(self):
        return list(self.records)

    def next_id(self):
        made, self._next = f"c-{self._next}", self._next + 1
        return made


class Exploding(Memory):
    """**실제로 던진다.** 대본 저장소는 방어를 지워도 초록이다."""

    def latest(self, key):
        raise OSError("저장소 파일이 깨졌다")


def finding(target="Operator/Check", site="mx/gumi", observed=None, **kw) -> Finding:
    body = {"check": "badge_all_zero", "site": site, "concern": "operation",
            "target": target, "reason": "전부 0 — alarm·caution·normal이 모두 0이다",
            "observed": {"alarm": 0, "caution": 0, "normal": 0} if observed is None
                        else observed,
            "observed_at": T0}
    body.update(kw)
    return Finding.model_validate(body)


def record(target="Operator/Check", site="mx/gumi", **kw) -> CaseRecord:
    body = {"id": "c-9", "site": site, "check": "badge_all_zero", "target": target,
            "concern": "operation", "symptom": "s", "opened_at": T0, "last_seen_at": T0}
    body.update(kw)
    return CaseRecord.model_validate(body)


def clock_at(moment):
    return lambda: moment


# ── 개설과 첨부 ────────────────────────────────────────────────────

def test_처음이면_연다():
    repo = Memory()
    got = admit(finding(), repo=repo, clock=clock_at(T0))
    assert (got.action, got.case_id) == ("opened", "c-1")
    assert repo.records[0].symptom.startswith("Operator/Check — 전부 0")


def test_열린_케이스가_있으면_첨부한다():
    """무시하면 "언제부터 이랬나"가 아무 데도 안 남는다."""
    repo = Memory([record(id="c-1")])
    later = T0 + timedelta(hours=9, minutes=30)
    got = admit(finding(), repo=repo, clock=clock_at(later))
    assert (got.action, got.case_id) == ("attached", "c-1")
    assert repo.records[0].observations == 2
    assert repo.records[0].last_seen_at == later
    assert "9시간 30분째" in got.reason


def test_첨부는_처음_본_값을_안_덮는다():
    """케이스가 든 것은 **열릴 때의 근거**다. 나중 값으로 덮으면 왜 열렸는지가 사라진다."""
    repo = Memory([record(id="c-1", observed={"alarm": 0, "caution": 0, "normal": 0})])
    admit(finding(observed={"alarm": 5, "caution": 0, "normal": 0}),
          repo=repo, clock=clock_at(T0))
    assert repo.records[0].observed == {"alarm": 0, "caution": 0, "normal": 0}


def test_대상이_다르면_다른_케이스다():
    repo = Memory([record(id="c-1", target="Operator/Check")])
    got = admit(finding(target="Material/ATR Status"), repo=repo, clock=clock_at(T0))
    assert got.action == "opened"


def test_사이트가_다르면_다른_케이스다():
    repo = Memory([record(id="c-1", site="mx/gumi")])
    got = admit(finding(site="mx/sevt"), repo=repo, clock=clock_at(T0))
    assert got.action == "opened"


# ── 닫힌 뒤 ────────────────────────────────────────────────────────

def test_닫힌_뒤_정상을_못_봤으면_다시_안_연다():
    """안 그러면 안 고쳐진 문제 하나로 **3시간마다 케이스가 쌓인다.**"""
    repo = Memory([record(id="c-1", status="closed", closed_at=T0)])
    got = admit(finding(), repo=repo, clock=clock_at(T0 + timedelta(hours=3)))
    assert got.action == "suppressed" and got.case_id == "c-1"
    assert len(repo.records) == 1


def test_닫힌_뒤_정상을_봤으면_다시_연다():
    """그게 "새 사건"의 정의다."""
    repo = Memory([record(id="c-1", status="closed", closed_at=T0,
                          cleared_at=T0 + timedelta(hours=1))])
    got = admit(finding(), repo=repo, clock=clock_at(T0 + timedelta(hours=3)))
    assert got.action == "opened" and got.case_id == "c-2"


def test_정상_관측은_닫힌_케이스에만_적힌다():
    """순찰이 정상을 봤다고 조사가 끝난 것은 아니다 — 케이스를 닫는 것은 조사(12a)다."""
    repo = Memory([record(id="c-1", status="open")])
    process(CheckOutcome(check="badge_all_zero", site="mx/gumi", concern="operation",
                         status="ok", reason="이상 없음",
                         targets=["Operator/Check"]),
            repo=repo, clock=clock_at(T0))
    assert repo.records[0].status == "open" and repo.records[0].cleared_at is None


def test_응답에_없던_대상은_정상으로_안_친다():
    """"빠진 것은 검사 대상이 아니다"가 이 점검의 전제다 — 안 본 것을 정상으로
    치면 닫힌 케이스가 잘못 되살아난다."""
    repo = Memory([record(id="c-1", status="closed", closed_at=T0)])
    process(CheckOutcome(check="badge_all_zero", site="mx/gumi", concern="operation",
                         status="ok", reason="이상 없음", targets=["Line/다른것"]),
            repo=repo, clock=clock_at(T0))
    assert repo.records[0].cleared_at is None


# ── 거부와 무raise ─────────────────────────────────────────────────

def test_관측값이_비면_거부한다():
    """근거 없는 케이스는 조사가 시작 지점을 못 갖는다."""
    repo = Memory()
    got = admit(finding(observed={}), repo=repo, clock=clock_at(T0))
    assert got.action == "rejected" and not repo.records


def test_저장소가_던져도_순찰이_안_죽는다():
    got = admit(finding(), repo=Exploding(), clock=clock_at(T0))
    assert got.action == "rejected" and "저장소 파일이 깨졌다" in got.reason


def test_게이트는_대상을_다시_읽지_않는다():
    """게이트 통과 시점에 다시 조회하면 그 사이 값이 바뀔 수 있고, 그러면 보고서가
    "0/0/0이라 열었다"는데 케이스엔 다른 값이 실린다."""
    repo = Memory()
    admit(finding(observed={"alarm": 0, "caution": 0, "normal": 0}),
          repo=repo, clock=clock_at(T0))
    # finding이 판정 시점에 본 것이 그대로 실렸다 — 어댑터를 인자로 받지도 않는다.
    assert repo.records[0].observed == {"alarm": 0, "caution": 0, "normal": 0}
    with pytest.raises(TypeError):
        admit(finding(), repo=repo, clock=clock_at(T0), adapters=object())


# ── process ────────────────────────────────────────────────────────

def outcome(status="finding", findings=(), targets=()) -> CheckOutcome:
    return CheckOutcome(check="badge_all_zero", site="mx/gumi", concern="operation",
                        status=status, reason="r", findings=list(findings),
                        targets=list(targets))


def test_판정_못_했으면_아무것도_안_한다():
    """`skipped`·`unreachable`은 정상도 이상도 관측되지 않았다 — 둘 중 어느
    쪽으로도 상태를 움직이면 안 된다.

    **움직일 수 있는 재료를 주고 본다.** 처음엔 빈 outcome으로 검사했는데, 그건
    가드를 지워도 통과한다(움직일 것이 애초에 없으니까). `targets`와 `findings`를
    실어야 "가드가 막고 있다"가 보인다.
    """
    for status in ("skipped", "unreachable"):
        repo = Memory([record(id="c-1", status="closed", closed_at=T0)])
        moved = outcome(status=status, findings=[finding()],
                        targets=["Operator/Check"])
        assert process(moved, repo=repo, clock=clock_at(T0)) == []
        assert repo.records[0].cleared_at is None      # 정상으로 안 친다
        assert len(repo.records) == 1                  # 케이스를 안 연다


def test_한_점검의_여러_이상이_각각_케이스가_된다():
    repo = Memory()
    results = process(outcome(findings=[finding(target="Operator/Check"),
                                        finding(target="Material/ATR Status")],
                              targets=["Operator/Check", "Material/ATR Status"]),
                      repo=repo, clock=clock_at(T0))
    assert [r.action for r in results] == ["opened", "opened"]
    assert [r.case_id for r in results] == ["c-1", "c-2"]


def test_CLI가_실제로_돈다(tmp_path, capsys, monkeypatch):
    """`patrol open` 명령 자체를 부른다 — 배선은 배선을 불러야 보인다."""
    import json
    from pathlib import Path

    from src.__main__ import main

    root = Path(__file__).resolve().parent.parent.parent
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps({"rest": {
        "summary_badge": [
            {"group": "Operator", "title": "Check", "alarm": 0, "caution": 0, "normal": 0},
            {"group": "Line", "title": "Target Rate", "alarm": 0, "caution": 0, "normal": 9}],
        "prod_status": {"status": "In Production"}}}), encoding="utf-8")
    app = json.loads((root / "config" / "app.json").read_text(encoding="utf-8"))
    app["case_store"] = str(tmp_path / "cases.json")
    config_root = tmp_path / "config"
    config_root.mkdir()
    for child in (root / "config").iterdir():
        if child.name != "app.json":
            (config_root / child.name).symlink_to(child)
    (config_root / "app.json").write_text(json.dumps(app, ensure_ascii=False),
                                          encoding="utf-8")
    for key in ("REDIS_PASSWORD", "MONGO_PASSWORD", "LLM_BASE_URL", "LLM_CLIENT_KEY",
                "LLM_PASS_KEY", "MAIL_AGENT_API_KEY", "MAIL_AGENT_ID"):
        monkeypatch.setenv(key, "https://x/v1" if key.endswith("URL") else "x")

    def run(*extra):
        monkeypatch.setattr("sys.argv", [
            "src", "--config-root", str(config_root), "--env-file", str(tmp_path / "none"),
            "patrol", "open", "--gbm", "mx", "--fct", "gumi",
            "--stub-seeds", str(seeds), *extra])
        return main(), capsys.readouterr().out

    code, out = run("--dry-run")
    assert code == 0 and "Operator/Check" in out
    assert not (tmp_path / "cases.json").exists()     # dry-run은 저장하지 않는다

    code, out = run()
    assert code == 0 and "c-1" in out
    code, out = run()
    assert "c-1" in out and "2회째" in out             # 두 번째는 첨부다
