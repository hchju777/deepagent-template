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


def load_app_config(config_root: Path, *, env=None) -> AppConfig:
    """app.json을 읽어 검증한다.

    env가 주어지면 사이트 config와 같은 규약으로 ${ENV_KEY} 참조를 해석한다
    (store.mongo_url 등 — C1). env를 넘기지 않으면(기본 None) 해석을 건너뛴다 —
    호출부가 모두 env를 넘기도록 고쳤지만(boot.py·__main__.py·daemon.py),
    env 없이도 app.json을 그냥 읽고 싶은 자리(예: 직접 스키마만 확인)를 막지
    않기 위해서다.
    """
    data = _read_json(config_root / "app.json")
    if env is not None:
        data, missing = resolve_env_refs(data, env=env)
        problems = [f"app.json: env 키 부재 또는 빈 값 — {k}" for k in sorted(set(missing))]
        if problems:
            raise ConfigError(problems)
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


def _body_env_problems(merged: dict, where: str) -> list[str]:
    """점검의 `params.body`에 든 `${ENV}` 참조를 거부한다(치환 **전에** 본다).

    body는 증거에 평문으로 영속되고(§2-N4의 request.params), 보고서 §4에 렌더되며,
    서브에이전트의 get_evidence 도구를 통해 LLM 프롬프트에도 실린다. 비밀값이 이
    경로로 새면 SecretStr 마스킹이 아무 소용이 없다 — 인증은 `target.rest.auth`의
    몫이고 거기서는 값이 마스킹된다.

    치환 전에 보는 이유: 치환 뒤에는 토큰과 part code를 구별할 방법이 없다.
    """
    problems = []
    checks = (merged.get("patrol") or {}).get("checks") or {}
    if not isinstance(checks, dict):
        return problems
    for name, check in checks.items():
        if not isinstance(check, dict):
            continue
        body = ((check.get("params") or {}).get("body"))
        for key in _env_refs(body):
            problems.append(
                f"{where}: 점검 {name!r}의 params.body.{key}에 env 참조가 있다 — "
                f"body는 증거로 평문 영속되므로 비밀값은 target.rest.auth에 둔다")
        # resolve 스펙도 같은 이유로 막는다: filter 값이 대상 쿼리로 나가고
        # config show에 평문으로 찍힌다(SecretStr이 아니라 마스킹 대상이 아니다).
        for key in _env_refs(check.get("resolve")):
            problems.append(
                f"{where}: 점검 {name!r}의 resolve.{key}에 env 참조가 있다 — "
                f"해석기 스펙은 마스킹되지 않으므로 비밀값을 둘 수 없다")
    return problems


def _env_refs(node, path: str = "") -> list[str]:
    """중첩 구조를 훑어 `${...}`를 품은 값의 경로를 모은다."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            found += _env_refs(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += _env_refs(value, f"{path}[{i}]")
    elif isinstance(node, str) and "${" in node:
        found.append(path or "?")
    return found


def load_site_config(config_root: Path, gbm: str, fct: str, *, env):
    merged: dict = {}
    provenance: dict[str, str] = {}
    for template in _SITE_LAYERS:
        rel = template.format(gbm=gbm, fct=fct)
        layer = _read_json(config_root / rel)
        source = rel.removesuffix(".json")
        merged = deep_merge(merged, layer, source=source, provenance=provenance)

    problems = _body_env_problems(merged, f"{gbm}/{fct}")
    if problems:
        raise ConfigError(problems)

    resolved, missing = resolve_env_refs(merged, env=env)
    problems = [f"{gbm}/{fct}: env 키 부재 또는 빈 값 — {k}" for k in sorted(set(missing))]
    if problems:
        raise ConfigError(problems)
    try:
        return SiteConfig.model_validate(resolved), provenance
    except ValidationError as exc:
        raise ConfigError(_validation_problems(exc, f"{gbm}/{fct}")) from exc
