"""엔진 의존성 묶음 — 노드 팩토리(make_nodes)가 클로저로 받는다."""
from dataclasses import dataclass
from typing import Any

from src.config.schema_app import EngineConfig
from src.domain.store import CaseStorePort
from src.infrastructure.factory import AdapterSet
from src.knowledge.topology import Topology


@dataclass
class EngineDeps:
    lead_llm: Any                 # async ainvoke(messages) -> .content
    subagent_llm: Any             # BaseChatModel (create_agent용)
    adapters: AdapterSet
    store: CaseStorePort
    topology: Topology
    engine_cfg: EngineConfig
    rules_text: str = ""
    history_text: str = ""
    docs_text: str = ""
