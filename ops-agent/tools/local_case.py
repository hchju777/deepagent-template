"""로컬 대역 측정판 — **사내 없이 같은 배선을 끝까지 돌린다.**

사내 모델은 이 리포 밖에서만 돈다. 그동안 측정은 매번 사내에 부탁했고, 결과를 사람이
손으로 옮겨야 했다 — 11a 후반의 왕복 전부가 그것이었다. 이 도구는 여기서 돌릴 수 있는
케이스 하나를 통째로 만든다: config 사본(LLM만 파일 턴 어댑터), knowledge 사본, 대상
config 층이 든 가짜 git 레포 둘, stub seed, 케이스 레코드. 리드 자리에 무엇을 세우든
(사람, 다른 모델) `turns/NNN-ask.md`를 읽고 `NNN-reply.md`를 써 주면 된다.

**여기 이름은 전부 지어낸 것이다**(decisions ⑮ — 사내 실물 이름을 테스트 데이터에 넣지
않는다). 심은 고장은 기본이 하나다: sink 컨슈머 그룹이 멈춰 lag가 쌓이고, `alarm_events`에
새 문서가 안 들어오고, API 화면에 알람이 안 올라온다. 토픽에는 새 메시지가 계속 온다.
`--variant`로 고장의 자리를 바꾼다(`VARIANTS`) — 원천 재집계의 일치/불일치를 재는 데 쓴다.

    .venv/bin/python tools/local_case.py --root output/local-case [--variant cache-stale]
"""
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

LINES = ["L1", "L2", "L3"]
# 제목에 '/'가 든 배지(R3-5b ① — 측정 #12 행 9: 입구가 대상 문자열을 '/'로 쪼개 대상 행을 놓쳤다).
BADGE_TITLES = ["Setup", "Idle", "Quality", "Stop", "Caution", "Alarm", "Up/Down Rate"]
# 배지 행의 링크 필드(사내 모양 — 값 안에 그 배지 키의 이름 토큰이 든다). 코드가 선언 없이 행의 값에서 토큰을 뽑아 선언 키와 맞춘다
# (`intake_map`). Alarm 배지의 토큰만 `alarm:stats:{line}`에 든다 — 다른 배지의 토큰은 어떤 키에도 안 들어야 "골랐다"가 난다.
BADGE_LINKS = {"Alarm": "stats", "Up/Down Rate": "rate"}


def _git(*args, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   encoding="utf-8", errors="replace")


def _head(repo: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def _submodule(parent: Path, lib: Path, *, path: str) -> None:
    """공유 라이브러리를 서브모듈로 심고 채운다 — 사내 모양이다(레포마다 같은 라이브러리를 `src`와 같은 깊이에
    두고 핀을 박는다). `git submodule add`는 로컬 경로에 막혀 있어(CVE-2022-39253) `.gitmodules`와 gitlink를
    손으로 만들고(tests/support와 같은 방식) 채우기만 git에 맡긴다."""
    _write(parent, ".gitmodules", f'[submodule "{path}"]\n\tpath = {path}\n\turl = {lib.as_posix()}\n')
    _git("add", ".gitmodules", cwd=parent)
    _git("update-index", "--add", "--cacheinfo", f"160000,{_head(lib)},{path}", cwd=parent)
    _git("commit", "-qm", "shared library", cwd=parent)
    _git("-c", "protocol.file.allow=always", "submodule", "update", "--init", "--", path, cwd=parent)


# 측정판의 서비스 지도 — 리포의 자리표시자 셋(processor·sink·api)에 요약 키를 명령에 실어 보내는 `batch`(R3-1b ②, 사내 10-07 진단표 #8의 모양).
BOARD_SERVICES = {
    "processor": {"repo": "dt-core", "role": "원천 이벤트를 받아 가공한다", "selects": {"SERVICE_ROLE": "processor"}},
    "sink": {"repo": "dt-core", "role": "가공된 결과를 저장한다", "selects": {"SERVICE_ROLE": "sink"}},
    "batch": {"repo": "dt-core", "role": "주기적으로 요약을 다시 만들라는 명령을 낸다", "selects": {"SERVICE_ROLE": "batch"}},
    "api": {"repo": "dt-api", "role": "저장된 것을 API 응답으로 바꾼다"},
}


def _board_topology(topo: dict) -> dict:
    """복사한 토폴로지의 `services`를 측정판 것으로 — 나머지(흐름 출처·config 경로·설명)는 그대로."""
    return {**topo, "services": dict(BOARD_SERVICES)}


def _write(root: Path, rel: str, content) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)
    path.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")


def _repo(root: Path, name: str, url: str, files: dict) -> Path:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=root)
    _git("config", "user.email", "local@example.com", cwd=root)
    _git("config", "user.name", "local", cwd=root)
    _git("remote", "add", "origin", url, cwd=root)
    for rel, content in files.items():
        _write(root, rel, content)
    _git("add", "-A", cwd=root)
    _git("commit", "-qm", f"{name}: local scenario", cwd=root)
    return root


# 공유 라이브러리(서브모듈) — processor가 부르는 `normalize`·`now`가 여기 산다. 대상 이름(토픽·컬렉션)을 안 쓴다:
# 흐름 그래프는 그대로이고, 인덱스에 레포 경계를 넘는 확실 엣지가 생기는지만 본다.
SHARED_FILES = {
    # 사내 공유 라이브러리처럼 패키지 머리에서 `*`로 재수출한다 — 소비 코드는 패키지에서 받는다.
    "__init__.py": "from .clock import *\nfrom .events import *\n",
    "events.py": '''"""소비 레포마다 서브모듈로 핀을 박아 쓴다."""


def normalize(msg):
    event = dict(msg)
    event.setdefault("severity", "minor")
    return event
''',
    "clock.py": '''import time


def now():
    return time.time()
''',
}

