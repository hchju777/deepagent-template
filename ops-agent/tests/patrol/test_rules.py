"""판정 — **"지금 이 응답이 이상한가"만** 답한다. 상태를 갖지 않는다."""
from datetime import datetime

from src.config.schema_patrol import CheckConfig
from src.domain.envelope import ProbeResult
from src.domain.patrol import ProbeSet
from src.patrol.rules import MISSING, get_path, judge

T0 = datetime(2026, 9, 16, 9, 0, 0)
CLOCK = lambda: T0                                                  # noqa: E731

CHECK = CheckConfig.model_validate({
    "concern": "operation",
    "probes": {
        "badge":  {"action": "rest.query",
                   "params": {"entry": "summary_badge", "params": {}}},
        "status": {"action": "rest.query",
                   "params": {"entry": "prod_status", "params": {}}}},
    "rule": "items_all_zero",
    "params": {
        "items": {"probe": "badge", "path": "response"},
        "identity": ["group", "title"],
        "counts": ["alarm", "caution", "normal"],
        "only_when": {"probe": "status", "path": "response.status",
                      "equals": "In Production"}}})


def badge(title="Target Rate", group="Line", **counts) -> dict:
    body = {"group": group, "source": "G-MES", "title": title,
            "alarm": 0, "caution": 0, "normal": 0}
    body.update(counts)
    return body


def probed(items, *, status="In Production", badge_error=None,
           status_body=MISSING) -> ProbeSet:
    body = {"status": status} if status_body is MISSING else status_body
    results = {
        "badge": (ProbeResult.failed(badge_error, source="badge", clock=CLOCK)
                  if badge_error else
                  ProbeResult.succeeded({"request": {}, "status": 200, "response": items},
                                        source="badge", clock=CLOCK)),
        "status": ProbeResult.succeeded({"request": {}, "status": 200, "response": body},
                                        source="status", clock=CLOCK)}
    return ProbeSet(check="badge_all_zero", site="mx/gumi", results=results)


def verdict(items, **kw):
    return judge(probed(items, **kw), CHECK, clock=CLOCK)


# ── 경로 ───────────────────────────────────────────────────────────

def test_없는_경로와_None_값을_가른다():
    """`None`을 부재의 표시로 쓰면 `{"status": null}`이 "필드가 없다"와 같아진다.
    전자는 대상이 말한 것이고 후자는 우리가 잘못 물은 것이다."""
    assert get_path({"status": None}, "status") is None
    assert get_path({}, "status") is MISSING
    assert get_path({"a": {"b": [1, 2]}}, "a.b.1") == 2
    assert get_path({"a": 1}, "a.b") is MISSING


# ── 가드 ───────────────────────────────────────────────────────────

def test_생산_중이_아니면_판정을_안_한다():
    out = verdict([badge()], status="Idle")
    assert out.status == "skipped" and "Idle" in out.reason
    assert out.findings == []


def test_가드_필드가_없으면_판정을_못_한다():
    """"생산 중인지 모르는데 0/0/0"은 이상인지 **알 수 없다** — `ok`로 접으면
    진짜 이상을 놓치고, `finding`이면 거짓 알람이다."""
    out = verdict([badge()], status_body={"factory": "GUMI MX Main"})
    assert out.status == "unreachable" and "확인할 수 없다" in out.reason


def test_못_읽은_것과_안_한_것은_다른_값이다():
    """대상이 죽은 날이 "쉬는 날"로 보이면 안 된다."""
    assert verdict([badge()], status="Idle").status == "skipped"
    assert verdict([badge()], badge_error="ConnectTimeout").status == "unreachable"


def test_프로브가_실패하면_unreachable이다():
    out = verdict([badge()], badge_error="ConnectTimeout")
    assert "ConnectTimeout" in out.reason


# ── 본 판정 ────────────────────────────────────────────────────────

def test_전부_0이면_finding이다():
    out = verdict([badge(title="Defect")])
    assert out.status == "finding" and len(out.findings) == 1
    got = out.findings[0]
    assert got.target == "Line/Defect"
    assert got.observed == {"alarm": 0, "caution": 0, "normal": 0}
    assert got.observed_at == T0 and got.concern == "operation"


def test_하나라도_0이_아니면_이상이_아니다():
    out = verdict([badge(normal=10)])
    assert out.status == "ok" and out.examined == 1


def test_finding은_항목마다_하나다():
    """묶으면 조사가 찍을 데가 없고, 6단계가 대상별 연속을 못 센다."""
    out = verdict([badge(title="A"), badge(title="B", normal=3), badge(title="C")])
    assert [f.target for f in out.findings] == ["Line/A", "Line/C"]
    assert out.examined == 3


