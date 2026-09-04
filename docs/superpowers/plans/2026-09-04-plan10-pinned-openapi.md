# 계획 10 — pinned OpenAPI 대조 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 대상 API의 OpenAPI 명세를 knowledge에 박제해 두고, 우리가 손으로 쓴 등재 항목을 그것과 **대조**한다 — 대체하지 않는다.

**Architecture:** 계획 8이 "어떤 끝점을 어떤 메서드로 부를 수 있는가"를 config로 닫았고, 계획 9가 "어떤 값을 보낼 것인가"를 실행 시점 해석으로 옮겼다. 남은 구멍은 **우리가 쓴 것이 실제 API와 맞는지 아무도 확인하지 않는다**는 것이다. 오타 하나, 대상 팀의 필드 이름 변경 하나가 매 순찰 error로만 드러나고, 심하면 "필드가 없다"는 finding으로 둔갑해 존재하지 않는 장애를 조사한다.

pinned OpenAPI가 그것을 배포 시점으로 당긴다. **핵심은 방향이다: 명세는 우리 스키마를 검증할 뿐, 넓히지 않는다.**

**Tech Stack:** Python 3.12 · pydantic 2 · httpx · pytest(`asyncio_mode=auto`)

**스펙:** [2026-09-04-v2-service-direction.md](../specs/2026-09-04-v2-service-direction.md) §2-N1(읽기 전용 강제 — "문서는 증거, config는 권한") · §4.1(as_of 4겹) · P4.

**선행:** 계획 9 머지(`76e9832`, 417 tests).
**후속:** P5(concern 축 + rule 확장)가 여기서 만든 응답 스키마 지식을 쓴다. P7(Fleet 집계)이 `target_api` digest를 집계 레코드의 as_of에 싣는다.

---

## Global Constraints

- **명세는 증거, config는 권한.** 이 계획이 만드는 어떤 코드도 **명세를 근거로 허용 범위를 넓혀서는 안 된다.** 대상이 새 POST를 배포하거나 body 필드를 추가했을 때 우리 쪽이 자동으로 그것을 받아들이면 fail-open이고, 계획 8이 세운 등재제 전체가 무의미해진다. 명세가 할 수 있는 일은 **"당신이 쓴 것이 틀렸다"고 말하는 것**뿐이다.
- **런타임에 명세를 읽어 등재 목록을 넓히지 않는다**(CLAUDE.md 규율 9). 명세는 knowledge에 박제된 파일이고, 갱신은 사람이 커밋한다.
- **무raise**: 로더·파서·대조 판정·어댑터는 예외를 던지지 않는다. 파싱 실패는 `BootError`/`ProbeResult(status="error")`로 흡수한다. 남의 JSON을 파싱하므로 이 규율이 특히 중요하다 — 대상이 이상한 명세를 배포했다고 우리 데몬이 죽으면 안 된다.
- **`StrictModel`(규율 5)은 우리 모델에만.** 대상의 OpenAPI 원문은 pydantic으로 파싱하지 **않는다** — `x-*` 확장 키 하나에 `extra="forbid"`가 죽는다. 원문은 평범한 `dict` 접근으로 필요한 부분집합만 뽑아내고, 그 결과를 담는 **우리 모델은 `StrictModel`을 상속한다.** (스펙 §7이 "이 리포에서 규율 5를 적용하지 않아야 하는 유일한 예외"라고 적었지만, 부분집합 추출로 예외 없이 해결된다 — 스펙보다 이쪽이 낫다.)
- **시계 주입**: `src/__main__.py` 밖에서 `datetime.now()` 금지.
- **레이어 방향**: `src/knowledge/`는 `src/config/`를 import하지 않는다(CLAUDE.md가 기록한 config→infrastructure→knowledge→config 순환을 만든다). 대조 함수는 `RestEntry`를 타입으로 받지 말고 `.method`/`.path`/`.body_schema`/`.query_schema`를 읽는 덕 타이핑으로 받는다 — `entry_schema()`가 이미 그렇게 돼 있다.
- **주석·문서는 한국어, WHY만.** **커밋 메시지는 영어 제목 + 한국어 본문.** 끝에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **테스트**: `rm -rf output/ && .venv/bin/python -m pytest tests/ -q` (기준선 **417 passed**). 잔재에 기대는 초록은 초록이 아니다.
- **완료 기준에 프로덕션 경로 스모크를 반드시 포함한다.** 계획 6~9에서 네 번 반복된 실패 형태가 "함수에는 인자를 열었는데 호출부가 안 넘긴다"였다. 각 태스크의 마지막 검증은 **실제로 명령을 쳐서** 확인한다.
- **브랜치**: `feat/plan10-pinned-openapi`에서 구현하고 리뷰 통과 후 머지한다.

---

## File Structure

