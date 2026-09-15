"""LLM 조립의 **유일한 지점**.

호출부가 각자 어댑터를 고르면 한 경로만 다른 LLM을 쓰는 사고가 난다 —
"CLI로는 되는데 순찰에서는 안 된다"는 증상이고, 원인은 조립이 두 벌이라는 사실이다.
"""
from src.config.schema_llm import LlmConfig
from src.domain.base import Clock
from src.domain.llm import LlmPort
from src.infrastructure.tls import warn_if_unverified


def build_llm(cfg: LlmConfig, *, clock: Clock, warn=None) -> LlmPort:
    if cfg.adapter == "echo":
        from src.infrastructure.llm_fakes import EchoAdapter
        return EchoAdapter(clock=clock, model=cfg.model)

    warn_if_unverified(cfg.tls, warn=warn)      # 검증이 꺼졌으면 시끄럽게

    if cfg.adapter == "http":
        from src.infrastructure.llm_http import HttpChatAdapter
        return HttpChatAdapter(cfg, clock=clock)

    from src.infrastructure.llm_chat_model import ChatModelAdapter   # 지연 import
    return ChatModelAdapter(cfg, clock=clock)
