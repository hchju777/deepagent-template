# 브리핑에 지식을 실제로 싣기 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 리드 브리핑의 `[적용 룰]`과 배포 버전이 프로덕션에서 실제 값을 갖게 하고,
아무도 채울 수 없는 `docs_text`를 없앤다.

**Architecture:** `build_briefing`은 처음부터 `rules_text`·`docs_text`를 받았지만
**어디에서도 설정되지 않는다** — `EngineDeps`의 기본값 `""`가 그대로 흘러 브리핑이 매번
"적용 룰: 없음"을 찍는다. 배포 정보는 스펙 §3.6이 브리핑 재료로 명시했는데 `assemble_sites`가
읽어 digest만 계산하고 버린다. 이 계획은 세 가지를 한다: (1) 사이트 점검 선언을
`EngineDeps`로 흘려 `frame`이 **슬라이스에 걸리는 것만** 렌더한다, (2) 배포 매핑을 같은 길로
흘려 슬라이스 서비스만 렌더한다, (3) 생산자가 없는 `docs_text`를 제거한다.

**Tech Stack:** Python 3.12, pydantic v2, pytest(`asyncio_mode=auto`).

## Global Constraints

- 규율 2: `datetime.now()`는 `src/__main__.py` 밖에서 부르지 않는다. 이 계획은 시각을 안 쓴다.
- 규율 5: 새 pydantic 모델은 `StrictModel` 상속. 이 계획은 새 모델을 안 만든다.
- 규율 6: 브리핑에 무엇이 실리는지는 **코드가 정한다** — 관련성 판정을 LLM에 맡기지 않는다.
- 규율 7: 이벤트 어휘를 안 늘린다.
- 주석·문서는 한국어 WHY, 커밋 메시지는 영어.
- 테스트: `rm -rf output/; .venv/bin/python -B -m pytest tests/ -q -p no:cacheprovider`

## 지금 사실(구현 전 실측)

```
$ grep -rn "rules_text\|docs_text" src/ | grep -v briefing.py
src/application/deps.py:19:    rules_text: str = ""
src/application/deps.py:21:    docs_text: str = ""
src/application/nodes.py:231,232  (deps.rules_text / deps.docs_text를 읽기만 한다)
$ grep -rn "rules_text\|docs_text" tests/
(없음)
$ grep -rn "deployment" src/application/*.py
(없음)
```

- `EngineDeps`를 만드는 프로덕션 호출부는 `assemble_sites`(`src/patrol/daemon.py:577`)
  **하나뿐**이고, 거기서 `rules_text`·`docs_text`를 안 넘긴다.
- `assemble_sites`는 `patrol run`·`chat`·`case resume` 세 CLI 명령이 부른다
  (`tests/test_cli.py:605`가 그 셋을 이미 단정한다) — 여기 배선하면 세 경로가 함께 얻는다.
- `api` 프로세스는 조사를 안 하므로(규율 9의 형제) 관계없다.
- 자유 문서 코퍼스는 **없다** — `src/knowledge/`에 로더가 없고 `KnowledgeConfig`는
  `root`만 있다. `docs_text`는 생산자 없는 매개변수다.

## File Structure

| 파일 | 책임 |
|---|---|
| `src/application/briefing.py` (수정) | `render_rules`·`render_deployment` 추가, `build_briefing` 시그니처 변경 |
| `src/application/deps.py` (수정) | `rules_text`/`docs_text` → `checks`/`deployment` |
| `src/application/nodes.py` (수정) | `frame`이 슬라이스로 필터해 렌더 |
| `src/patrol/daemon.py` (수정) | `assemble_sites`가 점검·배포를 `EngineDeps`에 넘긴다 |
| `tests/application/test_briefing.py` (수정/추가) | 렌더·필터 단위 |
| `tests/patrol/test_daemon.py` (수정/추가) | 호출부가 실제로 넘기는가 |
| `docs/architecture.md` (수정) | 브리핑 재료의 현재 진실 |