| 파일 | 책임 | 변화 |
|---|---|---|
| `stub-seeds.example.json` | **신설** — 예시 스텁 시드 | 리포 루트. config가 아니라는 것이 위치로 드러난다 |
| `src/config/schema_site.py` | 사이트 config 스키마 | `StubSeedsConfig`·`target.stub_seeds` **제거**, `RestTarget.openapi_path` 추가 |
| `src/knowledge/target_api.py` | **신설** — pinned 명세 로더 + 대조 판정 | `load_target_api`, `parse_spec`, `spec_problems`, `response_field_problems` |
| `src/domain/ports.py` | 대상 포트 | `RestProberPort.fetch_spec()` — **인자 없음** |
| `src/infrastructure/rest_prober.py` | REST 실구현 | `fetch_spec` |
| `src/infrastructure/stubs.py` | REST 스텁 | `fetch_spec` |
| `src/patrol/daemon.py` | 데몬 조립 | `seeds_problems`, `digests["rules"]`의 기본값 배제, `digests["target_api"]` |
| `src/boot.py` | 기동 검증 | 명세 대조, `--live` 드리프트 |
| `src/__main__.py` | CLI | `--stub-seeds` 플래그 |
| `src/presentation/report_html.py`·`report.py` | 보고서 | as_of 4겹 표기 |
| `knowledge.example/target_api/gbm/gumi.json` | **신설** — 예시 pinned 명세 | |

**`target_api.py`를 `src/knowledge/`에 두는 이유**: pinned 명세는 topology·deployment와 같은 성질의 산출물이다 — git에 커밋되고, digest가 케이스에 박제되고, 사람이 갱신한다. config(권한)와 knowledge(증거)를 물리적으로 다른 디렉터리에 두는 것이 "문서는 증거, config는 권한"의 배선판이다.

---

## Task 1: 스텁 시드를 config 밖으로

**Files:**
- Create: `stub-seeds.example.json`
- Modify: `src/config/schema_site.py`(제거) · `src/patrol/daemon.py` · `src/__main__.py` · `src/boot.py`(기존 검사 제거) · `config.example/gbm/mx.json`
- Modify: `README.md` · `docs/tutorial.md` · `docs/going-live.md` · `docs/config-reference.md` · `tests/README.md`
- Test: `tests/test_cli.py` · `tests/test_boot.py` · `tests/test_examples.py`

**Interfaces:**
- Produces: `seeds_problems(seeds: dict[str, dict], sites: list) -> list[str]` (`src/patrol/daemon.py`)
- Produces: `load_stub_seeds(path: Path) -> tuple[dict[str, dict], list[str]]` (`src/patrol/daemon.py`)
- Consumes: `assemble_sites(..., stub_seeds=...)` — 기존 인자 유지(테스트가 쓴다), 이제 `dict[str, StubSeeds]`도 받는다

계획 9가 예시를 돌리려고 `target.stub_seeds`를 사이트 config에 들였다. 목적은 정당했지만 자리가 틀렸다 — 같은 `target` 블록이 **권한**(`entries`)과 **가짜 증거**(`stub_seeds`)를 함께 담게 됐고, 계획 10이 여기에 **진짜 증거**(pinned 명세)까지 얹으면 한 블록에 세 성질이 섞인다. 게다가 `adapters="real"`에서 조용히 무시되는 표면이라 going-live 체크리스트에 "지우는 것을 잊지 마라" 한 줄을 더해야 했다 — 그것은 **사람의 기억에 기대는 안전**이고, 이 리포는 그런 안전을 신뢰하지 않는다.

CLI 플래그로 옮기면 잊을 수 없다. 프로덕션 `patrol run`에는 플래그가 없고, 플래그가 없으면 시드도 없다. 메커니즘이 규율을 대신한다.

파일 형식(리포 루트 `stub-seeds.example.json`):

```json
{
  "mx/gumi": {
    "rest_responses": {
      "GET /lines": [{"line_code": "L1"}, {"line_code": "L2"}],
      "POST /summary/prod": {"badge": [0, 0, 0]},
      "/api/v1/lines/{line}/oee": {"oee": 512}
    }
  }
}
```

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_cli.py
def test_실_어댑터_사이트에_시드를_주면_거부한다(tmp_path, monkeypatch, capsys):
    # 시드는 스텁에서만 쓰인다. 조용히 무시하면 사람이 "가짜 데이터로 돌고 있다"고
    # 믿는 채 실제 대상을 두드린다 — 반대도 마찬가지로 나쁘다.
    _tree(tmp_path)
    monkeypatch.setattr("os.environ", dict(ENV))
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["target"]["adapters"] = "real"
    gbm.write_text(json.dumps(data), encoding="utf-8")
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps({"mx/gumi": {"rest_responses": {}}}), encoding="utf-8")

    code = main(["patrol", "run", "--for-seconds", "0", "--stub-seeds", str(seeds),
                 "--config-root", str(tmp_path / "config"), "--repo-root", str(tmp_path)])
    assert code == 1
    assert "mx/gumi" in capsys.readouterr().err
```

```python
# tests/test_boot.py — 옛 검사가 사라졌는지
def test_config에는_더_이상_stub_seeds를_쓸_수_없다(tmp_path):
    # 표면 자체를 없앴으므로 StrictModel이 거부한다 — 기동 검증 항목이 아니라
    # 스키마가 막는 것이 맞다(검증할 것이 없으면 검증을 지운다).
    import pytest
    from pydantic import ValidationError
    from src.config.schema_site import SiteConfig
    with pytest.raises(ValidationError):
        SiteConfig.model_validate({"target": {"stub_seeds": {"rest_responses": {}}}})
```

```python
# tests/test_examples.py
def test_README_빠른_시작이_시드_파일을_실제로_가리킨다():
    # 시드를 플래그로 옮기면 README 명령이 플래그 없이는 아무것도 안 보여준다 —
    # 그 결합을 테스트가 잡는다(전에는 간격이 그랬다).
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"patrol run [^\n]*--stub-seeds (\S+)", readme)
    assert match, "README 빠른 시작에 --stub-seeds가 없다"
    assert (ROOT / match.group(1)).exists(), match.group(1)
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

