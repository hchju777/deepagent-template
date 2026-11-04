"""cron 계산 — **틀리면 증상이 "리포트가 안 온다"뿐이다.**

라이브러리 대신 직접 썼으므로(사내 PyPI에 croniter가 없을 수 있다) 그만큼 촘촘해야
한다. 여기서 새는 것은 며칠 뒤에야 사람이 알아챈다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.schedule.cron import CronError, next_fire, parse_cron

KST = ZoneInfo("Asia/Seoul")
# 2026-09-14는 월요일이다. 아래 기대값 전부가 이 사실에 기댄다.
MONDAY_NOON = datetime(2026, 9, 14, 12, 0, tzinfo=KST)


def at(text: str, *, after: datetime = MONDAY_NOON) -> str:
    return next_fire(text, after).strftime("%Y-%m-%d(%a) %H:%M")


# ── 기본 ──────────────────────────────────────────────────────────────

def test_매일_8시():
    assert at("0 8 * * *") == "2026-09-15(Tue) 08:00"


def test_평일만():
    """금요일 낮에 물으면 다음은 토·일을 건너뛴 월요일이다."""
    friday = datetime(2026, 9, 18, 12, 0, tzinfo=KST)
    assert at("0 8 * * 1-5", after=friday) == "2026-09-21(Mon) 08:00"


def test_간격_표기():
    assert at("*/15 * * * *") == "2026-09-14(Mon) 12:15"
    assert at("0 */6 * * *") == "2026-09-14(Mon) 18:00"


def test_목록_표기():
    assert at("0 8,20 * * *") == "2026-09-14(Mon) 20:00"


def test_같은_순간은_돌려주지_않는다():
    """정각에 발사한 직후 다시 계산하면 **같은 시각이 나오면 안 된다.**

    나오면 그 자리에서 무한히 즉시 발사한다 — 1분 간격 확인 중에 메일이 쏟아진다.
    """
    eight = datetime(2026, 9, 14, 8, 0, tzinfo=KST)
    assert next_fire("0 8 * * *", eight) == datetime(2026, 9, 15, 8, 0, tzinfo=KST)


def test_초는_버린다():
    """08:00:30에 물어도 다음은 내일 8시다 — 오늘 8시는 이미 지났다."""
    assert at("0 8 * * *", after=datetime(2026, 9, 14, 8, 0, 30, tzinfo=KST)) \
        == "2026-09-15(Tue) 08:00"


def test_시간대를_그대로_쓴다():
    """"매일 8시"는 **그 시간대의** 8시다. 여기가 틀리면 UTC 파드에서 오후 5시에 돈다."""
    assert next_fire("0 8 * * *", MONDAY_NOON).utcoffset().total_seconds() == 9 * 3600


# ── 경계 ──────────────────────────────────────────────────────────────

def test_자정을_넘긴다():
    late = datetime(2026, 9, 14, 23, 30, tzinfo=KST)
    assert at("0 1 * * *", after=late) == "2026-09-15(Tue) 01:00"


def test_달을_넘긴다():
    assert at("0 8 1 * *", after=datetime(2026, 9, 20, 12, 0, tzinfo=KST)) \
        == "2026-10-01(Thu) 08:00"


def test_해를_넘긴다():
    assert at("0 8 1 1 *", after=datetime(2026, 9, 20, 12, 0, tzinfo=KST)) \
        == "2027-01-01(Fri) 08:00"


def test_윤년만_도는_식도_찾는다():
    """2027년은 평년이라 2028년까지 가야 한다 — 탐색 범위가 1년이면 못 찾는다."""
    assert at("0 0 29 2 *") == "2028-02-29(Tue) 00:00"


def test_일요일은_0이고_7이기도_하다():
    """두 표기가 다 쓰인다. 사람은 cron 식을 다른 데서 복사해 온다."""
    assert at("0 8 * * 0") == at("0 8 * * 7") == "2026-09-20(Sun) 08:00"


def test_일과_요일이_둘_다_있으면_OR다():
    """원래 cron이 그렇다 — `13 * 5`는 "13일 **또는** 금요일"이다.

    우리만 AND로 읽으면 같은 글자가 다른 날에 돌고, 그 차이는 **안 도는 날에만**
    드러난다.
    """
    # 2026-09-18이 금요일. AND라면 13일의 금요일을 찾아 한참 뒤로 갔을 것이다.
    assert at("0 8 13 * 5") == "2026-09-18(Fri) 08:00"
    # 10월 13일은 화요일인데, 일(日)이 맞으므로 역시 돈다.
    assert at("0 8 13 * 5", after=datetime(2026, 10, 10, 12, 0, tzinfo=KST)) \
        == "2026-10-13(Tue) 08:00"


def test_한쪽만_있으면_그쪽만_본다():
    assert at("0 8 13 * *") == "2026-10-13(Tue) 08:00"


# ── 잘못된 식 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("broken, why", [
    ("0 8 * *", "칸이 모자란다"),
    ("0 8 * * * *", "칸이 남는다"),
    ("60 8 * * *", "분이 범위를 넘는다"),
    ("0 24 * * *", "시가 범위를 넘는다"),
    ("0 8 0 * *", "일은 1부터다"),
    ("0 8 * 13 *", "월이 범위를 넘는다"),
    ("0 8 * * 8", "요일이 범위를 넘는다"),
    ("0 8-2 * * *", "범위가 거꾸로다"),
    ("*/0 * * * *", "간격이 0이다"),
    ("매일 8시", "cron이 아니다"),
    ("", "빈 식"),
])
def test_읽을_수_없는_식은_거부한다(broken, why):
    """**기동에서 죽이려고** 있는 예외다. finding으로 삼키면 설정 실수가 매일
    "안 돌았다"로 둔갑하고, 그 증상으로는 원인을 못 찾는다."""
    with pytest.raises(CronError):
        parse_cron(broken)


def test_영원히_안_도는_식도_잡는다():
    """2월 30일. 조용히 영원히 안 도는 것보다 시끄럽게 죽는 편이 낫다."""
    with pytest.raises(CronError, match="도는 날이 없다"):
        next_fire("0 0 30 2 *", MONDAY_NOON)


def test_오류_메시지가_어느_칸인지_말한다():
    with pytest.raises(CronError, match="hour"):
        parse_cron("0 99 * * *")
