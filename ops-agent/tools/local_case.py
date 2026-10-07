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
        "redis_key": {"alarm_stats": "alarm:stats:{line}", "heartbeat": "hb:{service}"},
        "sink": {"batch_size": 200, "flush_sec": 5}},
    "config/factories/gumi/common.json": {"lines": LINES, "site_code": "gumi"},
    "config/factories/gumi/mx.json": {
        "infra": {"kafka": {"consumer": {"group_id": "gumi-mx-core"}}}},
    # 두 번째 사이트 — 11e-2의 "번들은 GBM 하나, 사이트는 덮은 값만"을 측정판에서 보기 위해서다.
    "config/factories/sevt/common.json": {"lines": LINES, "site_code": "sevt"},
    "config/factories/sevt/mx.json": {
        "infra": {"kafka": {"consumer": {"group_id": "sevt-mx-core"}}}},
    "processor/handler.py": '''"""alarm_raw를 읽어 정규화한 뒤 alarm_main으로 낸다."""
from shared_lib import normalize, now


def run(cfg, consumer, producer, redis):
    kafka = cfg["infra"]["kafka"]
    for msg in consumer.subscribe(kafka["consumer"]["topic"]["topic1"], group=kafka["consumer"]["group_id"]):
        event = normalize(msg)
        producer.send(kafka["producer"]["topic"]["topic1"], event)
        redis.set(cfg["redis_key"]["heartbeat"].format(service="processor"), now())
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
}

# 일부 서비스는 이름을 객체 안에 둔다(`{"collection": …, "ttl": 3}`, 사내 확인). dt-api가 그 모양이다.
API_FILES = {
    "config/gbm/mx.json": {
        "infra": {"mongodb": {"database": "data"}},
        "mongodb_collection": {"alarm": {"collection": "alarm_events", "ttl": 3}},
        "redis_key": {"alarm_stats": {"key": "alarm:stats:{line}", "ttl": 30}},
        "api": {"alarm_window_min": 60, "badge_format": "compact"}},
    "config/factories/gumi/common.json": {"lines": LINES},
    "config/factories/sevt/common.json": {"lines": LINES},
    # 사내 핸들러 모양(2026-09 확인): 캐시 키를 먼저 보고, 비면 저장소를 조회하고, 형식 같은 것은
    # config 이름으로 getattr해 고른다. 캐시만 읽는 핸들러로는 "핸들러에서 컬렉션까지 이어지나"를 잴 수 없다.
    "api/alarms.py": '''"""알람 화면 — alarm:stats:{line} 배지. 캐시가 비면 최근 alarm_window_min 분의 alarm_events에서 센다."""


router = APIRouter(prefix="/summary")


@router.post("/badge")
def summary_badge(cfg, mongo, redis):
    return {line: badge(cfg, mongo, redis, line) for line in cfg["lines"]}


def badge(cfg, mongo, redis, line):
    cached = redis.get(cfg["redis_key"]["alarm_stats"]["key"].format(line=line))
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
}
_FRESH_PER_LINE = {"L1": 2, "L2": 1, "L3": 3}


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
            "mx.alarm.raw": [{"line": LINES[i % 3], "alarm_code": f"A{200 + i}", "ts": fresh(14 - 3 * i)}
                             for i in range(5)],
            "mx.alarm.main": [{"line": LINES[i % 3], "alarm_code": f"A{200 + i}", "level": "minor",
                               "ts": fresh(13 - 3 * i)} for i in range(5)]},
        # 그룹은 레포당 하나라 processor·sink가 공유한다 — lag만으로는 누가 멈췄는지 모른다.
        # 그룹은 레포 공유라 processor(raw)와 sink(main)가 같은 이름이다. 실제 Kafka는
        # 토픽별로 답하고, 어느 토픽이 밀리는지가 두 서비스를 가르는 유일한 숫자다.
        "lags": {"gumi-mx-core": {"mx.alarm.main": 0 if sink_alive else 1830, "mx.alarm.raw": 0}},
        "redis": {
            **{f"alarm:stats:{l}": json.dumps({"count_1h": counts[l],
                                               "updated_at": fresh(1) if stats_fresh else _iso(stale)})
               for l in LINES},
            "hb:processor": fresh(0), "hb:sink": fresh(0) if sink_alive else _iso(stale)},
        # 등재된 항목 전부에 답을 둔다 — 없는 항목은 stub이 404를 내고, 그건 리드에게
        # "API가 죽었다"로 읽힌다(첫 로컬 실행에서 `prod_status`가 그랬다).
        "rest": {"lines": LINES,
                 "summary_badge": {l: {"alarm": counts[l], "caution": 0, "normal": 3} for l in LINES},
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
    print(f"\n리드 자리: {root / 'turns'}/NNN-ask.md 를 읽고 NNN-reply.md 를 써 준다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
