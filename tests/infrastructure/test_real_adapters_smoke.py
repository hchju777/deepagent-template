"""실구현은 실 백엔드 없이 동작 검증이 불가하다(통합 환경 YAGNI — 스펙 리뷰 판정).
여기서는 포트 구현 여부와 읽기 전용 표면만 검사한다."""
import inspect

from src.domain import ports
from src.infrastructure.kafka_inspector import RealKafka
from src.infrastructure.mongo_reader import RealMongo
from src.infrastructure.redis_reader import RealRedis
from src.infrastructure.rest_prober import RealRest


def test_포트_구현과_읽기전용_표면():
    assert issubclass(RealRedis, ports.RedisReaderPort)
    assert issubclass(RealMongo, ports.MongoReaderPort)
    assert issubclass(RealKafka, ports.KafkaInspectorPort)
    assert issubclass(RealRest, ports.RestProberPort)
    # 쓰기 냄새가 나는 공개 메서드가 없어야 한다
    for cls in (RealRedis, RealMongo, RealKafka, RealRest):
        public = [n for n, _ in inspect.getmembers(cls, inspect.isfunction)
                  if not n.startswith("_")]
        assert not [n for n in public if n in
                    {"set", "delete", "insert", "update", "write", "commit", "produce", "post", "put"}]
