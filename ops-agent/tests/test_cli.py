"""CLI 경계 — 어느 사이트를 볼 것인가를 어떻게 정하는가.

여기서 틀리면 **다른 법인의 Redis를 들여다본다.** 그건 에러가 아니라 조용히
잘못된 답이라서, 사람이 알아채기까지 오래 걸린다.
"""
import json

import pytest

from src.__main__ import _resolve_site, parse_args

ENV = {"REDIS_PASSWORD": "hunter2"}


@pytest.fixture
def config_root(tmp_path):
    root = tmp_path / "config"
    (root / "gbm").mkdir(parents=True)
    (root / "fct" / "gumi").mkdir(parents=True)
    (root / "fct" / "sevt").mkdir(parents=True)
    (root / "app.json").write_text('{"timezone": "Asia/Seoul"}', encoding="utf-8")
    (root / "registry.json").write_text(json.dumps({"sites": [
        {"gbm": "mx", "fct": "gumi"}, {"gbm": "mx", "fct": "sevt"}]}), encoding="utf-8")
    (root / "gbm" / "common.json").write_text(
        json.dumps({"infra": {"redis": {"db": 0, "password": "${REDIS_PASSWORD}"}}}),
        encoding="utf-8")
    for fct in ("gumi", "sevt"):
        (root / "fct" / fct / "mx.json").write_text(
            json.dumps({"infra": {"redis": {"url": f"redis://{fct}:6379"}}}), encoding="utf-8")
    return root


def _parse(*argv):
    """프로덕션과 **같은 입구**를 쓴다 — build_parser를 직접 부르면 병합 단계를
    건너뛰어, 실제로 깨지는 조합이 테스트에서는 통과한다."""
    return parse_args(list(argv))


# ── --gbm/--fct의 위치 ────────────────────────────────────────────────

def test_하위_명령_앞에_써도_된다(config_root):
    args = _parse("--gbm", "mx", "--fct", "sevt", "peek", "redis", "--key", "k")
    site, _ = _resolve_site(config_root, args, ENV)
    assert str(site.site) == "mx/sevt"


def test_하위_명령_뒤에_써도_된다(config_root):
    """argparse의 전역 옵션은 원래 앞에만 온다. 사람은 뒤에 쓰는 쪽이 자연스럽다."""
    args = _parse("peek", "redis", "--key", "k", "--gbm", "mx", "--fct", "sevt")
    site, _ = _resolve_site(config_root, args, ENV)
    assert str(site.site) == "mx/sevt"


def test_뒤에_안_쓰면_앞의_값이_살아_있다(config_root):
    # 같은 dest를 양쪽에 달면 하위 파서의 "안 줬음"이 전역 값을 덮어쓰는 파이썬
    # 버전이 있다 — Linux는 통과하고 Windows에서 깨졌던 지점이다.
    args = _parse("--gbm", "mx", "--fct", "gumi", "peek", "redis", "--key", "k")
    assert (args.gbm, args.fct) == ("mx", "gumi")


def test_양쪽에_다_쓰면_뒤가_이긴다(config_root):
    args = _parse("--fct", "gumi", "peek", "redis", "--key", "k", "--fct", "sevt")
    assert args.fct == "sevt"
    site, _ = _resolve_site(config_root, args, ENV)
    assert str(site.site) == "mx/sevt"


def test_어느_위치든_같은_결과다(config_root):
    """argparse 내부 동작에 기대지 않는다는 것을 양쪽으로 확인한다."""
    before, _ = _resolve_site(config_root,
                              _parse("--fct", "sevt", "peek", "redis", "--key", "k"), ENV)
    after, _ = _resolve_site(config_root,
                             _parse("peek", "redis", "--key", "k", "--fct", "sevt"), ENV)
    assert str(before.site) == str(after.site) == "mx/sevt"


def test_doctor와_config_show에도_붙는다(config_root):
    for argv in (("doctor", "--fct", "sevt"), ("config", "show", "--fct", "sevt")):
        site, _ = _resolve_site(config_root, _parse(*argv), ENV)
        assert str(site.site) == "mx/sevt"


# ── 좁히기 ───────────────────────────────────────────────────────────

