"""R3-5 ④ — 정답 홉 표(측정 #11 뒤 사람이 준 c-1·c-2의 표)를 측정판 변형으로 세우고 `case walk`(LLM 없음)로 돌려 대조한다.

트리는 **테스트가 세운다** — 리포의 `config/`·`knowledge/`는 사내에서 실제 값으로 바뀌는 칸이라 베끼면 사내 테스트가 깨진다(`test_cli_code`와 같은
이유). 대상 레포 둘의 파일은 `tools/local_case.py`의 것 그대로, 시드는 그 `_seeds` 그대로. graphify는 없는 것으로 둔다(오버레이만으로 번들이 선다).
"""
import io
import json
import re
import shutil
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime

import pytest

from tests.config.test_boot import ENV, _with_patrol
from tests.support import git
from tools.local_case import API_FILES, BOARD_SERVICES, CORE_FILES, _seeds

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git이 없다")

PROBES = {"badge": {"action": "rest.query", "params": {"entry": "summary_badge", "params": {}}}}
URLS = {"dt-core": "https://git.example.com/team/dt-core", "dt-api": "https://git.example.com/team/dt-api"}
# 측정판 레포의 config 층 자리(`CORE_FILES`·`API_FILES`의 `config/…`)와 흐름 출처 — 이 테스트의 것이다.
TOPOLOGY = {"services": BOARD_SERVICES,
            "config_paths": ["config/gbm/{gbm}.json", "config/factories/{fct}/common.json", "config/factories/{fct}/{gbm}.json"],
            "flow": {"sources": [
                {"path": "infra.kafka.consumer.topic", "kind": "topic", "relation": "consumes"},
                {"path": "infra.kafka.producer.topic", "kind": "topic", "relation": "produces"},
                {"path": "infra.kafka.consumer.group_id", "kind": "group", "relation": "consumes_as"},
                {"path": "mongodb_collection", "kind": "collection"},
                {"path": "redis_key", "kind": "rediskey", "prefix": "prefix", "join": ":"}]}}
VARIANTS = ("chain-empty", "chain-filtered", "healthy", "chain-objects", "palette-unread")


def _repo(root, url, files):
    root.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "t@t", cwd=root)
    git("config", "user.name", "t", cwd=root)
    git("remote", "add", "origin", url, cwd=root)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-qm", "board", cwd=root)


def _cli(*argv):
    from src.__main__ import main

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


