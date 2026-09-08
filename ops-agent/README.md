# ops-agent

운영 모니터링 에이전트를 **처음부터** 만드는 프로젝트. 대상 시스템(디지털 트윈)을
읽기만 하면서 이상을 탐지하고, 원인을 조사하고, 증거를 인용한 판정을 낸다.

단계별 문서가 [STEPS/](STEPS/)에 있다. [0단계 — 무엇을 만드는가](STEPS/step-00-overview.md)부터.

## 실행

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -v
```

## 진행 상황

| 단계 | 상태 |
|---|---|
| 1. 뼈대와 규율 | ✅ |
| 2. 도메인 모델과 포트 | ⬜ |
| 3. config와 기동 검증 | ⬜ |
| 4. 어댑터 (실데이터) | ⬜ |
| 5. 프로브와 rule 판정 | ⬜ |
| 6. 케이스 저장소와 게이트 | ⬜ |
| 7. 사내 LLM 연결 | ⬜ |
| 8. 조사 그래프 | ⬜ |
| 9. 서브에이전트 | ⬜ |
| 10. conclude와 verify | ⬜ |
| 11. 사람 개입 | ⬜ |
| 12. 보고서와 이벤트 | ⬜ |
| 13. 데몬·워커·CLI | ⬜ |
| 14. 실데이터 E2E | ⬜ |
| 15. 운영 | ⬜ |
