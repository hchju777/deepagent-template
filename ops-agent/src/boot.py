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

from src.config.loader import ConfigError, load_app_config, load_registry, load_site_config
from src.domain.base import StrictModel


class BootError(StrictModel):
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.where}] {self.message}"


def validate_boot(config_root: Path, *, env: dict[str, str]) -> list[BootError]:
    """config 트리 전체를 검증한다. 문제가 없으면 빈 리스트."""
    return _check(config_root, "app.json", lambda: load_app_config(config_root, env=env)) \
        + _check_sites(config_root, env=env)


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
