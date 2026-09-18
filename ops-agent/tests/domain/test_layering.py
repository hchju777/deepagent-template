"""의존 방향을 강제한다 — domain은 바깥 계층을 import하지 않는다.

```
presentation  →  application  →  domain  ←  infrastructure
```

화살표가 전부 domain을 향한다. 이게 지켜지면 도메인 테스트는 Redis도 Mongo도
LLM도 없이 돈다. 한 번 깨지면 되돌리기 어렵다 — domain이 infrastructure를
import하는 순간 그 아래 모든 테스트가 어댑터를 필요로 하게 되고, 그때는 이미
수십 개가 얽혀 있다.

참고로 원본 템플릿(`../src/`)은 `domain/envelope.py`가 `config.schema_app`에서
StrictModel을 가져와 이 규칙이 반쯤 깨져 있다. 우리는 StrictModel을
`domain/base.py`에 두어 domain을 완전히 닫았다.
"""
import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# domain이 의존해도 되는 것: 표준 라이브러리, pydantic, 그리고 domain 자신.
FORBIDDEN_FOR_DOMAIN = ("src.infrastructure", "src.application",
                        "src.presentation", "src.patrol", "src.config")


def _imported_modules(path: Path) -> list[tuple[int, str]]:
    """이 파일이 import하는 모듈 이름들 — (줄번호, 모듈)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module))
    return found


def layering_violations(path: Path, forbidden=FORBIDDEN_FOR_DOMAIN) -> list[str]:
    return [f"{path.name}:{line} — {module}"
            for line, module in _imported_modules(path)
            if module.startswith(forbidden)]


def test_domain은_바깥_계층을_import하지_않는다():
    violations = [v
                  for path in sorted((PROJECT_ROOT / "src" / "domain").rglob("*.py"))
                  for v in layering_violations(path)]
    assert not violations, (
        "domain이 바깥 계층을 import한다 — 이러면 도메인 테스트가 어댑터를 요구하게 된다:\n  "
        + "\n  ".join(violations))


def test_검사기가_위반을_실제로_잡는다(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from src.infrastructure.redis_reader import RealRedis\n", encoding="utf-8")
    assert layering_violations(bad) == ["bad.py:1 — src.infrastructure.redis_reader"]


def test_검사기가_허용된_import를_오탐하지_않는다(tmp_path):
    good = tmp_path / "good.py"
    good.write_text(
        "from datetime import datetime\n"
        "from pydantic import BaseModel\n"
        "from src.domain.base import StrictModel\n",
        encoding="utf-8")
    assert layering_violations(good) == []
