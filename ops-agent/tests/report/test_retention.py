"""출력 보관 — 최근 N개 날짜, 같은 시나리오의 HTML과 장부만, 나머지는 손대지 않는다."""
from datetime import date

from src.report import retention


def touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def fill(out):
    # 평일만 도는 리포트의 모양 — 주말 날짜가 없다.
    for day in ("2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02"):
        touch(out / f"daily-alarm-{day}.html")
    for day in ("2026-10-29", "2026-10-30", "2026-11-02"):
        touch(out / "sent" / f"daily-alarm-{day}.json")
    for day in ("2026-10-30", "2026-11-02"):
        touch(out / f"daily-alarm-{day}.preview.html")          # 장부가 있는 날의 안 보내는 실행
    # 건드리면 안 되는 것들
    touch(out / "cases.json")
    touch(out / "report.html")
    touch(out / "graph" / "mx" / "flow.html")
    touch(out / "traces" / "case-1" / "summary.md")
    touch(out / "daily-2026-10-01.html")                      # 앞이 같은 다른 시나리오
    touch(out / "daily-alarm-2026-13-40.html")                # 날짜가 아니다
    touch(out / "daily-alarm-2026-10-01.html.bak")


def test_최근_N개_날짜만_남긴다_달력_N일이_아니다(tmp_path):
    """화요일(11-03) 실행의 기준일은 월(11-02). 달력 3일이면 월·일·토만 남아 목·금이 지워진다 — 최근 3개는 월·금·목이다."""
    fill(tmp_path)
    got = retention.plan(tmp_path, "daily-alarm", 3)
    assert got.keep == (date(2026, 11, 2), date(2026, 10, 30), date(2026, 10, 29))
    assert sorted(item.path.name for item in got.delete) == [
        "daily-alarm-2026-10-26.html", "daily-alarm-2026-10-27.html", "daily-alarm-2026-10-28.html"]


def test_HTML과_장부는_같은_날짜로_함께_지운다(tmp_path):
    fill(tmp_path)
    got = retention.plan(tmp_path, "daily-alarm", 1)
    deleted = {(item.kind, item.day.isoformat()) for item in got.delete}
    assert ("ledger", "2026-10-30") in deleted and ("html", "2026-10-30") in deleted
    assert all(item.day != date(2026, 11, 2) for item in got.delete)


def test_이름_규칙에_정확히_맞는_것만_보고_나머지는_손대지_않는다(tmp_path):
    fill(tmp_path)
    retention.apply(retention.plan(tmp_path, "daily-alarm", 1))
    for kept in ("cases.json", "report.html", "graph/mx/flow.html", "traces/case-1/summary.md",
                 "daily-2026-10-01.html", "daily-alarm-2026-13-40.html", "daily-alarm-2026-10-01.html.bak",
                 "daily-alarm-2026-11-02.html", "sent/daily-alarm-2026-11-02.json",
                 "daily-alarm-2026-11-02.preview.html"):
        assert (tmp_path / kept).exists(), kept
    assert not (tmp_path / "daily-alarm-2026-10-30.html").exists()
    assert not (tmp_path / "sent" / "daily-alarm-2026-10-30.json").exists()
    assert not (tmp_path / "daily-alarm-2026-10-30.preview.html").exists()      # 미리보기도 같은 날짜로 함께


def test_keep_days가_없으면_아무것도_안_지운다(tmp_path):
    fill(tmp_path)
    got = retention.plan(tmp_path, "daily-alarm", None)
    assert got.delete == () and len(got.keep) == 6


def test_방금_쓴_날은_최근_N개_밖이어도_남긴다(tmp_path):
    """`report run --today`로 옛 날을 다시 만들면 쓰자마자 지워지면 안 된다."""
    fill(tmp_path)
    got = retention.plan(tmp_path, "daily-alarm", 2, keep_also=date(2026, 10, 26))
    assert date(2026, 10, 26) in got.keep
    assert "daily-alarm-2026-10-26.html" not in [item.path.name for item in got.delete]


def test_지우기는_던지지_않고_못_지운_것을_말한다(tmp_path):
    fill(tmp_path)
    got = retention.plan(tmp_path, "daily-alarm", 5)
    victim = got.delete[0].path
    victim.unlink()
    victim.mkdir()                                            # 파일 자리에 폴더 — unlink가 실패한다
    deleted, problems = retention.apply(got)
    assert len(problems) == 1 and victim.name in problems[0]
    assert victim.is_dir()


def test_보기는_output_기준_상대_경로(tmp_path):
    fill(tmp_path)
    shown = retention.describe(retention.plan(tmp_path, "daily-alarm", 5), tmp_path)
    assert shown == {"scenario": "daily-alarm", "keep_days": 5,
                     "keep": ["2026-11-02", "2026-10-30", "2026-10-29", "2026-10-28", "2026-10-27"],
                     "delete": ["daily-alarm-2026-10-26.html"]}
