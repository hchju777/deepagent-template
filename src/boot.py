"""기동 검증 — 스펙 §4.6.

하나라도 실패하면 기동 거부. 오류는 전부 모아 보고한다 —
밤에 조용히 틀리는 것보다 배포 시점에 시끄럽게 죽는 게 낫다.

번호는 [docs/config-reference.md](../docs/config-reference.md)의 "기동 검증 항목"
목록을 따른다 — 여기에 다시 적으면 항목이 늘 때마다 두 곳이 갈라진다(실제로 그랬다).

deployment hash 실재는 정적 — repo_root의 로컬 체크아웃만 본다.
live 접속이 필요한 둘(Mongo readonly 롤 · pinned 명세 드리프트)은
check_live=True일 때만 돈다("죽은 사이트가 기동을 막으면 역효과" 원칙은
opt-in으로 지켜지고, 켠 뒤에는 확인하지 못한 것도 막는다).
judge LLM 프로파일(계획 4b): enabled 사이트 어딘가에 judge="llm"/"rule+llm" 점검이
있으면 app config의 llm.profiles.judge가 비어 있으면 안 된다 — 판정을
LLM에 맡기는 점검이 있는데 그 LLM 프로파일이 빈 문자열이면 매 회차
"LLM 미주입" error로만 채워질 뿐이니 기동 시점에 막는다. app config
자체가 app.json 파싱에서 이미 실패했으면(app_config is None) 이 검사는 건너뛴다.
LLM_API_KEY(계획 4b): enabled 사이트가 하나라도 있고 llm.profiles(judge/subagent/lead)
중 하나라도 값이 있으면 env LLM_API_KEY가 있어야 한다 — assemble_sites가
사이트마다 lead/subagent LLM을 조건 없이 만들기 때문에, 키가 없으면 조립이
조용히 깨지거나 실LLM 호출 시점에야 뒤늦게 실패한다.
"""
import asyncio
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.config.loader import (ConfigError, load_app_config, load_registry,
                               load_scenarios, load_site_config)
from src.infrastructure.code_repo import CodeRepoError, CodeRepoReader
from src.infrastructure.query_rules import (entry_call_problems, entry_schema, filter_problems,
                                            mongo_role_problems)
from src.patrol.daemon import seeds_problems
from src.knowledge.deployment import load_deployment
from src.knowledge.target_api import (load_target_api, parse_spec,
                                      response_field_problems, spec_problems)
from src.knowledge.topology import load_topology, topology_problems
from src.patrol.probes import PROBES, mongo_find_problems, resolve_probe


@dataclass
class BootError:
    where: str
    problem: str


async def _fetch_conn_status(cfg) -> dict:
    """RealMongo를 만들어 connection_status()를 부르는 짧은 헬퍼.

    실구현 의존성(pymongo)은 여기서만 지연 import한다 — 스텁 전용 환경에서도
    정적 검증은 이 모듈 import만으로 돌아야 하므로.
    """
    from src.infrastructure.mongo_reader import RealMongo

    m = cfg.target.mongo
    mongo = RealMongo(
        m.url, username=m.username,
        password=m.password.get_secret_value() if m.password else None,
        db=m.db, guards=cfg.target.guards,
        semaphore=asyncio.Semaphore(cfg.target.guards.max_concurrent),
        clock=lambda: datetime.now(timezone.utc),
    )
    return await mongo.connection_status()


async def _fetch_live_spec(cfg, topo, *, seeds):
    """대상의 OpenAPI를 받아 온다. 어댑터를 통해서만 — 직접 HTTP를 치지 않는다.

    stub_seeds가 주어지면 스텁 어댑터가 조립된다(테스트가 "지금 대상의 명세"를
    심는 경로). 실운영에서는 adapters="real"이라 RealRest가 config의
    openapi_path로 GET한다.
    """
    from src.infrastructure.factory import build_adapters

    adapters = build_adapters(cfg, topo, clock=lambda: datetime.now(timezone.utc),
                              stub_seeds=seeds)
    if adapters.rest is None:
        return None
    return await adapters.rest.fetch_spec()


