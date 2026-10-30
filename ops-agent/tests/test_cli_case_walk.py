"""R3-5 ② — `case walk`: LLM 없이 입구 + 걷기만 돌리고 케이스 블록만 찍는 측정 도구(읽기 전용). 엔진 기능이 아니다."""
import json

from tests.config.test_boot import ENV, _with_patrol
from tests.support import set_real_config_env

PROBES = {"badge": {"action": "rest.query", "params": {"entry": "summary_badge", "params": {}}}}


def _run(config_root, tmp_path, monkeypatch, capsys, *argv):
    from src.__main__ import main

    set_real_config_env(monkeypatch)
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("sys.argv", ["src", "--config-root", str(config_root), "--env-file", str(tmp_path / "none"), *argv])
    code = main()
    return code, capsys.readouterr()


def _root(tmp_path):
    root = _with_patrol(tmp_path / "c", PROBES)
    app = json.loads((root / "app.json").read_text(encoding="utf-8"))
    app["case_store"] = str(tmp_path / "cases.json")
    app["output_dir"] = str(tmp_path / "out")
    (root / "app.json").write_text(json.dumps(app), encoding="utf-8")
    return root


def test_case_walk은_케이스_id나_target_하나만_받고_check는_필수이며_틀리면_점검_목록을_보인다(tmp_path, monkeypatch, capsys):
    """사람 결정(10-15): `--check`는 기본값 없이 필수, 틀리면 점검 목록. `--target`은 순찰의 대상 문자열 그대로(쪼개지 않는다). `--key`(R3-5b ③)까지
    셋 중 하나만."""
    root = _root(tmp_path)
    code, out = _run(root, tmp_path, monkeypatch, capsys, "case", "walk")
    assert code == 2 and "셋 중 하나만" in out.out
    code, out = _run(root, tmp_path, monkeypatch, capsys, "case", "walk", "c-1", "--target", "L1/Alarm")
    assert code == 2 and "셋 중 하나만" in out.out
    code, out = _run(root, tmp_path, monkeypatch, capsys, "case", "walk", "--target", "L1/Alarm", "--key", "alarm:stats:L1")
    assert code == 2 and "셋 중 하나만" in out.out
    code, out = _run(root, tmp_path, monkeypatch, capsys, "case", "walk", "--target", "L1/Alarm", "--gbm", "mx", "--fct", "gumi")
    assert code == 2 and "--check가 필요하다 — 점검: badge_all_zero" in out.out
    code, out = _run(root, tmp_path, monkeypatch, capsys, "case", "walk", "--target", "L1/Alarm", "--check", "nope", "--gbm", "mx", "--fct", "gumi")
    assert code == 2 and "없는 점검 — nope. 점검: badge_all_zero" in out.out
    code, out = _run(root, tmp_path, monkeypatch, capsys, "case", "walk", "c-없음")
    assert code == 1 and "없는 케이스 — c-없음" in out.out


def test_case_walk은_target으로_입구를_돌리고_케이스_블록만_찍으며_아무것도_남기지_않는다(tmp_path, monkeypatch, capsys):
    """stub seeds로 접수 끝점을 읽어 대상 행을 고른다 — 코드 번들이 없으니 걷기는 없고 그 사실을 꼬리에 적는다. 케이스 저장소·보고서·트레이스는 그대로."""
    root = _root(tmp_path)
    seeds = {"rest": {"summary_badge": [{"group": "L1", "title": "Alarm", "alarm": 0, "caution": 0, "normal": 0},
                                        {"group": "L1", "title": "Up/Down Rate", "alarm": 0, "caution": 0, "normal": 0}]},
             "redis": {"alarm:stats:L1": json.dumps({"record": [{"summary_info": {"group": "L1", "title": "Alarm"}, "data": []}]})}}
    (tmp_path / "seeds.json").write_text(json.dumps(seeds, ensure_ascii=False), encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())
    code, out = _run(root, tmp_path, monkeypatch, capsys, "case", "walk", "--target", "L1/Up/Down Rate", "--check", "badge_all_zero",
                     "--stub-seeds", str(tmp_path / "seeds.json"), "--gbm", "mx", "--fct", "gumi")
    assert code == 0, out.out + out.err
    assert "사이트: mx/gumi" in out.out and "증상: L1/Up/Down Rate — case walk(측정)" in out.out
    assert "입구 노드가 이미 읽었다: 접수 읽기(t-0.e1)" in out.out
    # 제목의 '/'를 쪼개지 않고 통째로 대상 행을 골랐다 — 코드 번들이 없어 후보 키가 없다는 데서 멈추지만 대상 행은 찾았다. R3-5 ②의 이 테스트는
    # 위 줄만 보고 "골랐다"고 적었는데 실제로는 "대상 행이 없다"였다(측정 #12 행 9, R3-5b ①).
    assert "(response[1] 대상 행은 찾았다)" in out.out and "대상 행이 없다" not in out.out
    assert "걷기 없음" in out.out and "읽기 전용" in out.out
    assert sorted(p.name for p in tmp_path.iterdir()) == before                                 # cases.json·out/ 어느 것도 생기지 않았다


