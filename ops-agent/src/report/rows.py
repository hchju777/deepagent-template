"""문서 한 건 → 행 한 줄. **여기서 모든 "이상한 값"이 숫자로 바뀐다.**

## 왜 정규화 층이 따로 있는가

집계 코드가 `doc.get("scen_name")`을 직접 부르면 두 가지가 섞인다: "세는 법"과
"필드가 없거나 형식이 깨졌을 때 어떻게 하는가". 후자는 리포트에서 **숫자보다
중요한 정보**다 — 어제 건수가 0인 게 현장이 조용해서인지 필드 이름이 바뀌어서인지
구별해야 하기 때문이다.

그래서 이 층의 산출물은 행 목록과 **`RowProblems` 한 묶음**이다. 못 읽은 것을
조용히 버리지 않고 종류별로 센다.

## 무raise

문서 하나가 이상해도 던지지 않는다. 5만 건 중 한 건의 status가 `"알 수 없음"`일
때 리포트 전체가 죽으면, 그날 아침 아무도 아무것도 못 본다.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

from src.config.schema_report import SourceSpec
from src.report.window import ReportWindow, parse_moment

# 값이 없을 때 쓰는 표시. 빈 문자열이 아닌 이유: TOP 목록에 빈 칸이 올라오면
# 사람은 렌더링이 깨진 줄 안다. "(없음)"이면 데이터 문제임이 드러난다.
MISSING = "(없음)"


@dataclass(frozen=True)
class AlarmRow:
    """집계가 보는 유일한 모양. 문서의 필드 이름은 여기까지 올라오지 않는다."""
    gbm: str
    fct: str
    plant: str
    occurred_at: datetime
    part_code: str
    line_code: str
    line_name: str
    scenario_id: str
    scenario_name: str
    status: int | None
    unresolved: bool

    @property
    def day(self) -> date:
        return self.occurred_at.date()

    @property
    def line(self) -> tuple[str, str]:
        """코드와 이름을 함께 키로 쓴다 — 이름만으로는 법인 간에 겹친다."""
        return (self.line_code, self.line_name)

    @property
    def scenario(self) -> tuple[str, str]:
        return (self.scenario_id, self.scenario_name)


@dataclass(frozen=True)
class RowProblems:
    """버린 것과 수상한 것. **0이 아니면 리포트 본문에 실린다.**"""
    # 날짜 관련 두 카운터는 **실서버에서 거의 오르지 않는다** — 범위 필터가
    # 서버에서 먼저 거르기 때문이다(날짜 필드가 없는 문서는 $gte에 안 걸리고,
    # "04/09/2026"은 사전순으로 범위 밖이다). 그래도 죽은 코드가 아니다:
    # "2026-08-25 25:99:99"처럼 **사전순으로는 범위 안인데 형식이 깨진** 값은
    # 여기서만 드러난다. 그리고 스텁·다른 소스처럼 클라이언트가 거르는 경로에서는
    # 이것이 유일한 신호다.
    unreadable_date: int = 0          # 날짜 필드를 형식대로 읽을 수 없었다
    missing_date: int = 0             # 날짜 필드 자체가 없었다
    outside_range: int = 0            # 쿼리 범위 밖 — 필터나 형식이 틀렸다는 신호
    not_wanted: int = 0               # 범위 안이지만 쓰지 않는 날(주말 등)
    unreadable_status: int = 0        # status가 정수가 아니었다
    missing_fields: Counter = field(default_factory=Counter)
    gbm_mismatch: int = 0             # 문서의 gbm이 사이트의 gbm과 달랐다
    # **실제로 실패한 값들.** 건수만 있으면 "44,507건이 형식에 안 맞는다"까지만
    # 알고, 그 다음에 할 수 있는 일이 대상 DB를 직접 뒤지는 것밖에 없다. 값을
    # 몇 개만 보여 주면 "아, T가 들어 있구나"로 5초에 끝난다.
    date_samples: tuple[str, ...] = ()
    status_samples: tuple[str, ...] = ()

    def total_dropped(self) -> int:
        return self.unreadable_date + self.missing_date + self.outside_range + self.not_wanted

    def dropped_breakdown(self) -> dict[str, int]:
        """버린 이유별 건수. **0인 항목은 넣지 않는다** — 0으로 가득한 표는 안 읽힌다.

        "받아온 13,179건 − 집계에 쓴 973건"의 차이를 사람이 직접 맞춰 볼 수 있어야
        한다. 합이 안 맞으면 이 층에 세지 않는 탈락 경로가 있다는 뜻이다.
        """
        named = {"날짜 형식이 안 맞음": self.unreadable_date,
                 "날짜 필드 없음": self.missing_date,
                 "조회 범위 밖": self.outside_range,
                 "집계 대상 날이 아님(주말 등)": self.not_wanted}
        return {name: count for name, count in named.items() if count}

    def merge(self, other: "RowProblems") -> "RowProblems":
        return RowProblems(
            unreadable_date=self.unreadable_date + other.unreadable_date,
            missing_date=self.missing_date + other.missing_date,
            outside_range=self.outside_range + other.outside_range,
            not_wanted=self.not_wanted + other.not_wanted,
            unreadable_status=self.unreadable_status + other.unreadable_status,
            missing_fields=self.missing_fields + other.missing_fields,
            gbm_mismatch=self.gbm_mismatch + other.gbm_mismatch,
            date_samples=_merge_samples(self.date_samples, other.date_samples),
            status_samples=_merge_samples(self.status_samples, other.status_samples))

    def describe(self) -> list[str]:
        """사람이 읽을 한 줄짜리 설명들. 문제가 없으면 빈 목록."""
        lines = []
        if self.missing_date:
            lines.append(f"날짜 필드가 없는 문서 {self.missing_date}건")
        if self.unreadable_date:
            lines.append(f"날짜 형식이 맞지 않는 문서 {self.unreadable_date}건 "
                         f"— 실제 값: {_show(self.date_samples)}. "
                         f"date_format을 이 값에 맞추거나 parse_formats에 더해라")
        if self.outside_range:
            lines.append(f"조회 범위 밖 문서 {self.outside_range}건 "
                         f"— 문자열 날짜 비교가 의도대로 안 되고 있다는 신호다")
        if self.unreadable_status:
            lines.append(f"status가 정수가 아닌 문서 {self.unreadable_status}건 "
                         f"— 실제 값: {_show(self.status_samples)}")
        for name, count in sorted(self.missing_fields.items()):
            lines.append(f"{name} 필드가 없는 문서 {count}건")
        if self.gbm_mismatch:
            lines.append(f"문서의 gbm이 사이트와 다른 문서 {self.gbm_mismatch}건")
        return lines


# 예시를 몇 개까지 들고 있을 것인가. 상한이 있는 이유: 5만 건이 전부 다른 형태면
# 그 5만 개를 전부 들고 리포트에 싣게 된다. 3~5개면 형태는 이미 드러난다.
SAMPLE_LIMIT = 5

# 값을 그대로 싣기 전에 자른다 — 날짜 필드에 문서 전체가 들어 있는 사고도 있다.
SAMPLE_MAX_LEN = 60


class _Tally:
    """누적용 가변 카운터. 불변 `RowProblems`를 매 문서마다 새로 만들면
    5만 건 × 7필드만큼 객체가 생긴다."""

    def __init__(self):
        self.unreadable_date = self.missing_date = self.outside_range = 0
        self.not_wanted = self.unreadable_status = self.gbm_mismatch = 0
        self.missing_fields: Counter = Counter()
        self.date_samples: list[str] = []
        self.status_samples: list[str] = []

    def sample(self, bucket: list[str], raw) -> None:
        """서로 **다른** 값만 모은다. 같은 값 5개를 보여 주면 아무것도 안 알려준다."""
        if len(bucket) >= SAMPLE_LIMIT:
            return
        text = repr(raw)[:SAMPLE_MAX_LEN]
        if text not in bucket:
            bucket.append(text)

    def freeze(self) -> RowProblems:
        return RowProblems(
            unreadable_date=self.unreadable_date, missing_date=self.missing_date,
            outside_range=self.outside_range, not_wanted=self.not_wanted,
            unreadable_status=self.unreadable_status,
            missing_fields=self.missing_fields, gbm_mismatch=self.gbm_mismatch,
            date_samples=tuple(self.date_samples),
            status_samples=tuple(self.status_samples))


def _merge_samples(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    merged = list(left)
    for value in right:
        if value not in merged and len(merged) < SAMPLE_LIMIT:
            merged.append(value)
    return tuple(merged)


def _show(samples: tuple[str, ...]) -> str:
    return ", ".join(samples) if samples else "(예시 없음)"


def _text(doc: dict, key: str, tally: _Tally) -> str:
    value = doc.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        tally.missing_fields[key] += 1
        return MISSING
    return str(value).strip()


def _status(doc: dict, key: str, tally: _Tally) -> int | None:
    raw = doc.get(key)
    if raw is None:
        tally.missing_fields[key] += 1
        return None
    if isinstance(raw, bool):          # bool은 int의 하위형이다 — 먼저 걸러야 한다
        tally.unreadable_status += 1
        tally.sample(tally.status_samples, raw)
        return None
    if isinstance(raw, int):
        return raw
    try:
        return int(str(raw).strip())   # 문서에 "0"처럼 문자열로 들어온 경우
    except (ValueError, TypeError):
        tally.unreadable_status += 1
        tally.sample(tally.status_samples, raw)
        return None


def normalize(documents: list[dict], *, source: SourceSpec, window: ReportWindow,
              gbm: str, fct: str) -> tuple[list[AlarmRow], RowProblems]:
    """문서 목록을 행으로. 쓰지 않는 날의 문서는 버리고, 버린 이유를 센다.

    `gbm`/`fct`는 **사이트가 정한다** — 문서에도 gbm 필드가 있지만 그것을 신뢰하면
    한 법인의 Mongo에 다른 법인 데이터가 섞여 있을 때 그대로 따라간다. 문서의
    값은 어긋날 때 세기만 하고(`gbm_mismatch`) 쓰지는 않는다.
    """
    fields = source.fields
    unresolved_set = set(source.unresolved_status)
    wanted = window.wanted
    tally = _Tally()
    rows: list[AlarmRow] = []

    for doc in documents:
        if not isinstance(doc, dict):
            tally.missing_date += 1
            continue
        raw_date = doc.get(source.date_field)
        if raw_date is None:
            tally.missing_date += 1
            continue
        moment = parse_moment(source, raw_date)
        if moment is None:
            tally.unreadable_date += 1
            tally.sample(tally.date_samples, raw_date)
            continue
        day = moment.date()
        if not window.covers(day):
            tally.outside_range += 1
            continue
        if day not in wanted:
            tally.not_wanted += 1
            continue

        doc_gbm = doc.get(fields.gbm)
        if doc_gbm is not None and str(doc_gbm).strip() and str(doc_gbm).strip() != gbm:
            tally.gbm_mismatch += 1

        status = _status(doc, fields.status, tally)
        plant = doc.get(fields.plant)
        rows.append(AlarmRow(
            gbm=gbm,
            fct=fct,
            # plant 필드가 없으면 사이트 이름으로 대체한다 — TOP 목록에 "(없음)"이
            # 1등으로 올라오는 것보다 법인 이름이 맞다.
            plant=str(plant).strip() if plant is not None and str(plant).strip() else fct,
            occurred_at=moment,
            part_code=_text(doc, fields.part_code, tally),
            line_code=_text(doc, fields.line_code, tally),
            line_name=_text(doc, fields.line_name, tally),
            scenario_id=_text(doc, fields.scenario_id, tally),
            scenario_name=_text(doc, fields.scenario_name, tally),
            status=status,
            unresolved=status in unresolved_set))

    return rows, tally.freeze()


def projection(source: SourceSpec) -> list[str]:
    """대상 Mongo에 요청할 필드 목록. **여기 없는 필드는 행에 올라오지 않는다.**

    `normalize`가 읽는 필드와 **같은 곳에서** 나와야 한다 — 두 벌로 적으면
    필드를 추가할 때 한쪽만 고쳐서 "값이 항상 (없음)"이 된다.
    """
    f = source.fields
    return sorted({source.date_field, f.gbm, f.plant, f.part_code, f.line_code,
                   f.line_name, f.scenario_id, f.scenario_name, f.status})
