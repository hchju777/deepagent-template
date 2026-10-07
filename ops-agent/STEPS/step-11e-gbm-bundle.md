# 11e단계 — GBM 단위 그래프 번들

> **목적**: `code graph`가 사이트마다 20분이던 것을 GBM당 한 번, 몇 분 안으로. 그래프는 **GBM 단위**다 —
> 심볼 인덱스·끝점 사슬·오버레이·graphify·사람용 페이지 전부 `output/graph/<gbm>/`에 하나. 사이트에
> 남는 것은 사이트 층이 덮은 값 몇 개(`sites/<fct>.json`)뿐이고, 조사 시작 때 그 사이트 몫만 끼운다.
> 상태: **11e-1·11e-2·11e-3 됐다(10-07)** · 남은 것은 11e-3 뒤 사내 한 번의 시간(종료 판단 5의 두 번째 측정).

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
2. 인덱스 읽기는 레포당 git 한 쌍(`ls-tree -r` + `cat-file --batch`) + 채워진 서브모듈당 한 쌍. `.py`용 `show` 0.
   (처음엔 `git archive` 1번이었다 — 아래 11e-1의 사내 확인에서 바꿨다.)
3. 결과 동일 — 측정판 `symbols.json`·`edges.json`은 바이트까지 같고, 오버레이는 덮어쓴 값만 다르다.
   서브모듈 픽스처에서 show 기반과 스냅샷 기반 인덱스가 같고 blind·stale 규칙이 같다. 스냅샷 실패 시
   파일별 show로 내려간다.
4. graphify는 켬 그대로. 조사가 읽는 것은 전후가 같다.
5. 사내 `code graph --gbm mx` 한 번의 시간과 `code check` 첫 줄(심볼 7,308 · calls 5,272/1,710 — 처음엔 4,885로
   적었는데 그건 6b 전 숫자였다, 마지막 사내 기록은 6b 종료의 5,272)이 같은지.

결과(10-07, 측정판): 1·2·4는 그대로 됐다. 3은 **글자 그대로는 아니다** — `edges.json`은 바이트까지 같지만 `symbols.json`은
덮인 그룹의 자원 이름(`gumi-mx-core` → 기준값 `mx-core`)만 다르다. 기준값이 GBM 층이 된 결과이고, 조사는 그 사이트의
이름을 입혀 받으므로 리드가 보는 것은 전후가 같다(아래 11e-2 측정).

5(사내, 10-07): `code graph --gbm mx` 한 번 **562.4초**(약 9.4분). 전엔 사이트 하나에 약 20분이었고 28사이트면 못
쓰던 것이 GBM 한 번이다. `code check`: 심볼 7308(module 876 · class 1657 · function 666 · method 4109) · 파싱 실패 0 ·
calls 5772/1710 · inherits 782 · imports 2324 · overrides 1859 · implements 184 · 불변식 OK · 정밀도 100/100 ·
재현율 365/365 · 미해석 builtin 3572 · external 2169 · field_call 3 · method_missing 67 · stoplist 4552 · unknown 1337 ·
variable_call 85 · 자원 참조 946 · gap 3. **심볼 수·파싱 실패·추정 calls 1710은 지금까지의 사내 기록과 같다** — 스냅샷이
넣은 파일과 본문이 전과 같다는 뜻이다. calls 확실은 마지막 사내 기록 5272(6b 종료, 10-02)보다 500 많은데, 그 사이에
5a91d93(10-03, 포트→구현 이름 규칙 + `Depends` 수신자 — implements 82 → 184도 여기서)이 사내 `code check` 기록 없이
들어갔다. 11e는 인덱서 로직을 안 건드렸고(바뀐 것은 바이트가 들어오는 길뿐, 측정판에서 바이트 동일) 차이는 거기서 온다.
562초의 분해(사내 진행 줄, 레포 5개): 스냅샷 받기·이름 뽑기 24초 → **이름 찾기 `git grep` 19번 × 5레포 = 219초**
→ 라우트 선언 grep 5번 12초 → 끝점·인덱서(7308)·사슬 10초 → **graphify 5레포 순차 296초**(worktree 2 + extract
35~45 + cluster 15~30씩) → 사이트·쓰기·wiki 1초. 인덱서는 예상대로 10초. 우리 몫 231초는 11e-1이 만든 모순이다 —
본문을 메모리에 다 받아 놓고도 이름을 찾을 때는 `git grep <커밋>`을 띄워 git이 매번 전체 blob을 다시 풀게 했다.
그래서 11e-3.

