"""uvicorn이 찾는 이름 — `uvicorn src.presentation.api.app:app --host 0.0.0.0 --port 9510`.

**API의 바깥 경계다.** CLI의 `__main__`처럼 여기서만 진짜 시계·env·빌드 정보를 만든다. 안쪽(`assembly` 이하)은
전부 받아 쓴다.

워커는 하나로 띄운다(`--workers`를 주지 않는다) — 작업 기록·스케줄러가 이 프로세스의 메모리에 있다.
"""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.config.schema_app import AppConfig
from src.presentation.api.assembly import create_app
from src.presentation.api.jobs import REPO_ROOT


def _timezone() -> ZoneInfo:
    """시계의 시간대는 **app.json이 정한다**(CLI `_clock`과 같은 이유 — 컨테이너 TZ는 UTC다).

    env 없이 읽는다: 비밀이 하나 비어 app.json이 로드되지 않아도 시계는 있어야 `/ready`가 그 사실을 말한다.
    읽을 수 없으면 기본값이고, 그때는 `/ready`의 config 칸이 이미 빨갛다.
    """
    try:
        raw = json.loads((REPO_ROOT / "config" / "app.json").read_text(encoding="utf-8"))
        return ZoneInfo(raw.get("timezone") or AppConfig().timezone)
    except (OSError, ValueError, AttributeError, ZoneInfoNotFoundError):
        return ZoneInfo(AppConfig().timezone)


def _boundary():
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
    env = dict(os.environ)
    zone = _timezone()
    return create_app(root=REPO_ROOT, env=env, clock=lambda: datetime.now(zone),
                      build={"commit": env.get("BUILD_COMMIT", ""), "built_at": env.get("BUILD_TIME", "")})


app = _boundary()