---

### Task 1: 적용 룰 렌더러와 슬라이스 필터

**Files:**
- Modify: `src/application/briefing.py`
- Test: `tests/application/test_briefing.py`

**Interfaces:**
- Consumes: `Topology`·`DataRef`(`src/knowledge/topology.py`) — locator는 `DataRef.locator`
  프로퍼티(`"<kind>:<field>"`)이고 별도 타입이 아니다. `CheckConfig`(`src/config/schema_site.py`)
- Produces: `render_rules(checks: dict[str, CheckConfig], *, slice_: Topology, target_locator: str | None) -> str`

관련성 판정은 **코드가 쥔다**(규율 6): 점검의 `target`이 (a) 케이스의
`target_locator`와 같거나 (b) 슬라이스의 derivation 출력 locator이거나 (c) 슬라이스
derivation의 입력 locator일 때만 싣는다. `rest:<이름>` 표적은 locator가 아니므로
(a)로만 걸린다 — 한계이고 주석에 적는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# tests/application/test_briefing.py
from src.application.briefing import render_rules, upstream_slice
from src.config.schema_site import CheckConfig
from src.knowledge.topology import DataRef, Derivation, Service, Topology


def _check(target, **kw):
    # Schedule은 interval/cron 중 하나다 — `every`는 StrictModel이 튕긴다.
    return CheckConfig.model_validate(
        {"judge": "rule", "schedule": {"interval": "10m"}, "target": target,
         "params": {"rule": "range", "min": 0, "max": 100}, **kw})


def test_슬라이스_밖의_점검은_브리핑에_안_실린다():
    topo = Topology(services={}, derivations={
        "mongo:twin.oee": Derivation(via="oee-calc", key="line",
                                     inputs=[DataRef(kind="mongo", collection="twin.raw")])})
    slice_ = upstream_slice(topo, "mongo:twin.oee")
    text = render_rules({"api.oee_range": _check("mongo:twin.oee"),
                         "kafka.lag": _check("kafka:edge.raw")},
                        slice_=slice_, target_locator="mongo:twin.oee")
    assert "api.oee_range" in text
    assert "kafka.lag" not in text


def test_상류_입력_locator를_보는_점검도_실린다():
    topo = Topology(services={}, derivations={
        "mongo:twin.oee": Derivation(via="oee-calc", key="line",
                                     inputs=[DataRef(kind="mongo", collection="twin.raw")])})
    slice_ = upstream_slice(topo, "mongo:twin.oee")
    text = render_rules({"raw.freshness": _check("mongo:twin.raw")},
                        slice_=slice_, target_locator="mongo:twin.oee")
    assert "raw.freshness" in text


def test_적용_룰은_한_줄로_접힌다():
    # params 값에 개행이 있으면 브리핑의 [적용 룰] 블록에 가짜 항목처럼 붙는다
    check = CheckConfig.model_validate(
        {"judge": "rule", "schedule": {"interval": "10m"}, "target": "a:b",
         "params": {"rule": "range", "note": "line1\nline2"}})
    text = render_rules({"c": check}, slice_=Topology(), target_locator="a:b")
    assert len(text.splitlines()) == 1


def test_걸리는_점검이_없으면_빈_문자열이다():
    assert render_rules({}, slice_=Topology(), target_locator=None) == ""
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -B -m pytest tests/application/test_briefing.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'render_rules'`

- [ ] **Step 3: 최소 구현**

```python
# src/application/briefing.py
def _slice_locators(slice_, target_locator):
    locators = {target_locator} if target_locator else set()
    for output, deriv in slice_.derivations.items():
        locators.add(output)
        locators.update(ref.locator for ref in deriv.inputs)
    return locators


