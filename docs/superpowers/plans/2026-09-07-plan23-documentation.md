# v2 문서화 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sonnet 등급 구현자가 이 리포에서 **구현과 수정을 문제없이** 할 수 있는 문서를
만든다. 기존 문서의 v2 누락을 메우고, 파일별 역할과 데이터 흐름을 담은 상세 분석을 만든다.

**Architecture:** 지금 문서는 여섯이고 `architecture.md`만 최신이다(계획마다 갱신했다).
나머지는 v2 기능이 통째로 빠져 있다 — 실측: `tutorial.md`에 라벨·`question_seq`·
캘리브레이션·`mongo_find`가 0건, `going-live.md`에 시나리오·라벨·캘리브레이션이 0건,
`README.md`에 시나리오·라벨이 0건.

**빠진 문서 종류가 둘 있다**: ①파일별로 "이게 뭐 하는 파일인가"를 답하는 지도 —
89개 파일 13,700줄에서 구현자가 어디를 열어야 하는지 알 길이 `CLAUDE.md`의 14줄짜리
코드 지도뿐이다. ②"X를 추가하려면 무엇을 어떤 순서로 만지나"를 답하는 레시피 — 지금은
규율(하지 마라)만 있고 절차(이렇게 하라)가 없다.

**Tech Stack:** 마크다운. 코드 변경 없음.

## Global Constraints

- **문서가 주장하는 배선은 grep으로 확인한 것만 쓴다.** 이 리포가 두 번 데인 형태다
  (`resume_once`, `patrol run`의 `scenarios`) — 호출부를 확인하기 전엔 사실로 적지 않는다.
- 파일 경로·함수 이름·줄 번호를 인용할 때는 **실제로 존재하는지 확인**한다. 줄 번호는
  금방 낡으므로 **함수 이름을 쓰고 줄 번호는 꼭 필요할 때만** 쓴다.
- 한국어. WHY 위주 — WHAT은 식별자가 이미 말한다.
- 기존 문서의 톤과 구조를 따른다(`architecture.md`가 기준).
- 코드 변경이 없으므로 테스트 수는 안 변한다: `1048 passed`가 유지돼야 한다
  (`tests/test_examples.py`가 README·튜토리얼의 명령을 실제로 대조하므로 그건 깨질 수 있다 —
  깨지면 문서가 틀린 것이다).

## File Structure

| 파일 | 상태 | 책임 |
|---|---|---|
| `docs/file-map.md` | **신규** | 파일별 역할·공개 표면·의존, 그리고 데이터 흐름 트레이스 |
| `docs/for-implementers.md` | **신규** | "X를 추가하려면" 레시피 — 만질 파일과 순서, 빠뜨리면 깨지는 것 |
| `docs/tutorial.md` | 갱신 | 라벨·캘리브레이션·`question_seq`·`mongo_find` 누락 |
| `docs/going-live.md` | 갱신 | 시나리오·라벨·캘리브레이션 누락 |
| `README.md` | 갱신 | v2 기능 목록 |
| `docs/howto.md`·`config-reference.md`·`glossary.md` | 갱신 | 빈 구멍 메우기 |
| `CLAUDE.md` | 갱신 | 새 문서 둘로 가는 길 |

---

### Task 1: `docs/file-map.md` — 데이터 흐름

**Files:** Create: `docs/file-map.md`

먼저 흐름을 적는다. 파일 목록만 있으면 "이 파일이 언제 불리는가"를 알 수 없다.

- [ ] **Step 1: 여섯 흐름을 함수 이름으로 추적해 적는다**

1. **순찰 → 케이스**: 스케줄러 → `run_check` → 프로브 → 해석기 → rule/LLM 판정 →
   `admit_finding` → 케이스 개설/첨부 → 큐
2. **조사**: 워커 `run_once` → lease → `investigate_case` → 그래프(frame→select→
   execute→integrate→ask_human/conclude→verify) → 종결 → 발행
