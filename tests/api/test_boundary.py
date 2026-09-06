"""`api`는 대상 시스템에 붙지 않고 조사를 시작하지 않는다 (스펙 §3.1).

산문 규율은 읽지 않으면 무력하다 — 규율 9가 포트 표면을 테스트로 지키는 것과 같은
형태로, import 그래프를 테스트가 지킨다. `src/api/` 아래 어느 파일이든 어댑터
팩토리나 워커를 import하면 `api` 풀 전체가 실행자가 된다.
"""
import ast
import pathlib

_FORBIDDEN = {
    "src.infrastructure.factory", "src.infrastructure.redis_reader",
    "src.infrastructure.mongo_reader", "src.infrastructure.kafka_inspector",
    "src.infrastructure.rest_prober", "src.infrastructure.code_repo",
    "src.infrastructure.stubs",
    "src.patrol.daemon", "src.application.worker", "src.application.graph",
}


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_api는_대상_시스템_어댑터와_워커를_import하지_않는다():
    files = list(pathlib.Path("src/api").rglob("*.py"))
    assert files, "src/api가 없다"
    for path in files:
        hit = _FORBIDDEN & _imports(path)
        assert not hit, f"{path}: {sorted(hit)}"