# **사내 config의 모양이다**(2026-09 확인, 이름은 지어냈다). `infra`는 레포당 하나라 한
# 레포의 서비스 둘이 공유한다 — 컨슈머 그룹도 같다. 토픽 키는 `topic1`·`topic2`처럼
# 뜻이 없고, 컬렉션·redis 키는 `infra` 밖 최상위에 있다.
CORE_FILES = {
    "config/gbm/mx.json": {
        "infra": {
            "kafka": {
                "consumer": {"group_id": "mx-core",
                             "topic": {"topic1": "mx.alarm.raw", "topic2": "mx.alarm.main"}},
                "producer": {"topic": {"topic1": "mx.alarm.main"}}}},
        "mongodb_collection": {"alarm": "alarm_events", "line_state": "line_state"},
        "redis_key": {"alarm_stats": "alarm:stats:{line}", "alarm_source": "alarm:source:{line}", "heartbeat": "hb:{service}",
                      "alarm_palette": "alarm:palette", "palette_source": "palette:source",
                      "alarm_domain": "alarm:domain:{line}", "alarm_raw": "alarm:raw:{line}",
                      "feed_events": "feed:events:{line}", "feed_status": "feed:status:{line}", "feed_ref": "feed:ref",
                      "alarm_rate": "alarm:rate:{line}"},
        "sink": {"batch_size": 200, "flush_sec": 5}},
    # `badge.state`는 미끼(측정 #10 P0 1의 가짜 ① — 사이트 상수 config 줄): 필드와 값이 한 줄에 리터럴로 있지만 비교식도 코드도 아니다.
    "config/factories/gumi/common.json": {"lines": LINES, "site_code": "gumi", "badge": {"empty_state": "-", "state": "-"}},
    # 사이트 층이 소비·발행 토픽을 **같은 값**으로 덮는 행(측정 #15 P0-1의 모양 — GBM 층엔 없는 키라 그래프엔 사이트 행으로만 든다). 감사 토픽은
    # message-gone의 포인터가 가리키는 자리다.
    "config/factories/gumi/mx.json": {
        "infra": {"kafka": {"consumer": {"group_id": "gumi-mx-core", "topic": {"topic3": "gumi.alarm.audit"}},
                            "producer": {"topic": {"topic2": "gumi.alarm.audit"}}}}},
    # 두 번째 사이트 — 11e-2의 "번들은 GBM 하나, 사이트는 덮은 값만"을 측정판에서 보기 위해서다.
    "config/factories/sevt/common.json": {"lines": LINES, "site_code": "sevt"},
    # sevt 층은 redis 키에 자기 접두어를 붙인다 — 사내 모양(측정 #30): 사이트마다 `키 접두어 항목`가 달라 다른 사이트의 같은 키는 그 사이트의 이름으로 물어야
    # 한다(R3-5t ④). gumi는 접두어 없이 둔다 — 측정판의 키 이름 전부가 바뀌지 않게, 번역은 sevt 쪽에서 보인다.
    # topic2도 사이트 이름으로 덮는다 — 다른 사이트 값의 `metadata.source.topic`이 그 사이트 이름이라 이 사이트 이름으로 바꿔야 한다(R3-5u ①).
    "config/factories/sevt/mx.json": {
        "infra": {"kafka": {"consumer": {"group_id": "sevt-mx-core", "topic": {"topic2": "sevt.alarm.main"}}}},
        "redis_key": {"prefix": "sevt"}},
    "processor/handler.py": '''"""alarm_raw를 읽어 정규화한 뒤 alarm_main으로 낸다."""
from shared_lib import normalize, now

EMPTY = {"state": "-", "alarm_code": ""}      # 자리표시자 행 — 사전 리터럴이지 비교식이 아니다(측정 #10의 가짜 ②: 다른 처리기의 dict 줄)


def run(cfg, consumer, producer, redis):
    kafka = cfg["infra"]["kafka"]
    for msg in consumer.subscribe(kafka["consumer"]["topic"]["topic1"], group=kafka["consumer"]["group_id"]):
        event = normalize(msg)
        producer.send(kafka["producer"]["topic"]["topic1"], event)
        redis.set(cfg["redis_key"]["heartbeat"].format(service="processor"), now())
''',
    # 원천 키(`alarm:source:{line}`)를 **쓰는** 클래스(R3-5p ①-c) — 원천 토픽을 구독해 행 묶음을 키로 쓴다. `source-missing`에서 걷기가 인덱스의 쓰기 자원과
    # 선언으로 이 클래스를 찾고, 그 클래스 코드가 구독하는 토픽의 보존 범위를 홉으로 적는다(R3-5r ②). 감사 토픽(topic3)은 사이트 층에만 있고 코드는 config 경로
    # 호출로 구독한다 — 사내 `<AGV 클래스>`의 모양(R3-5s ③): 인덱스엔 key_path로 섰다가 사이트 행이 이름을 바꾼다. 쓰기도 그 클래스의 모양(R3-5s ⑤): 키 토큰을
    # 키워드 인자로 부모(`SnapshotSink` — 사내 `<AGV 부모>`)에 넘기고, 부모가 `cfg.get("redis_key", config_key)`로 풀어 헬퍼로 저장한다 — 쓰기 줄은 하위
    # 클래스의 호출 줄 하나뿐이라 인덱스가 그 잇기를 못 하면 `source-missing`의 쓴 쪽이 사라진다(측정 #29 재측정 2부의 `<AGV 키>`).
    "processor/source_sink.py": '''"""alarm_raw를 받아 라인별 원천 묶음 키를 쓴다."""


class SnapshotSink:
    def __init__(self, cfg, consumer, redis):
        self.cfg, self.consumer, self.redis = cfg, consumer, redis

    def _flush_snapshot(self, payload, line, config_key):
        target_key = self.cfg.get("redis_key", config_key).format(line=line)
        self._store_snapshot(target_key, payload)

    def _store_snapshot(self, key, payload):
        self.redis.set(key, payload)


class SourceSink(SnapshotSink):
    def run(self):
        kafka = self.cfg["infra"]["kafka"]
        self.consumer.subscribe(self.cfg.get("infra", "kafka", "consumer", "topic", "topic3"), group=kafka["consumer"]["group_id"])
        for msg in self.consumer.subscribe(kafka["consumer"]["topic"]["topic1"], group=kafka["consumer"]["group_id"]):
            self._flush_snapshot(msg["rows"], msg["line"], config_key="alarm_source")
''',
    "sink/writer.py": '''"""alarm_main을 읽어 alarm_events에 넣고 alarm:stats:{line}을 갱신한다."""


def run(cfg, consumer, mongo, redis):
    kafka = cfg["infra"]["kafka"]
    batch = []
    for msg in consumer.subscribe(kafka["consumer"]["topic"]["topic2"], group=kafka["consumer"]["group_id"]):
        batch.append(msg)
        if len(batch) >= cfg["sink"]["batch_size"]:
            mongo[cfg["mongodb_collection"]["alarm"]].insert_many(batch)
            for line in {m["line"] for m in batch}:
                redis.set(cfg["redis_key"]["alarm_stats"].format(line=line), stats(line))
            redis.set(cfg["redis_key"]["heartbeat"].format(service="sink"), now())
            batch = []
''',
    # 사내 10-07 진단표 #8의 모양 — 배치 파이프라인이 요약 키를 **직접 쓰지 않고** 이름을 kafka 명령에 실어 보낸다. 그 줄엔 동사가 없어
    # `code.uses`가 읽기로 분류했었다(R3-1b ②의 "뒷줄 동사" 규칙은 사내에서 틀려 걷어냈고, R3-5r ①부터는 조립한 값이 처음 닿는 호출 `send()`의 동사로
    # 쓰기 — `code resources`에 ` · 키를 조립해 send()로 넘긴다`가 붙는다).
    # 사내 모양(측정 #8): 쓰는 쪽은 `_run`을 가진 클래스이고, 요약 키 값의 `metadata.event_type`이 이 클래스 이름이다(입구 노드가 그걸로
    # 쓴 쪽을 고른다, R3-1c ①-a). 원천 키(`alarm:source:{line}`)를 읽어 요약 이름을 메시지에 실어 보낸다 — 직접 `set` 안 함.
    "batch/refresh.py": '''"""배지 요약을 다시 만든다 — 도메인 키와 원천 키를 읽고, 요약 키 이름을 메시지에 실어 보낸다(직접 쓰지 않는다)."""


class SummaryRefresh:
    def __init__(self, cfg, redis, producer, components):
        self.cfg, self.redis, self.producer, self.components = cfg, redis, producer, components

    def _run(self):
        palette = json.loads(self.redis.get(self.cfg["redis_key"]["alarm_palette"]) or "{}")
        for line in self.cfg["lines"]:
            domain = json.loads(self.redis.get(self.cfg["redis_key"]["alarm_domain"].format(line=line)) or "{}")
            source = self.redis.get(self.cfg["redis_key"]["alarm_source"].format(line=line))
            rows = self.components.get("state_filter").apply(domain.get("record", []) + json.loads(source or "[]"))
            command = {"op": "refresh", "target": self.cfg["redis_key"]["alarm_stats"].format(line=line), "source": rows,
                       "colors": palette.get("colors", [])}
            self.producer.send(self.cfg["infra"]["kafka"]["producer"]["topic"]["topic1"], command)
''',
    # 측정 #16 P0-c의 실코드 모양: 부모가 `config.get("redis_key", self.<속성>)`으로 키를 고르고 하위 클래스마다 속성이 다르다 — 옛 인덱스는 클래스 속성을
    # 모듈 별칭으로 삼아 한 하위 클래스의 키를 부모 메서드에 박았고(이 파일에서는 마지막 `feed:ref`), 형제 클래스에 남의 키가 입력으로 실렸다(R3-5f ③).
    "batch/keyed.py": '''"""키를 클래스 속성으로 고르는 배치 — config에 redis_key 항목이 없으면 하위 클래스의 속성을 쓴다."""


class KeyedJob:
    def _run(self):
        data = json.loads(self.redis.get(self.cfg.get("redis_key", self.redis_key)) or "{}")
        return data if self.mode == "material" else {}


class PaletteSync(KeyedJob):
    redis_key = "alarm:palette"
    mode = "material"


class FeedRefSync(KeyedJob):
    redis_key = "feed:ref"
    mode = "feed:ref"
''',
    # 제목에 '/'가 든 배지의 요약 키를 쓰는 배치 클래스(R3-5b ①) — 요약 키 이름을 메시지에 실어 보낸다.
    "batch/rate.py": '''"""증감 배지 요약을 다시 만든다 — 원천 키를 읽고 요약 키 이름을 메시지에 실어 보낸다."""


class RateRefresh:
    def __init__(self, cfg, redis, producer):
        self.cfg, self.redis, self.producer = cfg, redis, producer

    def _run(self):
        for line in self.cfg["lines"]:
            source = json.loads(self.redis.get(self.cfg["redis_key"]["alarm_source"].format(line=line)) or "[]")
            command = {"op": "refresh", "target": self.cfg["redis_key"]["alarm_rate"].format(line=line), "rows": source}
            self.producer.send(self.cfg["infra"]["kafka"]["producer"]["topic"]["topic1"], command)
''',
    # 사내 모양(측정 #10·#11): `_run`은 조립뿐이고 **비교식은 컴포넌트 클래스에** 있다 — `_run`의 `get("<컴포넌트>")`와 등록 모음 파일의
    # `register(component="<컴포넌트>", …)(<클래스>)`가 리터럴로 이어진다(호출 그래프에는 없다). 단일값 검사의 범위는 등록이 가리키는 클래스의 파일이다
    # (R3-5 ③). 기본 클래스는 `== "ea"`로 **남기는** 선택뿐(측정 #22의 틀린 사실 모양 — 연산자를 안 보면 원천 행의 `unit='ea'`가 가짜 나쁨, R3-5l ②),
    # 이 사이트 전용 클래스가 `!=`로 거른다 — 한 줄에 짝이 둘이다.
    "batch/components.py": '''"""배치 파이프라인의 컴포넌트 — 등록은 등록 모음 파일에 있고 `_run`이 `get("…")`로 찾는다."""


class StateFilter:
    def apply(self, rows):
        return [r for r in rows if r.get("unit") == "ea"]


class GumiStateFilter:
    def apply(self, rows):
        return [r for r in rows if r.get("state") != "-" and r.get("wg") != "-"]
''',
    # 다른 사이트 전용 클래스(미끼) — 이 사이트(gumi)의 걷기는 이 파일을 보면 안 된다. 보면 원천 행의 `unit='ea'`가 가짜 나쁨이 된다.
    "batch/sevt_filters.py": '''"""sevt 전용 컴포넌트."""


class SevtStateFilter:
    def apply(self, rows):
        return [r for r in rows if r.get("unit") != "ea"]
''',
    # 사내 등록 모음 파일의 모양(사람 확인 10-15) — 레포마다 한 곳, 데코레이터가 아니라 호출형 네 모양: (a) 한 줄 `register(component="X")(Cls)`,
    # (b) 사이트 인자(기본 클래스 위에 사이트 전용 클래스), (c) 점 표기 `alias.Cls`(`import <모듈> as alias`), (d) 여러 줄. 문서 문자열 속 예시는
    # 등록이 아니다 — 세면 이 사이트의 state_filter가 둘이 돼 못 이음이 된다.
    "common/registry.py": '''"""등록 모음 파일 — 컴포넌트 이름 → 클래스.

예시(문서일 뿐 등록이 아니다):
    @register(component="state_filter", gbm="mx", factory="gumi")
    class LegacyStateFilter: ...
"""
import batch.components as comp
import batch.sevt_filters as sevt_filters
import common.fillers as fillers
from batch.components import StateFilter
from common.joiners import RefJoiner
from common.parsers import RowParser

register(component="state_filter")(StateFilter)
register(component="state_filter", gbm="mx", factory="gumi")(comp.GumiStateFilter)
register(component="state_filter", gbm="mx", factory="sevt")(sevt_filters.SevtStateFilter)
register(component="row_parser")(RowParser)
register(component="ref_joiner")(RefJoiner)
register(
    component="row_filler",
)(
    fillers.RowFiller
)
''',
    # 도메인 키를 쓰는 배치 클래스 — 원천 묶음을 읽는다(사내 c-1·c-2의 가운데 홉).
    # 읽기는 부모(다른 파일)의 `_run`에, 쓰기는 자식에 — 사내 처리기의 상속 모양(R3-5d ③, 측정 #13 추가분 3). `code.resources`가 부모의 읽기를 안 세면
    # 걷기가 여기서 "자원이 자기 출력뿐"으로 멈춘다.
    "common/readers.py": '''"""라인마다 원천 묶음을 읽어 자식에게 넘기는 부모 클래스."""


class LineReader:
    def __init__(self, cfg, redis):
        self.cfg, self.redis = cfg, redis

    def _run(self):
        for line in self.cfg["lines"]:
            raw = json.loads(self.redis.get(self.cfg["redis_key"]["alarm_raw"].format(line=line)) or "{}")
            self.handle(line, raw)
''',
    "batch/domain.py": '''"""도메인 키를 만든다 — 원천 묶음의 행을 그대로 record로."""
from common.readers import LineReader


class DomainBuilder(LineReader):
    def handle(self, line, raw):
        record = {"metadata": {"event_type": "DomainBuilder"}, "record": raw.get("rows", [])}
        self.redis.set(self.cfg["redis_key"]["alarm_domain"].format(line=line), json.dumps(record))
''',
    # 측정 #16 P0-3의 실코드 모양: `get(<변수>)`를 품은 부모 메서드에는 기본값이 없고, 기본값은 재정의에 있으며 재정의가 super()로 그 인자를 넘긴다 —
    # 이름 단위로 풀어야 `row_parser`가 이어진다(R3-5f ②). 함수 단위로 풀던 코드는 "못 이음 — 변수 parser"라 parsers.py의 비교식을 못 봤다.
    "common/jobs.py": '''"""메시지 작업의 뼈대 — 파서는 하위 클래스가 고른다."""


class MessageJob:
    def _run(self, message, parser):
        rows = self.components.get(parser).parse(message)
        self._store(message, rows)
''',
    # c-1형의 원천 클래스 — 토픽 메시지를 받아 인덱스 자원(읽기)이 0이다. 같은 이름이 dt-api에도 있다(그쪽은 alarm:raw를 선언하지 않는다).
    "common/source.py": '''"""원천 수집 — 메시지를 행으로 바꿔 원천 묶음에 둔다."""
from common.jobs import MessageJob


class SourceCollector(MessageJob):
    def __init__(self, cfg, redis, components):
        self.cfg, self.redis, self.components = cfg, redis, components

    def _run(self, message, parser="row_parser"):
        return super()._run(message, parser)

    def _store(self, message, rows):
        record = {"metadata": {"event_type": "SourceCollector"}, "rows": rows}
        self.redis.set(self.cfg["redis_key"]["alarm_raw"].format(line=message["line"]), json.dumps(record))
''',
    "common/parsers.py": '''"""메시지 → 행 — 자리 표시('-')인 행을 거른다."""


class RowParser:
    def parse(self, message):
        return [row for row in message.get("rows", []) if row.get("ws") != "-"]
''',
    # 읽는 자원이 자기 출력뿐인 원천 클래스(R3-5b ② — 측정 #12의 `<클래스>` 모양) — 원천 묶음을 읽어 행을 덧붙여 다시 쓴다.
    "common/appender.py": '''"""원천 묶음에 행을 덧붙인다 — 지금 값을 읽어 행을 더해 다시 쓴다."""


class RawAppender:
    def __init__(self, cfg, redis):
        self.cfg, self.redis = cfg, redis

    def _run(self, message):
        raw = json.loads(self.redis.get(self.cfg["redis_key"]["alarm_raw"].format(line=message["line"])) or "{}")
        rows = raw.get("rows", []) + [message]
        self.redis.set(self.cfg["redis_key"]["alarm_raw"].format(line=message["line"]), json.dumps({"metadata": {"event_type": "RawAppender"}, "rows": rows}))
''',
    # c-2형의 원천 클래스 — 입력 셋(정상)을 합치고 빈 칸을 채운다. 여기엔 비교식이 없다. 컴포넌트 둘 중 빈 칸을 채우는 쪽이 코드 차례로는 뒤다 —
    # 뒤 칸의 차례가 나쁜 필드 이름으로 정해지는지 본다(사람 결정 10-15 (c)).
    "common/merge.py": '''"""원천 병합 — 이벤트·상태·기준표를 합쳐 행을 만들고 빈 칸을 채워 원천 묶음에 둔다."""


class SourceMerger:
    def __init__(self, cfg, redis, components):
        self.cfg, self.redis, self.components = cfg, redis, components

    def _run(self):
        ref = json.loads(self.redis.get(self.cfg["redis_key"]["feed_ref"]) or "{}")
        for line in self.cfg["lines"]:
            events = json.loads(self.redis.get(self.cfg["redis_key"]["feed_events"].format(line=line)) or "[]")
            status = json.loads(self.redis.get(self.cfg["redis_key"]["feed_status"].format(line=line)) or "[]")
            merged = self.components.get("ref_joiner").join(events, status, ref)
            rows = self.components.get("row_filler").fill(merged)
            record = {"metadata": {"event_type": "SourceMerger"}, "rows": rows}
            self.redis.set(self.cfg["redis_key"]["alarm_raw"].format(line=line), json.dumps(record))
''',
    # P1-5 조인의 사내 모양(사람 결정 10-20: pandas merge) — 이벤트 행과 기준표를 inner 병합해 원천 묶음에 둔다. 비교식이 없고, 거름은 두 입력의 키 값 차이로만 보인다.
    "common/joiner_pd.py": '''"""이벤트에 기준표를 붙인다 — 안쪽 병합이라 짝이 없는 행은 사라진다."""
import pandas as pd


class SourceJoiner:
    def __init__(self, cfg, redis):
        self.cfg, self.redis = cfg, redis

    def _run(self):
        ref = pd.DataFrame(json.loads(self.redis.get(self.cfg["redis_key"]["feed_ref"]) or "{}").get("codes", []))
        for line in self.cfg["lines"]:
            events = pd.DataFrame(json.loads(self.redis.get(self.cfg["redis_key"]["feed_events"].format(line=line)) or "[]"))
            rows = events.merge(ref, left_on="alarm_code", right_on="code", how="inner").to_dict("records")
            self.redis.set(self.cfg["redis_key"]["alarm_raw"].format(line=line), json.dumps({"metadata": {"event_type": "SourceJoiner"}, "rows": rows}))
''',
    "common/joiner_outer.py": '''"""이벤트에 기준표를 붙인다 — 바깥 병합이라 행은 남고, 짝 없는 칸을 '-'로 채운 뒤 그 값을 거른다."""
import pandas as pd


def fill_blanks(frame, value):
    return frame.where(frame.notna(), value)


class SourceOuterJoiner:
    def __init__(self, cfg, redis):
        self.cfg, self.redis = cfg, redis

    def _run(self):
        ref = pd.DataFrame(json.loads(self.redis.get(self.cfg["redis_key"]["feed_ref"]) or "{}").get("codes", []))
        for line in self.cfg["lines"]:
            events = pd.DataFrame(json.loads(self.redis.get(self.cfg["redis_key"]["feed_events"].format(line=line)) or "[]"))
            merged = events.merge(ref, left_on="alarm_code", right_on="code", how="outer").pipe(fill_blanks, "-")
            rows = merged[(merged["code"] != "-") & (merged["alarm_code"] != "-")].to_dict("records")
            self.redis.set(self.cfg["redis_key"]["alarm_raw"].format(line=line), json.dumps({"metadata": {"event_type": "SourceOuterJoiner"}, "rows": rows}))
''',
    "common/joiners.py": '''"""이벤트에 상태·기준표를 붙인다 — 바깥 병합이라 짝이 없으면 칸이 빈다."""


class RefJoiner:
    def join(self, events, status, ref):
        by_id = {s["id"]: s for s in status}
        codes = {c["code"]: c for c in ref.get("codes", [])}
        return [{**e, **by_id.get(e["id"], {}), **codes.get(e["alarm_code"], {})} for e in events]
''',
    "common/fillers.py": '''"""병합 뒤 빈 칸 채우기."""


class RowFiller:
    def fill(self, rows):
        for row in rows:
            row.setdefault("state", "-")
            row.setdefault("wg", "-")
        return rows
''',
    # 측정 #10 P0 2의 모양 — 공용 키의 쓴 쪽이 서비스 디렉터리 **밖**(`common/`)에 있다. 토폴로지에 `path`가 없으니 processor·sink·batch 중 어느
    # 것인지 코드로는 못 가른다(사내 처리기 레포: 서비스 5개가 한 레포·한 config). 같은 이름이 dt-api에도 있어 레포 둘이다.
    "common/palette.py": '''"""배지 색 표 — 원천 색 목록에서 켜진 것만 남겨 alarm:palette에 둔다."""


class PaletteLoader:
    def __init__(self, cfg, redis):
        self.cfg, self.redis = cfg, redis

    def _run(self):
        source = self.redis.get(self.cfg["redis_key"]["palette_source"])
        colors = [c for c in json.loads(source or "[]") if c.get("enabled")]
        self.redis.set(self.cfg["redis_key"]["alarm_palette"], json.dumps({"metadata": {"event_type": "PaletteLoader"}, "colors": colors}))


class PaletteSeeder:
    """원천 색 목록을 메시지로 받아 키로 쓴다 — 쓰기가 레지스트리 컴포넌트의 결과(명령)를 거쳐 **인덱스가 쓰는 줄을 못 본다**(사내 P1-7의 모양, R3-5t ②).
    키 토큰이 이 파일에 없으니 `code uses palette:source`의 쓰기 줄은 0이고, 쓴 쪽은 값(`metadata.event_type`)에서만 알 수 있다. 구독도 레지스트리 경유라
    코드에 토픽이 없다(R3-5u ① — 사내 `<PGM 클래스>`의 모양): 입력 토픽은 값의 `metadata.source`(메시지 포인터)에서만."""

    def __init__(self, cfg, registry, storage):
        self.cfg, self.registry, self.storage = cfg, registry, storage

    def run(self):
        for msg in self.registry["palette_feed"].messages():
            command = self.registry["palette_writer"].process(msg)
            self.storage.save(command)
''',
}