def render_rules(checks, *, slice_, target_locator):
    """브리핑의 `[적용 룰]` — 슬라이스에 걸리는 점검만.

    전부 싣지 않는 이유는 스펙 §3.6("전체 코퍼스 덤프 금지")이다. 사이트 점검이
    수십 개인 곳에서 전량을 실으면 리드가 자기 케이스와 무관한 임계값을 정상 기준으로
    읽는다.

    `rest:<이름>` 표적은 locator가 아니라 등재 항목 이름이라 케이스 locator와 같을
    때만 걸린다 — 등재 항목이 어느 locator를 만드는지는 토폴로지가 말하지 않는다.
    """
    relevant = _slice_locators(slice_, target_locator)
    lines = []
    for name in sorted(checks):
        check = checks[name]
        if check.target not in relevant:
            continue
        params = ", ".join(f"{k}={v!r}" for k, v in sorted(check.params.items()))
        line = f"- {name}: judge={check.judge}, target={check.target}, {params}"
        # 한 줄로 접는다 — params 값의 개행이 [적용 룰] 블록에 가짜 항목을 만든다
        # (history의 _clean_line과 같은 이유).
        lines.append(" ".join(line.split()))
    return "\n".join(lines)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -B -m pytest tests/application/test_briefing.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/application/briefing.py tests/application/test_briefing.py
git commit -m "Render the checks that watch this case, not every check"
```

---

### Task 2: 배포 버전 렌더러

**Files:**
- Modify: `src/application/briefing.py`
- Test: `tests/application/test_briefing.py`

**Interfaces:**
- Consumes: `Deployment`(`src/knowledge/deployment.py`)
- Produces: `render_deployment(deployment: Deployment | None, *, slice_: Topology) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
from src.application.briefing import render_deployment
from src.knowledge.deployment import Deployment, DeployedVersion


def test_슬라이스_서비스의_배포_커밋만_싣는다():
    dep = Deployment(services={"oee-calc": DeployedVersion(repo="twin", commit="abc123"),
                               "unrelated": DeployedVersion(repo="x", commit="def456")})
    slice_ = Topology(services={"oee-calc": Service()}, derivations={})
    text = render_deployment(dep, slice_=slice_)
    assert "abc123" in text and "def456" not in text


def test_배포_매핑이_없으면_미검증이라고_적는다():
    text = render_deployment(None, slice_=Topology())
    assert "미검증" in text
```

`Service`의 필드는 `code`(선택)·`reads`·`writes`이므로 `Service()`로 충분하다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -B -m pytest tests/application/test_briefing.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'render_deployment'`

- [ ] **Step 3: 최소 구현**

```python
def render_deployment(deployment, *, slice_):
    """브리핑의 `[배포 버전]` — 슬라이스 서비스만.

    없을 때 "없음"이 아니라 "미검증"이라고 적는다: 배포 매핑의 부재는 "배포가 없다"가
    아니라 "무엇이 돌고 있는지 우리가 모른다"이고, 코드 증거는 그 사실을 달고 다녀야
    한다(`knowledge/deployment.py` docstring).
    """
    if deployment is None:
        return "배포 매핑 없음 — 이 사이트의 코드 증거는 배포 버전 미검증이다"
    lines = [f"- {name}: {v.repo}@{v.commit}"
             for name, v in sorted(deployment.services.items()) if name in slice_.services]
    return "\n".join(lines)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -B -m pytest tests/application/test_briefing.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/application/briefing.py tests/application/test_briefing.py
git commit -m "Say which commit is running for the services in the slice"
```

---

### Task 3: 브리핑 조립 교체 — `docs_text` 제거, 배포 추가

**Files:**
- Modify: `src/application/briefing.py`, `src/application/deps.py`, `src/application/nodes.py`
- Test: `tests/application/test_briefing.py`, `tests/application/test_nodes_frame.py`

**Interfaces:**
- Produces: `build_briefing(case, topo_slice, *, rules_text="", history_text="", deployment_text="")`
  (`docs_text` **삭제**)
- Produces: `EngineDeps.checks: dict[str, CheckConfig] = field(default_factory=dict)`,
  `EngineDeps.deployment: Deployment | None = None`
  (`rules_text`·`docs_text` **삭제**, `history_text`는 폴백으로 남긴다)