`.venv/bin/python -m pytest tests/test_cli.py tests/test_boot.py tests/test_examples.py -q`
기대: 3건 실패(`--stub-seeds` 인자 없음 / `StrictModel`이 아직 허용 / README에 플래그 없음).

- [ ] **Step 3: config에서 표면을 제거한다**

`src/config/schema_site.py`에서 `StubSeedsConfig` 클래스와 `Target.stub_seeds` 필드를 삭제한다. `src/boot.py`에서 `adapters=="real"인데 target.stub_seeds` 검사를 삭제한다(표면이 없으므로 검증할 것이 없다).

- [ ] **Step 4: 시드 로더와 판정을 만든다**

```python
# src/patrol/daemon.py
def load_stub_seeds(path: Path) -> tuple[dict[str, StubSeeds], list[str]]:
    """시드 파일을 읽어 (사이트키 → StubSeeds, 문제 목록)을 돌려준다. raise하지 않는다.

    사이트 키는 `"{gbm}/{fct}"`. 알 수 없는 키가 있으면 dataclass 생성이 TypeError로
    죽는 대신 문제로 보고한다 — 데몬 조립 중의 예외는 BootError가 아니라
    스택트레이스로 나가 사람이 원인을 못 찾는다.
    """
    allowed = {f.name for f in dataclasses.fields(StubSeeds)}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [f"시드 파일을 읽을 수 없다 ({path}): {exc}"]
    if not isinstance(raw, dict):
        return {}, [f"시드 파일의 최상위는 사이트 키 → 시드 매핑이어야 한다 ({path})"]
    seeds, problems = {}, []
    for site_key, spec in raw.items():
        if not isinstance(spec, dict):
            problems.append(f"시드 {site_key!r}가 dict가 아니다")
            continue
        unknown = set(spec) - allowed
        if unknown:
            problems.append(f"시드 {site_key!r}의 알 수 없는 키: {sorted(unknown)}")
            continue
        seeds[site_key] = StubSeeds(**spec)
    return seeds, problems


def seeds_problems(seeds: dict[str, StubSeeds], sites) -> list[str]:
    """시드가 향한 사이트가 실제로 스텁을 쓰는지 확인한다.

    조용히 무시하면 사람이 "가짜 데이터로 돌고 있다"고 믿는 채 실제 대상을 두드린다.
    반대(스텁인데 시드가 없다)는 문제가 아니다 — 빈 응답도 유효한 관측이다.
    """
    known = {f"{rt.gbm}/{rt.fct}": rt.cfg.target.adapters for rt in sites}
    problems = []
    for key in sorted(seeds):
        if key not in known:
            problems.append(f"시드 {key!r}에 해당하는 활성 사이트가 없다")
        elif known[key] == "real":
            problems.append(f"시드 {key!r}: 이 사이트는 adapters=\"real\"이라 시드가 무시된다")
    return problems
```

`assemble_sites`는 사이트별로 `seeds.get(f"{ref.gbm}/{ref.fct}")`를 꺼내 `build_adapters`에 넘긴다. 기존 `stub_seeds` 인자(단일 `StubSeeds`, 테스트가 쓴다)와의 공존은 이렇게 정한다: **인자로 단일 객체가 오면 모든 사이트에 적용**(현재 동작 유지), **dict가 오면 사이트 키로 조회**.

- [ ] **Step 5: CLI에 플래그를 단다**

`patrol run`과 `chat` 서브파서에 `--stub-seeds`(기본 `None`)를 추가한다. `_run_patrol`에서 **기동 검증 직후, 데몬 조립 전에** 로드·판정하고, 문제가 있으면 `BootError`와 같은 형식(`[where] problem`)으로 stderr에 찍고 `return 1`.

- [ ] **Step 6: 예시 트리를 옮긴다**

`config.example/gbm/mx.json`의 `target.stub_seeds` 블록을 `stub-seeds.example.json`으로 옮긴다(사이트 키 `"mx/gumi"`).

- [ ] **Step 7: 테스트 통과 확인 + 실제로 쳐 본다**

```bash
.venv/bin/python -m pytest tests/ -q
set -a; . ./.env.example; set +a
rm -f output/c-*.html
.venv/bin/python -m src patrol run --for-seconds 5 --stub-seeds stub-seeds.example.json \
  --config-root config.example --repo-root .
```
기대: 전체 통과 + `[상태] open` → `[보고서 준비] output/c-1.html`. **플래그를 빼고 한 번 더 쳐서** 케이스가 안 열리는 것도 확인하라 — 플래그가 실제로 하는 일이 있는지 보는 것이다.

- [ ] **Step 8: 문서를 사실에 맞춘다**

