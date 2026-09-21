"""**방어를 하나씩 지워서 테스트가 정말로 빨개지는지 본다** (RED 스윕).

`pytest`가 초록인 것은 "테스트가 통과한다"는 뜻이지 "테스트가 무언가를 지킨다"는
뜻이 아니다. 이 리포는 그 차이로 여러 번 당했다 — 방어를 지워도 초록인 테스트가
실제로 여러 개 있었다(handover.md "초록불이 아무것도 증명하지 않았던 세 번").

여기 모아 둔 것은 11a(대상 코드 확보)의 submodule 관련 방어들이다. 사내 Windows에서
네 라운드에 걸쳐 터진 자리라, **고칠 때마다 전부 다시 돌린다.**

    .venv/bin/python tools/red_sweep.py

각 항목은 제품 소스의 한 조각을 망가뜨리고 → 짝이 되는 테스트를 돌리고 →
**원래대로 되돌린다.** 되돌리기는 `finally`에 있고, 끝나고 작업 트리가 깨끗한지
스스로 확인한다.

`tests/` 밖에 있는 이유: pytest가 수집하면 안 된다. 이건 테스트가 아니라
**테스트를 검사하는 도구**다.

## 되돌린 뒤 `__pycache__`도 지운다

되돌리기만으로는 부족하다. 파이썬은 `.pyc`가 최신인지 **소스의 mtime과 크기**로
판단하는데, 망가뜨린 내용이 원본과 **같은 길이**이고(예: `1200` → `2400`) 같은 초
안에 되돌리면 **둘 다 그대로다.** 그러면 파이썬은 망가진 `.pyc`를 계속 쓴다.

실제로 그렇게 당했다: 트리는 `git status`로 깨끗한데 테스트 둘이 계속 빨간불이었고,
`evidence_chars`가 디스크에는 2400인데 런타임에는 1200이었다. 원인이 안 보이는
종류의 실패라 여기서 막는다.

## 중간에 끊겨도 소스를 되돌린다

`finally`만으로는 부족하다. Ctrl-C나 종료 신호로 프로세스가 끊기면 그 자리에서
죽고, **망가뜨린 소스가 그대로 남는다.** 실제로 그렇게 한 번 남겼고, 그 뒤 전체
테스트가 빨간불이 나서 원인을 찾는 데 시간이 들었다. 그래서 건드린 파일을 전부
기억해 두고, 신호로 끊길 때도 복구한 뒤 나간다.
"""
import atexit
import signal
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
R, C, M, S = (ROOT/"src/infrastructure/git_reader.py", ROOT/"src/knowledge/checkout.py",
              ROOT/"src/__main__.py", ROOT/"tests/support.py")
