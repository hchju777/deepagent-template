import json

from src.infrastructure.factory import StubSeeds
from src.boot import validate_boot

# LLM_API_KEY: 검사 11(계획 4b, I8) — enabled 사이트+llm 프로파일이 있으면 필수.
ENV = {"MX_REDIS_URL": "redis://g:6379", "LLM_API_KEY": "test-llm-key"}


def _write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _tree(tmp_path, *, check_target="rest:/oee", repo_name="twin-services"):
    _write(tmp_path, "config/app.json", json.dumps(
        {"llm": {"profiles": {"judge": "a", "subagent": "b", "lead": "c"}}}))
    _write(tmp_path, "config/registry.json", json.dumps(
        {"sites": [{"gbm": "mx", "fct": "gumi"},
                   {"gbm": "mx", "fct": "off", "enabled": False}]}))
    _write(tmp_path, "config/gbm/mx.json", json.dumps(
        {"target": {"redis": {"url": "${MX_REDIS_URL}"},
                    "code": {"repos": [{"name": "twin-services", "path": "/r"}]}},
         "patrol": {"checks": {"c1": {"judge": "rule",
                                      "schedule": {"interval": "5m"},
                                      "target": check_target}}}}))
    _write(tmp_path, "knowledge/topology/common.yaml", f"""
services:
  twin-api:
    code: {{ repo: {repo_name}, path: api }}
    writes: [ {{ kind: rest, endpoint: /oee }} ]
derivations:
  "rest:/oee": {{ inputs: [ {{ kind: mongo, collection: twin_state }} ], via: twin-api }}
""")
    return tmp_path


def test_정상_트리는_통과하고_disabled_사이트는_건너뛴다(tmp_path):
    _tree(tmp_path)   # mx/off 사이트는 config 파일이 없지만 disabled라 검사 안 함
    assert validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path) == []


def test_해석_안되는_룰_타깃은_거부(tmp_path):
    _tree(tmp_path, check_target="rest:/ghost")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("rest:/ghost" in e.problem for e in errors)


def test_토폴로지의_미등록_repo는_거부(tmp_path):
    _tree(tmp_path, repo_name="ghost-repo")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("ghost-repo" in e.problem for e in errors)


def test_오류는_전부_모인다(tmp_path):
    _tree(tmp_path, check_target="rest:/ghost", repo_name="ghost-repo")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert len(errors) >= 2


def test_깨진_토폴로지는_크래시가_아니라_오류로_모인다(tmp_path):
    _tree(tmp_path)
    # 토폴로지를 스키마 위반(kafka인데 collection 선언)으로 덮어쓴다
    _write(tmp_path, "knowledge/topology/common.yaml", """
services:
  twin-api:
    writes: [ { kind: kafka, collection: wrong_field } ]
""")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("토폴로지 로드 실패" in e.problem for e in errors)


def test_깨진_JSON_config는_기동_검증_오류로_모인다(tmp_path):
    _tree(tmp_path)
    _write(tmp_path, "config/app.json", "{ broken")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("JSON 파싱 실패" in e.problem for e in errors)


def test_deployment의_hash가_레포에_없으면_거부(tmp_path):
    _tree(tmp_path)
    # 실제 git repo를 만들어 config가 가리키게 한다
    import subprocess
    repo = tmp_path / "repos" / "twin-services"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    # config의 repo path를 실제 경로로 교체
    import json
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["target"]["code"]["repos"][0]["path"] = str(repo)
    gbm.write_text(json.dumps(data), encoding="utf-8")

    # 실재하는 hash → 통과
    _write(tmp_path, "knowledge/deployment/mx/gumi.yaml",
           f"services:\n  twin-api: {{ repo: twin-services, commit: {head} }}\n")
    assert validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path) == []

    # 유령 hash → 거부
    _write(tmp_path, "knowledge/deployment/mx/gumi.yaml",
           "services:\n  twin-api: { repo: twin-services, commit: deadbeef }\n")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("deadbeef" in e.problem for e in errors)


def test_deployment이_없으면_검사7은_건너뛴다(tmp_path):
    _tree(tmp_path)
    assert validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path) == []


