"""토폴로지 명세 — 스펙 §3.1. 파이프라인 이분탐색의 지도.

derivations는 output locator를 키로 하는 map이다. 리스트가 아닌 이유:
리스트 deep-merge는 통째 대체 아니면 append라 사이트별 편집에 틀린 의미가 된다.
"""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import model_validator

from src.config.merge import deep_merge
from src.config.schema_app import StrictModel

_KIND_FIELD = {"kafka": "topic", "redis": "key", "mongo": "collection", "rest": "endpoint"}


class DataRef(StrictModel):
    kind: Literal["kafka", "redis", "mongo", "rest"]
    topic: str | None = None
    key: str | None = None
    collection: str | None = None
    endpoint: str | None = None

    @model_validator(mode="after")
    def _field_matches_kind(self):
        want = _KIND_FIELD[self.kind]
        set_fields = [f for f in _KIND_FIELD.values() if getattr(self, f) is not None]
        if set_fields != [want]:
            raise ValueError(f"kind={self.kind}에는 {want}만 선언한다 (선언됨: {set_fields})")
        return self

    @property
    def locator(self) -> str:
        return f"{self.kind}:{getattr(self, _KIND_FIELD[self.kind])}"


class ServiceCode(StrictModel):
    repo: str
    path: str


class Service(StrictModel):
    code: ServiceCode | None = None
    reads: list[DataRef] = []
    writes: list[DataRef] = []


class Derivation(StrictModel):
    inputs: list[DataRef]
    via: str
    key: str = "fan-in"     # "fan-in" 또는 자리표시자 이름(per-key)


class Topology(StrictModel):
    services: dict[str, Service] = {}
    derivations: dict[str, Derivation] = {}

    def locators(self) -> set[str]:
        out = set(self.derivations)
        for svc in self.services.values():
            out |= {ref.locator for ref in svc.reads + svc.writes}
        return out


def load_topology(knowledge_root: Path, gbm: str, fct: str) -> Topology:
    base_path = knowledge_root / "topology" / "common.yaml"
    site_path = knowledge_root / "topology" / gbm / f"{fct}.yaml"
    base = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {} \
        if base_path.exists() else {}
    site = yaml.safe_load(site_path.read_text(encoding="utf-8")) or {} \
        if site_path.exists() else {}
    provenance: dict[str, str] = {}
    merged: dict = {}
    merged = deep_merge(merged, base, source="common", provenance=provenance)
    merged = deep_merge(merged, site, source=f"{gbm}/{fct}", provenance=provenance)
    return Topology.model_validate(merged)


def topology_problems(topo: Topology) -> list[str]:
    problems = []
    for output, deriv in topo.derivations.items():
        if deriv.via not in topo.services:
            problems.append(f"derivation {output!r}: via {deriv.via!r}가 services에 없다")
    return problems
