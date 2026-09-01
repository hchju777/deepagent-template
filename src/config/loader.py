"""config 파일 배치 규약과 로드 파이프라인.

순서: 파일 읽기 → deep-merge(출처 추적) → ${ENV} 해석 → pydantic 강타입 검증.
문제는 첫 건에서 멈추지 않고 ConfigError.problems에 모은다 (기동 거부 철학).
"""
import json
from pathlib import Path

from pydantic import ValidationError

from src.config.envresolve import resolve_env_refs
from src.config.merge import deep_merge
from src.config.schema_app import AppConfig, StrictModel
from src.config.schema_site import SiteConfig


class ConfigError(Exception):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class SiteRef(StrictModel):
    gbm: str
    fct: str
    enabled: bool = True


class Registry(StrictModel):
    sites: list[SiteRef]


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError([f"{path.name}: JSON 파싱 실패 — {exc}"]) from exc


def _validation_problems(exc: ValidationError, where: str) -> list[str]:
    return [f"{where}: {'.'.join(str(x) for x in e['loc'])} — {e['msg']}"
            for e in exc.errors()]


def load_app_config(config_root: Path) -> AppConfig:
    data = _read_json(config_root / "app.json")
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_validation_problems(exc, "app.json")) from exc


def load_registry(config_root: Path) -> Registry:
    data = _read_json(config_root / "registry.json")
    try:
        return Registry.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_validation_problems(exc, "registry.json")) from exc


_SITE_LAYERS = ("gbm/{gbm}.json", "factories/{fct}/common.json", "factories/{fct}/{gbm}.json")


def load_site_config(config_root: Path, gbm: str, fct: str, *, env):
    merged: dict = {}
    provenance: dict[str, str] = {}
    for template in _SITE_LAYERS:
        rel = template.format(gbm=gbm, fct=fct)
        layer = _read_json(config_root / rel)
        source = rel.removesuffix(".json")
        merged = deep_merge(merged, layer, source=source, provenance=provenance)

    resolved, missing = resolve_env_refs(merged, env=env)
    problems = [f"{gbm}/{fct}: env 키 부재 또는 빈 값 — {k}" for k in sorted(set(missing))]
    if problems:
        raise ConfigError(problems)
    try:
        return SiteConfig.model_validate(resolved), provenance
    except ValidationError as exc:
        raise ConfigError(_validation_problems(exc, f"{gbm}/{fct}")) from exc
