"""**두 곳에 적힌 단계 목록이 갈리는 것**을 막는다.

## 왜 이 파일이 있는가

단계 번호가 README와 step-00에서 실제로 갈렸다. 조사 그래프가 한쪽은 8, 다른 쪽은
10이었다 — 8·9단계(메일·리포트)가 사업 우선순위로 중간에 끼면서 뒤가 두 칸씩 밀렸는데
한쪽만 고쳐졌다.

번호가 어긋난 것보다 나쁜 일이 그 사이에 있었다. **step-00의 12단계 "보고서와
이벤트"가 README 표에서 통째로 사라졌다.** 조사 결과가 사람에게 닿는 경로가 번호를
못 받은 채로 한동안 있었고, 아무도 못 알아챘다 — 두 표 중 어느 쪽도 자기가 빠뜨린
것을 모르기 때문이다.

"두 표를 맞춰라"라고 산문으로 적으면 또 갈린다. 사람이 두 곳을 손으로 맞춰야 하는
구조 자체가 원인이므로, 어긋나는 순간을 테스트가 잡는다.
"""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# **템플릿 리포에만 있는 파일.** 사내 트리는 지워도 된다 — 운영은 `.env`만 둔다.
# 없다고 빨간불을 켜면 사람이 테스트를 고쳐 쓰게 되고, 그러면 그 테스트는 죽는다.
TEMPLATE_ONLY = {".env.example"}
README = PROJECT_ROOT / "README.md"
OVERVIEW = PROJECT_ROOT / "STEPS" / "step-00-overview.md"

# 두 파일에서 같은 머리말을 쓴다 — 이걸로 표를 찾는다. 세 번째 칸은 서로 다르다
# (README는 상태, step-00은 "끝나면 할 수 있는 것")이므로 앞 두 칸만 본다.
_HEADER = "| 단계 | 만드는 것 |"
_SEPARATOR = re.compile(r"^\|[\s|:-]+\|$")


def _steps(path: Path) -> list[tuple[str, str]]:
    """마크다운 표에서 (단계, 만드는 것) 쌍을 순서대로 뽑는다."""
    rows: list[tuple[str, str]] = []
    inside = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(_HEADER):
            inside = True
            continue
        if not inside:
            continue
        if _SEPARATOR.match(line):
            continue
        if not line.startswith("|"):
            break                      # 표가 끝났다
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append((cells[0], cells[1]))
    return rows


def test_README와_step00의_단계_목록이_같다():
    overview = _steps(OVERVIEW)
    readme = _steps(README)
    assert overview, f"{OVERVIEW.name}에서 로드맵 표를 못 찾았다 — 머리말이 바뀌었나"
    assert readme, f"{README.name}에서 진행 표를 못 찾았다 — 머리말이 바뀌었나"
    # 순서까지 본다. 순서가 다르면 실행 순서를 읽는 사람이 헷갈린다.
    assert overview == readme


def test_완료로_표시된_단계에는_STEPS_문서가_있다():
    """✅인데 문서가 없는 단계를 잡는다.

    "돌아가는 것으로 끝난다"는 원칙의 짝이다 — 만들었으면 왜 그렇게 만들었는지가
    남아야 다음 사람이 그 위에 얹을 수 있다. 0단계는 로드맵 자체라 예외.
    """
    missing = []
    for line in README.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "✅" not in line:
            continue
        step = line.strip().strip("|").split("|")[0].strip()
        # "9g" → "step-09g-", "3a" → "step-03a-", "7" → "step-07-"
        digits = "".join(c for c in step if c.isdigit())
        suffix = "".join(c for c in step if c.isalpha())
        if not digits:
            continue
        if not list((PROJECT_ROOT / "STEPS").glob(f"step-{int(digits):02d}{suffix}-*.md")):
            missing.append(step)
    assert not missing, f"완료 표시인데 STEPS 문서가 없는 단계: {missing}"


def test_문서의_내부_링크가_실재한다():
    """`[...](step-xx.md)`가 가리키는 파일이 실제로 있는가.

    셋이 깨져 있었다. 전부 "→ 다음:" 링크였고, **쓸 당시의 계획을 가리키고 있었다** —
    3단계는 나중에 3a/3b로 쪼개졌고, 3b 다음은 4단계가 아니라 7단계였다. 실행 순서가
    바뀔 때 뒤를 가리키던 링크는 아무도 다시 안 본다.

    깨진 링크 자체는 작은 문제지만, 그게 **가리키는 내용도 틀렸다**는 신호다.
    """
    broken = []
    for md in [README] + sorted((PROJECT_ROOT / "STEPS").glob("*.md")):
        for text, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)",
                                       md.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "#")):
                continue
            if target.split("#")[0] in TEMPLATE_ONLY:
                continue
            if not (md.parent / target.split("#")[0]).resolve().exists():
                broken.append(f"{md.name}: [{text}]({target})")
    assert not broken, "가리키는 파일이 없는 링크:\n  " + "\n  ".join(broken)
