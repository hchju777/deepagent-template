"""config 계층을 읽어 병합하고, env를 해석하고, 강타입으로 검증한다.

순서: **파일 읽기 → deep-merge(출처 추적) → ${ENV} 해석 → pydantic 검증**

이 순서가 중요하다. 병합을 먼저 해야 아래 층이 위 층의 `${...}`를 덮을 수 있고,
검증을 마지막에 해야 "합쳐진 최종 모습"이 검증된다 — 층마다 검증하면 각 층이
불완전해서(url만 있고 database가 없는 등) 전부 실패한다.

여기는 무raise 규율의 예외 지대다. **기동 시점의 설정 오류는 시끄럽게 죽는 것이
맞다** — 조용히 기본값으로 돌면 밤에 틀린다. 다만 죽는 방식은 `boot.py`가 정한다:
하나씩 던지는 게 아니라 전부 모아서 한 번에.
"""
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.config.envresolve import resolve_env
from src.config.merge import deep_merge
from src.config.schema_app import AppConfig
from src.config.schema_site import Registry, SiteConfig

# 아래로 갈수록 이긴다. 사내 config 배치 규약이 곧 이 순서다.
SITE_LAYERS = (
    "gbm/common.json",        # 전 사업부·전 법인 공통
    "gbm/{gbm}.json",         # 사업부 공통 — redis 키 규칙, REST 등재 항목 등
    "fct/{fct}/common.json",  # 법인 공통 — 그 공장의 전 사업부
    "fct/{fct}/{gbm}.json",   # 법인 × 사업부 — 실제 접속 url 등
)


class ConfigError(Exception):
    """config가 없거나, JSON이 깨졌거나, 스키마에 안 맞거나, env가 비었다."""


def _read_json(path: Path, *, required: bool = False) -> dict:
    if not path.exists():
        if required:
            raise ConfigError(f"config 파일이 없다 — {path}")
        return {}          # 없는 층은 빈 층이다 — 넷 다 있을 필요는 없다
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON이 깨졌다 — {path}:{exc.lineno}:{exc.colno} {exc.msg}") from exc


def _resolve_or_fail(raw: Any, *, env: dict[str, str], where: str) -> Any:
    resolved, missing = resolve_env(raw, env)
    if missing:
        raise ConfigError(f"{where}가 참조하는 env가 비어 있다 — {', '.join(missing)}. "
                          f".env에 값을 넣어라(빈 값도 없는 것으로 친다)")
    return resolved


def _build(model, data: Any, *, where: str):
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        lines = [f"  {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise ConfigError(f"{where} 스키마 오류:\n" + "\n".join(lines)) from exc


# env에 기본값을 두지 않는다 — 기본값이 있으면 누군가 안 넘기고, 그 경로만
# 치환이 안 된 채로 돈다(envresolve.py의 "빠뜨리기 쉬운 함정" 참고).

def load_app_config(config_root: Path, *, env: dict[str, str]) -> AppConfig:
    path = config_root / "app.json"
    raw = _read_json(path, required=True)
    return _build(AppConfig, _resolve_or_fail(raw, env=env, where="app.json"), where="app.json")


def load_registry(config_root: Path) -> Registry:
    """어느 (gbm, fct) 조합이 실재하는가. 디렉터리에서 유추하지 않는다."""
    path = config_root / "registry.json"
    return _build(Registry, _read_json(path, required=True), where="registry.json")


def load_site_config(config_root: Path, gbm: str, fct: str,
                     *, env: dict[str, str]) -> tuple[SiteConfig, dict[str, str]]:
    """네 층을 병합해 사이트 하나의 설정을 만든다. (설정, 출처)를 돌려준다."""
    where = f"{gbm}/{fct}"
    merged: dict = {}
    provenance: dict[str, str] = {}
    found_any = False

    for template in SITE_LAYERS:
        relative = template.format(gbm=gbm, fct=fct)
        layer = _read_json(config_root / relative)
        if not layer:
            continue
        found_any = True
        if "site" in layer:
            # 사이트 정체성은 registry와 경로가 정한다. 파일이 또 말하면
            # 둘이 어긋났을 때 어느 쪽이 맞는지 아무도 모른다.
            raise ConfigError(f"{relative}에 site가 선언돼 있다 — "
                              f"사이트 정체성은 registry.json과 파일 경로가 정한다")
        merged = deep_merge(merged, layer, source=relative.removesuffix(".json"),
                            provenance=provenance)

    if not found_any:
        raise ConfigError(f"{where}: 층 파일이 하나도 없다 — "
                          f"{', '.join(t.format(gbm=gbm, fct=fct) for t in SITE_LAYERS)}")

    merged["site"] = {"gbm": gbm, "fct": fct}
    resolved = _resolve_or_fail(merged, env=env, where=where)
    return _build(SiteConfig, resolved, where=where), provenance