def _live_spec_body(result) -> tuple[dict | None, str | None]:
    """fetch_spec 결과에서 명세 본문을 꺼낸다 — (본문, 문제) 중 하나만 채워진다.

    이 가드가 없으면 404가 "빈 명세"로 파싱되어 **모든 등재 항목이 명세에 없다**로
    기동이 막힌다(실측: RealRest.fetch_spec은 get()과 같은 규칙으로 4xx/5xx도
    status="ok"로 돌려주고 body는 비JSON이면 None이다). 원인은 404인데 메시지는
    오타를 가리키는 형태 — 사람을 틀린 곳으로 보내는 것이 이 검사의 최악이다.
    """
    if result is None or result.status != "ok":
        return None, f"명세를 받을 수 없다 — {getattr(result, 'error', None) or '응답 없음'}"
    data = result.data if isinstance(result.data, dict) else {}
    code = data.get("status_code")
    if not (isinstance(code, int) and 200 <= code < 300):
        return None, f"명세를 받을 수 없다 — HTTP {code}"
    body = data.get("body")
    if not isinstance(body, dict):
        return None, ("명세를 받을 수 없다 — 응답이 JSON 객체가 아니다 "
                      f"(받은 타입: {type(body).__name__})")
    return body, None


def _drift_problems(entries: dict, pinned, live_raw) -> list[str]:
    """박제한 pin과 지금 받은 명세를 견준다.

    **차이 전체를 쏟지 않는다.** 대상 API에는 우리가 안 쓰는 끝점이 수백 개이고,
    그것들의 변화를 전부 보고하면 아무도 읽지 않는다 — 경보 피로는 경보가 없는
    것과 같다. 우리 등재 항목에 실제로 영향을 주는 것만 말하고, 영향이 없으면
    "pin을 갱신하라" 한 줄로 끝낸다.
    """
    live = parse_spec(live_raw)
    if live.problems:
        # 파싱이 포기한 것을 버리고 대조로 넘어가면 진짜 원인("최상위가 객체가
        # 아니다")이 사라지고 "등재 항목이 명세에 없다"만 남아, 사람이 멀쩡한
        # config를 뒤지게 된다.
        return [f"대상 명세를 읽을 수 없다 — {p}" for p in live.problems]
    if live.digest == pinned.digest:
        return []
    impact = spec_problems(entries, live)
    if impact:
        return [f"대상 명세가 pin과 다르고 등재 항목에 영향이 있다 — {p}" for p in impact]
    return ["대상 명세가 pin과 달라졌지만 등재 항목에는 영향이 없다 — "
            "knowledge/target_api/의 pin을 갱신하고 커밋하라"]