T = ROOT / "src/knowledge/target_config.py"
D = ROOT / "src/infrastructure/deployed_code.py"
B = ROOT / "src/application/briefing.py"
GR = ROOT / "src/infrastructure/git_reader.py"
MN = ROOT / "src/__main__.py"
ND = ROOT / "src/application/nodes.py"
RP = ROOT / "src/application/runner_probe.py"
SA = ROOT / "src/config/schema_app.py"
TD = ROOT / "src/application/trace_digest.py"
K = "tests/knowledge/test_checkout.py"
K2 = "tests/knowledge/test_target_config.py"
K3 = "tests/infrastructure/test_deployed_code.py"
K4 = "tests/application/test_briefing.py"
K5 = "tests/knowledge/test_cli_code.py"; G = "tests/infrastructure/test_git_reader.py"
L = "tests/knowledge/test_cli_code.py"; P = "tests/test_portability.py"
CASES = [
 ("show가 경계를 안 넘는다", R,
  '        sub = containing_submodule(await self._declared_subs(repo, commit), path)\n        if sub:',
  '        sub = ""\n        if sub:',
  [f"{G}::test_안_채워진_submodule의_파일은_없다고_하지_않는다", f"{G}::test_채워진_submodule_안을_실제로_읽는다"]),
 ("grep이 못 본 구석을 안 말한다", R,
  'unseen=await self._blind(repo, commit))', 'unseen=[])',
  [f"{G}::test_안_채워진_submodule이면_grep이_조용히_0건을_안_준다"]),
 ("grep이 submodule로 안 들어간다", R,
  '"grep", "-n", "-I", "--no-color", "--recurse-submodules"', '"grep", "-n", "-I", "--no-color"',
  [f"{G}::test_채워진_submodule_안까지_grep한다"]),
 ("ls가 못 본 구석을 안 말한다", R,
  '        reasons += _unseen_reasons(await self._blind(repo, commit))', '        reasons += []',
  [f"{G}::test_목록도_submodule_안은_못_봤다고_말한다"]),
 ("봉투가 늘 불완전하다고 우긴다", R,
  '    if not blind:\n        return []', '    if not blind:\n        return ["아무튼 못 봤다"]',
  [f"{G}::test_submodule이_없으면_봉투가_멀쩡하다"]),
 ("show가 stale을 안 본다", R,
  '        if sub in await self._blind(repo, commit):', '        if False:',
  [f"{G}::test_안_채워진_submodule의_파일은_없다고_하지_않는다"]),
 ("grep이 stale에 git 원문을 흘린다", R,
  '        behind = await self._stale(repo, commit, subs)\n        if behind:', '        behind = []\n        if behind:',
  [f"{G}::test_버전이_없으면_grep이_git의_원문을_안_흘린다"]),
 ("등록을 안 본다(.git만 본다)", C,
  '                   if name not in registered\n                   or not (Path(repo.path) / path / ".git").exists()})',
  '                   if not (Path(repo.path) / path / ".git").exists()})',
  [f"{K}::test_채워도_등록이_안_되면_여전히_못_본다"]),
 ("등록 정보가 없으면 괜찮다고 한다", C,
  '    registered = {match.group(1) for line in out.splitlines()',
  '    if not out:\n        return []\n    registered = {match.group(1) for line in out.splitlines()',
  [f"{K}::test_물어볼_수_없으면_읽을_수_있다고_말하지_않는다"]),
 ("이름 대신 경로로 등록을 찾는다", C,
  '        if match and name:\n            entries[name] = match.group(1).strip()',
  '        if match and name:\n            entries[match.group(1).strip()] = match.group(1).strip()',
  [f"{K}::test_이름이_경로와_다른_submodule도_등록을_알아본다"]),
 ("path로 시작하는 키를 다 먹는다", C,
  '_PATH_LINE = re.compile(r"path\\s*=\\s*(.+)$")', '_PATH_LINE = re.compile(r"path\\S*\\s*=?\\s*(.*)$")',
  [f"{K}::test_path로_시작하는_다른_키를_안_먹는다"]),
 ("경로 검사가 경계를 안 넘는다", C,
  '    sub = containing_submodule(subs, path)\n    if not sub:', '    sub = ""\n    if not sub:',
  [f"{K}::test_submodule_안의_config_층을_없다고_하지_않는다", f"{L}::test_submodule_안의_config_층을_없다고_하지_않는다"]),
 ("경계를 넘으며 아무거나 통과시킨다", C,
  '    code, _, _ = _git(Path(repo.path) / sub, "cat-file", "-e", f"{sha}:{rest}")\n    return code == 0', '    return True',
  [f"{K}::test_진짜로_없는_경로는_여전히_없다고_한다"]),
 ("stale을 안 본다", C,
  '        if code != 0:\n            behind.append(sub)\n    return behind', '        if code != 0:\n            pass\n    return behind',
  [f"{K}::test_채워졌어도_그_커밋의_버전이_없으면_찾아낸다"]),
 ("토큰 헤더를 호스트에 안 묶는다", C,
  '    return ["-c", f"http.{host.group(1)}/.extraHeader=Authorization: Basic "',
  '    return ["-c", f"http.extraHeader=Authorization: Basic "',
  [f"{K}::test_토큰_헤더는_호스트에_묶인다"]),
 ("ssh url에도 헤더를 붙인다", C,
  '    host = _HTTP_HOST.match(repo.url)\n    if not host:\n        return []',
  '    host = _HTTP_HOST.match(repo.url) or _HTTP_HOST.match("https://x")',
  [f"{K}::test_ssh_url에는_토큰_헤더를_안_붙인다"]),
 ("sync가 submodule을 안 채운다", C,
  '    code, out, err = _git(Path(repo.path), *header, "submodule", "update",\n                          "--init", "--recursive", timeout=_TIMEOUT_S)',
  '    code, out, err = 0, "", ""', [f"{K}::test_sync가_안_채워진_submodule을_채운다"]),
 ("plan이 fetch 한 줄만 준다", C,
  '    return [f"git -C {repo.path} fetch --all --prune --recurse-submodules",\n            f"git -C {repo.path} submodule update --init --recursive"]',
  '    return [f"git -C {repo.path} fetch --all --prune --recurse-submodules"]',
  [f"{K}::test_plan이_submodule_채우는_줄까지_준다"]),
 ("_git이 로캘로 디코딩한다", C,
  '                              text=True, encoding="utf-8", errors="replace",\n                              timeout=timeout)',
  '                              text=True, timeout=timeout)', [f"{P}::test_자식_프로세스_출력도_인코딩을_명시한다"]),
 ("sync clone이 로캘로 디코딩한다", C,
  '                capture_output=True, text=True, encoding="utf-8", errors="replace",\n                timeout=_TIMEOUT_S)',
  '                capture_output=True, text=True, timeout=_TIMEOUT_S)', [f"{P}::test_자식_프로세스_출력도_인코딩을_명시한다"]),
 ("status가 submodule을 안 본다", M,
  '                blocked = _report_submodules(repo, pin.commit)', '                blocked = 0',
  [f"{L}::test_안_채워진_submodule을_status가_말한다"]),
 ("status가 submodule 탓을 config에 돌린다", M,
  '                if blocked:\n                    print(f"       (먼저 위의 submodule부터 — 그 안의 층은 지금 안 읽힌다)")\n                else:',
  '                if False:\n                    pass\n                else:',
  [f"{L}::test_submodule이_안_읽히면_config_경로를_탓하지_않는다"]),
 ("윈도우 경로를 그대로 .gitmodules에", S,
  '    return str(path).replace("\\\\", "/")', '    return str(path)',
  [f"{K}::test_윈도우_경로가_gitmodules에서_깨지지_않는다"]),
 ("픽스처가 안 채워져도 말 안 한다", S,
  '    git("-c", "protocol.file.allow=always", "submodule", "update", "--init", "--",\n        path, cwd=checkout)',
  '    pass', [f"{K}::test_채워졌으면_신고하지_않는다"]),
 ("픽스처가 file 허락을 탄다", S,
  '    git("update-index", "--add", "--cacheinfo", f"160000,{at},{path}", cwd=parent)',
  '    git("submodule", "add", "-q", str(sub), path, cwd=parent)',
  [f"{K}::test_submodule_픽스처가_file_프로토콜_허락을_안_탄다"]),
 # ── 대상 config 층 병합 (11a 2차) ──────────────────────────────────
 ("나중 층이 안 덮는다(앞이 이긴다)", T,
  '    for _, layer in layers:\n        _overlay(merged, layer)',
  '    for _, layer in reversed(layers):\n        _overlay(merged, layer)',
  [f"{K2}::test_나중_층이_덮는다"]),
 ("dict를 재귀 안 하고 통째로 교체", T,
  '        if isinstance(value, dict) and isinstance(into.get(key), dict):\n            _overlay(into[key], value)\n        else:',
  '        if False:\n            pass\n        else:', [f"{K2}::test_dict는_재귀로_합친다"]),
 ("결과가 입력을 그대로 가리킨다(deepcopy 제거)", T,
  '            into[key] = deepcopy(value) if isinstance(value, (dict, list)) else value',
  '            into[key] = value', [f"{K2}::test_층이_하나뿐이어도_입력과_공유하지_않는다"]),
 ("null을 우리 규칙(삭제)으로 처리", T,
  '            into[key] = deepcopy(value) if isinstance(value, (dict, list)) else value',
  '            if value is None:\n                into.pop(key, None)\n                continue\n            into[key] = deepcopy(value) if isinstance(value, (dict, list)) else value',
  [f"{K2}::test_null은_값이다_우리_로더와_다르다"]),
 ("리스트를 이어붙인다", T,
  '            into[key] = deepcopy(value) if isinstance(value, (dict, list)) else value',
  '            if isinstance(value, list) and isinstance(into.get(key), list):\n                into[key] = into[key] + value\n                continue\n            into[key] = deepcopy(value) if isinstance(value, (dict, list)) else value',
  [f"{K2}::test_리스트는_교체다"]),
 ("파싱 실패에 파일 이름을 안 적는다", T,
  '        return None, f"{path}: JSON이 아니다 — {type(exc).__name__}: {exc}"',
  '        return None, f"JSON이 아니다 — {type(exc).__name__}: {exc}"',
  [f"{K2}::test_json이_아니면_이유를_돌려준다"]),
 ("최상위 객체 검사를 뺀다", T,
  '    if not isinstance(value, dict):\n        return None, f"{path}: 최상위가 객체가 아니다 — {type(value).__name__}"',
  '    if False:\n        pass', [f"{K2}::test_최상위가_객체가_아니면_거부한다"]),
 # ── 서비스 이름으로 읽기 (11a 2차) ────────────────────────────────
 ("config가 층 하나만 읽는다", D,
  '        return ProbeResult.succeeded(\n            merge_target(layers), source=f"{source} [{read}]", clock=self._clock,',
  '        return ProbeResult.succeeded(\n            layers[-1][1], source=f"{source} [{read}]", clock=self._clock,',
  [f"{K3}::test_층을_합친_값을_돌려준다"]),
 ("법인 자리를 안 치환한다", D,
  '        wanted = self._topology.resolved_config_paths(self._gbm, self._fct)',
  '        wanted = self._topology.resolved_config_paths(self._gbm, "gumi")',
  [f"{K3}::test_법인이_다르면_다른_값이_나온다"]),
 ("어느 층을 읽었는지 안 남긴다", D,
  '        read = " → ".join(path for path, _ in layers)', '        read = ""',
  [f"{K3}::test_어느_층을_읽었는지_증거에_남는다"]),
 ("깨진 층을 조용히 넘긴다", D,
  '            if value is None:\n                broken.append(why)\n                continue',
  '            if value is None:\n                continue',
  [f"{K3}::test_깨진_층이_있으면_완전하다고_안_한다"]),
 ("없는 서비스에 아는 것을 안 알려준다", D,
  '                f"없는 서비스 — {service}. 아는 것: "\n                f"{\', \'.join(sorted(self._topology.services)) or \'없음\'}",',
  '                f"없는 서비스 — {service}",',
  [f"{K3}::test_없는_서비스는_아는_것을_알려준다"]),
 ("grep이 어느 커밋인지 안 적는다", D,
  '                chunks.append(f"# {repo} @ {commit[:12]}\\n{got.data.rstrip()}")',
  '                chunks.append(got.data.rstrip())', [f"{K3}::test_grep이_읽은_커밋을_적는다"]),
 ("코드가 없어도 목록에 적는다", B,
  '    if adapter == "code":\n        return bool(services)',
  '    if adapter == "code":\n        return True', [f"{K4}::test_코드가_없으면_목록에_안_나온다"]),
 ("서비스 이름을 목록에 안 적는다", B,
  '        lines.insert(after + 1, f"  (service 자리에 쓸 이름: {\', \'.join(services)})")',
  '        pass',
  [f"{K4}::test_코드가_있으면_서비스_이름까지_적는다"]),
 ("모르는 어댑터를 통과시킨다", B,
  '    field = _INFRA_FIELD.get(adapter)\n    return field is not None and getattr(site_config.infra, field, None) is not None',
  '    return True', [f"{K4}::test_모르는_어댑터는_목록에_안_샌다"]),
 # ── 잘린 층을 대상 탓으로 돌리지 않는다 ─────────────────────────────
 ("config를 400줄에서 자른다", D,
  'got = await self._reader.show(known.repo, commit, path, whole=True)',
  'got = await self._reader.show(known.repo, commit, path)',
  [f"{K3}::test_400줄이_넘는_층도_통째로_읽는다"]),
 ("whole인데도 줄 수로 자른다", GR,
  '    if not whole:\n        lines = text.splitlines()',
  '    if True:\n        lines = text.splitlines()',
  [f"{K3}::test_400줄이_넘는_층도_통째로_읽는다"]),
 ("잘린 층을 그냥 파싱한다", D,
  '            if not got.envelope.complete:\n                # **파싱하기 전에** 본다. 우리가 자른 것을 대상 탓으로 돌리지 않는다.',
  '            if False:\n                # **파싱하기 전에** 본다. 우리가 자른 것을 대상 탓으로 돌리지 않는다.',
  [f"{K3}::test_우리가_자른_것을_대상_탓으로_돌리지_않는다"]),
 ("층이 없는 이유를 안 말한다", D,
  '            why = (" · ".join(broken) if broken\n                   else f"찾은 자리: {\', \'.join(wanted)}. `code status`를 보라")',
  '            why = f"찾은 자리: {\', \'.join(wanted)}. `code status`를 보라"',
  [f"{K3}::test_우리가_자른_것을_대상_탓으로_돌리지_않는다"]),
 # ── 예시가 리드를 코드로 보내는가 (11a 2차 마무리) ─────────────────
 ("발견 예시에 코드를 안 넣는다", B,
  '    return (("code.config", {"service": services[0]}),) + _DISCOVERY',
  '    return _DISCOVERY', [f"{K4}::test_코드가_있으면_발견의_첫_수가_config다"]),
 ("service 자리에 지시문을 넣는다", B,
  '    return (("code.config", {"service": services[0]}),) + _DISCOVERY',
  '    return (("code.config", {"service": "조사할 서비스 이름"}),) + _DISCOVERY',
  [f"{K4}::test_코드가_있으면_발견의_첫_수가_config다"]),
 ("다음 수에 grep을 안 넣는다", B,
  '    return (("code.grep", {"patterns": ["위 증거에서 본 이름"]}),) + _NAMED_READ',
  '    return _NAMED_READ', [f"{K4}::test_코드가_있으면_다음_수가_grep이다"]),
 ("코드가 없어도 예시에 넣는다", B,
  '    if not services:\n        return _DISCOVERY',
  '    if False:\n        return _DISCOVERY', [f"{K4}::test_코드가_없으면_예시에_안_나온다"]),
 ("지식이 없으면 조사가 죽는다", MN,
  '    try:\n        code = _build_code(site, gbm, fct, knowledge_root=knowledge_root, clock=clock)\n    except Exception:                                              # noqa: BLE001\n        return None, ()',
  '    code = _build_code(site, gbm, fct, knowledge_root=knowledge_root, clock=clock)',
  [f"{K5}::test_코드가_없어도_조사가_죽지_않는다"]),
 ("지식이 있어도 비어 있다고 한다", MN,
  '    return code, code.service_names()', '    return None, ()',
  [f"{K5}::test_지식이_있으면_서비스_이름이_나온다"]),
 # ── 우리가 자른 것도 잘린 것이다 ───────────────────────────────────
 ("우리가 자른 것을 완전하다고 적는다", RP,
  '            complete=result.envelope.complete and not ours)',
  '            complete=result.envelope.complete)',
  ["tests/application/test_runner_probe.py::test_예산에서_자르면_완전하다고_안_한다"]),
 ("늘 불완전하다고 우긴다", RP,
  '            complete=result.envelope.complete and not ours)',
  '            complete=False)',
  ["tests/application/test_runner_probe.py::test_안_자르면_완전하다고_한다"]),
 ("무엇이 잘렸는지 안 말한다", ROOT / "src/application/diagnose.py",
  '        *[f"    ✂ {ref.source}" for ref in state.evidence if not ref.complete],',
  '        *[],',
  ["tests/application/test_diagnose.py::test_무엇이_잘렸는지_말한다"]),
 ("_line이 자르고도 안 알린다", RP,
  '    return flat[:limit] + " …(잘림)", True', '    return flat[:limit] + " …(잘림)", False',
  [f"{K4}::test_예산에서_자른_것을_호출부에_알린다"]),
 ("dict에 키 목록을 안 준다", RP,
  '    return [f"키 {len(mapping)}개: {head}"] + rows, cut_head or cut_rows',
  '    return rows, cut_head or cut_rows', [f"{K4}::test_dict는_키_목록이_먼저_나온다"]),
 ("큰 키에서 멈춘다", RP,
  '            skipped += 1\n            continue', '            break',
  [f"{K4}::test_큰_키_하나가_뒤의_키를_가리지_않는다"]),
 # ── 거부를 리드에게 돌려준다 ───────────────────────────────────────
 ("잘린 증거가 또 읽으라고 한다", B,
  '               "같은 질의는 같은 답이다 — 좁혀서 물어라")',
  '               "")',
  [f"{K4}::test_잘린_증거가_또_읽으라고_말하지_않는다"]),
 ("예산에서 빠진 것을 또 읽으라고 한다", B,
  '            lines.append("    (내용은 예산에서 빠졌다 — 같은 질의를 또 내지 마라. "\n                         "필요하면 더 좁혀서 물어라)")',
  '            lines.append("    (내용은 예산에서 빠졌다 — 필요하면 다시 읽어라)")',
  [f"{K4}::test_예산에서_빠진_내용도_또_읽으라고_안_한다"]),
 ("버려진 것을 리드에게 안 돌려준다", B,
  '    return "\\n".join(f"- {_oneline(reason)}" for reason in state.llm_errors)',
  '    return "(없음)"', [f"{K4}::test_버려진_태스크가_리드에게_돌아간다"]),
 ("프롬프트가 거부 자리를 안 쓴다", ROOT / "config/prompts/investigate-integrate.md",
  '<버려진 태스크>\n{rejected}\n</버려진 태스크>\n\n', '',
  [f"{K4}::test_프롬프트가_모든_자리를_실제로_쓴다"]),
 # ── 증거 예산 ─────────────────────────────────────────────────────
 ("개별 상한이 총 예산을 놀린다", SA,
  '    evidence_chars: int = Field(default=2400, ge=200)',
  '    evidence_chars: int = Field(default=1200, ge=200)',
  ["tests/config/test_schema_app.py::test_개별_상한이_총_예산을_놀리지_않는다"]),
 ("예시가 읽을 수 없는 건수를 낸다", B,
  '                               "limit": 3}),',
  '                               "limit": 9}),',
  [f"{K4}::test_예시의_건수가_읽을_수_있는_크기다"]),
 # ── 예시가 중복을 만들지 않는다 ───────────────────────────────────
 ("예시가 이미 한 읽기를 또 보여준다", B,
  '        fresh = tuple(shape for shape in _named_reads(services)\n                      if shape[0] not in used or _refinable(shape[1]))',
  '        fresh = tuple(_named_reads(services))',
  [f"{K4}::test_예시가_이미_한_읽기를_다시_보여주지_않는다"]),
 ("전부 썼을 때 예시가 빈다", B,
  '        shapes = (_available(site_config, fresh, 2, services)\n                  or _available(site_config, _named_reads(services), 2, services))',
  '        shapes = _available(site_config, fresh, 2, services)',
  [f"{K4}::test_전부_써_봤으면_그래도_보여준다"]),
 ("예시가 좁히는 모양을 안 보여준다", B,
  '                               "filter": {"위 증거에서 본 필드 이름": "찾으려는 값"},',
  '                               "filter": {},', [f"{K4}::test_좁히는_모양을_예시가_보여준다"]),
 # ── 인용 형식을 가르친다 ──────────────────────────────────────────
 ("예시가 증거 id의 모양을 안 보여준다", B,
  '                                "supporting_ids": ["위 <모은 증거>에 실제로 있는 id "\n                                                   "— `t-3.e1` 같은 모양"],',
  '                                "supporting_ids": ["위 <모은 증거>에 실제로 있는 id"],',
  [f"{K4}::test_예시가_증거_id의_모양을_보여준다"]),
 ("거부가 맞는 모양을 안 알려준다", ND,
  '                          + ". 증거 id는 `t-3.e1` 모양이다 — 태스크 id가 아니다")',
  '                          )',
  ["tests/application/test_nodes.py::test_없는_증거를_인용하면_맞는_모양을_알려준다"]),
 ("마지막 수단에 지시문을 박는다", B,
  '            entry = _free_rest_entry(site_config)\n            shapes = [entry] if entry else []',
  '            shapes = [("rest.query", {"entry": "등재 목록의 항목 이름", "params": {}})]',
  [f"{K4}::test_마지막_수단도_실재하는_것을_보여준다"]),
 # ── 트레이스 요약 ─────────────────────────────────────────────────
 ("예시를 안 찍는다", TD,
  '    out.append("  예시가 보여준 것 : "',
  '    out.append("  (예시 생략) : "',
  ["tests/application/test_trace_digest.py::test_예시와_리드가_낸_것을_나란히_놓는다"]),
 ("증거에 없는 이름을 안 짚는다", TD,
  '        if ghosts:\n            marks.append(f"증거에 없는 이름 {\', \'.join(ghosts)}")',
  '        if False:\n            marks.append("")',
  ["tests/application/test_trace_digest.py::test_증거에_없는_이름을_표시한다"]),
 ("보이는 반복과 안 보이는 반복을 안 가른다", TD,
  '        if spoken in visible:\n            marks.append("**이미 한 질의 — 증거에 보이는데도 또 냈다**")',
  '        if False:\n            marks.append("")',
  ["tests/application/test_trace_digest.py::test_증거에_보이는_질의를_또_내면_구별한다"]),
 ("증거 내용을 찍는다", TD,
  '    return sum(1 for line in block.splitlines() if line.startswith("- "))',
  '    return block',
  ["tests/application/test_trace_digest.py::test_증거_내용은_안_찍는다"]),
 ("비밀처럼 생긴 인자를 안 가린다", TD,
  '        f"{k}:{\'***\' if _SECRETISH.search(str(k)) else _clip(v)}"',
  '        f"{k}:{_clip(v)}"',
  ["tests/application/test_trace_digest.py::test_비밀처럼_생긴_인자는_가린다",
   "tests/application/test_trace_digest.py::test_키_이름은_안_가린다"]),
 ("못 읽는 응답에 죽는다", TD,
  '    if not parsed.ok:\n        out.append("  리드가 낸 것 : (응답을 JSON으로 못 읽었다 — "',
  '    if not parsed.ok:\n        raise ValueError("못 읽었다")\n        out.append("  리드가 낸 것 : (응답을 JSON으로 못 읽었다 — "',
  ["tests/application/test_trace_digest.py::test_못_읽는_응답에도_안_죽는다"]),
 # ── 첫 전체 트레이스가 드러낸 것 ─────────────────────────────────
 ("좁힐 수 있는 읽기도 한 번 쓰면 예시에서 뺀다", B,
  '                      if shape[0] not in used or _refinable(shape[1]))',
  '                      if shape[0] not in used)',
  [f"{K4}::test_좁힐_수_있는_읽기는_이미_썼어도_보여준다"]),
 ("빈 filter도 좁힐 수 있다고 본다", B,
  '    return bool(params.get("filter"))',
  '    return "filter" in params',
  [f"{K4}::test_좁힐_축이_있는_것만_다시_보여준다"]),
 ("프롬프트 총량만 찍는다", TD,
  '           f" = {_sizes(prompt, evidence)}"',
  '           f""',
  ["tests/application/test_trace_digest.py::test_프롬프트가_어디로_가는지_블록별로_센다"]),
 ("결정을 안 찍는다", TD,
  '        + (f" · decision={body[\'decision\']}" if body.get("decision") else ""))',
  '        )',
  ["tests/application/test_trace_digest.py::test_가설과_결정을_찍는다"]),
 ("재시도를 새 라운드처럼 찍는다", TD,
  '    elif attempt > 1:\n        labels.append(f"재시도 {attempt}회째")',
  '    elif False:\n        pass',
  ["tests/application/test_trace_digest.py::test_같은_라운드가_두_번이면_재시도라고_적는다"]),
 ("못 읽은 응답의 앞머리를 안 보여준다", TD,
  '    return f"시작: {head!r}" if head else "빈 응답"',
  '    return "?"',
  ["tests/application/test_trace_digest.py::test_못_읽은_응답의_앞머리를_보여준다"]), # ── 두 번째 전체 트레이스: 거부 뒤 되묻기 ─────────────────────────
 ("거부가 있어도 되묻지 않는다", ND,
  '        if (refused and deps.redo_on_rejection and not patch.get("stopped_by")',
  '        if (False and deps.redo_on_rejection and not patch.get("stopped_by")',
  ["tests/application/test_nodes.py::test_거부가_있으면_그_자리에서_한_번_되묻는다"]),
 ("찍은 이름으로도 되묻는다", ND,
  '            return fresh, hypotheses, ghosts + rejected, guessed',
  '            return fresh, hypotheses, ghosts + rejected + guessed, []',
  ["tests/application/test_nodes.py::test_찍은_이름은_거부가_아니라_되묻지_않는다"]),
 ("마지막 라운드에도 되묻는다", ND,
  '                and state.round < deps.max_rounds):',
  '                and True):',
  ["tests/application/test_nodes.py::test_마지막_라운드에는_되묻지_않는다"]),
 ("되묻기가 실패하면 라운드가 죽는다", ND,
  '            if second.get("stopped_by"):\n                refused += list(second.get("llm_errors", []))\n            else:',
  '            if False:\n                pass\n            else:',
  ["tests/application/test_nodes.py::test_되묻기가_실패하면_첫_답으로_간다"]),
 ("되물었다는 표시를 안 남긴다", ND,
  '            refused = [c + REDO_NOTE for c in refused]',
  '            refused = list(refused)',
  ["tests/application/test_nodes.py::test_거부가_있으면_그_자리에서_한_번_되묻는다"]),
 ("되묻기 스위치를 무시한다", ND,
  '        if (refused and deps.redo_on_rejection and not patch.get("stopped_by")',
  '        if (refused and True and not patch.get("stopped_by")',
  ["tests/application/test_nodes.py::test_되묻기를_끄면_한_번만_묻는다"]),
 ("대본 경로가 되묻는다", ROOT / "src/application/dryrun.py",
  '                      redo_on_rejection=False)',
  '                      redo_on_rejection=True)',
  ["tests/application/test_dryrun.py::test_대본_경로는_되묻지_않는다"]),
 ("요약이 우리 판정을 안 찍는다", TD,
  '           + (f" · 결과: {verdict}" if verdict else "")]',
  '           ]',
  ["tests/application/test_trace_digest.py::test_우리_판정을_시도마다_찍는다"]),
 ("frame 가설에 물음표를 찍는다", TD,
  '    if node == "frame":',
  '    if False:',
  ["tests/application/test_trace_digest.py::test_frame_가설은_status를_안_찍는다"]),
 ("되물음을 JSON 재시도로 찍는다", TD,
  '    if REDO_MARK in rejected:',
  '    if False:',
  ["tests/application/test_trace_digest.py::test_거부_뒤_되물은_것을_재시도와_가른다"]), ("리드가 되물을 때 첫 프롬프트를 재사용한다", ROOT / "src/application/lead.py",
  '                      briefing.integrate_fields(state, site_config=site_config,',
  '                      briefing.integrate_fields(state.model_copy(update={"llm_errors": []}), site_config=site_config,',
  ["tests/application/test_lead.py::test_거부되면_같은_프롬프트에_사유를_얹어_되묻는다"]),
]
# 건드린 파일의 **원본**을 들고 있는다. 신호로 끊겨도 이걸로 되돌린다.
_ORIGINAL: dict = {}