- `README.md`: 빠른 시작의 `patrol run` 명령에 `--stub-seeds stub-seeds.example.json` 추가. 그 아래 설명에 "이 플래그가 가짜 응답을 심는다. 실제 대상에 붙을 때는 플래그를 빼면 되고, **빼는 것을 잊을 수 없다** — config에 남는 설정이 아니기 때문이다"를 적어라.
- `docs/tutorial.md`: Part A의 `patrol run` 명령에 같은 플래그. `target.stub_seeds.mongo_collections` 언급을 시드 파일 경로로 바꿔라.
- `docs/going-live.md`: "`target.stub_seeds`도 같이 지워라" 문단과 체크리스트 항목을 **삭제**한다(표면이 없어져 지울 것이 없다). 대신 "`--stub-seeds` 플래그 없이 띄운다" 한 줄.
- `docs/config-reference.md`: `target.stub_seeds` 행 삭제, 기동 검증 목록에서 6번 삭제 후 **뒤 번호 전부 재정렬**하고 본문 참조(`검사 12`·`검사 13`·`검사 15`·`검사 16`)를 다시 맞춰라. **같은 파일 안의 참조를 놓치는 것이 이 리포에서 두 번 일어난 실수다** — `grep -n "검사 [0-9]" docs/config-reference.md`로 전부 확인하라.
- `tests/README.md`: `target.stub_seeds` 문단을 `--stub-seeds` 파일로 바꿔라.

- [ ] **Step 9: 커밋**

```bash
git add -A && git commit
```

---

## Task 2: 규칙 digest가 기본값에 흔들리지 않게

**Files:** Modify `src/patrol/daemon.py:~408` · Test `tests/patrol/test_daemon.py`

**Interfaces:** 변화 없음 — `digests["rules"]`의 계산 방식만 바뀐다

`checks_dump = {name: chk.model_dump(mode="json")}`는 **선언되지 않은 기본값까지 전부 덤프한다.** 계획 9가 `CheckConfig`에 `resolve: {}`를 더했을 때 모든 사이트의 rules digest가 통째로 바뀌었다. 지금은 아무도 비교하지 않아 무해하지만, 계획 10이 `RestEntry`에 필드를 더하고 그 digest로 드리프트를 판정하는 순간 **"설정을 안 바꿨는데 드리프트"**가 뜬다.

이 태스크는 `RestEntry`에 필드를 더하기 **전에** 끝나야 한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/patrol/test_daemon.py
def test_기본값을_명시해도_규칙_digest가_같다():
    # digest가 기본값에 흔들리면 스키마에 필드 하나 더할 때마다 전 사이트가
    # "드리프트"로 보인다 — 드리프트 판정이 그 순간 쓸모없어진다.
    from src.config.schema_site import CheckConfig
    from src.knowledge.digest import canonical_digest
    from src.patrol.daemon import rules_digest
    base = {"judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:/x",
            "params": {"rule": "exists", "field": "body"}}
    lean = CheckConfig.model_validate(base)
    verbose = CheckConfig.model_validate({**base, "resolve": {}, "probe": None})
    assert rules_digest({"c": lean}) == rules_digest({"c": verbose})
```

- [ ] **Step 2: 실패 확인** — `rules_digest` 미존재로 ImportError.

- [ ] **Step 3: 구현**

```python
# src/patrol/daemon.py
def rules_digest(checks: dict) -> str:
    """점검 설정의 content digest.

    `exclude_defaults=True`가 핵심이다 — 선언되지 않은 기본값을 덤프에 넣으면
    스키마에 필드를 하나 더할 때마다 손대지 않은 사이트의 digest가 바뀐다.
    드리프트 판정이 그것을 "설정이 바뀌었다"로 읽으면 경보가 전부 소음이 된다.
    """
    return canonical_digest({name: chk.model_dump(mode="json", exclude_defaults=True)
                             for name, chk in checks.items()})
```
`assemble_sites`의 `digests["rules"]`가 이것을 부른다.

- [ ] **Step 4: 통과 확인** — `.venv/bin/python -m pytest tests/ -q`

- [ ] **Step 5: 커밋**

---

## Task 3: pinned 명세 로더 — 남의 JSON에서 우리가 아는 부분집합만

**Files:** Create `src/knowledge/target_api.py` · Create `knowledge.example/target_api/gbm/gumi.json` · Test `tests/knowledge/test_target_api.py`

**Interfaces:**
- Produces: `TargetApi`, `OperationSpec`(둘 다 `StrictModel`)
- Produces: `parse_spec(raw: dict) -> TargetApi` — 절대 raise하지 않는다
- Produces: `load_target_api(knowledge_root: Path, gbm: str, fct: str) -> tuple[TargetApi | None, list[str]]`

**원문을 pydantic으로 파싱하지 않는다.** OpenAPI 문서는 남의 것이고 `x-*` 확장 키가 자유롭게 붙는다 — `StrictModel`로 받으면 그 순간 죽는다. 그렇다고 규율 5를 예외 처리하지도 않는다. **평범한 dict 접근으로 우리가 아는 것만 뽑아내고, 그 결과를 담는 우리 모델은 `StrictModel`이다.** 모르는 것은 담지 않으므로 `extra` 문제가 애초에 생기지 않는다.

```python
class OperationSpec(StrictModel):
    method: str
    path: str
    props: dict[str, str] = {}        # 필드명 → 우리 어휘의 타입("str"/"list[int]"…)
    unknown_props: list[str] = []     # 명세에 있으나 타입을 우리 어휘로 못 옮긴 것
    required: list[str] = []
    response_props: list[str] | None = None   # None = 명세가 응답 모양을 말하지 않았다


class TargetApi(StrictModel):
    digest: str                                # 원문 전체의 canonical_digest
    operations: dict[str, OperationSpec] = {}  # "POST /summary/prod" → spec
    problems: list[str] = []                   # 파싱 중 포기한 것들(조용한 생략 금지)
