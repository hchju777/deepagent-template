"""엔진 의존성 묶음 — 노드 팩토리(make_nodes)가 클로저로 받는다."""
from dataclasses import dataclass, field
from typing import Any

from src.config.schema_app import EngineConfig
from src.config.schema_site import CheckConfig
from src.domain.store import CaseStorePort
from src.infrastructure.factory import AdapterSet
from src.knowledge.deployment import Deployment
from src.knowledge.topology import Topology


@dataclass
class EngineDeps:
    lead_llm: Any                 # async ainvoke(messages) -> .content
    subagent_llm: Any             # BaseChatModel (create_agent용)
    adapters: AdapterSet
    store: CaseStorePort
    topology: Topology
    engine_cfg: EngineConfig
    # 브리핑 재료는 텍스트가 아니라 구조로 받는다 — 무엇이 케이스에 관련 있는지는
    # 슬라이스를 아는 frame이 정해야 하고, 조립 시점에는 그 답이 없다.
    checks: dict[str, CheckConfig] = field(default_factory=dict)
    deployment: Deployment | None = None
    history_text: str = ""
