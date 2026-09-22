"""로컬 대역 측정판 — **사내 없이 같은 배선을 끝까지 돌린다.**

사내 모델은 이 리포 밖에서만 돈다. 그동안 측정은 매번 사내에 부탁했고, 결과를 사람이
손으로 옮겨야 했다 — 11a 후반의 왕복 전부가 그것이었다. 이 도구는 여기서 돌릴 수 있는
케이스 하나를 통째로 만든다: config 사본(LLM만 파일 턴 어댑터), knowledge 사본, 대상
config 층이 든 가짜 git 레포 둘, stub seed, 케이스 레코드. 리드 자리에 무엇을 세우든
(사람, 다른 모델) `turns/NNN-ask.md`를 읽고 `NNN-reply.md`를 써 주면 된다.

**여기 이름은 전부 지어낸 것이다**(decisions ⑮ — 사내 실물 이름을 테스트 데이터에 넣지
않는다). 심은 고장은 하나다: sink 컨슈머 그룹이 멈춰 lag가 쌓이고, `alarm_events`에 새
문서가 안 들어오고, API 화면에 알람이 안 올라온다. 토픽에는 새 메시지가 계속 온다.

    .venv/bin/python tools/local_case.py --root output/local-case
"""
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

LINES = ["L1", "L2", "L3"]


def _git(*args, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   encoding="utf-8", errors="replace")


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


CORE_FILES = {
    "config/gbm/mx.json": {
        "kafka": {"topics": {"alarm_raw": "mx.alarm.raw", "alarm_main": "mx.alarm.main"},
                  "groups": {"processor": "mx-processor", "sink": "mx-sink"}},
        "mongo": {"collections": {"alarm": "alarm_events", "line_state": "line_state"}},
        "redis": {"keys": {"alarm_stats": "alarm:stats:{line}", "heartbeat": "hb:{service}"}},
        "sink": {"batch_size": 200, "flush_sec": 5}},
    "config/factories/gumi/common.json": {"lines": LINES, "site_code": "gumi"},
    "config/factories/gumi/mx.json": {
        "kafka": {"groups": {"processor": "gumi-mx-processor", "sink": "gumi-mx-sink"}}},
    "processor/handler.py": '''"""alarm_raw를 읽어 정규화한 뒤 alarm_main으로 낸다."""


def run(cfg, consumer, producer, redis):
    topics, groups = cfg["kafka"]["topics"], cfg["kafka"]["groups"]
    for msg in consumer.subscribe(topics["alarm_raw"], group=groups["processor"]):
        event = normalize(msg)
        producer.send(topics["alarm_main"], event)
        redis.set(cfg["redis"]["keys"]["heartbeat"].format(service="processor"), now())
''',
    "sink/writer.py": '''"""alarm_main을 읽어 alarm_events에 넣고 alarm:stats:{line}을 갱신한다."""


def run(cfg, consumer, mongo, redis):
    topics, groups = cfg["kafka"]["topics"], cfg["kafka"]["groups"]
    batch = []
    for msg in consumer.subscribe(topics["alarm_main"], group=groups["sink"]):
        batch.append(msg)
        if len(batch) >= cfg["sink"]["batch_size"]:
            mongo[cfg["mongo"]["collections"]["alarm"]].insert_many(batch)
            for line in {m["line"] for m in batch}:
                redis.set(cfg["redis"]["keys"]["alarm_stats"].format(line=line), stats(line))
            redis.set(cfg["redis"]["keys"]["heartbeat"].format(service="sink"), now())
            batch = []
''',
}

API_FILES = {
    "config/gbm/mx.json": {
        "mongo": {"collections": {"alarm": "alarm_events"}},
        "redis": {"keys": {"alarm_stats": "alarm:stats:{line}"}},
        "api": {"alarm_window_min": 60}},
    "config/factories/gumi/common.json": {"lines": LINES},
    "api/alarms.py": '''"""알람 화면 — 최근 alarm_window_min 분의 alarm_events와 alarm:stats:{line} 배지."""


def recent_alarms(cfg, mongo, since):
    coll = mongo[cfg["mongo"]["collections"]["alarm"]]
    return list(coll.find({"occ_date": {"$gte": since}}).sort("occ_date", -1))


def badge(cfg, redis, line):
    return redis.get(cfg["redis"]["keys"]["alarm_stats"].format(line=line))
''',
}


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat()