def test_해석_안되는_프로브는_기동_거부(tmp_path):
    _tree(tmp_path)
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["patrol"]["checks"]["c1"]["probe"] = "ghost_probe"
    gbm.write_text(json.dumps(data), encoding="utf-8")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("프로브" in e.problem for e in errors)


def test_check_live에서_mongo_role_problems가_가짜_connection_status로_검사된다(tmp_path, monkeypatch):
    _tree(tmp_path)
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["target"]["adapters"] = "real"
    data["target"]["mongo"] = {"url": "mongodb://x", "username": "svc", "db": "twin"}
    gbm.write_text(json.dumps(data), encoding="utf-8")

    async def _fake_conn_status(cfg):
        return {"authInfo": {"authenticatedUserRoles": [{"role": "dbOwner", "db": "twin"}]}}

    import src.boot as boot
    monkeypatch.setattr(boot, "_fetch_conn_status", _fake_conn_status)

    # check_live=False(기본)는 patch된 헬퍼조차 건드리지 않고 정적 검증만 통과한다
    assert validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path) == []

    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path, check_live=True)
    assert any("dbOwner" in e.problem for e in errors)


def test_enabled_사이트가_있는데_LLM_API_KEY가_없으면_기동_거부(tmp_path):
    _tree(tmp_path)
    env_without_key = {k: v for k, v in ENV.items() if k != "LLM_API_KEY"}
    errors = validate_boot(tmp_path / "config", env=env_without_key, repo_root=tmp_path)
    assert any("LLM_API_KEY" in e.problem for e in errors)


def test_app_json의_env_참조가_미치환이면_기동_거부(tmp_path):
    _tree(tmp_path)
    _write(tmp_path, "config/app.json", json.dumps(
        {"store": {"backend": "mongo", "mongo_url": "${AGENT_MONGO_URL}"},
         "llm": {"profiles": {"judge": "a", "subagent": "b", "lead": "c"}}}))
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("AGENT_MONGO_URL" in e.problem for e in errors)


def test_llm_판정기가_있는데_judge_프로파일이_비면_기동_거부(tmp_path):
    _tree(tmp_path)
    app = tmp_path / "config" / "app.json"
    app.write_text(json.dumps({"llm": {"profiles": {"judge": "", "subagent": "b", "lead": "c"}}}), encoding="utf-8")
    gbm = tmp_path / "config" / "gbm" / "mx.json"
    data = json.loads(gbm.read_text(encoding="utf-8"))
    data["patrol"]["checks"]["c1"]["judge"] = "llm"
    gbm.write_text(json.dumps(data), encoding="utf-8")
    errors = validate_boot(tmp_path / "config", env=ENV, repo_root=tmp_path)
    assert any("judge" in e.problem for e in errors)


def test_등재_항목_target은_토폴로지가_아니라_entries로_해석된다(tmp_path):
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", json.dumps({
        "target": {"adapters": "stub", "rest": {
            "base_url": "http://x",
            "entries": {"summary_prod": {"method": "POST", "path": "/summary/prod",
                                         "body_schema": {"part_code": "list[str]"}}}}},
        "patrol": {"checks": {"prod.badge": {
            "judge": "rule", "schedule": {"interval": "5m"},
            "target": "rest:summary_prod",
            "params": {"rule": "exists", "field": "body.badge"}}}},
        "knowledge": {"root": "knowledge.example"}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert not [e for e in errors if "summary_prod" in e.problem]


def test_미등재_항목을_참조하는_점검은_기동을_거부한다(tmp_path):
    # 오타나 삭제된 항목을 참조하면 매 순찰이 error를 내고 끝난다 — 밤에 조용히
    # 틀리는 것보다 배포 시점에 시끄럽게 죽는 게 낫다.
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", json.dumps({
        "target": {"adapters": "stub", "rest": {"base_url": "http://x", "entries": {}}},
        "patrol": {"checks": {"prod.badge": {
            "judge": "rule", "schedule": {"interval": "5m"},
            "target": "rest:summary_prod",
            "params": {"rule": "exists", "field": "body.badge"}}}},
        "knowledge": {"root": "knowledge.example"}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("summary_prod" in e.problem for e in errors)


