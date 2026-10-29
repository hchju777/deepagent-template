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
VARIANTS = ("chain-empty", "chain-filtered", "healthy", "chain-objects", "palette-unread", "message-empty", "message-gone", "message-blank", "join-empty",
            "join-outer", "source-missing", "source-missing-quiet", "palette-missing", "source-quiet", "palette-source-missing")


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
    # 같은 GBM의 다른 사이트(R3-5t ②) — 없는 키의 쓴 쪽을 그 사이트의 같은 키 값에서 채운다. stub은 시드의 `peers` 절만 읽으니 그 사이트의 층은 없어도 된다.
    (config / "registry.json").write_text(json.dumps({"sites": [{"gbm": "mx", "fct": "gumi"}, {"gbm": "mx", "fct": "sevt"}]}), encoding="utf-8")
    layer = json.loads((config / "gbm" / "mx.json").read_text(encoding="utf-8"))
    layer["code"] = {"repos": [{"name": n, "url": u, "path": str(root / "target-code" / n)} for n, u in URLS.items()]}
    # 포인터 메시지 읽기(R3-5d ④)는 kafka 어댑터가 있어야 한다 — 시드의 토픽을 선언한다(stub은 seeds의 kafka 절로 선다).
    layer["infra"]["kafka"] = {"consumer": {"bootstrap_server": ["kafka.example:9092"], "topic": {"topic1": "mx.alarm.raw", "topic2": "mx.alarm.main"}}}
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


# 기본 StateFilter의 `== "ea"`는 그 값을 남기는 쪽 — 거르는 줄이 아니고 사실 토막만(R3-5l ②, 측정 #22의 틀린 사실 모양).
_KEEP_LINE = _at("batch/components.py", '== "ea"')
SOURCE_ROW = ("alarm:source:L1 | — | 정상 — 행 4 · 필드 3 · 모든 행이 같은 값: unit='ea' · 단일값 unit='ea' — 거르는 줄 없음(1/1 대조, 2파일, "
              f"이은 컴포넌트 state_filter) · == 비교식 batch/components.py:L{_KEEP_LINE} "
              "'return [r for r in rows if r.get(\"unit\") == \"ea\"]' — 그 값을 남기는 쪽이라 안 센다")
# 정상 홉의 쓴 쪽도 키 선언으로 가른다(R3-5d ①-a) — 두 레포 중 alarm:palette을 선언한 쪽.
PALETTE_ROW = "alarm:palette | PaletteLoader [dt-core(서비스 3개 공유)] | 정상 — colors: 행 2 · 필드 2"
PALETTE_NOTE = "PaletteLoader는 레포 2곳(api, dt-core(서비스 3개 공유))에 있다 — alarm:palette을 선언한 dt-core(서비스 3개 공유)로 골랐다"


def test_c1형_빈_배열_사슬은_메시지_입력_원천_클래스에서_입력을_모른다로_멈추고_다음_칸은_그_클래스와_이은_컴포넌트의_본문이다(board, monkeypatch):
    """정답 표 c-1: 요약 빈 배열 ← 도메인 0행 ← 원천 0행. 다른 입력은 전부 정상(가짜 나쁨 0). 원천의 쓴 쪽은 레포 둘에 있고 키를 선언한 쪽(처리기
    레포, 서비스 공유)으로 고른다. 멈춤은 "입력을 모른다"(인덱스 자원 0), 다음 칸은 그 클래스 본문 → 이은 컴포넌트 본문(그 안의 비교식).
    원천 키의 단일값 통과가 두 레포 가르기보다 먼저다 — 측정 #11 P0 1(중첩 함수 가림)의 순서."""
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "chain-empty")
    assert rows == ["alarm:stats:L1 | SummaryRefresh [batch] | 빈 배열 (record[0].data)",
                    "alarm:domain:L1 | DomainBuilder [batch] | 빈 배열 (record)",
                    PALETTE_ROW, SOURCE_ROW,
                    "alarm:raw:L1 | SourceCollector [dt-core(서비스 3개 공유)] | 빈 배열 (rows)"]
    assert notes == [PALETTE_NOTE, "SourceCollector는 레포 2곳(api, dt-core(서비스 3개 공유))에 있다 — alarm:raw:{line}을 선언한 dt-core(서비스 3개 공유)로 골랐다"]
    assert stopped == "입력을 모른다 — SourceCollector(인덱스 자원 0 — 메시지 입력일 수 있다 · 포인터 없음)"
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
    assert notes == [PALETTE_NOTE]
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
    assert stopped == "입력을 모른다 — RawAppender(인덱스 자원이 자기 출력뿐 · 포인터 없음)"
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
    assert lines[head].endswith("멈춤: 입력을 모른다 — RawAppender(인덱스 자원이 자기 출력뿐 · 포인터 없음)") and "읽기 전용" in out



