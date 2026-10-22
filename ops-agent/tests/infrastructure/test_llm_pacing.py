"""429(분당 할당량) 뒤 기다리기와 호출 간 최소 간격 — 게이트웨이 단위로 공유한다(할당량이 모델을 가리지 않는다)."""
from datetime import datetime, timedelta, timezone

from src.infrastructure.llm_pacing import Pacer, next_access_wait, pacer_for

T0 = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)


def test_nextAccessTime을_여러_모양으로_읽는다():
    """사내 게이트웨이는 429 본문에 `nextAccessTime`(보통 다음 분)을 싣는다 — 모양은 확인 못 했으니 ISO·naive·epoch 초·밀리초를 다 받는다."""
    later = T0 + timedelta(seconds=42)
    assert next_access_wait({"error": {"nextAccessTime": later.isoformat()}}, now=T0) == 42
    assert next_access_wait({"nextAccessTime": later.strftime("%Y-%m-%dT%H:%M:%S")}, now=T0) == 42   # naive는 now의 시간대
    assert next_access_wait({"detail": {"nextAccessTime": later.timestamp()}}, now=T0) == 42           # epoch 초
    assert next_access_wait({"nextAccessTime": int(later.timestamp() * 1000)}, now=T0) == 42           # epoch 밀리초
    assert next_access_wait({"nextAccessTime": (T0 - timedelta(seconds=5)).isoformat()}, now=T0) == 0  # 과거면 바로
    assert next_access_wait({"error": "quota"}, now=T0) is None
    assert next_access_wait("not json", now=T0) is None
    assert next_access_wait({"nextAccessTime": "언제?"}, now=T0) is None


async def test_pacer는_게이트웨이_단위로_최소_간격을_지킨다():
    """리드가 빨라지자 판정 모델까지 같이 막혔다 — 간격은 어댑터가 아니라 게이트웨이(base_url)가 쥔다."""
    clock, slept = {"t": 100.0}, []

    async def sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds

    pacer = Pacer(ticker=lambda: clock["t"])
    await pacer.wait_turn(5.0, sleep=sleep)
    assert slept == []                                   # 첫 호출은 안 기다린다
    clock["t"] += 2
    await pacer.wait_turn(5.0, sleep=sleep)
    assert slept == [3.0]
    await pacer.wait_turn(0.0, sleep=sleep)
    assert slept == [3.0]                                # 간격 0이면 안 기다린다
    assert pacer_for("http://gw.example/v1") is pacer_for("http://gw.example/v1/")
    assert pacer_for("http://gw.example/v1") is not pacer_for("http://other.example/v1")


def test_사내_게이트웨이의_월_약어_시각을_읽는다():
    """사내 측정 #3: 429가 매번 정확히 60.0초 — nextAccessTime이 `2026-Oct-08 02:09:00+0000 UTC`(월 영문 약어, 오프셋 뒤 UTC)라
    ISO로 못 읽고 기본값으로 갔다. 월 약어는 로캘과 무관하게 표로 읽는다(사내 Windows는 한국어 로캘이다)."""
    now = datetime(2026, 10, 8, 2, 8, 30, tzinfo=timezone.utc)
    body = {"code": "429", "message": "quota", "description": "x", "nextAccessTime": "2026-Oct-08 02:09:00+0000 UTC"}
    assert next_access_wait(body, now=now) == 30
    kst = now.astimezone(timezone(timedelta(hours=9)))                                              # 사내 시계는 KST다
    assert next_access_wait({"nextAccessTime": "2026-OCT-08 02:09:00 UTC"}, now=kst) == 30          # 오프셋 없이 UTC만 — KST로 보면 안 된다
    assert next_access_wait({"nextAccessTime": "2026-oct-8 02:09:00+0900"}, now=now) is not None   # 한 자리 날짜·다른 오프셋
    assert next_access_wait({"nextAccessTime": "2026-Foo-08 02:09:00+0000 UTC"}, now=now) is None


def test_quota_wait는_기다린_근거를_같이_준다():
    from src.infrastructure.llm_pacing import quota_wait

    now = datetime(2026, 10, 8, 2, 8, 30, tzinfo=timezone.utc)
    assert quota_wait({"nextAccessTime": "2026-Oct-08 02:09:00+0000 UTC"}, None, now=now, cap=120) == (30, "nextAccessTime")
    assert quota_wait({"code": "429"}, {"retry-after": "4"}, now=now, cap=120) == (4.0, "Retry-After")
    assert quota_wait({"code": "429"}, None, now=now, cap=120) == (60.0, "기본값")
    assert quota_wait({"code": "429"}, None, now=now, cap=10) == (10, "기본값")