def test_점검의_body가_등재_스키마와_어긋나면_기동을_거부한다(tmp_path):
    # query_rules 모듈 docstring이 "기동 검증도 같은 규칙을 공유한다"고 밝히는데
    # boot이 entry_body_problems를 안 부르면 그 문장이 거짓이 된다. 오타 하나가
    # 매 순찰 error로만 드러나는 것은 Task 7이 미등재 참조를 기동 거부로 올린
    # 논거와 정확히 같은 상황이다.
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", json.dumps({
        "target": {"adapters": "stub", "rest": {
            "base_url": "http://x",
            "entries": {"summary_prod": {"method": "POST", "path": "/summary/prod",
                                         "body_schema": {"part_code": "list[str]"}}}}},
        "patrol": {"checks": {"prod.badge": {
            "judge": "rule", "schedule": {"interval": "5m"},
            "target": "rest:summary_prod",
            "params": {"rule": "exists", "field": "body.badge",
                       "body": {"save_as": "x"}}}}},      # 스키마 밖 키
        "knowledge": {"root": "knowledge.example"}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("save_as" in e.problem for e in errors)


def _site_with_resolve(resolve, extra_entries=None):
    entries = {"e": {"method": "POST", "path": "/x",
                     "body_schema": {"part_code": "list[str]"}}}
    entries.update(extra_entries or {})
    return json.dumps({
        "target": {"adapters": "stub", "rest": {"base_url": "http://x", "entries": entries}},
        "patrol": {"checks": {"c": {
            "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:e",
            "params": {"rule": "exists", "field": "body"}, "resolve": resolve}}},
        "knowledge": {"root": "knowledge.example"}})


