"""HTTP 표면 — 세 프로세스(api / worker / patrol) 중 `api`의 진입점 (스펙 §3.1).

**`api`는 실행자가 아니라 클라이언트다.** 케이스를 쓰고 이벤트를 읽는다. 조사는
워커만 하고, `api`는 대상 시스템(Redis/Mongo/Kafka/REST/코드 저장소)에 붙지 않는다 —
어댑터를 조립하지 않는다. `tests/api/test_boundary.py`가 import 그래프로 그것을
지킨다: 산문 규율은 읽지 않으면 무력하다.

`presentation/` 아래가 아닌 이유: presentation은 케이스 종결 후 산출물(보고서·메일)
이고 프로세스 안에서 불린다. 이것은 **별도 프로세스**이고 경계 테스트가 패키지
단위로 걸린다.
"""