## 11e-1 — 인덱스 소스를 커밋 스냅샷에서 ✅

- `CodeReaderPort.snapshot(repo, commit)` — 그 커밋의 파일 전부를 `경로 → 본문`으로 한 번에. 읽기이고 커밋을
  지정하므로 포트의 성질(쓰기 없음·커밋 명시)이 그대로다. `tests/domain/test_ports.py`가 자동으로 본다.
- `RealCodeReader.snapshot`: 레포당 git 두 번 — `ls-tree -r -z <커밋>`으로 blob 목록을, `cat-file --batch`(표준 입력으로
  sha 목록)로 본문을 받는다. 디스크 쓰기 없음. 내용은 `show(whole=True)`와 **바이트까지** 같다(같은 blob, 같은
  디코딩, 같은 1,000,000자 상한). submodule은 `ls`·`show`와 **같은 헬퍼**(`_declared_subs`·`_blind`·`_stale`·
  `_gitlink`)로 같은 규칙 — 채워진 것은 부모가 박은 SHA로 그 레포에서 한 쌍 더 받고, 안 채워진 것과 버전 없는
  것은 빼고 봉투가 말한다. 목록의 gitlink(type `commit`)는 파일이 아니라 뺀다.
- **사내 확인(10-07)에서 바꾼 것**: 처음 구현은 `git archive --format=tar`를 메모리에서 푸는 것이었는데, 사내 Windows
  (`core.autocrlf=true`)에서 `test_snapshot은_show와_같은_내용을_한_번에_준다`·`…부모가_박은_버전으로_넣는다`가
  CRLF로 빨간불이 났다. archive는 **체크아웃과 같은 변환**(autocrlf, `.gitattributes`의 `eol`·`filter`·`export-ignore`)을
  타고, `show <커밋>:<경로>`는 저장소의 blob 그대로다 — 여기 리눅스에서도 `core.autocrlf=true`나 `*.json text eol=crlf`로
  재현됐다(`test_snapshot은_작업_트리용_변환을_안_타고_show와_바이트까지_같다`). `cat-file`은 blob 그대로라 바이트까지
  같고, `export-ignore` 구멍(과 그걸 메우던 `show`)이 없어졌고, LFS 같은 filter가 smudge로 밖에 나가려 들 일도 없다.
  프로세스는 레포당 1 → 2(목록·본문)지만 파일 수와 무관한 것은 같다.
- `_GitSource`(인덱서의 소스)는 첫 호출에 스냅샷을 한 번 받아 `files()`·`read()`를 거기서 답하고, 스냅샷을 못
  받으면(옛 git·시간 초과) 예전처럼 `ls`·파일별 `show`로 간다. `build_index`와 `IndexSource`는 안 바뀌었다.
- 측정판 결과: `symbols.json`·`edges.json`·`overlay.json` **바이트까지 같다.** git 호출 47 → 36, `.py`용 `show`
  3 → 0, `archive` 3(dt-core·shared_lib·dt-api; 지금은 `ls-tree`+`cat-file` 3쌍). 남은 `show` 11은 서비스별 config 층(3×3)과 `.gitmodules`다 —
  11e-2에서 28사이트 덮어쓰기를 만들 때 이것도 스냅샷에서 읽는다(안 그러면 28×8×3 ≈ 670번이 되살아난다).
- 테스트: `test_git_reader.py` 9(같은 내용·없는 커밋·서브모듈 핀 버전·blind·stale·호출 수·export-ignore 파일도 듦·
  작업 트리용 변환 안 탐·같은 내용의 두 파일),
  `test_deployed_code.py` 2(스냅샷 우선·실패 시 되돌아감), `test_cli_code.py`에 `code graph` 동안 `.py`용
  `show` 0. 구현을 먼저 써 버려서 src 변경을 stash로 걷어내고 **RED 10을 본 뒤** 되돌려 GREEN을 봤다. 스윕 +7.

예상(Windows): 인덱스 읽기 1,600 × 0.5~1초 → 레포당 git 두 번, 수 초. 인덱서 파이썬은 심볼 2,172개가 1.9초
측정 → 7,308개 비례 약 10초. 확인은 종료 판단 5.

