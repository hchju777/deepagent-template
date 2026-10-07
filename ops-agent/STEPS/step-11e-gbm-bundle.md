# 11e단계 — GBM 단위 그래프 번들

> **목적**: `code graph`가 사이트마다 20분이던 것을 GBM당 한 번, 몇 분 안으로. 그래프는 **GBM 단위**다 —
> 심볼 인덱스·끝점 사슬·오버레이·graphify·사람용 페이지 전부 `output/graph/<gbm>/`에 하나. 사이트에
> 남는 것은 사이트 층이 덮은 값 몇 개(`sites/<fct>.json`)뿐이고, 조사 시작 때 그 사이트 몫만 끼운다.
> 상태: **11e-1 됐다(10-07)** · 11e-2(번들 레이아웃·CLI) 다음.

## 왜 (10-07에 정리한 것)

- 사내에서 `code graph`가 사이트 하나에 **약 20분**. 사이트 28개면 못 쓴다. graphify를 따로 돌리면 금방
  끝났다고 하므로 20분은 우리 쪽이다.
- 측정판에서 git 호출을 세어 보니(`gitshim`) 47번 중 `show` 19번이고, 그중 **`.py` 파일당 한 번**이 인덱서다.
  Windows에서 git 프로세스 하나가 0.5~1초라 사내 .py 1,600개 급이면 그대로 10~20분이다. graphify는
  worktree의 파일을 그냥 읽어서 빠르다 — 우리 인덱스만 파일마다 git에 물었다.
- 번들에서 사이트에 따라 달라지는 것은 config 층에서 온 값뿐이다. 측정판(사내 모양을 본뜸)의 사이트 층은
  `lines`·`site_code`와 `group_id` 덮어쓰기(`mx-core` → `gumi-mx-core`)가 전부고 토픽·컬렉션·키 이름은 GBM
  층에 있다. 사이트는 "무엇을 읽고 무엇을 돌릴지"를 고르는 층이지 이름을 새로 정하는 층이 아니다 — 그래서
  그래프는 GBM에 하나.
- graphify는 **기본 켬 그대로**. 사내 실제 레포에서는 `calls` 5,421·`imports` 4,155·`inherits` 1,170 같은
  진짜 심볼 그래프를 냈다(측정판 가짜 레포가 너무 작아 `contains`·`rationale_for`만 보였던 것을 성질로 일반화한
  것이 틀렸다). 조사는 아직 우리 인덱스만 읽지만, 둘을 같은 커밋에서 대조하는 것(6b-3 하네스 D)이 값을
  갖게 됐다 — 별도 단계.

## 종료 판단 (시작할 때 적음, 10-07)

1. `code graph --gbm mx` 한 번으로 그 GBM의 모든 사이트가 조사에 실린다. 측정판에서 gumi 조사는
   `gumi-mx-core`, sevt 조사는 sevt 값을 받는다.
2. 인덱스 읽기는 레포당 `git archive` 1번 + 채워진 서브모듈당 1번. `.py`용 `show` 0.
3. 결과 동일 — 측정판 `symbols.json`·`edges.json`은 바이트까지 같고, 오버레이는 덮어쓴 값만 다르다.
   서브모듈 픽스처에서 show 기반과 archive 기반 인덱스가 같고 blind·stale 규칙이 같다. `git archive` 실패 시
   파일별 show로 내려간다.
4. graphify는 켬 그대로. 조사가 읽는 것은 전후가 같다.
5. 사내 `code graph --gbm mx` 한 번의 시간과 `code check` 첫 줄(심볼 7,308 · calls 4,885/1,710)이 같은지.

## 11e-1 — 인덱스 소스를 커밋 스냅샷에서 ✅

- `CodeReaderPort.snapshot(repo, commit)` — 그 커밋의 파일 전부를 `경로 → 본문`으로 한 번에. 읽기이고 커밋을
  지정하므로 포트의 성질(쓰기 없음·커밋 명시)이 그대로다. `tests/domain/test_ports.py`가 자동으로 본다.
- `RealCodeReader.snapshot`: `git archive --format=tar <커밋>`을 메모리에서 `tarfile`로 푼다. 외부 tar도
  디스크 쓰기도 없다. 내용은 `show(whole=True)`와 같다(같은 디코딩, 같은 1,000,000자 상한). submodule은
  `ls`·`show`와 **같은 헬퍼**(`_declared_subs`·`_blind`·`_stale`·`_gitlink`)로 같은 규칙 — 채워진 것은 부모가 박은
  SHA로 그 레포에서 한 번 더 받고, 안 채워진 것과 버전 없는 것은 빼고 봉투가 말한다. `export-ignore`로
  아카이브에서 빠진 파일은 목록(`ls`)과 대조해 그만큼만 `show`로 메운다(평소 0건).
- `_GitSource`(인덱서의 소스)는 첫 호출에 스냅샷을 한 번 받아 `files()`·`read()`를 거기서 답하고, 스냅샷을 못
  받으면(옛 git·시간 초과) 예전처럼 `ls`·파일별 `show`로 간다. `build_index`와 `IndexSource`는 안 바뀌었다.
- 측정판 결과: `symbols.json`·`edges.json`·`overlay.json` **바이트까지 같다.** git 호출 47 → 36, `.py`용 `show`
  3 → 0, `archive` 3(dt-core·shared_lib·dt-api). 남은 `show` 11은 서비스별 config 층(3×3)과 `.gitmodules`다 —
  11e-2에서 28사이트 덮어쓰기를 만들 때 이것도 스냅샷에서 읽는다(안 그러면 28×8×3 ≈ 670번이 되살아난다).
- 테스트: `test_git_reader.py` 7(같은 내용·없는 커밋·서브모듈 핀 버전·blind·stale·호출 수·export-ignore),
  `test_deployed_code.py` 2(스냅샷 우선·실패 시 되돌아감), `test_cli_code.py`에 `code graph` 동안 `.py`용
  `show` 0. 구현을 먼저 써 버려서 src 변경을 stash로 걷어내고 **RED 10을 본 뒤** 되돌려 GREEN을 봤다. 스윕 +7.

예상(Windows): 인덱스 읽기 1,600 × 0.5~1초 → archive 2~3번 수 초. 인덱서 파이썬은 심볼 2,172개가 1.9초
측정 → 7,308개 비례 약 10초. 확인은 종료 판단 5.

## 11e-2 — GBM 번들 레이아웃·CLI (다음)

`output/graph/<gbm>/` 하나에 인덱스·사슬·오버레이·graphify·사람용 페이지, `sites/<fct>.json`에 사이트 층이 덮은
값만. `code graph --gbm mx`가 GBM의 사이트 전부를 돈다(사이트당 config 층 병합은 스냅샷에서, git 0번).
조사·`code status/trace/flow/callers/uses`는 GBM 번들을 읽고 사이트 값을 입힌다. 커밋은 GBM 하나, 사이트 핀이
다르면 그 사이트에만 안 싣는다.

## 그다음 (별도 예고)

- 사이트에서 도는 파이프라인만 배선으로 보이게 — 토폴로지에 "돌릴 파이프라인을 정하는 config 키"를
  `flow.sources`처럼 선언하고, 조사 시작 때 그 사이트 값으로 꺼진 처리기를 `<데이터 흐름>`에서 표시하거나 뺀다.
  지금은 두 엔진 다 코드에 있는 것을 전부 잇는다.
- graphify `calls` vs 우리 인덱스 `calls`를 같은 커밋에서 대조(6b-3 하네스 D).