def validate_boot(config_root: Path, *, env, repo_root: Path,
                  check_live: bool = False, stub_seeds=None) -> list[BootError]:
    errors: list[BootError] = []

    # 시드가 향한 사이트가 실제로 스텁을 쓰는지 — 순찰 CLI와 **같은 판정 함수**를
    # 쓴다. `--live --stub-seeds`는 시드를 받는 네 번째 경로이고, 여기만 가드가
    # 없으면 real 사이트에서 시드가 조용히 무시된 채 실제 네트워크를 친다.
    adapters_by_site: dict[str, str] = {}
    # 시나리오 검증에 쓸 사이트별 (토폴로지 locator, 등재 항목) — 사이트 루프에서 채운다.
    # 집계 검증이 사이트마다 필요로 하는 사실 — locator 집합과 어댑터 유무.
    site_targets: dict[str, tuple[set, bool, bool]] = {}
    entry_specs: dict[str, dict] = {}

    app_config = None
    try:
        app_config = load_app_config(config_root, env=env)
    except ConfigError as exc:
        errors += [BootError("app", p) for p in exc.problems]

    if app_config is not None:
        # 스케줄러와 clock 해석기가 둘 다 쓴다 — 오타 하나면 매 점검이 죽는데
        # 기동은 통과하던 자리다.
        try:
            ZoneInfo(app_config.timezone)
        except Exception:                                          # noqa: BLE001
            errors.append(BootError("app", f"timezone {app_config.timezone!r}을 해석할 수 없다"))

    needs_judge_llm = False

    try:
        registry = load_registry(config_root)
    except ConfigError as exc:
        return errors + [BootError("registry", p) for p in exc.problems]

    for site in registry.sites:
        if not site.enabled:
            continue
        where = f"{site.gbm}/{site.fct}"
        try:
            cfg, _ = load_site_config(config_root, site.gbm, site.fct, env=env)
        except ConfigError as exc:
            errors += [BootError(where, p) for p in exc.problems]
            continue

        knowledge_root = repo_root / cfg.knowledge.root
        try:
            topo = load_topology(knowledge_root, site.gbm, site.fct)
        except Exception as exc:   # yaml 구문 오류·스키마 위반 — 사이트 단위로 모아 보고
            errors.append(BootError(where, f"토폴로지 로드 실패: {exc}"))
            continue

        adapters_by_site[f"{site.gbm}/{site.fct}"] = cfg.target.adapters
        errors += [BootError(where, p) for p in topology_problems(topo)]

        # pinned 명세와 대조한다. **명세는 우리 스키마를 검증할 뿐 넓히지 않는다**
        # (규율 9) — spec_problems가 그 방향을 지킨다. 명세가 없는 것은 오류가
        # 아니다(못 얻는 대상도 있다); 있는데 깨진 것이 오류다.
        known = topo.locators()
        entries = dict(cfg.target.rest.entries) if cfg.target.rest else {}
        site_targets[f"{site.gbm}/{site.fct}"] = (
            set(known), cfg.target.mongo is not None, cfg.target.redis is not None)
        entry_specs[f"{site.gbm}/{site.fct}"] = dict(entries)
        target_api, api_problems = load_target_api(knowledge_root, site.gbm, site.fct)
        errors += [BootError(where, p) for p in api_problems]
        if target_api is not None:
            errors += [BootError(where, p) for p in spec_problems(entries, target_api)]
            errors += [BootError(where, p) for p in
                       response_field_problems(cfg.patrol.checks, entries, target_api)]
        entry_schema_of: dict[str, tuple[dict, str]] = {}
        for name, check in cfg.patrol.checks.items():
            # rest:<이름>은 토폴로지가 아니라 등재 항목에서 해석된다 — 두 이름공간을
            # 섞어 보면 정상 설정이 거부당한다. 미등재 참조를 기동 거부로 올리는
            # 이유는 기동 거부 철학 그대로다: 오타나 삭제된 항목을 참조하면 매
            # 순찰이 error를 내고 끝나는데, 밤에 조용히 틀리는 것보다 배포 시점에
            # 시끄럽게 죽는 게 낫다.
            if check.target is not None:
                kind, _, rest = check.target.partition(":")
                if kind == "rest" and not rest.startswith("/"):
                    entry = entries.get(rest)
                    if entry is None:
                        errors.append(BootError(
                            where, f"점검 {name!r}의 target {check.target!r}이 "
                                   f"target.rest.entries에 등재돼 있지 않다"))
                    else:
                        # 어댑터와 같은 판정 함수를 쓴다 — query_rules가 "기동 검증도
                        # 같은 규칙을 공유한다"고 밝힌 존재 이유가 여기서 성립한다.
                        # body 오타가 매 순찰 error로만 드러나는 것은 미등재 참조를
                        # 기동 거부로 올린 것과 같은 상황이다.
                        body = check.params.get("body", {})
                        for problem in entry_call_problems(entry, body):
                            errors.append(BootError(where, f"점검 {name!r}: {problem}"))
                        entry_schema_of[name] = (entry_schema(entry), rest)
                elif check.target not in known:
                    errors.append(BootError(
                        where, f"점검 {name!r}의 target {check.target!r}이 토폴로지로 해석되지 않는다"))
            # mongo_find의 필터·정렬도 배포 시점에 본다 — 매 순찰이 error를 내고
            # 끝나는 것은 미등재 REST 항목을 기동 거부로 올린 것과 같은 상황이다.
            if resolve_probe(check) == "mongo_find":
                errors += [BootError(where, f"점검 {name!r}: {p}")
                           for p in mongo_find_problems(check.params, check.resolve)]

            unused = _resolve_unused_problem(check, f"점검 {name!r}")
            if unused is not None:
                errors.append(BootError(where, unused))
            schema, entry_name = entry_schema_of.get(name, (None, None))
            errors += [BootError(where, p) for p in _resolver_problems(
                check.resolve, label=f"점검 {name!r}", entry_name=entry_name,
                schema=schema, entries=entries,
                has_mongo=cfg.target.mongo is not None,
                has_redis=cfg.target.redis is not None)]
            if check.judge in ("llm", "rule+llm"):
                needs_judge_llm = True

        # 각 점검의 프로브가 레지스트리에서 해석되는가 (§4.6-9)
        for name, check in cfg.patrol.checks.items():
            probe_name = resolve_probe(check)
            if probe_name is None or probe_name not in PROBES:
                errors.append(BootError(
                    where, f"점검 {name!r}의 프로브를 해석할 수 없다"))

        repo_names = {r.name for r in cfg.target.code.repos} if cfg.target.code else set()
        for svc_name, svc in topo.services.items():
            if svc.code is not None and svc.code.repo not in repo_names:
                errors.append(BootError(
                    where, f"서비스 {svc_name!r}의 repo {svc.code.repo!r}가 config에 없다"))

        # deployment.yaml의 (repo, commit)이 실재하는가 (§4.6-7)
        deployment = load_deployment(knowledge_root, site.gbm, site.fct)
        if deployment is not None:
            reader = CodeRepoReader(
                {r.name: r.path for r in cfg.target.code.repos}
                if cfg.target.code else {})
            for svc_name, ver in deployment.services.items():
                try:
                    if not reader.hash_exists(ver.repo, ver.commit):
                        errors.append(BootError(
                            where, f"deployment: {svc_name}의 커밋 {ver.commit!r}이 "
                                   f"레포 {ver.repo!r}에 없다 (fetch 누락 또는 오타)"))
                except CodeRepoError as exc:
                    errors.append(BootError(where, f"deployment: {exc}"))

        # Mongo 계정이 readonly 롤인가 (§4.6-8) — live 접속이 필요해 opt-in
        if check_live and cfg.target.adapters == "real" and \
                cfg.target.mongo and cfg.target.mongo.username:
            try:
                status = asyncio.run(_fetch_conn_status(cfg))
                errors.extend(BootError(where, p) for p in mongo_role_problems(status))
            except Exception as exc:
                errors.append(BootError(where, f"Mongo 롤 확인 불가 — {exc}"))

        # pinned 명세와 지금 대상의 명세를 견준다 — live 접속이 필요해 opt-in.
        # pin이 없으면 견줄 대상이 없다(명세를 받아 오는 것 자체가 목적이 아니다).
        if check_live and target_api is not None:
            site_seeds = stub_seeds.get(f"{site.gbm}/{site.fct}") \
                if isinstance(stub_seeds, dict) else stub_seeds
            result, fetch_failed = None, False
            try:
                result = asyncio.run(_fetch_live_spec(cfg, topo, seeds=site_seeds))
            except Exception as exc:            # noqa: BLE001 — 무raise 규율
                errors.append(BootError(where, f"명세를 받을 수 없다 — {exc}"))
                fetch_failed = True
            if not fetch_failed:
                # 못 받은 것도 **기동을 막는다.** `--live`를 켠 사람은 "지금 실제와
                # 맞는가"를 묻고 있고, 못 물어본 것을 조용히 통과시키면 확인 안 한
                # 것이 "이상 없음"으로 둔갑한다(조용한 생략 금지). "죽은 사이트가
                # 기동을 막으면 역효과"라는 원칙은 --live를 opt-in으로 둔 것으로
                # 이미 지켜진다 — Mongo 롤 검사가 같은 형태다.
                body, problem = _live_spec_body(result)
                if problem is not None:
                    errors.append(BootError(where, problem))
                else:
                    errors += [BootError(where, p) for p in
                               _drift_problems(entries, target_api, body)]

    if app_config is not None:
        # `${ENV}`가 미설정이면 조용히 빈 토큰이 되고, 그 주체는 "Bearer "만 보내면
        # 누구나다 — 그 토큰은 없는 것보다 나쁘다.
        seen: dict[str, str] = {}
        for subject, token in app_config.access.subjects.items():
            value = token.get_secret_value()
            if not value:
                errors.append(BootError(
                    "app", f"access.subjects[{subject!r}]의 토큰이 비어 있다 — "
                           f".env의 참조 값을 확인하라"))
            elif value in seen:
                # 같은 토큰이 두 주체면 역조회가 마지막 주체를 골라 requested_by가
                # 오귀속된다 — 두 참조가 같은 env 키를 가리키는 오타가 전형이다.
                errors.append(BootError(
                    "app", f"access.subjects[{subject!r}]와 [{seen[value]!r}]의 토큰이 같다"))
            else:
                seen[value] = subject

    if app_config is not None and app_config.access.allow:
        # 오타난 사이트 키는 그 주체를 영원히 눈멀게 하는데 아무도 모른다.
        # 와일드카드(`mx/*`)는 사업부 실재만 본다 — fct는 아직 안 정해진 것이다.
        known_sites = {f"{s.gbm}/{s.fct}" for s in registry.sites}
        known_gbms = {s.gbm for s in registry.sites}
        for subject, entries in app_config.access.allow.items():
            for entry in entries:
                gbm, _, fct = entry.partition("/")
                ok = gbm in known_gbms if fct == "*" else entry in known_sites
                if not ok:
                    errors.append(BootError(
                        "app", f"access.allow[{subject!r}]의 {entry!r}가 registry에 없다"))

    if stub_seeds:
        errors += [BootError("stub-seeds", p) for p in
                   seeds_problems(stub_seeds, adapters_by_site)]

    # llm/rule+llm 판정 점검이 하나라도 있으면 judge LLM 프로파일 필수 (계획 4b)
    if app_config is not None and needs_judge_llm and not app_config.llm.profiles.judge:
        errors.append(BootError("app", "judge LLM 프로파일 필요 — llm/rule+llm 점검이 있다"))

    # enabled 사이트가 있고 llm.profiles 중 하나라도 쓰이면 LLM_API_KEY 필수 (계획 4b)
    has_enabled_site = any(site.enabled for site in registry.sites)
    if app_config is not None and has_enabled_site:
        profiles = app_config.llm.profiles
        profiles_used = bool(profiles.judge or profiles.subagent or profiles.lead)
        if profiles_used and not env.get("LLM_API_KEY"):
            errors.append(BootError("app", "LLM_API_KEY 필요 — llm 프로파일을 쓰는 활성 사이트가 있다"))

    errors += _scenario_errors(config_root, env, site_targets, entry_specs)
    return errors