def test_한쪽만_줘도_유일하면_통한다(config_root):
    # 사업부가 mx뿐이면 --fct만으로 충분하다.
    site, _ = _resolve_site(config_root, _parse("peek", "redis", "--fct", "sevt"), ENV)
    assert str(site.site) == "mx/sevt"


def test_활성_사이트가_하나면_생략해도_된다(tmp_path, config_root):
    (config_root / "registry.json").write_text(
        json.dumps({"sites": [{"gbm": "mx", "fct": "gumi"},
                              {"gbm": "mx", "fct": "sevt", "enabled": False}]}),
        encoding="utf-8")
    site, _ = _resolve_site(config_root, _parse("peek", "redis"), ENV)
    assert str(site.site) == "mx/gumi"


def test_좁혀지지_않으면_임의로_고르지_않고_묻는다(config_root):
    with pytest.raises(SystemExit) as caught:
        _resolve_site(config_root, _parse("peek", "redis"), ENV)
    message = str(caught.value)
    assert "mx/gumi, mx/sevt" in message
    assert "--gbm mx --fct gumi" in message, "따라 칠 수 있는 예가 있어야 한다"


def test_없는_사이트는_등록된_것을_알려준다(config_root):
    with pytest.raises(SystemExit) as caught:
        _resolve_site(config_root, _parse("peek", "redis", "--fct", "없는법인"), ENV)
    assert "registry에 없는 사이트" in str(caught.value)
    assert "mx/gumi" in str(caught.value)


def test_고른_사이트의_설정이_실제로_다르다(config_root):
    gumi, _ = _resolve_site(config_root, _parse("peek", "redis", "--fct", "gumi"), ENV)
    sevt, _ = _resolve_site(config_root, _parse("peek", "redis", "--fct", "sevt"), ENV)
    assert gumi.infra.redis.url != sevt.infra.redis.url
    assert gumi.infra.redis.db == sevt.infra.redis.db == 0   # 공통 층은 같다


# ── 구조 불변식: argparse 내부 동작에 기대지 않는다 ─────────────────────

def _subparser_actions():
    from src.__main__ import build_parser
    parser = build_parser()
    choices = {}
    for action in parser._actions:                              # noqa: SLF001
        if hasattr(action, "choices") and isinstance(action.choices, dict):
            choices.update(action.choices)
    return choices


def test_하위_명령의_사이트_옵션은_전역과_dest를_공유하지_않는다():
    """dest를 공유하면 값을 합치는 일이 **argparse 내부 동작**에 달린다.

    하위 파서의 결과를 부모 namespace에 합치는 방식이 파이썬 버전 사이에서
    바뀌었고, 그래서 같은 dest를 쓰면 어떤 버전에서는 하위 파서의 "안 줬음"이
    전역에서 받은 값을 덮어쓴다. 실제로 Linux는 통과하고 Windows(다른 파이썬)에서
    다섯 개가 깨졌다.

    "나중에 누가 단순화하려고 dest를 합치는 것"을 막기 위해 구조로 못 박는다.
    """
    offenders = []
    for name, sub in _subparser_actions().items():
        for action in sub._actions:                              # noqa: SLF001
            if action.dest in ("gbm", "fct"):
                offenders.append(f"{name}.{action.dest}")
    assert not offenders, (
        f"하위 명령이 전역과 같은 dest를 쓴다 — {offenders}. "
        "gbm_sub/fct_sub로 분리하고 _merge_site_options가 합쳐야 한다")


def test_사이트_옵션은_앞뒤_양쪽에서_받아들여진다():
    """파싱만 본다 — config가 없어도 도는 테스트라 원인 구분에 쓸 수 있다."""
    assert _parse("--fct", "gumi", "doctor").fct == "gumi"
    assert _parse("doctor", "--fct", "gumi").fct == "gumi"
    assert _parse("--gbm", "mx", "peek", "redis", "--key", "k").gbm == "mx"
    assert _parse("peek", "redis", "--key", "k", "--gbm", "mx").gbm == "mx"


# ── 명령이 실제로 돌아가는가 (import 누락류를 잡는다) ────────────────────