@pytest.fixture(scope="module")
def board(tmp_path_factory):
    root = tmp_path_factory.mktemp("board")
    _repo(root / "target-code" / "dt-core", URLS["dt-core"], CORE_FILES)
    _repo(root / "target-code" / "dt-api", URLS["dt-api"], API_FILES)
    config = _with_patrol(root / "config", PROBES)
    app = json.loads((config / "app.json").read_text(encoding="utf-8"))
    app.update(output_dir=str(root / "out"), case_store=str(root / "out" / "cases.json"))
    (config / "app.json").write_text(json.dumps(app), encoding="utf-8")
    layer = json.loads((config / "gbm" / "mx.json").read_text(encoding="utf-8"))
    layer["code"] = {"repos": [{"name": n, "url": u, "path": str(root / "target-code" / n)} for n, u in URLS.items()]}
    (config / "gbm" / "mx.json").write_text(json.dumps(layer, ensure_ascii=False), encoding="utf-8")
    (root / "knowledge" / "topology").mkdir(parents=True)
    (root / "knowledge" / "topology" / "mx.json").write_text(json.dumps(TOPOLOGY, ensure_ascii=False), encoding="utf-8")
    now = datetime(2026, 10, 15, 9, 0, 0)
    for variant in VARIANTS:
        (root / f"{variant}.json").write_text(json.dumps(_seeds(now, variant), ensure_ascii=False), encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        for key, value in ENV.items():
            mp.setenv(key, value)
        mp.setenv("GRAPHIFY_BIN", str(root / "없는-graphify"))
        code, out = _cli("--config-root", str(config), "--env-file", str(root / "none"), "code", "graph", "--gbm", "mx", "--fct", "gumi")
    assert code == 0, out
    return root


def _out(board, monkeypatch, variant, target="L1/Alarm"):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    code, out = _cli("--config-root", str(board / "config"), "--env-file", str(board / "none"), "case", "walk", "--target", target,
                     "--check", "badge_all_zero", "--stub-seeds", str(board / f"{variant}.json"), "--gbm", "mx", "--fct", "gumi")
    assert code == 0, out
    return out


def _walk(board, monkeypatch, variant, target="L1/Alarm"):
    """`case walk --target <대상>`의 출력 → (행들 — 증거 칸 뺌, 주들, 멈춤, 칸들)."""
    out = _out(board, monkeypatch, variant, target)
    lines = out.splitlines()
    head = next((i for i, line in enumerate(lines) if line.startswith("걷기(코드가 상류를 걸었다)")), None)
    assert head is not None, out
    rows, notes = [], []
    for line in lines[head + 2:]:
        if line.startswith("  주: "):
            notes.append(line[len("  주: "):])
        elif line.startswith("  ") and " | " in line:
            rows.append(line.strip().rsplit(" | ", 1)[0])
        else:
            break
    rungs = []
    at = next((i for i, line in enumerate(lines) if line.startswith("  칸(")), len(lines))
    for line in lines[at + 1:]:
        m = re.match(r"^\s+\d+\. (.+)$", line)
        if m is None:
            break
        rungs.append(m.group(1))
    return rows, notes, lines[head].split(" — 멈춤: ", 1)[1], rungs


def _at(path, needle):
    found = [n for n, line in enumerate(CORE_FILES[path].splitlines(), 1) if needle in line]
    assert len(found) == 1, (path, needle, found)
    return found[0]


SOURCE_ROW = ("alarm:source:L1 | — | 정상 — 행 4 · 필드 3 · 모든 행이 같은 값: unit='ea' · 단일값 unit='ea' — 거르는 줄 없음(1/1 대조, 2파일, "
              "이은 컴포넌트 state_filter)")
PALETTE_ROW = "alarm:palette | PaletteLoader [api, dt-core(서비스 3개 공유)] | 정상 — colors: 행 2 · 필드 2"


def test_c1형_빈_배열_사슬은_메시지_입력_원천_클래스에서_입력을_모른다로_멈추고_다음_칸은_그_클래스와_이은_컴포넌트의_본문이다(board, monkeypatch):
    """정답 표 c-1: 요약 빈 배열 ← 도메인 0행 ← 원천 0행. 다른 입력은 전부 정상(가짜 나쁨 0). 원천의 쓴 쪽은 레포 둘에 있고 키를 선언한 쪽(처리기
    레포, 서비스 공유)으로 고른다. 멈춤은 "입력을 모른다"(인덱스 자원 0), 다음 칸은 그 클래스 본문 → 이은 컴포넌트 본문(그 안의 비교식).
    원천 키의 단일값 통과가 두 레포 가르기보다 먼저다 — 측정 #11 P0 1(중첩 함수 가림)의 순서."""
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "chain-empty")
    assert rows == ["alarm:stats:L1 | SummaryRefresh [batch] | 빈 배열 (record[0].data)",
                    "alarm:domain:L1 | DomainBuilder [batch] | 빈 배열 (record)",
                    PALETTE_ROW, SOURCE_ROW,
                    "alarm:raw:L1 | SourceCollector [dt-core(서비스 3개 공유)] | 빈 배열 (rows)"]
    assert notes == ["SourceCollector는 레포 2곳(api, dt-core(서비스 3개 공유))에 있다 — alarm:raw:{line}을 선언한 dt-core(서비스 3개 공유)로 골랐다"]
    assert stopped == "입력을 모른다 — SourceCollector(인덱스 자원 0 — 메시지 입력일 수 있다)"
    assert rungs == [f"code.read line={_at('common/source.py', 'class SourceCollector')} path='common/source.py' service='batch'",
                     f"code.read line={_at('common/parsers.py', 'class RowParser')} path='common/parsers.py' service='batch'"]