def _resolver_problems(resolve, *, label, entry_name, schema, entries,
                       has_mongo, has_redis):
    """해석기 선언의 정적 문제 — **점검과 집계 지표가 같은 함수를 쓴다**.

    두 경로가 각자 베끼면 하나가 빠뜨린다. 실제로 그랬다: 지표 경로에는 GET 강제가
    없어서, 등재된 POST 항목을 가리키는 해석기가 기동을 통과하고 런타임에 실제로
    POST를 냈다(검증 리뷰). 집계는 사이트 수만큼 팬아웃하므로 그 한 줄이 여러 법인에
    동시에 나간다 — 규율 9가 메커니즘으로 막으려던 바로 그 형태다.

    `schema`가 None이면 표적이 등재 항목이 아니라는 뜻이라 키·모양 검사를 건너뛴다.
    나머지(항목 실재·GET 강제·어댑터 유무·mongo 필터)는 표적 종류와 무관하게 돈다 —
    해석기는 mongo_find 표적에서도 실행되기 때문이다.
    """
    problems = []
    for key, spec in resolve.items():
        if schema is not None:
            declared = schema.get(key)
            if declared is None:
                problems.append(f"{label}의 resolve 키 {key!r}가 "
                                f"항목 {entry_name!r}의 스키마에 없다")
            elif spec.from_ != "unfiltered":
                # 해석기가 내는 모양은 종류가 정한다: clock은 항상 문자열 하나,
                # 소스 해석기는 항상 리스트. 스키마와 어긋나면 매 실행이 "list[str]여야
                # 한다"로 끝나는데, 그건 정적으로 알 수 있는 것을 런타임에 미룬 것이다.
                is_list = declared.startswith("list[")
                wants_list = spec.from_ != "clock"
                if is_list != wants_list:
                    shape = "리스트" if wants_list else "문자열 하나"
                    problems.append(f"{label}의 해석기 {key!r}는 {spec.from_}라 "
                                    f"{shape}를 내는데 항목 {entry_name!r}의 스키마는 "
                                    f"{declared!r}이다")
        if spec.from_ == "rest":
            source = entries.get(spec.entry)
            if source is None:
                problems.append(f"{label}의 해석기 {key!r}가 가리키는 "
                                f"항목 {spec.entry!r}이 등재돼 있지 않다")
            elif source.method != "GET":
                # 값을 얻으려고 부수효과 가능성이 있는 메서드를 쓰지 않는다.
                problems.append(f"{label}의 해석기 {key!r}가 가리키는 "
                                f"항목 {spec.entry!r}은 GET이어야 한다")
        if spec.from_ in ("mongo", "redis") and not {"mongo": has_mongo,
                                                     "redis": has_redis}[spec.from_]:
            problems.append(f"{label}의 해석기 {key!r}가 {spec.from_}를 쓰는데 "
                            f"target.{spec.from_}가 설정돼 있지 않다")
        if spec.from_ == "mongo":
            problems += [f"{label}의 해석기 {key!r} filter: {p}"
                         for p in filter_problems(spec.filter)]
    return problems