@pytest.fixture
def echo_config(tmp_path):
    """네트워크를 안 타는 llm 설정 — 명령 배선만 본다."""
    root = tmp_path / "config"
    (root / "gbm").mkdir(parents=True)
    (root / "fct" / "gumi").mkdir(parents=True)
    (root / "app.json").write_text(json.dumps({
        "timezone": "Asia/Seoul",
        "llm": {"adapter": "echo", "model": "dev"}}), encoding="utf-8")
    (root / "registry.json").write_text(
        json.dumps({"sites": [{"gbm": "mx", "fct": "gumi"}]}), encoding="utf-8")
    (root / "fct" / "gumi" / "mx.json").write_text(
        json.dumps({"infra": {"redis": {"url": "redis://h:6379"}}}), encoding="utf-8")
    (root / "scenarios").mkdir()
    (root / "scenarios" / "daily-alarm.json").write_text(json.dumps({
        "kind": "alarm_daily", "title": "일일 알람 리포트",
        "source": {"collection": "alarm", "date_field": "occ_date"},
        "scope": {"gbms": ["mx"], "sites": ["mx/gumi"]}}), encoding="utf-8")
    return root


@pytest.mark.parametrize("argv", [
    ["boot"], ["sites"], ["config", "show"], ["config", "show", "--no-provenance"],
    ["llm", "describe"], ["llm", "ask", "안녕"], ["llm", "check"],
    ["mail", "describe"], ["mail", "send", "--subject", "테스트", "--dry-run"],
    ["mail", "send", "--subject", "테스트"],
    ["peek", "rest", "--list"],
    ["report", "scenarios"], ["report", "window"],
    ["report", "window", "--today", "2026-09-07"],
    ["report", "aggregate", "--today", "2026-09-07"],
])
def test_명령이_끝까지_돌아간다(echo_config, argv, capsys):
    """`llm describe`가 import 누락으로 죽은 적이 있다 — 스키마 테스트로는 안 잡힌다.

    `peek rest --list`는 rest 설정이 없으므로 SystemExit이 정상이다. 그 경우에도
    **NameError나 AttributeError가 아니어야** 한다.
    """
    from src.__main__ import main

    try:
        code = main(["--config-root", str(echo_config), "--env-file", "/dev/null", *argv])
        assert code in (0, 1)
    except SystemExit as exit_:
        assert isinstance(exit_.code, (int, str)), f"예상 밖 종료 — {exit_.code!r}"
    out = capsys.readouterr().out
    assert out or argv[0] in ("peek",)


def test_llm_check가_echo로도_결과를_낸다(echo_config, capsys):
    from src.__main__ import main

    main(["--config-root", str(echo_config), "--env-file", "/dev/null", "llm", "check"])
    out = capsys.readouterr().out
    assert "붙는가" in out and "JSON" in out


def test_report_render가_파일을_쓴다(echo_config, tmp_path, capsys):
    """`--out`이 없는 디렉터리를 가리켜도 만들어져야 한다 — 첫 실행이 그 상황이다."""
    from src.__main__ import main

    out = tmp_path / "없던" / "디렉터리" / "report.html"
    code = main(["--config-root", str(echo_config), "--env-file", "/dev/null",
                 "report", "render", "--today", "2026-09-07", "--out", str(out)])
    assert code in (0, 1)
    assert out.exists(), "파일이 안 만들어졌다"
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>") and html.rstrip().endswith("</html>")
    assert "일일 알람 리포트" in html
    assert "블록" in capsys.readouterr().out


def _set_scenario(config_root, **fields):
    """시나리오 JSON에 항목을 얹는다. 픽스처가 만든 것을 그대로 쓰되 일부만 바꾼다."""
    path = config_root / "scenarios" / "daily-alarm.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body.update(fields)
    path.write_text(json.dumps(body), encoding="utf-8")