def test_peer_reader는_같은_GBM의_다른_사이트에_같은_키를_그_사이트의_이름으로_묻고_없으면_같은_템플릿의_첫_키를_읽고_안_닿는_사이트는_다시_안_묻는다(tmp_path):
    """R3-5t ②·④ — 걷기의 `peer(키, 템플릿)`: registry의 같은 GBM 다른 사이트를 차례로(키마다 `PEER_SITES`곳까지), stub은 시드 `peers.<fct>.redis`. 사이트마다
    키에 자기 접두어가 붙으니(측정 #30: 이 사이트 키를 글자 그대로 물어 채움 0) 물을 키는 **그 사이트의 이름 목록**(이 사이트 키를 만든 것과 같은 `names_for`)에서
    같은 key_path의 값으로 다시 만들고 자리는 이 사이트 키에서 채운다 — 문자열 바꿈 규칙이 아니다; scan도 그 사이트 머리로. 그 사이트가 그 key_path를 안 선언하면
    거기엔 없는 것(글자 그대로의 키가 있어도 안 읽는다). 이름 목록이 없으면 글자 그대로. 같은 키가 없으면 같은 템플릿의 첫 키, 그것도 없으면 다음 사이트. 한 번 못
    읽은 사이트는 그 걷기에서 다시 안 묻는다. 던지지 않는다."""
    import asyncio
    from datetime import datetime
    from src.__main__ import PEER_SITES, _peer_reader
    from src.knowledge.flow import Name

    clock = lambda: datetime(2026, 10, 15, 9, 0, 0)          # noqa: E731 — stub 봉투의 observed_at
    root = _root(tmp_path)
    # 다른 GBM(nw/vina)과 비활성(mx/off)은 같은 키를 들고 있어도 안 묻는다 — registry 순서상 앞에 둬서 거르지 않으면 그쪽이 먼저 답한다.
    (root / "registry.json").write_text(json.dumps({"sites": [{"gbm": "mx", "fct": "gumi"}, {"gbm": "nw", "fct": "vina"}, {"gbm": "mx", "fct": "off", "enabled": False},
                                                              {"gbm": "mx", "fct": "sevt"}, {"gbm": "mx", "fct": "suwon"}]}), encoding="utf-8")
    declared = {"gumi": ("k", "other"), "sevt": ("k",), "suwon": ("k", "other"), "vina": ("k",), "off": ("k",)}

    async def names_for(site):
        # 사이트마다 자기 접두어 — `<사이트>:k:{line}`·`<사이트>:other`(사내 `키 접두어 항목`의 모양). sevt는 `other`를 선언하지 않는다. 토픽도 사이트 이름 —
        # 값의 `metadata.source.topic`을 이 사이트 이름으로 바꾸는 표(R3-5u ①)가 같은 key_path로 선다.
        return ([Name("rediskey", f"{site}:{tok}" + (":{line}" if tok == "k" else ""), f"redis_key.{tok}", code_value=tok) for tok in declared[site]]
                + [Name("topic", f"{site}.raw", "infra.kafka.consumer.topic.topic1", "consumes")]), []

    w1 = json.dumps({"metadata": {"event_type": "W1"}})
    seeds = {"redis": {}, "peers": {"vina": {"redis": {"vina:k:L1": "nw"}}, "off": {"redis": {"off:k:L1": "off"}},
                                    "sevt": {"redis": {"sevt:k:L7": w1, "gumi:other": "wrong"}},
                                    "suwon": {"redis": {"suwon:k:L1": json.dumps({"metadata": {"event_type": "W2"}}), "suwon:other": "x"}}}}
    peer, close = _peer_reader(root, "mx", "gumi", env=dict(ENV), clock=clock, seeds=seeds, names_for=names_for)
    # 이 사이트 키 `gumi:k:L1` → sevt의 이름 `sevt:k:{line}` → `sevt:k:L1`은 없고 같은 템플릿의 첫 키(scan `sevt:k:*`) — suwon의 똑같은 자리 키보다 먼저
    # 넷째는 그 사이트의 토픽 값 → 이 사이트의 토픽 값(같은 key_path) — 값의 source.topic을 이 사이트 이름으로 바꾸는 표(R3-5u ①)
    assert asyncio.run(peer("gumi:k:L1", "gumi:k:{line}")) == ("sevt", "sevt:k:L7", {"type": "string", "value": w1}, {"sevt.raw": "gumi.raw"})
    # sevt는 `other`를 선언하지 않는다 — 글자 그대로의 키를 들고 있어도 안 읽고 suwon의 이름으로
    assert asyncio.run(peer("gumi:other", "gumi:other")) == ("suwon", "suwon:other", {"type": "string", "value": "x"}, {"suwon.raw": "gumi.raw"})
    # 이 사이트 이름에 없는 템플릿은 글자 그대로 — 어디에도 없다
    assert asyncio.run(peer("gumi:none:L1", "gumi:none:{line}")) is None
    asyncio.run(close())
    # 이름 목록이 없으면(인덱스 없음) 글자 그대로
    plain = {"redis": {}, "peers": {"sevt": {"redis": {"k:L7": w1}}}}
    peer, close = _peer_reader(root, "mx", "gumi", env=dict(ENV), clock=clock, seeds=plain)
    assert asyncio.run(peer("k:L1", "k:{line}")) == ("sevt", "k:L7", {"type": "string", "value": w1}, {})
    asyncio.run(close())
    # 읽기가 실패한 사이트(오류 시드)는 그 뒤로 안 묻는다 — 첫 키는 다음 사이트의 같은 템플릿 키로, 둘째 키는 sevt에 있어도 suwon에서
    bad = {"redis": {}, "peers": {"sevt": {"redis": {"sevt:k:L1": {"__error__": "down"}, "sevt:k:L2": "y"}}, "suwon": {"redis": {"suwon:k:L2": "z"}}}}
    peer, close = _peer_reader(root, "mx", "gumi", env=dict(ENV), clock=clock, seeds=bad, names_for=names_for)
    assert asyncio.run(peer("gumi:k:L1", "gumi:k:{line}")) == ("suwon", "suwon:k:L2", {"type": "string", "value": "z"}, {"suwon.raw": "gumi.raw"})
    assert asyncio.run(peer("gumi:k:L2", "gumi:k:{line}")) == ("suwon", "suwon:k:L2", {"type": "string", "value": "z"}, {"suwon.raw": "gumi.raw"})
    asyncio.run(close())
    # 사이트가 하나뿐이면 늘 None(묻지 않는다); 상한은 상수
    (root / "registry.json").write_text(json.dumps({"sites": [{"gbm": "mx", "fct": "gumi"}]}), encoding="utf-8")
    peer, close = _peer_reader(root, "mx", "gumi", env=dict(ENV), clock=clock, seeds=seeds, names_for=names_for)
    assert asyncio.run(peer("gumi:k:L1", "gumi:k:{line}")) is None and PEER_SITES == 3
    asyncio.run(close())