def test_해석기가_없는_항목을_가리키면_기동을_거부한다(tmp_path):
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", _site_with_resolve(
        {"part_code": {"from": "rest", "entry": "없는항목", "field": "part_code"}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("없는항목" in e.problem for e in errors)


def test_스키마에_없는_키를_해석하면_기동을_거부한다(tmp_path):
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", _site_with_resolve(
        {"없는키": {"from": "unfiltered"}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("없는키" in e.problem for e in errors)


def test_조회용_해석기_항목이_POST면_기동을_거부한다(tmp_path):
    # 값을 얻으려고 부수효과 가능성이 있는 메서드를 쓰면 안 된다.
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", _site_with_resolve(
        {"part_code": {"from": "rest", "entry": "lister", "field": "part_code"}},
        extra_entries={"lister": {"method": "POST", "path": "/list",
                                  "body_schema": {"q": "str"}}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("lister" in e.problem and "GET" in e.problem for e in errors)


def test_정상_해석기는_기동을_막지_않는다(tmp_path):
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", _site_with_resolve(
        {"part_code": {"from": "rest", "entry": "lister", "field": "part_code"}},
        extra_entries={"lister": {"method": "GET", "path": "/list",
                                  "query_schema": {"q": "str"}}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert not [e for e in errors if "part_code" in e.problem or "lister" in e.problem]


def test_등재_항목이_아닌_target에_해석기를_달면_기동을_거부한다(tmp_path):
    # resolve는 rest_query에서만 실행된다. 다른 target에 달면 런타임이 조용히
    # 무시해, 사람이 "범위를 좁혔다"고 믿는 점검이 무필터 전체 스캔을 돈다 —
    # §2-N3이 "거짓 안심, 조용해서 더 위험"이라 지목한 경로다.
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", json.dumps({
        "target": {"adapters": "stub", "mongo": {"url": "mongodb://x"},
                   "rest": {"base_url": "http://x"}},
        "patrol": {"checks": {"c": {
            "judge": "rule", "schedule": {"interval": "5m"},
            "target": "mongo:twin_state",
            "params": {"rule": "exists", "field": "x"},
            "resolve": {"k": {"from": "clock", "expr": "today"}}}}},
        "knowledge": {"root": "knowledge.example"}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("resolve" in e.problem and "등재 항목" in e.problem for e in errors)


def test_해석기가_쓰는_어댑터가_없으면_기동을_거부한다(tmp_path):
    # 정적으로 알 수 있는데 매 순찰 "어댑터 미설정" error로만 드러나면 안 된다.
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", _site_with_resolve(
        {"part_code": {"from": "mongo", "collection": "lines", "field": "part_code"}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("mongo" in e.problem and "part_code" in e.problem for e in errors)


def test_알_수_없는_시간대는_기동을_거부한다(tmp_path):
    # app.timezone은 스케줄러와 clock 해석기가 둘 다 쓴다. 오타 하나면 매 점검이
    # 죽는데 기동은 통과하던 상태였다.
    _tree(tmp_path)
    _write(tmp_path, "config/app.json", json.dumps(
        {"llm": {"profiles": {"judge": "a", "subagent": "b", "lead": "c"}},
         "timezone": "Asia/서울"}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("timezone" in e.problem for e in errors)


def test_probe를_명시해도_해석기_검증을_우회할_수_없다(tmp_path):
    # resolve_probe가 check.probe를 그대로 돌려주므로, probe만 박으면 target 모양
    # 검사가 통째로 비껴간다 — 등재 항목 이름 위장을 막은 것과 같은 계열이다.
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", json.dumps({
        "target": {"adapters": "stub", "mongo": {"url": "mongodb://x"},
                   "rest": {"base_url": "http://x"}},
        "patrol": {"checks": {"c": {
            "judge": "rule", "schedule": {"interval": "5m"},
            "target": "mongo:twin_state", "probe": "rest_query",
            "params": {"rule": "exists", "field": "x"},
            "resolve": {"없는키": {"from": "unfiltered"}}}}},
        "knowledge": {"root": "knowledge.example"}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("resolve" in e.problem for e in errors)


def test_real_어댑터에_스텁_시드가_남아있으면_기동을_거부한다(tmp_path):
    # 조용히 무시되면 운영자가 "테스트용 값이 살아 있나?" 하고 헷갈린다.
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", json.dumps({
        "target": {"adapters": "real", "redis": {"url": "redis://x"},
                   "stub_seeds": {"rest_responses": {"/x": {"a": 1}}}},
        "patrol": {"checks": {}},
        "knowledge": {"root": "knowledge.example"}}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("stub_seeds" in e.problem for e in errors)


def test_해석기_결과_모양이_스키마와_어긋나면_기동을_거부한다(tmp_path):
    # clock 해석기는 항상 문자열 하나, 소스 해석기는 항상 리스트다. 스키마와
    # 어긋나면 매 순찰이 "body 필드 X는 list[str]여야 한다"로 끝난다 —
    # 정적으로 알 수 있는 것을 런타임 반복 error로 미루지 않는다.
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", _site_with_resolve(
        {"part_code": {"from": "clock", "expr": "today"}}))   # 스키마는 list[str]
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("part_code" in e.problem and "clock" in e.problem for e in errors), errors


def test_config에는_더_이상_stub_seeds를_쓸_수_없다():
    # 표면 자체를 없앴으므로 StrictModel이 거부한다 — 검증할 것이 없으면 기동
    # 검증 항목도 지운다.
    import pytest
    from pydantic import ValidationError
    from src.config.schema_site import SiteConfig
    with pytest.raises(ValidationError):
        SiteConfig.model_validate({"target": {"stub_seeds": {"rest_responses": {}}}})


def _pin(tmp_path, spec):
    _write(tmp_path, "knowledge/target_api/mx/gumi.json", json.dumps(spec))


_SPEC = {"paths": {"/summary/prod": {"post": {
    "requestBody": {"content": {"application/json": {"schema": {
        "type": "object", "required": ["date"],
        "properties": {"part_code": {"type": "array", "items": {"type": "string"}},
                       "date": {"type": "string"}}}}}},
    "responses": {"200": {"content": {"application/json": {"schema": {
        "type": "object", "properties": {"badge": {"type": "array",
                                                   "items": {"type": "integer"}}}}}}}}}}}}


def _site_with_entry(body, field="body.badge"):
    return json.dumps({
        "target": {"adapters": "stub", "code": {"repos": [{"name": "twin-services", "path": "/r"}]},
                   "rest": {"base_url": "http://x", "entries": {"summary_prod": {
                       "method": "POST", "path": "/summary/prod", "body_schema": body}}}},
        "patrol": {"checks": {"c": {
            "judge": "rule", "schedule": {"interval": "5m"}, "target": "rest:summary_prod",
            "params": {"rule": "exists", "field": field}}}},
        "knowledge": {"root": "knowledge"}})


def test_명세와_어긋난_등재_항목은_기동을_거부한다(tmp_path):
    # 오타 하나가 매 순찰 404로만 드러나던 것을 배포 시점으로 당긴다.
    _tree(tmp_path)
    _pin(tmp_path, _SPEC)
    _write(tmp_path, "config/gbm/mx.json",
           _site_with_entry({"part_code": "list[str]", "save_as": "str", "date": "str"}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("save_as" in e.problem for e in errors), errors


def test_명세가_말한_응답에_없는_필드를_보는_점검을_거부한다(tmp_path):
    _tree(tmp_path)
    _pin(tmp_path, _SPEC)
    _write(tmp_path, "config/gbm/mx.json",
           _site_with_entry({"date": "str"}, field="body.badgee"))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path)
    assert any("badgee" in e.problem for e in errors), errors


def test_명세와_맞으면_통과한다(tmp_path):
    _tree(tmp_path)
    _pin(tmp_path, _SPEC)
    _write(tmp_path, "config/gbm/mx.json", _site_with_entry({"date": "str"}))
    assert validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path) == []


def test_pinned_명세가_없으면_그것만으로는_기동을_막지_않는다(tmp_path):
    # 명세를 못 얻는 대상도 있다. 없는 것은 오류가 아니다 — 깨진 것이 오류다.
    _tree(tmp_path)
    assert not any("명세" in e.problem for e in
                   validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path))


def test_깨진_pinned_명세는_기동을_거부한다(tmp_path):
    _tree(tmp_path)
    _write(tmp_path, "knowledge/target_api/mx/gumi.json", "{ 망가진 json")
    assert any("명세" in e.problem for e in
               validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path))


def _live_tree(tmp_path, live_spec, *, body=None):
    """--live 드리프트 점검용 트리 — 스텁 어댑터에 '지금 대상의 명세'를 심는다."""
    _tree(tmp_path)
    _pin(tmp_path, _SPEC)
    _write(tmp_path, "config/gbm/mx.json", _site_with_entry(body or {"date": "str"}))
    return {"mx/gumi": StubSeeds(rest_openapi=live_spec)} if live_spec is not None else {}


def test_라이브_명세가_pin과_같으면_조용하다(tmp_path):
    seeds = _live_tree(tmp_path, _SPEC)
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path,
                           check_live=True, stub_seeds=seeds)
    assert errors == [], errors


def test_라이브_명세가_등재_항목에_영향을_주면_잡는다(tmp_path):
    # 대상이 date를 지웠다 — 우리 등재 스키마가 그 키를 보내고 있다.
    live = {"paths": {"/summary/prod": {"post": {"requestBody": {"content": {
        "application/json": {"schema": {"type": "object", "properties": {
            "part_code": {"type": "array", "items": {"type": "string"}}}}}}}}}}}
    seeds = _live_tree(tmp_path, live)
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path,
                           check_live=True, stub_seeds=seeds)
    assert any("date" in e.problem for e in errors), errors