def test_그룹과_제목이_같은_배지는_그_값이_한_번_든_키와_안_맞는다(board, monkeypatch):
    """측정 #12b P0 2(R3-5c ①): 집합 비교로 `ALL/ALL`이 그 값이 한 번 든 미끼 키 셋과 맞아 "3개 — 못 고른다"였다. 개수를 세면 든 키가 없다."""
    out = _out(board, monkeypatch, "healthy", "ALL/ALL")
    assert "중 값에 대상 identity(ALL/ALL)가 든 키가 없다" in out and "하나로 못 좁힌다" not in out



# ── R3-5d ④ — 키를 만든 메시지 한 건(포인터 홉) ──

def test_메시지_변형_빈_메시지는_메시지_홉에서_멈추고_다음_칸은_받는_클래스_본문_이은_컴포넌트_지금_오는_것이다(board, monkeypatch):
    """측정 #13 추가분 2(사람 결정 10-19): 원천 묶음 키 값의 포인터가 가리키는 메시지 한 건이 입력이다 — rows가 비어 거기서 멈춘다. 발행 선언이 없는 토픽은
    우리 밖. 다음 칸: 그 메시지를 받는 클래스 본문 → 이은 컴포넌트 → 지금 토픽에 오는 것."""
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "message-empty")
    assert rows[-2:] == ["alarm:raw:L1 | SourceCollector [dt-core(서비스 3개 공유)] | 빈 배열 (rows)",
                         "mx.alarm.raw[0]@2 | 발행 선언 없음(우리 밖) | 빈 배열 (rows)"]
    assert stopped == "입력 메시지가 나쁘다 — SourceCollector의 mx.alarm.raw[0]@2(발행 선언 없음(우리 밖))"
    assert rungs == [f"code.read line={_at('common/source.py', 'class SourceCollector')} path='common/source.py' service='batch'",
                     f"code.read line={_at('common/parsers.py', 'class RowParser')} path='common/parsers.py' service='batch'",
                     "kafka.tail limit=5 topic='mx.alarm.raw'"]


def test_메시지_변형_지워진_메시지는_키를_만든_메시지가_토픽에_없다이고_발행_선언은_config에서_온다(board, monkeypatch):
    """측정 #13 추가분 2: 오프셋이 보존 범위 밖인 키 둘이 진짜 원인 자리였다. 발행 쪽은 CLI가 흐름 그래프의 config 발행 선언으로 넘긴다(dt-core의 세 서비스)
    — 그 토픽은 사이트 층이 소비·발행 key_path 둘에 같은 값으로 덮은 것이라 발행 선언이 **사이트 행**으로 온다(측정 #15 P0-1: 둘째 행이 엣지 없이 넘어가
    16행 전부 "없음(우리 밖)"이었다). 다음 칸은 지금 토픽에 오는 것이 먼저."""
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "message-gone")
    assert rows[-1] == "gumi.alarm.audit[0]@2 | 발행 선언: batch, processor, sink | 없음 — 키를 만든 메시지가 토픽에 없다(보존 범위 100~105)"
    assert stopped == "키를 만든 메시지가 토픽에 없다 — SourceCollector의 gumi.alarm.audit[0]@2(보존 범위 100~105 · 발행 선언: batch, processor, sink)"
    assert rungs[0] == "kafka.tail limit=5 topic='gumi.alarm.audit'" and len(rungs) == 3


