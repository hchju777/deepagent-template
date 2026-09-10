"""기동 검증 — 문제를 **전부 모아서** 돌려준다.

## 왜 첫 문제에서 죽지 않는가

config에 오류가 3개 있을 때 하나씩 던지면 사람은 이 짓을 3번 한다:
고치고 → 돌리고 → 다음 오류 보고 → 고치고. 사내 서버에 배포해 놓고 이걸
반복하면 30분이 사라진다. 전부 모아서 보여 주면 한 번에 고친다.

그래서 이 함수는 **절대 raise하지 않고** `list[BootError]`를 돌려준다 —
하나가 실패해도 다음 검증을 계속한다.

## "밤에 조용히 틀리느니 배포 시점에 시끄럽게 죽는다"

설정이 잘못됐는데 기본값으로 돌면, 그 시스템은 몇 주 뒤에 "왜 알람이 안 왔지"로
발견된다. 그때는 무엇이 잘못됐는지 아무도 모른다.
"""
from pathlib import Path

from src.config.loader import (ConfigError, load_app_config, load_registry,
                               load_scenarios, load_site_config)
from src.domain.base import StrictModel


class BootError(StrictModel):
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.where}] {self.message}"


def validate_boot(config_root: Path, *, env: dict[str, str]) -> list[BootError]:
    """config 트리 전체를 검증한다. 문제가 없으면 빈 리스트."""
    return _check(config_root, "app.json", lambda: load_app_config(config_root, env=env)) \
        + _check_llm(config_root, env=env) \
        + _check_sites(config_root, env=env) \
        + _check_scenarios(config_root)


def _check_llm(config_root: Path, *, env: dict[str, str]) -> list[BootError]:
    """LLM·메일의 CA 번들 경로 오타를 런타임까지 미루지 않는다.

    미루면 밤에 첫 조사가 TLS로 죽고, 메일 쪽은 **보고서가 다 나온 뒤**에 죽어
    "조사는 됐는데 아무도 못 봤다"가 된다.
    """
    from src.infrastructure.tls import tls_problems

    try:
        app = load_app_config(config_root, env=env)
    except Exception:                                              # noqa: BLE001
        return []          # app.json 자체의 문제는 위에서 이미 보고됐다
    problems = [BootError(where="app.json mail.tls", message=problem)
                for problem in tls_problems(app.mail.tls)] if app.mail.enabled else []
    if app.llm is None:
        return problems
    return problems + [BootError(where="app.json llm.tls", message=problem)
                       for problem in tls_problems(app.llm.tls)]


def _check(config_root: Path, where: str, action) -> list[BootError]:
    try:
        action()
    except ConfigError as exc:
        return [BootError(where=where, message=str(exc))]
    except Exception as exc:                                       # noqa: BLE001
        # 예상 밖 예외까지 마지막 방어선으로 잡는다 — 기동 검증이 스스로 죽으면
        # "검증을 통과했는지 실패했는지"조차 알 수 없다.
        return [BootError(where=where, message=f"예상 밖 오류 — {type(exc).__name__}: {exc}")]
    return []


def _check_sites(config_root: Path, *, env: dict[str, str]) -> list[BootError]:
    try:
        registry = load_registry(config_root)
    except ConfigError as exc:
        return [BootError(where="registry.json", message=str(exc))]
    except Exception as exc:                                       # noqa: BLE001
        return [BootError(where="registry.json",
                          message=f"예상 밖 오류 — {type(exc).__name__}: {exc}")]

    errors: list[BootError] = []
    if not registry.sites:
        errors.append(BootError(where="registry.json", message="사이트가 하나도 없다"))

    seen: set[str] = set()
    for entry in registry.sites:
        key = str(entry)
        if key in seen:
            # 같은 조합이 두 번 있으면 어느 쪽 enabled가 이기는지 순서에 달린다.
            errors.append(BootError(where="registry.json", message=f"중복된 사이트 — {key}"))
        seen.add(key)

    for entry in registry.active():
        errors += _check(config_root, str(entry),
                         lambda e=entry: load_site_config(config_root, e.gbm, e.fct, env=env))
    return errors


def _check_scenarios(config_root: Path) -> list[BootError]:
    """리포트 시나리오 — 날짜 형식과 대상 사이트를 기동에서 본다.

    날짜 형식을 여기서 보는 이유는 `window.py` 첫머리에 적혀 있다: 필드가
    문자열이라 형식이 틀리면 범위 쿼리가 **조용히 다른 구간**을 읽고, 리포트는
    오류 없이 틀린 숫자를 낸다. 새벽에 메일이 나간 뒤에 발견될 종류다.

    registry에 **없는** 사이트는 오류다(오타는 런타임에 고칠 수 없다). 반면
    registry에 있는데 `enabled: false`인 사이트는 오류로 보지 않는다 — 그건
    "지금 못 붙는다"는 정상 상태이고, 리포트는 그런 법인을 본문 하단의 제외
    목록에 이름으로 남긴다. 분모에서 조용히 사라지는 것이 아니라면 괜찮다.
    """
    try:
        scenarios = load_scenarios(config_root)
    except ConfigError as exc:
        return [BootError(where="scenarios", message=str(exc))]
    except Exception as exc:                                       # noqa: BLE001
        return [BootError(where="scenarios",
                          message=f"예상 밖 오류 — {type(exc).__name__}: {exc}")]
    if not scenarios:
        return []

    from src.report.window import date_format_problem

    try:
        known = {str(entry) for entry in load_registry(config_root).sites}
    except Exception:                                              # noqa: BLE001
        known = None       # registry 자체의 문제는 위에서 이미 보고됐다

    errors: list[BootError] = []
    for name, scenario in scenarios.items():
        where = f"scenarios/{name}.json"
        problem = date_format_problem(scenario.source.date_format)
        if problem:
            errors.append(BootError(where=f"{where} source.date_format", message=problem))
        if known is None:
            continue
        for site in scenario.scope.sites:
            if site not in known:
                errors.append(BootError(
                    where=f"{where} scope.sites",
                    message=f"registry.json에 없는 사이트 — {site}. "
                            f"등록된 것: {', '.join(sorted(known)) or '(없음)'}"))
    return errors