def test_c2형_단일값_사슬은_비교식_줄을_사실로_적고_원천_클래스의_입력이_다_정상이면_멈추며_뒤_칸은_나쁜_필드가_든_컴포넌트가_먼저다(board, monkeypatch):
    """정답 표 c-2: 요약 빈 배열 ← 도메인 나쁨(행 전부 state='-' wg='-', 그 값이 든 비교식 줄 — 원인 말 없음) ← 원천 나쁨(같은 단일값) ← 입력 셋 정상.
    나쁜 필드는 같은 값인 문자열 일곱 필드 뒤(상한 없음), 비교식은 이 사이트의 등록(사이트 인자·점 표기)이 가리키는 클래스 파일에 있고 다른 사이트
    전용 클래스 파일은 범위 밖. 다음 칸: 그 줄의 함수 → 원천 클래스의 읽는 메서드 → 이은 컴포넌트 본문(나쁜 필드 이름이 든 것 먼저, 여러 줄 등록)."""
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "chain-filtered")
    n = _at("batch/components.py", 'r.get("state") != "-"')
    text = CORE_FILES["batch/components.py"].splitlines()[n - 1].strip()
    assert rows == ["alarm:stats:L1 | SummaryRefresh [batch] | 빈 배열 (record[0].data)",
                    f"alarm:domain:L1 | DomainBuilder [batch] | 나쁨 — 5행 전부 state='-' wg='-' · 이 필드 값이 든 비교식 batch/components.py:L{n} '{text}'"
                    " · 대조 안 함 1개(문자열 아님)",
                    PALETTE_ROW, SOURCE_ROW,
                    "alarm:raw:L1 | SourceMerger [dt-core(서비스 3개 공유)] | 나쁨 — 같은 단일값 state='-' (아래 홉과 같다)",
                    "feed:events:L1 | — | 정상 — 행 4 · 필드 2",
                    "feed:ref | — | 정상 — codes: 행 3 · 필드 2",
                    "feed:status:L1 | — | 정상 — 행 4 · 필드 2"]
    assert notes == []
    assert stopped == f"입력이 다 정상 — SourceMerger (단일값이 든 비교식 batch/components.py:L{n})"
    assert rungs == [f"code.read line={n} path='batch/components.py' service='batch'",
                     f"code.read line={_at('common/merge.py', 'feed_events')} path='common/merge.py' service='batch'",
                     f"code.read line={_at('common/fillers.py', 'class RowFiller')} path='common/fillers.py' service='batch'",
                     f"code.read line={_at('common/joiners.py', 'class RefJoiner')} path='common/joiners.py' service='batch'"]


def test_정상_사슬은_가짜_나쁨이_없다(board, monkeypatch):
    """측정 #12의 나머지 행(가짜 나쁨 0)의 대역 — 같은 코드, 정상 시드. 단일값 검사는 돌지만(원천 키 unit='ea') 다른 사이트 전용 클래스의 비교식에
    안 걸린다."""
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "healthy")
    assert not [r for r in rows if re.search(r"\| (나쁨|미확인|없음|0행|빈 배열|읽기 실패)", r)], rows
    assert SOURCE_ROW in rows and stopped == "입력이 다 정상 — SummaryRefresh" and rungs


def test_제목에_슬래시가_든_행도_입구가_고르고_잘린_키의_쓴_쪽은_전체_값에서_찾아_걷는다(board, monkeypatch):
    """측정 #12(R3-5b ①): 행 9는 입구가 대상 문자열을 '/'로 쪼개 "대상 행이 없다"였고, data가 커서 좁혀 다시 읽은 16행은 쓴 쪽 이름(data의 형제
    자리)을 못 봐 "쓴 쪽: 모른다"였다 — 걷기가 c-1·c-2에서만 돌았다. 대상 키 줄의 `summary_info`(부모 `record[0]`로 좁힌 값 안의 자리)가 잘린
    키라는 표시다."""
    out = _out(board, monkeypatch, "healthy", "L1/Up/Down Rate")
    assert "대상 키: alarm:rate:L1 — 키 값의 summary_info에 대상 identity(L1/Up/Down Rate)가 그대로 있다 (t-1.e1)" in out
    assert "쓴 쪽: RateRefresh — 값의 metadata.event_type과 인덱스의 클래스 이름이 완전 일치" in out
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "healthy", "L1/Up/Down Rate")
    assert rows == ["alarm:rate:L1 | RateRefresh [batch] | 정상",
                    "alarm:source:L1 | — | 정상 — 행 4 · 필드 3 · 모든 행이 같은 값: unit='ea' · 단일값 unit='ea' — 거르는 줄 없음(1/1 대조, 1파일)"]
    assert stopped == "입력이 다 정상 — RateRefresh" and rungs



