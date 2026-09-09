# ops-agent

운영 모니터링 에이전트를 **처음부터** 만드는 프로젝트. 대상 시스템(디지털 트윈)을
읽기만 하면서 이상을 탐지하고, 원인을 조사하고, 증거를 인용한 판정을 낸다.

단계별 문서가 [STEPS/](STEPS/)에 있다. [0단계 — 무엇을 만드는가](STEPS/step-00-overview.md)부터.

## 실행

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -v
```

Windows(사내):

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -v
```

Windows에서만 터지는 지점들은 [STEPS/windows.md](STEPS/windows.md)에 모아 뒀다 —
인코딩, tz 데이터베이스, 사내 CA와 TLS. **읽고 시작하는 편이 빠르다.**

## 데이터를 하나 꺼내 보기

```bash
python -m src boot                                    # 설정이 온전한가
python -m src config show                             # 병합 결과 + 값의 출처
python -m src doctor                                  # 실제로 붙는가
python -m src peek redis --key oee:L3
python -m src peek mongo --collection oee --filter '{"line":"L3"}' --limit 5
python -m src peek kafka --lag
python -m src peek rest --entry oee_summary --params '{"line":"L3"}'
```

자세한 것은 [3b단계 문서](STEPS/step-03b-adapters.md).

## 진행 상황

| 단계 | 상태 |
|---|---|
| 1. 뼈대와 규율 | ✅ |
| 2. 도메인 모델과 포트 | ✅ |
| 3a. config와 기동 검증 | ✅ |
| 3b. 실제 어댑터와 접속 | ✅ |
| 4. 순찰 프로브 | ⬜ |
| 5. rule 판정과 finding | ⬜ |
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