def _drop_pyc(path: Path) -> None:
    """그 소스의 컴파일 캐시를 지운다 — 같은 길이·같은 초면 무효화가 안 된다."""
    cache = path.parent / "__pycache__"
    for stale in cache.glob(f"{path.stem}.*.pyc") if cache.is_dir() else ():
        stale.unlink(missing_ok=True)


def _restore_all(*_signal) -> None:
    for path, text in _ORIGINAL.items():
        path.write_text(text, encoding="utf-8")
        _drop_pyc(path)
    if _signal:
        print("\n끊겼다 — 건드린 소스를 되돌렸다.")
        sys.exit(130)


atexit.register(_restore_all)
for _sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(_sig, _restore_all)

# **케이스를 붙이다 조용히 놓치는 일**이 실제로 있었다 — 문자열 치환이 안 맞아도
# 파이썬은 아무 말도 안 한다. 수가 줄면 여기서 드러난다.
assert len(CASES) >= 92, f"케이스가 {len(CASES)}개뿐이다 — 붙이려던 것이 안 붙었나"

bad = []
for label, path, old, new, tests in CASES:
    src = path.read_text(encoding="utf-8")
    if old not in src:
        bad.append(f"{label}: 자리 못 찾음"); print(f"???  {label}"); continue
    _ORIGINAL[path] = src
    path.write_text(src.replace(old, new, 1), encoding="utf-8")
    try:
        done = subprocess.run([".venv/bin/python", "-m", "pytest", "-q", *tests],
                              cwd=ROOT, capture_output=True, text=True)
    finally:
        path.write_text(src, encoding="utf-8")
        _drop_pyc(path)
        _ORIGINAL.pop(path, None)
    tail = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else "(출력 없음)"
    ok = done.returncode != 0
    print(f"{'RED ' if ok else '초록!'} {label} — {tail}")
    if not ok: bad.append(f"{label}: 지웠는데 통과")
print()
print(f"총 {len(CASES)}가지 — " + ("모두 RED" if not bad else "문제:\n  " + "\n  ".join(bad)))

# **되돌리기가 실제로 됐는지 확인한다.** 스윕이 소스를 건드리므로, 여기서
# 안 보면 망가진 채로 커밋될 수 있다 — 그건 스윕이 막으려던 것보다 나쁘다.
dirty = subprocess.run(["git", "status", "--porcelain", "src", "tests", "config", "tools"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace").stdout.strip()
if dirty:
    print("\n⚠ 작업 트리가 깨끗하지 않다 — 스윕이 되돌리지 못했거나 원래 수정이 있었다:")
    print("  " + dirty.replace("\n", "\n  "))
sys.exit(1 if bad else 0)
