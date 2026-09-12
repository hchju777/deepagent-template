"""사내(Windows)에서만 깨지는 코드를 여기서 막는다.

## 왜 이 파일이 있는가

Windows에서 파이썬의 `open()` 기본 인코딩은 **로케일**이다 — 한국어 Windows면
`cp949`. 우리 config에도 보고서에도 한국어가 들어가므로 `encoding="utf-8"`을
빠뜨린 파일 접근은 **사내에서만** 터진다. 개발은 Linux/macOS에서 하고 운영은
Windows에서 하는 이 프로젝트에서, 그 실패는 "Linux CI는 전부 초록인데 사내에
배포하면 UnicodeDecodeError"라는 제일 나쁜 모양으로 나타난다.

"인코딩을 명시하자"는 산문 규율은 읽지 않으면 무력하다. 그래서 테스트가 지킨다.

## 왜 문자열 검색이 아니라 AST인가

`grep "open("`은 주석과 문자열 안의 `open(`도 잡고, 줄바꿈으로 이어진 호출은
놓친다. `ast`로 파싱하면 **실제 호출 노드**만 보므로 오탐도 누락도 없다.
"""
import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 인코딩을 명시해야 하는 호출들. read_bytes/write_bytes는 텍스트가 아니므로 제외.
_TEXT_IO = {"open", "read_text", "write_text"}


def _python_files():
    for base in ("src", "tests"):
        yield from sorted((PROJECT_ROOT / base).rglob("*.py"))


def _is_binary_mode(node: ast.Call) -> bool:
    """open(path, "rb") 처럼 바이너리 모드면 encoding 인자를 받지 않는다."""
    mode = None
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        mode = node.args[1].value
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return isinstance(mode, str) and "b" in mode


def _called_name(node: ast.Call) -> str | None:
    """open(...) 은 Name, path.read_text(...) 는 Attribute."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _label(path: Path) -> str:
    """실패 메시지에 쓸 경로. 프로젝트 밖(테스트의 tmp_path)이면 그대로 쓴다 —
    relative_to는 subpath가 아니면 ValueError를 던지고, 그러면 검사기 자신을
    검증하는 테스트가 죽는다(실제로 그렇게 죽었다).
    """
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def encoding_violations(path: Path) -> list[str]:
    """이 파일에서 encoding을 빠뜨린 텍스트 파일 접근들."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name not in _TEXT_IO or _is_binary_mode(node):
            continue
        if not any(kw.arg == "encoding" for kw in node.keywords):
            found.append(f"{_label(path)}:{node.lineno} — {name}() 에 encoding= 이 없다")
    return found


def test_텍스트_파일_접근에는_인코딩이_명시돼_있다():
    violations = [v for path in _python_files() for v in encoding_violations(path)]
    assert not violations, (
        "Windows(cp949)에서만 깨질 파일 접근이 있다 — encoding=\"utf-8\"을 붙여라:\n  "
        + "\n  ".join(violations))


# ── 위 검사가 실제로 잡는지 (검사기 자신의 테스트) ────────────────────

def test_검사기가_누락을_실제로_잡는다(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from pathlib import Path\nPath('a.json').read_text()\n", encoding="utf-8")
    assert encoding_violations(bad), "누락을 못 잡으면 이 파일 전체가 무의미하다"


def test_검사기가_정상_코드를_오탐하지_않는다(tmp_path):
    good = tmp_path / "good.py"
    good.write_text(
        "from pathlib import Path\n"
        "Path('a.json').read_text(encoding='utf-8')\n"
        "open('b.bin', 'rb')\n"          # 바이너리는 encoding을 안 받는다
        "Path('c.png').read_bytes()\n"   # 텍스트가 아니다
        "# open('d.txt') 는 주석이라 호출이 아니다\n",
        encoding="utf-8")
    assert encoding_violations(good) == []


# ── 테스트 트리의 import 구조 ────────────────────────────────────────
# 여기 두는 이유는 위와 같다: **환경에 따라서만 터지는 실패**이고, 산문 규율로는
# 못 막는다. 사내에서 실제로 이렇게 났다:
#
#   ImportError: attempted relative import beyond top-level package
#
# pytest는 `__init__.py`가 있는 디렉터리까지 거슬러 올라가 모듈의 패키지 이름을
# 정한다. `tests/__init__.py` 하나가 빠지면 `tests/presentation/test_x.py`는
# `presentation.test_x`가 되고, 그 안의 `..report.conftest`는 최상위를 넘는다.
# 절대 import는 같은 조건에서도 동작한다(`ops-agent/`가 sys.path에 있으므로
# `tests`가 암묵적 namespace package로 import된다) — 그래서 상대 import를 금지한다.

def _test_modules() -> list[Path]:
    return sorted(p for p in (PROJECT_ROOT / "tests").rglob("*.py")
                  if "__pycache__" not in p.parts)


def relative_imports(source: str) -> list[str]:
    """`from . import x` / `from ..a import b` 같은 것들. 절대 import만 허용한다."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level:
            dots = "." * node.level
            found.append(f"from {dots}{node.module or ''} import ...")
    return found


def test_테스트_트리에_상대_import가_없다():
    offenders = {}
    for path in _test_modules():
        found = relative_imports(path.read_text(encoding="utf-8"))
        if found:
            offenders[str(path.relative_to(PROJECT_ROOT))] = found
    assert not offenders, (
        f"테스트에 상대 import가 있다: {offenders}\n"
        "`tests/__init__.py`가 없는 환경에서 수집 자체가 실패한다 "
        "(attempted relative import beyond top-level package). "
        "`from tests.support import ...`처럼 절대 경로로 써라.")


def test_검사기가_상대_import를_실제로_잡는다():
    assert relative_imports("from ..report.conftest import doc") == \
        ["from ..report.conftest import ..."]
    assert relative_imports("from .conftest import doc") == ["from .conftest import ..."]
    assert relative_imports("from tests.support import doc") == []


def test_테스트_디렉터리마다_init이_있다():
    """`__init__.py`가 빠지면 같은 이름의 파일(conftest.py가 여러 개)이 충돌하고,
    pytest가 "import file mismatch"로 죽는다. 빈 파일이라 옮길 때 빠뜨리기 쉽다."""
    missing = []
    for directory in sorted({p.parent for p in _test_modules()}):
        if not (directory / "__init__.py").exists():
            missing.append(str(directory.relative_to(PROJECT_ROOT)))
    assert not missing, f"__init__.py가 없는 테스트 디렉터리 — {missing}"
