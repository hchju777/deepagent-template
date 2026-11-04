"""실패 알림 — 스케줄 실행이 `failed`·`timeout`이면 `mail.notify_to`로 짧은 메일 한 통. 여기는 **무엇을 쓰나**만 정한다.

보내는 것은 스케줄러가 작업 실행기에 `mail send --to=<notify_to>`로 넣는다(`scheduler.py`) — 발송 호출 자리를 늘리지 않고,
알림 자체도 `/jobs`에 작업으로 남는다.

## 왜 failed·timeout만인가

경고(일부 법인을 못 읽고 나간 리포트)는 28개 중 하나가 거의 매일 안 붙어서, 알리면 매일 간다 — 매일 오는 알림은 아무도 안
읽는다. 이미 보냄도 실패가 아니다. 사람이 Swagger에서 돌린 실행은 그 사람이 결과를 이미 봤다 — 알림은 아무도 안 보는 스케줄
실행을 위한 것이다.

## 본문 크기

본문은 명령줄 한 칸(`--body=`)으로 넘어간다. Windows 명령줄은 3만 2천 자가 상한이라, stderr 끝 30줄을 줄마다 300자로 자른다.
"""
from src.presentation.api.jobs import Job
from src.presentation.api.paths import OPS
from src.report.publish import subject_for_day
from src.report.window import WEEKDAY_LABEL

NOTIFY_ON = ("failed", "timeout")
TAIL_LINES = 30
LINE_MAX = 300


def kind_of(job: Job) -> tuple[str, str]:
    """(제목에 붙일 짧은 말, 본문의 실패 종류)."""
    if job.status == "timeout":
        return "시간 초과", f"시간 초과 — 상한 {job.timeout_s:g}초에 죽였다"
    if job.exit_code is None:
        return "실패", "실패 — 작업을 띄우지 못했다"
    return "실패", f"실패 — 종료 코드 {job.exit_code}"


def stderr_tail(text: str) -> list[str]:
    lines = text.rstrip("\n").splitlines()[-TAIL_LINES:] if text.strip() else []
    return [line if len(line) <= LINE_MAX else line[:LINE_MAX] + "…" for line in lines]


def compose(*, name: str, scenario, day, job: Job) -> tuple[str, str]:
    """(제목, 본문). 제목의 접두(`[운영리포트]`)는 메일 포트가 붙인다."""
    short, kind = kind_of(job)
    title = subject_for_day(scenario, day)
    tail = stderr_tail(job.stderr) or [f"(비어 있다 — 출력 전체는 {OPS}/jobs/{job.id})"]
    body = "\n".join([
        "스케줄 실행이 실패했다 — 오늘 리포트가 안 나갔을 수 있다.",
        "",
        f"- 시나리오: {name} ({scenario.title})",
        f"- 기준일: {day.isoformat()}({WEEKDAY_LABEL[day.weekday()]})",
        f"- 실패 종류: {kind}",
        f"- 작업: {job.id} ({job.started_at:%Y-%m-%d %H:%M} 시작, {job.took_s}초)",
        "",
        f"할 일: {OPS}/jobs/{job.id} 보고 {OPS}/report/resend (date={day.isoformat()})",
        f"그날 HTML이 없으면(파일을 쓰기 전에 멈췄으면) {OPS}/report/run (dry_run=false)",
        "",
        "자동 재시도는 없다. 이 알림은 시나리오마다 하루 한 통이다.",
        "",
        f"stderr 끝 {TAIL_LINES}줄:",
        *tail,
    ])
    return f"[실패 알림] {title} — {short}", body