@pytest.fixture
def seeded_config(echo_config, tmp_path):
    """대상에 **안 붙고** 끝까지 도는 설정.

    `echo_config`만으로는 mongodb 설정이 없어 법인이 `skipped`가 되고, 그러면
    종료 코드가 항상 1이라 "실패를 종료 코드로 말한다"를 시험할 수 없다. 여기서는
    mongodb 설정을 주고 **가짜 데이터**(seeds)로 읽게 한다 — factory가 유일한
    전환점이므로 실접속은 일어나지 않는다.
    """
    (echo_config / "fct" / "gumi" / "mx.json").write_text(json.dumps({
        "infra": {"redis": {"url": "redis://h:6379"},
                  "mongodb": {"url": "mongodb://h:27017", "database": "twin"}}}),
        encoding="utf-8")
    seeds = tmp_path / "seeds.json"
    seeds.write_text(json.dumps({"mx/gumi": {"mongo": {"alarm": [
        {"occ_date": f"2026-09-0{day} 09:00:00", "gbm": "mx", "plant": "gumi",
         "part_code": "PN100", "line_code": "P222", "line_name": "조립2라인",
         "scen_id": "S01", "scen_name": "재고 불일치", "status": 0}
        for day in (1, 2, 3, 4)]}}}), encoding="utf-8")
    return echo_config, str(seeds)


def test_report_run이_파일을_쓰고_메일은_꺼져_있어서_건너뛴다(echo_config, tmp_path,
                                                         capsys):
    """스케줄에 걸리는 명령이라 **혼자 끝까지 가야 한다.**

    `echo_config`에는 mail 설정이 없으므로 `mail.enabled=false`다 — 그때도 파일은
    나와야 한다. 메일이 꺼졌다고 리포트가 안 만들어지면 사람이 손으로 확인할
    방법이 없다.
    """
    from src.__main__ import main

    out_dir = tmp_path / "없던" / "디렉터리"
    code = main(["--config-root", str(echo_config), "--env-file", "/dev/null",
                 "report", "run", "--today", "2026-09-07", "--out-dir", str(out_dir)])
    captured = capsys.readouterr()
    assert code in (0, 1), captured.err

    written = list(out_dir.glob("*.html"))
    assert written, f"파일이 안 만들어졌다\n{captured.out}\n{captured.err}"
    # 기준일(어제)이 이름에 들어간다 — 매일 덮어쓰면 어제 것을 다시 못 본다.
    assert written[0].name == "daily-alarm-2026-09-04.html", written[0].name
    assert "일일 알람 리포트" in written[0].read_text(encoding="utf-8")
    assert "건너뜀" in captured.out, captured.out


def test_report_run의_제목에_기준일이_들어간다(echo_config, tmp_path, capsys):
    """받는 쪽 메일함에서 어제 것과 구분돼야 하고, 날짜로 검색이 돼야 한다."""
    from src.__main__ import main

    main(["--config-root", str(echo_config), "--env-file", "/dev/null",
          "report", "run", "--today", "2026-09-07",
          "--out-dir", str(tmp_path / "out")])
    out = capsys.readouterr().out

    assert "2026-09-04(금)" in out, out
    assert "2026-09-07" not in out.split("제목:")[1].splitlines()[0], \
        "돌린 날이 제목에 들어갔다"


def test_report_run_dry_run은_나갈_요청을_보여준다(seeded_config, tmp_path, capsys):
    """보내기 **전에** 수신자를 눈으로 확인할 수 있어야 한다."""
    from src.__main__ import main

    echo_config, seeds = seeded_config
    app = json.loads((echo_config / "app.json").read_text(encoding="utf-8"))
    app["mail"] = {"enabled": True, "recipients": ["ops@example.com"],
                   "api_base": "https://agent.example.net/api",
                   "agent_id": "a1", "api_key": "hunter2-secret"}
    (echo_config / "app.json").write_text(json.dumps(app), encoding="utf-8")

    out_dir = tmp_path / "out"
    code = main(["--config-root", str(echo_config), "--env-file", "/dev/null",
                 "report", "run", "--today", "2026-09-07", "--stub-seeds", seeds,
                 "--out-dir", str(out_dir), "--dry-run"])
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert "보내지 않았다(dry-run)" in captured.out
    assert "ops@example.com" in captured.out
    assert "hunter2-secret" not in captured.out, "미리보기에 키가 찍혔다"
    # 나갈 **요청**을 보여준다 — 어느 Agent로 가는지가 여기서 드러난다.
    assert "agent.example.net/api/a1" in captured.out
    # 본문(HTML 수만 자)까지 쏟으면 정작 봐야 할 수신자가 화면 밖으로 밀린다.
    assert len(captured.out) < 4000, f"미리보기가 본문을 통째로 찍었다({len(captured.out)}자)"
    # dry-run에서도 파일은 쓴다 — 보낼 본문을 열어 봐야 하기 때문이다.
    assert list(out_dir.glob("*.html"))


