import importlib.util

from langgraph.checkpoint.memory import InMemorySaver

from src.config.schema_app import StoreConfig
from src.domain.cases import InMemoryCaseRepository
from src.domain.store import InMemoryCaseStore
from src.infrastructure.checkpointer import build_checkpointer, build_persistence
from src.patrol.ledger import InMemoryLedger


def test_memory_백엔드와_mongo_모듈_존재():
    assert isinstance(build_checkpointer(StoreConfig(backend="memory")), InMemorySaver)
    assert importlib.util.find_spec("langgraph.checkpoint.mongodb") is not None


def test_build_persistence_memory_백엔드():
    store, repo, ledger, events = build_persistence(StoreConfig(backend="memory"))
    assert isinstance(store, InMemoryCaseStore)
    assert isinstance(repo, InMemoryCaseRepository)
    assert isinstance(ledger, InMemoryLedger)