`docs_text`를 지우는 근거: 자유 문서 코퍼스도 선별기도 없다. 생산자가 없는 매개변수는
"배선돼 있다"는 착각을 만들고, 이 리포는 정확히 그 착각으로 두 번 데었다(`resume_once`,
`patrol run`의 `scenarios`). 필요해지면 그때 로더와 함께 연다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_브리핑에_docs_섹션이_없다():
    # 자유 문서는 생산자가 없다 — 매번 "없음"을 찍는 섹션은 리드 프롬프트의 잡음이다
    import inspect
    from src.application import briefing
    assert "docs_text" not in inspect.getsource(briefing)


def test_frame이_슬라이스로_필터한_룰을_브리핑에_싣는다(...):
    # deps.checks에 슬라이스 안 점검 하나와 밖 점검 하나를 두고,
    # ScriptedLLM이 받은 프롬프트에 앞의 것만 있는지 단정한다
```

`test_nodes_frame.py`의 기존 픽스처(`EngineDeps(...)`)를 새 필드로 갱신한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -B -m pytest tests/application/ -q -p no:cacheprovider`
Expected: FAIL

- [ ] **Step 3: 구현**

`build_briefing`에서 `[관련 문서]` 줄을 지우고 `[배포 버전]`을 넣는다.
`frame`은 다음처럼 조립한다:

```python
rules_text = render_rules(deps.checks, slice_=topo_slice,
                          target_locator=case.target_locator)
briefing = build_briefing(case, topo_slice, rules_text=rules_text,
                          history_text=history_text,
                          deployment_text=render_deployment(deps.deployment, slice_=topo_slice))
```

- [ ] **Step 4: 전체 스위트를 돌린다**

Run: `rm -rf output/; .venv/bin/python -B -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS (기존 픽스처 갱신 포함)

- [ ] **Step 5: 커밋**

```bash
git add -A
git commit -m "Put the rules and the running commit into the lead's briefing"
```

---

### Task 4: 호출부 — `assemble_sites`가 실제로 넘기는가

**Files:**
- Modify: `src/patrol/daemon.py`
- Test: `tests/patrol/test_daemon.py`

이 리포가 반복해서 데인 지점이다: 함수는 되는데 호출부가 안 넘긴다. `assemble_sites`가
`patrol run`·`chat`·`case resume` 셋의 공통 조립점이므로 여기 한 곳을 단정하면 셋이 함께
지켜진다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
def test_조립이_사이트_점검과_배포를_엔진_의존에_넣는다(tmp_path):
    # 임시 config 트리(기존 픽스처 헬퍼 재사용) + knowledge/deployment/<gbm>/<fct>.yaml
    app, sites = assemble_sites(config_root, repo_root, env, clock=lambda: FIXED,
                                llm_factory=lambda profile: object())
    deps = sites[0].deps
    assert deps.checks == sites[0].cfg.patrol.checks
    assert deps.deployment is not None and "oee-calc" in deps.deployment.services
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -B -m pytest tests/patrol/test_daemon.py -q -p no:cacheprovider`
Expected: FAIL — `deps.checks == {}`

- [ ] **Step 3: 구현**

```python
        deps = EngineDeps(
            ..., engine_cfg=app.engine,
            checks=site_cfg.patrol.checks, deployment=deployment,
        )
```

- [ ] **Step 4: 전체 스위트**

Run: `rm -rf output/; .venv/bin/python -B -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add -A
git commit -m "Hand the site's checks and deployment to the engine that briefs the lead"
```

---

### Task 5: 문서 정정

**Files:**
- Modify: `docs/architecture.md`

- [ ] **Step 1: 브리핑 재료의 현재 진실을 적는다**

무엇이 실리고(토폴로지 슬라이스·적용 룰·유사 이력·배포 버전) 무엇이 **안 실리는지**
(자유 문서 — 코퍼스도 선별기도 없다) 적는다. 스펙 §3.6이 다섯을 열거하므로, 넷만
있다는 사실을 문서가 말하지 않으면 다음 사람이 또 "배선돼 있겠지"로 읽는다.