# 일부 서비스는 이름을 객체 안에 둔다(`{"collection": …, "ttl": 3}`, 사내 확인). dt-api가 그 모양이다.
API_FILES = {
    "config/gbm/mx.json": {
        "infra": {"mongodb": {"database": "data"}},
        "mongodb_collection": {"alarm": {"collection": "alarm_events", "ttl": 3}},
        "redis_key": {"alarm_stats": {"key": "alarm:stats:{line}", "ttl": 30},
                      "alarm_scan": {"key": "alarm:scan:{line}", "ttl": 30},
                      "alarm_rate": {"key": "alarm:rate:{line}", "ttl": 30}},
        "api": {"alarm_window_min": 60, "badge_format": "compact"}},
    "config/factories/gumi/common.json": {"lines": LINES},
    # 사내 모양(측정 #16 P0-b): 다른 레포가 **같은 key_path**(`producer.topic.topic2`)로 다른 사이트 전용 토픽을 선언한다 — 이름의 정체는 (종류, key_path)가
    # 아니라 (종류, key_path, 서비스)다. key_path로 찾던 `apply_site`는 이 행으로 dt-core의 `gumi.alarm.audit` 노드 이름을 바꿔 message-gone의 발행 선언을 지웠다.
    "config/factories/gumi/mx.json": {"infra": {"kafka": {"producer": {"topic": {"topic2": "gumi.api.audit"}}}}},
    "config/factories/sevt/common.json": {"lines": LINES},
    # 사내 핸들러 모양(2026-09 확인): 캐시 키를 먼저 보고, 비면 저장소를 조회하고, 형식 같은 것은
    # config 이름으로 getattr해 고른다. 캐시만 읽는 핸들러로는 "핸들러에서 컬렉션까지 이어지나"를 잴 수 없다.
    # dt-core의 `common/palette.py`와 **같은 클래스 이름** — 레포 둘(사내 수집기·처리기의 모양). 이쪽은 alarm:palette를 선언하지 않는다.
    "api/palette.py": '''"""화면 색 — 고정 팔레트에 형식만 config에서."""


class PaletteLoader:
    def __init__(self, cfg):
        self.cfg = cfg

    def _run(self):
        return {"format": self.cfg["api"]["badge_format"], "colors": ["#d33", "#fc0", "#3a3"]}
''',
    # dt-core의 `common/source.py`와 같은 클래스 이름(사내 수집기·처리기 레포의 모양) — 이쪽은 alarm:raw를 선언하지 않는다.
    # 화면 스키마 클래스 — 이름이 증감 배지 키의 행 안 화면 라벨과 같다(R3-5c ① 미끼).
    "api/schemas.py": '''"""화면 스키마."""


class Overview:
    def __init__(self, rows):
        self.rows = rows
''',
    "api/source.py": '''"""화면용 원천 표 — 메시지의 행을 형식과 함께 돌려준다."""


class SourceCollector:
    def __init__(self, cfg):
        self.cfg = cfg

    def _run(self, message):
        return {"format": self.cfg["api"]["badge_format"], "rows": message.get("rows", [])}
''',
    "api/alarms.py": '''"""알람 화면 — alarm:stats:{line} 배지. 캐시가 비면 최근 alarm_window_min 분의 alarm_events에서 센다."""


router = APIRouter(prefix="/summary")


@router.post("/badge")
def summary_badge(cfg, mongo, redis):
    return {line: badge(cfg, mongo, redis, line) for line in cfg["lines"]}


def badge(cfg, mongo, redis, line):
    cached = redis.get(cfg["redis_key"]["alarm_stats"]["key"].format(line=line))
    caution = redis.get(cfg["redis_key"]["alarm_scan"]["key"].format(line=line))
    rate = redis.get(cfg["redis_key"]["alarm_rate"]["key"].format(line=line))
    counts = cached or count_recent(cfg, mongo, line)
    render = getattr(formatters, cfg["api"]["badge_format"])
    return render(counts)


def count_recent(cfg, mongo, line):
    coll = cfg["mongodb_collection"]["alarm"]["collection"]
    since = window_start(cfg["api"]["alarm_window_min"])
    return {"alarm": mongo[coll].count_documents({"line": line, "occ_date": {"$gte": since}})}


def window_start(minutes):
    return now() - timedelta(minutes=minutes)


def recent_alarms(cfg, mongo, since):
    coll = cfg["mongodb_collection"]["alarm"]["collection"]
    return list(mongo[coll].find({"occ_date": {"$gte": since}}).sort("occ_date", -1))
''',
}


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat()


