"""사이트×서비스 → 배포 커밋 매핑 — 스펙 §3.3.

로컬 체크아웃의 HEAD는 배포 진실이 아니다. 이 파일이 진실이고,
운영 절차로 수동 유지된다. 없으면 None — 그 사이트의 코드 증거에는
"배포 버전 미검증" 플래그가 강제된다(계획 3에서 소비).
"""
from pathlib import Path

import yaml

from src.config.schema_app import StrictModel


class DeployedVersion(StrictModel):
    repo: str
    commit: str


class Deployment(StrictModel):
    services: dict[str, DeployedVersion] = {}


def load_deployment(knowledge_root: Path, gbm: str, fct: str) -> Deployment | None:
    path = knowledge_root / "deployment" / gbm / f"{fct}.yaml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Deployment.model_validate(data)