3. **사람 접수**: `open_case` → `intake_turn` 턴 반복 → `intake_done` → requeue
4. **답변 두 경로**: CLI `case resume` / HTTP `POST /answers` → `answer_case` →
   접수면 `intake_turn`, 조사면 `attach_answer` → 워커 `resume_once`
5. **Fleet 집계**: 시나리오 → `scenario_sites` 팬아웃 → `collect_site` → `reduce` →
   `FleetReport` → 렌더 → digest 저장
6. **학습 루프**: 종결 시 `VerdictSnapshot` 박제 → `case label` → `calibration`

각 단계에 **파일:함수**를 단다. 문서가 부르는 함수는 전부 grep으로 확인한다.

- [ ] **Step 2: 확인** — 인용한 함수가 전부 실재하는지 스크립트로 대조한다.

- [ ] **Step 3: 커밋**

---

### Task 2: `docs/file-map.md` — 파일별 항목

**Files:** Modify: `docs/file-map.md`

- [ ] **Step 1: 패키지별로 89개 파일 항목을 쓴다**

각 항목: **경로 · 줄수 · 역할 한 줄 · 공개 표면 · 이 파일을 열어야 할 때**.
`__init__.py`는 묶어서 한 줄로 처리한다.

패키지 순서는 의존 방향을 따른다(안쪽부터): `domain` → `config` → `knowledge` →
`infrastructure` → `patrol` → `application` → `fleet` → `presentation` → `api` → `__main__`.

- [ ] **Step 2: 확인** — 파일 목록이 `find src -name "*.py"`와 정확히 일치하는지 대조.

- [ ] **Step 3: 커밋**

---

### Task 3: `docs/for-implementers.md` — 레시피

**Files:** Create: `docs/for-implementers.md`

- [ ] **Step 1: 레시피를 쓴다**

각 레시피: **만질 파일과 순서 · 먼저 쓸 테스트 · 빠뜨리면 조용히 깨지는 것**.

1. 새 프로브 추가
2. 새 rule 추가
3. 새 등재 항목(REST)·해석기 추가
4. 새 config 키 추가
5. 새 기동 검증 추가
6. 새 API 엔드포인트 추가
7. 새 이벤트 종류 — **대개 답은 "추가하지 마라"**. 성질 시험과 기각 사례
8. 새 저장소 백엔드 메서드 추가(두 구현 + 계약 테스트)
9. 케이스 종결 경로 추가
10. 프롬프트에 새 줄 추가

그리고 **집행 전 점검표**: 이 리포에서 실제로 깨졌던 것들을 질문 형태로.

- [ ] **Step 2: 확인** — 레시피가 지목한 파일·함수가 전부 실재하는지 대조.

- [ ] **Step 3: 커밋**

---

### Task 4: 기존 문서 갱신

**Files:** Modify: `docs/tutorial.md`, `docs/going-live.md`, `README.md`,
`docs/howto.md`, `docs/config-reference.md`, `docs/glossary.md`, `CLAUDE.md`

- [ ] **Step 1: 누락 실측을 다시 돌려 갱신할 항목을 확정한다**

- [ ] **Step 2: 문서마다 갱신** — 튜토리얼은 **실제로 돌려 보고** 적는다(명령이 낡았으면
  튜토리얼이 거짓말한다).

- [ ] **Step 3: 전체 스위트** — `tests/test_examples.py`가 README·튜토리얼을 대조한다.

- [ ] **Step 4: 커밋**

---

## 인계(계획 23 이후)

1. **줄 번호를 쓴 인용은 낡는다** — 함수 이름 위주로 썼지만 몇 곳은 줄 번호가 필요했다.
   코드가 움직이면 이 문서도 움직여야 한다.
2. **`file-map.md`는 파일이 늘면 낡는다** — 파일 목록이 `find src`와 일치하는지 보는
   확인 스크립트를 Task 2 Step 2에 적어 뒀다. 자동화하려면 테스트로 올려야 한다.
