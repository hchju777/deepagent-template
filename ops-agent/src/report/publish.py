"""리포트 발행 — **파일을 먼저 쓰고, 그다음 메일.** 조립은 여기 한 곳뿐이다.

## 왜 조립이 한 곳이어야 하는가

지금 여기를 거치는 것은 `report run` 하나뿐이다(`report render`는 파일만 쓰는 검토
도구라 발행이 아니다). 앞으로 는다 — 재발송, 순찰에서의 자동 발행, 웹 UI의 "지금
보내기". 각 경로가 "파일 쓰고 메일 보내기"를 따로 베끼면 **언젠가 하나가 빠뜨린다.**
원본 템플릿에서 `case resume`이 한동안 메일을 안 보냈고, 아무도 몰랐다.

그래서 새 발행 경로를 추가한다면 반드시 `publish()`를 거쳐야 한다.
`tests/report/test_publish.py`가 `.send(` 호출 위치를 구조로 못 박는다.

## 왜 파일이 먼저인가

메일이 실패하는 이유는 많다 — 게이트웨이 점검, 키 만료, 방화벽. 그때 **파일이라도
남아 있으면** 사람이 열어 보거나 직접 첨부해 보낼 수 있다. 순서를 뒤집으면 발송
실패가 곧 "아무것도 없음"이 된다.

같은 이유로 메일 실패가 파일 쓰기를 되돌리지 않는다. 반쪽이라도 남는 편이 낫다.

## 제목에 기준일이 들어간다

매일 같은 제목이면 받는 쪽 메일함에서 어제 것과 구분이 안 되고, 검색도 안 된다.
`[운영리포트] 일일 알람 리포트 2026-09-04(금)` 꼴로 나간다.
"""
from dataclasses import dataclass
from pathlib import Path

from src.config.schema_report import ReportScenario
from src.domain.base import Clock
from src.domain.mail import MailPort, MailResult
from src.report.window import WEEKDAY_LABEL, ReportWindow


@dataclass(frozen=True)
class Published:
    """발행 한 번의 결과. **실패도 결과다.**"""
    path: Path | None
    # 메일을 조립했다면 config의 접두사가 붙은 **실제로 나간 제목**, 안 했다면
    # (`--no-mail`) 접두사 없는 것. 접두사는 메일 설정의 것이라 포트가 붙인다.
    subject: str
    write_error: str | None = None
    mail: MailResult | None = None
    preview: dict | None = None          # --dry-run일 때 나갈 요청

    @property
    def failed(self) -> bool:
        return bool(self.write_error) or (self.mail is not None
                                          and self.mail.status == "error")

    def describe(self) -> list[str]:
        lines = []
        if self.write_error:
            lines.append(f"파일을 쓰지 못했다 — {self.write_error}")
        elif self.path:
            lines.append(f"파일: {self.path}")
        if self.preview is not None:
            lines.append(f"보내지 않았다(dry-run) — 수신자 "
                         f"{', '.join(self.preview['recipients'])}")
        elif self.mail is not None:
            if self.mail.status == "sent":
                lines.append(f"메일 발송: {', '.join(self.mail.recipients)}")
            elif self.mail.status == "skipped":
                lines.append(f"메일 건너뜀 — {self.mail.reason or 'mail.enabled=false'}")
            else:
                lines.append(f"메일 실패 — {self.mail.error}")
        return lines


def subject_for(scenario: ReportScenario, window: ReportWindow) -> str:
    """제목의 **기준일은 어제**다(리포트가 다루는 날). 돌린 날이 아니다.

    돌린 날을 쓰면 재실행·지연 실행에서 같은 데이터가 다른 제목으로 두 번 간다.
    """
    day = window.yesterday
    return (f"{scenario.title} {day.isoformat()}"
            f"({WEEKDAY_LABEL[day.weekday()]})")


def output_path(directory: Path, scenario_name: str, window: ReportWindow) -> Path:
    """`output/daily-alarm-2026-09-04.html`.

    기준일을 파일 이름에 넣는 이유: 매일 덮어쓰면 "어제 리포트를 다시 보고 싶다"가
    불가능해진다. 정렬도 날짜순으로 맞는다.
    """
    return directory / f"{scenario_name}-{window.yesterday.isoformat()}.html"


async def publish(html: str, *, scenario: ReportScenario, scenario_name: str,
                  window: ReportWindow, output_dir: Path, mail: MailPort | None,
                  clock: Clock, dry_run: bool = False) -> Published:
    """파일을 쓰고, 메일을 보낸다. **이 순서는 바뀌지 않는다.**

    무raise: 디스크가 가득 차도, 게이트웨이가 죽어도 예외를 올리지 않는다. 스케줄러가
    예외를 받으면 그 잡을 조용히 스케줄에서 뺄 수 있고, 그러면 리포트가 안 나가는
    것을 아무도 모르게 된다.
    """
    subject = subject_for(scenario, window)
    path: Path | None = output_path(output_dir, scenario_name, window)
    write_error: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
    except OSError as exc:
        write_error, path = f"{type(exc).__name__}: {exc}", None

    if mail is None:
        return Published(path=path, subject=subject, write_error=write_error)

    full = mail.full_subject(subject)
    if dry_run:
        return Published(path=path, subject=full, write_error=write_error,
                         preview=mail.preview(full, html))

    try:
        result = await mail.send(full, html)
    except Exception as exc:                                       # noqa: BLE001
        # 최외곽 방어선. 어댑터가 계약을 어기고 던져도 파일은 이미 쓰였다.
        result = MailResult(status="error", sent_at=clock(), recipients=[],
                            subject=full, error=f"{type(exc).__name__}: {exc}")
    return Published(path=path, subject=full, write_error=write_error, mail=result)
