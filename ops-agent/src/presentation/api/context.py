"""경로들이 나눠 쓰는 것 — 리포 루트·env·시계·작업 실행기와, 바깥에 붙는 자리의 이음매."""
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.config.loader import load_app_config
from src.config.schema_app import AppConfig
from src.domain.base import Clock
from src.presentation.api.jobs import JobRunner


@dataclass
class ApiContext:
    root: Path
    env: dict[str, str]
    clock: Clock
    runner: JobRunner
    build: dict[str, str]
    started_at: datetime
    # 아래는 바깥(대상 DB·라우터·게이트웨이)에 붙는 자리 — 테스트가 가짜를 넣는다. None이면 진짜.
    site_probes: Any = None
    trace_probe: Callable | None = None
    llm_transport: Any = None
    # 스케줄러 태스크가 살아 있는가(2단계). None이면 아직 없다.
    scheduler_alive: Callable[[], bool] | None = None

    @property
    def config_root(self) -> Path:
        return self.root / "config"

    def app_config(self) -> AppConfig:
        """던진다(`ConfigError`) — 부르는 경로가 값으로 바꾼다."""
        return load_app_config(self.config_root, env=self.env)

    def output_dir(self) -> Path:
        """env 없이 읽는다 — 비밀이 하나 비었다고 출력 목록까지 못 보면, 무엇이 나갔는지 확인할 길이 같이 막힌다."""
        try:
            raw = json.loads((self.config_root / "app.json").read_text(encoding="utf-8"))
            relative = raw.get("output_dir") or AppConfig().output_dir
        except (OSError, ValueError, AttributeError):
            relative = AppConfig().output_dir
        return (self.root / relative).resolve()