## 11e-2 — GBM 번들 레이아웃·CLI ✅

- 번들은 `output/graph/<gbm>/` 하나(`graph_build.bundle_dir`) — 인덱스(`symbols.json`·`edges.json`)·오버레이·합친
  그래프·`flow.html`·`calls.html`·graphify 산출물 전부. `meta.fct=""`, `meta.sites`에 그 번들이 덮은 값을 적은 사이트 목록.
- 이름의 **기준값은 GBM 층만**으로 병합한 것(`DeployedCode.names_for("")` — `{fct}`가 든 층을 뺀다). 사이트마다는
  `names_for(fct)`와 비교해 **다른 값만** `sites/<fct>.json`에 — 행은 `{kind, key_path, value, base, services, relation}`,
  `base`는 그 키의 GBM 값(GBM 층에 없던 키면 null). 측정판은 사이트당 1행(`group_id`).
- 사이트 층 병합은 레포 스냅샷(11e-1)에서 한다(`_layers_in`) — 사이트 28개 × 서비스 × 층을 git에 다시 묻지 않는다.
  스냅샷을 못 받으면 그 경로만 `show(whole=True)`로 내려간다(같은 결과, 느릴 뿐 — 400줄 상한 규칙도 `_layers`와 같다).
- `code graph --gbm mx`가 registry의 그 GBM 활성 사이트 전부를 적는다. `--fct`를 주면 그 사이트만. 등재 항목(`entries`)은
  사이트 전부의 합집합이다(끝점 노드는 번들에 한 번 선다). `code sync` 끝도 같은 길(`_build_graph`).
- 조사 시작(`_code_if_ready`)과 `code status/flow/trace/uses/callers/path`는 GBM 번들을 읽고 **그 사이트의 값을 입힌다**:
  그래프는 `flow.apply_site`(같은 `key_path`의 노드는 이름만 바뀌고 엣지가 따라간다, GBM 층에 없던 이름은 config 엣지만
  단 새 노드), 심볼 인덱스는 `Index.renamed(flow.site_renames(rows))`(함수별 자원 이름). `code check`는 번들 그대로를
  잰다. 사이트 파일이 없으면 GBM 값 그대로 싣고 한 줄 말한다(`사이트 덮어쓰기 없음`). 사이트 핀이 번들 커밋과 다르면
  그 사이트에만 안 싣는다(낡음 판정 그대로).
- 측정판(사이트 gumi·sevt, 사이트 층은 `group_id`만 덮는다): `code graph --gbm mx` 한 번에 `sites/gumi.json`(gumi-mx-core)·
  `sites/sevt.json`(sevt-mx-core) 각 1행. git 호출 36 → **24**(`ls-tree`+`cat-file --batch` 3쌍, `show`는 `.gitmodules` 2뿐, config 층·`.py` 0 —
  archive 때 목록과 대조하던 `ls`도 없어졌다).
  `edges.json`은 11e 전과 바이트까지 같고 `symbols.json`·오버레이는 그룹 이름만 다르다(위 종료 판단 3). 조사 조립에서
  gumi는 `gumi-mx-core`, sevt는 `sevt-mx-core`를 받고(`known_names` 차이가 그 둘뿐) `code.uses(그 이름)`도 사이트마다
  답한다. 측정판엔 graphify가 안 깔려 있어 그 다리는 여기서 안 돌았다(사내에서 켬, 코드는 안 바뀜).
- 하다 잡은 것: 처음엔 그래프에만 입히고 인덱스는 GBM 값 그대로 뒀다 — 리드가 `<데이터 흐름>`에서 본 `gumi-mx-core`로
  `code.uses`를 물으면 "인덱스에 없다"가 됐을 것이다. 측정판 `symbols.json` 대조(그룹 자원 이름이 달라짐)에서 드러나
  행에 `base`를 더하고 인덱스도 입힌다.
- 테스트: `test_graph_build.py` 2(번들 경로·사이트 파일·옛 meta), `test_flow.py` 4(이름만 바뀌고 엣지 따라감·새 노드는
  config 엣지만·두 번 같음·자원 이름 표), `test_deployed_code.py` 4(GBM 층만·다른 값만·스냅샷 뒤 git 0·되돌아가는 길도
  통째로), `test_index.py` 1(`renamed`), `test_cli_code.py` 4(GBM 번들 한 번·등재 합집합·사이트 파일 없을 때·status/flow/uses).
  테스트를 먼저 썼다(RED 17 → GREEN, 인덱스 쪽 RED 4 → GREEN). 스윕 +22, 11e-2가 옮긴 앵커 2개 재지정.

