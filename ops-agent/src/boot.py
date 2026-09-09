"""기동 검증 — 문제를 **전부 모아서** 돌려준다.

## 왜 첫 문제에서 죽지 않는가

config에 오류가 3개 있을 때 하나씩 던지면 사람은 이 짓을 3번 한다:
고치고 → 돌리고 → 다음 오류 보고 → 고치고 → ... 사내 서버에 배포해 놓고
이걸 반복하면 30분이 사라진다.

전부 모아서 한 번에 보여 주면 한 번에 고친다. 그래서 이 함수는 **절대
raise하지 않고** `list[BootError]`를 돌려준다 — 하나가 실패해도 다음 검증을
계속한다.

## "밤에 조용히 틀리느니 배포 시점에 시끄럽게 죽는다"

설정이 잘못됐는데 기본값으로 돌면, 그 시스템은 몇 주 뒤에 "왜 알람이 안 왔지"로
발견된다. 그때는 무엇이 잘못됐는지 아무도 모른다.
"""
from pathlib import Path

from src.config.loader import ConfigError, load_app_config, load_site_config, site_files
from src.domain.base import StrictModel


class BootError(StrictModel):
    where: str          # 어느 파일/영역인가
    message: str

    def __str__(self) -> str:
        return f"[{self.where}] {self.message}"


def validate_boot(config_root: Path, *, env: dict[str, str]) -> list[BootError]:
    """config 트리 전체를 검증한다. 문제가 없으면 빈 리스트."""
    errors: list[BootError] = []

    errors += _check_app(config_root, env=env)
    errors += _check_sites(config_root, env=env)
    return errors


def _check_app(config_root: Path, *, env: dict[str, str]) -> list[BootError]:
    path = config_root / "app.json"
    try:
        load_app_config(path, env=env)
    except ConfigError as exc:
        return [BootError(where="app.json", message=str(exc))]
    except Exception as exc:                                   # noqa: BLE001
        # 예상 밖 예외까지 마지막 방어선으로 잡는다 — 기동 검증이 스스로 죽으면
        # "검증을 통과했는지 실패했는지"조차 알 수 없다.
        return [BootError(where="app.json", message=f"예상 밖 오류 — {type(exc).__name__}: {exc}")]
    return []


def _check_sites(config_root: Path, *, env: dict[str, str]) -> list[BootError]:
    files = site_files(config_root)
    if not files:
        return [BootError(where="sites/",
                          message=f"사이트 config가 하나도 없다 — {config_root / 'sites'}/*.json")]

    errors: list[BootError] = []
    seen: dict[str, Path] = {}
    for path in files:
        where = f"sites/{path.name}"
        try:
            site = load_site_config(path, env=env)
        except ConfigError as exc:
            errors.append(BootError(where=where, message=str(exc)))
            continue                                   # 다음 사이트 검증을 계속한다
        except Exception as exc:                                   # noqa: BLE001
            errors.append(BootError(where=where,
                                    message=f"예상 밖 오류 — {type(exc).__name__}: {exc}"))
            continue

        key = str(site.site)
        if key in seen:
            # 같은 gbm/fct가 두 파일에 있으면 어느 쪽이 이기는지가 파일 이름 순서에
            # 달리게 된다 — 나중에 "설정을 바꿨는데 안 먹는다"로 나타난다.
            errors.append(BootError(
                where=where, message=f"사이트 {key}가 {seen[key].name}에도 있다 — 중복"))
        seen[key] = path

    return errors