# 변형은 **어느 홉이 깨졌나**가 다르다. 11b 커밋 4의 T3가 이 셋으로 recompute의 일치/불일치를 잰다 —
# 고장이 컬렉션 상류(sink-stopped)면 배지와 컬렉션이 **일치**하는 것이 정상이고, 컬렉션과 화면 사이
# (cache-stale)면 불일치가 나야 한다. healthy는 대조군.
VARIANTS = {
    "sink-stopped": "sink 컨슈머 정지 — 토픽엔 새 메시지, 컬렉션·캐시·화면은 4시간째 옛것",
    "cache-stale": "sink는 컬렉션에 쓰는데 stats 캐시를 안 갱신 — 컬렉션은 최신, 캐시·화면은 옛것",
    "healthy": "고장 없음 — 배지가 창 안 문서 수와 같다",
    # 요약 키가 Redis에 아예 없다 — "선언됐는데 없는 키"가 r1 브리핑에 실리는지 잰다. (측정 #6 정정: 사내 c-1형이 **아니다**.)
    "cache-missing": "sink는 컬렉션에 쓰는데 stats 캐시 키를 한 번도 안 만들었다 — 키 자체가 없고 화면은 0",
    # 사내 c-1형(측정 #6 정정) — 요약 키는 있는데 data가 빈 배열. 값의 summary_info에 identity가 그대로 있어 입구 노드가 키를 고른다.
    "data-empty": "sink가 요약 키를 갱신하지만 data가 빈 배열 — 키는 있고 화면은 0, 컬렉션엔 최근 문서가 있다",
    # R3-3 ② — 걷기가 나쁜 입력에서 멈추는 두 모양. 요약 키는 c-1형 그대로이고 쓴 쪽(batch)의 원천 키가 없거나 빈 배열이다.
    "source-missing": "요약 키 data는 빈 배열이고 batch가 읽는 원천 키가 아예 없다 — 걷기가 '없음'에서 멈춘다",
    # R3-5p ①-c — 원천 키가 없고, 그 키를 쓰는 SourceSink가 구독하는 원천 토픽도 비었다(걷기가 `구독 토픽이 비었다`에서 멈춘다).
    "source-missing-quiet": "source-missing과 같고 원천 키를 쓰는 SourceSink가 구독하는 원천 토픽(mx.alarm.raw)도 비었다 — 입력이 없다",
    # R3-5s ⑥ — 원천 키 값이 SourceSink를 대고 rows가 빈 배열이라 걷기가 그 클래스로 내려가고, 그 코드가 구독하는 원천 토픽이 비었다(토픽 입력 홉은 보존 범위·발행
    # 선언으로 — 값의 쓴 쪽을 찾지 않는다).
    "source-quiet": "요약 키 data는 빈 배열이고 원천 키 값이 SourceSink를 대며 rows가 빈 배열, SourceSink가 구독하는 원천 토픽(mx.alarm.raw)도 비었다 — 걷기가 그 "
                    "클래스의 구독 토픽 입력에서 '구독 토픽이 비었다'로 멈춘다",
    # R3-5r ② — 없는 키를 쓰는 클래스(PaletteLoader)의 코드에 구독 토픽이 없다(걷기가 서비스 선언을 읽지 않고 수만 적는다).
    "palette-missing": "palette-empty와 같되 공용 팔레트 키가 아예 없다 — 쓴 쪽 PaletteLoader(인덱스 쓰기 줄, 두 레포)의 코드엔 구독 토픽이 없다",
    # R3-5t ② — 원천 색 키가 없고 쓰는 PaletteSeeder는 레지스트리 결과를 저장해 인덱스 쓰기 줄이 0(P1-7의 모양). 같은 GBM의 sevt 시드(`peers`)가 그 키 값에 그 이름을
    # 댄다 — 걷기가 쓴 쪽 칸에 출처와 함께 채우고 그 클래스의 구독 토픽까지 걷는다.
    "palette-source-missing": "palette-empty와 같되 PaletteLoader가 읽는 원천 색 키가 아예 없다 — 쓴 쪽 PaletteSeeder는 인덱스에 쓰기 줄이 없고(레지스트리 경유) "
                              "같은 GBM의 sevt에 있는 같은 키 값이 그 이름을 댄다",
    "source-blank": "요약 키 data는 빈 배열이고 batch가 읽는 원천 키가 빈 배열이다 — 걷기가 '0행'에서 멈춘다",
    # 사내 c-2형(측정 #9) — 원천 행은 있는데 모든 행의 한 필드가 '-'이고 batch 코드가 그 값을 거른다(`r.get("state") != "-"`).
    "source-filtered": "요약 키 data는 빈 배열이고 원천 키 행은 있지만 모든 행의 state가 '-'다 — batch가 그 값을 걸러 아무것도 안 남는다",
    # 측정 #10 P0 2의 모양(R3-4 ①) — batch가 읽는 공용 키의 쓴 클래스가 서비스 디렉터리 밖·두 레포에 있다. 걷기가 레포 단위로 가르는지 잰다.
    "palette-empty": "요약 키 data는 빈 배열이고 batch가 읽는 공용 팔레트 키의 colors가 빈 배열이다 — 쓴 쪽 PaletteLoader는 dt-core의 세 서비스가 "
                     "공유하는 자리와 dt-api에 같은 이름으로 있다",
    # 측정 #11 뒤 사람이 준 정답 홉 표의 두 모양(R3-5 ④). c-1형: 요약 ← 도메인 ← 원천이 전부 빈 배열이고 원천을 쓴 클래스는 메시지 입력이다.
    "chain-empty": "요약 키 data·도메인 키 record·원천 묶음 rows가 전부 빈 배열 — 원천을 쓴 SourceCollector는 메시지를 받아 인덱스 자원이 0이고 "
                   "같은 이름이 dt-api에도 있다",
    # c-2형: 도메인·원천 행은 있는데 모든 행의 state·wg가 '-'(같은 값인 문자열 일곱 필드 뒤)이고 이 사이트의 컴포넌트에 그 값의 비교식이 있다.
    "chain-filtered": "요약 키 data는 빈 배열이고 도메인·원천 묶음 행은 있지만 모든 행의 state·wg가 '-'다 — 원천을 쓴 SourceMerger의 입력 셋은 정상이다",
    # 측정 #12의 걷기 틀린 사실 둘(R3-5b ②) — 도메인 키 `record {}`(빈 객체)와, 읽는 자원이 자기 출력뿐인 원천 클래스.
    "chain-objects": "요약 키 data는 빈 배열이고 도메인 키 record가 빈 객체다 — 원천 묶음은 빈 배열이고 그 쓴 쪽 RawAppender는 그 묶음만 읽는다(자기 출력)",
    # P1-5 조인(R3-5g ④, 사람 결정 10-20) — 사내 작업자 배지의 모양: 원천 행의 키 값이 전부 비어 기준표와 pandas inner 병합이 0건. 입력 둘은 각각 정상이다.
    "join-empty": "chain-empty와 같고 원천 묶음을 쓴 SourceJoiner가 이벤트 행과 기준표를 pandas inner 병합으로 잇는데 이벤트 행의 alarm_code가 전부 ''라 병합이 0건이다",
    # 측정 #18 P0-a(R3-5h ②) — 사내 작업자 배지의 실제 모양: outer 병합이라 행은 남고, 결과에 바로 이어진 호출이 짝 없는 칸을 '-'로 채우며 같은 파일의
    # 비교식이 그 값을 거른다. 입력 둘은 각각 정상이다.
    "join-outer": "join-empty와 같되 쓴 쪽이 SourceOuterJoiner — outer 병합 뒤 짝 없는 칸을 '-'로 채우고 그 값을 거르는 줄이 같은 파일에 있어 원천 묶음이 빈 배열",
    # 입력 읽기 실패(R3-5b ② — 키 없음과 다른 사실). palette-empty에 원천 색 키 읽기 실패를 더했다.
    "palette-unread": "palette-empty와 같고 PaletteLoader가 읽는 원천 색 키의 읽기가 실패한다(WRONGTYPE)",
    # 측정 #13 추가분 2(R3-5d ④) — 원천 묶음 키 값에 그 키를 만든 메시지의 포인터(`metadata.source`)가 있다. 걷기가 그 메시지 한 건을 입력으로 읽는다.
    "message-empty": "chain-empty와 같고 원천 묶음 키 값에 포인터가 있다 — 가리키는 메시지의 rows가 빈 배열이라 걷기가 메시지 홉에서 멈춘다(발행 선언 없는 토픽)",
    "message-gone": "chain-empty와 같고 포인터가 사이트 층이 소비·발행으로 같은 값으로 덮은 감사 토픽의 지워진 오프셋을 가리킨다 — '키를 만든 메시지가 토픽에 없다', "
                    "발행 선언은 사이트 행에서",
    "message-blank": "chain-empty와 같고 포인터 메시지의 행은 있지만 모든 행의 ws가 '-'다 — 수집기의 파서가 그 값을 걸러 원천 묶음이 빈 배열",
}
_MESSAGE_VARIANTS = {"message-empty", "message-gone", "message-blank"}
# 포인터의 자리 — 라인마다 다른 오프셋(토픽 하나, 파티션 0). message-gone은 사이트 층이 덮은 감사 토픽(dt-core 세 서비스가 소비·발행 선언)의 보존(100부터) 앞이다.
_POINTER_OFFSET = {"L1": 2, "L2": 3, "L3": 4}
_POINTER_TOPIC = {"message-gone": "gumi.alarm.audit"}
_EMPTY_DATA = {"data-empty", "source-missing", "source-missing-quiet", "source-quiet", "source-blank", "source-filtered", "palette-empty", "palette-missing",
               "palette-source-missing", "chain-empty",
               "chain-filtered", "chain-objects", "palette-unread", "join-empty", "join-outer", *_MESSAGE_VARIANTS}