```

`response_props`가 `None`인 것과 `[]`인 것은 다르다. `None`은 **"명세가 말하지 않았다"**(우리는 아무 주장도 하지 않는다), `[]`는 **"명세가 빈 객체라고 말했다"**. 이 구별을 잃으면 Task 4의 응답 필드 검증이 명세가 침묵한 자리에서 거짓 오류를 낸다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/knowledge/test_target_api.py
from src.knowledge.target_api import parse_spec

RAW = {
    "openapi": "3.0.0",
    "x-vendor-extension": {"뭐든": "들어올 수 있다"},
    "paths": {
        "/summary/prod": {
            "post": {
                "operationId": "get_prod_summary",
                "requestBody": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "required": ["date"],
                    "properties": {
                        "part_code": {"type": "array", "items": {"type": "string"}},
                        "line_code": {"type": "array", "items": {"type": "string"}},
                        "date": {"type": "string"},
                        "graph_type": {"$ref": "#/components/schemas/GraphType"},
                    }}}}},
                "responses": {"200": {"content": {"application/json": {"schema": {
                    "type": "object", "properties": {"badge": {"type": "array",
                                                               "items": {"type": "integer"}}}}}}}},
            }
        },
        "/lines": {"get": {"parameters": [
            {"name": "active", "in": "query", "required": False, "schema": {"type": "boolean"}}]}},
    },
    "components": {"schemas": {"GraphType": {"type": "string", "enum": ["bar", "line"]}}},
}


def test_확장_키가_있어도_파싱된다():
    # 대상의 OpenAPI는 남의 문서다. x-* 하나에 죽으면 기동이 대상 팀 손에 있다.
    api = parse_spec(RAW)
    assert api.problems == []
    assert set(api.operations) == {"POST /summary/prod", "GET /lines"}


def test_body_스키마를_우리_어휘로_옮긴다():
    op = parse_spec(RAW).operations["POST /summary/prod"]
    assert op.props == {"part_code": "list[str]", "line_code": "list[str]",
                        "date": "str", "graph_type": "str"}   # $ref 해석
    assert op.required == ["date"]
    assert op.response_props == ["badge"]


def test_쿼리_파라미터도_같은_어휘로_옮긴다():
    op = parse_spec(RAW).operations["GET /lines"]
    assert op.props == {"active": "bool"} and op.required == []


def test_응답_스키마가_없으면_None이지_빈_리스트가_아니다():
    # "명세가 말하지 않았다"와 "명세가 빈 객체라고 말했다"는 다르다. 섞으면
    # 침묵한 자리에서 거짓 오류가 난다.
    api = parse_spec({"paths": {"/x": {"get": {}}}})
    assert api.operations["GET /x"].response_props is None


def test_모르는_타입은_버리지_않고_기록한다():
    # 조용히 넘기면 Task 4의 대조가 그 필드를 "명세에 없다"고 오판한다.
    api = parse_spec({"paths": {"/x": {"post": {"requestBody": {"content": {
        "application/json": {"schema": {"type": "object", "properties": {
            "blob": {"type": "object"}}}}}}}}}})
    op = api.operations["POST /x"]
    assert op.props == {} and op.unknown_props == ["blob"]


def test_망가진_명세에도_raise하지_않는다():
    for bad in (None, [], {"paths": "문자열"}, {"paths": {"/x": None}},
                {"paths": {"/x": {"get": {"requestBody": 3}}}}):
        api = parse_spec(bad)
        assert isinstance(api.problems, list)      # 죽지 않고 문제로 남긴다
```

**순환 `$ref` 테스트도 넣어라** — `{"$ref": "#/components/schemas/A"}`가 자기 자신을 가리키는 경우. 깊이 상한(예: 8)으로 끊고 `unknown_props`에 남긴다.

- [ ] **Step 2: 실패 확인** — 모듈 미존재.

- [ ] **Step 3: 구현**

타입 매핑(`_OPENAPI_TYPES`): `string→str`, `integer→int`, `number→float`, `boolean→bool`, `array` + `items.type`이 스칼라 → `list[<스칼라>]`. 그 외(`object`, items 없는 array, `oneOf`/`anyOf`) → `unknown_props`.

`$ref` 해석은 `#/components/schemas/<name>` 형태만 지원하고, 깊이 상한을 둔다. 다른 형태(외부 파일 참조 등)는 `unknown_props`.

전체를 `try/except Exception`으로 감싸 `problems`에 흡수한다 — 남의 JSON이다.

`digest`는 **원문 전체**의 `canonical_digest`다(부분집합이 아니라). 드리프트는 "우리가 보는 부분이 바뀌었나"보다 넓게 잡아야 사람이 확인할 기회를 얻는다.

- [ ] **Step 4: 로더**

```python
def load_target_api(knowledge_root: Path, gbm: str, fct: str) -> tuple[TargetApi | None, list[str]]:
    """`knowledge/target_api/{gbm}/{fct}.json`을 읽는다. 없으면 (None, [])."""
```
없는 것은 오류가 아니다 — pinned 명세는 선택이다(명세를 못 얻는 대상도 있다). 다만 **있는데 깨진 것**은 문제다.

- [ ] **Step 5: 예시 명세를 만든다**

`knowledge.example/target_api/gbm/gumi.json` — `config.example/gbm/mx.json`의 두 등재 항목(`summary_prod` POST `/summary/prod`, `list_lines` GET `/lines`)을 정확히 서술하는 최소 명세. **`config.example`의 시드 응답(`{"badge": [0,0,0]}`, `[{"line_code":"L1"}]`)이 이 명세를 만족해야 한다** — 안 그러면 Task 5의 응답 필드 검증이 예시 트리에서 실패한다.

