"""라벨 유입구와 게이트(계획 15/P8)."""
from datetime import datetime, timezone

from src.application.labels import calibration, label_stats, submit_label
from src.domain.cases import CaseRecord, InMemoryCaseRepository
from src.domain.label import InMemoryLabelStore, RootCauseLabel
from src.domain.snapshot import InMemoryVerdictSnapshotStore, VerdictSnapshot

T = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def _repo(closed=0, open_=0):
    repo = InMemoryCaseRepository()
    for i in range(closed):
        repo.save(CaseRecord(id=f"c-{i}", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                             t0=T, created_at=T, updated_at=T, status="closed",
                             closed_reason="조사 완료"))
    for i in range(open_):
        repo.save(CaseRecord(id=f"o-{i}", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                             t0=T, created_at=T, updated_at=T, status="investigating"))
    return repo


def test_라벨은_기록되고_없는_케이스는_거절된다():
    repo, labels = _repo(closed=1), InMemoryLabelStore()
    assert submit_label("c-0", agreement="wrong", resolution="fixed",
                        actual_component="plan-sync", saw_report=True, labeled_by="hchju",
                        repo=repo, labels=labels, clock=lambda: T) == "recorded"
    row = labels.list_for("c-0")[0]
    assert row.agreement == "wrong" and row.actual_root_cause_component == "plan-sync"
    assert row.saw_report is True and row.labeled_by == "hchju" and row.labeled_at == T
    assert submit_label("없음", agreement="correct", repo=repo, labels=labels,
                        clock=lambda: T) == "not_found"


def test_조사_중인_케이스도_라벨할_수_있다():
    # 사람이 조사가 끝나기 전에 원인을 알 수 있다. 분모(종결)와는 다른 축이다.
    repo, labels = _repo(open_=1), InMemoryLabelStore()
    assert submit_label("o-0", agreement="correct", repo=repo, labels=labels,
                        clock=lambda: T) == "recorded"


def test_저장소_장애는_error다():
    class _Boom(InMemoryLabelStore):
        def append(self, label):
            raise RuntimeError("mongo down")
    assert submit_label("c-0", agreement="correct", repo=_repo(closed=1), labels=_Boom(),
                        clock=lambda: T) == "error"


def test_게이트는_닫혀_있고_퍼센트를_내지_않는다():
    # n>=30 그리고 라벨률>50% 전에는 어떤 정확도도 내지 않는다 — 건수만.
    repo, labels = _repo(closed=40), InMemoryLabelStore()
    for i in range(12):
        submit_label(f"c-{i}", agreement="correct", repo=repo, labels=labels, clock=lambda: T)
    stats = label_stats(repo=repo, labels=labels)
    assert stats.closed_total == 40 and stats.labeled_cases == 12 and stats.gate_open is False
    assert "12" in stats.why and "40" in stats.why and "%" not in stats.why


def test_게이트는_건수와_라벨률을_둘_다_요구한다():
    repo, labels = _repo(closed=100), InMemoryLabelStore()
    for i in range(40):                      # 40건이지만 라벨률 40%
        submit_label(f"c-{i}", agreement="correct", repo=repo, labels=labels, clock=lambda: T)
    assert label_stats(repo=repo, labels=labels).gate_open is False
    repo3, labels3 = _repo(closed=40), InMemoryLabelStore()
    for i in range(25):                      # 라벨률 62%지만 25건 — 건수 미달
        submit_label(f"c-{i}", agreement="correct", repo=repo3, labels=labels3, clock=lambda: T)
    assert label_stats(repo=repo3, labels=labels3).gate_open is False
    repo2, labels2 = _repo(closed=40), InMemoryLabelStore()
    for i in range(30):                      # 30건, 75%
        submit_label(f"c-{i}", agreement="correct", repo=repo2, labels=labels2, clock=lambda: T)
    assert label_stats(repo=repo2, labels=labels2).gate_open is True


def test_종결_케이스가_없으면_라벨률은_0이고_게이트는_닫힌다():
    assert label_stats(repo=_repo(), labels=InMemoryLabelStore()).gate_open is False


def test_게이트의_n은_종결_케이스의_라벨만_센다():
    # 리뷰 M2: 진행 중 케이스 27건을 라벨한 것만으로 게이트가 열렸다 — 그때 대조 가능한
    # 데이터는 종결 3건뿐이다.
    repo, labels = _repo(closed=4, open_=27), InMemoryLabelStore()
    for i in range(3):
        submit_label(f"c-{i}", agreement="correct", repo=repo, labels=labels, clock=lambda: T)
    for i in range(27):
        submit_label(f"o-{i}", agreement="correct", repo=repo, labels=labels, clock=lambda: T)
    stats = label_stats(repo=repo, labels=labels)
    assert stats.labeled_cases == 30 and stats.gate_open is False