def test_메시지_변형_행이_전부_자리_표시면_파서의_비교식_줄이_첫_칸이다(board, monkeypatch):
    """사내 `<작업자 배지>`의 모양 — 메시지는 오는데 한 필드가 전원 빈 값이라 처리기가 거른다. 단일값 판정은 키 값과 같고, 그 줄의 함수가 첫 칸이다."""
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "message-blank")
    n = _at("common/parsers.py", 'row.get("ws") != "-"')
    text = CORE_FILES["common/parsers.py"].splitlines()[n - 1].strip()
    assert rows[-1] == f"mx.alarm.raw[0]@2 | 발행 선언 없음(우리 밖) | 나쁨 — 5행 전부 ws='-' · 이 필드 값이 든 비교식 common/parsers.py:L{n} '{text}'"
    assert stopped == f"입력 메시지가 나쁘다 — SourceCollector의 mx.alarm.raw[0]@2(발행 선언 없음(우리 밖)) (단일값이 든 비교식 common/parsers.py:L{n})"
    assert rungs == [f"code.read line={n} path='common/parsers.py' service='batch'",
                     f"code.read line={_at('common/source.py', 'class SourceCollector')} path='common/source.py' service='batch'",
                     f"code.read line={_at('common/parsers.py', 'class RowParser')} path='common/parsers.py' service='batch'",
                     "kafka.tail limit=5 topic='mx.alarm.raw'"]


def test_클래스_속성으로_키를_고르는_부모_메서드는_하위_클래스마다_자기_속성의_키만_읽는다(board, monkeypatch):
    """측정 #16 P0-c: 한 하위 클래스의 키가 형제 클래스의 입력으로 붙어 c-10이 `키가 없다 — <남의 키>`에서 멈췄다. `code resources <하위 클래스>`는 그 클래스
    (없으면 가장 가까운 조상)의 속성 리터럴만, 부모 자신은 못 이음."""
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)

    def resources(name):
        code, out = _cli("--config-root", str(board / "config"), "--env-file", str(board / "none"), "code", "resources", name, "--repo", "dt-core")
        assert code == 0, out
        return out

    palette, feed, parent = resources("PaletteSync"), resources("FeedRefSync"), resources("KeyedJob")
    assert "alarm:palette [rediskey]" in palette and "feed:ref" not in palette
    assert "feed:ref [rediskey]" in feed and "alarm:palette" not in feed
    assert "self.redis_key" in parent and "못 이음" in parent and "feed:ref [rediskey]" not in parent
    # R3-5g ①(측정 #17 작은 결함): 자기 속성이 자원 아닌 리터럴(`mode = "material"`)이면 풀린 것 — 형제가 그 속성에 키 이름을 써도 줄도 못 이음도 없다.
    # R3-5j ③: 부모가 그 속성을 비교(`self.mode == "material"`)에만 쓰면 자원이 아니다 — 부모 조회에도 줄이 없다(쓰임새로 흐르는 속성만 자원).
    assert "self.mode" not in palette and "self.mode" not in parent


def test_조인_변형은_이벤트_행과_기준표가_각각_정상인데_병합이_0건이라_두_홉이_나쁨이고_첫_칸은_병합_줄이다(board, monkeypatch):
    """P1-5(사람 결정 10-20): 사내 작업자 배지 — 원천 행의 키 값이 전부 비어 기준표와 pandas inner 병합이 0건. 코드 비교식이 없으니 걷기가 두 입력의 키 값
    겹침을 센다. 멈춤은 `병합이 0건 — <클래스>: …`, 첫 칸은 병합 줄, 그다음은 그 입력의 칸(쓴 쪽을 모르는 키 → `code.uses`)."""
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "join-empty")
    n = _at("common/joiner_pd.py", ".merge(")
    note = "feed:events:L1.alarm_code {''} ↔ feed:ref.code {'A110', 'A111', 'A112'} 겹침 0 (inner 병합)"
    assert rows[:5] == ["alarm:stats:L1 | SummaryRefresh [batch] | 빈 배열 (record[0].data)",
                        "alarm:domain:L1 | DomainBuilder [batch] | 빈 배열 (record)", PALETTE_ROW, SOURCE_ROW,
                        "alarm:raw:L1 | SourceJoiner [dt-core(서비스 3개 공유)] | 빈 배열 (rows)"]
    assert rows[5].startswith(f"feed:events:L1 | — | 나쁨 — 병합 짝 없음: {note} (common/joiner_pd.py:L{n}) · 행 4 · 필드 2 · 모든 행이 같은 값: alarm_code=''")
    assert rows[6] == f"feed:ref | — | 나쁨 — 병합 짝 없음: {note} (common/joiner_pd.py:L{n}) · codes: 행 3 · 필드 2" and len(rows) == 7
    assert stopped == f"병합이 0건 — SourceJoiner: {note} (common/joiner_pd.py:L{n})"
    assert rungs[0] == f"code.read line={n} path='common/joiner_pd.py' service='batch'" and rungs[1] == "code.uses name='feed:events:{line}'"