# 원천 묶음의 쓴 쪽 — c-1형은 메시지 입력 클래스, chain-objects는 자기 출력만 읽는 클래스, 나머지는 입력 셋을 합치는 클래스.
_RAW_WRITER = {"chain-empty": "SourceCollector", "chain-objects": "RawAppender", "join-empty": "SourceJoiner", "join-outer": "SourceOuterJoiner",
               **{v: "SourceCollector" for v in _MESSAGE_VARIANTS}}
_FRESH_PER_LINE = {"L1": 2, "L2": 1, "L3": 3}
# 증감 배지 요약 키의 data 행 수 — 입구의 후보 본문(기본 evidence_chars 2400)이 잘릴 만큼(R3-5b ①).
_RATE_ROWS = 150


def _stats_data(docs, line, variant):
    """요약 키 값의 `data` — c-1형(`data-empty`)만 빈 배열, 나머지는 그 라인의 문서 요약."""
    if variant in _EMPTY_DATA:
        return []
    return [{"alarm_code": d["alarm_code"], "occ_date": d["occ_date"]} for d in docs if d["line"] == line]


# c-2형의 행 — 같은 값인 문자열 필드 일곱(무해) 뒤에 숫자 하나, 그 뒤에 state·wg가 '-'(측정 #11 P0 3: 필드 상한 6이 7번째 뒤를 가렸다).
_FILTERED_ROW = {"unit": "ea", "zone": "A", "shift": "D", "area": "G1", "model": "X", "mode": "auto", "grade": "1", "qty": 0, "state": "-", "wg": "-"}


