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
인코딩, tz 데이터베이스, 사내 CA와 TLS, 그리고 **같은 3.11인데 argparse 동작이
갈린 사례**. 읽고 시작하는 편이 빠르다.

검증된 파이썬: 3.11.3(사내 Windows), 3.11.15(개발).

## 데이터를 하나 꺼내 보기

```bash
python -m src boot                                    # 설정이 온전한가
python -m src config show                             # 병합 결과 + 값의 출처
python -m src doctor                                  # 실제로 붙는가
python -m src peek redis --key oee:L3
python -m src peek mongo --collection oee --filter '{"line":"L3"}' --limit 5
python -m src peek kafka --lag
python -m src peek rest --entry summary_badge --params '{"line_code":"P222"}'
```

사이트가 여러 개면 `--gbm mx --fct gumi`를 붙인다 — **하위 명령 앞뒤 아무 데나** 된다.

## LLM에 물어보기

```bash
python -m src llm describe            # 무엇에 붙어 있는지 (호출은 안 한다)
python -m src llm check               # 붙는가 · 한국어 · JSON
python -m src llm ask "질문"
```

사내 게이트웨이 접속은 [7단계 문서](STEPS/step-07-llm.md). 실제로 붙는지는
사내에서 `pytest tests/live -m live_llm -v`가 확인한다.

## 메일 보내기

```bash
python -m src mail describe                          # 누구에게 보내게 돼 있는지
python -m src mail send --subject "연결 테스트" --dry-run   # 나갈 요청만 확인
python -m src mail send --subject "연결 테스트"             # 실제 발송
python -m src mail send --subject "mx/gumi" --file output/report.md
```

**수신자는 config가 정한다** — 본문이 바꿀 수 없다. 왜 그게 중요한지는
[8단계 문서](STEPS/step-08-mail.md).

## 운영 리포트

```bash
python -m src report scenarios                      # 시나리오 목록
python -m src report window                         # 집계 대상 날짜와 나갈 Mongo 필터
python -m src report window --today 2026-09-07      # 그날 돌았다면 어떻게 되는가
```

시나리오는 `config/scenarios/<이름>.json` 한 파일이 하나다 — 리포트는 사이트를
가로지르므로 사이트 층 병합을 타지 않는다. 기간 계산이 왜 따로 떨어져 있고
날짜 형식을 왜 기동에서 거부하는지는 [9a단계 문서](STEPS/step-09a-window.md).

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
| 7. 사내 LLM 연결 | ✅ |
| 8. 메일 발송 Agent API | ✅ |
| 9a. 리포트 기간과 시나리오 | ✅ |
| 9b. 집계 | ⬜ |
| 9c. 블록 렌더링(HTML) | ⬜ |
| 9d. 차트 이미지(base64) | ⬜ |
| 9e. LLM 코멘트 | ⬜ |
| 9f. `report run` + 메일 배선 | ⬜ |
| 10. 조사 그래프 | ⬜ |
| 11. 서브에이전트 | ⬜ |
| 12. conclude와 verify | ⬜ |
| 13. 사람 개입 | ⬜ |
| 14. 데몬·워커 | ⬜ |
| 15. 실데이터 E2E·운영 | ⬜ |