def test_조인_outer_변형은_행이_남지만_짝_없는_행의_채움값을_거르는_줄이_있어_두_홉이_나쁨이고_첫_칸은_그_거르는_줄이다(board, monkeypatch):
    """측정 #18 P0-a(R3-5h ②): 사내 작업자 배지의 실제 모양 — outer 병합이라 병합 결과는 비지 않고, 결과에 바로 이어진 호출이 짝 없는 칸을 '-'로 채우며 같은
    파일의 비교식이 그 값을 거른다. 걷기는 채워지는 열을 채움값으로 단일값 조회해 그 줄을 찾는다. 멈춤 `병합 뒤 채움값을 거른다 — <클래스>: …`, 칸은 거르는
    줄 → 병합 줄 → 입력 칸."""
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "join-outer")
    m, f = _at("common/joiner_outer.py", ".merge("), _at("common/joiner_outer.py", '!= "-"')
    text = ("feed:events:L1.alarm_code {''} ↔ feed:ref.code {'A110', 'A111', 'A112'} 겹침 0 (outer 병합) → outer 병합이라 행은 남고 짝 없는 행의 code(오른쪽 열) "
            f"값이 '-'(pipe('-')) · 이 값을 거르는 줄 common/joiner_outer.py:L{f} 'rows = merged[(merged[\"code\"] != \"-\") & (merged[\"alarm_code\"] != \"-\")].to_dict(\"records\")'")
    assert rows[:5] == ["alarm:stats:L1 | SummaryRefresh [batch] | 빈 배열 (record[0].data)",
                        "alarm:domain:L1 | DomainBuilder [batch] | 빈 배열 (record)", PALETTE_ROW, SOURCE_ROW,
                        "alarm:raw:L1 | SourceOuterJoiner [dt-core(서비스 3개 공유)] | 빈 배열 (rows)"]
    assert rows[5].startswith(f"feed:events:L1 | — | 나쁨 — 병합 뒤 채움값: {text} (common/joiner_outer.py:L{m}) · 행 4 · 필드 2 · 모든 행이 같은 값: alarm_code=''")
    assert rows[6] == f"feed:ref | — | 나쁨 — 병합 뒤 채움값: {text} (common/joiner_outer.py:L{m}) · codes: 행 3 · 필드 2" and len(rows) == 7
    assert stopped == f"병합 뒤 채움값을 거른다 — SourceOuterJoiner: {text} (common/joiner_outer.py:L{m})" and not any("겹침" in n for n in notes)
    assert rungs[:3] == [f"code.read line={f} path='common/joiner_outer.py' service='batch'", f"code.read line={m} path='common/joiner_outer.py' service='batch'",
                         "code.uses name='feed:events:{line}'"]


