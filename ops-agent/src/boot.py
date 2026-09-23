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

from src.config.loader import (ConfigError, load_app_config, load_prompt,
                               load_registry, load_scenarios, load_site_config)
from src.domain.base import StrictModel


class BootError(StrictModel):
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.where}] {self.message}"


def validate_boot(config_root: Path, *, env: dict[str, str],
                  knowledge_root: Path | None = None) -> list[BootError]:
    """config 트리 전체를 검증한다. 문제가 없으면 빈 리스트."""
    return _check(config_root, "app.json", lambda: load_app_config(config_root, env=env)) \
        + _check_llm(config_root, env=env) \
        + _check_sites(config_root, env=env) \
        + _check_scenarios(config_root) \
        + _check_code(config_root,
                      # **config 트리 옆을 본다.** 고정 경로를 쓰면 테스트가 tmp에
                      # 세운 트리를 검증하면서 리포의 진짜 knowledge를 읽는다 —
                      # 실제로 그랬고, 관계없는 검증이 8개 깨졌다.
                      knowledge_root or config_root.parent / "knowledge", env=env)


def _repos_declared(config_root: Path, registry, gbm: str, *,
                    env: dict[str, str]) -> bool:
    site = next((e for e in registry.active() if e.gbm == gbm), None)
    if site is None:
        return False
    try:
        config, _ = load_site_config(config_root, site.gbm, site.fct, env=env)
    except Exception:                                              # noqa: BLE001
        return False
    return bool(config.code.repos)


def _check_code(config_root: Path, knowledge_root: Path, *,
                env: dict[str, str]) -> list[BootError]:
    """지식 층과 config가 **서로를 가리키는가.**

    디스크 상태(체크아웃이 실제로 있는가)는 **여기서 안 본다** — 그건 `code status`의
    일이고, 개발 환경에는 체크아웃이 없는 것이 정상이다. 기동이 거기서 막히면
    사람이 기동 검증을 통째로 끄게 된다.

    여기서 보는 것은 **선언끼리의 어긋남**이다. 토폴로지가 없는 레포를 가리키거나
    배포가 없는 서비스를 가리키면, 증상은 런타임에 "코드 증거가 조용히 안 나온다"로
    나타난다 — 조용한 실패라 제일 비싸다.
    """
    from src.knowledge.loader import load_deployment, load_topology

    try:
        registry = load_registry(config_root)
    except Exception:                                              # noqa: BLE001
        return []          # 위의 _check_sites가 이미 보고했다

    errors: list[BootError] = []
    for gbm in sorted({e.gbm for e in registry.active()}):
        where = f"knowledge/{gbm}"
        try:
            topology = load_topology(knowledge_root, gbm)
            deployment = load_deployment(knowledge_root, gbm)
        except ConfigError as exc:
            if not _repos_declared(config_root, registry, gbm, env=env):
                continue       # 11a를 아직 안 쓰는 트리다 — 조용히 넘어간다
            errors.append(BootError(where=where, message=(
                f"{exc}. config에 code.repos를 선언해 놓고 토폴로지가 없으면 "
                f"**코드를 한 줄도 못 읽는다**")))
            continue
        except Exception as exc:                                   # noqa: BLE001
            errors.append(BootError(where=where,
                                    message=f"예상 밖 오류 — {type(exc).__name__}: {exc}"))
            continue

        site = next(e for e in registry.active() if e.gbm == gbm)
        try:
            config, _ = load_site_config(config_root, site.gbm, site.fct, env=env)
        except Exception:                                          # noqa: BLE001
            continue           # 위에서 이미 보고했다
        known = {r.name for r in config.code.repos}
        if not topology.services and not known:
            continue           # 11a를 아직 안 쓰는 트리다

        for name, service in sorted(topology.services.items()):
            if service.repo not in known:
                errors.append(BootError(where=f"{where} topology", message=(
                    f"서비스 {name}이 없는 레포를 가리킨다 — {service.repo}. "
                    f"config의 code.repos에 있는 것: {', '.join(sorted(known)) or '없음'}")))
        for name in sorted(set(deployment.pins) |
                           {s for site_pins in deployment.sites.values() for s in site_pins}):
            if name not in topology.services:
                errors.append(BootError(where=f"{where} deployment", message=(
                    f"배포가 토폴로지에 없는 서비스를 가리킨다 — {name}. "
                    f"이름이 바뀌었으면 양쪽을 같이 고쳐라")))
    return errors


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
        where = str(entry)
        problems = _check(config_root, where,
                          lambda e=entry: load_site_config(config_root, e.gbm, e.fct, env=env))
        errors += problems
        if problems:
            continue          # 로드가 실패했으면 점검을 볼 수 없다
        site, _ = load_site_config(config_root, entry.gbm, entry.fct, env=env)
        errors += _check_patrol(site, where=where)
    return errors


def _check_patrol(site, *, where: str) -> list[BootError]:
    """점검이 가리키는 REST 등재 항목이 실재하고 params가 그 스키마를 통과하는가.

    **오타는 런타임에 고칠 수 없다.** `summary_badge`를 `summary_bagde`라고 적으면
    매 순찰마다 실패하는데, 그 실패는 `unreachable`로 흡수되어 "대상이 안 붙는다"처럼
    보인다. 진짜 장애와 구별이 안 되고, 28사이트에서는 그런 줄 하나가 묻힌다.

    `action` 자체의 등재 검사는 `ProbeSpec`이 로드 시점에 이미 한다. 여기서 보는 것은
    **그다음 층** — rest 등재 항목과 그 닫힌 스키마다.
    """
    from src.infrastructure.rest_prober import param_problems

    errors: list[BootError] = []
    entries = site.infra.rest.entries if site.infra.rest else {}
    for name, check in site.patrol.checks.items():
        for probe_name, spec in check.probes.items():
            if spec.action != "rest.query":
                continue
            at = f"{where} patrol.checks.{name}.probes.{probe_name}"
            entry_name = spec.params.get("entry")
            entry = entries.get(entry_name)
            if entry is None:
                errors.append(BootError(where=at, message=(
                    f"등재되지 않은 REST 항목 — {entry_name!r}. "
                    f"등재된 것: {', '.join(sorted(entries)) or '(없음)'}")))
                continue
            for problem in param_problems(entry, spec.params.get("params") or {}):
                errors.append(BootError(where=at, message=problem))
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
        for index, fmt in enumerate(scenario.source.formats()):
            problem = date_format_problem(fmt)
            if problem:
                field = "source.date_format" if index == 0 else "source.parse_formats"
                errors.append(BootError(where=f"{where} {field}", message=problem))
        if scenario.comment.enabled:
            # 프롬프트가 깨진 것은 밤에 "코멘트가 비어 있다"로만 드러난다.
            errors += _check(config_root, f"{where} comment",
                             lambda sc=scenario: load_prompt(config_root, sc))
        if known is None:
            continue
        for site in scenario.scope.sites:
            if site not in known:
                errors.append(BootError(
                    where=f"{where} scope.sites",
                    message=f"registry.json에 없는 사이트 — {site}. "
                            f"등록된 것: {', '.join(sorted(known)) or '(없음)'}"))
    return errors
