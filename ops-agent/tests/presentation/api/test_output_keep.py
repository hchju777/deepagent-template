"""4단계 — 출력 보관을 API가 보이는 자리: GET /output의 정책·지울 예정, POST /output/cleanup(dry_run 기본)."""
import json

from src.presentation.api.assembly import OPS

from tests.presentation.api.conftest import SCENARIO


def with_keep(cli_root, keep_days):
    body = {**SCENARIO, **({"keep_days": keep_days} if keep_days else {})}
    (cli_root / "config" / "scenarios" / "daily-alarm.json").write_text(json.dumps(body), encoding="utf-8")


def fill(out):
    (out / "sent").mkdir(parents=True, exist_ok=True)
    for day in ("2026-10-28", "2026-10-29", "2026-10-30"):
        (out / f"daily-alarm-{day}.html").write_text("리포트", encoding="utf-8")
        (out / "sent" / f"daily-alarm-{day}.json").write_text("{}", encoding="utf-8")
    (out / "cases.json").write_text("{}", encoding="utf-8")


async def test_GET_output에_정책과_지울_예정이_보인다(client, cli_root):
    with_keep(cli_root, 2)
    fill(cli_root / "output")
    [policy] = (await client.get(f"{OPS}/output")).json()["policy"]
    assert policy == {"scenario": "daily-alarm", "keep_days": 2, "keep": ["2026-10-30", "2026-10-29"],
                      "delete": ["daily-alarm-2026-10-28.html", "sent/daily-alarm-2026-10-28.json"]}


async def test_cleanup은_기본이_dry_run이라_안_지운다(client, cli_root):
    with_keep(cli_root, 2)
    fill(cli_root / "output")
    body = (await client.post(f"{OPS}/output/cleanup")).json()
    assert body["dry_run"] is True
    assert body["results"][0]["delete"] == ["daily-alarm-2026-10-28.html", "sent/daily-alarm-2026-10-28.json"]
    assert "deleted" not in body["results"][0]
    assert (cli_root / "output" / "daily-alarm-2026-10-28.html").exists()


async def test_cleanup_dry_run_false면_정책대로_지우고_나머지는_둔다(client, cli_root):
    with_keep(cli_root, 2)
    out = cli_root / "output"
    fill(out)
    body = (await client.post(f"{OPS}/output/cleanup", params={"dry_run": False})).json()
    [row] = body["results"]
    assert row["deleted"] == ["daily-alarm-2026-10-28.html", "sent/daily-alarm-2026-10-28.json"]
    assert row["problems"] == []
    assert not (out / "daily-alarm-2026-10-28.html").exists()
    assert (out / "daily-alarm-2026-10-29.html").exists() and (out / "cases.json").exists()


async def test_keep_days가_없으면_정책은_비어_있고_cleanup도_안_지운다(client, cli_root):
    with_keep(cli_root, None)
    fill(cli_root / "output")
    [policy] = (await client.get(f"{OPS}/output")).json()["policy"]
    assert (policy["keep_days"], policy["delete"]) == (None, [])
    body = (await client.post(f"{OPS}/output/cleanup", params={"dry_run": False})).json()
    assert "deleted" not in body["results"][0]
    assert len(list((cli_root / "output").glob("daily-alarm-*.html"))) == 3


async def test_모르는_시나리오는_400(client):
    response = await client.post(f"{OPS}/output/cleanup", params={"scenario": "nope"})
    assert response.status_code == 400 and "nope" in response.json()["detail"]


def test_cleanup의_dry_run은_쿼리_체크박스이고_기본_true(app):
    params = {p["name"]: p for p in app.openapi()["paths"][f"{OPS}/output/cleanup"]["post"]["parameters"]}
    assert params["dry_run"]["in"] == "query"
    assert params["dry_run"]["schema"]["type"] == "boolean" and params["dry_run"]["schema"]["default"] is True