## 11e-3 — 이름·라우트 찾기를 스냅샷에서, graphify는 레포 병렬 ✅

- `graph_build.grep_snapshot(repo, commit, files, patterns, fixed, context)`: 스냅샷(경로 → 본문)에서 `git grep -n -I
  [-F] [-C1] <커밋>`과 **같은 `Hit`**를 프로세스 없이. 같아야 하는 것을 패리티 테스트가 진짜 git과 대조한다 — 파일 순서
  (트리 순서 = 전체 경로의 바이트 순서), 줄 번호(`\n`으로만 가르고 꼬리 `\r`은 뗀다), NUL이 든 파일 제외(`-I`), 한 줄에
  패턴이 여럿이어도 `Hit` 하나, 문맥은 줄 번호로 앞뒤 한 줄(`context=0`이면 인접한 매치 줄만 — `_parse_group`이 그렇게
  읽는다), 정규식(라우트)까지. 라우트의 파이썬 정규식 `flow.ROUTE_REGEXES`는 git에 주는 BRE `ROUTE_PATTERNS`에서
  만든다(BRE의 `(`만 이스케이프) — 한 출처.
- `flow_hits`·`route_hits`는 스냅샷이 있으면 거기서 찾고(git 0번), 못 받았으면 전처럼 `git grep`을 묶어 띄운다. 스냅샷이
  못 본 submodule의 사유는 전과 같은 "잘렸다" 메모로 간다(`_snapshot_result`가 사유를 같이 든다). 스냅샷 길에는 봉투
  상한(`FLOW_MAX_LINES`)이 없다 — 봉투를 안 거친다.
- graphify는 레포마다 독립이라 `run_graphify_many`가 스레드 풀에서 겹쳐 돌린다(자식 프로세스라 GIL 밖, worktree는
  레포마다 따로). 폭은 코드가 정한다 — `graphify_width` = min(4, CPU 수, 레포 수). 합치는 순서는 끝난 순서가 아니라
  config의 레포 순서 그대로라 결과가 같다. 진행 줄은 레포 이름이 붙어 섞여 나온다.
- 측정판(레포 2개, graphify 없음): `overlay.json`·`symbols.json`·`edges.json`·`graph.json`·`sites/*.json` 전부 11e-2 번들과
  **바이트까지 같다.** git 호출 24 → **14**(`grep` 4 → 0, grep이 매번 하던 submodule 검사 `config`·`ls-tree`도 같이 사라짐).
- 예상(사내, 레포 5개): 이름·라우트 231초 → 수 초, graphify 296초 → 가장 긴 레포 하나(약 65초, 폭 4면 5레포가 두 바퀴라
  70~110초). 합쳐 약 100~150초. 확인은 사내 한 번.
- 테스트: `test_graph_build.py` 2(패리티 — 인접 매치·CRLF·같은 blob 둘·정렬 경계·NUL 파일·라우트 세 모양; 병렬·순서·폭),
  `test_deployed_code.py` 3(스냅샷이면 grep 0번 + 되돌아간 길과 답 같음, 상한은 되돌아간 길만, 못 본 submodule 사유),
  `test_cli_code.py` 2(`code graph` 동안 `grep` 0, graphify가 병렬 길을 탄다). 테스트를 먼저 썼다(RED 5 → GREEN). 스윕 +12,
  짝 테스트가 바뀐 기존 3건은 다시 짝지었다(전체 스윕에서 초록으로 드러남).

## 그다음 (별도 예고)

- 사이트에서 도는 파이프라인만 배선으로 보이게 — 토폴로지에 "돌릴 파이프라인을 정하는 config 키"를
  `flow.sources`처럼 선언하고, 조사 시작 때 그 사이트 값으로 꺼진 처리기를 `<데이터 흐름>`에서 표시하거나 뺀다.
  지금은 두 엔진 다 코드에 있는 것을 전부 잇는다.
- graphify `calls` vs 우리 인덱스 `calls`를 같은 커밋에서 대조(6b-3 하네스 D).
