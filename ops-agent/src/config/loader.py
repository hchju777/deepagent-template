"""config 파일을 읽어 검증된 모델로 만든다.

여기는 무raise 규율(도메인/어댑터)의 예외 지대다. **기동 시점의 설정 오류는
시끄럽게 죽는 것이 맞다** — 조용히 기본값으로 돌면 밤에 틀린다. 다만 죽는
방식은 `boot.py`가 정한다: 하나씩 던지는 게 아니라 전부 모아서 한 번에.
"""
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.config.envresolve import resolve_env
from src.config.schema_app import AppConfig
from src.config.schema_site import SiteConfig


class ConfigError(Exception):
    """config 파일이 없거나, JSON이 깨졌거나, 스키마에 안 맞거나, env가 비었다."""


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise ConfigError(f"config 파일이 없다 — {path}")
    try:
        # encoding 명시는 Windows(cp949)에서만 필요해 보이지만 빠뜨리면 사내에서만 깨진다.
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON이 깨졌다 — {path}:{exc.lineno}:{exc.colno} {exc.msg}") from exc


def _resolve_or_fail(raw: Any, *, env: dict[str, str], path: Path) -> Any:
    resolved, missing = resolve_env(raw, env)
    if missing:
        raise ConfigError(
            f"{path}가 참조하는 env가 비어 있다 — {', '.join(missing)}. "
            f".env에 값을 넣어라(빈 값도 없는 것으로 친다)")
    return resolved


def _build(model, data: Any, *, path: Path):
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        lines = [f"  {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise ConfigError(f"{path} 스키마 오류:\n" + "\n".join(lines)) from exc


# env에 기본값을 두지 않는다 — 기본값이 있으면 누군가 안 넘기고, 그 경로만
# 치환이 안 된 채로 돈다(envresolve.py의 "빠뜨리기 쉬운 함정" 참고).

def load_app_config(path: Path, *, env: dict[str, str]) -> AppConfig:
    raw = _read_json(path)
    return _build(AppConfig, _resolve_or_fail(raw, env=env, path=path), path=path)


def load_site_config(path: Path, *, env: dict[str, str]) -> SiteConfig:
    raw = _read_json(path)
    return _build(SiteConfig, _resolve_or_fail(raw, env=env, path=path), path=path)


def site_files(config_root: Path) -> list[Path]:
    """config_root/sites/*.json — 사이트 하나가 파일 하나다."""
    sites = config_root / "sites"
    return sorted(sites.glob("*.json")) if sites.is_dir() else []
