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
    """사람 결정(10-15): `--check`는 기본값 없이 필수, 틀리면 점검 목록. `--target`은 순찰의 대상 문자열 그대로(쪼개지 않는다)."""
    root = _root(tmp_path)
    code, out = _run(root, tmp_path, monkeypatch, capsys, "case", "walk")
    assert code == 2 and "둘 중 하나만" in out.out
    code, out = _run(root, tmp_path, monkeypatch, capsys, "case", "walk", "c-1", "--target", "L1/Alarm")
    assert code == 2 and "둘 중 하나만" in out.out
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