def test_빈_객체_입력과_자기_출력뿐인_원천_클래스는_사실대로_적는다(board, monkeypatch):
    """측정 #12(R3-5b ②): `record {}`는 빈 객체(나쁨)이고, 읽는 자원이 자기 출력뿐인 클래스는 "입력이 다 정상"이 아니라 "입력을 모른다"."""
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "chain-objects")
    assert rows == ["alarm:stats:L1 | SummaryRefresh [batch] | 빈 배열 (record[0].data)",
                    "alarm:domain:L1 | DomainBuilder [batch] | 빈 객체 (record)",
                    PALETTE_ROW, SOURCE_ROW,
                    "alarm:raw:L1 | RawAppender [dt-core(서비스 3개 공유)] | 빈 배열 (rows)"]
    assert stopped == "입력을 모른다 — RawAppender(인덱스 자원이 자기 출력뿐)"
    assert rungs == [f"code.read line={_at('common/appender.py', 'class RawAppender')} path='common/appender.py' service='batch'"]


def test_입력_읽기가_실패하면_입력이_다_정상이_아니라_다_못_봤다(board, monkeypatch):
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "palette-unread")
    assert [r for r in rows if r.startswith("palette:source")] == ["palette:source | — | 읽기 실패 — redis.get key='palette:source' — WRONGTYPE Operation against a key holding the wrong kind of value"]
    assert stopped == "입력을 다 못 봤다 — PaletteLoader(읽기 실패 palette:source)"
    # 다음 칸은 그 읽기가 든 메서드 — 클래스 머리가 아니다(읽은 입력이 0이어서 "모른다"일 때만 클래스 본문)
    rung = f"code.read line={_at('common/palette.py', 'palette_source')} path='common/palette.py' service='batch'"
    assert rungs == [rung]



def test_key로_댄_키부터_입구_없이_걷는다(board, monkeypatch):
    """측정 #12(R3-5b ③): 배지 밖 끝점은 입구에서 막힌다 — 사람이 키를 대면 그 키부터 걷는다(측정 도구, 읽기 전용). 선언 이름은 흐름 그래프에서."""
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    code, out = _cli("--config-root", str(board / "config"), "--env-file", str(board / "none"), "case", "walk", "--key", "alarm:domain:L1",
                     "--stub-seeds", str(board / "chain-objects.json"), "--gbm", "mx", "--fct", "gumi")
    assert code == 0, out
    assert "대상 키: alarm:domain:L1 — --key로 댄 키 (t-1.e1)" in out and "쓴 쪽: DomainBuilder — 값의 metadata.event_type" in out
    lines = out.splitlines()
    head = next(i for i, line in enumerate(lines) if line.startswith("걷기(코드가 상류를 걸었다)"))
    rows = [line.strip().rsplit(" | ", 1)[0] for line in lines[head + 2:head + 4]]
    assert rows == ["alarm:domain:L1 | DomainBuilder [batch] | 빈 객체 (record)", "alarm:raw:L1 | RawAppender [dt-core(서비스 3개 공유)] | 빈 배열 (rows)"]
    assert lines[head].endswith("멈춤: 입력을 모른다 — RawAppender(인덱스 자원이 자기 출력뿐)") and "읽기 전용" in out



def test_그룹과_제목이_같은_배지는_그_값이_한_번_든_키와_안_맞는다(board, monkeypatch):
    """측정 #12b P0 2(R3-5c ①): 집합 비교로 `ALL/ALL`이 그 값이 한 번 든 미끼 키 셋과 맞아 "3개 — 못 고른다"였다. 개수를 세면 든 키가 없다."""
    out = _out(board, monkeypatch, "healthy", "ALL/ALL")
    assert "중 값에 대상 identity(ALL/ALL)가 든 키가 없다" in out and "하나로 못 좁힌다" not in out
