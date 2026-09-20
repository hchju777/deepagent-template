"""지식 층 로더 — **선언끼리 어긋나면 기동이 막힌다.**

어긋남의 증상은 런타임에 "코드 증거가 조용히 안 나온다"이다. 조용한 실패라
제일 비싸고, 그래서 기동에서 잡는다.
"""
import json

import pytest

from src.config.loader import ConfigError
from src.knowledge.loader import load_deployment, load_topology
from src.knowledge.schema import Deployment, Pin, Topology

MX = {"services": {"processor": {"repo": "dt-core",
                                 "selects": {"SERVICE_ROLE": "processor"}}}}


def write(root, kind, gbm, body):
    folder = root / kind
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{gbm}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return root


def test_토폴로지를_읽는다(tmp_path):
    topology = load_topology(write(tmp_path, "topology", "mx", MX), "mx")
    assert topology.services["processor"].repo == "dt-core"


def test_토폴로지가_없으면_죽는다(tmp_path):
    """코드를 한 줄도 못 읽는다는 뜻이다 — 조용히 빈 지도로 떨어지면 안 된다."""
    with pytest.raises(ConfigError, match="없다"):
        load_topology(tmp_path, "mx")


def test_배포_선언은_없어도_된다(tmp_path):
    """선언이 없는 상태가 **정상적인 출발점**이다. 그때도 조사는 돌아야 한다."""
    assert load_deployment(tmp_path, "mx").default_ref == "main"


def test_주석_키는_걷힌다(tmp_path):
    """JSON에 주석이 없어서 둔 관례다(`examples/`와 같다)."""
    body = {"_설명": "사람이 읽는 것", **MX}
    assert load_topology(write(tmp_path, "topology", "mx", body), "mx").services


def test_모르는_키는_거부한다(tmp_path):
    """규율 5. `servcies` 오타가 조용히 무시되면 **서비스가 0개인 지도**가 된다."""
    with pytest.raises(ConfigError, match="servcies|Extra"):
        load_topology(write(tmp_path, "topology", "mx", {"servcies": {}}), "mx")


def test_서비스가_없는_토폴로지는_거부한다(tmp_path):
    with pytest.raises(ConfigError):
        load_topology(write(tmp_path, "topology", "mx", {"services": {}}), "mx")


# ── 선언한 것과 가정한 것은 다른 사실이다 ─────────────────────────

def test_선언이_없으면_가정으로_떨어지고_그렇게_적힌다():
    """**가정을 확인처럼 다루면** 뒤처진 사이트에서 떠 있지도 않은 코드를 읽고
    확신에 찬 오답을 낸다. 잘린 표본에 `complete=False`를 붙이는 것과 같은 규율."""
    pin = Deployment().pin_for("processor", fct="gumi")
    assert pin.how == "assumed" and pin.commit == "main"


def test_배포_시각이_있으면_status에_실린다():
    """"증상이 11일부터인데 이게 10일에 배포됐다"는 한 줄이 가설 순위를 바꾼다.
    **읽는 코드와 함께 들어온 칸이다**(decisions ⑧)."""
    pin = Deployment(pins={"processor": Pin(commit="a3f9c2", how="declared",
                                            deployed_at="2026-09-10")}).pin_for("processor")
    assert pin.deployed_at == "2026-09-10"


def test_선언이_있으면_그것을_쓴다():
    deployment = Deployment(pins={"processor": Pin(commit="a3f9c2", how="declared")})
    assert deployment.pin_for("processor", fct="gumi").how == "declared"


def test_사이트_예외가_GBM_선언을_이긴다():
    """한 법인만 롤백한 경우다 — 코드는 GBM별로 같지만 예외는 생긴다."""
    deployment = Deployment(
        pins={"processor": Pin(commit="a3f9c2", how="declared")},
        sites={"sevt": {"processor": Pin(commit="b71e04", how="declared")}})
    assert deployment.pin_for("processor", fct="gumi").commit == "a3f9c2"
    assert deployment.pin_for("processor", fct="sevt").commit == "b71e04"


def test_확인했다면_무엇을_확인했는지_적어야_한다():
    """`how=declared`인데 커밋이 없으면 그 선언은 아무 뜻이 없다."""
    with pytest.raises(ValueError, match="무엇을 확인"):
        Pin(how="declared")


# ── 서비스는 경로가 아니라 선택자다 ────────────────────────────────

def test_한_코드베이스에서_env가_역할을_고른다():
    """한 레포에 서비스가 여럿인 것은 디렉터리가 나뉜 게 아니라 **코드가 하나이고
    `.env`가 역할을 고르는 것**이다(사내 확인). 그래서 경로로 못 좁힌다."""
    topology = Topology.model_validate({"services": {
        "processor": {"repo": "dt-core", "selects": {"SERVICE_ROLE": "processor"}},
        "sink": {"repo": "dt-core", "selects": {"SERVICE_ROLE": "sink"}}}})
    assert {s.repo for s in topology.services.values()} == {"dt-core"}
    assert topology.services["sink"].path == ""        # 경로가 없는 것이 정상이다


def test_레포_밖을_가리키는_경로는_거부한다():
    with pytest.raises(ValueError, match="상대 경로"):
        Topology.model_validate({"services": {
            "x": {"repo": "r", "path": "../../etc/passwd"}}})