- [ ] **Step 6: 통과 확인 + 커밋**

---

## Task 4: 대조 판정 — 명세는 말할 뿐, 넓히지 않는다

**Files:** Modify `src/knowledge/target_api.py` · Test `tests/knowledge/test_target_api.py`

**Interfaces:**
- Produces: `spec_problems(entries: dict, api: TargetApi) -> list[str]`
- Produces: `response_field_problems(checks: dict, entries: dict, api: TargetApi) -> list[str]`

I/O가 없는 순수 함수다 — 기동 검증과 `--live` 드리프트가 **같은 함수를 공유한다**(계획 8이 `entry_call_problems`로 세운 형태). 판정이 두 곳에서 갈리면 프로덕션 버그를 테스트가 못 잡는다.

네 가지를 본다:

| 상황 | 판정 | 왜 |
|---|---|---|
| 등재 항목이 명세에 없다 | 문제 | 오타이거나 대상이 제거했다. 매 순찰 404로 드러날 것을 배포 시점에 당긴다 |
| 우리 스키마 키가 명세에 없다 | 문제 | 보내도 무시되거나 400이다 |
| 타입이 다르다 | 문제 | `list[str]`로 보낼 것을 명세는 `str`이라 한다 |
| 명세가 **필수**라 한 키가 우리 스키마에 없다 | 문제 | 그 키 없이는 호출이 실패한다. 우리가 보낼 수단조차 없다 |
| 명세에 있는데 우리가 안 쓰는 키 | **문제 아님** | 우리는 필요한 것만 등재한다. 이걸 문제로 삼으면 명세가 우리 스키마를 넓히는 압력이 된다 |
| 우리 키를 명세가 `unknown_props`로 흘렸다 | **문제 아님, 다만 조용하지도 않다** | 명세가 우리 어휘 밖 타입을 썼다는 뜻이지 우리가 틀렸다는 뜻이 아니다. 검증을 건너뛴다는 사실을 문제 문자열이 아니라 별도 경로로 알린다 |

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_명세에_없는_항목을_잡는다():
    entries = {"ghost": _entry("POST", "/nowhere", body={"x": "str"})}
    problems = spec_problems(entries, parse_spec(RAW))
    assert any("ghost" in p and "/nowhere" in p for p in problems)


def test_명세에_없는_키와_타입_불일치를_잡는다():
    entries = {"summary_prod": _entry("POST", "/summary/prod",
                                      body={"part_code": "str", "save_as": "str"})}
    problems = spec_problems(entries, parse_spec(RAW))
    assert any("save_as" in p for p in problems)              # 명세에 없는 키
    assert any("part_code" in p and "list[str]" in p for p in problems)   # 타입 불일치


def test_명세가_필수라_한_키가_없으면_잡는다():
    entries = {"summary_prod": _entry("POST", "/summary/prod", body={"part_code": "list[str]"})}
    assert any("date" in p and "필수" in p
               for p in spec_problems(entries, parse_spec(RAW)))


def test_명세에만_있는_키는_문제가_아니다():
    # 이것을 문제로 삼으면 명세가 우리 스키마를 넓히는 압력이 된다 — 방향이 뒤집힌다.
    entries = {"summary_prod": _entry("POST", "/summary/prod",
                                      body={"date": "str"})}
    problems = spec_problems(entries, parse_spec(RAW))
    assert not any("line_code" in p or "graph_type" in p for p in problems)


def test_대조는_등재_항목을_수정하지_않는다():
    # "명세로 스키마를 넓히지 않는다"를 문장이 아니라 테스트로 못 박는다.
    entries = {"summary_prod": _entry("POST", "/summary/prod", body={"date": "str"})}
    before = {k: dict(v.body_schema) for k, v in entries.items()}
    spec_problems(entries, parse_spec(RAW))
    assert {k: dict(v.body_schema) for k, v in entries.items()} == before


def test_명세가_침묵한_응답_필드는_판정하지_않는다():
    # response_props가 None이면 아무 주장도 하지 않는다.
    api = parse_spec({"paths": {"/x": {"get": {}}}})
    checks = {"c": _check(target="rest:e", field="body.무엇이든")}
    assert response_field_problems(checks, {"e": _entry("GET", "/x")}, api) == []


def test_명세가_말한_응답에_없는_필드를_보면_잡는다():
    checks = {"c": _check(target="rest:summary_prod", field="body.badgee")}   # 오타
    entries = {"summary_prod": _entry("POST", "/summary/prod")}
    assert any("badgee" in p for p in
               response_field_problems(checks, entries, parse_spec(RAW)))