def test_원천_키가_없으면_인덱스의_쓰는_클래스와_구독_토픽의_보존_범위가_홉으로_실리고_토픽이_비었으면_거기서_멈춘다(board, monkeypatch):
    """R3-5p ①-c(평가자 분석 10-28): `키가 없다`에서 쓰는 클래스·구독 토픽·범위를 적지 않아 2-a 전부 inconclusive. 측정판 `source-missing`: 원천 키를 쓰는
    `SourceSink`(processor, 인덱스의 쓰기 자원 + 그 키를 선언한 서비스)의 **코드가 구독하는** 토픽 하나(`code.resources` 읽기 `[topic]`, R3-5r ② — 공유 config의
    서비스 선언 셋은 안 읽는다)의 범위가 홉으로 — 메시지가 남아 있으니 멈춤은 `키가 없다`에 그 사실, 첫 칸은 그 클래스 본문. `source-missing-quiet`는 원천
    토픽이 비어 그 홉이 나쁨이고 멈춤이다."""
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "source-missing")
    assert rows[:2] == ["alarm:stats:L1 | SummaryRefresh [batch] | 빈 배열 (record[0].data)", "alarm:domain:L1 | DomainBuilder [batch] | 정상 — record: 행 4 · 필드 2"]
    assert rows[2] == PALETTE_ROW and rows[3] == "alarm:source:L1 | SourceSink [processor] | 없음"
    # 클래스 코드의 토픽 둘 — GBM 선언 topic1(mx.alarm.raw)과 사이트 층에만 있는 topic3(gumi.alarm.audit — 코드는 `cfg.get("infra", "kafka", "consumer",
    # "topic", "topic3")` 경로 호출, R3-5s ③: 인덱스엔 key_path로 섰다가 사이트 행이 이름을 바꾼다). 브로커에 없는 토픽은 `읽기 실패`(모르는 것). 서비스 선언
    # 셋(main 포함)은 안 읽는다.
    # 쓴 쪽 칸은 발행 선언(R3-5t ①): 감사 토픽은 사이트 행의 발행 선언 셋, 원천 토픽은 발행 선언이 없어 우리 밖 — 구독 클래스는 상태 꼬리.
    assert rows[4].startswith("gumi.alarm.audit | 발행 선언: batch, processor, sink | 읽기 실패 — ") and "토픽이 없거나" in rows[4]
    assert rows[4].endswith(" · 구독 SourceSink [processor]")
    assert rows[5:] == ["mx.alarm.raw | 발행 선언 없음(우리 밖) | 정상 — 보존 범위 0~5 · 남은 메시지 5 · 구독 SourceSink [processor]"]
    assert stopped == "키가 없다 — alarm:source:L1(쓰는 SourceSink [processor] · 구독 gumi.alarm.audit 범위 읽기 실패 / 구독 mx.alarm.raw 범위 0~5, 남은 메시지 5)"
    body = f"code.read line={_at('processor/source_sink.py', 'class SourceSink')} path='processor/source_sink.py' service='processor'"
    assert rungs[:2] == [body, "kafka.tail limit=5 topic='mx.alarm.raw'"] and notes == [PALETTE_NOTE]
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "source-missing-quiet")
    assert rows[3] == "alarm:source:L1 | SourceSink [processor] | 없음" and rows[4].startswith("gumi.alarm.audit | 발행 선언: batch, processor, sink | 읽기 실패 — ")
    assert rows[5:] == ["mx.alarm.raw | 발행 선언 없음(우리 밖) | 비었다 — 보존 범위 0~0 · 남은 메시지 0 · 구독 SourceSink [processor]"]
    assert stopped == "구독 토픽이 비었다 — mx.alarm.raw(SourceSink [processor] · 발행 선언 없음(우리 밖))"
    assert rungs[:2] == ["kafka.tail limit=5 topic='mx.alarm.raw'", body]


def test_걸은_클래스가_구독하는_토픽은_보존_범위와_발행_선언으로_읽고_비었으면_거기서_멈춘다(board, monkeypatch):
    """R3-5s ⑥(측정 #29 재측정 2부): ③으로 인덱스에 `[topic]`이 돌아오자 걸은 클래스의 구독 토픽이 `kafka.tail` 입력 홉이 되어 `<ATR 토픽> | — | 빈 배열 (messages)`
    → `쓴 쪽을 모른다`로 멈췄다. 측정판 `source-quiet`: 원천 키 값이 `SourceSink`를 대고 rows가 빈 배열이라 걷기가 그 클래스로 내려가는데, 그 코드가 구독하는 토픽
    둘(사이트 전용 감사 토픽은 브로커에 없어 읽기 실패, 원천 토픽은 비었음)을 보존 범위로 읽고 쓴 쪽 칸은 발행 선언이다 — 원천 토픽이 비어 거기서 멈추고, 첫 칸은
    지금 오는 것, 그다음 그 클래스 본문. 발행 쪽으로 걷지 않는다."""
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "source-quiet")
    assert rows[:2] == ["alarm:stats:L1 | SummaryRefresh [batch] | 빈 배열 (record[0].data)", "alarm:domain:L1 | DomainBuilder [batch] | 정상 — record: 행 4 · 필드 2"]
    assert rows[2] == PALETTE_ROW and rows[3] == "alarm:source:L1 | SourceSink [processor] | 빈 배열 (rows)"
    assert rows[4].startswith("gumi.alarm.audit | 발행 선언: batch, processor, sink | 읽기 실패 — ") and "토픽이 없거나" in rows[4]
    assert rows[4].endswith(" · 구독 SourceSink [processor]")
    assert rows[5:] == ["mx.alarm.raw | 발행 선언 없음(우리 밖) | 비었다 — 보존 범위 0~0 · 남은 메시지 0 · 구독 SourceSink [processor]"]
    assert stopped == "구독 토픽이 비었다 — mx.alarm.raw(SourceSink [processor] · 발행 선언 없음(우리 밖))"
    body = f"code.read line={_at('processor/source_sink.py', 'class SourceSink')} path='processor/source_sink.py' service='processor'"
    assert rungs[:2] == ["kafka.tail limit=5 topic='mx.alarm.raw'", body] and notes == [PALETTE_NOTE]