def test_라벨률은_절반_초과여야_한다():
    # 리뷰 돌연변이 #9: 경계(정확히 절반)가 미검증이었다.
    repo, labels = _repo(closed=60), InMemoryLabelStore()
    for i in range(30):                       # 정확히 50% — 초과가 아니다
        submit_label(f"c-{i}", agreement="correct", repo=repo, labels=labels, clock=lambda: T)
    assert label_stats(repo=repo, labels=labels).gate_open is False


def test_집계는_저장소가_죽어도_raise하지_않는다():
    # 이 테스트는 한동안 `list_by_status`의 **두 번째** 호출에서 죽는 저장소를 썼는데,
    # `label_stats`는 한 번만 부른다 — 예외가 한 번도 발화하지 않아 핸들러를 지우는
    # 변조가 통과했다(검증 리뷰 LOW-B). 첫 호출에서 죽여야 실제로 방어를 지난다.
    class _Boom(InMemoryCaseRepository):
        def list_by_status(self, status):
            raise RuntimeError("mongo down")

    stats = label_stats(repo=_Boom(), labels=InMemoryLabelStore())
    assert stats.gate_open is False and "실패" in stats.why


def test_라벨_표현은_저장소_장애에도_빈_목록이다():
    # 리뷰 돌연변이 #10·#11: try/except와 None 가드 둘 다 테스트가 없었다.
    from src.application.labels import label_texts

    class _Boom(InMemoryLabelStore):
        def list_for(self, case_id):
            raise RuntimeError("mongo down")
    assert label_texts(_Boom(), "c-1") == []
    assert label_texts(None, "c-1") == []


def test_집계는_게이트를_모는_숫자를_따로_낸다():
    # 재검증 low: labeled_cases는 전체 라벨이라 게이트를 모는 숫자가 아니다.
    repo, labels = _repo(closed=2, open_=5), InMemoryLabelStore()
    submit_label("c-0", agreement="correct", repo=repo, labels=labels, clock=lambda: T)
    for i in range(5):
        submit_label(f"o-{i}", agreement="correct", repo=repo, labels=labels, clock=lambda: T)
    stats = label_stats(repo=repo, labels=labels)
    assert stats.labeled_cases == 6 and stats.labeled_closed == 1
    assert "그중 라벨됨 1건" in stats.why


def _snap(case_id, confidence, *, outcome="closed"):
    return VerdictSnapshot(case_id=case_id, closed_at=T, gbm="mx", fct="gumi",
                           fingerprint="fp", outcome=outcome, confidence=confidence)


def _open_gate(*, labeled, confidence="high", agreement="correct", saw_report=False):
    """게이트를 여는 최소 트리 — 종결 `labeled*2`건 중 `labeled`건에 라벨."""
    repo = _repo(closed=labeled * 2 - 1)          # 라벨 수가 종결의 절반을 넘게
    labels, snapshots = InMemoryLabelStore(), InMemoryVerdictSnapshotStore()
    for i in range(labeled):
        snapshots.put(_snap(f"c-{i}", confidence))
        submit_label(f"c-{i}", agreement=agreement, saw_report=saw_report,
                     repo=repo, labels=labels, clock=lambda: T)
    return repo, labels, snapshots


def test_게이트가_닫혀_있으면_버킷을_내지_않는다():
    # 12/40으로 낸 30%는 다음 주에 뒤집힐 숫자이고, 한 번 보고되면 사람이 기억한다.
    repo, labels, snapshots = _open_gate(labeled=3)
    result = calibration(repo=repo, labels=labels, snapshots=snapshots)
    assert result.gate_open is False and result.buckets == []


def test_게이트가_열리면_confidence별_적중을_낸다():
    repo, labels, snapshots = _open_gate(labeled=30)
    result = calibration(repo=repo, labels=labels, snapshots=snapshots)
    assert result.gate_open is True
    bucket = next(b for b in result.buckets if b.confidence == "high")
    assert bucket.n == 30 and bucket.correct == 30


def test_unknown_라벨은_분모에서_빠지고_수는_보고된다():
    # "모르겠다"는 틀렸다는 증거가 아니다. 다만 조용히 빼면 n이 왜 작은지 모른다.
    repo, labels, snapshots = _open_gate(labeled=30)
    snapshots.put(_snap("c-40", "high"))
    repo.save(CaseRecord(id="c-40", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                         t0=T, created_at=T, updated_at=T, status="closed",
                         closed_reason="조사 완료"))
    submit_label("c-40", agreement="unknown", repo=repo, labels=labels, clock=lambda: T)
    bucket = next(b for b in calibration(repo=repo, labels=labels,
                                         snapshots=snapshots).buckets
                  if b.confidence == "high")
    # `n == correct + partially + wrong`은 n의 **정의**라 어떤 구현에서도 참이다 —
    # 항진명제로는 unknown을 wrong에 더하는 변조를 못 잡는다(검증 리뷰 M2a).
    assert bucket.n == 30 and bucket.correct == 30
    assert bucket.wrong == 0 and bucket.partially_correct == 0
    assert bucket.excluded_unknown == 1


