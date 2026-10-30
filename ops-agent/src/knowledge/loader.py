"""`knowledge/`를 읽는다. **ConfigError와 같은 계약** — 문제는 시끄럽게 죽는다.

기동 경계에서만 불리므로 raise해도 된다(`boot.py`가 모아서 보고한다). 조사 중에
불리는 경로가 아니다 — 조사 중에는 코드도 지식도 움직이지 않는다([decisions ⑥]).
"""
import json
from pathlib import Path

from pydantic import ValidationError

from src.config.loader import ConfigError
from src.knowledge.schema import Deployment, Topology


def _read(path: Path, model, *, required: bool):
    if not path.exists():
        if required:
            raise ConfigError(f"{path}가 없다")
        return model()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError(f"{path}를 JSON으로 읽을 수 없다 — {exc}") from exc
    # `_`로 시작하는 키는 주석이다(JSON에 주석이 없어서 둔 관례 — examples와 같다).
    raw = {k: v for k, v in raw.items() if not k.startswith("_")}
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        lines = [f"  {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise ConfigError(f"{path} 스키마 오류:\n" + "\n".join(lines)) from exc


def load_topology(knowledge_root: Path, gbm: str) -> Topology:
    """GBM 하나의 서비스 지도. **없으면 죽는다** — 코드를 못 읽는다는 뜻이다."""
    return _read(Path(knowledge_root) / "topology" / f"{gbm}.json", Topology, required=True)


def load_deployment(knowledge_root: Path, gbm: str) -> Deployment:
    """GBM 하나의 배포 상태. **없어도 된다** — 전부 `main` 최신 가정으로 떨어진다.

    없는 것을 오류로 삼지 않는 이유: 선언이 없는 상태가 정상적인 출발점이고,
    그때도 조사는 돌아야 한다. 다만 그렇게 읽은 코드에는 `how="assumed"`가 붙어
    **가정이었다는 사실이 증거까지 따라간다.**
    """
    return _read(Path(knowledge_root) / "deployment" / f"{gbm}.json",
                 Deployment, required=False)