```

- [ ] **Step 2: 실패 확인 → Step 3: 구현 → Step 4: 통과 확인**

응답 필드 판정은 **최상위 키만** 본다. `body.badge.0`처럼 깊이 들어가는 경로는 명세의 중첩 스키마를 다 따라가야 하는데, 그 정확도를 확보하기 전에는 거짓 오류가 더 비싸다. 최상위만 보고, 그 사실을 docstring에 적어라.

`rule` 판정이 아닌 점검(`judge: "llm"`)은 `params.field`가 없으므로 건너뛴다.

- [ ] **Step 5: 커밋**

---

## Task 5: 기동 검증 배선 + `target_api` digest

**Files:** Modify `src/boot.py` · `src/patrol/daemon.py` · `src/presentation/report.py` · `src/presentation/report_html.py` · `src/domain/report_model.py` · Test `tests/test_boot.py` · `tests/presentation/test_report.py`

**Interfaces:**
- Consumes: `load_target_api`, `spec_problems`, `response_field_problems`
- Produces: `digests["target_api"]`가 `knowledge_digests`를 타고 케이스·보고서까지

`validate_boot`가 사이트마다 pinned 명세를 읽어 두 판정을 돌린다. 기동 거부 철학대로 **전부 모아서** 보고한다.

`assemble_sites`의 `digests`에 `"target_api"`를 더한다(명세가 없으면 `"absent"` — `deployment`가 이미 그 관례를 쓴다). 이것이 스펙 §4.1의 **as_of 4겹** 중 네 번째다.

보고서에 as_of 4겹을 적는다. **`knowledge_digests`가 이미 `Case`에 실려 있으므로 새 배선이 아니라 표기다** — 다만 `ReportModel`이 그것을 들고 있는지 먼저 확인하라. 없으면 `build_report_model`에 더한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/test_boot.py
def test_명세와_어긋난_등재_항목은_기동을_거부한다(tmp_path):
    # 오타 하나가 매 순찰 404로만 드러나던 것을 배포 시점으로 당긴다.
    _tree_with_target_api(tmp_path)       # knowledge/target_api/gbm/gumi.json을 쓴다
    _write(tmp_path, "config/gbm/mx.json", ...)   # body_schema에 "save_as"(명세에 없음)
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("save_as" in e.problem for e in errors)


def test_pinned_명세가_없으면_그것만으로는_기동을_막지_않는다(tmp_path):
    # 명세를 못 얻는 대상도 있다. 없는 것은 오류가 아니다 — 깨진 것이 오류다.
    _tree(tmp_path)
    assert not any("target_api" in e.problem for e in
                   validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path))
```

```python
# tests/patrol/test_daemon.py
def test_명세_digest가_케이스에_박제된다(tmp_path):
    # as_of 4겹의 네 번째. 없으면 "그때 그 API가 어떤 모양이었나"를 사후에 알 수 없다.
    _app, sites = assemble_sites(...)
    assert sites[0].digests["target_api"] not in ("", None)
```

```python
# tests/presentation/test_report.py
def test_보고서가_as_of_네_겹을_보여준다():
    text = render_report(RECORD, verdict=VERDICT, evidence=[], case_file=CASE_FILE,
                         clock=lambda: T)
    for axis in ("데이터", "코드", "지식", "target_api"):
        assert axis in text
```

- [ ] **Step 2~4: 실패 확인 → 구현 → 통과 확인**

- [ ] **Step 5: 예시 트리로 실제로 확인한다**

```bash
.venv/bin/python -m src knowledge validate --config-root config.example --repo-root .
```
기대: `OK`. 그다음 **일부러 깨뜨려서** 잡히는지 보라 — `config.example/gbm/mx.json`의 `body_schema`에 `"save_as": "str"`을 넣고 다시 쳐서 오류가 나오는지, 그리고 되돌려라.

- [ ] **Step 6: 문서**

`docs/config-reference.md`의 기동 검증 목록에 새 항목 둘을 넣고 번호를 다시 맞춰라(본문 참조 포함). `docs/glossary.md`에 `pinned OpenAPI`·`as_of 4겹` 항목. `docs/architecture.md`의 knowledge 절에 `target_api/` 추가.

- [ ] **Step 7: 커밋**

---

## Task 6: `--live` 드리프트 — 박제한 것과 지금 것을 견준다

**Files:** Modify `src/domain/ports.py` · `src/infrastructure/rest_prober.py` · `src/infrastructure/stubs.py` · `src/config/schema_site.py` · `src/boot.py` · Test `tests/domain/test_ports.py` · `tests/infrastructure/test_rest_prober.py` · `tests/test_boot.py`

**Interfaces:**
- Produces: `RestProberPort.fetch_spec() -> ProbeResult` — **인자 없음**
- Produces: `RestTarget.openapi_path: str = "/openapi.json"`

pin은 정적 린트일 뿐이다. 대상이 어제 배포한 변경을 알려면 실제로 받아 와서 견줘야 한다. 다만 **포트 표면을 넓히는 일**이므로 규율 9를 정면으로 만난다.

`fetch_spec()`에 **인자를 두지 않는 것**이 그 답이다. 호출자는 경로를 고를 수 없고, 어댑터가 `target.rest.openapi_path`(config가 선언)를 쓴다. "임의의 경로를 GET하라"가 여전히 표현 불가능하다 — `get(endpoint)`가 토폴로지 등재 경로만 받는 것과 같은 논리를 한 걸음 더 밀어붙인 형태다.

- [ ] **Step 1: 포트 표면 테스트를 먼저 고친다(실패 확인)**

```python
# tests/domain/test_ports.py
def test_REST_포트에_쓰기_메서드가_없다():
    surface = {name for name in vars(RestProberPort) if not name.startswith("_")}
    assert surface == {"get", "query", "fetch_spec"}
    for forbidden in ("post", "put", "patch", "delete", "request", "send"):
        assert not hasattr(RestProberPort, forbidden)


def test_fetch_spec은_경로를_받지_않는다():
    # 경로가 인자면 호출자가 정하게 된다 — "임의의 경로를 GET하라"가 다시 표현
    # 가능해지고, get(endpoint)의 토폴로지 등재 제약을 우회하는 문이 열린다.
    assert list(inspect.signature(RestProberPort.fetch_spec).parameters) == ["self"]
```