def _seeds(now: datetime) -> dict:
    stale = now - timedelta(hours=4, minutes=5)
    docs = []
    for i in range(6):
        t = now - timedelta(hours=9) + timedelta(minutes=47 * i)
        docs.append({"_id": f"al-{i + 1:03d}", "line": LINES[i % 3], "occ_date": _iso(t),
                     "alarm_code": f"A{110 + i}", "level": ["minor", "major"][i % 2],
                     "sent_yn": i % 2 == 0})
    fresh = lambda m: _iso(now - timedelta(minutes=m))     # noqa: E731
    return {
        "_설명": "tools/local_case.py가 만든 가짜 데이터. sink가 멈춘 상황.",
        "mongo": {
            "alarm_events": docs,
            "line_state": [{"line": l, "status": "RUN", "updated_at": fresh(1)} for l in LINES]},
        "kafka": {
            "mx.alarm.raw": [{"line": LINES[i % 3], "alarm_code": f"A{200 + i}", "ts": fresh(14 - 3 * i)}
                             for i in range(5)],
            "mx.alarm.main": [{"line": LINES[i % 3], "alarm_code": f"A{200 + i}", "level": "minor",
                               "ts": fresh(13 - 3 * i)} for i in range(5)]},
        "lags": {"gumi-mx-sink": 1830, "gumi-mx-processor": 2},
        "redis": {
            **{f"alarm:stats:{l}": json.dumps({"count_1h": 0, "updated_at": _iso(stale)}) for l in LINES},
            "hb:processor": fresh(0), "hb:sink": _iso(stale)},
        # 등재된 항목 전부에 답을 둔다 — 없는 항목은 stub이 404를 내고, 그건 리드에게
        # "API가 죽었다"로 읽힌다(첫 로컬 실행에서 `prod_status`가 그랬다).
        "rest": {"lines": LINES,
                 "summary_badge": {l: {"alarm": 0, "caution": 0, "normal": 3} for l in LINES},
                 "prod_status": [{"line_code": l, "status": "RUN", "updated_at": fresh(1)} for l in LINES],
                 "oee_summary": {"line": "L3", "oee": 0.87, "date": _iso(now)[:10]}},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("output/local-case"))
    ap.add_argument("--case-id", default="c-local-1")
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

    _repo(root / "target-code" / "dt-core", "dt-core", urls["dt-core"], CORE_FILES)
    _repo(root / "target-code" / "dt-api", "dt-api", urls["dt-api"], API_FILES)

    # 이 도구는 CLI 경계다 — 가짜 데이터의 "지금"은 실제 지금이어야 stub의 $gte가 뜻을 가진다.
    now = datetime.now()
    _write(root, "seeds.json", _seeds(now))
    _write(root, ".env", "\n".join(f"{k}=local-dummy" for k in (
        "MONGO_PASSWORD", "REDIS_PASSWORD", "MAIL_AGENT_ID", "MAIL_AGENT_API_KEY")))

    from src.domain.cases import CaseRecord
    from src.infrastructure.case_store_file import FileCaseRepository
    store = FileCaseRepository(root / "output" / "cases.json")
    if not any(c.id == args.case_id for c in store.all()):
        store.add(CaseRecord(
            id=args.case_id, site="mx/gumi", check="alarm_flow", target="alarm_events",
            concern="system", opened_at=now, last_seen_at=now,
            symptom="gumi MX 알람 화면에 새 알람이 4시간째 안 올라온다. 라인은 정상 가동 중이라고 한다.",
            observed={"recent_alarms": 0, "window_min": 60}))
    for stale in (root / "turns").glob("*"):
        stale.unlink()

    py = Path(sys.executable)
    print(f"측정판: {root}\n")
    print(f"{py} -m src --config-root {cfg} --env-file {root / '.env'} "
          f"case investigate {args.case_id} --stub-seeds {root / 'seeds.json'} --trace {root / 'trace'}")
    print(f"{py} -m src --config-root {cfg} --env-file {root / '.env'} "
          f"case trace {args.case_id} --trace {root / 'trace'}")
    print(f"\n리드 자리: {root / 'turns'}/NNN-ask.md 를 읽고 NNN-reply.md 를 써 준다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
