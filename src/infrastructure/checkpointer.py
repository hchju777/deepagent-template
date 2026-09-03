"""체크포인터·영속 저장소 팩토리 — StoreConfig.backend로 memory/mongo를 고른다(계획 4b).

`langgraph.checkpoint.mongodb`는 지연 import한다: memory 백엔드(로컬·테스트에서
가장 흔한 경로)가 langchain-mongodb 등 mongo 전용 무거운 의존을 강제로 짊어지지
않게 하기 위해서다. pymongo 자체는 이미 mongo_reader.py 등에서 무조건 임포트되는
필수 의존이라 지연시키지 않는다.

설치된 langgraph-checkpoint-mongodb(0.4.0)에는 브리프가 가리킨
`langgraph.checkpoint.mongodb.aio.AsyncMongoDBSaver`가 없다 — 이 버전은 별도의
async 구현체를 없애고, 동기 `MongoClient`를 받는 `MongoDBSaver` 하나가
`aget_tuple`/`aput`/`adelete_thread` 등 async 메서드를 `run_in_executor`로
제공한다(실제 설치본을 introspect해서 확인). 그래서 mongo 경로는
`MongoDBSaver(MongoClient(cfg.mongo_url), db_name=cfg.mongo_db)`로 구성한다 —
브리프의 AsyncMongoDBSaver/AsyncMongoClient 조합은 이 설치본에 존재하지 않는다
(task-4-report.md에 근거를 남겼다).
"""
from langgraph.checkpoint.memory import InMemorySaver
from pymongo import MongoClient

from src.config.schema_app import StoreConfig
from src.domain.cases import InMemoryCaseRepository
from src.domain.store import InMemoryCaseStore
from src.infrastructure.mongo_store import (MongoCaseRepository, MongoCaseStore, MongoLedger,
                                            ensure_indexes)
from src.patrol.ledger import InMemoryLedger


def build_checkpointer(cfg: StoreConfig):
    """cfg.backend에 따라 LangGraph 체크포인터를 만든다."""
    if cfg.backend == "memory":
        return InMemorySaver()
    from langgraph.checkpoint.mongodb import MongoDBSaver     # 지연 import(위 docstring 참고)
    client = MongoClient(cfg.mongo_url)
    return MongoDBSaver(client, db_name=cfg.mongo_db)


def build_persistence(cfg: StoreConfig):
    """cfg.backend에 따라 (store, repo, ledger) 3종 세트를 만든다."""
    if cfg.backend == "memory":
        return InMemoryCaseStore(), InMemoryCaseRepository(), InMemoryLedger()
    db = MongoClient(cfg.mongo_url)[cfg.mongo_db]
    ensure_indexes(db)
    return MongoCaseStore(db), MongoCaseRepository(db), MongoLedger(db)
