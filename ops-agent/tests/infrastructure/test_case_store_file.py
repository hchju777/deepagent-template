"""케이스 파일 저장소 — **재시작을 넘기고, 깨졌을 때 조용히 비우지 않는다.**"""
from datetime import datetime

import pytest

from src.domain.cases import CaseRecord, fingerprint
from src.infrastructure.case_store_file import CaseStoreError, FileCaseRepository

T0 = datetime(2026, 9, 18, 9, 0, 0)


def record(repo_id="c-1", target="Operator/Check", **kw) -> CaseRecord:
    body = {"id": repo_id, "site": "mx/gumi", "check": "badge_all_zero",
            "target": target, "concern": "operation", "symptom": "s",
            "opened_at": T0, "last_seen_at": T0,
            "observed": {"alarm": 0, "caution": 0, "normal": 0}}
    body.update(kw)
    return CaseRecord.model_validate(body)


def test_없는_파일은_빈_저장소다(tmp_path):
    assert FileCaseRepository(tmp_path / "none.json").all() == []


def test_재시작을_넘긴다(tmp_path):
    path = tmp_path / "cases.json"
    first = FileCaseRepository(path)
    first.add(record(first.next_id()))

    # 완전히 새 객체 — 프로세스가 다시 뜬 것과 같다.
    again = FileCaseRepository(path)
    found = again.latest(fingerprint("mx/gumi", "badge_all_zero", "Operator/Check"))
    assert found is not None and found.observed == {"alarm": 0, "caution": 0, "normal": 0}


def test_번호가_재시작해도_안_되돌아간다(tmp_path):
    """호출부가 세면 재시작할 때 1로 돌아가고, 이미 있는 케이스와 id가 겹친다."""
    path = tmp_path / "cases.json"
    assert FileCaseRepository(path).next_id() == "c-1"
    assert FileCaseRepository(path).next_id() == "c-2"
    assert FileCaseRepository(path).next_id() == "c-3"


def test_깨진_파일을_조용히_비우지_않는다(tmp_path):
    """빈 저장소로 떨어지면 **열린 케이스를 전부 잊고 다시 연다.** 게다가 닫힌
    케이스도 잊으므로 "이미 조사한 것"이 되살아난다 — 조용히 틀리는 쪽이다."""
    path = tmp_path / "cases.json"
    path.write_text("{이건 JSON이 아니다", encoding="utf-8")
    with pytest.raises(CaseStoreError) as caught:
        FileCaseRepository(path).all()
    assert "빈 저장소로 떨어지지 않는다" in str(caught.value)


def test_스키마가_안_맞아도_안_비운다(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text('{"next_id": 1, "cases": [{"id": "c-1"}]}', encoding="utf-8")
    with pytest.raises(CaseStoreError):
        FileCaseRepository(path).all()


def test_쓰기가_원자적이다(tmp_path, monkeypatch):
    """쓰는 도중에 죽어도 **원본이 멀쩡해야** 한다.

    안 그러면 원본이 반쯤 덮여 깨지고, 그게 위의 "깨진 파일"이 된다 — 우리가 직접
    만들어 놓고 다음 순찰에 못 읽는 꼴이다.

    바꿔치기(`os.replace`)가 실패하게 만들어 본다. 임시 파일에 먼저 쓰는 구조면
    원본은 손도 안 탔다. 곧장 덮어쓰는 구조면 이미 날아가 있다.
    """
    import os

    path = tmp_path / "cases.json"
    first = FileCaseRepository(path)
    first.add(record(first.next_id()))
    before = path.read_text(encoding="utf-8")

    second = FileCaseRepository(path)
    monkeypatch.setattr(os, "replace",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("여기서 죽는다")))
    with pytest.raises(OSError):
        second.add(record("c-2", target="Material/ATR Status"))

    assert path.read_text(encoding="utf-8") == before      # 원본이 그대로다
    assert FileCaseRepository(path).all()[0].id == "c-1"   # 그리고 여전히 읽힌다


def test_임시_파일을_남기지_않는다(tmp_path):
    repo = FileCaseRepository(tmp_path / "cases.json")
    repo.add(record(repo.next_id()))
    assert not list(tmp_path.glob("*.tmp"))


def test_갱신이_파일에_남는다(tmp_path):
    path = tmp_path / "cases.json"
    repo = FileCaseRepository(path)
    made = record(repo.next_id())
    repo.add(made)
    repo.update(made.model_copy(update={"observations": 4, "status": "closed"}))

    again = FileCaseRepository(path).all()[0]
    assert (again.observations, again.status) == (4, "closed")


def test_없는_케이스를_갱신하면_거부한다(tmp_path):
    repo = FileCaseRepository(tmp_path / "cases.json")
    with pytest.raises(CaseStoreError):
        repo.update(record("c-99"))


def test_같은_지문이_여럿이면_최근_것을_준다(tmp_path):
    from datetime import timedelta
    repo = FileCaseRepository(tmp_path / "cases.json")
    repo.add(record(repo.next_id(), status="closed", cleared_at=T0))
    repo.add(record(repo.next_id(), opened_at=T0 + timedelta(hours=5)))
    found = repo.latest(fingerprint("mx/gumi", "badge_all_zero", "Operator/Check"))
    assert found.id == "c-2" and found.status == "open"


def test_디렉터리가_없으면_만든다(tmp_path):
    repo = FileCaseRepository(tmp_path / "깊은" / "경로" / "cases.json")
    repo.add(record(repo.next_id()))
    assert repo.all()[0].id == "c-1"