def _resolve_unused_problem(spec, label):
    """resolve를 실제로 실행하지 않는 표적에 해석기를 달았는가.

    런타임이 조용히 무시해, 사람이 "범위를 좁혔다"고 믿는 것이 무필터 전체 조회를
    돈다 — 사람이 쓴 제약이 아무 효과 없이 통과하는 형태다.

    target **모양**으로 판정한다 — resolve_probe는 선언된 probe를 그대로 돌려주므로
    probe만 박으면 이 검사가 통째로 비껴간다(등재 항목 이름 위장과 같은 계열의 우회).
    """
    kind, _, rest = (spec.target or "").partition(":")
    is_entry_target = kind == "rest" and rest and not rest.startswith("/")
    if is_entry_target or resolve_probe(spec) == "mongo_find" or not spec.resolve:
        return None
    return (f"{label}에 resolve가 있는데 target {spec.target!r}은 "
            f"등재 항목이 아니다 — resolve는 등재 항목 호출에서만 쓰인다")


def _scenario_errors(config_root: Path, env, site_targets: dict,
                     entry_specs: dict) -> list[BootError]:
    """시나리오 검증(계획 16) — 문제를 전부 모아서 돌려준다(기동 거부 철학).

    집계는 사이트를 가로지르므로 scope의 사이트가 registry에 실재하는지, 각 지표의
    target이 **scope의 모든 사이트에서** 해석되는지를 본다. 오타 하나가 매 집계에서
    "N개 사이트 미확인"으로만 드러나면 커버리지 블록이 진짜 장애와 설정 실수를
    구별할 수 없게 된다.
    """
    try:
        scenarios = load_scenarios(config_root, env=env)
    except ConfigError as exc:
        return [BootError("scenarios", p) for p in exc.problems]
    errors: list[BootError] = []
    for name, scenario in scenarios.items():
        where = f"scenarios/{name}"
        wanted = (list(site_targets) if scenario.scope.sites == "all" else scenario.scope.sites)
        in_scope = [key for key in wanted if key not in set(scenario.scope.exclude)]
        for key in in_scope:
            if key not in site_targets:
                errors.append(BootError(where, f"scope의 사이트 {key!r}가 registry에 없다"))
        for metric, spec in scenario.metrics.items():
            label = f"지표 {metric!r}"
            # 지표도 프로브에 그대로 실린다(`fleet/collect.py`) — 점검과 같은 검증을
            # 받지 않으면 오타가 "매 집계 missing"으로만 드러난다(검증 리뷰 MG-1).
            if resolve_probe(spec) == "mongo_find":
                errors += [BootError(where, f"{label}: {p}")
                           for p in mongo_find_problems(spec.params, spec.resolve)]
            unused = _resolve_unused_problem(spec, label)
            if unused is not None:
                errors.append(BootError(where, unused))
            if spec.probe is not None and spec.probe not in PROBES:
                errors.append(BootError(where, f"지표 {metric!r}의 probe {spec.probe!r}가 "
                                               f"프로브 레지스트리에 없다"))
            if spec.target is None and spec.probe is None:
                errors.append(BootError(where, f"지표 {metric!r}에 target도 probe도 없다 — "
                                               f"매 집계가 '프로브 해석 불가'로 끝난다"))
            if spec.target is None:
                continue
            kind, _, rest = spec.target.partition(":")
            for key in in_scope:
                targets = site_targets.get(key)
                if targets is None:
                    continue
                known, has_mongo, has_redis = targets
                site_entries = entry_specs.get(key, {})
                schema = entry_name = None
                if kind == "rest" and rest and not rest.startswith("/"):
                    entry = site_entries.get(rest)
                    if entry is None:
                        errors.append(BootError(
                            where, f"{label}의 target {spec.target!r}이 "
                                   f"{key}의 target.rest.entries에 등재돼 있지 않다"))
                    else:
                        # 점검과 **같은 판정 함수**를 쓴다 — body 오타가 매 집계 error로만
                        # 드러나는 것은 미등재 참조를 기동 거부로 올린 것과 같은 상황이다.
                        body = spec.params.get("body", {}) if isinstance(spec.params, dict) else {}
                        for problem in entry_call_problems(entry, body):
                            errors.append(BootError(where, f"{label}: {problem}"))
                        schema, entry_name = entry_schema(entry), rest
                elif spec.target not in known:
                    errors.append(BootError(
                        where, f"{label}의 target {spec.target!r}이 "
                               f"{key}의 토폴로지로 해석되지 않는다"))
                # 해석기 검증은 표적 종류와 무관하게 돈다 — mongo_find 표적도 해석기를
                # 실제로 실행한다. 점검과 **같은 함수**를 쓰는 것이 요점이다.
                errors += [BootError(where, p) for p in _resolver_problems(
                    spec.resolve, label=label, entry_name=entry_name, schema=schema,
                    entries=site_entries, has_mongo=has_mongo, has_redis=has_redis)]
    return errors