def test_report_run_no_mail은_메일을_조립하지_않는다(seeded_config, tmp_path, capsys):
    """mail 설정이 깨져 있어도 파일은 나와야 한다 — `--no-mail`이 그 경로다."""
    from src.__main__ import main

    echo_config, seeds = seeded_config
    out_dir = tmp_path / "out"
    code = main(["--config-root", str(echo_config), "--env-file", "/dev/null",
                 "report", "run", "--today", "2026-09-07", "--stub-seeds", seeds,
                 "--out-dir", str(out_dir), "--no-mail"])
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert list(out_dir.glob("*.html"))
    # 메일에 대한 어떤 결말도 찍히지 않는다 — 조립조차 안 했기 때문이다.
    # (경로 문자열에 "메일"이 들어갈 수 있으므로 결말 문구로 본다)
    for phrase in ("메일 발송", "메일 건너뜀", "메일 실패", "dry-run"):
        assert phrase not in captured.out, f"{phrase!r}가 찍혔다\n{captured.out}"


def test_report_run이_실패를_종료_코드로_말한다(seeded_config, tmp_path, capsys):
    """스케줄러가 조용한 실패를 알아챌 유일한 신호다.

    파일을 쓸 수 없는 자리(디렉터리 자리에 파일이 있다)를 주면 1이어야 한다.
    0을 돌려주면 cron이 "잘 돌았다"고 보고, 리포트가 몇 주 안 나가도 모른다.
    """
    from src.__main__ import main

    echo_config, seeds = seeded_config
    blocked = tmp_path / "out"
    blocked.write_text("이 자리는 파일이다", encoding="utf-8")
    code = main(["--config-root", str(echo_config), "--env-file", "/dev/null",
                 "report", "run", "--today", "2026-09-07", "--stub-seeds", seeds,
                 "--out-dir", str(blocked), "--no-mail"])
    captured = capsys.readouterr()

    assert code == 1, f"파일을 못 썼는데 0을 돌려줬다\n{captured.out}"
    assert "파일을 쓰지 못했다" in captured.out


def test_schedule_list가_다음_발사_시각을_찍는다(seeded_config, capsys):
    """cron 식은 사람이 자주 틀린다. **돌리기 전에** 언제 도는지 볼 수 있어야 한다."""
    from src.__main__ import main

    echo_config, _ = seeded_config
    _set_scenario(echo_config, schedule={"enabled": True, "kind": "cron",
                                         "cron": "0 8 * * 1-5"})

    code = main(["--config-root", str(echo_config), "--env-file", "/dev/null",
                 "schedule", "--list", "--list-count", "3"])
    out = capsys.readouterr().out

    assert code == 0, capsys.readouterr().err
    assert "cron 0 8 * * 1-5" in out
    assert out.count("08:00") == 3, out
    for weekend in ("Sat", "Sun"):
        assert weekend not in out, f"평일만인데 주말이 찍혔다\n{out}"


def test_schedule이_시나리오의_설정을_실제로_쓴다(seeded_config, tmp_path, capsys):
    """config에 적은 주기가 배선까지 닿는지 본다. 끊겨 있어도 프로세스는 멀쩡히
    떠 있고, 리포트만 안 나온다 — 제일 알아채기 어려운 고장이다."""
    from src.__main__ import main

    echo_config, seeds = seeded_config
    _set_scenario(echo_config, schedule={"enabled": True, "kind": "interval",
                                         "interval_seconds": 1, "run_on_start": True})

    code = main(["--config-root", str(echo_config), "--env-file", "/dev/null",
                 "schedule", "--stub-seeds", seeds, "--out-dir", str(tmp_path / "out"),
                 "--no-mail", "--max-runs", "1"])
    out = capsys.readouterr().out

    assert code == 0, out
    assert "1초마다" in out, out
    assert list((tmp_path / "out").glob("*.html")), f"리포트가 안 나왔다\n{out}"
    assert "발사 1회, 실패 0회" in out