def _chain_rows(docs, line, variant):
    """도메인 키 record·원천 묶음 rows — chain-empty는 빈 배열, chain-filtered는 state·wg가 전부 '-'인 다섯 행, 나머지는 그 라인의 문서 요약(정상)."""
    if variant in ("chain-empty", "chain-objects", "join-empty", "join-outer") or variant in _MESSAGE_VARIANTS:
        return []
    if variant == "chain-filtered":
        return [{"alarm_code": f"A{500 + i}", **_FILTERED_ROW} for i in range(5)]
    return [{"alarm_code": d["alarm_code"], "occ_date": d["occ_date"]} for d in docs if d["line"] == line]


def _message_rows(variant: str, i: int) -> list[dict]:
    """포인터 메시지의 행 — message-blank만 모든 행의 ws가 '-'(수집기의 파서가 거르는 값), 나머지는 빈 배열."""
    return [{"code": f"A{600 + 10 * i + k}", "ws": "-"} for k in range(5)] if variant == "message-blank" else []


def _seeds(now: datetime, variant: str = "sink-stopped") -> dict:
    if variant not in VARIANTS:
        raise ValueError(f"모르는 변형 — {variant}. 있는 것: {', '.join(VARIANTS)}")
    stale = now - timedelta(hours=4, minutes=5)
    docs = []
    for i in range(6):
        t = now - timedelta(hours=9) + timedelta(minutes=47 * i)
        docs.append({"_id": f"al-{i + 1:03d}", "line": LINES[i % 3], "occ_date": _iso(t),
                     "alarm_code": f"A{110 + i}", "level": ["minor", "major"][i % 2],
                     "sent_yn": i % 2 == 0})
    sink_alive = variant != "sink-stopped"
    if sink_alive:
        n = len(docs)
        for line, count in _FRESH_PER_LINE.items():
            for k in range(count):
                n += 1
                docs.append({"_id": f"al-{n:03d}", "line": line, "occ_date": _iso(now - timedelta(minutes=5 + 11 * k)),
                             "alarm_code": f"A{300 + n}", "level": "minor", "sent_yn": False})
    stats_fresh = variant == "healthy"
    counts = {l: (_FRESH_PER_LINE[l] if stats_fresh else 0) for l in LINES}
    fresh = lambda m: _iso(now - timedelta(minutes=m))     # noqa: E731
    return {
        "_설명": f"tools/local_case.py가 만든 가짜 데이터. 변형 {variant}: {VARIANTS[variant]}.",
        "mongo": {
            "alarm_events": docs,
            "line_state": [{"line": l, "status": "RUN", "updated_at": fresh(1)} for l in LINES]},
        "kafka": {
            # 메시지 변형(R3-5d ④)은 원천 토픽의 메시지가 포인터가 가리키는 모양이다 — rows가 빈 배열(message-empty)이거나 모든 행의 ws가 '-'(message-blank).
            # message-gone은 사이트 층이 덮은 감사 토픽의 보존이 100부터라 포인터(2~4)가 범위 밖이다(stub 시드의 `begin`).
            "mx.alarm.raw": ([{"line": LINES[i % 3], "rows": _message_rows(variant, i)} for i in range(5)] if variant in _MESSAGE_VARIANTS
                             else [] if variant in ("source-missing-quiet", "source-quiet")
                             else [{"line": LINES[i % 3], "alarm_code": f"A{200 + i}", "ts": fresh(14 - 3 * i)} for i in range(5)]),
            "mx.alarm.main": [{"line": LINES[i % 3], "alarm_code": f"A{200 + i}", "level": "minor", "ts": fresh(13 - 3 * i)} for i in range(5)],
            **({"gumi.alarm.audit": {"begin": 100, "messages": [{"line": LINES[i % 3], "note": f"audit-{i}"} for i in range(5)]}}
               if variant == "message-gone" else {})},
        # 그룹은 레포당 하나라 processor·sink가 공유한다 — lag만으로는 누가 멈췄는지 모른다.
        # 그룹은 레포 공유라 processor(raw)와 sink(main)가 같은 이름이다. 실제 Kafka는
        # 토픽별로 답하고, 어느 토픽이 밀리는지가 두 서비스를 가르는 유일한 숫자다.
        "lags": {"gumi-mx-core": {"mx.alarm.main": 0 if sink_alive else 1830, "mx.alarm.raw": 0}},
        # 요약 키 값은 사내 모양 — `record[0].summary_info`에 배지 행의 identity(group/title)가 그대로 있고 라벨(`source`)도 있다.
        # 입구 노드(R3-1 ①)는 이 identity로 키를 고른다. `alarm:scan:{line}`은 미끼 — 이름이 비슷하고 라벨은 같지만 identity가
        # 다른 배지(Caution)다(측정 #6 c-2의 모양). api가 둘 다 읽어 둘 다 후보가 된다.
        "redis": {
            **({} if variant == "cache-missing" else
               {f"alarm:stats:{l}": json.dumps({
                    "record": [{"summary_info": {"group": l, "title": "Alarm", "source": "MES"},
                                "data": _stats_data(docs, l, variant)}],
                    # 사내 모양 — 값이 쓴 파이프라인 클래스 이름을 싣는다(측정 #8). 입구 노드가 이걸로 "쓴 쪽"을 고른다.
                    "metadata": {"event_type": "SummaryRefresh"},
                    "count_1h": counts[l], "updated_at": fresh(1) if stats_fresh or variant in _EMPTY_DATA else _iso(stale)})
                for l in LINES}),
            # 쓴 쪽의 입력 — 원천 키. `source-missing`은 키가 없고 `source-blank`은 빈 배열(R3-3 ②).
            **({} if variant in ("source-missing", "source-missing-quiet") else
               {f"alarm:source:{l}": json.dumps({"metadata": {"event_type": "SourceSink"}, "rows": []} if variant == "source-quiet" else
                                                [] if variant == "source-blank" else
                                                [{"alarm_code": d["alarm_code"], "occ_date": d["occ_date"], "unit": "ea",
                                                  **({"state": "-"} if variant == "source-filtered" else {})} for d in docs if d["line"] == l])
                for l in LINES}),
            # 도메인 키·원천 묶음과 그 입력 셋(R3-5 ④ — 사내 c-1·c-2의 사슬). 기본은 정상이라 다른 변형의 걷기는 여기서 안 멈춘다.
            **{f"alarm:domain:{l}": json.dumps({"metadata": {"event_type": "DomainBuilder"},
                                                "record": {} if variant == "chain-objects" else _chain_rows(docs, l, variant)}) for l in LINES},
            **{f"alarm:raw:{l}": json.dumps({"metadata": {"event_type": _RAW_WRITER.get(variant, "SourceMerger"),
                                                          # 그 키를 만든 메시지의 포인터(사내 모양 `metadata.source`, R3-5d ④) — 메시지 변형에만.
                                                          **({"source": {"topic": _POINTER_TOPIC.get(variant, "mx.alarm.raw"), "partition": 0,
                                                                         "offset": _POINTER_OFFSET[l]}} if variant in _MESSAGE_VARIANTS else {})},
                                             "rows": _chain_rows(docs, l, variant)}) for l in LINES},
            # join-empty는 이벤트 행의 키 값이 전부 '' — 기준표(`feed:ref`의 codes)에 없는 값이라 SourceJoiner의 inner 병합이 0건(R3-5g ④).
            **{f"feed:events:{l}": json.dumps([{"id": i, "alarm_code": "" if variant in ("join-empty", "join-outer") else d["alarm_code"]}
                                               for i, d in enumerate(docs) if d["line"] == l]) for l in LINES},
            **{f"feed:status:{l}": json.dumps([{"id": i, "status": ["RUN", "STOP"][i % 2]} for i in range(4)]) for l in LINES},
            "feed:ref": json.dumps({"codes": [{"code": f"A{110 + i}", "name": f"code-{i}"} for i in range(3)]}),
            **{f"alarm:scan:{l}": json.dumps({
                    # `scope`는 그룹=제목 배지(`ALL/ALL`)의 값이 한 번 든 미끼 — identity는 개수까지 센다(R3-5c ①, 측정 #12b P0 2).
                    "record": [{"summary_info": {"group": l, "title": "Caution", "source": "MES", "scope": "ALL"}, "data": []}],
                    # 값이 쓴 쪽 이름을 **안** 싣는 키 — 클래스 이름이 아닌 문자열. 이런 키에서는 "쓴 쪽: 모른다"가 떠야 한다.
                    "metadata": {"event_type": "refresh-job"},
                    "count_1h": 0, "updated_at": fresh(1)})
               for l in LINES},
            # 제목에 '/'가 든 배지의 요약 키(R3-5b ①) — data가 커서 입구의 후보 본문이 잘리고(부모로 좁혀 다시 읽는다), 쓴 쪽 이름은 data 밖 `metadata`에 있다.
            **{f"alarm:rate:{l}": json.dumps({
                    "record": [{"summary_info": {"group": l, "title": "Up/Down Rate", "source": "MES"},
                                # 행 안의 화면 라벨이 dt-api의 스키마 클래스 이름과 같다 — 쓴 쪽은 행 밖에서만 찾는다(R3-5c ① 미끼, 측정 #12b P0 1).
                                "data": [{"seq": i, "dir": ["up", "down"][i % 2], "v": i, "view": "Overview"} for i in range(_RATE_ROWS)]}],
                    "metadata": {"event_type": "RateRefresh"}, "updated_at": fresh(1)})
               for l in LINES},
            # batch가 읽는 공용 팔레트 키 — 값이 쓴 쪽(`PaletteLoader`)을 댄다. `palette-empty`만 colors가 빈 배열(R3-4 ①), `palette-missing`은 키가 없다(R3-5r ②).
            **({} if variant == "palette-missing" else
               {"alarm:palette": json.dumps({"metadata": {"event_type": "PaletteLoader"},
                                             "colors": [] if variant in ("palette-empty", "palette-unread", "palette-source-missing")
                                             else [{"name": "alarm", "hex": "#d33"}, {"name": "caution", "hex": "#fc0"}]})}),
            # palette-unread는 이 키의 읽기가 실패한다 — stub이 오류 신호 시드(`{"__error__": …}`)를 실패로 돌려준다. palette-source-missing은 키가 없다(R3-5t ②).
            **({} if variant == "palette-source-missing" else
               {"palette:source": {"__error__": "WRONGTYPE Operation against a key holding the wrong kind of value"} if variant == "palette-unread"
                else json.dumps([{"name": "alarm", "hex": "#d33", "enabled": True}, {"name": "caution", "hex": "#fc0", "enabled": True},
                                 {"name": "normal", "hex": "#3a3", "enabled": False}])}),
            "hb:processor": fresh(0), "hb:sink": fresh(0) if sink_alive else _iso(stale)},
        # 같은 GBM의 다른 사이트(sevt)의 Redis — 걷기가 인덱스가 모르는 쓴 쪽을 거기 같은 키 값에서 채운다(R3-5t ②). 그 변형에만 — 다른 변형의 걷기는 안 묻는다.
        **({"peers": {"sevt": {"redis": {"sevt:palette:source": json.dumps({"metadata": {"event_type": "PaletteSeeder",
                                                                                    # 그 키를 만든 메시지의 포인터 — sevt 이름의 토픽(R3-5u ①)
                                                                                    "source": {"topic": "sevt.alarm.main", "partition": 0, "offset": 3}},
                                                                       "colors": [{"name": "alarm", "hex": "#d33", "enabled": True}]})}}}}
           if variant == "palette-source-missing" else {}),
        # 등재된 항목 전부에 답을 둔다 — 없는 항목은 stub이 404를 내고, 그건 리드에게
        # "API가 죽었다"로 읽힌다(첫 로컬 실행에서 `prod_status`가 그랬다).
        "rest": {"lines": LINES,
                 # 사내 모양 — 라인×항목의 행 목록(식별은 group/title, 케이스 target `L1/Alarm`이 그 둘을 이은 것).
                 # 대상 행이 뒤쪽에 오게 둔다: 증거가 첫 행에서 잘리면 리드가 대상 행을 못 보는 사내 실측의 재현이다.
                 "summary_badge": [{"group": l, "title": t, "alarm": counts[l] if t == "Alarm" else 0,
                                    "caution": 0, "normal": 3,
                                    "link": f"/summary/badge/{BADGE_LINKS.get(t, t.lower())}/"}
                                   for t in BADGE_TITLES for l in LINES]
                                  # 그룹과 제목이 같은 배지(R3-5c ①) — 그 값이 한 번 든 미끼 키와 맞으면 안 된다.
                                  + [{"group": "ALL", "title": "ALL", "alarm": 0, "caution": 0, "normal": 3, "link": "/summary/badge/all/"}],
                 "prod_status": [{"line_code": l, "status": "RUN", "updated_at": fresh(1)} for l in LINES],
                 "oee_summary": {"line": "L3", "oee": 0.87, "date": _iso(now)[:10]}},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("output/local-case"))
    ap.add_argument("--case-id", default="c-local-1")
    ap.add_argument("--variant", default="sink-stopped", choices=sorted(VARIANTS),
                    help="심은 고장 — " + " / ".join(f"{k}: {v}" for k, v in VARIANTS.items()))
    args = ap.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    # config 사본 — LLM만 파일 턴으로, 저장소·출력은 이 폴더 안으로.
    cfg = root / "config"
    if cfg.exists():
        shutil.rmtree(cfg)
    shutil.copytree(HERE / "config", cfg)
    app = json.loads((cfg / "app.json").read_text(encoding="utf-8"))
    app["llm"] = {"adapter": "file", "model": "standin", "turn_dir": str(root / "turns")}
    app["case_store"] = str(root / "output" / "cases.json")
    app["output_dir"] = str(root / "output")
    app["mail"]["enabled"] = False
    _write(cfg, "app.json", app)
    site = json.loads((cfg / "gbm" / "mx.json").read_text(encoding="utf-8"))
    for repo in site["code"]["repos"]:
        repo["path"] = str(root / "target-code" / repo["name"])
        repo.pop("token", None)
    _write(cfg, "gbm/mx.json", site)
    urls = {r["name"]: r["url"] for r in site["code"]["repos"]}

    know = root / "knowledge"
    if know.exists():
        shutil.rmtree(know)
    shutil.copytree(HERE / "knowledge", know)
    # 측정판은 **자기 서비스 지도**를 쓴다 — 리포의 `knowledge/topology/mx.json`은 사내에서 실제 서비스 이름으로 바뀌는 파일이라 거기에
    # 측정판 서비스(`batch`)를 적지 않는다(883175e가 적어 사내 테스트가 깨졌다, 10-14). 사본의 `services`만 바꾸고 흐름 출처·config 경로는 그대로.
    topo_path = know / "topology" / "mx.json"
    _write(know, "topology/mx.json", _board_topology(json.loads(topo_path.read_text(encoding="utf-8"))))

    shared = _repo(root / "target-code" / "_shared" / "shared-lib", "shared-lib",
                   "https://git.example.com/team/shared-lib", SHARED_FILES)
    _submodule(_repo(root / "target-code" / "dt-core", "dt-core", urls["dt-core"], CORE_FILES), shared,
               path="shared_lib")
    _repo(root / "target-code" / "dt-api", "dt-api", urls["dt-api"], API_FILES)

    # 이 도구는 CLI 경계다 — 가짜 데이터의 "지금"은 실제 지금이어야 stub의 $gte가 뜻을 가진다.
    now = datetime.now()
    TZ = json.loads((cfg / "app.json").read_text(encoding="utf-8"))["timezone"]
    _write(root, "seeds.json", _seeds(now, args.variant))
    _write(root, ".env", "\n".join(f"{k}=local-dummy" for k in (
        "MONGO_PASSWORD", "REDIS_PASSWORD", "MAIL_AGENT_ID", "MAIL_AGENT_API_KEY")))

    from src.domain.cases import CaseRecord
    from src.infrastructure.case_store_file import FileCaseRepository
    store = FileCaseRepository(root / "output" / "cases.json")
    if not any(c.id == args.case_id for c in store.all()):
        store.add(CaseRecord(
            # config/gbm/common.json의 점검이다 — 브리핑이 여기서 출발점(프로브 → REST path)을 되짚는다.
            id=args.case_id, site="mx/gumi", check="badge_all_zero", target="L1/Alarm",
            # 앱 시계와 같은 시간대로 — 시간대 없는 값을 적으면 `case list`의 `sustained_for`가 앱 시계와 못 뺀다(실제로 죽었다).
            concern="system", opened_at=now.astimezone(ZoneInfo(TZ)), last_seen_at=now.astimezone(ZoneInfo(TZ)),
            symptom="gumi MX 알람 화면에 새 알람이 4시간째 안 올라온다. 라인은 정상 가동 중이라고 한다.",
            observed={"recent_alarms": 0, "window_min": 60}))
    for stale in (root / "turns").glob("*"):
        stale.unlink()

    py = Path(sys.executable)
    print(f"측정판: {root} — 변형 {args.variant}: {VARIANTS[args.variant]}\n")
    print(f"{py} -m src --config-root {cfg} --env-file {root / '.env'} code graph      # 흐름 그래프(+graphify)")
    print(f"{py} -m src --config-root {cfg} --env-file {root / '.env'} code flow processor --to sink")
    print(f"{py} -m src --config-root {cfg} --env-file {root / '.env'} "
          f"case investigate {args.case_id} --stub-seeds {root / 'seeds.json'} --trace {root / 'trace'}")
    print(f"{py} -m src --config-root {cfg} --env-file {root / '.env'} "
          f"case trace {args.case_id} --trace {root / 'trace'}")
    print(f"{py} -m src --config-root {cfg} --env-file {root / '.env'} "
          f"case walk --target L1/Alarm --check badge_all_zero --stub-seeds {root / 'seeds.json'} --gbm mx --fct gumi      # LLM 없이 입구 + 걷기")
    print(f"\n리드 자리: {root / 'turns'}/NNN-ask.md 를 읽고 NNN-reply.md 를 써 준다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