- [ ] **Step 2: 어댑터 구현**

`RealRest.fetch_spec`은 `self._openapi_path`를 GET한다(생성자에 추가). `guarded_call`을 그대로 쓴다. `StubRest.fetch_spec`은 시드의 `openapi` 항목을 돌려주거나, 없으면 `error`.

- [ ] **Step 3: 기동 검증 `--live` 배선**

```python
# src/boot.py — check_live 블록 안, Mongo 롤 검사 옆
# pin과 지금의 명세를 견준다. 다른 부분 전체를 보고하지 않는다 — 대상 API는
# 우리가 안 쓰는 끝점이 수백 개다. **우리 등재 항목에 영향을 주는 차이만** 말한다.
```
1. `fetch_spec()` → 실패면 `"명세를 받을 수 없다 — {error}"` 하나로 끝(기동을 막을지는 아래 참조).
2. `parse_spec(live)`의 `digest`가 pin과 같으면 조용히 끝.
3. 다르면 `spec_problems(entries, live_api)`를 돌려 **우리 항목에 실제로 영향을 주는 문제만** 보고하고, 영향이 없으면 `"명세가 바뀌었지만 등재 항목에는 영향이 없다 — pin을 갱신하라"` 한 줄.

**기동을 막을 것인가?** 막는다 — `--live`는 명시적으로 켜는 옵션이고, 그 옵션을 켠 사람은 "지금 실제와 맞는지" 묻고 있다. 다만 3번의 "영향 없음"은 **문제로 올리되 문구가 조치를 지시**해야 한다(pin 갱신). 1번(못 받음)은 대상이 죽어 있을 때 우리 배포를 막는 형태라 **경고에 그친다** — "죽은 사이트가 기동을 막으면 역효과"라는 기존 원칙(`--live` Mongo 검사와 같은 자리)을 따른다.

- [ ] **Step 4: 테스트**

```python
def test_라이브_명세가_pin과_다르면_영향받는_항목을_말한다(tmp_path):
def test_라이브_명세를_못_받아도_기동을_막지_않는다(tmp_path):
def test_라이브_명세가_pin과_같으면_조용하다(tmp_path):
```
스텁 어댑터에 `openapi` 시드를 심어 세 경우를 만든다.

- [ ] **Step 5: 문서 + 커밋**

`docs/going-live.md`에 "pin을 언제 갱신하는가" 절 — `knowledge validate --live`가 드리프트를 알리면 `curl {base}/openapi.json > knowledge/target_api/{gbm}/{fct}.json`으로 갱신하고 **커밋한다**. 자동 갱신하지 않는 이유(fail-open)를 한 줄로.

---

## Self-Review

**스펙 커버리지**: §2-N1(문서는 증거, config는 권한) → Task 4의 "명세에만 있는 키는 문제가 아니다"·"대조는 등재 항목을 수정하지 않는다" 두 테스트가 방향을 못 박는다. §4.1 as_of 4겹 → Task 5. P4의 "pinned OpenAPI + 드리프트 점검 + boot 검증" → Task 3·4·5·6. **`stub_seeds` 이전(Task 1)과 digest 안정화(Task 2)는 P4에 없던 항목이다** — 계획 9의 리뷰가 남긴 인계이고, 둘 다 Task 3 이후에 하면 더 비싸진다(전자는 config에 세 성질이 섞인 뒤, 후자는 `RestEntry`에 필드가 늘어난 뒤).

**타입 일관성**: `spec_problems(entries, api)`의 `entries`는 `dict[str, RestEntry]`지만 타입을 명시하지 않는다(레이어 방향 제약). `parse_spec`은 `TargetApi`를 돌려주고 `load_target_api`는 `(TargetApi | None, list[str])`를 돌려준다 — `load_deployment`가 `Deployment | None`만 돌려주는 것과 다른 이유는, 명세는 **깨진 채 존재할 수 있고** 그 사실을 기동 검증이 말해야 하기 때문이다.

**하지 않는 것**:

| 하지 않는 것 | 왜 |
|---|---|
| 명세에서 등재 항목을 **자동 생성** | 교과서적 fail-open. 대상이 새 POST를 배포하면 우리 허용 범위가 자동으로 넓어진다 |
| `body_schema`를 명세 스키마로 **치환** | 같은 이유. 명세는 별도 파일에 살고 대조만 한다 |
| OpenAPI enum으로 **파라미터 값을 채우기** | 계획 9의 해석기가 하는 일이고, enum은 "무엇이 합법인가"이지 "무엇을 원하는가"가 아니다. 값 소스로 넣고 싶다면 별도 계획 |
| 중첩 응답 필드 경로 검증 | 정확도를 확보하기 전에는 거짓 오류가 더 비싸다. 최상위 키만 |
| 순찰 self_check에 드리프트 잡 얹기 | `--live`가 먼저 실전에서 쓸 만한지 보고 나서. 주기 실행은 대상 부하와 경보 피로를 함께 만든다 |
| 명세의 `operationId`/`summary`/`tags` 활용 | 우리가 소유하지 않는 남의 산문이다(스펙 §2-N1) |