def test_우리_항목_밖의_변화는_pin_갱신만_요구한다(tmp_path):
    # 대상 API는 우리가 안 쓰는 끝점이 수백 개다. 전부 보고하면 아무도 안 읽는다.
    live = {**_SPEC, "paths": {**_SPEC["paths"], "/other": {"get": {}}}}
    seeds = _live_tree(tmp_path, live)
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path,
                           check_live=True, stub_seeds=seeds)
    assert len(errors) == 1 and "pin" in errors[0].problem, errors
    assert "/other" not in errors[0].problem       # 차이 전체를 쏟지 않는다


def test_라이브_명세를_못_받아도_기동을_막지_않는다(tmp_path):
    # "죽은 사이트가 기동을 막으면 역효과" — Mongo 롤 검사와 같은 자리의 원칙.
    seeds = _live_tree(tmp_path, None)
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path,
                           check_live=True, stub_seeds=seeds)
    assert errors == [], errors


def test_pin이_없으면_라이브_대조를_하지_않는다(tmp_path):
    # 견줄 대상이 없다. 명세를 받아 오는 것 자체가 목적이 아니다.
    _tree(tmp_path)
    _write(tmp_path, "config/gbm/mx.json", _site_with_entry({"date": "str"}))
    errors = validate_boot(tmp_path / "config", env=dict(ENV), repo_root=tmp_path,
                           check_live=True, stub_seeds={"mx/gumi": StubSeeds(rest_openapi=_SPEC)})
    assert errors == [], errors