def test_인덱스에_쓰는_클래스가_없는_키는_다른_사이트의_같은_키_값이_대는_클래스로_쓴_쪽을_채우고_그_구독_토픽까지_걷는다(board, monkeypatch):
    """R3-5t ②(측정 #29 3부): `인덱스에 쓰는 함수가 없다` 주를 리드가 "쓰는 코드 누락"으로 읽었다. 측정판 `palette-source-missing`: 원천 색 키가 없고, 쓰는
    `PaletteSeeder`는 레지스트리 결과(명령)를 저장해 인덱스가 쓰는 줄을 못 본다(P1-7의 모양) — 같은 GBM의 sevt 시드가 그 키 값에 `PaletteSeeder`를 대서 쓴 쪽 칸에
    출처(`sevt 값의 metadata.event_type`)와 함께 채우고, 그 클래스가 구독하는 토픽(GBM 선언 topic2)의 보존 범위까지 걷는다. 멈춤은 `키가 없다`에 그 사실."""
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "palette-source-missing")
    assert "alarm:palette | PaletteLoader [dt-core(서비스 3개 공유)] | 빈 배열 (colors)" in rows
    assert "palette:source | PaletteSeeder [dt-core(서비스 3개 공유) · sevt 값의 metadata.event_type] | 없음" in rows
    topic = [r for r in rows if r.startswith("mx.alarm.main | ")]
    assert topic and topic[0].endswith(" | 정상 — 보존 범위 0~5 · 남은 메시지 5 · 구독 PaletteSeeder [dt-core(서비스 3개 공유) · sevt 값의 metadata.event_type]")
    assert stopped == ("키가 없다 — palette:source(쓰는 PaletteSeeder [dt-core(서비스 3개 공유) · sevt 값의 metadata.event_type] · 구독 mx.alarm.main 범위 0~5, "
                       "남은 메시지 5)")
    assert notes == [PALETTE_NOTE, "palette:source: 인덱스에 쓰는 함수가 없다(code.uses 쓰기 줄 0) — 쓴 쪽은 sevt의 같은 키 palette:source 값의 metadata.event_type에서 채웠다: PaletteSeeder"]
    assert rungs[:2] == [f"code.read line={_at('common/palette.py', 'class PaletteSeeder')} path='common/palette.py' service='batch'",
                         "kafka.tail limit=5 topic='mx.alarm.main'"]


def test_공용_키가_없고_쓰는_클래스_코드에_구독_토픽이_없으면_서비스_선언을_읽지_않고_그_수만_적는다(board, monkeypatch):
    """R3-5r ② — 평가자 회신(10-30) 4(b): 공유 config의 서비스 선언은 법인마다 10~11개라 상한 3은 고르는 규칙이 된다. 측정판 `palette-missing`: 공용 팔레트 키가
    없고 쓰는 `PaletteLoader`(두 레포 — 선언으로 dt-core, 서비스 셋 공유)의 코드엔 토픽이 없다 — 세 서비스의 선언 셋은 그 클래스의 것인지 몰라 안 읽고 수만
    주와 멈춤 꼬리에. 첫 칸은 그 클래스 본문."""
    rows, notes, stopped, rungs = _walk(board, monkeypatch, "palette-missing")
    assert "alarm:palette | PaletteLoader [dt-core(서비스 3개 공유)] | 없음" in rows and len(rows) == 4
    assert stopped == "키가 없다 — alarm:palette(쓰는 PaletteLoader [dt-core(서비스 3개 공유)] · 구독 토픽 없음(클래스 코드 · 서비스 선언 3개))"
    assert notes == [PALETTE_NOTE, "PaletteLoader: 클래스 코드에 구독 토픽이 없다(code.resources 읽기에 [topic] 0줄) — 서비스 선언 3개는 그 클래스의 것인지 몰라 안 읽는다"]
    assert rungs[0] == f"code.read line={_at('common/palette.py', 'class PaletteLoader')} path='common/palette.py' service='batch'"
