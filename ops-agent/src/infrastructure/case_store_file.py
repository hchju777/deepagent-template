"""케이스를 JSON 파일 하나에 둔다.

## 왜 파일인가

Mongo를 붙이면 6a가 저장소 설계가 된다. 지금 필요한 것은 "재시작을 넘긴다"뿐이고,
케이스는 사이트 28개 × 점검 몇 개 × 대상 몇십 개라 파일로 충분하다. 규모가 문제가
되면 그때 옮긴다 — 포트가 하나라 바꿀 곳도 하나다.

**파일이 프로세스보다 오래 사는지**(공유 드라이브인가, 컨테이너 안인가)는 배포 형태가
정해져야 답이 나온다. backlog ④와 같은 질문이다.

## 깨진 파일을 조용히 비우지 않는다

읽다 실패하면 "케이스가 하나도 없다"로 떨어지고 싶은 유혹이 있는데, 그러면
**열려 있던 케이스를 전부 잊고 다시 연다.** 게다가 닫힌 케이스도 잊으므로 "이미
조사한 것"이 되살아난다. 조용히 틀리는 쪽이다.

그래서 던진다. 게이트가 그걸 `rejected`로 흡수하고 이유를 남기므로, 순찰은 안 죽고
사람은 "케이스가 안 열린다"는 것을 출력에서 본다.

## 쓰기는 원자적으로

임시 파일에 쓰고 `os.replace`로 바꾼다. 쓰는 도중에 프로세스가 죽으면 원본이 반쯤
덮여 깨지는데, 그게 바로 위의 "깨진 파일"이다 — 만들지 않는 게 낫다.
`os.replace`는 Windows에서도 원자적이다.
"""
import json
import os
from pathlib import Path

from src.domain.cases import CaseRecord, CaseRepositoryPort, Fingerprint


class CaseStoreError(Exception):
    """파일이 없거나 깨졌거나 쓸 수 없다."""


class FileCaseRepository(CaseRepositoryPort):
    def __init__(self, path: Path):
        self._path = Path(path)
        self._state: dict | None = None

    # ── 파일 ────────────────────────────────────────────────────
    def _load(self) -> dict:
        if self._state is not None:
            return self._state
        if not self._path.exists():
            self._state = {"next_id": 1, "cases": []}
            return self._state
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._state = {"next_id": int(raw["next_id"]),
                           "cases": [CaseRecord.model_validate(c) for c in raw["cases"]]}
        except Exception as exc:                                    # noqa: BLE001
            raise CaseStoreError(
                f"{self._path}를 읽을 수 없다 — {type(exc).__name__}: {exc}. "
                f"빈 저장소로 떨어지지 않는다 — 열린 케이스를 전부 잊고 다시 열게 된다") from exc
        return self._state

    def _flush(self) -> None:
        state = self._load()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"next_id": state["next_id"],
                           "cases": [json.loads(c.model_dump_json()) for c in state["cases"]]},
                          ensure_ascii=False, indent=2)
        temp = self._path.with_suffix(self._path.suffix + ".tmp")
        temp.write_text(body, encoding="utf-8")
        os.replace(temp, self._path)

    # ── 포트 ────────────────────────────────────────────────────
    def latest(self, key: Fingerprint) -> CaseRecord | None:
        found = [c for c in self._load()["cases"] if c.fingerprint == key]
        return max(found, key=lambda c: c.opened_at) if found else None

    def add(self, record: CaseRecord) -> None:
        self._load()["cases"].append(record)
        self._flush()

    def update(self, record: CaseRecord) -> None:
        cases = self._load()["cases"]
        for index, existing in enumerate(cases):
            if existing.id == record.id:
                cases[index] = record
                self._flush()
                return
        raise CaseStoreError(f"없는 케이스를 갱신하려 한다 — {record.id}")

    def all(self) -> list[CaseRecord]:
        return sorted(self._load()["cases"], key=lambda c: c.opened_at, reverse=True)

    def next_id(self) -> str:
        state = self._load()
        made = f"c-{state['next_id']}"
        state["next_id"] += 1
        self._flush()
        return made