- [ ] **Step 2: 커밋**

```bash
git add docs/architecture.md
git commit -m "Say which of the five briefing materials actually exist"
```

---

## 인계(계획 18 이후)

1. **자유 문서는 안 만들었다** — 코퍼스 로더도 선별기도 없다. 만들 때는 "무엇을 고르는가"를
   코드가 쥐어야 한다(규율 6): LLM에 문서 목록을 주고 고르게 하면 그것이 곧 컨텍스트 폭발이다.
2. **`rest:<이름>` 표적의 관련성은 케이스 locator와의 문자열 일치로만 걸린다** — 등재 항목이
   어느 locator를 만드는지는 토폴로지가 말하지 않는다. 필요해지면 토폴로지에 그 연결을 넣어야
   하고, 그건 지식 스키마 변경이다.
3. **필터의 제외 대상은 "토폴로지 안, 슬라이스 밖"이어야 한다** — 토폴로지에 아예 없는
   locator를 픽스처의 제외 대상으로 고르면 "슬라이스로 걸렀다"와 "토폴로지 전체로 걸렀다"가
   같은 결과를 내고, 전자를 후자로 바꾸는 변조가 테스트를 통과한다(검증 리뷰 F1). 스펙
   §3.6이 금지한 코퍼스 덤프로 되돌아가는 바로 그 변조다.
4. **`params` 값에 개행을 넣어 접기를 시험할 수 없다** — `{value!r}`로 렌더돼 개행이 이미
   이스케이프된다. 접기가 실제로 막는 자리는 `!r`를 안 거치는 **점검 이름**과 `target`이다
   (검증 리뷰 F2).
5. **배포는 "없음"을 안 쓴다 — 세 상태를 가른다**: 매핑 부재, 슬라이스 서비스가 매핑에
   빠짐(이름을 댄다), 슬라이스가 빔. 처음엔 첫째만 구별했는데, 사람이 연 케이스는
   `target_locator`가 없어 **항상** 셋째에 걸리므로 "없음"이 대부분을 뭉갰다(검증 리뷰 F5).
6. **브리핑에 실리는 가변 문자열은 전부 `_one_line`을 지난다** — 고정 문자열과
   `Literal`·`datetime` 필드는 값이 개행을 못 담아 예외다. 렌더러 두 개만 접고
   `build_briefing` 자신의 자리를 안 접으면 한 위협 모델에 대해 반만 막은 것이다.
   같은 서비스 이름이 한 줄 위에서는 접히고 아래에서는 안 접히는 상태였고(검증 리뷰 I1),
   그것을 고친 커밋이 **같은 함수에 안 접히는 줄을 새로 추가**했다(다음 라운드가 잡았다).
   **브리핑에 새 줄을 더할 때 이 함수를 지나게 하고, 그것을 지키는 테스트를 함께 써라.**
7. **`[배포 버전]`은 "없음"을 안 쓴다 — 세 상태를 가른다.** 매핑 부재는 슬라이스보다
   **먼저** 답한다: 그건 서비스가 아니라 사이트에 대한 진술이라 슬라이스가 비어도 참이다.
8. **`docs_text` 부재 테스트는 그 이름만 지킨다** — 다른 이름으로 같은 섹션을 되살리면
   통과한다. 되돌림 방지에는 충분하고, "생산자 없는 섹션을 두지 않는다"는 성질은 사람이
   리뷰에서 본다(검증 리뷰 F6).
9. **배포 매핑은 여전히 코드 어댑터에 안 간다** — `build_adapters`는 `deployment`를 안 받고,
   `code_repo.py`의 docstring이 주장하는 "배포 hash로 읽는다"는 배선이 아직 없다. 브리핑에
   커밋이 실리는 것과 어댑터가 그 커밋으로 읽는 것은 다른 문제다.