def test_group이_다르면_각각_판정한다():
    """식별자가 `(group, title)`이다. title만 쓰면 둘이 하나로 접힌다."""
    out = verdict([badge(group="Line", title="X"), badge(group="Part", title="X")])
    assert [f.target for f in out.findings] == ["Line/X", "Part/X"]


# ── 데이터 이상 ────────────────────────────────────────────────────

def test_개수_필드가_없으면_finding이다():
    """`caution`을 `cuation`으로 적은 응답을 **실제로 만났다.**

    조용히 건너뛰면 남은 `[0, 0]`만 보고 "전부 0"이라 판정한다 — 틀린 답이 아니라
    **없는 이상을 만들어 내는** 방향이다.
    """
    item = {"group": "Line", "title": "Downtime", "alarm": 0, "cuation": 5, "normal": 0}
    out = verdict([item])
    assert out.status == "finding"
    assert "필드 부재" in out.findings[0].reason and "caution" in out.findings[0].reason


def test_bool은_0으로_안_센다():
    """파이썬에서 `False == 0`이라 `{"caution": false}`가 "현장이 멈췄다"로 둔갑한다."""
    out = verdict([badge(caution=False)])
    assert out.status == "finding"
    assert "bool" in out.findings[0].reason


def test_NaN은_수치로_안_본다():
    out = verdict([badge(normal=float("nan"))])
    assert "유한한 수가 아니다" in out.findings[0].reason


def test_식별자가_중복이면_finding이다():
    """덮어쓰면 항목 하나가 조용히 사라진다.

    둘 다 0이 아닌 것으로 둬서 **중복만** 남긴다 — 0/0/0이면 finding이 둘이 되어
    어느 쪽이 중복 때문인지 안 보인다.
    """
    out = verdict([badge(title="X", normal=7), badge(title="X", normal=9)])
    assert [f.target for f in out.findings] == ["Line/X"]
    assert "중복" in out.findings[0].reason
    assert "#0과 #1" in out.findings[0].reason


def test_식별_필드가_없으면_finding이다():
    out = verdict([{"title": "X", "alarm": 0, "caution": 0, "normal": 0}])
    assert "식별 필드 부재" in out.findings[0].reason and "group" in out.findings[0].reason


# ── 응답 모양 ──────────────────────────────────────────────────────

def test_빈_리스트는_이상이_아니다():
    """"빠진 badge는 검사 대상이 아니다"가 이 점검의 전제다."""
    out = verdict([])
    assert out.status == "ok" and out.examined == 0


def test_리스트가_아니면_finding이다():
    """응답 모양이 바뀐 것이다. 조용히 통과시키면 그날부터 감시가 죽는다."""
    out = verdict({"total": 3})
    assert out.status == "finding" and "리스트가 아니다" in out.reason


def test_경로가_없으면_finding이다():
    from src.config.schema_patrol import CheckConfig as CC
    moved = CC.model_validate({**CHECK.model_dump(),
                               "params": {**CHECK.params.model_dump(),
                                          "items": {"probe": "badge", "path": "rows"}}})
    out = judge(probed([badge()]), moved, clock=CLOCK)
    assert out.status == "finding" and "경로가 응답에 없다" in out.reason


def test_판정한_대상_목록이_남는다():
    """"이상 없음"이 0개를 본 결과인지 12개를 본 결과인지 구별되어야 한다.

    개수가 아니라 **목록**인 이유: 6a의 게이트가 "이 대상이 정상으로 관측됐다"를
    알아야 하는데, finding 목록만으로는 "정상이었다"와 "응답에 아예 없었다"를 못 가른다.
    """
    out = verdict([badge(title=f"T{i}", normal=1) for i in range(12)])
    assert out.examined == 12
    assert out.targets[:2] == ["Line/T0", "Line/T1"]
    assert out.cleared() == out.targets          # 전부 정상


def test_이상인_대상은_cleared에서_빠진다():
    out = verdict([badge(title="A"), badge(title="B", normal=3)])
    assert out.targets == ["Line/A", "Line/B"]
    assert out.cleared() == ["Line/B"]


def test_중복된_항목은_판정한_것으로_안_센다():
    """12개가 왔는데 식별자가 하나면 실제로 판정한 것은 하나다."""
    out = verdict([badge(title="X", normal=1) for _ in range(12)])
    assert out.targets == ["Line/X"]