def test_schedule이_꺼져_있으면_말하고_끝낸다(seeded_config, capsys):
    """조용히 0을 돌려주면 "떠 있는데 아무것도 안 하는" 프로세스가 된다."""
    from src.__main__ import main

    echo_config, _ = seeded_config
    _set_scenario(echo_config, schedule={"enabled": False})

    code = main(["--config-root", str(echo_config), "--env-file", "/dev/null",
                 "schedule"])
    captured = capsys.readouterr()

    assert code == 1, capsys.readouterr().err
    assert "돌릴 것이 없다" in captured.err


def test_schedule은_config_시간대로_돈다(seeded_config, capsys):
    """`0 8 * * *`이 **어느 8시**인가. 기계의 TZ를 따르면 UTC 파드에서 오후 5시다."""
    from src.__main__ import main

    echo_config, _ = seeded_config
    app = json.loads((echo_config / "app.json").read_text(encoding="utf-8"))
    app["timezone"] = "Asia/Seoul"
    (echo_config / "app.json").write_text(json.dumps(app), encoding="utf-8")
    _set_scenario(echo_config, schedule={"enabled": True, "cron": "0 8 * * *"})

    main(["--config-root", str(echo_config), "--env-file", "/dev/null",
          "schedule", "--list", "--list-count", "1"])
    out = capsys.readouterr().out

    assert "KST" in out, f"시계가 config 시간대가 아니다\n{out}"
    assert "(Asia/Seoul)" in out


def test_app의_output_dir이_실제로_쓰인다(seeded_config, tmp_path, capsys):
    """`--out-dir` 없이 돌리면 **config가 정한 곳**에 쓴다.

    코드에 "output"을 박아 두면 `app.json`의 `output_dir`이 선언만 되고 아무도 안
    읽는 칸이 된다 — 사람이 적어도 아무 일이 안 일어나는데 그 실패는 조용하다.
    `timezone`이 실제로 그랬다.
    """
    from src.__main__ import main

    echo_config, seeds = seeded_config
    declared = tmp_path / "선언한곳"
    app = json.loads((echo_config / "app.json").read_text(encoding="utf-8"))
    app["output_dir"] = str(declared)
    (echo_config / "app.json").write_text(json.dumps(app), encoding="utf-8")

    code = main(["--config-root", str(echo_config), "--env-file", "/dev/null",
                 "report", "run", "--today", "2026-09-07", "--stub-seeds", seeds,
                 "--no-mail"])
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert (declared / "daily-alarm-2026-09-04.html").exists(), captured.out


def test_report_prompt이_실제로_나갈_프롬프트를_찍는다(echo_config, capsys):
    """검토 도구가 실제와 다른 글을 보여 주면 검토가 무의미하다 — `{max_chars}`가
    그대로 찍히면 치환 누락을 이 도구가 숨긴다."""
    from src.__main__ import main

    (echo_config / "prompts").mkdir()
    (echo_config / "prompts" / "p.txt").write_text(
        "{max_chars}자 이내\n{facts}\n{gbm}", encoding="utf-8")
    body = json.loads(
        (echo_config / "scenarios" / "daily-alarm.json").read_text(encoding="utf-8"))
    body["comment"] = {"enabled": True, "prompt_file": "prompts/p.txt",
                       "max_chars": 333}
    (echo_config / "scenarios" / "daily-alarm.json").write_text(
        json.dumps(body), encoding="utf-8")

    code = main(["--config-root", str(echo_config), "--env-file", "/dev/null",
                 "report", "prompt", "--today", "2026-09-07"])
    captured = capsys.readouterr()
    assert code in (0, 1), captured.err

    # 실패했을 때 **무엇이 찍혔는지** 보여 준다. "치환되지 않았다"만 말하면 다음에
    # 할 수 있는 일이 추측뿐이다 — 사내에서 이 테스트가 깨졌을 때 실제로 그랬다.
    def report(reason: str) -> str:
        return (f"{reason}\n"
                f"--- stdout ({len(captured.out)}자) ---\n{captured.out}\n"
                f"--- stderr ---\n{captured.err}")

    leftovers = [line for line in captured.out.splitlines() if "{" in line and "}" in line]
    assert not leftovers, report(f"치환되지 않은 자리가 찍혔다 — {leftovers}")
    assert "333자 이내" in captured.out, report("상한이 프롬프트에 안 들어갔다")
    assert "허용 숫자" in captured.out, report("허용 목록을 함께 보여야 한다")