def test_케이스당_마지막_라벨만_센다():
    # 라벨은 append-only라 한 케이스에 여럿 붙는다. 전부 세면 여러 번 고친 케이스가
    # 분모를 지배한다.
    repo, labels, snapshots = _open_gate(labeled=30)
    submit_label("c-0", agreement="wrong", repo=repo, labels=labels, clock=lambda: T)
    bucket = next(b for b in calibration(repo=repo, labels=labels,
                                         snapshots=snapshots).buckets
                  if b.confidence == "high")
    assert bucket.n == 30 and bucket.wrong == 1 and bucket.correct == 29


def test_confidence가_없으면_미상_버킷이다():
    # 버리면 분모에 생존 편향이 생긴다 — confidence를 못 낸 판정이 곧 어려운 케이스다.
    repo, labels, snapshots = _open_gate(labeled=30)
    snapshots.put(_snap("c-0", None))
    buckets = {b.confidence: b for b in calibration(repo=repo, labels=labels,
                                                    snapshots=snapshots).buckets}
    assert buckets["미상"].n == 1 and buckets["high"].n == 29


def test_스냅샷이_없는_라벨은_세지_않는다():
    repo, labels, snapshots = _open_gate(labeled=30)
    repo.save(CaseRecord(id="c-41", gbm="mx", fct="gumi", fingerprint="fp", symptom="s",
                         t0=T, created_at=T, updated_at=T, status="closed",
                         closed_reason="조사 완료"))
    submit_label("c-41", agreement="wrong", repo=repo, labels=labels, clock=lambda: T)
    assert sum(b.n for b in calibration(repo=repo, labels=labels,
                                        snapshots=snapshots).buckets) == 30


def test_보고서를_보고_단_라벨의_수를_함께_낸다():
    # 앵커링 의심을 숫자 옆에 두지 않으면 사람이 적중률만 읽는다.
    repo, labels, snapshots = _open_gate(labeled=30, saw_report=True)
    bucket = next(b for b in calibration(repo=repo, labels=labels,
                                         snapshots=snapshots).buckets
                  if b.confidence == "high")
    assert bucket.saw_report == 30


def test_저장소가_던져도_캘리브레이션은_상태로_돌려준다():
    # 규율 1 — 관측성이 명령을 죽이면 안 된다.
    class _Boom(InMemoryVerdictSnapshotStore):
        def get(self, case_id):
            raise RuntimeError("mongo down")

    repo, labels, _ = _open_gate(labeled=30)
    result = calibration(repo=repo, labels=labels, snapshots=_Boom())
    assert result.buckets == [] and "실패" in result.why


def test_버킷_순서는_결정론적이다():
    # "표시 순서는 코드가 쥔다"는 주석을 지키는 테스트가 없었다 — 정렬을 없애도,
    # 역순으로 뒤집어도 통과했다(검증 리뷰 LOW-1).
    repo, labels, snapshots = _open_gate(labeled=30)
    for i, confidence in enumerate((None, "low", "medium")):
        snapshots.put(_snap(f"c-{i}", confidence))
    result = calibration(repo=repo, labels=labels, snapshots=snapshots)
    assert [b.confidence for b in result.buckets] == ["high", "medium", "low", "미상"]


def test_라벨_유입구도_저장소_장애를_error로_돌려준다():
    # `KeyError`(없는 케이스) 경로만 테스트가 있었고, 저장소 장애 경로는 무검증이라
    # 그 핸들러를 지워도 스위트가 몰랐다(검증 리뷰 LOW-C).
    class _Boom(InMemoryCaseRepository):
        def get(self, case_id):
            raise RuntimeError("mongo down")

    assert submit_label("c-0", agreement="correct", repo=_Boom(),
                        labels=InMemoryLabelStore(), clock=lambda: T) == "error"


def test_저장소에서_사라진_케이스의_라벨은_세지_않는다():
    # 스냅샷과 라벨은 retention보다 오래 산다(`domain/snapshot.py`) — 그래서 케이스가
    # 이미 purge된 라벨이 존재할 수 있다. 종결 목록 교집합이 그것을 거르는데, 그 필터를
    # 지켜 주는 테스트가 없었다(검증 리뷰 LOW-D).
    repo, labels, snapshots = _open_gate(labeled=30)
    snapshots.put(_snap("c-사라짐", "low"))
    labels.append(RootCauseLabel(case_id="c-사라짐", agreement="wrong", labeled_at=T))
    result = calibration(repo=repo, labels=labels, snapshots=snapshots)
    assert [b.confidence for b in result.buckets] == ["high"]
