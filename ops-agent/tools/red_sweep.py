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
IM = ROOT / "src/application/intake_map.py"
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
  'unseen=await self._blind(repo, commit),', 'unseen=[],',
  [f"{G}::test_안_채워진_submodule이면_grep이_조용히_0건을_안_준다"]),
 ("grep이 submodule로 안 들어간다", R,
  '"grep", "-n", "-I", "--no-color", "--recurse-submodules"', '"grep", "-n", "-I", "--no-color"',
  [f"{G}::test_채워진_submodule_안까지_grep한다"]),
 ("ls가 못 본 구석을 안 말한다", R,
  '        reasons += _unseen_reasons(blind) + notes', '        reasons += notes',
  [f"{G}::test_목록도_submodule_안은_못_봤다고_말한다"]),
 ("봉투가 늘 불완전하다고 우긴다", R,
  '    if not blind:\n        return []', '    if not blind:\n        return ["아무튼 못 봤다"]',
  [f"{G}::test_submodule이_없으면_봉투가_멀쩡하다"]),
 ("show가 stale을 안 본다", R,
  '        if sub in blind:\n            return ProbeResult.failed(', '        if False:\n            return ProbeResult.failed(',
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
  '        read = " → ".join(path for path, _, _ in layers)\n        merged = merge_target([(path, value) for path, _, value in layers])',
  '        read = " → ".join(path for path, _, _ in layers)\n        merged = layers[-1][2]',
  [f"{K3}::test_층을_합친_값을_돌려준다"]),
 ("법인 자리를 안 치환한다", D,
  '        wanted = self._topology.resolved_config_paths(self._gbm, self._fct)',
  '        wanted = self._topology.resolved_config_paths(self._gbm, "gumi")',
  [f"{K3}::test_법인이_다르면_다른_값이_나온다"]),
 ("어느 층을 읽었는지 안 남긴다", D,
  '        read = " → ".join(path for path, _, _ in layers)', '        read = ""',
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
  '            head = f"# {repo} @ {commit[:12]}"',
  '            head = f"# {repo}"', [f"{K3}::test_grep이_읽은_커밋을_적는다"]),
 ("코드가 없어도 목록에 적는다", B,
  '    if adapter == "code":\n        return bool(services)',
  '    if adapter == "code":\n        return True', [f"{K4}::test_코드가_없으면_목록에_안_나온다"]),
 ("서비스 이름을 목록에 안 적는다", B,
  '        lines.insert(after + 1, f"  (service 자리에 쓸 이름: {\' / \'.join(named)}"\n                                + (" — 설명 전문은 code.services" if clipped else "") + ")")',
  '        pass',
  [f"{K4}::test_코드가_있으면_서비스_이름까지_적는다"]),
 ("모르는 어댑터를 통과시킨다", B,
  '    field = _INFRA_FIELD.get(adapter)\n    return field is not None and getattr(site_config.infra, field, None) is not None',
  '    return True', [f"{K4}::test_모르는_어댑터는_목록에_안_샌다"]),
 # ── 잘린 층을 대상 탓으로 돌리지 않는다 ─────────────────────────────
 ("config를 400줄에서 자른다", D,
  '깨졌다**는 뜻으로 읽힌다. 사내에서 실제로 그랬다.\n            got = await self._reader.show(known.repo, commit, path, whole=True)',
  '깨졌다**는 뜻으로 읽힌다. 사내에서 실제로 그랬다.\n            got = await self._reader.show(known.repo, commit, path)',
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
  '        unused = tuple(shape for shape in _named_reads(services) if shape[0] not in used)',
  '        unused = tuple(_named_reads(services))',
  [f"{K4}::test_예시가_이미_한_읽기를_다시_보여주지_않는다"]),
 ("전부 썼을 때 예시가 빈다", B,
  '        shapes = (_available(site_config, fresh, 1, services)\n                  or _available(site_config, _named_reads(services), 1, services))',
  '        shapes = _available(site_config, fresh, 1, services)',
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
  '        elif spoken in visible:\n            marks.append("**이미 한 질의 — 증거에 보이는데도 또 냈다**")',
  '        elif False:\n            marks.append("")',
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
  '        again = tuple(shape for shape in _named_reads(services) if shape[0] in used and _refinable(shape[1]))',
  '        again = ()',
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
  ["tests/application/test_lead.py::test_거부되면_같은_프롬프트에_사유를_얹어_되묻는다"]), # ── 로컬 대역 측정을 위한 것들 ────────────────────────────────────
 ("역할을 이름 옆에 안 붙인다", B,
  '        named = [f"{name} — {briefs[name]}" if name in briefs else name for name in services]',
  '        named = list(services)',
  [f"{K4}::test_역할이_있으면_이름_옆에_붙는다"]),
 ("증거 없는 태스크의 질의를 안 보여준다", B,
  '        if task.status != "pending" and not task.result_evidence_ids and task.action:',
  '        if False:',
  [f"{K4}::test_증거를_못_만든_태스크는_질의를_보여준다"]),
 ("트레이스 폴더를 안 비운다", ROOT / "src/__main__.py",
  '    for stale in list(folder.glob("*.md")):\n        stale.unlink()',
  '    for stale in []:\n        pass',
  ["tests/test_cli.py::test_트레이스_폴더는_실행마다_비운다"]),
 ("지난 답 파일을 새 답으로 읽는다", ROOT / "src/infrastructure/llm_fakes.py",
  '            reply.unlink(missing_ok=True)          # 지난 실행의 답을 새 답으로 오인하지 않게',
  '            pass',
  ["tests/infrastructure/test_llm_file_turn.py::test_지난_실행의_답_파일을_새_답으로_읽지_않는다"]),
 ("답이 안 온 이유를 안 남긴다", ROOT / "src/infrastructure/llm_fakes.py",
  '                                    error=f"{reply.name}이 {self._timeout:.0f}초 안에 안 왔다")',
  '                                    error="x")',
  ["tests/infrastructure/test_llm_file_turn.py::test_답이_안_오면_오류로_흡수한다"]),
 ("file 어댑터가 조립 안 된다", ROOT / "src/infrastructure/llm_factory.py",
  '    if cfg.adapter == "file":',
  '    if False:',
  ["tests/infrastructure/test_llm_file_turn.py::test_config로_조립된다"]),
 ("역할을 안 준다", ROOT / "src/infrastructure/deployed_code.py",
  '        return {name: svc.role for name, svc in sorted(self._topology.services.items())\n                if svc.role}',
  '        return {}',
  ["tests/infrastructure/test_deployed_code.py::test_역할은_이름_옆에_붙일_수_있게_따로_준다"]),
 ("리드가 역할을 안 넘긴다", ROOT / "src/application/lead.py",
  'briefing.frame_fields(state, site_config=site_config,\n                                            services=services, roles=roles,',
  'briefing.frame_fields(state, site_config=site_config,\n                                            services=services, roles=None,',
  ["tests/application/test_lead.py::test_역할이_리드_프롬프트까지_간다"]),
 ("file 어댑터에 turn_dir을 안 요구한다", ROOT / "src/config/schema_llm.py",
  '        if self.adapter == "file" and not self.turn_dir:',
  '        if False:',
  ["tests/infrastructure/test_llm_file_turn.py::test_file_어댑터는_turn_dir이_필요하다"]), ("대기 중인 태스크의 갱신을 거부한다", ND,
  '        if task.id in pending and task.id not in used - set(taken):',
  '        if False:',
  ["tests/application/test_nodes.py::test_대기_중인_태스크는_같은_id로_다시_내면_갱신이다"]),
 ("끝난 태스크의 부활까지 연다", ND,
  '        if task.id in pending and task.id not in used - set(taken):',
  '        if task.id in taken:',
  ["tests/application/test_nodes.py::test_끝난_태스크의_id는_여전히_받지_않는다"]),
 ("버려진 답의 질의를 이미 한 질의로 든다", TD,
  '            for query in last_added:\n                asked.pop(query, None)',
  '            pass',
  ["tests/application/test_trace_digest.py::test_되물음_앞의_버려진_답은_이미_한_질의가_아니다"]), ("대기 중인 질의를 새 id로 내면 거부한다", ND,
  '        held = (waiting or {}).get(query)',
  '        held = None',
  ["tests/application/test_nodes.py::test_대기_중인_질의를_새_id로_내면_그_태스크의_갱신이다"]),
 ("되물은 답이 첫 답의 받은 것을 버린다", ND,
  '                fresh = merge_by_id(fresh, more)',
  '                fresh = more',
  ["tests/application/test_nodes.py::test_되물은_답이_첫_답의_것을_빼도_남는다"]),
 ("전송 오류에도 수리 프롬프트를 보낸다", ROOT / "src/application/lead.py",
  '        if attempt and transport:',
  '        if False:',
  ["tests/application/test_lead.py::test_전송_오류는_같은_프롬프트로_다시_묻는다"]),
 ("brief가 아무것도 안 뺀다", TD,
  '    if not brief and node != "conclude":',
  '    if node != "conclude":',
  ["tests/application/test_trace_digest.py::test_brief는_옮길_줄만_남긴다"]),
 # ── 사내 네 번째 트레이스: role·낱개 검증 ─────────────────────────────
 ("role을 리드가 정하게 둔다", ROOT / "src/application/lead.py",
  '    return {**task, "role": role_for(str(task.get("action", "")))}',
  '    return dict(task)',
  ["tests/application/test_lead.py::test_role은_코드가_정한다"]),
 ("틀린 항목을 안 거른다", ROOT / "src/application/lead.py",
  '        if not got.ok:\n            notes.append(f"{label}: {kind} 모양이 틀렸다 — {got.error} — 받지 않는다")\n            continue',
  '        if False:\n            continue',
  ["tests/application/test_lead.py::test_모양이_틀린_태스크만_버리고_나머지는_받는다",
   "tests/application/test_lead.py::test_모양이_틀린_가설만_버린다"]),
 ("예시에 role을 다시 넣는다", B,
  '    return {"id": f"t-{index}", "goal": _GOAL,\n',
  '    return {"id": f"t-{index}", "goal": _GOAL, "role": "data_prober",\n',
  [f"{K4}::test_예시에_role이_없다"]),
 ("못 읽은 시도의 질의를 이미 한 질의로 든다", TD,
  '        elif not verdict.startswith("**못 읽었다**"):',
  '        else:',
  ["tests/application/test_trace_digest.py::test_못_읽은_시도의_질의는_이미_한_질의가_아니다"]),
 # ── 11a를 닫으며 ─────────────────────────────────────────────────
 ("예시 회전에 컨슈머 lag가 없다", B,
  '               ("kafka.group_offsets", {"group": "위 증거에서 본 컨슈머 그룹 이름"}),\n',
  '',
  [f"{K4}::test_예시_회전에_컨슈머_lag_읽기가_있다"]),
 ("라운드 상한 기본값이 돌아간다", ROOT / "src/config/schema_app.py",
  '    max_rounds: int = Field(default=6, ge=1)',
  '    max_rounds: int = Field(default=4, ge=1)',
  ["tests/config/test_schema_app.py::test_라운드_상한_기본값은_6이다"]),
 # ── 11c 흐름 그래프 ────────────────────────────────────────────────
 ("키 토큰을 부모 키 없이 맞춘다", ROOT / "src/knowledge/flow.py",
  '                if confidence == "INFERRED" and any(\n                        tok not in hit.text for tok in name.required_tokens\n                ) and not _quoted_whole(name.key_token, hit.text):\n                    continue',
  '                if False:\n                    continue',
  ["tests/knowledge/test_flow.py::test_키_토큰은_조상_둘을_요구한다",
   "tests/knowledge/test_flow.py::test_키_토큰은_조상_키가_같은_줄에_있어야_한다"]),
 ("동사를 조각이 아니라 통째로 맞춘다", ROOT / "src/knowledge/flow.py",
  '        for chunk in word.split("_"):',
  '        for chunk in [word]:',
  ["tests/knowledge/test_flow.py::test_동사로_방향을_정한다",
   "tests/knowledge/test_flow.py::test_코드가_공유_레포의_서비스를_가른다"]),
 ("동사 없는 줄을 버린다", ROOT / "src/knowledge/flow.py",
  '                relation = (RELATION[name.kind][verb] if verb in ("reads", "writes")\n                            else "mentions")',
  '                if verb not in ("reads", "writes"):\n                    continue\n                relation = RELATION[name.kind][verb]',
  ["tests/knowledge/test_flow.py::test_동사가_없는_줄은_mentions로_남긴다"]),
 ("경로가 흐름 방향을 무시한다", ROOT / "src/knowledge/flow.py",
  '    if undirected or edge["relation"] == _BRIDGE:\n        if node == edge["source"]:',
  '    if True:\n        if node == edge["source"]:',
  ["tests/knowledge/test_flow.py::test_processor에서_sink까지_경로는_토픽을_지난다",
   "tests/knowledge/test_flow.py::test_흐름이_없으면_경로도_없다"]),
 ("모르는 자원 종류를 받는다", ROOT / "src/knowledge/schema.py",
  '    kind: Literal["topic", "group", "collection", "rediskey"]',
  '    kind: str',
  ["tests/knowledge/test_flow.py::test_모르는_자원_종류나_관계는_거부한다"]),
 ("디렉터리 이름 귀속을 안 한다", ROOT / "src/knowledge/flow.py",
  '    head = file.split("/", 1)[0]\n    if head in candidates:\n        return head, "INFERRED"',
  '    pass',
  ["tests/knowledge/test_flow.py::test_파일을_서비스에_붙인다"]),
 ("관계 있는 출처를 선언으로만 적는다", ROOT / "src/knowledge/flow.py",
  '                                  "relation": name.relation or "declares",',
  '                                  "relation": "declares",',
  ["tests/knowledge/test_flow.py::test_관계_있는_출처는_config_선언이_곧_엣지다"]),
 ("키 토큰이 부모 하나만 요구한다", ROOT / "src/knowledge/flow.py",
  '        return tuple(parts[-3:-1])',
  '        return tuple(parts[-2:-1])',
  ["tests/knowledge/test_flow.py::test_키_토큰은_조상_둘을_요구한다",
   "tests/knowledge/test_flow.py::test_코드가_공유_레포의_서비스를_가른다"]),
 ("공유 레포의 config를 첫 서비스 것으로 친다", ROOT / "src/knowledge/flow.py",
  '    return mine[0] if len(mine) == 1 else None',
  '    return mine[0] if mine else None',
  ["tests/knowledge/test_flow.py::test_config는_레포당_하나라_공유_레포면_주인이_없다"]),
 ("git grep의 커밋 접두를 안 벗긴다", ROOT / "src/knowledge/graph_build.py",
  '        if raw.startswith(prefix):\n            raw = raw[len(prefix):]',
  '        pass',
  ["tests/knowledge/test_graph_build.py::test_git_grep_출력을_읽는다"]),
 ("커밋이 달라도 낡았다고 안 한다", ROOT / "src/knowledge/graph_build.py",
  '        elif have != sha:',
  '        elif False:',
  ["tests/knowledge/test_graph_build.py::test_번들을_쓰고_읽고_커밋을_대조한다",
   "tests/knowledge/test_cli_code.py::test_code_status가_낡은_그래프를_말한다"]),
 ("객체 안의 이름을 못 뽑는다", ROOT / "src/knowledge/flow.py",
  '                if isinstance(value, dict):\n                    value = value.get(field)',
  '                pass',
  ["tests/knowledge/test_flow.py::test_이름이_객체_안에_있어도_뽑는다"]),
 ("객체의 필드를 못 바꾼다", ROOT / "src/knowledge/flow.py",
  '        field = src.field or FLOW_FIELDS.get(src.kind, "")',
  '        field = FLOW_FIELDS.get(src.kind, "")',
  ["tests/knowledge/test_flow.py::test_객체의_필드는_종류별_기본이고_바꿀_수_있다"]),
 ("없는 config 경로에서 죽는다", ROOT / "src/knowledge/flow.py",
  '            node = node.get(key) if isinstance(node, dict) else None',
  '            node = node[key]',
  ["tests/knowledge/test_flow.py::test_kafka가_없는_서비스도_정상이다"]),
 ("옆 줄의 동사를 안 본다", ROOT / "src/knowledge/flow.py",
  '                verb = verb or direction(hit.context)',
  '                verb = verb',
  ["tests/knowledge/test_flow.py::test_옆_줄의_동사는_쓰되_INFERRED다"]),
 ("옆 줄의 동사도 EXTRACTED로 친다", ROOT / "src/knowledge/flow.py",
  '                sure_verb = "EXTRACTED" if verb else "INFERRED"',
  '                sure_verb = "EXTRACTED"',
  ["tests/knowledge/test_flow.py::test_옆_줄의_동사는_쓰되_INFERRED다"]),
 ("문맥 줄을 히트에 안 붙인다", ROOT / "src/knowledge/graph_build.py",
  '        around = [lines[(f, k)] for k in (n - 1, n + 1) if (f, k) in lines]',
  '        around = []',
  ["tests/knowledge/test_graph_build.py::test_문맥은_줄_번호로_앞뒤_한_줄만_붙는다"]),
 ("문맥 줄의 파일을 안 가른다", ROOT / "src/knowledge/graph_build.py",
  '        around = [lines[(f, k)] for k in (n - 1, n + 1) if (f, k) in lines]',
  '        around = [v for (_, k), v in lines.items() if k in (n - 1, n + 1)]',
  ["tests/knowledge/test_graph_build.py::test_문맥_없이_이웃한_줄_번호는_파일이_다르면_안_섞인다"]),
 ("흐름 추출이 문맥을 안 청한다(git grep 길)", ROOT / "src/infrastructure/deployed_code.py",
  '                got = await self._reader.grep(repo, commit, chunk, context=1, fixed=True,',
  '                got = await self._reader.grep(repo, commit, chunk, context=0, fixed=True,',
  ["tests/infrastructure/test_deployed_code.py::test_흐름_히트는_스냅샷에서_찾고_git_grep은_안_띄운다"]),
 ("흐름 추출이 문맥을 안 청한다(스냅샷 길)", ROOT / "src/infrastructure/deployed_code.py",
  '                for hit in grep_snapshot(repo, commit, snap, patterns, fixed=True, context=1):',
  '                for hit in grep_snapshot(repo, commit, snap, patterns, fixed=True, context=0):',
  ["tests/infrastructure/test_deployed_code.py::test_흐름_히트에는_앞뒤_한_줄이_실려_온다"]),
 ("흐름 추출이 패턴마다 git을 띄운다", ROOT / "src/infrastructure/deployed_code.py",
  'FLOW_CHUNK = 20', 'FLOW_CHUNK = 1',
  ["tests/infrastructure/test_deployed_code.py::test_흐름_히트는_스냅샷에서_찾고_git_grep은_안_띄운다"]),
 ("히트를 패턴별로 안 나눈다", ROOT / "src/infrastructure/deployed_code.py",
  '                        if p in hit.text:', '                        if True:',
  ["tests/infrastructure/test_deployed_code.py::test_흐름_히트는_스냅샷에서_찾고_git_grep은_안_띄운다"]),
 ("잘린 코드 찾기를 조용히 버린다", ROOT / "src/infrastructure/deployed_code.py",
  '                if not got.envelope.complete:', '                if False:',
  ["tests/infrastructure/test_deployed_code.py::test_잘린_코드_찾기는_버리지_않고_사유로_남는다"]),
 ("-F가 안 붙는다", ROOT / "src/infrastructure/git_reader.py",
  '        if fixed:\n            args.append("-F")', '        if False:\n            pass',
  ["tests/infrastructure/test_git_reader.py::test_고정_문자열이면_점이_점이다"]),
 ("흐름용 상한이 안 먹는다", ROOT / "src/infrastructure/git_reader.py",
  '                     max_lines=max_lines, max_chars=max_chars)', '                     )',
  ["tests/infrastructure/test_git_reader.py::test_흐름용_상한은_호출부가_따로_준다"]),
 ("python 옆의 graphify를 안 본다", ROOT / "src/knowledge/graph_build.py",
  '    if beside.exists():\n        return str(beside)', '    if False:\n        pass',
  ["tests/knowledge/test_graph_build.py::test_python_옆의_graphify를_PATH보다_먼저_본다"]),
 ("worktree 경로를 절대 경로로 안 바꾼다", ROOT / "src/knowledge/graph_build.py",
  '    repo_dir, scratch = Path(os.path.abspath(repo_dir)), Path(os.path.abspath(scratch))',
  '    pass',
  ["tests/knowledge/test_graph_build.py::test_worktree_자리는_상대_경로여도_레포_밖에_생긴다"]),
 ("config 엣지를 서비스별로 안 만든다", ROOT / "src/knowledge/flow.py",
  '        if name.services:\n            _declare_per_service(name, target, topology, hits_for, commits, put_node, links)',
  '        if False:\n            pass',
  ["tests/knowledge/test_flow.py::test_config_엣지는_서비스마다_그_서비스의_합친_config에서_만든다"]),
 ("서비스→레포 다리가 없다", ROOT / "src/knowledge/flow.py",
  '                      "relation": "runs", "confidence": "EXTRACTED", "attributed": "service",',
  '                      "relation": "member", "confidence": "EXTRACTED", "attributed": "service",',
  ["tests/knowledge/test_flow.py::test_같은_코드를_띄우는_서비스는_레포를_거쳐_경로가_난다"]),
 ("다리를 자원 경로보다 먼저 탄다", ROOT / "src/knowledge/flow.py",
  '    for bridge in (False, True):', '    for bridge in (True, False):',
  ["tests/knowledge/test_flow.py::test_processor에서_sink까지_경로는_토픽을_지난다"]),
 ("문서·테스트·주석을 코드 엣지로 센다", ROOT / "src/knowledge/flow.py",
  '                if not _is_config(hit.file) and _is_noise(hit.file, hit.text):\n                    continue',
  '                if False:\n                    continue',
  ["tests/knowledge/test_flow.py::test_문서_테스트_주석_줄은_코드_엣지가_아니다"]),
 ("따옴표 통째 키를 안 받는다", ROOT / "src/knowledge/flow.py",
  '                ) and not _quoted_whole(name.key_token, hit.text):',
  '                ) and True:',
  ["tests/knowledge/test_flow.py::test_따옴표로_통째_적힌_config_키는_조상_없이도_잡는다"]),
 ("공유 레포를 권고에서 안 말한다", ROOT / "src/knowledge/flow.py",
  '    for repo, n in sorted(shared_code.items()):', '    for repo, n in []:',
  ["tests/knowledge/test_flow.py::test_같은_코드를_띄우는_서비스는_레포를_거쳐_경로가_난다"]),
 ("코드에서 못 찾은 이름을 안 센다", ROOT / "src/knowledge/flow.py",
  '            "unreferenced": len(resources - coded),', '            "unreferenced": 0,',
  ["tests/knowledge/test_flow.py::test_요약과_권고"]),
 ("근거 줄이 합친 층보다 레포 grep을 먼저 집는다", ROOT / "src/knowledge/flow.py",
  '        if svc_name in own:', '        if False:',
  ["tests/knowledge/test_flow.py::test_config_엣지의_근거는_그_서비스가_실제로_합친_층이다"]),
 ("자원 노드에 종류를 안 붙인다", ROOT / "src/knowledge/flow.py",
  '    return f"{node[\'label\']} [{node[\'type\']}]"', '    return node["label"]',
  ["tests/knowledge/test_cli_code.py::test_code_flow가_흐름_경로를_보여준다"]),
 ("CLI가 콘솔을 UTF-8로 안 바꾼다", ROOT / "src/__main__.py",
  '    _utf8_console()\n    args = parse_args(argv)', '    args = parse_args(argv)',
  ["tests/test_cli.py::test_콘솔이_cp949여도_한글과_대시가_안_죽는다"]),
 ("데이터 속 닫는 태그를 안 피한다", ROOT / "src/presentation/flow_html.py",
  '    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\\\/")',
  '    blob = json.dumps(data, ensure_ascii=False)',
  ["tests/presentation/test_flow_html.py::test_데이터_속_닫는_태그와_제목이_문서를_못_끊는다"]),
 ("아홉 번째 레포에 색을 돌려 쓴다", ROOT / "src/presentation/flow_html.py",
  '    colors = {r: (REPO_COLORS[i] if i < len(REPO_COLORS) else OTHER_COLOR)',
  '    colors = {r: (REPO_COLORS[i % len(REPO_COLORS)])',
  ["tests/presentation/test_flow_html.py::test_아홉_번째_레포부터는_회색이다"]),
 ("flow.html을 안 쓴다", ROOT / "src/__main__.py",
  '    (out_dir / "flow.html").write_text(', '    (out_dir / "flow.txt").write_text(',
  ["tests/knowledge/test_cli_code.py::test_code_graph가_배포_커밋에_그래프를_박는다"]),
 ("GRAPH_REPORT를 worktree와 함께 버린다", ROOT / "src/knowledge/graph_build.py",
  '        report = _read_optional(path.parent / "GRAPH_REPORT.md")', '        report = None',
  ["tests/knowledge/test_graph_build.py::test_레포별_GRAPH_REPORT를_worktree와_함께_안_버린다"]),
 ("wiki 대신 다른 것을 내보낸다", ROOT / "src/knowledge/graph_build.py",
  '        done = subprocess.run([binary, "export", "wiki", "--graph", str(graph_json)],',
  '        done = subprocess.run([binary, "export", "graphml", "--graph", str(graph_json)],',
  ["tests/knowledge/test_graph_build.py::test_wiki는_graph_json_옆에_생기고_없으면_건너뛴다"]),
 ("흐름 텍스트에 코드 층이 섞인다", ROOT / "src/knowledge/flow.py",
  '             if (e.get("origin") in ("config", "trace") or e["relation"] == "serves")',
  '             if (e.get("origin") != "runs" or e["relation"] == "serves")',
  ["tests/knowledge/test_flow.py::test_흐름_텍스트는_config_층만_씨앗의_이웃_그리고_닿은_서비스의_토픽"]),
 ("흐름 텍스트가 예산을 안 지킨다", ROOT / "src/knowledge/flow.py",
  '        if used + len(line) + 1 > budget and (out or pinned):', '        if False:',
  ["tests/knowledge/test_flow.py::test_흐름_텍스트는_예산에서_끊고_끊었다고_적는다"]),
 ("그래프가 없어도 code.flow를 목록에 둔다", ROOT / "src/application/briefing.py",
  '    if flow_graph is None:\n        out |= {"code.flow", "code.trace"}',
  '    if False:\n        out |= {"code.flow", "code.trace"}',
  ["tests/application/test_briefing.py::test_그래프가_없으면_없다고_적고_code_flow를_목록에서_뺀다"]),
 ("증거의 이름을 씨앗으로 안 쓴다", ROOT / "src/application/briefing.py",
  '    seeds_from += [f"{ref.summary}\\n{ref.body}" for ref in state.evidence]', '    seeds_from += []',
  ["tests/application/test_briefing.py::test_흐름_블록은_증상과_증거의_이름을_씨앗으로_config_층만_싣는다"]),
 ("낡은 그래프를 조사에 싣는다", ROOT / "src/__main__.py",
  '    if stale:\n        return None, "그래프 낡음 — 브리핑에 안 싣는다: " + " · ".join(stale)',
  '    if False:\n        pass',
  ["tests/knowledge/test_cli_code.py::test_조사에는_배포_커밋과_같은_그래프만_실린다"]),
 ("graphify 단계를 안 알린다", ROOT / "src/knowledge/graph_build.py",
  '        if progress:\n            progress(f"graphify {args[0]} 중 (최대 {GRAPHIFY_TIMEOUT_S // 60}분)")',
  '        if False:\n            pass',
  ["tests/knowledge/test_graph_build.py::test_graphify_단계를_알린다"]),
 ("-C 옵션이 안 붙는다", ROOT / "src/infrastructure/git_reader.py",
  '        if context > 0:\n            args.append(f"-C{context}")',
  '        if False:\n            pass',
  ["tests/infrastructure/test_git_reader.py::test_문맥을_청하면_앞뒤_줄이_대시로_온다"]),
 ("대기 태스크의 갱신을 반복으로 찍는다", TD,
  '        if task_id in waiting:\n            marks.append("대기 중이던 태스크의 갱신")\n        elif spoken in visible:',
  '        if spoken in visible:',
  ["tests/application/test_trace_digest.py::test_대기_중이던_태스크를_같은_id로_다시_내면_갱신이라고_적는다"]),
 ("스텁이 파티션을 지어낸다", ROOT / "src/infrastructure/stubs.py",
  '            rows, total = [], lag',
  '            rows, total = [{"topic": "stub", "partition": 0, "committed": 0, "end": lag, "lag": lag}], lag',
  ["tests/infrastructure/test_stubs.py::test_group_offsets는_총량만_알면_파티션을_지어내지_않는다"]),
 ("브리핑이 준 이름을 찍은 것으로 센다", ND,
  '    return _seen(state) + "\\n" + deps.known_names', '    return _seen(state)',
  ["tests/application/test_nodes.py::test_브리핑이_준_이름은_찍은_것이_아니다"]),
 ("요약이 흐름 블록을 안 본다", TD,
  '    seen_names = evidence + "\\n" + _block(prompt, "데이터 흐름")', '    seen_names = evidence',
  ["tests/application/test_trace_digest.py::test_데이터_흐름_블록의_이름은_증거에_없는_이름이_아니다"]),
 ("known_names가 빈다", ROOT / "src/knowledge/flow.py",
  '    return "\\n".join(sorted({n["label"] for n in graph.get("nodes", []) if n.get("label")}))',
  '    return ""',
  ["tests/knowledge/test_flow.py::test_known_names는_그래프의_이름_전부이고_없으면_빈다"]),
 ("접수 경로가 점검을 못 찾는다", IM,
  '    return checks[case.check]', '    return None',
  ["tests/application/test_briefing.py::test_접수_경로가_config에서_출발점을_되짚는다"]),
 ("frame 예시가 출발 읽기를 뺀다", B,
  '            shapes.append(first_read)', '            pass',
  ["tests/application/test_briefing.py::test_frame_예시는_출발_읽기로_시작한다"]),
 ("관계당 이름 상한이 없다", ROOT / "src/knowledge/flow.py",
  '_MAX_NAMES = 8', '_MAX_NAMES = 80',
  ["tests/knowledge/test_flow.py::test_관계당_여덟_개까지만_적고_나머지는_센다"]),
 ("2단계 서비스를 안 접는다", ROOT / "src/knowledge/flow.py",
  '            if len(ids) > 1:\n                out.append(f"{repo}', '            if False:\n                out.append(f"{repo}',
  ["tests/knowledge/test_flow.py::test_2단계_서비스도_같은_config면_접는다"]),
 ("코드 층 방향을 안 보탠다", ROOT / "src/knowledge/flow.py",
  '        tail = code_direction(node_id, directions) if (with_code or ambiguous) else ""', '        tail = ""',
  ["tests/knowledge/test_flow.py::test_config가_못_가른_방향은_코드_층_한_줄로_보탠다"]),
 ("씨앗을 부분 문자열로 맞춘다", ROOT / "src/knowledge/flow.py",
  '             and _as_token(n["label"], blob)]', '             and n["label"] in blob]',
  ["tests/knowledge/test_flow.py::test_씨앗은_토큰_단위로_맞추고_자원은_셋까지다"]),
 ("템플릿 이름을 채운 값을 찍은 것으로 센다", ROOT / "src/domain/actions.py",
  '    return any(value.startswith(head) for head in _TEMPLATE_HEAD.findall(seen))', '    return False',
  ["tests/application/test_nodes.py::test_템플릿_이름을_채운_값은_찍은_것이_아니다",
   "tests/application/test_trace_digest.py::test_템플릿_이름을_채운_값은_증거에_없는_이름이_아니다"]),
 ("스텁이 모르는 그룹에 lag 0을 준다", ROOT / "src/infrastructure/stubs.py",
  '        if lag is None:\n            # 모르는 그룹에', '        if False:\n            # 모르는 그룹에',
  ["tests/infrastructure/test_stubs.py::test_group_offsets는_모르는_그룹에_lag_0을_지어내지_않는다"]),
 ("include_router의 prefix를 안 붙인다", ROOT / "src/knowledge/flow.py",
  '                prefix = _join_path(outer[0], prefix)', '                prefix = prefix',
  ["tests/knowledge/test_flow.py::test_라우트_줄에서_끝점을_조립한다"]),
 ("못 이은 prefix를 EXTRACTED로 속인다", ROOT / "src/knowledge/flow.py",
  '                confidence = "INFERRED"', '                confidence = "EXTRACTED"',
  ["tests/knowledge/test_flow.py::test_라우트_줄에서_끝점을_조립한다"]),
 ("라우트에서 테스트 파일을 안 뺀다", ROOT / "src/knowledge/flow.py",
  '        if _is_noise(hit.file, hit.text):\n            continue\n        m = _ROUTER_DEF.search(hit.text)',
  '        if False:\n            continue\n        m = _ROUTER_DEF.search(hit.text)',
  ["tests/knowledge/test_flow.py::test_라우트_줄에서_끝점을_조립한다",
   "tests/infrastructure/test_deployed_code.py::test_라우트_히트는_배포_커밋의_git_grep이고_테스트_파일은_뺀다"]),
 ("등재 path의 꼬리로 안 잇는다", ROOT / "src/knowledge/flow.py",
  '            if len(tails) == 1:\n                target = tails[0]', '            if False:\n                target = tails[0]',
  ["tests/knowledge/test_flow.py::test_등재_path와_코드_끝점을_잇는다"]),
 ("서빙 미상을 안 센다", ROOT / "src/knowledge/flow.py",
  '            "endpoints_unserved": sum(1 for n in endpoints if n["id"] not in served),',
  '            "endpoints_unserved": 0,',
  ["tests/knowledge/test_flow.py::test_등재_path와_코드_끝점을_잇는다"]),
 ("블록이 serves를 뺀다", ROOT / "src/knowledge/flow.py",
  '             if (e.get("origin") in ("config", "trace") or e["relation"] == "serves")',
  '             if (e.get("origin") in ("config", "trace"))',
  ["tests/knowledge/test_flow.py::test_끝점_씨앗은_serves_줄이_맨_앞이고_서비스_다음_순위다"]),
 ("블록이 접수 경로를 씨앗으로 안 쓴다", B,
  '    seeds_from = [state.case.symptom, *texts]', '    seeds_from = [state.case.symptom]',
  ["tests/application/test_briefing.py::test_흐름_블록은_접수_경로의_path를_씨앗으로_쓴다"]),
 ("예시의 code.grep이 서빙 서비스를 안 채운다", B,
  '                                             **({"service": served[0]} if len(served) == 1 else {})}))',
  '                                             }))',
  ["tests/application/test_briefing.py::test_frame_예시의_code_grep은_서빙_서비스를_안다"]),
 ("추정 읽기를 EXTRACTED로 싣는다", ROOT / "src/knowledge/flow.py",
  '                      "confidence": "EXTRACTED" if r.grade == "확실" else "INFERRED", "grade": r.grade,',
  '                      "confidence": "EXTRACTED", "grade": r.grade,',
  ["tests/knowledge/test_flow.py::test_add_trace는_읽기_엣지를_등급과_함께_싣고_노드에_사슬을_남긴다"]),
 ("블록이 확실·추정 읽기를 안 가른다", ROOT / "src/knowledge/flow.py",
  '                    rel = "reads" + _READ_MARK[read_mark(e)]', '                    rel = "reads"',
  ["tests/knowledge/test_flow.py::test_끝점_줄에_reads가_붙고_2단계로_그_자원과_쓰는_서비스가_온다"]),
 ("끝점이 읽는 자원 줄을 안 낸다", ROOT / "src/knowledge/flow.py",
  '            lines.append(line_for(rid, with_code=True))', '            pass',
  ["tests/knowledge/test_flow.py::test_끝점_줄에_reads가_붙고_2단계로_그_자원과_쓰는_서비스가_온다"]),
 ("지도가 추적 엣지를 데이터에서 뺀다", ROOT / "src/presentation/flow_html.py",
  '                  for e in overlay["links"] if e["relation"] != "runs"],', '                  for e in overlay["links"] if e["relation"] != "runs" and e.get("origin") != "trace"],',
  ["tests/presentation/test_flow_html.py::test_추적_엣지는_데이터에_싣되_기본은_숨기고_초점에서만_켠다"]),
 ("상류 2홉(자원을 쓰는 서비스)을 안 잇는다", ROOT / "src/presentation/flow_html.py",
  '_UP_WRITERS = ("writes", "produces")          # 자원 → 그 자원을 만드는 서비스', '_UP_WRITERS = ()          # 자원 → 그 자원을 만드는 서비스',
  ["tests/presentation/test_flow_html.py::test_끝점의_상류_3홉을_계산해_싣는다"]),
 ("초점에서도 추적 엣지를 안 켠다", ROOT / "src/presentation/flow_html.py",
  '.edge.trace{display:none}\nsvg.focused .edge.trace.on{display:inline}', '.edge.trace{display:none}',
  ["tests/presentation/test_flow_html.py::test_추적_엣지는_데이터에_싣되_기본은_숨기고_초점에서만_켠다"]), ('따옴표 밖의 리터럴도 읽기로 친다', ROOT / "src/knowledge/trace.py",
  '    m = re.search(r"""["\'][^"\'\\n]*""" + re.escape(needle), segment)', '    m = re.search(re.escape(needle), segment)',
  ['tests/knowledge/test_reads.py::test_템플릿_리터럴은_따옴표_안에_있어야_읽기다']),
 ('읽기의 출처를 안 적는다', ROOT / "src/knowledge/trace.py",
  '            add_read(n.kind, n.value, "추정", path, _line_of(segment, ident, base), "alias")',
  '            add_read(n.kind, n.value, "추정", path, _line_of(segment, ident, base), "literal")',
  ['tests/knowledge/test_reads.py::test_별칭_경유와_추정_길은_출처와_등급으로_남는다']),
 ('한 단어 config 키를 조상 키 없이도 읽기로 친다', ROOT / "src/knowledge/trace.py",
  '        if all(t in line for t in n.required_tokens) or _quoted_whole(tok, line):', '        if True:',
  ['tests/knowledge/test_reads.py::test_한_단어_config_키는_조상_키가_같은_줄에_있어야_읽기다']),
 ('사슬을 안 쳐내고 통째로 보여 준다', ROOT / "src/knowledge/flow.py",
  '    if not keep:\n        keep = set(range(min(_TRACE_HEAD, len(chain))))', '    if True:\n        keep = set(range(len(chain)))',
  ['tests/knowledge/test_flow.py::test_trace_lines는_읽기로_이어진_걸음만_남기고_표시_셋으로_적는다']),
 ('config 키 경유 읽기를 추정과 안 가른다', ROOT / "src/knowledge/flow.py",
  '    return "config키" if edge.get("via") == "key" else "추정"', '    return "추정"',
  ['tests/knowledge/test_flow.py::test_끝점_줄에_reads가_붙고_2단계로_그_자원과_쓰는_서비스가_온다', 'tests/knowledge/test_flow.py::test_trace_lines는_읽기로_이어진_걸음만_남기고_표시_셋으로_적는다']),
 ('추적 안 된 끝점을 code.trace가 실패로 안 답한다', ROOT / "src/infrastructure/deployed_code.py",
  '        if lines is None:\n            return ProbeResult.failed(\n                f"{endpoint}: 추적이 안 된 끝점이다', '        if False:\n            return ProbeResult.failed(\n                f"{endpoint}: 추적이 안 된 끝점이다',
  ['tests/infrastructure/test_deployed_code.py::test_code_trace는_붙인_그래프의_끝점_사슬을_주고_없으면_실패로_답한다']),
 ('code.* 레인이 data_prober다', ROOT / "src/domain/actions.py",
  '    if action.startswith("code."):\n        return "code_tracer"', '    if False:\n        return "code_tracer"',
  ['tests/domain/test_actions.py::test_레인은_action에서_정해진다']),
 ('그래프 없는 조사에 code.trace가 목록에 뜬다', ROOT / "src/application/briefing.py",
  '        out |= {"code.flow", "code.trace"}', '        out |= {"code.flow"}',
  ['tests/application/test_briefing.py::test_그래프가_없으면_없다고_적고_code_flow를_목록에서_뺀다']),
 ("code trace가 없는 끝점을 없다고 안 한다", ROOT / "src/__main__.py",
  '    if not any(n["id"] == node_id for n in graph["nodes"]):\n        print(f"  {path}: 그래프의 끝점에 없다', '    if False:\n        print(f"  {path}: 그래프의 끝점에 없다',
  ["tests/knowledge/test_cli_code.py::test_code_graph가_끝점을_싣고_flow와_status가_말한다"]),
 ('접두사 leaf를 키 자원으로 만든다', ROOT / "src/knowledge/flow.py",
  '                if src.prefix and key == src.prefix:\n                    continue', '                if False:\n                    continue',
  ['tests/knowledge/test_flow.py::test_접두사_leaf가_있으면_키는_접두사를_붙인_이름이고_코드에는_원래_값으로_찾는다']),
 ('키에 접두사를 안 붙인다', ROOT / "src/knowledge/flow.py",
  '                    if pre:\n                        found.append(Name(src.kind, f"{pre}{src.join}{value}"', '                    if False:\n                        found.append(Name(src.kind, f"{pre}{src.join}{value}"',
  ['tests/knowledge/test_flow.py::test_접두사_leaf가_있으면_키는_접두사를_붙인_이름이고_코드에는_원래_값으로_찾는다']),
 ('grep 판정이 한 단어 리터럴을 동사 없이 받는다', ROOT / "src/knowledge/flow.py",
  '                        and not _DISTINCTIVE.search(pattern) and direction(hit.text) is None):', '                        and False):',
  ['tests/knowledge/test_flow.py::test_한_단어_리터럴은_같은_줄에_읽기쓰기_동사가_있어야_잡는다']),
 ('추적기가 한 단어 리터럴을 동사 없이 읽기로 친다', ROOT / "src/knowledge/trace.py",
  '            at = None       # 한 단어 리터럴은 같은 줄에 읽기/쓰기 동사가 있어야 읽기다 — 배지 상태값 "alarm"', '            pass',
  ['tests/knowledge/test_reads.py::test_한_단어_리터럴은_같은_줄에_읽기쓰기_동사가_있어야_읽기다']),
 ("코드 쪽 키 템플릿을 안 본다", ROOT / "src/knowledge/trace.py",
  '        for head in _KEY_TEMPLATE.findall(line):', '        for head in ():',
  ['tests/knowledge/test_reads.py::test_코드가_키_토큰을_템플릿으로_조립하면_그_머리로_시작하는_키_전부가_읽기다']),
 ('소독이 expect의 증거를 입력 증거로 안 넣는다', ROOT / "src/application/nodes.py",
  '    if isinstance(expect, dict) and isinstance(expect.get("evidence"), str) and expect["evidence"] not in ids:', '    if False:',
  ['tests/application/test_nodes.py::test_소독은_expect의_증거를_입력_증거로_강제한다', 'tests/application/test_nodes.py::test_expect가_가리키는_증거가_생기기_전에는_안_돈다']),
 ('실행기가 원본을 안 보관한다', ROOT / "src/application/runner_probe.py",
  '        self._raw[ref.id] = result.data', '        pass',
  ['tests/application/test_runner_probe.py::test_실행기는_자기가_만든_원본을_보관하고_recompute가_그것으로_대조한다']),
 ('recompute가 표의 인자 검사를 건너뛴다', ROOT / "src/application/runner_probe.py",
  '            if problem is not None:\n                return TaskOutcome(task_id=task.id, status="error", error=f"{source} — {problem}")', '            if False:\n                return TaskOutcome(task_id=task.id, status="error", error=f"{source} — {problem}")',
  ['tests/application/test_runner_probe.py::test_recompute도_등재_검사를_먼저_받고_mongo가_없으면_거부된다']),
 ('숫자가 아닌 기대값을 대조한다', ROOT / "src/application/recompute.py",
  '        if isinstance(value, bool) or not isinstance(value, (int, float)):', '        if False:',
  ['tests/application/test_recompute.py::test_기대값을_못_찾으면_불일치가_아니라_error다']),
 ('잘린 표본으로 합계를 낸다', ROOT / "src/application/recompute.py",
  '        if not got.envelope.complete:', '        if False:',
  ['tests/application/test_recompute.py::test_표본이_잘리면_합계를_안_낸다']),
 ('mongo 없는 사이트에도 recompute가 목록에 뜬다', ROOT / "src/application/briefing.py",
  '"rest": "rest", "recompute": "mongodb"}', '"rest": "rest", "recompute": "redis"}',
  ['tests/application/test_briefing.py::test_원천_재집계는_mongo가_있는_사이트에서만_목록에_있다']),
 # ── 11b 3b-2: 브리핑 예시의 사다리 칸 · 대본의 코드 자리 ──
 ('추적 안 된 끝점에도 code.trace를 예시로 보여 준다', ROOT / "src/application/briefing.py",
  'or flowgraph.trace_lines(flow_graph, flowgraph.endpoint_id(path)) is None):', 'or False):',
  ['tests/application/test_briefing.py::test_추적_안_된_끝점_그래프_없음_코드_없음이면_code_trace_예시가_없다']),
 ('이미 낸 recompute·mongo 없는 사이트에도 recompute 예시가 뜬다', ROOT / "src/application/briefing.py",
  'if "recompute.count" in used or not _has(site_config, "recompute", services):', 'if False:',
  ['tests/application/test_briefing.py::test_recompute_예시는_이미_냈거나_mongo가_없으면_안_나온다']),
 ('code.trace 예시의 입력 증거가 비어 있다', ROOT / "src/application/briefing.py",
  'return ("code.trace", {"endpoint": path}), [rest_id]', 'return ("code.trace", {"endpoint": path}), []',
  ['tests/application/test_briefing.py::test_rest_증거가_있고_끝점이_추적됐으면_integrate_예시_첫_수가_그_path의_code_trace다']),
 ('recompute 예시의 컬렉션이 추적 읽기에서 안 온다', ROOT / "src/application/briefing.py",
  '    collections = flowgraph.traced_reads(flow_graph, ep, kind="collection")',
  '    collections = flowgraph.traced_reads(flow_graph, ep, kind="topic")',
  ['tests/application/test_briefing.py::test_trace_증거가_있으면_다음_수가_그_끝점이_읽는_컬렉션의_recompute_count다']),
 ('integrate_fields가 태스크를 예시에 안 넘긴다', ROOT / "src/application/briefing.py",
  'tasks=tuple(state.plan_tasks), flow_graph=flow_graph,', 'tasks=(), flow_graph=flow_graph,',
  ['tests/application/test_briefing.py::test_rest_증거가_있고_끝점이_추적됐으면_integrate_예시_첫_수가_그_path의_code_trace다']),
 ('추적 읽기 목록이 확실을 앞세우지 않는다', ROOT / "src/knowledge/flow.py",
  'found.append((_MARK_ORDER[read_mark(e)], ', 'found.append((0, ',
  ['tests/knowledge/test_flow.py::test_traced_reads는_끝점의_추적_읽기를_종류별로_확실_먼저_준다']),
 ('스텁 코드가 seed의 사슬을 안 준다', ROOT / "src/infrastructure/stubs.py",
  '        self._trace = dict(seeds.get("trace") or {})', '        self._trace = {}',
  ['tests/infrastructure/test_stubs.py::test_스텁_코드는_seed의_사슬만_주고_나머지는_없다고_한다',
   'tests/application/test_dryrun.py::test_사다리_대본이_게이트를_한_칸씩_거쳐_스텁으로_끝까지_돈다']),
 ('seeds의 code 절이 스텁 코드를 안 만든다', ROOT / "src/infrastructure/factory.py",
  'if code is None and "code" in seeds:', 'if False:',
  ['tests/infrastructure/test_factory.py::test_seeds에_code_절이_있을_때만_스텁_코드를_조립하고_만들어진_것이_있으면_그것이_이긴다']),
 ('다른 path로 낸 code.trace가 실패하면 칸이 사라진다', ROOT / "src/application/briefing.py",
  '        if (_issued(tasks, "code.trace", endpoint=path) or flow_graph is None',
  '        if ("code.trace" in used or flow_graph is None',
  ['tests/application/test_briefing.py::test_다른_이름으로_낸_code_trace가_실패해도_그_path의_칸은_남는다']),
 # ── 11d 6a: 심볼 인덱스 ──
 ('stoplist를 안 지킨다', ROOT / "src/knowledge/index.py",
  '        if meth in STOPLIST:\n            self._count("stoplist")\n            return None',
  '        if False:\n            self._count("stoplist")\n            return None',
  ['tests/knowledge/test_index.py::test_전부_실패하면_같은_이름_메서드에_candidate이되_stoplist와_상한을_지킨다']),
 ('후보 상한을 안 지킨다', ROOT / "src/knowledge/index.py",
  '        if len(cands) > MAX_CANDIDATES:\n            # 재정의 뿌리로 접는다',
  '        if False:\n            # 재정의 뿌리로 접는다',
  ['tests/knowledge/test_index.py::test_전부_실패하면_같은_이름_메서드에_candidate이되_stoplist와_상한을_지킨다']),
 ('self 호출 미해석을 레포 전체로 푼다', ROOT / "src/knowledge/index.py",
  'for s in self.descendants(fn.class_sid)', 'for s in self.classes',
  ['tests/knowledge/test_index.py::test_self_호출은_조상에서_찾고_못_찾으면_하위_클래스로만_좁힌다']),
 ('candidate가 exact를 끌어내린다', ROOT / "src/knowledge/index.py",
  '        if cur.certainty == "candidate" and certainty == "exact":', '        if True:',
  ['tests/knowledge/test_index.py::test_같은_엣지는_candidate에서_exact로만_승격한다']),
 ('생성 지점 역전파를 안 한다', ROOT / "src/knowledge/index.py",
  '            types = self._types_at_construction(csid, init_fn, param)', '            types = []',
  ['tests/knowledge/test_index.py::test_생성_지점이_하나면_힌트_없는_주입도_확실이고_그_타입으로_푼다']),
 ('컨테이너 어노테이션을 벗긴다', ROOT / "src/knowledge/index.py",
  '            if head and head[-1] in _UNWRAP:', '            if head:',
  ['tests/knowledge/test_index.py::test_어노테이션은_Optional_Annotated_문자열을_벗기고_컨테이너는_안_벗긴다']),
 ('동명 클래스를 전부 구현체로 잇는다', ROOT / "src/knowledge/index.py",
  '            chosen = [c for c in ranked if score(c) == top]', '            chosen = ranked',
  ['tests/knowledge/test_index.py::test_동명_클래스는_메서드_단위로_implements이고_없는_메서드는_엣지가_없으며_경로가_가까운_쪽이다']),
 ('Depends 제공자를 안 본다', ROOT / "src/knowledge/index.py",
  '                    provided = self._depends_provider_class(mod, inner.elts[1:])', '                    provided = None',
  ['tests/knowledge/test_index.py::test_Depends_제공자의_반환_클래스가_수신_타입이다']),
 ('커버리지 누락을 안 잡는다', ROOT / "src/knowledge/index_check.py",
  '        if missing or extra:', '        if False:',
  ['tests/knowledge/test_index_check.py::test_불변식은_id_엣지_class_id_줄_범위_커버리지_위반을_각각_잡는다']),
 ('정밀도 표본이 데코레이터를 안 본다', ROOT / "src/knowledge/index_check.py",
  '        body = "\\n".join(lines[src.line - 1:src.end_line]) + "\\n" + "\\n".join(src.decorators)',
  '        body = "\\n".join(lines[src.line - 1:src.end_line])',
  ['tests/knowledge/test_index_check.py::test_정밀도_표본은_exact_calls의_대상_이름이_호출자_본문이나_데코레이터에_있는지_본다']),
 ('code graph가 인덱스를 안 쓴다', ROOT / "src/__main__.py",
  '    gb.write_index(out_dir, symbol_index)', '    pass  # gb.write_index(out_dir, symbol_index)',
  ['tests/knowledge/test_cli_code.py::test_code_graph가_심볼_인덱스를_쓰고_status와_check가_말한다']),
 ('파라미터가 모듈 이름을 안 가린다', ROOT / "src/knowledge/index.py",
  '            if name in fn.params or (name in fn.free and name not in fn.locals_):',
  '            if name in fn.free and name not in fn.locals_:',
  ['tests/knowledge/test_index.py::test_파라미터나_지역변수_호출은_이름이_같은_함수에_안_잇고_별칭_지역은_확정으로_푼다']),
 ('함수 안 import를 안 본다', ROOT / "src/knowledge/index.py",
  '            elif isinstance(node, (ast.Import, ast.ImportFrom)):\n                self._imports(node, overwrite=False)',
  '            elif isinstance(node, (ast.Import, ast.ImportFrom)):\n                pass',
  ['tests/knowledge/test_index.py::test_함수_안_import도_해석하고_from_import는_정의한_모듈로_imports_엣지를_건다']),
 ('서드파티 호출을 동명 후보로 떨어뜨린다', ROOT / "src/knowledge/index.py",
  '        if self.is_external(fn, chain):\n            self._count("external")\n            self._note_external(fn, chain)\n            return None\n        # (c′)',
  '        if False:\n            self._count("external")\n            self._note_external(fn, chain)\n            return None\n        # (c′)',
  ['tests/knowledge/test_index.py::test_서드파티_호출과_호출_가능한_필드는_따로_세고_후보를_안_만든다']),
 ('순환 대입의 타입 추론을 안 끊는다', ROOT / "src/knowledge/index.py",
  '                target = self._call_target(fn, chain, hops=hops + 1)', '                target = self._call_target(fn, chain)',
  ['tests/knowledge/test_index.py::test_자기_자신으로_도는_대입은_타입_추론을_끊고_인덱스는_끝난다']),
 ('테스트 파일을 인덱싱한다', ROOT / "src/knowledge/index.py",
  '    return path.endswith(".py") and not _skipped(path) and not _is_test_file(path)',
  '    return path.endswith(".py") and not _skipped(path)',
  ['tests/knowledge/test_index.py::test_테스트_파일은_입구에서_건너뛰어_심볼도_엣지도_안_만든다']),
 ('overrides가 조상 전부에 걸린다', ROOT / "src/knowledge/index.py",
  '                        break           # 가장 가까운 조상 하나', '                        pass            # 가장 가까운 조상 하나',
  ['tests/knowledge/test_index.py::test_상속과_재정의_엣지_던더는_제외']),
 ('정밀도 표본이 하위 클래스 생성자를 거짓 실패로 본다', ROOT / "src/knowledge/index_check.py",
  '            needles = {index.symbols[c].name for c in _with_descendants(dst.class_id, subs)}',
  '            needles = {index.symbols[dst.class_id].name}',
  ['tests/knowledge/test_index_check.py::test_정밀도_표본은_하위_클래스_생성이_베이스_생성자로_풀린_것을_맞은_것으로_본다']),
 ('공유 라이브러리 접두사를 서드파티와 안 가른다', ROOT / "src/knowledge/index.py",
  '        return any(fqn == p or fqn.startswith(p + ".") for p in self.shared.get(repo, ()))',
  '        return False',
  ['tests/knowledge/test_index.py::test_미해석_호출은_수신자_모양별로_세어_6b가_무엇을_먼저_지을지_숫자로_정한다']),
 ('못 푼 호출의 모양을 안 센다', ROOT / "src/knowledge/index.py",
  '        if not self._quiet:\n            self.shapes[shape][name] += 1',
  '        if False:\n            self.shapes[shape][name] += 1',
  ['tests/knowledge/test_index.py::test_미해석_호출은_수신자_모양별로_세어_6b가_무엇을_먼저_지을지_숫자로_정한다']),
 ('super 호출을 조상에서 안 찾는다', ROOT / "src/knowledge/index.py",
  '            if head_chain == ("super",) and fn.class_sid is not None and len(tail) == 1:',
  '            if False:',
  ['tests/knowledge/test_index.py::test_super_호출은_조상에서_확실로_풀고_조상이_갈리면_그_조상들만_후보다']),
 ('f().m()을 반환 클래스로 안 푼다', ROOT / "src/knowledge/index.py",
  '            if c is None and plain:', '            if False:',
  ['tests/knowledge/test_index.py::test_함수_호출_결과에_대한_호출은_그_함수의_반환_클래스에서_푼다']),
 ('동명 후보를 재정의 뿌리로 안 접는다', ROOT / "src/knowledge/index.py",
  '            if len(roots) <= MAX_CANDIDATES:\n                return "candidate", roots, "root"',
  '            if False:\n                return "candidate", roots, "root"',
  ['tests/knowledge/test_index.py::test_동명_후보가_상한을_넘으면_재정의_뿌리로_접어_베이스_메서드_하나에_candidate다']),
 ('ls가 채워진 서브모듈 안으로 안 내려간다', ROOT / 'src/infrastructure/git_reader.py',
  '        subs = await self._declared_subs(repo, commit)\n        if not subs:\n            return names, []',
  '        subs = await self._declared_subs(repo, commit)\n        if True:\n            return names, []',
  ['tests/infrastructure/test_git_reader.py::test_채워진_submodule_안의_파일도_목록에_든다_부모가_박은_버전으로', 'tests/knowledge/test_cli_code.py::test_code_graph가_채워진_공유_서브모듈까지_인덱싱해_소비_코드에서_확실로_잇는다']),
 ('ls가 부모가 박은 버전이 아니라 지금 HEAD로 내려간다', ROOT / 'src/infrastructure/git_reader.py',
  '            args = ["ls-tree", "-r", "--name-only", sha]',
  '            args = ["ls-tree", "-r", "--name-only", "HEAD"]',
  ['tests/infrastructure/test_git_reader.py::test_채워진_submodule_안의_파일도_목록에_든다_부모가_박은_버전으로']),
 ('버전 없는 서브모듈을 목록이 완전하다고 한다', ROOT / 'src/infrastructure/git_reader.py',
  '        if stale:\n            notes.append(self._stale_error(stale))',
  '        if False:\n            notes.append(self._stale_error(stale))',
  ['tests/infrastructure/test_git_reader.py::test_그_커밋의_submodule_버전이_없으면_목록은_안_펼치고_이유를_말한다']),
 ('gitlink를 파일마다 다시 푼다', ROOT / 'src/infrastructure/git_reader.py',
  '                if _FULL_SHA.fullmatch(commit):\n                    self._gitlinks[key] = sha',
  '                if False:\n                    self._gitlinks[key] = sha',
  ['tests/infrastructure/test_git_reader.py::test_submodule_안_파일을_여럿_읽어도_gitlink와_버전_확인은_한_번이다']),
 ('추정 후보 풀이 레포를 건넌다', ROOT / 'src/knowledge/index.py',
  '        return [s for s in pool if s != fn.sid and self.index.symbols[s].repo == repo]',
  '        return [s for s in pool if s != fn.sid]',
  ['tests/knowledge/test_index.py::test_추정_후보는_부르는_쪽_레포_안에서만_고른다']),
 ('재수출(import *)을 안 따라간다', ROOT / 'src/knowledge/index.py',
  '                return self._via_star(mod, rest, _seen)',
  '                return None',
  ['tests/knowledge/test_index.py::test_import_별표로_재수출한_이름을_따라가되_all과_밑줄과_순환을_지킨다']),
 ('import * 뒤 맨 이름을 안 찾는다', ROOT / 'src/knowledge/index.py',
  '        return self._via_star(mod, [name])',
  '        return None',
  ['tests/knowledge/test_index.py::test_import_별표로_재수출한_이름을_따라가되_all과_밑줄과_순환을_지킨다']),
 ('재수출이 __all__을 안 지킨다', ROOT / 'src/knowledge/index.py',
  '        return any(isinstance(e, ast.Constant) and e.value == name for e in declared.elts)',
  '        return True',
  ['tests/knowledge/test_index.py::test_import_별표로_재수출한_이름을_따라가되_all과_밑줄과_순환을_지킨다']),
 ('공유 라이브러리 미해석을 이유별로 안 가른다', ROOT / 'src/knowledge/index.py',
  '            return self._note("shared_absent", repo)',
  '            return self._note("shared_unnamed", repo)',
  ['tests/knowledge/test_index.py::test_공유_라이브러리_미해석은_레포에_안_들어옴과_이름_못_찾음으로_가른다']),
 ('클로저 변수를 맨 이름으로 센다', ROOT / 'src/knowledge/index.py',
  '            if name in fn.params or (name in fn.free and name not in fn.locals_):',
  '            if name in fn.params:',
  ['tests/knowledge/test_index.py::test_바깥_함수의_인자를_부르는_클로저는_변수_호출이다']),
 ('정밀도 표본이 import 별칭을 안 본다', ROOT / 'src/knowledge/index_check.py',
  '        needles |= {a for n in needles for a in aliases.get(n, ())}',
  '        needles |= set()',
  ['tests/knowledge/test_index_check.py::test_정밀도_표본은_import_별칭으로_부른_것을_맞은_것으로_본다']),
 ('다른 모듈의 모듈 수준 값을 안 따라간다', ROOT / 'src/knowledge/index.py',
  '                if rest[0] in mod.aliases:\n                    return self._value(mod, mod.aliases[rest[0]], rest[1:])',
  '                if False:\n                    return self._value(mod, mod.aliases[rest[0]], rest[1:])',
  ['tests/knowledge/test_index.py::test_다른_모듈의_싱글턴은_정의한_모듈에서_값의_클래스를_정하고_서드파티_값이면_서드파티로_센다']),
 ('싱글턴의 클래스를 부르는 쪽 모듈에서 찾는다', ROOT / 'src/knowledge/index.py',
  '                    or self._class_of_value(owner, got[1], hops + 1)',
  '                    or self._class_of_value(mod, got[1], hops + 1)',
  ['tests/knowledge/test_index.py::test_다른_모듈의_싱글턴은_정의한_모듈에서_값의_클래스를_정하고_서드파티_값이면_서드파티로_센다']),
 ('다시 내보낸 서드파티를 공유 라이브러리로 센다', ROOT / 'src/knowledge/index.py',
  '        origin = self._origin(repo, target)',
  '        origin = target',
  ['tests/knowledge/test_index.py::test_다른_모듈의_싱글턴은_정의한_모듈에서_값의_클래스를_정하고_서드파티_값이면_서드파티로_센다']),
 ('서드파티 값의 싱글턴 호출을 안 세고 버린다', ROOT / 'src/knowledge/index.py',
  '                    if origin is not None:\n                        self._count("external")',
  '                    if False:\n                        self._count("external")',
  ['tests/knowledge/test_index.py::test_다른_모듈의_싱글턴은_정의한_모듈에서_값의_클래스를_정하고_서드파티_값이면_서드파티로_센다']),
 ('정밀도 표본이 super 경유 엣지를 거짓 실패로 본다', ROOT / 'src/knowledge/index_check.py',
  '        if e.via == "super":\n            needles.add("super")',
  '        if False:\n            needles.add("super")',
  ['tests/knowledge/test_index_check.py::test_정밀도_표본은_super_경유_엣지를_본문의_super로_확인한다']),
 ('질의가 베이스·포트를 거친 디스패치를 안 본다', ROOT / 'src/knowledge/query.py',
  '            elif e.type in ("overrides", "implements"):',
  '            elif False:',
  ['tests/knowledge/test_query.py::test_부르는_쪽은_디스패치를_거쳐_진입점까지_가고_베이스_메서드는_진입점이_아니다']),
 ('디스패치로만 닿은 베이스 메서드를 진입점이라 한다', ROOT / 'src/knowledge/query.py',
  '                (dead if parent[node].mark == DISPATCH else roots).append(node)',
  '                roots.append(node)',
  ['tests/knowledge/test_query.py::test_디스패치로만_닿은_베이스_메서드는_진입점이_아니라_막다른_곳으로_따로_말한다']),
 ('이름 찾기가 정확한 qualname을 앞세우지 않는다', ROOT / 'src/knowledge/query.py',
  '    return exact or [s.id for s in pool if s.qualname.endswith("." + name)]',
  '    return exact + [s.id for s in pool if s.qualname.endswith("." + name)]',
  ['tests/knowledge/test_query.py::test_이름은_qualname_끝부분_파일_레포로_찾고_정확한_qualname이_끝부분보다_앞선다']),
 ('라우트 핸들러를 못 알아본다', ROOT / 'src/knowledge/query.py',
  '    return next((d for d in index.symbols[sid].decorators if _ROUTE.search(d)), None)',
  '    return None',
  ['tests/knowledge/test_query.py::test_부르는_쪽은_디스패치를_거쳐_진입점까지_가고_베이스_메서드는_진입점이_아니다']),
 ('자원 이름이 정확하지 않으면 못 찾는다', ROOT / 'src/knowledge/query.py',
  '    return collect(lambda n: n == name) or collect(lambda n: name in n)',
  '    return collect(lambda n: n == name)',
  ['tests/knowledge/test_query.py::test_자원을_쓰고_읽는_함수를_이름으로_찾고_정확한_이름이_없으면_부분_일치다']),
 ('이름이 모호해도 아무거나 고른다', ROOT / 'src/__main__.py',
  '    if len(found) > 1:\n        for line in qy.ambiguous_lines(index, text, found):',
  '    if False:\n        for line in qy.ambiguous_lines(index, text, found):',
  ['tests/knowledge/test_cli_code.py::test_code_callers_path_uses가_인덱스로_역질문에_답한다']),
 ('이름이 다른 포트와 구현을 안 잇는다', ROOT / 'src/knowledge/index.py',
  '            ruled = [c for a in _port_aliases(port.node.name) for c in pool(a)',
  '            ruled = [c for a in [] for c in pool(a)',
  ['tests/knowledge/test_index.py::test_이름_규칙이_다른_포트와_구현을_메서드_단위로_잇고_메서드가_없으면_안_잇는다', 'tests/knowledge/test_query.py::test_포트_타입으로_부르는_곳도_구현의_부르는_쪽으로_모인다']),
 ('이름만 닮은 클래스를 구현으로 본다', ROOT / 'src/knowledge/index.py',
  '                     if c not in same and any(self.find_method(c, n) is not None for n in declared)]',
  '                     if c not in same]',
  ['tests/knowledge/test_index.py::test_이름_규칙이_다른_포트와_구현을_메서드_단위로_잇고_메서드가_없으면_안_잇는다']),
 ('포트를 상속한 구현에 implements를 또 건다', ROOT / 'src/knowledge/index.py',
  '                        and psid not in self.ancestors(c)]',
  '                        ]',
  ['tests/knowledge/test_index.py::test_이름_규칙이_다른_포트와_구현을_메서드_단위로_잇고_메서드가_없으면_안_잇는다']),
 ('기본값 자리 Depends의 공급자를 안 본다', ROOT / 'src/knowledge/index.py',
  '                c = provided if provided is not None else self.class_of_annotation(fn.mod, ann)',
  '                c = self.class_of_annotation(fn.mod, ann) or provided',
  ['tests/knowledge/test_index.py::test_기본값_자리의_Depends도_공급자가_돌려주는_구현이_수신_타입이다']),
 ('질의 그래프가 같은 디스패치를 두 번 담는다', ROOT / 'src/knowledge/query.py',
  '            if link in seen:            # 같은 짝이',
  '            if False:            # 같은 짝이',
  ['tests/knowledge/test_query.py::test_같은_디스패치가_두_길로_들어와도_그래프에는_한_번이다']),
 ('calls.html이 베이스 메서드를 진입점이라 한다', ROOT / 'src/presentation/calls_html.py',
  '            entry = "base" if any(l.mark == qy.DISPATCH for l in graph.out.get(sid, [])) else "entry"',
  '            entry = "entry"',
  ['tests/presentation/test_calls_html.py::test_진입점_라우트_베이스와_함수마다_쓰고_읽는_자원을_표시한다']),
 ('calls.html이 자원의 진입점 경로를 안 싣는다', ROOT / 'src/presentation/calls_html.py',
  '                flows[sid] = {"p": entries[:PATHS_PER_USE],',
  '                flows[sid] = {"p": [],',
  ['tests/presentation/test_calls_html.py::test_자원마다_쓰고_읽는_함수와_진입점_경로를_질의로_미리_계산해_싣는다']),
 ('calls.html이 쓰기와 읽기를 가르지 않는다', ROOT / 'src/presentation/calls_html.py',
  '            d = "w" if r.direction == "writes" else "r"',
  '            d = "w"',
  ['tests/presentation/test_calls_html.py::test_자원마다_쓰고_읽는_함수와_진입점_경로를_질의로_미리_계산해_싣는다']),
 ('calls.html 데이터의 닫는 태그가 문서를 끊는다', ROOT / 'src/presentation/calls_html.py',
  '    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\\\/")',
  '    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))',
  ['tests/presentation/test_calls_html.py::test_외부_참조가_없고_데이터_속_닫는_태그와_제목이_문서를_못_끊는다']),
 ('code graph가 calls.html을 안 쓴다', ROOT / 'src/__main__.py',
  '    (out_dir / "calls.html").write_text(',
  '    (out_dir / "calls-off.html").write_text(',
  ['tests/knowledge/test_cli_code.py::test_code_graph가_심볼_인덱스를_쓰고_status와_check가_말한다']),
 ('드라이브 문자 없는 path도 되돌린다', ROOT / 'src/knowledge/flow.py',
  '    if not shell_mangled(arg):\n        return None\n    tail',
  '    tail',
  ['tests/knowledge/test_flow.py::test_Git_Bash가_바꾼_끝점_path를_가장_길게_맞는_path로_되돌린다']),
 ('가장 짧게 맞는 path로 되돌린다', ROOT / 'src/knowledge/flow.py',
  '    return max(hits, key=len) if hits else None',
  '    return min(hits, key=len) if hits else None',
  ['tests/knowledge/test_flow.py::test_Git_Bash가_바꾼_끝점_path를_가장_길게_맞는_path로_되돌린다']),
 ('code trace가 Git Bash 인자를 안 되돌린다', ROOT / 'src/__main__.py',
  '    path = flow.endpoint_path(_shell_path(args.path, graph) or "")',
  '    path = flow.endpoint_path(args.path or "")',
  ['tests/knowledge/test_cli_code.py::test_code_trace와_flow는_Git_Bash가_바꾼_끝점_path를_되돌려_읽는다']),
 ('code flow가 Git Bash 인자를 안 되돌린다', ROOT / 'src/__main__.py',
  '    args.name, args.to = _shell_path(args.name, graph), _shell_path(args.to, graph)\n',
  '',
  ['tests/knowledge/test_cli_code.py::test_code_trace와_flow는_Git_Bash가_바꾼_끝점_path를_되돌려_읽는다']),
 ('없는 끝점에 MSYS 안내를 안 한다', ROOT / 'src/__main__.py',
  '                 if flow.shell_mangled(path) else ""))',
  '                 if False else ""))',
  ['tests/knowledge/test_cli_code.py::test_code_trace와_flow는_Git_Bash가_바꾼_끝점_path를_되돌려_읽는다']),
 ('역질문 action이 등재에서 빠진다', ROOT / 'src/domain/actions.py',
  '    "code.callers":        ("code",  "callers",       ("name",),               ()),\n',
  '',
  ['tests/domain/test_actions.py::test_역질문_둘이_이름_하나로_등재돼_있고_코드_추적_레인이다']),
 ('인덱스 없이도 역질문을 목록에 둔다', ROOT / 'src/application/briefing.py',
  '    if not code_index:\n        out |= {"code.callers", "code.uses", "code.resources"}',
  '    if False:\n        out |= {"code.callers", "code.uses", "code.resources"}',
  ['tests/application/test_briefing.py::test_그래프가_없으면_없다고_적고_code_flow를_목록에서_뺀다']),
 ('리드에 인덱스 유무를 안 넘긴다', ROOT / 'src/application/lead.py',
  '                                            flow_graph=flow_graph, code_index=code_index))\n        got = await ask_json(llm, prompt, FrameReply',
  '                                            flow_graph=flow_graph))\n        got = await ask_json(llm, prompt, FrameReply',
  ['tests/application/test_lead.py::test_심볼_인덱스가_있을_때만_역질문_action이_프롬프트에_실린다']),
 ('조사가 심볼 인덱스를 안 붙인다', ROOT / 'src/__main__.py',
  '    code.attach_index(index)\n    return code, code.service_names(), graph, note',
  '    code.attach_index(None)\n    return code, code.service_names(), graph, note',
  ['tests/knowledge/test_cli_code.py::test_조사에는_신선한_번들의_심볼_인덱스도_실리고_낡으면_같이_빠진다']),
 ('낡은 번들의 인덱스를 붙인다', ROOT / 'src/__main__.py',
  '        if graph is not None:\n            # 번들은 GBM 하나다(11e-2)',
  '        if True:\n            # 번들은 GBM 하나다(11e-2)',
  ['tests/knowledge/test_cli_code.py::test_조사에는_신선한_번들의_심볼_인덱스도_실리고_낡으면_같이_빠진다']),
 ('모호한 이름을 코드가 고른다', ROOT / 'src/infrastructure/deployed_code.py',
  '        if len(found) > 1:\n            return ProbeResult.failed("\\n".join(qy.ambiguous_lines',
  '        if False:\n            return ProbeResult.failed("\\n".join(qy.ambiguous_lines',
  ['tests/infrastructure/test_deployed_code.py::test_code_callers와_uses는_붙인_인덱스로_답하고_없거나_모호하면_실패로_답한다']),
 ('역질문 답에 서비스 이름을 안 붙인다', ROOT / 'src/infrastructure/deployed_code.py',
  '        svc = flowgraph.owner(s.file, s.repo, self._topology)[0]\n        return f"[{s.repo}{\' · \' + svc if svc else \'\'}]"',
  '        svc = flowgraph.owner(s.file, s.repo, self._topology)[0]\n        return f"[{s.repo}]"',
  ['tests/infrastructure/test_deployed_code.py::test_code_callers와_uses는_붙인_인덱스로_답하고_없거나_모호하면_실패로_답한다']),
 ('스텁이 seed의 uses를 안 준다', ROOT / 'src/infrastructure/stubs.py',
  '        self._uses = dict(seeds.get("uses") or {})',
  '        self._uses = {}',
  ['tests/infrastructure/test_stubs.py::test_스텁_코드는_seed의_callers와_uses도_그대로_준다']),
 ('callers 조립이 진입점 경로를 안 그린다', ROOT / 'src/knowledge/query.py',
  '    if res.entries:\n        lines.append(f"진입점 {len(res.entries)}:")',
  '    if False:\n        lines.append(f"진입점 {len(res.entries)}:")',
  ['tests/infrastructure/test_deployed_code.py::test_code_callers와_uses는_붙인_인덱스로_답하고_없거나_모호하면_실패로_답한다']),
 ('uses 조립이 진입점 꼬리를 안 붙인다', ROOT / 'src/knowledge/query.py',
  '            lines += [f"    {display(index, u.sid, u.line)} {where(u.sid)} — {entry_brief(index, graph, u.sid)}"',
  '            lines += [f"    {display(index, u.sid, u.line)} {where(u.sid)}"',
  ['tests/knowledge/test_cli_code.py::test_code_callers_path_uses가_인덱스로_역질문에_답한다']),
 ('재집계 뒤 uses 칸이 없다', ROOT / 'src/application/briefing.py',
  '    if not code_index or flow_graph is None:\n        return None\n    keys =',
  '    if True:\n        return None\n    keys =',
  ['tests/application/test_briefing.py::test_재집계_뒤_넷째_칸은_code_uses이고_이름은_일치면_컬렉션_불일치면_끝점이_읽은_키다']),
 ('인덱스 없어도 uses 칸을 보여 준다', ROOT / 'src/application/briefing.py',
  '    if not code_index or flow_graph is None:',
  '    if flow_graph is None:',
  ['tests/application/test_briefing.py::test_재집계_뒤_넷째_칸은_code_uses이고_이름은_일치면_컬렉션_불일치면_끝점이_읽은_키다']),
 ('불일치에서 끝점이 읽은 키 대신 컬렉션을 묻는다', ROOT / 'src/application/briefing.py',
  '    name = keys[0] if (match is False and keys) else',
  '    name = keys[0] if False else',
  ['tests/application/test_briefing.py::test_재집계_뒤_넷째_칸은_code_uses이고_이름은_일치면_컬렉션_불일치면_끝점이_읽은_키다']),
 ('일치·불일치를 안 가리고 키를 묻는다', ROOT / 'src/application/briefing.py',
  '    name = keys[0] if (match is False and keys) else',
  '    name = keys[0] if keys else',
  ['tests/application/test_briefing.py::test_재집계_뒤_넷째_칸은_code_uses이고_이름은_일치면_컬렉션_불일치면_끝점이_읽은_키다']),
 ('uses 칸 억제가 없다', ROOT / 'src/application/briefing.py',
  '        if _issued(tasks, "code.uses", name=name):\n            return None',
  '        if False:\n            return None',
  ['tests/application/test_briefing.py::test_재집계_뒤_넷째_칸은_code_uses이고_이름은_일치면_컬렉션_불일치면_끝점이_읽은_키다']),
 ('쓰는 함수가 없어도 callers 칸을 낸다', ROOT / 'src/application/briefing.py',
  '    if writer is None or _issued(tasks, "code.callers", name=writer):',
  '    if _issued(tasks, "code.callers", name=writer):',
  ['tests/application/test_briefing.py::test_uses_증거_뒤_다섯째_칸은_그_자원을_쓰는_함수의_code_callers다']),
 ('callers 칸 억제가 없다', ROOT / 'src/application/briefing.py',
  '    if writer is None or _issued(tasks, "code.callers", name=writer):',
  '    if writer is None:',
  ['tests/application/test_briefing.py::test_uses_증거_뒤_다섯째_칸은_그_자원을_쓰는_함수의_code_callers다']),
 ('integrate가 증거를 예시에 안 넘긴다', ROOT / 'src/application/briefing.py',
  '                                     evidence=tuple(state.evidence), code_index=code_index, target=target, walk=state.walk),',
  '                                     evidence=(), code_index=code_index, target=target, walk=state.walk),',
  ['tests/application/test_briefing.py::test_uses_증거_뒤_다섯째_칸은_그_자원을_쓰는_함수의_code_callers다']),
 ('쓰는 함수 대신 읽는 함수를 집는다', ROOT / 'src/application/briefing.py',
  '_WRITER = re.compile(r"쓰기 \\d+:',
  '_WRITER = re.compile(r"읽기 \\d+:',
  ['tests/application/test_briefing.py::test_uses_증거_뒤_다섯째_칸은_그_자원을_쓰는_함수의_code_callers다']),
 ('인덱스 사슬이 깊이 상한을 안 지킨다', ROOT / 'src/knowledge/index_trace.py',
  '        if node.depth >= max_depth and any(c.depth > node.depth for c in children):',
  '        if node.depth > max_depth and any(c.depth > node.depth for c in children):',
  ['tests/knowledge/test_index_trace.py::test_깊이_상한에서_멈추고_그렇다고_적는다']),
 ('후보 아래 읽기를 확실로 둔다', ROOT / 'src/knowledge/index_trace.py',
  '            out.append(_Node(link.dst, node.depth + 1, step, "추정"))',
  '            out.append(_Node(link.dst, node.depth + 1, step, node.cap))',
  ['tests/knowledge/test_index_trace.py::test_이름만_같은_후보를_거친_읽기는_추정이고_gap에_후보_수가_남는다']),
 ('후보 수 gap을 안 남긴다', ROOT / 'src/knowledge/index_trace.py',
  '        gaps.append(Gap(s.file, line, f"{name}: 받는 쪽을 못 좁혀 후보 {len(group)}개 — 전부 따라가되 읽기는 추정"))',
  '        pass',
  ['tests/knowledge/test_index_trace.py::test_이름만_같은_후보를_거친_읽기는_추정이고_gap에_후보_수가_남는다']),
 ('디스패치가 깊이를 먹는다', ROOT / 'src/knowledge/index_trace.py',
  '            out.append(_Node(link.dst, node.depth, step, dispatch_cap))',
  '            out.append(_Node(link.dst, node.depth + 1, step, dispatch_cap))',
  ['tests/knowledge/test_index_trace.py::test_디스패치는_깊이를_안_먹는다']),
 ('같은 path의 라우트 선언 전부가 뿌리가 아니다', ROOT / 'src/knowledge/index_trace.py',
  '        if sid not in roots:\n            roots.append(sid)',
  '        if not roots:\n            roots.append(sid)',
  ['tests/knowledge/test_index_trace.py::test_같은_path의_라우트_선언_전부가_뿌리이고_확실_호출을_따라_읽기를_모은다']),
 ('getattr gap을 안 남긴다', ROOT / 'src/knowledge/index_trace.py',
  '            if why == "getattr":\n                gaps.append(Gap(s.file, line, _GETATTR))',
  '            if False:\n                gaps.append(Gap(s.file, line, _GETATTR))',
  ['tests/knowledge/test_index_trace.py::test_측정판_핸들러는_캐시_키를_지나_컬렉션까지_닿고_getattr_gap_하나를_남긴다']),
 ('config 키 경유 읽기를 확실로 둔다', ROOT / 'src/knowledge/index_trace.py',
  '            grade = "추정" if node.cap == "추정" or r.via != "literal" else "확실"',
  '            grade = "추정" if node.cap == "추정" else "확실"',
  ['tests/knowledge/test_index_trace.py::test_측정판_핸들러는_캐시_키를_지나_컬렉션까지_닿고_getattr_gap_하나를_남긴다']),
 ('라우트 줄 아래 함수가 없는 것을 말하지 않는다', ROOT / 'src/knowledge/index_trace.py',
  '            missing.append(f"{rt.file}:L{rt.line}")',
  '            pass',
  ['tests/knowledge/test_index_trace.py::test_라우트_선언이_없거나_그_줄_아래_함수가_인덱스에_없으면_not_found다']),
 ('함수별 미해석 호출을 안 남긴다', ROOT / 'src/knowledge/index.py',
  '                    if self._last_why not in ("builtin", "stoplist"):\n                        missed.append(',
  '                    if False:\n                        missed.append(',
  ['tests/knowledge/test_index.py::test_함수마다_못_푼_호출과_getattr_자리가_심볼에_남고_번들을_오간다']),
 ('getattr 자리를 안 남긴다', ROOT / 'src/knowledge/index.py',
  '            missed: list[tuple[int, str, str]] = _getattr_sites(fn)',
  '            missed: list[tuple[int, str, str]] = []',
  ['tests/knowledge/test_index.py::test_함수마다_못_푼_호출과_getattr_자리가_심볼에_남고_번들을_오간다']),
 ('내장 호출도 미해석으로 남긴다', ROOT / 'src/knowledge/index.py',
  '                    if self._last_why not in ("builtin", "stoplist"):',
  '                    if self._last_why not in ("stoplist",):',
  ['tests/knowledge/test_index.py::test_함수마다_못_푼_호출과_getattr_자리가_심볼에_남고_번들을_오간다']),
 ('번들에서 함수별 미해석을 안 읽는다', ROOT / 'src/knowledge/index.py',
  '            d["unresolved"] = tuple(tuple(u) for u in d.get("unresolved", ()))',
  '            d["unresolved"] = ()',
  ['tests/knowledge/test_index.py::test_함수마다_못_푼_호출과_getattr_자리가_심볼에_남고_번들을_오간다']),
 ("막힌 끝점을 안 센다", ROOT / "src/knowledge/flow.py",
  '                                     and n.get("traced") == "ok" and n.get("gaps"))}',
  '                                     and False)}',
  ["tests/knowledge/test_flow.py::test_막힌_끝점을_센다"]),
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

# ── 12a — conclude + verify ─────────────────────────────────────────
LD = ROOT / "src/application/lead.py"
DR = ROOT / "src/application/dryrun.py"
DG = ROOT / "src/application/diagnose.py"
CS = ROOT / "src/domain/case.py"
V = "tests/application/test_verdict.py"
CASES += [
 ("죽은 조사에도 LLM을 묻는다(증거 0건)", ND,
  '        if not state.evidence:\n            why = (',
  '        if False:\n            why = (',
  [f"{V}::test_조사가_안_돌았으면_LLM을_묻지_않고_degraded다"]),
 ("증거 0건에도 판정을 묻는다", ND,
  '        if not state.evidence:\n            why = (',
  '        if False:\n            why = (',
  [f"{V}::test_증거가_하나도_없으면_degraded이고_실패한_태스크가_caveat에_남는다"]),
 ("판정자의 예외를 흡수하지 않는다", ND,
  '            # 판정자는 LLM 어댑터를 품고 있다. 여기서 죽으면 케이스가 investigating으로 남는다.\n            reply = {"llm_errors": [f"conclude: {type(exc).__name__}: {exc}"]}',
  '            # 판정자는 LLM 어댑터를 품고 있다. 여기서 죽으면 케이스가 investigating으로 남는다.\n            raise',
  [f"{V}::test_판정자가_던져도_흡수한다"]),
 ("llm_error 뒤 판정자의 예외를 흡수하지 않는다", ND,
  '            except Exception as exc:                                # noqa: BLE001\n                reply = {"llm_errors": [f"conclude: {type(exc).__name__}: {exc}"]}',
  '            except Exception as exc:                                # noqa: BLE001\n                raise',
  [f"{V}::test_llm_error여도_증거가_있으면_판정을_한_번_묻는다"]),
 ("비LLM 사유를 caveat에 안 남긴다", ND,
  '            reasons = notes + ([str(reply["note"])] if reply.get("note") else [])',
  '            reasons = notes',
  [f"{V}::test_판정자의_비LLM_사유는_caveat에만_남는다"]),
 ("없는 id 인용을 안 본다", ND,
  '            if evidence_id not in citable:\n                problems.append(f"없는 id',
  '            if False:\n                problems.append(f"없는 id',
  [f"{V}::test_verify는_리드가_본_증거만_인용으로_친다"]),
 ("잘린 증거의 caveat을 안 본다", ND,
  '            elif evidence_id in incomplete and not _id_mentioned(evidence_id, verdict.caveats):',
  '            elif False:',
  [f"{V}::test_잘린_증거로_주장하면_caveat에_그_id가_있어야_한다"]),
 ("id 경계를 안 본다", ND,
  '    pattern = re.compile(rf"(?<![A-Za-z0-9_.-]){re.escape(evidence_id)}(?![A-Za-z0-9_-])")',
  '    pattern = re.compile(re.escape(evidence_id))',
  [f"{V}::test_잘린_증거로_주장하면_caveat에_그_id가_있어야_한다"]),
 ("인용 없는 다리를 받는다", ND,
  '        if not link.evidence_ids:\n            problems.append(f"다리에 인용 없음: {link.component}")\n            continue',
  '        if not link.evidence_ids:\n            continue',
  [f"{V}::test_후보와_기여_요인의_인용도_검사한다"]),
 ("component를 안 본다", ND,
  '        if problem:\n            problems.append(problem)',
  '        if False:\n            problems.append(problem)',
  [f"{V}::test_component는_토폴로지나_증거에_있어야_한다"]),
 ("component 우주에 토폴로지가 빠진다", ND,
  '        if component in deps.components:\n            return None',
  '        if False:\n            return None',
  [f"{V}::test_verify_노드의_component는_토폴로지_서비스여야_하고_external만_증거의_이름을_허용한다"]),
 ("대본 경로도 component를 검사한다", ND,
  '    if not deps.check_discovery:\n        return lambda component: None',
  '    if False:\n        return lambda component: None',
  [f"{V}::test_verify_노드의_component는_토폴로지_서비스여야_하고_external만_증거의_이름을_허용한다"]),
 ("되묻지 않고 바로 강등한다", ND,
  '        if state.verify_attempts < VERIFY_REWRITES:',
  '        if False:',
  [f"{V}::test_첫_실패는_재작성을_요구하고_두_번째_실패는_강등한다", f"{V}::test_재작성_한_번_뒤_통과한다"]),
 ("강등해도 확신을 안 낮춘다", ND,
  '        "contributing": contributing, "confidence": "low", "caveats": caveats})',
  '        "contributing": contributing, "confidence": verdict.confidence, "caveats": caveats})',
  [f"{V}::test_첫_실패는_재작성을_요구하고_두_번째_실패는_강등한다"]),
 ("없는 인용을 안 걷어낸다", ND,
  '        return link.model_copy(update={"evidence_ids": [e for e in link.evidence_ids if e in citable]})',
  '        return link.model_copy(update={"evidence_ids": list(link.evidence_ids)})',
  [f"{V}::test_첫_실패는_재작성을_요구하고_두_번째_실패는_강등한다"]),
 ("근거 잃은 최상위를 결론으로 둔다", ND,
  '    if verdict.root_cause is not None and root is None:\n        verdict_type = "inconclusive"',
  '    if verdict.root_cause is not None and root is None:\n        verdict_type = verdict.verdict_type',
  [f"{V}::test_첫_실패는_재작성을_요구하고_두_번째_실패는_강등한다"]),
 ("verify가 되묻는 길이 없다", ND,
  '    return "conclude" if state.verify_problems else "__end__"',
  '    return "__end__"',
  [f"{V}::test_첫_실패는_재작성을_요구하고_두_번째_실패는_강등한다", f"{V}::test_재작성_한_번_뒤_통과한다"]),
 ("죽은 frame이 판정 없이 끝난다", ND,
  '    return "conclude" if state.stopped_by else "select"',
  '    return "__end__" if state.stopped_by else "select"',
  [f"{V}::test_라우터들", f"{V}::test_frame이_죽어도_판정은_남는다"]),
 ("후보 상한이 없다", ND,
  '        if link.component in seen or len(kept) >= MAX_ALTERNATES:',
  '        if link.component in seen:',
  [f"{V}::test_인과_사슬의_형태는_코드가_정한다"]),
 ("최상위에도 confidence를 둔다", ND,
  '        "confidence": link.confidence if own_confidence else None})',
  '        "confidence": link.confidence})',
  [f"{V}::test_인과_사슬의_형태는_코드가_정한다"]),
 ("리드가 degraded를 낼 수 있다", LD,
  '                          "external", "inconclusive", "not_a_defect"]\n    root_cause: CauseLink | None = None',
  '                          "external", "inconclusive", "not_a_defect", "degraded"]\n    root_cause: CauseLink | None = None',
  ["tests/application/test_lead.py::test_degraded_판정은_LLM이_못_낸다",
   "tests/domain/test_verdict_model.py::test_degraded는_판정_어휘에_있지만_LLM_어휘에는_없다"]),
 ("판정 턴이 트레이스에 안 남는다", LD,
  '        got = await ask_json(conclude_llm or llm, prompt, ConcludeReply, on_exchange=_hook("conclude", state))',
  '        got = await ask_json(llm, prompt, ConcludeReply)',
  ["tests/application/test_lead.py::test_conclude가_판정_JSON을_Verdict로_받고_프롬프트에_본_것만_싣는다"]),
 ("판정 예시가 degraded를 보여 준다", B,
  '        "verdict_type": "<logic_bug | data_loss | config_error | stale_data | external | not_a_defect | inconclusive 중 하나>",',
  '        "verdict_type": "<logic_bug | data_loss | config_error | stale_data | external | not_a_defect | inconclusive | degraded 중 하나>",',
  ["tests/application/test_briefing.py::test_판정_예시는_degraded를_안_보여주고_component_후보를_서비스에서_만든다"]),
 ("재작성 블록이 늘 실린다", B,
  '    if not state.verify_problems:\n        return ""\n    return ("## 재작성',
  '    if False:\n        return ""\n    return ("## 재작성',
  ["tests/application/test_briefing.py::test_재작성_블록은_문제가_있을_때만_실린다"]),
 ("component 후보에 서비스가 빠진다", B,
  '    return "서비스 " + ", ".join(services) + " — 또는 " + tail',
  '    return tail',
  ["tests/application/test_briefing.py::test_판정_예시는_degraded를_안_보여주고_component_후보를_서비스에서_만든다"]),
 ("태스크 오류율을 안 센다", B,
  '    errors = sum(1 for t in state.plan_tasks if t.status == "error")\n    return (f"라운드 {state.round}에서 끝났다',
  '    errors = 0\n    return (f"라운드 {state.round}에서 끝났다',
  ["tests/application/test_briefing.py::test_판정_재료의_증거_블록은_리드가_본_것이고_접속_정보가_없다"]),
 ("대본의 판정을 안 낸다", DR,
  '        return {"verdict": self._script.verdict}',
  '        return {"note": "대본에 verdict가 없다"}',
  ["tests/application/test_dryrun.py::test_대본의_판정을_conclude가_내고_없으면_사유를_남긴다",
   "tests/application/test_dryrun.py::test_사다리_대본은_판정까지_가고_인용이_실재한다"]),
 ("진단에 판정 줄이 없다", DG,
  '    lines.append("  " + verdict_summary(state))',
  '    pass',
  ["tests/application/test_diagnose.py::test_판정_줄이_있다"]),
 ("강등을 통과로 적는다", DG,
  '    if _demoted(state):\n        return "검증 미통과 → 강등"',
  '    if False:\n        return "검증 미통과 → 강등"',
  ["tests/application/test_diagnose.py::test_판정_줄이_있다"]),
 ("판정 트레이스를 태스크처럼 읽는다", TD,
  '    if node == "conclude":',
  '    if False:',
  ["tests/application/test_trace_digest.py::test_conclude_트레이스는_판정을_한_줄로_찍는다"]),
 ("판정 트레이스가 없는 id를 안 찍는다", TD,
  '    if ghosts:\n        line += f" · **증거에 없는 id',
  '    if False:\n        line += f" · **증거에 없는 id',
  ["tests/application/test_trace_digest.py::test_conclude_트레이스는_판정을_한_줄로_찍는다"]),
 ("판정 프롬프트에 actions를 요구한다", MN,
  '                                             slots=briefing.CONCLUDE_SLOTS,\n                                             required=briefing.CONCLUDE_REQUIRED)}',
  '                                             slots=briefing.CONCLUDE_SLOTS)}',
  ["tests/application/test_lead.py::test_CLI가_실제로_돈다"]),
 ("CLI가 판정을 안 찍는다", MN,
  '    print("\\n".join(verdict_lines(CaseState.model_validate(final))) + "\\n")',
  '    pass',
  ["tests/application/test_lead.py::test_CLI가_실제로_돈다"]),
 ("dryrun CLI가 판정을 안 찍는다", MN,
  '    print("\\n".join(verdict_lines(CaseState.model_validate(final))))',
  '    pass',
  ["tests/application/test_dryrun.py::test_CLI가_사다리_대본을_돈다"]),
 ("결론에 root_cause가 없어도 선다", CS,
  '        if self.verdict_type not in ("inconclusive", "degraded", "not_a_defect") and self.root_cause is None:',
  '        if False:',
  ["tests/domain/test_verdict_model.py::test_결론이_있는_판정에는_root_cause가_필요하다"]),
 ("판정 프롬프트 기본값이 바뀐다", SA,
  '    conclude_prompt: str = Field(default="prompts/investigate-conclude.md", min_length=1)',
  '    conclude_prompt: str = Field(default="prompts/conclude.md", min_length=1)',
  ["tests/config/test_schema_app.py::test_판정_프롬프트_경로도_config에_있다"]),
]


# ── 11e-1 — 인덱스 소스를 커밋 스냅샷에서 ─────────────────────────────
GRT = "tests/infrastructure/test_git_reader.py"
DCT = "tests/infrastructure/test_deployed_code.py"
CASES += [
 ("스냅샷이 안 채워진 submodule을 넣는다", GR,
  '                if sub in blind or sub in stale:\n                    continue\n                sha = await self._gitlink(repo, commit, sub)',
  '                if sub in stale:\n                    continue\n                sha = await self._gitlink(repo, commit, sub)',
  [f"{GRT}::test_snapshot은_안_채워진_submodule을_못_봤다고_말한다"]),
 ("스냅샷이 버전 없는 submodule을 넣는다", GR,
  '                if sub in blind or sub in stale:\n                    continue\n                sha = await self._gitlink(repo, commit, sub)',
  '                if sub in blind:\n                    continue\n                sha = await self._gitlink(repo, commit, sub)',
  [f"{GRT}::test_snapshot은_버전_없는_submodule을_펼치지_않고_이유를_말한다"]),
 ("스냅샷이 submodule을 부모 SHA가 아니라 HEAD로 받는다", GR,
  '                inner = await self._blobs(repo, sha, source=f"{source} (submodule {sub}@{sha[:12]})",',
  '                inner = await self._blobs(repo, "HEAD", source=f"{source} (submodule {sub}@{sha[:12]})",',
  [f"{GRT}::test_snapshot은_채워진_submodule을_부모가_박은_버전으로_넣는다"]),
 ("스냅샷이 같은 내용의 파일을 하나만 준다", GR,
  '                for path in paths_by_sha.get(header[0].decode("ascii", "replace"), ()):',
  '                for path in paths_by_sha.get(header[0].decode("ascii", "replace"), ())[:1]:',
  [f"{GRT}::test_snapshot은_내용이_같은_두_파일을_둘_다_준다"]),
 ("스냅샷이 cat-file 머리줄 자리를 잘못 짚는다", GR,
  '                pos += size + 1                     # 본문 뒤에 줄바꿈 하나',
  '                pos += size + 2',
  [f"{GRT}::test_snapshot은_내용이_같은_두_파일을_둘_다_준다"]),
 ("스냅샷이 blob 마지막 글자를 빠뜨린다", GR,
  '                text = data[pos:pos + size].decode("utf-8", "replace")[:_WHOLE_MAX_CHARS]',
  '                text = data[pos:pos + size - 1].decode("utf-8", "replace")[:_WHOLE_MAX_CHARS]',
  [f"{GRT}::test_snapshot은_show와_같은_내용을_한_번에_준다"]),
 ("인덱스 소스가 스냅샷을 안 쓴다", D,
  '            got = await self._reader.snapshot(self._repo, self._commit)\n            if got.status == "ok" and isinstance(got.data, dict):',
  '            got = await self._reader.snapshot(self._repo, self._commit)\n            if False:',
  [f"{DCT}::test_인덱스_소스는_스냅샷_한_번으로_목록과_내용을_다_답한다",
   "tests/knowledge/test_cli_code.py::test_code_graph가_심볼_인덱스를_쓰고_status와_check가_말한다"]),
 ("스냅샷이 실패하면 목록이 빈다", D,
  '        snap = await self._snap()\n        if snap is not None:\n            return sorted(snap)\n        got = await self._reader.ls(',
  '        snap = await self._snap()\n        if snap is not None:\n            return sorted(snap)\n        return []\n        got = await self._reader.ls(',
  [f"{DCT}::test_스냅샷을_못_받으면_예전처럼_파일별로_읽는다"]),
]


# ── 11e-2 — 번들은 GBM 단위, 사이트는 덮은 값 파일 ─────────────────────
FLW = ROOT / "src/knowledge/flow.py"
GBD = ROOT / "src/knowledge/graph_build.py"
IX = ROOT / "src/knowledge/index.py"
GBT = "tests/knowledge/test_graph_build.py"
FLT = "tests/knowledge/test_flow.py"
CLI_GBM = "tests/knowledge/test_cli_code.py::test_code_graph는_GBM_하나에_번들을_만들고_사이트는_덮은_값만_적는다"
CASES += [
 ("번들이 사이트 단위로 돌아간다", GBD,
  '    return Path(output_dir) / "graph" / gbm\n',
  '    return Path(output_dir) / "graph" / f"{gbm}-gumi"\n',
  [f"{GBT}::test_번들은_GBM_단위이고_사이트는_덮은_값_파일이다"]),
 ("없는 사이트 파일이 빈 목록으로 읽힌다", GBD,
  '    except (OSError, ValueError):\n        return None\n    return [r for r in rows',
  '    except (OSError, ValueError):\n        return []\n    return [r for r in rows',
  [f"{GBT}::test_번들은_GBM_단위이고_사이트는_덮은_값_파일이다",
   "tests/knowledge/test_cli_code.py::test_사이트_파일이_없으면_GBM_값_그대로_싣고_말한다"]),
 ("덮은 값을 입혀도 엣지가 옛 노드를 가리킨다", FLW,
  '            if node["id"] != new_id:\n                rename[node["id"]] = new_id',
  '            if False:\n                rename[node["id"]] = new_id',
  [f"{FLT}::test_덮은_값은_같은_key_path의_노드를_이름만_바꾸고_엣지를_따라간다"]),
 ("덮은 값이 노드 이름을 안 바꾼다", FLW,
  '            node["id"], node["label"] = new_id, value\n            continue',
  '            continue',
  [f"{FLT}::test_덮은_값은_같은_key_path의_노드를_이름만_바꾸고_엣지를_따라간다", CLI_GBM]),
 ("GBM 층에 없는 이름이 config 엣지 없이 든다", FLW,
  '        for svc in sorted(row.get("services") or []):\n            repo = repo_of.get(svc, "")',
  '        for svc in []:\n            repo = repo_of.get(svc, "")',
  [f"{FLT}::test_GBM_층에_없는_이름은_config_엣지만_달고_새_노드로_든다"]),
 ("GBM 기준값에 사이트 층이 섞인다", D,
  '        return [p.replace("{gbm}", self._gbm) for p in self._topology.config_paths if "{fct}" not in p]',
  '        return self._topology.resolved_config_paths(self._gbm, self._fct)',
  [f"{DCT}::test_names_for는_층을_스냅샷에서_읽고_GBM_층만으로도_뽑는다", CLI_GBM]),
 ("사이트 파일에 GBM과 같은 값도 적는다", D,
  '            if base_by.get((n.kind, n.key_path)) == n.value:\n                continue',
  '            if False:\n                continue',
  [f"{DCT}::test_site_overrides는_GBM_값과_다른_것만_적는다", CLI_GBM]),
 ("사이트 층을 스냅샷 대신 show로 읽는다", D,
  '        snap = await self.snapshot_for(known.repo)\n        layers = []',
  '        snap = None\n        layers = []',
  [f"{DCT}::test_사이트_층_읽기는_스냅샷_뒤로는_git을_안_띄운다", CLI_GBM]),
 ("code graph가 사이트 층을 기준으로 삼는다", MN,
  '        names, problems = await code.names_for("")\n        if not names:',
  '        names, problems = await code.names_for(fct)\n        if not names:',
  [CLI_GBM]),
 ("code graph가 사이트 파일을 안 쓴다", MN,
  '        rows = _run(code.site_overrides(one, names))\n        gb.write_site(out_dir, one, rows)',
  '        rows = _run(code.site_overrides(one, names))',
  [CLI_GBM]),
 ("meta에 번들의 사이트 목록이 안 실린다", MN,
  '                        graphify=gb.graphify_version(binary), notes=problems + states, sites=fcts)',
  '                        graphify=gb.graphify_version(binary), notes=problems + states, sites=[])',
  [CLI_GBM]),
 ("끝점 등재가 고른 사이트 것만 든다", MN,
  '        for name, pair in _rest_entries(other).items():\n            entries.setdefault(name, pair)',
  '        for name, pair in []:\n            entries.setdefault(name, pair)',
  ["tests/knowledge/test_cli_code.py::test_끝점_등재는_GBM의_사이트_전부를_합친다"]),
 ("조사가 사이트 값을 안 입힌다", MN,
  '                graph = flowmod.apply_site(graph, rows)\n                note += f" · 사이트 값 {len(rows)}개"',
  '                note += f" · 사이트 값 {len(rows)}개"',
  [CLI_GBM]),
 ("사이트 파일이 없어도 조사가 말하지 않는다", MN,
  '            if rows is None:\n                note += f" · 사이트 덮어쓰기 없음(`code graph --gbm {gbm}`으로 다시 만든다)"',
  '            if rows is None:\n                rows = []',
  ["tests/knowledge/test_cli_code.py::test_사이트_파일이_없으면_GBM_값_그대로_싣고_말한다"]),
 ("code status가 덮은 값 수를 안 센다", MN,
  '    rows = gb.read_site(out_dir, fct)\n    listed = ", ".join(meta.sites)',
  '    rows = []\n    listed = ", ".join(meta.sites)',
  ["tests/knowledge/test_cli_code.py::test_code_status와_flow는_그_사이트의_값으로_말한다"]),
 ("사이트 층의 되돌아가는 길이 400줄에서 자른다", D,
  '                got = await self._reader.show(known.repo, commit, path, whole=True)\n                text = got.data if',
  '                got = await self._reader.show(known.repo, commit, path)\n                text = got.data if',
  [f"{DCT}::test_스냅샷이_없으면_사이트_층도_통째로_읽는다"]),
 ("사이트 파일 행이 GBM 값을 안 든다", D,
  '                         "base": base_by.get((n.kind, n.key_path)),',
  '                         "base": None,',
  [f"{DCT}::test_site_overrides는_GBM_값과_다른_것만_적는다", CLI_GBM]),
 ("자원 이름 표가 같은 값도 바꾼다", FLW,
  '        if kind and base and value and str(base) != value:',
  '        if kind and base and value:',
  [f"{FLT}::test_덮은_값_행은_인덱스의_자원_이름을_바꿀_표도_준다"]),
 ("인덱스가 자원 이름을 안 바꾼다", IX,
  '        if not renames:\n            return self\n        symbols = []',
  '        if True:\n            return self\n        symbols = []',
  ["tests/knowledge/test_index.py::test_renamed는_자원_이름만_바꾼_새_인덱스를_준다", CLI_GBM]),
 ("조사가 인덱스에는 사이트 값을 안 입힌다", MN,
  '            elif rows:\n                # 인덱스의 함수별 자원도 그 사이트의 이름으로',
  '            elif False:\n                # 인덱스의 함수별 자원도 그 사이트의 이름으로',
  [CLI_GBM]),
 ("code uses가 GBM 값 그대로 묻는다", MN,
  '    if site_values:\n        from src.knowledge import flow',
  '    if False:\n        from src.knowledge import flow',
  [CLI_GBM]),
 ("code flow가 사이트 값을 안 입힌다", MN,
  '    graph = flow.apply_site(graph, gb.read_site(_graph_dir(args, env, gbm), fct) or [])\n    args.name, args.to =',
  '    args.name, args.to =',
  ["tests/knowledge/test_cli_code.py::test_code_status와_flow는_그_사이트의_값으로_말한다"]),
]

# ── 11e-3 — 이름·라우트 찾기를 스냅샷에서, graphify는 레포 병렬 ─────────────
PARITY = "tests/knowledge/test_graph_build.py::test_스냅샷_grep은_git_grep과_같은_Hit를_준다"
SNAP_HITS = f"{DCT}::test_흐름_히트는_스냅샷에서_찾고_git_grep은_안_띄운다"
MANY = "tests/knowledge/test_graph_build.py::test_graphify는_레포를_병렬로_돌리고_결과는_레포_순서다"
CASES += [
 ("이름 찾기가 스냅샷을 두고 git grep을 띄운다", D,
  '            snap, unseen = await self._snapshot_result(repo)\n            if snap is not None:\n                if progress:',
  '            snap, unseen = await self._snapshot_result(repo)\n            if False:\n                if progress:',
  [SNAP_HITS, "tests/knowledge/test_cli_code.py::test_code_graph가_심볼_인덱스를_쓰고_status와_check가_말한다"]),
 ("라우트 찾기가 스냅샷을 두고 git grep을 띄운다", D,
  '            snap, unseen = await self._snapshot_result(repo)\n            if snap is not None:\n                if unseen:\n                    notes.append(f"{repo}: 라우트 찾기가',
  '            snap, unseen = await self._snapshot_result(repo)\n            if False:\n                if unseen:\n                    notes.append(f"{repo}: 라우트 찾기가',
  [SNAP_HITS]),
 ("스냅샷 길이 못 본 submodule을 말하지 않는다", D,
  '            self._snapshots[key] = (got.data if ok else None, (got.envelope.truncated_reason or "") if ok else "")',
  '            self._snapshots[key] = (got.data if ok else None, "")',
  [f"{DCT}::test_흐름_히트는_스냅샷이_못_본_submodule을_사유로_남긴다"]),
 ("스냅샷 grep이 파일을 트리 순서로 안 본다", GBD,
  '    for path in sorted(files):\n        text = files[path]',
  '    for path in files:\n        text = files[path]',
  [PARITY]),
 ("스냅샷 grep이 이진 파일을 안 뺀다", GBD,
  '        if "\\x00" in text[:8000]:\n            continue',
  '        if False:\n            continue',
  [PARITY]),
 ("스냅샷 grep이 CR을 안 뗀다", GBD,
  '        lines = [line[:-1] if line.endswith("\\r") else line for line in lines]',
  '        lines = list(lines)',
  [PARITY]),
 ("스냅샷 grep의 문맥이 앞뒤가 아니다", GBD,
  '            around = [lines[k - 1] for k in (n - 1, n + 1) if k in visible]',
  '            around = [lines[k - 1] for k in (n + 1,) if k in visible]',
  [PARITY]),
 ("라우트 정규식이 git 패턴과 갈린다", FLW,
  'ROUTE_REGEXES = tuple(re.compile(p.replace("(", r"\\(")) for p in ROUTE_PATTERNS)',
  'ROUTE_REGEXES = tuple(re.compile(p.replace("(", r"\\(")) for p in ROUTE_PATTERNS[:-1])',
  [PARITY]),
 ("graphify 결과를 끝난 순서로 합친다", GBD,
  '        return list(pool.map(one, jobs))',
  '        return list(pool.map(one, jobs))[::-1]',
  [MANY]),
 ("graphify 폭이 항상 1이다", GBD,
  '    return max(1, min(4, os.cpu_count() or 1, n))',
  '    return 1',
  [MANY]),
 ("code graph가 graphify를 레포마다 차례로 돌린다", MN,
  '                    gb.run_graphify_many(jobs, binary, width=width, progress=lambda r, m: progress(f"{r}: {m}"))))',
  '                    [gb.run_graphify_at(d, s, binary, sc, progress=lambda m, r=n: progress(f"{r}: {m}")) for n, d, s, sc in jobs]))',
  ["tests/knowledge/test_cli_code.py::test_code_graph는_graphify를_레포_병렬_길로_돌리고_config_순서로_합친다"]),
]

# ── 12a-R1 — 사내 실측(sevt) 뒤: 시간 초과·트레이스 초·메서드 접두·degraded 표기·프록시 안내 ──
SL = ROOT / "src/config/schema_llm.py"
LD = ROOT / "src/application/lead.py"
DG = ROOT / "src/application/diagnose.py"
RPB = ROOT / "src/infrastructure/rest_prober.py"
CASES += [
 ("LLM 기본 상한이 60초로 돌아간다", SL,
  '    timeout_s: float = 300.0', '    timeout_s: float = 60.0',
  ["tests/config/test_schema_llm.py::test_기본_시간_상한은_생각하는_모델_기준이고_SDK_재시도는_없다"]),
 ("SDK 재시도 기본이 되살아난다", SL,
  '    max_retries: int = 0', '    max_retries: int = 2',
  ["tests/config/test_schema_llm.py::test_기본_시간_상한은_생각하는_모델_기준이고_SDK_재시도는_없다"]),
 ("시간 초과를 한 번 더 기다린다", LD,
  '        if timed_out:\n            break', '        if False:\n            break',
  ["tests/application/test_lead.py::test_시간_초과는_되묻지_않는다"]),
 ("시간 초과를 못 알아본다", LD,
  '    return bool(_TIMEOUT_WORDS.search(error))', '    return False',
  ["tests/application/test_lead.py::test_시간_초과는_되묻지_않는다"]),
 ("걸린 초가 트레이스로 안 간다", LD,
  '        _tell(on_exchange, asked, text, failure, latency, waited, source, note, stats)', '        _tell(on_exchange, asked, text, failure, None, waited, source, note, stats)',
  ["tests/application/test_lead.py::test_응답_시간이_트레이스로_간다"]),
 ("트레이스 파일에 응답 초를 안 적는다", MN,
  '        took = f"응답: {latency_s:.1f}초\\n" if latency_s is not None else ""', '        took = ""',
  ["tests/test_cli.py::test_트레이스_폴더는_실행마다_비운다"]),
 ("요약 머리줄이 응답 초를 안 올린다", TD,
  '           + (f" · 응답 {latency}초" if latency else "")', '',
  ["tests/application/test_trace_digest.py::test_요약_머리줄에_호출이_걸린_초가_붙는다"]),
 ("code.trace가 메서드 접두를 안 뗀다", D,
  '        endpoint = flowgraph.endpoint_path(endpoint)\n        node_id = flowgraph.endpoint_id(endpoint)',
  '        node_id = flowgraph.endpoint_id(endpoint)',
  [f"{DCT}::test_code_trace는_붙인_그래프의_끝점_사슬을_주고_없으면_실패로_답한다"]),
 ("degraded에 검증 통과가 붙는다", DG,
  '    if state.verdict is not None and state.verdict.verdict_type == "degraded":', '    if False:',
  ["tests/application/test_verdict.py::test_degraded_판정에는_검증_해당_없음이_붙는다"]),
 ("프록시 의심을 말하지 않는다", RPB,
  '            hint = proxy_suspicion(url) if isinstance(exc, httpx.TransportError) else ""', '            hint = ""',
  ["tests/infrastructure/test_rest_prober.py::test_프록시_env가_있고_호스트가_NO_PROXY에_없으면_의심을_말한다"]),
 ("NO_PROXY를 안 본다", RPB,
  '    if proxy_bypass_environment(host, {"no": no_proxy}):\n        return ""', '    if False:\n        return ""',
  ["tests/infrastructure/test_rest_prober.py::test_NO_PROXY에_있거나_프록시_env가_없으면_의심을_안_말한다"]),
]

# ── 12a-R2-1 — 역할별 LLM·토큰 상한·스트리밍 ─────────────────────────────
SAP = ROOT / "src/config/schema_app.py"
LCM = ROOT / "src/infrastructure/llm_chat_model.py"
LHT = ROOT / "src/infrastructure/llm_http.py"
ROLE_T = "tests/config/test_schema_app.py::test_역할별_LLM은_기본_위에_부분_덮어쓰기다"
CASES += [
 ("역할 덮어쓰기를 무시하고 기본만 쓴다", SAP,
  "        merged = _merged(_merged(base, _ROLE_DEFAULTS.get(role, {})), getattr(self.llm_roles, role))",
  "        merged = _merged(base, _ROLE_DEFAULTS.get(role, {}))",
  [ROLE_T]),
 ("역할 덮어쓰기가 중첩을 통째로 바꾼다", SAP,
  '        out[key] = _merged(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value',
  '        out[key] = value',
  [ROLE_T]),
 ("역할 덮어쓰기의 오류를 기동에서 안 잡는다", SAP,
  '        for role in used:\n            try:\n                self.llm_for(role)',
  '        for role in []:\n            try:\n                self.llm_for(role)',
  ["tests/config/test_schema_app.py::test_역할_덮어쓰기의_모르는_키와_모르는_역할은_막는다"]),
 ("describe가 실효 상한을 안 찍는다", SL,
  '        limits = (f" · 상한 {self.timeout_s:g}s · 재시도 {self.max_retries}"', '        limits = ("" + ""',
  ["tests/config/test_schema_llm.py::test_토큰_상한과_스트리밍은_config이고_설명에_실효값이_보인다"]),
 ("스트리밍 조각을 모으지 않는다", LCM,
  '                    parts.append(str(getattr(chunk, "content", "") or ""))', '                    pass',
  ["tests/infrastructure/test_llm_adapters.py::test_스트리밍이면_조각을_모아_한_답으로_주고_첫_토큰_시각을_적는다"]),
 ("첫 토큰 시각을 안 적는다", LCM,
  '                    if first is None:\n                        first = round(self._ticker() - started, 3)',
  '                    if False:\n                        first = round(self._ticker() - started, 3)',
  ["tests/infrastructure/test_llm_adapters.py::test_스트리밍이면_조각을_모아_한_답으로_주고_첫_토큰_시각을_적는다"]),
 ("chat_model이 토큰 상한을 안 보낸다", LCM,
  '            if self._cfg.max_tokens is not None:\n                extra["max_tokens"] = self._cfg.max_tokens',
  '            if False:\n                extra["max_tokens"] = self._cfg.max_tokens',
  ["tests/infrastructure/test_llm_adapters.py::test_토큰_상한이_요청에_실린다"]),
 ("http가 토큰 상한을 안 보낸다", LHT,
  '        if self._cfg.max_tokens is not None:\n            body["max_tokens"] = self._cfg.max_tokens',
  '        if False:\n            body["max_tokens"] = self._cfg.max_tokens',
  ["tests/infrastructure/test_llm_adapters.py::test_토큰_상한이_요청에_실린다"]),
 ("판정 턴이 액션 LLM에게 간다", LD,
  '        got = await ask_json(conclude_llm or llm, prompt, ConcludeReply, on_exchange=_hook("conclude", state))',
  '        got = await ask_json(llm, prompt, ConcludeReply, on_exchange=_hook("conclude", state))',
  ["tests/application/test_lead.py::test_판정_턴은_다른_LLM을_쓸_수_있다", "tests/application/test_lead.py::test_CLI가_역할별_LLM을_따로_꽂는다"]),
 ("investigate가 판정 LLM을 안 꽂는다", MN,
  '                flow_graph=flow_graph, code_index=code.has_index() if code else False,\n                conclude_llm=conclude_llm,',
  '                flow_graph=flow_graph, code_index=code.has_index() if code else False,\n                conclude_llm=None,',
  ["tests/application/test_lead.py::test_CLI가_역할별_LLM을_따로_꽂는다"]),
 ("llm describe가 역할을 안 찍는다", MN,
  '        if cfg is not base:\n            print(f"  역할 {role}: {cfg.describe()}")',
  '        if False:\n            print(f"  역할 {role}: {cfg.describe()}")',
  ["tests/test_cli.py::test_llm_describe는_역할별_실효값을_찍고_ask는_역할을_고른다"]),
]

# ── 12a-R2-2a — 엔진 P0: llm_error 뒤 판정 1회·사실 서술·미실행·중복 사유·열린 질문 ──
VT = "tests/application/test_verdict.py"
CASES += [
 ("llm_error 뒤 판정이 와도 degraded로 찍는다", ND,
  '            if reply.get("verdict") is not None:\n                return {"verdict": sanitize_verdict(resolve_task_citations(reply["verdict"], state.plan_tasks)),',
  '            if False:\n                return {"verdict": sanitize_verdict(resolve_task_citations(reply["verdict"], state.plan_tasks)),',
  [f"{VT}::test_llm_error여도_증거가_있으면_판정을_한_번_묻는다"]),
 ("증거가 없어도 죽은 리드 뒤에 판정을 묻는다", ND,
  '        if not state.evidence:\n            why = (', '        if False:\n            why = (',
  [f"{VT}::test_증거가_없으면_죽은_리드_뒤에_판정을_묻지_않는다"]),
 ("degraded 서술이 읽기 수를 안 센다", ND,
  '    return (f"조사 중단 — {why}. {state.round}라운드 · 읽기 {len(ran)}회(성공 {good}) · "',
  '    return (f"조사 중단 — {why}. {state.round}라운드 · 읽기 0회(성공 {good}) · "',
  [f"{VT}::test_llm_error여도_증거가_있으면_판정을_한_번_묻는다"]),
 ("미실행 태스크를 안 센다", ROOT / "src/application/diagnose.py",
  '    if unrun:\n        lines.append(f"  미실행', '    if False:\n        lines.append(f"  미실행',
  ["tests/application/test_diagnose.py::test_끝까지_안_돈_태스크를_미실행으로_센다"]),
 ("중복 거부 사유에 증거 id를 안 적는다", ND,
  '            where = (evidence_of or {}).get(query) or []',
  '            where = []',
  ["tests/application/test_nodes.py::test_중복_질의_거부_사유에_기존_증거_id가_있다"]),
 ("열린 질문이 잘린 증거를 안 적는다", B,
  '    if cut:\n        lines.append(f"- 잘린 증거', '    if False:\n        lines.append(f"- 잘린 증거',
  ["tests/application/test_briefing.py::test_열린_질문_블록은_잘린_증거_실패한_읽기_거부된_중복을_모은다"]),
 ("열린 질문이 실패한 읽기를 안 적는다", B,
  '    if failed:\n        lines.append(f"- 실패한 읽기', '    if False:\n        lines.append(f"- 실패한 읽기',
  ["tests/application/test_briefing.py::test_열린_질문_블록은_잘린_증거_실패한_읽기_거부된_중복을_모은다"]),
 ("열린 질문이 거부된 중복을 안 적는다", B,
  '    if dup:\n        found = sorted(', '    if False:\n        found = sorted(',
  ["tests/application/test_briefing.py::test_열린_질문_블록은_잘린_증거_실패한_읽기_거부된_중복을_모은다"]),
 ("integrate 재료에 열린 질문이 없다", B,
  '            "rejected": rejected_block(state),\n            "open": open_questions_block(state),',
  '            "rejected": rejected_block(state),',
  ["tests/application/test_briefing.py::test_열린_질문은_integrate와_conclude_프롬프트에_자리가_있다"]),
]

# ── 12a-R2-2b-1 — 도구: 골라서 전부(jsonpath·redis path·code.read 범위·code.config key·grep 코드 줄 먼저·예시 하나) ──
JP = ROOT / "src/domain/jsonpath.py"
AC = ROOT / "src/domain/actions.py"
RR = ROOT / "src/infrastructure/redis_reader.py"
ST = ROOT / "src/infrastructure/stubs.py"
IT = ROOT / "config/prompts/investigate-integrate.md"
JPT = "tests/domain/test_jsonpath.py"
DCT2 = "tests/infrastructure/test_deployed_code.py"
NARROW_T = "tests/domain/test_actions.py::test_범위_경로_키_인자는_선택이다"
CASES += [
 ("jsonpath가 없는 키에서 있는 키를 안 말한다", JP,
  '                return False, f"{where}에 {key!r}가 없다 — 있는 키: {_keys(cur)}"',
  '                return False, f"{where}에 {key!r}가 없다"',
  [f"{JPT}::test_없는_자리는_있는_것을_알려_주며_실패한다"]),
 ("jsonpath가 JSON 문자열을 안 푼다", JP,
  "    if isinstance(value, str):\n        try:\n            value = json.loads(value)",
  "    if False:\n        try:\n            value = json.loads(value)",
  [f"{JPT}::test_JSON_문자열이면_먼저_푼다"]),
 ("jsonpath가 목록 범위를 안 본다", JP,
  "            if n >= len(cur):", "            if False:",
  [f"{JPT}::test_없는_자리는_있는_것을_알려_주며_실패한다"]),
 ("stub redis가 path를 무시한다", ST,
  '        if path is None:\n            return ProbeResult.succeeded({"type": "string", "value": found}',
  '        if True:\n            return ProbeResult.succeeded({"type": "string", "value": found}',
  ["tests/infrastructure/test_stubs.py::test_stub_redis_get은_JSON_경로로_고른_부분만_준다"]),
 ("실제 redis가 path를 무시한다", RR,
  '            if path is None:\n                return ProbeResult.succeeded({"type": kind, "value": data},',
  '            if True:\n                return ProbeResult.succeeded({"type": kind, "value": data},',
  ["tests/infrastructure/test_redis_reader.py::test_path를_주면_JSON_값_안의_그_자리만_준다"]),
 ("redis.get에 path가 등재돼 있지 않다", AC,
  '("redis", "get",           ("key",),                ("path",)),',
  '("redis", "get",           ("key",),                ()),',
  [NARROW_T]),
 ("좁힘 판정이 필수 인자 이름도 센다", AC,
  "    return bool(spec and NARROWING_ARGS & set(spec[3]) & set(params))",
  "    return bool(spec and NARROWING_ARGS & set(params))",
  [NARROW_T]),
 ("좁힌 읽기도 증거 한 건 예산에서 자른다", RP,
  "        body, ours = detail(result.data, limit=(self._narrowed_chars if narrowed(task.action, task.params)",
  "        body, ours = detail(result.data, limit=(self._detail_chars if narrowed(task.action, task.params)",
  ["tests/application/test_runner_probe.py::test_경로로_고른_redis_값은_예산에_안_잘린다"]),
 ("code.read가 범위를 무시한다", D,
  "        picked = lines[start - 1:start - 1 + count]", "        picked = lines[:count]",
  [f"{DCT2}::test_code_read는_범위를_주면_그_범위를_전부_준다"]),
 ("code.read가 파일 끝 너머 offset을 오류로 안 본다", D,
  "        if start > len(lines):", "        if False:",
  [f"{DCT2}::test_code_read는_범위를_주면_그_범위를_전부_준다"]),
 ("code.read가 offset 0을 거르지 않는다", D,
  "        if isinstance(value, bool) or not isinstance(value, int) or value < floor:",
  "        if isinstance(value, bool) or not isinstance(value, int):",
  [f"{DCT2}::test_code_read는_범위를_주면_그_범위를_전부_준다"]),
 ("code.config가 key를 무시한다", D,
  "        if key:\n            ok, picked = select(merged, key)", "        if False:\n            ok, picked = select(merged, key)",
  [f"{DCT2}::test_code_config는_key로_좁히고_크면_키_지도를_준다"]),
 ("code.config가 큰 덤프를 그대로 싣는다", D,
  "        elif len(json.dumps(merged, ensure_ascii=False)) > _CONFIG_INLINE_CHARS:", "        elif False:",
  [f"{DCT2}::test_code_config는_key로_좁히고_크면_키_지도를_준다"]),
 ("키 지도가 큰 부채꼴 안으로 내려간다", D,
  "            if isinstance(v, dict) and left > 1 and 0 < len(v) <= _MAP_FANOUT:",
  "            if isinstance(v, dict) and left > 1 and 0 < len(v):",
  [f"{DCT2}::test_400줄이_넘는_층도_통째로_읽는다"]),
 ("grep이 문서 줄을 뒤로 안 보낸다", D,
  "        (docs if path.lower().endswith(_DOC_SUFFIXES) else code).append(line)", "        code.append(line)",
  [f"{DCT2}::test_code_grep은_코드_줄을_먼저_문서_줄을_뒤에_둔다"]),
 ("grep을 리더 기본 상한으로 받는다", D,
  "                                          max_lines=_GREP_RAW_LINES, max_chars=_GREP_RAW_CHARS)",
  "                                          )",
  [f"{DCT2}::test_code_grep은_코드_줄을_먼저_문서_줄을_뒤에_둔다"]),
 ("integrate 예시가 읽기를 둘 보여 준다", B,
  "        shapes = (_available(site_config, fresh, 1, services)\n                  or _available(site_config, _named_reads(services), 1, services))",
  "        shapes = (_available(site_config, fresh, 2, services)\n                  or _available(site_config, _named_reads(services), 2, services))",
  ["tests/application/test_briefing.py::test_integrate_예시는_사다리_수와_읽기_하나뿐이다"]),
 ("예시 회전이 좁힐 수 있는 것을 먼저 둔다", B,
  "        fresh = unused + again", "        fresh = again + unused",
  ["tests/application/test_briefing.py::test_예시_회전에_컨슈머_lag_읽기가_있다"]),
 ("integrate 규칙에 좁혀 읽기 줄이 없다", IT,
  "- **큰 것은 잘라 보지 말고 골라서 전부 봐라.**", "- **큰 것은 잘 봐라.**",
  ["tests/application/test_briefing.py::test_좁혀_읽기_규칙이_integrate_프롬프트에_있다"]),
]

# ── 12a-R2-2b-2 — 증거 모양: 대상 행 먼저·항목당 한 줄·찾은 이름·선언됐는데 없는 키·끝점 줄 고정·이름 순서 ──
FC = ROOT / "src/application/facts.py"
LC = ROOT / "tools/local_case.py"
RPT = "tests/application/test_runner_probe.py"
GT = "tests/application/test_graph.py"
FT2 = "tests/knowledge/test_flow.py"
CASES += [
 ("대상 행을 앞에 안 둔다", RP,
  "        hits = [i for i, text in enumerate(texts) if focus and all(value in text for value in focus)]",
  "        hits = []",
  [f"{RPT}::test_대상_값이_든_행을_앞에_통째로_두고_나머지는_한_줄씩"]),
 ("target의 식별 값을 안 푼다", RP,
  '    return tuple(part for part in target.split("/") if len(part) >= 2)', "    return ()",
  [f"{RPT}::test_대상_값이_든_행을_앞에_통째로_두고_나머지는_한_줄씩"]),
 ("dict 안의 문서 목록을 한 줄로 눕힌다", RP,
  "        if _doc_list(value):", "        if False:",
  [f"{RPT}::test_rest_응답의_response_목록은_항목당_한_줄이다"]),
 ("발견 읽기의 이름을 안 남긴다", RP,
  "                           found=_found(task.action, result.data))", "                           found=[])",
  [f"{RPT}::test_발견_읽기는_찾은_이름을_outcome에_싣는다"]),
 ("잘린 scan으로도 없다고 한다", FC,
  "            or not outcome.evidence[0].complete or not declared.get(kind)):", "            or not declared.get(kind)):",
  [f"{GT}::test_패턴이_안_덮는_키와_잘린_scan은_없다고_하지_않는다"]),
 ("패턴이 안 덮는 키도 없다고 한다", FC,
  "    return head.startswith(prefix) or prefix.startswith(head)", "    return True",
  [f"{GT}::test_패턴이_안_덮는_키와_잘린_scan은_없다고_하지_않는다"]),
 ("템플릿 키를 앞부분으로 안 맞춘다", FC,
  "        return any(name.startswith(head) for name in found)", "        return False",
  [f"{GT}::test_완전한_scan에_선언된_키가_없으면_사실로_남긴다"]),
 ("사실을 State에 안 남긴다", ND,
  '        return {"plan_tasks": [done], "evidence": list(outcome.evidence), "facts": facts}',
  '        return {"plan_tasks": [done], "evidence": list(outcome.evidence)}',
  [f"{GT}::test_완전한_scan에_선언된_키가_없으면_사실로_남긴다"]),
 ("사실을 태스크 요약에 안 붙인다", ND,
  '            "result_summary": " · ".join([outcome.summary, *facts]) if facts else (outcome.summary or None),',
  '            "result_summary": outcome.summary or None,',
  [f"{GT}::test_완전한_scan에_선언된_키가_없으면_사실로_남긴다"]),
 ("끝점 씨앗 줄을 예산 안에 둔다", FLW,
  '                (pinned if by_id[sid]["type"] == "endpoint" else lines).append(line_for(sid))',
  "                lines.append(line_for(sid))",
  [f"{FT2}::test_흐름_텍스트는_끝점_씨앗_줄을_맨_앞에_두고_예산에_센다"]),
 ("이름을 알파벳순으로만 둔다", FLW,
  "        names = sorted(names, key=lambda n: (0 if _preferred(n, prefer) else 1, n))", "        names = sorted(names)",
  [f"{FT2}::test_이름_목록은_prefer_단어가_든_것이_먼저다"]),
 ("끝점이 읽는 키가 있어도 전부를 준다", FLW,
  "        if keys:\n            return tuple(keys)", "        if False:\n            return tuple(keys)",
  [f"{FT2}::test_선언된_키는_끝점이_읽는_것_없으면_config의_전부다"]),
 ("끝점을 모르면 선언된 키를 안 준다", FLW,
  '    return tuple(sorted({n["label"] for n in graph.get("nodes", []) if n.get("type") == "rediskey"}))', "    return ()",
  [f"{FT2}::test_선언된_키는_끝점이_읽는_것_없으면_config의_전부다"]),
 ("열린 질문에 사실이 없다", B,
  '        lines.append(f"- {fact}")\n    cut = [', "        pass\n    cut = [",
  ["tests/application/test_briefing.py::test_열린_질문에_코드가_남긴_사실이_실린다"]),
 ("같은 이름의 사실을 두 번 싣는다", B,
  "        if names in seen:\n            continue", "        if False:\n            continue",
  ["tests/application/test_briefing.py::test_열린_질문에_코드가_남긴_사실이_실린다"]),
 ("흐름 블록이 케이스 단어를 안 넘긴다", B,
  "                               texts=(origin_line(state.case, site_config) or \"\",), prefer=((target.key,) if target.key else ())),\n            \"example\"",
  "                               texts=(origin_line(state.case, site_config) or \"\",)),\n            \"example\"",
  ["tests/application/test_briefing.py::test_케이스_블록이_입구_노드가_고른_대상_키를_근거와_함께_말하고_흐름_줄은_그_키를_앞세운다"]),
 ("cache-missing 변형이 캐시 키를 만든다", LC,
  '            **({} if variant == "cache-missing" else', '            **({} if False else',
  ["tests/test_local_case.py::test_cache_missing은_캐시_키가_아예_없고_배지는_0이다"]),
]

# ── 12a-R2-2c — 운영: LLM 클라이언트 닫기·종료 소음 거름망 ──
LCM2 = ROOT / "src/infrastructure/llm_chat_model.py"
CASES += [
 ("chat_model이 httpx 클라이언트를 안 닫는다", LCM2,
  '                await client.aclose() if hasattr(client, "aclose") else client.close()', "                continue",
  ["tests/infrastructure/test_llm_adapters.py::test_close는_chat_model의_httpx_클라이언트를_닫는다"]),
 ("조사가 끝나도 LLM을 안 닫는다", MN,
  "            await adapters.close()\n            await _close_llms(llm, conclude_llm)", "            await adapters.close()",
  ["tests/application/test_lead.py::test_CLI가_역할별_LLM을_따로_꽂는다"]),
 ("거름망이 태스크 예외까지 삼킨다", MN,
  "    if transport_level and quiet:", "    if quiet:",
  ["tests/test_cli.py::test_종료_소음_처리기는_transport의_끊김만_거른다"]),
 ("unraisable 거름망이 다른 객체의 것도 삼킨다", MN,
  '        if "_ProactorBasePipeTransport" in where and (', "        if (",
  ["tests/test_cli.py::test_unraisable_거름망은_proactor_transport의_닫힌_루프만_삼킨다"]),
 ("_run이 거름망을 안 단다", MN,
  "        asyncio.get_running_loop().set_exception_handler(_shutdown_noise_handler)", "        pass",
  ["tests/test_cli.py::test_run은_루프에_종료_소음_처리기를_단다"]),
]

# ── 12a-R2-3 ① — llm check 한 번만 닫기 · 사실 머리줄 · kafka tail 루프 ──
KI = ROOT / "src/infrastructure/kafka_inspector.py"
CASES += [
 ("llm check가 질문마다 닫는다", MN,
  "                reply = await llm.ask(prompt)\n                reported = reported or reply.reported_model",
  "                reply = await llm.ask(prompt)\n                await _close_llms(llm)\n                reported = reported or reply.reported_model",
  ["tests/test_cli.py::test_llm_check는_끝에서_한_번만_닫는다"]),
 ("LLM 오류 머리줄이 판정을 안 적는다", DG,
  '        verdict = f"판정 {state.verdict.verdict_type}" if state.verdict is not None else "판정 없음"\n        return head + f"조사 중단(llm_error) · {verdict}"',
  '        return head + "**이 조사는 안 돌았다**"',
  ["tests/application/test_diagnose.py::test_LLM_오류_머리줄은_판정_유무를_사실대로_적는다"]),
 ("kafka tail이 getmany를 한 번만 부른다", KI,
  "                records.extend(got)", "                records.extend(got)\n                break",
  ["tests/infrastructure/test_kafka_inspector.py::test_tail은_limit을_채울_때까지_getmany를_돈다"]),
]

# ── 12a-R2-3 ② — 429 대기·호출 간격·트레이스의 대기 초 ──
PC = ROOT / "src/infrastructure/llm_pacing.py"
LH = ROOT / "src/infrastructure/llm_http.py"
PCT = "tests/infrastructure/test_llm_pacing.py"
LAT = "tests/infrastructure/test_llm_adapters.py"
CASES += [
 ("epoch 밀리초를 초로 읽는다", PC,
  "        seconds = raw / 1000.0 if raw > 1e11 else float(raw)", "        seconds = float(raw)",
  [f"{PCT}::test_nextAccessTime을_여러_모양으로_읽는다"]),
 ("naive 시각에 시간대를 안 붙인다", PC,
  "        if when.tzinfo is None:\n            when = when.replace(tzinfo=timezone.utc if named_utc else tz)", "        if False:\n            when = when.replace(tzinfo=timezone.utc if named_utc else tz)",
  [f"{PCT}::test_nextAccessTime을_여러_모양으로_읽는다"]),
 ("429 대기를 상한으로 안 자른다", PC,
  "    return min(max(0.0, wait), cap)", "    return max(0.0, wait)",
  [f"{LAT}::test_시각이_없으면_Retry_After나_기본값을_상한_안에서_기다린다"]),
 ("pacer가 간격을 안 기다린다", PC,
  "            if remaining > 0:\n                await sleep(remaining)", "            if False:\n                await sleep(remaining)",
  [f"{PCT}::test_pacer는_게이트웨이_단위로_최소_간격을_지킨다"]),
 ("끝 슬래시만 다른 게이트웨이를 둘로 센다", PC,
  '    key = (base_url or "").rstrip("/")', '    key = base_url or ""',
  [f"{PCT}::test_pacer는_게이트웨이_단위로_최소_간격을_지킨다"]),
 ("http 어댑터가 429 뒤 안 기다린다", LH,
  "            if quota is None or attempt:", "            if True:",
  [f"{LAT}::test_429면_nextAccessTime까지_기다렸다_한_번_다시_묻는다"]),
 ("chat_model 어댑터가 429 뒤 안 기다린다", LCM2,
  "            if quota is None or attempt:", "            if True:",
  [f"{LAT}::test_429면_nextAccessTime까지_기다렸다_한_번_다시_묻는다"]),
 ("chat_model이 429 예외를 못 알아본다", LCM2,
  '    if getattr(exc, "status_code", None) != 429:\n        return None', "    if True:\n        return None",
  [f"{LAT}::test_429면_nextAccessTime까지_기다렸다_한_번_다시_묻는다"]),
 ("리드가 대기 초를 트레이스에 안 넘긴다", LD,
  "            waited = reply.waited_s or None", "            waited = None",
  ["tests/application/test_lead.py::test_트레이스가_시도마다_날것을_건넨다"]),
 ("트레이스 파일이 대기 초를 안 적는다", MN,
  '        took += f"대기: {waited_s:.1f}초(429{\'·\' + wait_source if wait_source else \'\'})\\n" if waited_s else ""', '        took += ""',
  ["tests/test_cli.py::test_트레이스가_429_대기_초를_적는다"]),
 ("요약 머리줄에 대기가 없다", TD,
  '           + (f" · 대기 {waited}초" if waited else "")', "",
  ["tests/application/test_trace_digest.py::test_요약_머리줄과_끝줄에_429_대기가_붙는다"]),
 ("요약 끝줄에 429 합계가 없다", TD,
  "    if waits:", "    if False:",
  ["tests/application/test_trace_digest.py::test_요약_머리줄과_끝줄에_429_대기가_붙는다"]),
 ("음수 간격을 받는다", ROOT / "src/config/schema_llm.py",
  '        if v < 0:\n            raise ValueError(f"min_interval_s는 0 이상 — {v}")', '        if False:\n            raise ValueError(f"min_interval_s는 0 이상 — {v}")',
  ["tests/config/test_schema_llm.py::test_호출_간격과_429_대기_상한은_음수가_아니고_describe에_보인다"]),
]

# ── 12a-R2-3 ③ — 답 스키마를 response_format으로 ──
LLS = ROOT / "src/domain/llm_schema.py"
LFM = ROOT / "src/infrastructure/llm_format.py"
LLST = "tests/domain/test_llm_schema.py"
FMT_T = "tests/infrastructure/test_llm_adapters.py::test_스키마를_주면_response_format으로_나가고_닫힌_것만_strict다"
CASES += [
 ("선택 속성이 남아도 닫힌 것으로 본다", LLS,
  '            if set(schema.get("required", ())) != set(props):', "            if False:",
  [f"{LLST}::test_닫힌_스키마만_strict로_보낼_수_있다"]),
 ("strictify가 전부를 required로 안 만든다", LLS,
  '                node["required"] = list(node["properties"])', "                pass",
  [f"{LLST}::test_strictify는_전부_required로_닫고_default를_지우고_null은_남긴다"]),
 ("strictify가 default를 안 지운다", LLS,
  '            node.pop("default", None)', "            pass",
  [f"{LLST}::test_strictify는_전부_required로_닫고_default를_지우고_null은_남긴다"]),
 ("json_object 설정을 무시한다", LFM,
  '    if mode == "json_object":\n        return {"type": "json_object"}', '    if False:\n        return {"type": "json_object"}',
  [FMT_T]),
 ("닫힌 스키마도 strict로 안 보낸다", LFM,
  '"strict": is_closed(schema)}}', '"strict": False}}',
  [FMT_T]),
 ("http 어댑터가 response_format을 안 보낸다", LH,
  '        if fmt is not None:\n            body["response_format"] = fmt', '        if False:\n            body["response_format"] = fmt',
  [FMT_T]),
 ("chat_model 어댑터가 response_format을 안 보낸다", LCM2,
  '        extra = {"response_format": fmt} if fmt is not None else {}', "        extra = {}",
  [FMT_T]),
 ("리드가 스키마를 어댑터에 안 넘긴다", LD,
  "            reply = await llm.ask(asked, schema=use_schema)", "            reply = await llm.ask(asked)",
  ["tests/application/test_lead.py::test_ask_json은_답_스키마를_어댑터에_넘긴다"]),
 ("판정 스키마를 안 닫는다", LD,
  "    schema = strictify(model.model_json_schema())", "    schema = model.model_json_schema()",
  ["tests/application/test_lead.py::test_답_스키마는_턴마다_모양이_다르고_판정만_닫힌다"]),
 ("response_format 기본이 none이다", ROOT / "src/config/schema_llm.py",
  '    response_format: Literal["json_schema", "json_object", "none"] = "json_schema"',
  '    response_format: Literal["json_schema", "json_object", "none"] = "none"',
  ["tests/config/test_schema_llm.py::test_response_format은_셋_중_하나이고_describe에_보인다"]),
]

# ── 12a-R2-3 ④ — stream 기본 켬 ──
CASES += [
 ("stream 기본값이 chat_model에서도 꺼져 있다", ROOT / "src/config/schema_llm.py",
  '            self.stream = self.adapter == "chat_model"', "            self.stream = False",
  ["tests/config/test_schema_llm.py::test_stream_기본값은_chat_model이면_켬_http면_끔이고_명시가_이긴다"]),
]

# ── 12a-R2-3 ⑤ — 프롬프트 상한 ──
CASES += [
 ("프롬프트 상한을 넘어도 증거를 안 접는다", LD,
  "    while len(prompt) > cap and budget > _EVIDENCE_FLOOR:", "    while False:",
  ["tests/application/test_lead.py::test_프롬프트_상한을_넘으면_오래된_증거부터_접고_최신은_통째로_남는다"]),
 ("증거 바닥 뒤에도 태스크를 안 접는다", LD,
  "    if len(prompt) > cap:\n        prompt = render(budget, True)", "    if False:\n        prompt = render(budget, True)",
  ["tests/application/test_lead.py::test_증거를_바닥까지_접어도_넘으면_끝난_태스크_줄을_접는다"]),
 ("integrate가 상한을 안 넘긴다", LD,
  '        prompt, _ = fit_prompt(render, cap=(prompt_caps or {}).get("integrate"), budget=evidence_budget)',
  "        prompt, _ = fit_prompt(render, cap=None, budget=evidence_budget)",
  ["tests/application/test_lead.py::test_프롬프트_상한을_넘으면_오래된_증거부터_접고_최신은_통째로_남는다"]),
 ("태스크 접기가 끝난 것을 안 줄인다", B,
  '        if fold and task.status in ("ok", "error"):', "        if False:",
  ["tests/application/test_briefing.py::test_태스크_블록을_접으면_끝난_것만_짧아지고_대기는_그대로다"]),
 ("프롬프트 상한 기본값이 다르다", SAP,
  "    integrate_prompt_chars: int = Field(default=8000, ge=2000)", "    integrate_prompt_chars: int = Field(default=80000, ge=2000)",
  ["tests/config/test_schema_app.py::test_프롬프트_상한은_integrate_8000_conclude_10000이고_2000_아래는_막는다"]),
]

# ── 12a-R2-3 ⑥ — verify component는 서비스만 · integrate 규칙 두 줄 ──
VT2 = "tests/application/test_verdict.py::test_verify_노드의_component는_토폴로지_서비스여야_하고_external만_증거의_이름을_허용한다"
CASES += [
 ("증거에 나온 키 이름을 원인으로 통과시킨다", ND,
  '        if deps.components and verdict.verdict_type != "external":', "        if False:",
  [VT2]),
 ("external 판정에도 서비스만 허용한다", ND,
  '        if deps.components and verdict.verdict_type != "external":', '        if deps.components and verdict.verdict_type != "__none__":',
  [VT2]),
 ("빈 값의 다음 홉 규칙이 없다", IT,
  "- **값이 비었으면**(빈 배열·0건) 다음 홉은", "- 값이 차 있으면 다음 홉은",
  ["tests/application/test_briefing.py::test_integrate_규칙에_빈_값의_다음_홉과_인자_규칙이_있다"]),
 ("인자 규칙이 없다", IT,
  "- **인자는 그 읽기의 것만.**", "- **인자.**",
  ["tests/application/test_briefing.py::test_integrate_규칙에_빈_값의_다음_홉과_인자_규칙이_있다"]),
]

# ── 12a-R2-4 ① — 10054는 콜백 실패로도 온다 ──
CASES += [
 ("거름망이 proactor 콜백 실패를 못 알아본다", MN,
  '    transport_level = transport_level or _callback_name(context.get("handle")).endswith(',
  '    transport_level = transport_level or False and _callback_name(context.get("handle")).endswith(',
  ["tests/test_cli.py::test_종료_소음_처리기는_proactor의_connection_lost_콜백_실패도_거른다"]),
 ("거름망이 아무 콜백의 connection_lost나 삼킨다", MN,
  '        "_ProactorBasePipeTransport._call_connection_lost")', '        "_call_connection_lost")',
  ["tests/test_cli.py::test_종료_소음_처리기는_proactor의_connection_lost_콜백_실패도_거른다"]),
]

# ── 12a-R2-4 ② — 429 시각 형식·원문 본문·대기 근거 ──
PCT2 = "tests/infrastructure/test_llm_pacing.py"
CASES += [
 ("월 약어를 숫자로 안 바꾼다", PC,
  "    if match and match.group(2).lower() in _MONTHS:", "    if False:",
  [f"{PCT2}::test_사내_게이트웨이의_월_약어_시각을_읽는다"]),
 ("끝의 UTC를 안 뗀다", PC,
  '    text = _ZONE_NAME.sub("", text)', "    pass",
  [f"{PCT2}::test_사내_게이트웨이의_월_약어_시각을_읽는다"]),
 ("오프셋 없는 UTC를 로컬 시간대로 본다", PC,
  "        when = when.replace(tzinfo=timezone.utc if named_utc else tz)", "        when = when.replace(tzinfo=tz)",
  [f"{PCT2}::test_사내_게이트웨이의_월_약어_시각을_읽는다"]),
 ("대기 근거를 안 준다", PC,
  '        wait, source = _RATE_FALLBACK_S, "기본값"', '        wait, source = _RATE_FALLBACK_S, "nextAccessTime"',
  [f"{PCT2}::test_quota_wait는_기다린_근거를_같이_준다"]),
 ("chat_model이 429 원문 본문을 안 본다", LCM2,
  '    return {"sdk": getattr(exc, "body", None), "raw": raw}, getattr(response, "headers", None)',
  '    return {"sdk": getattr(exc, "body", None)}, getattr(response, "headers", None)',
  ["tests/infrastructure/test_llm_adapters.py::test_error_옆에_있는_nextAccessTime도_원문에서_찾는다"]),
 ("트레이스 대기 줄에 근거가 없다", MN,
  "        took += f\"대기: {waited_s:.1f}초(429{'·' + wait_source if wait_source else ''})\\n\" if waited_s else \"\"",
  "        took += f\"대기: {waited_s:.1f}초(429)\\n\" if waited_s else \"\"",
  ["tests/test_cli.py::test_트레이스_대기_줄에_근거가_붙는다"]),
 ("요약 끝줄에 근거별 횟수가 없다", TD,
  '        why = " · 근거 " + " · ".join(f"{k} {v}" for k, v in sorted(sources.items())) if sources else ""',
  '        why = ""',
  ["tests/application/test_trace_digest.py::test_요약_끝줄에_429_대기_근거별_횟수가_붙는다"]),
]

# ── 12a-R2-4 ③ — kafka tail은 빈 배치 한 번에 멈추지 않는다 ──
KIT = "tests/infrastructure/test_kafka_inspector.py"
CASES += [
 ("kafka tail이 첫 빈 배치에 멈춘다", KI,
  "                    if empties >= EMPTY_POLLS:\n                        break", "                    if True:\n                        break",
  [f"{KIT}::test_tail은_첫_배치가_비어도_멈추지_않는다"]),
 ("kafka tail이 빈 배치가 이어져도 안 멈춘다", KI,
  "                    if empties >= EMPTY_POLLS:\n                        break", "                    if False:\n                        break",
  [f"{KIT}::test_tail은_빈_배치가_연속_세_번이면_멈추고_모자란다고_적는다"]),
 ("kafka tail이 받은 뒤 빈 횟수를 안 지운다", KI,
  "                empties = 0\n                records.extend(got)", "                records.extend(got)",
  [f"{KIT}::test_tail은_받는_사이사이_빈_배치는_연속으로_세지_않는다"]),
]

# ── 12a-R2-4 ④ — 판정 역할의 기본 형식 none · 깨진 답의 재시도는 스키마 없이 ──
ROLE_RF = "tests/config/test_schema_app.py::test_판정_역할은_따로_안_적으면_response_format이_none이다"
CASES += [
 ("판정 역할도 기본 형식이 json_schema다", SAP,
  '_ROLE_DEFAULTS: dict[str, dict] = {"conclude": {"response_format": "none", "stream_idle_s": 170.0}}', '_ROLE_DEFAULTS: dict[str, dict] = {"conclude": {"stream_idle_s": 170.0}}',
  [ROLE_RF]),
 ("역할 기본값이 덮어쓰기를 이긴다", SAP,
  "        merged = _merged(_merged(base, _ROLE_DEFAULTS.get(role, {})), getattr(self.llm_roles, role))",
  "        merged = _merged(_merged(base, getattr(self.llm_roles, role)), _ROLE_DEFAULTS.get(role, {}))",
  [ROLE_RF]),
 ("바뀌는 게 없어도 새 설정 객체를 만든다", SAP,
  "        if merged == base:\n            return self.llm", "        if False:\n            return self.llm",
  [ROLE_RF]),
 ("깨진 답의 재시도에도 스키마를 건다", LD,
  "            use_schema = None", "            pass",
  ["tests/application/test_lead.py::test_스키마를_건_답이_깨지면_재시도는_스키마_없이_묻는다"]),
 ("호출 실패의 재시도는 스키마를 뗀다", LD,
  "            await asyncio.sleep(RETRY_BACKOFF_S)\n            asked = prompt",
  "            await asyncio.sleep(RETRY_BACKOFF_S)\n            asked = prompt\n            use_schema = None",
  ["tests/application/test_lead.py::test_호출이_실패한_재시도는_스키마를_그대로_건다"]),
]

# ── 12a-R2-4 ⑤ — 고정부 줄이기 ──
BT5 = "tests/application/test_briefing.py"
CASES += [
 ("서비스 설명을 통째로 싣는다", B,
  "        briefs = {name: _role_brief(roles[name]) for name in services if roles.get(name)}",
  "        briefs = {name: roles[name] for name in services if roles.get(name)}",
  [f"{BT5}::test_읽기_목록의_서비스_설명은_첫_구절_40자까지다"]),
 ("잘린 설명이라고 안 적는다", B,
  '                                + (" — 설명 전문은 code.services" if clipped else "") + ")")',
  '                                + ")")',
  [f"{BT5}::test_읽기_목록의_서비스_설명은_첫_구절_40자까지다"]),
 ("조건부 규칙 줄을 안 뺀다", LD,
  "            dropping = fields.get(mark.group(1), \"\").strip() in _EMPTY_BLOCK",
  "            dropping = False",
  [f"{BT5}::test_fill의_조건부_줄은_이어지는_들여쓴_줄까지_같이_빠진다", f"{BT5}::test_해당_없는_규칙_줄은_프롬프트에서_빠진다"]),
 ("조건부 규칙의 이어지는 줄을 남긴다", LD,
  '        if dropping and line.startswith("  "):\n            continue', '        if False:\n            continue',
  [f"{BT5}::test_fill의_조건부_줄은_이어지는_들여쓴_줄까지_같이_빠진다"]),
 ("integrate 템플릿에 조건부 표지가 없다", IT,
  "- {?open}`<열린 질문>`은 아직 모르는 것이다", "- `<열린 질문>`은 아직 모르는 것이다",
  [f"{BT5}::test_해당_없는_규칙_줄은_프롬프트에서_빠진다"]),
 ("끝점 줄을 흐름 예산에 안 센다", FLW,
  "    out, used = [], sum(len(line) + 1 for line in pinned)", "    out, used = [], 0",
  ["tests/knowledge/test_flow.py::test_흐름_텍스트는_끝점_씨앗_줄을_맨_앞에_두고_예산에_센다"]),
 ("증거 바닥이 4K보다 낮다", LD,
  "_EVIDENCE_FLOOR = 4000", "_EVIDENCE_FLOOR = 1500",
  ["tests/application/test_lead.py::test_증거는_4K_밑으로_깎지_않고_그래도_넘으면_태스크를_접고_보낸다"]),
 ("요약이 고정부를 안 쪼갠다", TD,
  '             "읽기 목록": len(_block(prompt, "부를 수 있는 읽기")),', "",
  ["tests/application/test_trace_digest.py::test_요약_머리줄은_고정부를_읽기_목록_흐름_케이스_열린_질문으로_쪼갠다"]),
]

# ── 12a-R2-4 ⑥ — 태스크 id 인용 정리 · caveats는 문장으로 ──
CITE_T = "tests/application/test_verdict.py::test_태스크_id를_인용하면_그_태스크의_증거가_하나일_때_코드가_고친다"
CASES += [
 ("태스크 id 인용을 안 고친다", ND,
  '        return {"verdict": sanitize_verdict(resolve_task_citations(verdict, state.plan_tasks)), "llm_errors": notes}',
  '        return {"verdict": sanitize_verdict(verdict), "llm_errors": notes}',
  [CITE_T]),
 ("증거가 여럿인 태스크도 첫 증거로 고친다", ND,
  "    only = {t.id: t.result_evidence_ids[0] for t in tasks if len(t.result_evidence_ids) == 1}",
  "    only = {t.id: t.result_evidence_ids[0] for t in tasks if t.result_evidence_ids}",
  [CITE_T]),
 ("고친 인용을 caveat에 안 적는다", ND,
  '                                      "caveats": verdict.caveats + [note]})', '                                      "caveats": verdict.caveats})',
  [CITE_T]),
 ("판정 예시가 caveats에 id만 요구한다", B,
  '        "caveats": ["한 문장으로 — 무엇이 잘리거나 실패해서 무엇을 단정 못 하는가, 그 증거 id와 함께 (한국어)"],',
  '        "caveats": ["잘린 증거(⚠)로 주장했다면 그 증거 id를 여기 적는다"],',
  ["tests/application/test_verdict.py::test_판정_예시의_caveats는_문장을_요구한다"]),
]

# ── 12a-R2-5 ① — 멈춘 스트림: 받은 글을 남기고, 온전하면 쓰고, 아니면 한 번 다시 · 유휴 상한 config · 경고 거름 · 요약 줄 ──
DLM = ROOT / "src/domain/llm.py"
IDLE_T = "tests/config/test_schema_llm.py::test_스트림_유휴_상한은_양수이고_스트리밍일_때_describe에_보인다"
STALL_T = "tests/infrastructure/test_llm_adapters.py::test_멈춘_스트림은_받은_글을_남기고_유휴_상한은_config가_정한다"
PART_T = "tests/infrastructure/test_llm_adapters.py::test_받은_글은_실패한_답에만_실린다"
CUT_OK = "tests/application/test_lead.py::test_끊긴_스트림의_받은_글이_온전한_답이면_그대로_쓴다"
CUT_RE = "tests/application/test_lead.py::test_끊긴_스트림의_글이_깨졌으면_시간_초과가_아니라_같은_질문으로_한_번_다시_묻는다"
CUT_TR = "tests/test_cli.py::test_트레이스가_스트림_끊김을_적는다"
CUT_LOG = "tests/test_cli.py::test_스트림_유휴_경고_줄만_거른다"
CUT_DG = "tests/application/test_trace_digest.py::test_요약_끝에_전송_실패와_스트림_끊김을_센다"
CASES += [
 ("유휴 상한을 describe에 안 적는다", SL,
  '                  + (f" · 유휴 {self.stream_idle_s:g}s" if self.stream and self.stream_idle_s else "")\n', "", [IDLE_T]),
 ("유휴 상한 0을 받는다", SL, "        if v is not None and v <= 0:\n            raise ValueError(f\"stream_idle_s",
  "        if v is not None and v < 0:\n            raise ValueError(f\"stream_idle_s", [IDLE_T]),
 ("유휴 상한을 모델에 안 넘긴다", LCM, '                extra["stream_chunk_timeout"] = self._cfg.stream_idle_s',
  "                pass", [STALL_T]),
 ("멈춘 스트림이 받은 글을 버린다", LCM, 'partial_text="".join(parts),', 'partial_text="",', [STALL_T]),
 ("성공한 답에도 받은 글을 허용한다", DLM, '        if self.status == "ok" and self.partial_text:',
  "        if False:", [PART_T]),
 ("끊긴 스트림을 따로 보지 않는다", LD, '            if reply.status == "error" and reply.partial_text:',
  "            if False:", [CUT_OK, CUT_RE]),
 ("받은 글이 온전해도 안 쓴다", LD, "                if got.ok:\n                    last = got\n",
  "                if False:\n                    last = got\n", [CUT_OK]),
 ("깨진 받은 글을 시간 초과로 본다", LD,
  '                    note = f"답 도중 끊김 — 받은 글 {len(text):,}자, 같은 질문으로 다시 묻는다"',
  '                    note, timed_out = f"답 도중 끊김 — 받은 글 {len(text):,}자, 같은 질문으로 다시 묻는다", True', [CUT_RE]),
 ("받은 글을 트레이스 날것에 안 싣는다", LD, "                text = reply.partial_text\n                parsed = parse_object(text)",
  "                parsed = parse_object(reply.partial_text)", [CUT_OK, CUT_RE]),
 ("끊김 메모를 트레이스에 안 넘긴다", LD, "        _tell(on_exchange, asked, text, failure, latency, waited, source, note, stats)",
  "        _tell(on_exchange, asked, text, failure, latency, waited, source, None, stats)", [CUT_OK]),
 ("라이브러리 오류 문구를 통째로 싣는다", LD, '    return error.split(". ", 1)[0]', "    return error", [CUT_OK]),
 ("트레이서가 스트림 줄을 안 쓴다", MN, '        took += f"스트림: {note}\\n" if note else ""\n', "", [CUT_TR]),
 ("유휴 경고를 안 거른다", MN, '    return getattr(record, "source", None) != "stream_chunk_timeout"', "    return True", [CUT_LOG]),
 ("_run이 경고 거름망을 안 단다", MN, "    _quiet_stream_stall_log()\n\n    async def wrapped():", "    async def wrapped():", [CUT_LOG]),
 ("요약이 전송 실패를 안 센다", TD, "        elif reply.startswith(_NO_REPLY) and verdict.startswith(_FAILED):", "        elif False:", [CUT_DG]),
 ("요약이 받은 글로 읽음과 다시 물음을 뒤바꾼다", TD,
  '            cuts["받은 글로 읽음" if "읽었다" in stream.group(1) else "다시 물음"] += 1',
  '            cuts["다시 물음" if "읽었다" in stream.group(1) else "받은 글로 읽음"] += 1', [CUT_DG]),
]

# ── 12a-R2-5 ② — 강등은 검사와 같은 규칙으로 걷어낸다 · 판정 프롬프트의 component 줄 ──
DEMOTE_T = "tests/application/test_verdict.py::test_강등은_서비스_아닌_component_다리도_걷어낸다"
CASES += [
 ("강등이 component 문제 다리를 안 걷는다", ND, "        if component_problem(link.component):\n            foreign.append",
  "        if False:\n            foreign.append", [DEMOTE_T]),
 ("verify가 강등에 component 검사를 안 넘긴다", ND,
  "        return {\"verdict\": demote_verdict(verdict, problems, citable=citable, component_problem=component_problem),",
  "        return {\"verdict\": demote_verdict(verdict, problems, citable=citable),", [DEMOTE_T]),
 ("서비스 아닌 component 제외를 caveat에 안 적는다", ND,
  "        [f\"서비스 아닌 component 제외: {', '.join(foreign)}\"] if foreign else [])", "        [])", [DEMOTE_T]),
 ("판정 프롬프트가 만드는 서비스를 말하지 않는다", ROOT / "config/prompts/investigate-conclude.md",
  "원인이 데이터(키·컬렉션·토픽)면 그 데이터를 **만드는** 서비스다", "원인이 데이터(키·컬렉션·토픽)면 그 데이터의 서비스다",
  ["tests/application/test_verdict.py::test_판정_프롬프트는_component가_데이터를_만드는_서비스라고_말한다"]),
]

# ── 12a-R2-5 ③ — recompute 기대값은 대상 행: 0부터의 번호 · 머리줄·케이스·예시의 경로 · 실행 전 검사 ──
ROWS_T = "tests/application/test_runner_probe.py::test_대상_값이_든_행을_앞에_통째로_두고_나머지는_한_줄씩"
REST_ROWS_T = "tests/application/test_runner_probe.py::test_rest_응답의_response_목록은_항목당_한_줄이다"
GUARD_T = "tests/application/test_runner_probe.py::test_recompute_기대값이_대상_행이_아닌_행을_가리키면_실행_전에_거부하고_대상_행_경로를_준다"
CASES += [
 ("행 번호를 1부터 적는다", RP, '        body, cut_body = _fill([f"[{i}] {texts[i]}" for i in order], limit=limit, total=len(rows))',
  '        body, cut_body = _fill([f"[{i + 1}] {texts[i]}" for i in order], limit=limit, total=len(rows))', [ROWS_T]),
 ("머리줄이 대상 행 경로 대신 건수만 적는다", RP,
  """        note = f" · 대상 행 {', '.join(f'{at}[{i}]' for i in hits)} 먼저" if hits else \"\"""",
  """        note = f" · 대상 행 {len(hits)}건 먼저" if hits else \"\"""", [ROWS_T, REST_ROWS_T]),
 ("dict 안 목록의 경로에 키를 안 붙인다", RP, "focus=focus, at=str(key))", "focus=focus)", [REST_ROWS_T]),
 ("recompute 전에 대상 행을 안 본다", RP, "                if wrong is not None:\n                    return TaskOutcome",
  "                if False:\n                    return TaskOutcome", [GUARD_T]),
 ("대상 행이 아닌 행도 통과시킨다", RP, "            if hits and step not in hits:", "            if False:", [GUARD_T]),
 ("케이스 블록에 대상 행을 안 적는다", B, """            + (f"\\n대상 행: {'; '.join(rows)}" if rows else "")\n""", "            + \"\"\n",
  ["tests/application/test_briefing.py::test_케이스_블록이_증거가_짚은_대상_행을_적는다"]),
 ("recompute 예시가 첫 행을 보여 준다", B,
  """                       "path": f"{rows[0] if rows else 'response[대상 행 번호]'}.화면이 보여 준 그 숫자의 필드"}})""",
  """                       "path": "response 아래 그 숫자의 위치 — response.items[0].alarm 같은 모양"}})""",
  ["tests/application/test_briefing.py::test_recompute_예시의_기대값_경로는_증거가_짚은_대상_행이다"]),
]

# ── 12a-R2-5 ④ — 흐름 블록의 고정 줄은 케이스 자신의 끝점만 ──
PIN_T = "tests/application/test_briefing.py::test_흐름_블록은_증거에_나온_다른_끝점에_밀리지_않는다"
CASES += [
 ("증거에 나온 끝점도 고정한다", FLW, "            if sid in by_id and by_id[sid][\"type\"] == \"endpoint\" and pin is not None and sid not in pin:",
  "            if False:", [PIN_T]),
 ("브리핑이 고정할 끝점을 안 넘긴다", B, "prefer=prefer, pin=set(own))", "prefer=prefer)", [PIN_T]),
 ("케이스 끝점을 씨앗 상한에 맡긴다", B, "    seeds = own + [s for s in flowgraph.find_seeds(graph, seeds_from) if s not in own]",
  "    seeds = flowgraph.find_seeds(graph, seeds_from)", [PIN_T]),
 ("고정 안 한 끝점 줄을 버린다", FLW, "        lines += [line_for(sid) for sid in others]\n", "", ["tests/knowledge/test_flow.py::test_고정_안_한_끝점은_맨_뒤에_예산_안에서_실린다"]),
]

# ── 12a-R3-1 ① — frame 전 입구 노드: 값 안의 identity로 대상 키 ──
IM = ROOT / "src/application/intake_map.py"
GP = ROOT / "src/application/graph.py"
IMT = "tests/application/test_intake_map.py"
PICK_T = f"{IMT}::test_identity가_그대로_든_키가_하나면_그_키를_고르고_State에는_접수와_대상_키_읽기만_남는다"
NOPICK_T = f"{IMT}::test_고르지_못하면_고르지_않고_이유와_후보를_적는다"
HOLD_T = f"{IMT}::test_값_안의_같은_객체에_identity_전부가_잎으로_있으면_그_자리를_준다"
NODE_T = "tests/application/test_nodes.py::test_입구_노드는_frame_전에_접수를_읽고_대상_키를_State에_둔다"
LINK_B = "tests/application/test_briefing.py::test_케이스_블록이_입구_노드가_고른_대상_키를_근거와_함께_말하고_흐름_줄은_그_키를_앞세운다"
PREF_B = "tests/application/test_briefing.py::test_흐름_블록은_증상_단어로_이름을_앞세우지_않고_prefer로만_앞세운다"
CASES += [
 ("identity 일부만 있어도 그 자리로 친다", IM, "        if values and all(leaves[v] >= n for v, n in Counter(values).items()):", "        if values and any(leaves[v] >= n for v, n in Counter(values).items()):", [HOLD_T]),
 ("부분 문자열로 identity를 찾는다", IM, "        leaves = Counter(str(v) for v in value.values() if isinstance(v, (str, int, float)) and not isinstance(v, bool))", "        leaves = Counter(str(v) for v in value.values() if isinstance(v, (str, int, float)) and not isinstance(v, bool))\n        leaves.update({v: 9 for v in values if any(l in v for l in list(leaves))})", [HOLD_T]),
 ("둘 이상이어도 첫 키를 고른다", IM, "    if len(hits) == 1:\n        (key, (at, complete, value)), = hits.items()", "    if hits:\n        (key, (at, complete, value)), *_ = hits.items()", [NOPICK_T]),
 ("identity가 없는 키도 고른다", IM, "        at = holds_identity(value, identity)\n        if at:", "        at = holds_identity(value, identity) or \"(맨 위)\"\n        if at:", [PICK_T, NOPICK_T]),
 ("접수 읽기가 실패해도 계속 간다", IM, "    if outcome.status != \"ok\" or not outcome.evidence:\n        return patch(", "    if False:\n        return patch(",
  [f"{IMT}::test_접수_읽기가_실패하거나_대상_행이_없으면_거기서_멈춘다"]),
 ("템플릿 머리를 scan하지 않는다", IM, "    for template in (k for k in declared if \"{\" in k):", "    for template in ():",
  [f"{IMT}::test_템플릿_키는_머리로_scan해_실재하는_키를_후보로_삼고_상한을_지킨다"]),
 ("후보 상한을 안 지킨다", IM, "            if len(candidates) >= MAX_CANDIDATES:\n                capped = True\n                break", "            if False:\n                capped = True\n                break",
  [f"{IMT}::test_템플릿_키는_머리로_scan해_실재하는_키를_후보로_삼고_상한을_지킨다"]),
 ("입구 노드가 그래프에 없다", GP, '    builder.add_edge(START, "intake")\n', '    builder.add_edge(START, "walk")\n', [NODE_T]),
 ("대본 경로에서도 입구를 돈다", ND, "        if deps.intake is None or not deps.check_discovery or state.target is not None:", "        if deps.intake is None or state.target is not None:", [NODE_T]),
 ("실행기가 원본을 안 준다", RP, "    def raw(self, evidence_id: str):\n        return self._raw.get(evidence_id)", "    def raw(self, evidence_id: str):\n        return None",
  ["tests/application/test_runner_probe.py::test_실행기는_증거_id로_원본을_돌려준다"]),
 ("케이스 블록에 대상 키를 안 적는다", B, '            + (f"\\n{target.line()}" if target is not None and target.line() else "")\n', "", [LINK_B]),
 ("흐름 줄이 고른 키를 안 앞세운다", B, "    body = flowgraph.flow_text(graph, seeds, budget=budget, prefer=prefer, pin=set(own))",
  "    body = flowgraph.flow_text(graph, seeds, budget=budget, prefer=(), pin=set(own))", [LINK_B, PREF_B]),
 ("브리핑이 State의 대상 키를 안 본다", B, "    return state.target or TargetPick()", "    return TargetPick()", [LINK_B]),
 ("CLI가 입구 재료를 안 넘긴다", MN, "                              intake=intake_spec(case, site),\n", "", ["tests/application/test_lead.py::test_CLI가_실제로_돈다"]),
]
# ── 12a-R3-0 ② — 대상과 연결 안 된 키로 high 금지 ──
CAP_T = "tests/application/test_verdict.py::test_최상위가_인용한_키가_대상_키가_아니면_확신_상한_medium과_caveat"
CAP2_T = "tests/application/test_verdict.py::test_대상_키를_못_골랐어도_키를_인용한_high는_medium이고_대본_경로는_안_건다"
CASES += [
 ("키를 인용해도 확신을 안 낮춘다", ND, '    confidence = "medium" if verdict.confidence == "high" else verdict.confidence',
  "    confidence = verdict.confidence", [CAP_T]),
 ("대상 키를 읽었어도 낮춘다", ND, "    if not keys or any(present(known, [k]) for k in keys for known in linked):", "    if not keys:", [CAP_T]),
 ("키를 인용하지 않은 최상위도 낮춘다", ND, "    keys = cited_keys(verdict.root_cause.evidence_ids, evidence)\n    linked", "    keys = cited_keys(verdict.root_cause.evidence_ids, evidence) or [\"?\"]\n    linked", [CAP_T]),
 ("verify가 상한을 안 건다", ND, '            return {"verify_problems": [], **({"verdict": capped} if (changed or capped is not verdict) else {})}',
  '            return {"verify_problems": [], **({"verdict": verdict} if changed else {})}', [CAP_T, CAP2_T]),
 ("대본 경로에도 상한을 건다", ND, "            if not deps.check_discovery:\n                return {\"verify_problems\": [], **({\"verdict\": verdict} if changed else {})}", "            if False:\n                return {\"verify_problems\": [], **({\"verdict\": verdict} if changed else {})}", [CAP2_T]),
 ("scan 패턴을 키 읽기로 센다", IM, "_KEY_READ = re.compile(r\"^redis\\.get key='((?:[^'\\\\]|\\\\.)*)'\")",
  "_KEY_READ = re.compile(r\"^redis\\.(?:get|scan) (?:key|pattern)='((?:[^'\\\\]|\\\\.)*)'\")", [CAP_T]),
]

# ── 12a-R3-0 ② 곁다리 — 파일 턴 번호는 폴더가 센다(판정 어댑터가 따로라 둘이 001을 겹쳐 썼다) ──
LFK = ROOT / "src/infrastructure/llm_fakes.py"
CASES += [
 ("파일 턴 번호를 폴더에서 안 센다", LFK, '        taken = [int(p.name[:3]) for p in self._dir.glob("[0-9][0-9][0-9]-ask.md") if p.name[:3].isdigit()]',
  "        taken = []", ["tests/infrastructure/test_llm_file_turn.py::test_같은_폴더의_어댑터_둘은_번호를_이어_쓴다"]),
]

# ── 12a-R3-0 ③ — 키 지도의 경로는 이름이 아니다 · code.flow 서비스 요약 · code.uses 서비스 안내 ──
MAPPATH_T = "tests/application/test_runner_probe.py::test_앞선_키_지도의_경로를_컬렉션이나_키_이름으로_내면_실행_전에_거부한다"
SVCFLOW_T = "tests/infrastructure/test_deployed_code.py::test_code_flow에_서비스_이름을_주면_엣지_나열_대신_관계별_요약이다"
CASES += [
 ("키 지도가 경로라고 말하지 않는다", D, '            merged = {"_주의": "아래 경로는 설정 안의 위치이지 컬렉션·키 이름이 아니다 — 그 자리의 값은 code.config key=<경로>로 읽는다",\n                      "_키_지도": key_map(merged)}',
  '            merged = {"_키_지도": key_map(merged)}', ["tests/infrastructure/test_deployed_code.py::test_400줄이_넘는_층도_통째로_읽는다"]),
 ("실행기가 키 지도의 경로를 기억하지 않는다", RP, '            self._map_paths |= set(result.data["_키_지도"])', "            pass", [MAPPATH_T]),
 ("키 지도의 경로를 이름으로 내도 소켓에 나간다", RP, "            if isinstance(name, str) and name in self._map_paths:", "            if False:", [MAPPATH_T]),
 ("code.flow가 서비스도 엣지로 나열한다", D, "        if services:\n            # 서비스는", "        if False:\n            # 서비스는", [SVCFLOW_T]),
 ("서비스 요약이 이름을 여덟 개 넘게 싣는다", FLW, '        shown = ", ".join(sorted(labels)[:names]) + (f" 외 {len(labels) - names}개" if len(labels) > names else "")',
  '        shown = ", ".join(sorted(labels))', [SVCFLOW_T]),
 ("code.uses가 서비스 이름을 안내하지 않는다", D, "        if name in self._topology.services:\n            # 사내 측정 #5", "        if False:\n            # 사내 측정 #5",
  ["tests/infrastructure/test_deployed_code.py::test_code_uses에_서비스_이름을_주면_code_flow로_안내한다"]),
]

# ── 12a-R3-1 ①-b — 측정판: 요약 키 값의 identity · c-1형 · 미끼 ──
LCT = "tests/test_local_case.py"
CASES += [
 ("data-empty의 data가 안 비었다", LC, '    if variant in _EMPTY_DATA:\n        return []', '    if False:\n        return []',
  [f"{LCT}::test_data_empty는_요약_키가_있고_data가_빈_배열이며_값_안에_대상_identity가_그대로_있다"]),
 ("요약 키 값에 identity가 없다", LC, '                    "record": [{"summary_info": {"group": l, "title": "Alarm", "source": "MES"},',
  '                    "record": [{"summary_info": {"source": "MES"},',
  [f"{LCT}::test_data_empty는_요약_키가_있고_data가_빈_배열이며_값_안에_대상_identity가_그대로_있다"]),
 ("미끼 키의 identity가 대상과 같다", LC, '                    "record": [{"summary_info": {"group": l, "title": "Caution", "source": "MES", "scope": "ALL"}, "data": []}],',
  '                    "record": [{"summary_info": {"group": l, "title": "Alarm", "source": "MES", "scope": "ALL"}, "data": []}],',
  [f"{LCT}::test_미끼_키는_이름이_비슷하고_같은_라벨을_달았지만_identity가_다르다"]),
]

# ── 12a-R3-1 ② — 태스크 id 입력 · 막힘 되묻기 · 미실행 사유 ──
INPUT_T = "tests/application/test_nodes.py::test_입력이_태스크_id면_그_태스크의_증거_id로_바꾸고_실패했으면_의존을_뗀다"
WIRED_T = "tests/application/test_nodes.py::test_integrate가_실패한_태스크_id를_입력으로_낸_태스크의_의존을_떼고_기록한다"
BLOCK_T = "tests/application/test_nodes.py::test_대기만_있고_돌릴_것이_없으면_한_번_되묻고_그래도_없으면_no_runnable이다"
REASON_T = "tests/application/test_diagnose.py::test_미실행_사유는_사실대로다_막힌_입력과_밀린_우선순위를_가른다"
ALIAS_T = "tests/application/test_nodes.py::test_같은_답에서_거부되거나_접힌_id를_입력으로_대면_그_읽기의_증거로_바꾼다"
PAST_T = "tests/application/test_nodes.py::test_앞_라운드에서_거부된_중복_읽기의_id를_입력으로_대면_그_기록의_증거로_바꾼다"
CASES += [
 ("태스크 id 입력을 안 바꾼다", ND, "        task, notes = _resolve_inputs(task, {**(inputs_of or {}), **aliases})", "        task, notes = task, []", [INPUT_T]),
 ("실패한 태스크 입력의 의존을 안 뗀다", ND, "        if found is None:\n            notes.append(f\"{task.id}: 입력 {item}은 태스크 id이고",
  "        if found is None:\n            ids.append(item)\n            notes.append(f\"{task.id}: 입력 {item}은 태스크 id이고", [INPUT_T]),
 ("기대값의 증거 id를 안 바꾼다", ND, "        if found and len(found) == 1:\n            params = {**params,", "        if False:\n            params = {**params,", [INPUT_T]),
 ("아직 안 돈 태스크 id를 첫 증거 id로 안 바꾼다", ND, "    return [EvidenceRef.make_id(item, 1)]", "    return [item]", [INPUT_T]),
 ("integrate가 입력 지도를 안 넘긴다", ND, "                inputs_of={**_rejected_reads(base), **_inputs_of(base)},",
  "", [WIRED_T]),
 ("앞 라운드에서 거부된 id를 입력에서 안 바꾼다", ND, "                inputs_of={**_rejected_reads(base), **_inputs_of(base)},",
  "                inputs_of=_inputs_of(base),", [PAST_T]),
 ("막혔는데 되묻지 않는다", ND, "        if (blocked and patch.get(\"decision\") != \"conclude\" and not patch.get(\"stopped_by\")\n                and not any(BLOCKED_MARK in e for e in state.llm_errors)):",
  "        if False:", [BLOCK_T]),
 ("막힘 되묻기를 케이스당 한 번으로 안 묶는다", ND, "        if (blocked and patch.get(\"decision\") != \"conclude\" and not patch.get(\"stopped_by\")\n                and not any(BLOCKED_MARK in e for e in state.llm_errors)):",
  "        if blocked and patch.get(\"decision\") != \"conclude\" and not patch.get(\"stopped_by\"):", [BLOCK_T]),
 ("막혀서 끝날 때 이유를 안 남긴다", ND, "                    \"llm_errors\": errors + ([still] if still and still not in errors else [])}",
  "                    \"llm_errors\": errors}", [BLOCK_T]),
 ("막힌 입력을 우선순위 탓으로 적는다", ND, "        if owner is not None and owner.status == \"error\":\n            return f\"막힘 — 입력 {item} 실패\"",
  "        if owner is not None and owner.status == \"error\":\n            return \"우선순위에 밀림\"", [REASON_T]),
 ("거부된 중복의 id를 입력에서 안 바꾼다", ND, "            aliases[task.id] = list(where) or None\n", "", [ALIAS_T]),
 ("갱신으로 접힌 id를 입력에서 안 바꾼다", ND, "            aliases[task.id] = [EvidenceRef.make_id(held.id, 1)]\n", "", [ALIAS_T]),
 ("diagnose가 미실행 사유를 안 가른다", DG, "                     + \", \".join(f\"{t.id}({unrun_reason(state, t)})\" for t in unrun))",
  "                     + \", \".join(f\"{t.id}(우선순위에 밀림)\" for t in unrun))", [REASON_T]),
]

# ── 12a-R3-1 ③ — 받을 때 인자 검사 · 참고 재집계 ──
ACCEPT_T = "tests/application/test_nodes.py::test_모르는_인자나_빠진_인자나_없는_action은_받을_때_거부하고_받는_인자를_같이_적는다"
ASIDE_T = "tests/application/test_runner_probe.py::test_recompute_filter에_대상_identity가_하나도_없으면_결과를_참고로_표시한다"
LADDER_ASIDE_T = "tests/application/test_briefing.py::test_참고_재집계의_match는_사다리가_사실로_쓰지_않는다"
CASES += [
 ("모르는 인자를 받을 때 안 거른다", ND, "        problem = _accept_problem(task)\n        if problem is not None:", "        problem = _accept_problem(task)\n        if False:", [ACCEPT_T]),
 ("거부 기록에 받는 인자를 안 적는다", ND, "    return problem + hint", "    return problem", [ACCEPT_T]),
 ("action 없는 태스크를 받는다", ND, "    if not task.action:\n        return \"action이 없다", "    if False:\n        return \"action이 없다", [ACCEPT_T]),
 ("identity 없는 재집계를 참고로 안 표시한다", RP, "    return bool(focus) and not (set(focus) & _leaves(filter_))", "    return False", [ASIDE_T]),
 ("identity가 하나만 있어도 참고로 표시한다", RP, "    return bool(focus) and not (set(focus) & _leaves(filter_))", "    return bool(focus) and not (set(focus) <= _leaves(filter_))", [ASIDE_T]),
 ("dryrun이 거부 기록을 안 보여 준다", MN, "    if final[\"llm_errors\"]:\n        # 받을 때 거부된 태스크(R3-1 ③)", "    if False:\n        # 받을 때 거부된 태스크(R3-1 ③)",
  ["tests/application/test_dryrun.py::test_CLI가_실제로_돈다"]),
 ("참고 재집계의 match를 사다리가 사실로 쓴다", B, "    if ASIDE_MARK in _evidence_text(evidence, recomputed.result_evidence_ids[0]):\n        return None", "    if False:\n        return None", [LADDER_ASIDE_T]),
]

# ── 12a-R3-1b ① — 입구 노드는 결과만 보인다 ──
INTAKE_T = "tests/application/test_intake_map.py::test_identity가_그대로_든_키가_하나면_그_키를_고르고_State에는_접수와_대상_키_읽기만_남는다"
NARROW_T = "tests/application/test_intake_map.py::test_후보_읽기_본문이_잘렸으면_대상_키를_identity_객체의_부모로_좁혀_다시_읽는다"
REREAD_T = "tests/application/test_intake_map.py::test_대상_키_다시_읽기가_실패하면_고른_키는_남기고_그_사실을_적는다"
CASES += [
 ("후보 읽기를 State에 싣는다", IM, "        if keep:\n            tasks.append(done)", "        if True:\n            tasks.append(done)", [INTAKE_T]),
 ("고른 키를 State에 남는 읽기로 다시 안 읽는다", IM, "        _, again = await run(goal, \"redis.get\", {\"key\": key, **({\"path\": narrow} if narrow else {})}, keep=True, task_id=\"t-1\")",
  "        _, again = await run(goal, \"redis.get\", {\"key\": key, **({\"path\": narrow} if narrow else {})}, keep=False, task_id=\"t-1\")", [INTAKE_T]),
 ("잘린 본문인데 좁혀 읽지 않는다", IM, "        narrow = None if complete else _parent(at)", "        narrow = None", [NARROW_T]),
 ("identity 자리 자체로 좁혀 data를 놓친다", IM, "    m = re.match(r\"^(.*)(\\.[^.\\[\\]]+|\\[\\d+\\])$\", path)\n    return m.group(1) or None if m else None",
  "    return path", [NARROW_T]),
 ("다시 읽기 실패를 사실로 안 적는다", IM, "            basis = f\"키 값의 {at}에 {label}가 그대로 있다 (다시 읽기 실패: {again.error or '증거 없음'})\"",
  "            basis = f\"키 값의 {at}에 {label}가 그대로 있다\"", [REREAD_T]),
 ("접기 줄의 있음 개수가 틀리다", CS, "        present = self.read - len(self.absent) - self.failed", "        present = self.read", [INTAKE_T]),
 ("케이스 블록에 접기 줄이 없다", B, "            + (f\"\\n{target.fold_line()}\" if target is not None and target.fold_line() else \"\")\n", "",
  ["tests/application/test_briefing.py::test_케이스_블록이_입구_노드가_고른_대상_키를_근거와_함께_말하고_흐름_줄은_그_키를_앞세운다"]),
]

# ── 12a-R3-1b ② — 쓰는 쪽 엣지: 명령으로 쓰는 자원 ──
IXP = ROOT / "src/knowledge/index.py"
QYP = ROOT / "src/knowledge/query.py"
FLP = ROOT / "src/knowledge/flow.py"
CMD_T = "tests/knowledge/test_index.py::test_동사_없는_줄의_이름이_뒤에서_토픽으로_나가면_쓰라는_명령이다"
CMD_USES_T = "tests/knowledge/test_index.py::test_code_uses는_명령으로_쓰는_함수를_쓰기에_두고_그렇게_적는다"
CMD_EDGE_T = "tests/knowledge/test_index.py::test_흐름_그래프에_명령으로_쓰는_서비스의_writes_엣지를_단다"
CASES += [
 ("config 조회의 get을 읽기로 센다", FLP, "    words = _parts(_CONFIG_GET.sub(\"\", text))", "    words = _parts(text)",
  ["tests/knowledge/test_flow.py::test_config_조회의_get은_자원_읽기가_아니다"]),
]

# ── 12a-R3-1b ③ — 이미 읽었다 + 다음 홉 ──
DECL_T = f"{IMT}::test_고른_키의_선언_이름과_identity_옆의_빈_목록을_적는다"
HOP_T = f"{IMT}::test_다음_홉은_고른_키의_선언_이름으로_code_uses이고_인덱스가_없으면_없다"
LINES_T = f"{IMT}::test_입구_줄은_이미_읽은_것과_빈_목록과_다음_홉을_말한다"
FRAME_HOP_T = "tests/application/test_briefing.py::test_입구가_돌았으면_케이스_블록에_입구_줄이_실리고_frame_예시_첫_칸은_접수_읽기가_아니라_다음_홉이다"
LADDER_HOP_T = "tests/application/test_briefing.py::test_입구가_고른_키가_있으면_사다리_첫_칸은_그_키를_누가_쓰나이고_그다음은_쓰는_함수의_호출자다"
HINT_T = "tests/application/test_nodes.py::test_입구가_이미_한_읽기를_또_내면_거부_기록에_다음_홉을_적는다"
HINT_WIRE_T = "tests/application/test_nodes.py::test_integrate는_입구_결과로_다음_홉_힌트를_넘긴다"
CASES += [
 ("선언 이름을 안 남긴다", IM, "                declared_of[name] = template\n", "", [DECL_T]),
 ("identity 옆 빈 목록을 안 적는다", IM, "    return ([place(k) for k, v in obj.items() if isinstance(v, list) and not v],", "    return ([],", [DECL_T]),
 ("인덱스가 없어도 다음 홉을 낸다", IM, "    if target is None or not target.key or not code_index:", "    if target is None or not target.key:", [HOP_T]),
 ("입구 줄이 이미 읽은 것을 안 적는다", IM, "    if done:\n        lines.append(\"입구 노드가 이미 읽었다: \"", "    if False:\n        lines.append(\"입구 노드가 이미 읽었다: \"", [LINES_T]),
 ("케이스 블록에 입구 줄이 없다", B, "            + (f\"\\n{intake_lines(state, code_index=code_index)}\" if intake_lines(state, code_index=code_index) else \"\"))", "            )", [FRAME_HOP_T]),
 ("입구가 돌았는데 frame 예시에 접수 읽기를 둔다", B, "            if not intake_done:\n                shapes.append(first_read)", "            if True:\n                shapes.append(first_read)", [FRAME_HOP_T]),
 ("frame 예시에 다음 홉을 안 둔다", B, "        if hop:\n            shapes.append(hop)", "        if False:\n            shapes.append(hop)", [FRAME_HOP_T]),
 ("사다리 첫 칸이 고른 키의 uses가 아니다", B, "        hop = (pending.action, dict(pending.params))\n    if hop is not None:", "        hop = (pending.action, dict(pending.params))\n    if False:", [LADDER_HOP_T]),
 ("uses 뒤에 쓰는 함수의 호출자를 안 묻는다", B, "            if writer is not None and not _issued(tasks, \"code.callers\", name=writer):\n                return (\"code.callers\", {\"name\": writer}), [used_hop.result_evidence_ids[0]]",
  "            if False:\n                return (\"code.callers\", {\"name\": writer}), [used_hop.result_evidence_ids[0]]", [LADDER_HOP_T]),
 ("거부 기록에 다음 홉을 안 적는다", ND, "            at_intake = bool(where) and hint and set(where) <= hint_for", "            at_intake = False", [HINT_T]),
 ("리드가 한 읽기의 중복에도 다음 홉을 붙인다", ND, "            at_intake = bool(where) and hint and set(where) <= hint_for", "            at_intake = bool(where) and hint", [HINT_T]),
 ("integrate가 다음 홉 힌트를 안 넘긴다", ND, "                hint=hop_line(base.target, code_index=deps.code_index, walk=base.walk), hint_for=_intake_ids(base),", "                hint=\"\", hint_for=_intake_ids(base),", [HINT_WIRE_T]),
]

# ── 핫픽스 10-14 — 측정판 토폴로지는 사본에만 · 리포 knowledge를 테스트가 안 읽는다 ──
CASES += [
 ("측정판이 토폴로지 사본의 services를 안 바꾼다", LC, "    return {**topo, \"services\": dict(BOARD_SERVICES)}", "    return dict(topo)",
  ["tests/test_local_case.py::test_batch는_요약_키를_직접_쓰지_않고_명령에_실어_보낸다"]),
 ("예외 목록을 OS 구분자 그대로 비교한다", ROOT / "tests/test_portability.py", "        return path.relative_to(root).as_posix()", "        return str(path.relative_to(root))",
  ["tests/test_portability.py::test_예외_목록은_Windows_구분자에도_맞는다"]),
 ("repo 루트 knowledge 읽기 검사가 안 잡는다", ROOT / "tests/test_portability.py",
  "        if (isinstance(node, ast.BinOp) and _mentions_knowledge(node)\n                and any(isinstance(n, ast.Name) and n.id == \"__file__\" for n in ast.walk(node))):",
  "        if False:", ["tests/test_portability.py::test_검사기가_repo_루트의_knowledge_읽기를_실제로_잡는다"]),
]

# ── 12a-R3-1c ①-a — 쓴 쪽은 데이터로 · code.resources ──
DCP = ROOT / "src/infrastructure/deployed_code.py"
WRITER_T = f"{IMT}::test_값의_문자열_잎이_인덱스의_클래스_이름과_완전_일치하면_그_클래스가_쓴_쪽이다"
HOP2_T = f"{IMT}::test_다음_홉은_쓴_쪽을_알면_그_클래스의_입력이고_모르면_선언_키의_uses다"
RESOURCES_T = "tests/knowledge/test_index.py::test_resources는_클래스의_메서드들이_읽고_쓰는_자원을_나눠_준다"
RESOURCES_DC_T = "tests/infrastructure/test_deployed_code.py::test_code_resources는_클래스가_읽고_쓰는_자원을_주고_없거나_여럿이면_실패로_답한다"
WRITER_B_T = "tests/application/test_briefing.py::test_쓴_쪽을_알면_케이스_블록에_그_줄이_실리고_첫_칸은_code_resources다"
CASES += [
 ("클래스 이름이 둘이어도 첫 것을 고른다", IM, "    if len(names) == 1:\n        return hits[0][1], hits[0][0], \"\"", "    if names:\n        return hits[0][1], hits[0][0], \"\"", [WRITER_T]),
 ("부분 일치로 쓴 쪽을 고른다", IM, "    hits = [(at, text) for at, text in _string_leaves(value) if text in classes]",
  "    hits = [(at, text) for at, text in _string_leaves(value) if any(c.lower() == text.lower() for c in classes)]", [WRITER_T]),
 ("입구가 쓴 쪽을 안 적는다", IM, "        writer, writer_at, writer_problem = writer_of(value, classes)",
  "        writer, writer_at, writer_problem = \"\", \"\", \"\"", [WRITER_T]),
 ("인덱스 없이도 모른다고 말한다", IM, "                                writer_searched=bool(classes), **folded,", "                                writer_searched=True, **folded,", [WRITER_T]),
 ("쓴 쪽을 알아도 uses로 간다", IM, "    if target.writer:\n        return \"code.resources\", {\"name\": target.writer}", "    if False:\n        return \"code.resources\", {\"name\": target.writer}", [HOP2_T, WRITER_B_T]),
 ("resources가 메서드 자원을 안 모은다", QYP, "    if s.kind != \"class\":\n        return [sid], [], 0", "    if True:\n        return [sid], [], 0", [RESOURCES_T]),
 ("resources가 읽기와 쓰기를 안 가른다", QYP, "        rows = sorted((k, v) for k, v in seen.items() if k[0] == direction)", "        rows = sorted((k, v) for k, v in seen.items())", [RESOURCES_T]),
 ("code.resources가 여럿이어도 하나를 고른다", DCP, "        if len(found) > 1:\n            lines = qy.ambiguous_lines(self._index, name, found)", "        if False:\n            lines = qy.ambiguous_lines(self._index, name, found)", [RESOURCES_DC_T]),
 ("class_names가 함수까지 센다", DCP, "        return frozenset(s.name for s in self._index.symbols if s.kind == \"class\")", "        return frozenset(s.name for s in self._index.symbols)", [RESOURCES_DC_T]),
 ("인덱스 없이 code.resources를 목록에 둔다", B, "        out |= {\"code.callers\", \"code.uses\", \"code.resources\"}", "        out |= {\"code.callers\", \"code.uses\"}", [WRITER_B_T]),
 ("등재 목록이 포트에 없는 메서드를 가리킨다", ROOT / "src/domain/actions.py", "    \"code.resources\":      (\"code\",  \"resources\",", "    \"code.resources\":      (\"code\",  \"inputs\",",
  ["tests/domain/test_ports.py::test_등재된_code_액션의_메서드는_전부_포트에_있다"]),
 ("CLI code resources가 찾은 심볼을 안 쓴다", MN, "    for line in qy.resources_lines(index, found[0], where=where):", "    for line in []:",
  ["tests/knowledge/test_cli_code.py::test_code_resources가_클래스가_읽고_쓰는_자원에_답한다"]),
 ("옛 번들의 여섯 칸 자원을 못 읽는다", IXP, "            d[\"resources\"] = tuple(Resource(*r[:5]) for r in d.get(\"resources\", []))", "            d[\"resources\"] = tuple(Resource(*r) for r in d.get(\"resources\", []))",
  ["tests/knowledge/test_index.py::test_옛_번들의_여섯_칸_자원_튜플도_그대로_읽힌다"]),
 ("입구 노드가 클래스 집합을 안 넘긴다", ND, "                                    declared=tuple(deps.declared.get(\"rediskey\", ())), classes=deps.classes)",
  "                                    declared=tuple(deps.declared.get(\"rediskey\", ())))", ["tests/application/test_nodes.py::test_입구_노드는_frame_전에_접수를_읽고_대상_키를_State에_둔다"]),
]

# ── 12a-R3-1c ② — pinned 증거 ──
PIN_T = "tests/application/test_nodes.py::test_코드가_지정한_홉과_같은_태스크만_pinned이고_리드가_단_pinned는_지운다"
PIN_WIRE_T = "tests/application/test_nodes.py::test_integrate는_입구_결과의_홉을_받을_때_넘긴다"
PIN_B_T = "tests/application/test_briefing.py::test_입구_읽기와_pinned_태스크의_증거_본문은_예산에서_빠지지_않는다"
CASES += [
 ("홉과 같은 태스크에 pinned를 안 단다", ND, "        kept = [t.model_copy(update={\"pinned\": True}) if (t.action, t.params) == hop else t for t in kept]", "        kept = list(kept)", [PIN_T]),
 ("리드가 단 pinned를 안 지운다", ND, "                                   \"input_evidence_ids\": ids, \"pinned\": False, \"walked\": False})", "                                   \"input_evidence_ids\": ids, \"walked\": False})", [PIN_T]),
 ("integrate가 홉을 받을 때 안 넘긴다", ND, "                hop=_hop(base, deps),\n", "", [PIN_WIRE_T]),
 ("고정 증거의 본문을 안 남긴다", B, "    detailed = {ref.id for ref in state.evidence if ref.id in pinned}", "    detailed = set()", [PIN_B_T]),
 ("고정 증거가 예산을 안 쓴다", B, "    used = sum(len(ref.body or ref.summary) for ref in state.evidence if ref.id in pinned)", "    used = 0", [PIN_B_T]),
 ("입구 읽기를 고정으로 안 본다", B, "        if t.pinned or (state.target is not None and t.id in (\"t-0\", \"t-1\")):", "        if t.pinned:", [PIN_B_T]),
 ("입구가 안 돈 케이스의 t-0도 고정한다", B, "        if t.pinned or (state.target is not None and t.id in (\"t-0\", \"t-1\")):", "        if t.pinned or t.id in (\"t-0\", \"t-1\"):", [PIN_B_T]),
]

# ── 12a-R3-1c ③ — 예시 홉 하나 · 입력 자원 칸 · 중복 기록 홉 · code.read 안내 ──
LAD3_T = "tests/application/test_briefing.py::test_홉이_있으면_integrate_예시는_그_칸_하나이고_code_resources_뒤는_입력_자원마다_읽기_한_칸이다"
DUP3_T = "tests/application/test_nodes.py::test_입구가_이미_한_읽기를_또_내면_거부_기록에_다음_홉을_적는다"
REJ3_T = "tests/application/test_nodes.py::test_거부_기록의_증거_id는_홉_안내가_붙어도_그대로_읽힌다"
READ3_T = "tests/infrastructure/test_deployed_code.py::test_code_read에_다른_서비스의_경로를_주면_그_경로가_어느_서비스에_있는지_안내한다"
CASES += [
 ("홉이 있어도 예시에 둘째 칸을 둔다", B, "        if lead and next_hop(target, code_index=code_index, walk=walk) is not None:\n            shapes = []", "        if False:\n            shapes = []", [LAD3_T]),
 ("code.resources 뒤에 입력 칸이 없다", B, "        elif action == \"code.resources\":\n            # 쓴 쪽이", "        elif False:\n            # 쓴 쪽이", [LAD3_T]),
 ("쓰기 구간의 자원도 입력으로 센다", ROOT / "src/application/walk.py", "    return _RESOURCE.findall(text[:m.start()] if m else text)", "    return _RESOURCE.findall(text)", [LAD3_T]),
 ("못 채우는 템플릿도 get으로 낸다", B, "            shape = (\"redis.get\", {\"key\": filled}) if filled is not None else (\"redis.scan\", {\"pattern\": f\"{key_head(name)}*\"})", "            shape = (\"redis.get\", {\"key\": filled if filled is not None else name})", [LAD3_T]),
 ("낸 입력 칸을 또 보여 준다", B, "        if not _available(site_config, [shape], 1, services) or _issued(tasks, shape[0], **{ident: shape[1][ident]}):", "        if not _available(site_config, [shape], 1, services):", [LAD3_T]),
 ("없는 시스템의 입력도 보여 준다", B, "        if not _available(site_config, [shape], 1, services) or _issued(tasks, shape[0], **{ident: shape[1][ident]}):", "        if _issued(tasks, shape[0], **{ident: shape[1][ident]}):", [LAD3_T]),
 ("리드 읽기 중복에 홉을 안 붙인다", ND, "                   else f\" — {hint}\" if hint else \"\")", "                   else \"\")", [DUP3_T]),
 ("홉 자체의 중복에도 홉 줄을 붙인다", ND, "                   else \" — 그 홉은 이미 돌았다. 예시의 첫 칸(그 결과를 입력으로 한 다음 읽기)을 내라\" if hop is not None and (task.action, task.params) == hop\n", "", [DUP3_T]),
 ("거부 기록의 증거 id에 홉 안내가 섞인다", ND, "            out[head] = tail.rsplit(DUP_WHERE, 1)[1].split(\" — \", 1)[0].split(\", \")", "            out[head] = tail.rsplit(DUP_WHERE, 1)[1].split(\", \")", [REJ3_T]),
 ("다른 레포의 경로에 안내를 안 붙인다", DCP, "            return ProbeResult.failed(got.error + self._elsewhere(path, known.repo), source=pinned, clock=self._clock)", "            return ProbeResult.failed(got.error, source=pinned, clock=self._clock)", [READ3_T]),
 ("같은 레포의 없는 경로에도 안내를 붙인다", DCP, "        repos = sorted({s.repo for s in self._index.symbols if s.kind == \"module\" and s.file == path and s.repo != repo})", "        repos = sorted({s.repo for s in self._index.symbols if s.kind == \"module\" and s.file == path} or {repo})", [READ3_T]),
]

# ── 12a-R3-3 ① — 행 목록 요약 · frame 예시 한 칸 · code.uses 클래스 안내 ──
PROFILE_T = "tests/application/test_runner_probe.py::test_행_목록_값은_코드가_요약한다_행수_필드수_모든_행이_같은_값인_필드"
FRAME1_T = "tests/application/test_briefing.py::test_입구가_돌았으면_케이스_블록에_입구_줄이_실리고_frame_예시_첫_칸은_접수_읽기가_아니라_다음_홉이다"
USES_CLS_T = "tests/infrastructure/test_deployed_code.py::test_code_uses에_클래스_이름을_주면_code_resources로_안내한다"
CASES += [
 ("단일값 필드를 안 센다", RP, "    for key, value in rows[0].items():\n        marker = json.dumps(value, sort_keys=True, default=str)", "    for key, value in {}.items():\n        marker = json.dumps(value, sort_keys=True, default=str)", [PROFILE_T]),
 ("한 행이어도 단일값을 적는다", RP, "    if len(rows) < 2:\n        return []\n    out = []", "    if False:\n        return []\n    out = []", [PROFILE_T]),
 ("JSON 문자열 값을 안 푼다", RP, "        decoded = _json_value(value)\n        if decoded is not None:", "        decoded = None\n        if decoded is not None:", [PROFILE_T]),
 ("요약 줄에 프로필을 안 싣는다", RP, "    head = _first_profile(data)\n    if isinstance(data, list):", "    head = \"\"\n    if isinstance(data, list):", [PROFILE_T]),
 ("JSON 안의 목록은 요약에 안 싣는다", RP, "            inner = value if _rows_or_empty(value) or isinstance(value, dict) else _json_value(value)", "            inner = value if _rows_or_empty(value) or isinstance(value, dict) else None", [PROFILE_T]),
 ("홉이 있어도 frame 예시에 code.grep을 둔다", B, "        if first_read and not hop and _has(site_config, ACTIONS[first_read[0]][0], services):", "        if first_read and _has(site_config, ACTIONS[first_read[0]][0], services):", [FRAME1_T]),
 ("홉이 있어도 frame 예시에 발견 읽기를 채운다", B, "        room = 0 if hop else (4 if shapes else 3) - len(shapes)", "        room = (4 if shapes else 3) - len(shapes)", [FRAME1_T]),
 ("클래스 이름에 안내를 안 한다", DCP, "            if any(self._index.symbols[sid].kind == \"class\" for sid in qy.find(self._index, name)):", "            if False:", [USES_CLS_T]),
]

# ── 12a-R3-3 ②-a — 걷기 본체 ──
WK = ROOT / "src/application/walk.py"
WALK_T = "tests/application/test_walk.py::test_걷기는_쓴_쪽의_입력을_읽고_나쁜_입력의_쓴_쪽으로_한_홉_더_가며_입력이_다_정상이면_멈춘다"
UNKNOWN_T = "tests/application/test_walk.py::test_나쁜_입력의_쓴_쪽을_모르면_거기서_멈추고_다음_홉은_그_키의_uses이며_못_채우는_템플릿은_scan_뒤_다섯_개까지다"
CAP_T = "tests/application/test_walk.py::test_걷기의_상한은_깊이_읽기_시간이고_상한에서_멈추면_다음_홉은_그_자리의_resources다"
STATUS_T = "tests/application/test_walk.py::test_상태는_없음_0행_빈_배열만_나쁘고_정상이면_행_요약을_단다"
WNODE_T = "tests/application/test_nodes.py::test_걷기_노드는_입구_뒤_frame_전에_돌고_State에_walk를_둔다"
WHOP_T = "tests/application/test_nodes.py::test_걷기가_있으면_받을_때의_홉과_거부_기록의_홉은_걷기가_멈춘_자리다"
WBRIEF_T = "tests/application/test_briefing.py::test_걷기가_있으면_케이스_블록에_홉_표가_실리고_다음_홉은_멈춘_자리이며_걸은_읽기는_접힌다"
CASES += [
 ("출력을 입력으로 센다", WK, "                if w and w == writer:\n", "                if False:\n", [WALK_T]),
 ("나쁜 입력의 쓴 쪽으로 안 간다", WK, "                frontier += [(w, b, depth + 1, ev, carry, pointer_of(_unwrap(raw)) if raw is not None else {}) for b in next_branches]", "                pass", [WALK_T]),
 ("정상인 입력으로도 간다", WK, "                if bad and w:\n                    next_branches, shown, cands = branches(w, name)", "                if w:\n                    next_branches, shown, cands = branches(w, name)", [WALK_T]),
 ("쓴 쪽 모름을 걷는다", WK, "                if bad and w:\n                    next_branches, shown, cands = branches(w, name)", "                if bad:\n                    next_branches, shown, cands = branches(w, name)", [UNKNOWN_T]),
 ("대상 키를 또 읽는다", WK, "    seen = {target.key, target.declared}", "    seen = set()", [WALK_T]),
 ("쓰기 구간도 걷는다", WK, "    m = _WRITES_AT.search(text)\n    return _RESOURCE.findall(text[:m.start()] if m else text)", "    return _RESOURCE.findall(text)", [WALK_T]),
 ("깊이 상한이 없다", WK, "        if depth >= MAX_DEPTH:", "        if False:", [CAP_T]),
 ("읽기 상한이 없다", WK, "        if counter >= MAX_READS:\n            return f\"읽기", "        if False:\n            return f\"읽기", [CAP_T]),
 ("시간 상한이 없다", WK, "        if elapsed() > MAX_SECONDS:", "        if False:", [CAP_T]),
 ("쓴 쪽 모름에서 uses를 안 가리킨다", WK, "            rungs.append(Rung(action=\"code.uses\", params={\"name\": declared_names.get(ev, stop_hop.key)}, evidence=ev))", "            pass", [UNKNOWN_T]),
 ("scan 뒤 전부 읽는다", WK, "for k in scanned_keys[:SCAN_READS]]", "for k in scanned_keys[:SCAN_READS * 2]]", [UNKNOWN_T]),
 ("읽기 실패를 나쁨으로 본다", WK, "                    status, bad = f\"읽기 실패 — {got.error or '증거 없음'}\", False", "                    status, bad = f\"읽기 실패 — {got.error or '증거 없음'}\", True", [UNKNOWN_T]),
 ("없음을 정상으로 본다", WK, "    if raw is None:\n        return \"없음\", True", "    if raw is None:\n        return \"정상\", False", [STATUS_T]),
 ("빈 배열을 정상으로 본다", WK, "        if arrays or objects:\n            return empty_text(arrays, objects), True", "        if False:\n            return empty_text(arrays, objects), True", [STATUS_T]),
 ("걷기 노드가 안 돈다", ND, "            return await run_walk(state, runner=deps.runner, classes=deps.classes, class_services=deps.class_services,\n                                  service_repos=deps.service_repos, declared_by=deps.declared_by,\n                                  topic_producers=deps.topic_producers, lookup=deps.filter_lines, clock=deps.clock)", "            return {}", [WNODE_T]),
 ("인덱스 없이도 걷는다", ND, "        if (state.walk is not None or state.target is None or not state.target.writer or not deps.code_index\n                or not deps.check_discovery):", "        if (state.walk is not None or state.target is None or not state.target.writer\n                or not deps.check_discovery):", [WNODE_T]),
 ("걸은 읽기를 입구 읽기로 안 센다", ND, "    return frozenset(e for t in state.plan_tasks if t.id in (\"t-0\", \"t-1\") or t.walked for e in t.result_evidence_ids)", "    return frozenset(e for t in state.plan_tasks if t.id in (\"t-0\", \"t-1\") for e in t.result_evidence_ids)", [WHOP_T]),
 ("받을 때의 홉이 걷기를 안 본다", ND, "    return next_hop(state.target, code_index=deps.code_index, walk=walk)", "    return next_hop(state.target, code_index=deps.code_index)", [WHOP_T]),
 ("다음 홉이 걷기를 안 본다", IM, "    if walk is not None and walk.hops:\n        return walk.next_hop() if code_index else None", "    if False:\n        return walk.next_hop() if code_index else None", [WBRIEF_T]),
 ("홉 표를 안 싣는다", IM, "    if state.walk is not None:\n        lines += state.walk.lines()", "    if False:\n        lines += state.walk.lines()", [WBRIEF_T]),
 ("나쁜 홉의 증거를 안 고정한다", B, "        ids |= set(state.walk.chain_ids())", "        pass", [WBRIEF_T]),
 ("걸은 읽기 본문을 안 접는다", B, "        if ref.id in pinned or ref.id in folded:\n            continue", "        if ref.id in pinned:\n            continue", [WBRIEF_T]),
 ("걸은 태스크를 한 줄로 안 접는다", B, "        if task.walked:\n            # 걷기 노드의 읽기는 한 줄로", "        if False:\n            # 걷기 노드의 읽기는 한 줄로", [WBRIEF_T]),
 ("사다리 칸의 입력이 걷기의 증거가 아니다", B, "                inputs = ([rung.evidence] if rung is not None and rung.evidence\n                          else [key_read.result_evidence_ids[0]] if key_read else [])", "                inputs = [key_read.result_evidence_ids[0]] if key_read else []", [WBRIEF_T]),
 ("class_services가 서비스를 안 단다", DCP, "                out.setdefault(s.name, set()).update([svc] if svc else shared.get(s.repo, ()))", "                out.setdefault(s.name, set())", ["tests/infrastructure/test_deployed_code.py::test_class_services는_클래스_이름을_그_파일의_서비스에_잇는다"]),
 ("source-missing에 원천 키가 있다", ROOT / "tools/local_case.py", "            **({} if variant == \"source-missing\" else", "            **({} if False else", ["tests/test_local_case.py::test_source_missing은_원천_키가_없고_source_blank은_빈_배열이며_둘_다_요약_키의_data는_빈_배열이다"]),
]

# ── 12a-R3-3 ②-b — 두 레포의 같은 클래스 이름 ──
TIE_T = "tests/application/test_walk.py::test_같은_클래스_이름이_두_레포면_그_키를_선언한_서비스로_가르고_못_가르면_둘_다_걷는다"
RES_SVC_T = "tests/infrastructure/test_deployed_code.py::test_code_resources는_service로_레포를_좁히고_같은_이름이_두_서비스에_있으면_그렇게_안내한다"
DECL_T = "tests/knowledge/test_flow.py::test_declaring_services는_그_자원을_config로_선언한_서비스들이다"
CASES += [
 ("선언한 서비스로 안 가른다", WK, "        if len(picked) == 1:\n            repo, svcs = picked[0]", "        if False:\n            repo, svcs = picked[0]", [TIE_T]),
 ("못 가르면 한 쪽만 걷는다", WK, "        return [_Branch(params_for(r, s), shown_as(r, s), s) for r, s in found], shown, cands", "        return [_Branch(params_for(r, s), shown_as(r, s), s) for r, s in found[:1]], shown, cands", [TIE_T]),
 ("한 곳뿐인데도 service 인자를 단다", WK, "            return [_Branch({}, \"\", cands)], shown, cands", "            return [_Branch({\"service\": cands[0]} if len(cands) == 1 else {}, \"\", cands)], shown, cands", [TIE_T]),
 ("깊은 홉을 대상 키의 선언으로 가른다", WK, "                    next_branches, shown, cands = branches(w, name)", "                    next_branches, shown, cands = branches(w, target.declared)", [TIE_T]),
 ("service로 레포를 안 좁힌다", DCP, "            text = f\"{known.repo}:{name}\"\n        elif repo:\n            # 한 레포를", "            text = name\n        elif repo:\n            # 한 레포를", [RES_SVC_T]),
 ("두 서비스 안내를 안 한다", DCP, "            lines += self._repo_hint(found)", "            pass", [RES_SVC_T]),
 ("등재 목록에 service 인자가 없다", ROOT / "src/domain/actions.py", "    \"code.resources\":      (\"code\",  \"resources\",     (\"name\",),               (\"service\", \"repo\")),", "    \"code.resources\":      (\"code\",  \"resources\",     (\"name\",),               (\"repo\",)),", [RES_SVC_T]),
 ("declares가 아닌 엣지도 선언으로 센다", ROOT / "src/knowledge/flow.py", "                         if e.get(\"relation\") == \"declares\" and e.get(\"target\") in targets", "                         if e.get(\"target\") in targets", [DECL_T]),
 ("걷기 노드가 선언 서비스를 안 넘긴다", ND, "declared_by=deps.declared_by,\n                                  topic_producers=deps.topic_producers,", "topic_producers=deps.topic_producers,", [WNODE_T]),
 ("CLI가 없는 서비스를 안 거른다", MN, "            return 1\n        text = f\"{known.repo}:{args.name}\"", "            pass\n        text = f\"{known.repo}:{args.name}\"", ["tests/knowledge/test_cli_code.py::test_code_resources가_클래스가_읽고_쓰는_자원에_답한다"]),
]

# ── 12a-R3-3 ③ — 단일값 필드의 나쁨은 코드 줄이 정한다 ──
LIT_T = "tests/application/test_walk.py::test_단일값_필드는_읽는_쪽_코드_줄에_필드와_값이_한_비교식_양쪽에_있을_때만_나쁘고_그_값이_이어지는_동안_위로_간다"
FILTER_T = "tests/infrastructure/test_deployed_code.py::test_filter_lines는_이_사이트의_등록이_가리키는_클래스_파일을_범위로_비교식을_먼저_뽑아_단일값_전부와_맞춘다"
LIT_NEXT_T = "tests/application/test_walk.py::test_리터럴_줄을_잡고도_위가_다_정상이면_다음_홉은_그_코드_줄_둘레다"
CASES += [
 ("값이 리터럴이 아니어도 센다", DCP, '_LITERAL = r"""(?P<q4>["\'])(?P<v>[^"\'\\n]*)(?P=q4)"""', '_LITERAL = r"""(?P<q4>["\']?)(?P<v>[^"\'\\s]*)(?P=q4)"""', [FILTER_T]),
 ("필드가 리터럴이 아니어도 센다", DCP, '_ACCESS = r"""(?:\\[\\s*(?P<q1>["\'])(?P<f1>[^"\'\\n]+)(?P=q1)\\s*\\]|\\.get\\(\\s*(?P<q2>["\'])(?P<f2>[^"\'\\n]+)(?P=q2)[^()]*\\)|getattr\\([^(),]+,\\s*(?P<q3>["\'])(?P<f3>[^"\'\\n]+)(?P=q3)[^()]*\\))"""', '_ACCESS = r"""(?:\\[\\s*(?P<q1>["\']?)(?P<f1>[^"\'\\n]+)(?P=q1)\\s*\\]|\\.get\\(\\s*(?P<q2>["\'])(?P<f2>[^"\'\\n]+)(?P=q2)[^()]*\\)|getattr\\([^(),]+,\\s*(?P<q3>["\'])(?P<f3>[^"\'\\n]+)(?P=q3)[^()]*\\))"""', [FILTER_T]),
 ("같은 단일값이 이어져도 위로 안 간다", WK, "        if not bad and watch is not None and same.get(watch[0]) == watch[1]:", "        if False:", [LIT_T]),
 ("단일값 조회에 가지의 인자를 안 넘긴다", WK, "            scan = await lookup(writer, fields=same, **branch.params)", "            scan = await lookup(writer, fields=same)", [LIT_T, WNODE_T]),
 ("단일값 검사를 안 한다", WK, "        elif not bad and same and status.startswith(\"정상\") and lookup is not None:", "        elif False:", [LIT_T]),
 ("리터럴 줄을 다음 홉으로 안 가리킨다", WK, "    if literal is not None:\n        # 아래에", "    if False:\n        # 아래에", [LIT_NEXT_T]),
 ("다음 홉 줄이 name 인자를 가정한다", IM, "        return f\"다음 홉: {describe(*hop)} — 걷기가 멈춘 자리({walk.stopped})\"", "        return f\"다음 홉: {hop[0]} name={hop[1]['name']!r} — 걷기가 멈춘 자리({walk.stopped})\"", [LIT_NEXT_T]),
 ("잡은 단일값을 위로 안 넘긴다", WK, "                frontier += [(w, b, depth + 1, ev, carry, pointer_of(_unwrap(raw)) if raw is not None else {}) for b in next_branches]", "                frontier += [(w, b, depth + 1, ev, None, pointer_of(_unwrap(raw)) if raw is not None else {}) for b in next_branches]", [LIT_T]),
]

# ── 12a-R3-3 ⑤ — 클래스 이름 component는 서비스로 ──
CLS_T = "tests/application/test_verdict.py::test_verify는_클래스_이름_component를_인덱스의_서비스로_바꾸고_caveat을_단다"
CASES += [
 ("클래스 이름을 서비스로 안 바꾼다", ND, "        return link.model_copy(update={\"component\": picked})", "        return link", [CLS_T]),
 ("두 레포에서 걷기가 가른 쪽을 안 쓴다", ND, "        candidates = resolved.get(link.component) or services", "        candidates = services", [CLS_T]),
 ("번역에 caveat을 안 단다", ND, "    return verdict.model_copy(update={\"root_cause\": root, \"alternates\": alternates, \"contributing\": contributing,\n                                      \"caveats\": verdict.caveats + caveats})", "    return verdict.model_copy(update={\"root_cause\": root, \"alternates\": alternates, \"contributing\": contributing})", [CLS_T]),
 ("verify가 번역 전 판정을 검사한다", ND, "        verdict = translate_classes(state.verdict, class_services=deps.class_services, walk=state.walk, service_repos=deps.service_repos)", "        verdict = state.verdict", [CLS_T]),
 ("번역한 판정을 State에 안 둔다", ND, "            return {\"verify_problems\": [], **({\"verdict\": capped} if (changed or capped is not verdict) else {})}", "            return {\"verify_problems\": []} if capped is verdict else {\"verdict\": capped, \"verify_problems\": []}", [CLS_T]),
]

# ── 12a-R3-3 ④-a — code.read line= : 그 줄을 품은 함수 전체 ──
READ_LINE_T = "tests/infrastructure/test_deployed_code.py::test_code_read에_line을_주면_그_줄을_품은_가장_안쪽_함수_전체를_준다"
CASES += [
 ("line이 품은 함수를 안 찾는다", DCP, "            sym = self._enclosing(known.repo, path, line)\n            if sym is not None:", "            sym = None\n            if sym is not None:", [READ_LINE_T]),
 ("안쪽이 아니라 바깥 심볼을 고른다", DCP, "        return min(inside, key=lambda s: s.end_line - s.line) if inside else None", "        return max(inside, key=lambda s: s.end_line - s.line) if inside else None", [READ_LINE_T]),
 ("line 0을 받는다", DCP, "            if isinstance(line, bool) or not isinstance(line, int) or line < 1:", "            if False:", [READ_LINE_T]),
 ("등재 목록에 line 인자가 없다", ROOT / "src/domain/actions.py", "    \"code.read\":           (\"code\",  \"read\",          (\"service\", \"path\"),     (\"offset\", \"limit\", \"line\")),", "    \"code.read\":           (\"code\",  \"read\",          (\"service\", \"path\"),     (\"offset\", \"limit\")),", [READ_LINE_T]),
 ("리터럴 홉이 줄 둘레로 간다", WK, "            head = Rung(action=\"code.read\", params={\"service\": scope, \"path\": path, \"line\": line}, evidence=gev)", "            head = Rung(action=\"code.read\", params={\"service\": scope, \"path\": path, \"offset\": max(1, line - 15), \"limit\": 40}, evidence=gev)", [LIT_NEXT_T]),
]


# ── 12a-R3-4 ① — 레포 단위 가르기·가지 격리·code.uses service=·code.resources repo= ──
SHARED_T = "tests/application/test_walk.py::test_한_레포를_여러_서비스가_공유하면_레포_단위로_가르고_표에_공유로_적으며_그_레포를_resources에_넘긴다"
ISOLATE_T = "tests/application/test_walk.py::test_한_가지의_resources_실패는_그_행에만_적고_다른_가지는_계속_걷는다"
REPO_HINT_T = "tests/knowledge/test_query.py::test_repo_hint는_찾은_심볼이_레포_둘_이상에_걸칠_때만_레포와_그_서비스들을_한_줄로_안내한다"
REPO_SVC_T = "tests/knowledge/test_flow.py::test_repo_services는_레포마다_그_레포를_쓰는_서비스들을_이름순으로_준다"
PAL_T = "tests/test_local_case.py::test_palette_empty는_공용_팔레트_키가_빈_배열이고_그_쓴_쪽_클래스는_서비스_디렉터리_밖과_다른_레포에_같은_이름으로_있다"
ACT_USES_T = "tests/domain/test_actions.py::test_역질문_둘이_이름_하나로_등재돼_있고_코드_추적_레인이다"
CLI_RES_T = "tests/knowledge/test_cli_code.py::test_code_resources가_클래스가_읽고_쓰는_자원에_답한다"
QY = ROOT / "src/knowledge/query.py"
FLW = ROOT / "src/knowledge/flow.py"
CASES += [
 ("서비스를 레포로 안 묶는다", WK, "            by_repo.setdefault(repos.get(s, s), []).append(s)", "            by_repo.setdefault(s, []).append(s)", [SHARED_T]),
 ("공유 레포를 표에 안 적는다", WK, "        return svcs[0] if len(svcs) == 1 else f\"{repo}(서비스 {len(svcs)}개 공유)\"", "        return svcs[0]", [SHARED_T]),
 ("가른 레포를 resources에 안 넘긴다", WK, "        return {\"service\": svcs[0]} if len(svcs) == 1 else {\"repo\": repo}", "        return {\"service\": svcs[0]} if len(svcs) == 1 else {}", [SHARED_T]),
 ("후보를 홉에 안 적는다", WK, "candidates=list(first_cands),", "candidates=[],", [SHARED_T, TIE_T]),
 ("깊은 홉의 후보를 안 적는다", WK, "service=shown, candidates=list(cands), status=status", "service=shown, status=status", [SHARED_T, TIE_T]),
 ("한 가지의 실패가 걷기를 끊는다", WK, "            failed_at[came_from] = (f\"{writer}{tag}\", why, here)\n            continue", "            failed_at[came_from] = (f\"{writer}{tag}\", why, here)\n            break", [ISOLATE_T]),
 ("실패를 그 행에 안 적는다", WK, "            hops[at] = hops[at].model_copy(update={\"status\": f\"{hops[at].status} · {writer}{tag}의 자원 못 읽음 — {why}\"})", "            pass", [ISOLATE_T, UNKNOWN_T]),
 ("실패를 멈춘 이유로 안 적는다", WK, "        elif ev in failed_at:\n            tag, why, here = failed_at[ev]", "        elif False:\n            tag, why, here = failed_at[ev]", [ISOLATE_T, UNKNOWN_T]),
 ("실패를 여기로 오게 한 행이 아니라 마지막 행에 적는다", WK, "            at = next((i for i, h in enumerate(hops) if h.evidence_id == came_from and h.writer == writer), len(hops) - 1)", "            at = len(hops) - 1", [ISOLATE_T]),
 ("못 가른 파일의 클래스를 버린다", DCP, "                out.setdefault(s.name, set()).update([svc] if svc else shared.get(s.repo, ()))", "                out.setdefault(s.name, set()).update([svc] if svc else [])", [RES_SVC_T]),
 ("서비스 지도를 어댑터가 안 낸다", DCP, "        return {name: svc.repo for name, svc in self._topology.services.items()}", "        return {}", [RES_SVC_T]),
 ("repo로 레포를 안 좁힌다", DCP, "            text = f\"{repo}:{name}\"\n        found = qy.find(self._index, text)", "            text = name\n        found = qy.find(self._index, text)", [RES_SVC_T]),
 ("없는 레포를 안 거른다", DCP, "            if repo not in known_repos:", "            if False:", [RES_SVC_T]),
 ("uses가 service로 안 좁힌다", DCP, "            found = [u for u in found if self._index.symbols[u.sid].repo == known.repo]", "            pass", [RES_SVC_T]),
 ("uses가 없는 서비스를 안 거른다", DCP, "            known = self._topology.services.get(service)\n            if known is None:\n                return ProbeResult.failed(f\"없는 서비스 — {service}. 아는 것: {', '.join(sorted(self._topology.services)) or '없음'}\",\n                                          source=source, clock=self._clock)\n        if name in self._topology.services:", "            known = self._topology.services.get(service)\n            if False:\n                return ProbeResult.failed(f\"없는 서비스 — {service}. 아는 것: {', '.join(sorted(self._topology.services)) or '없음'}\",\n                                          source=source, clock=self._clock)\n        if name in self._topology.services:", [RES_SVC_T]),
 ("레포 안내를 안 한다", QY, "    if len(repos) < 2:\n        return []", "    if len(repos) < 3:\n        return []", [REPO_HINT_T, RES_SVC_T]),
 ("레포 안내에 서비스를 안 적는다", QY, "    shown = \", \".join(f\"{repo}({', '.join(repo_services.get(repo, ())) or '서비스 없음'})\" for repo in repos)", "    shown = \", \".join(repos)", [REPO_HINT_T, RES_SVC_T]),
 ("repo_services가 서비스를 안 묶는다", FLW, "        out.setdefault(svc.repo, []).append(name)", "        out.setdefault(svc.repo, [name])", [REPO_SVC_T, RES_SVC_T]),
 ("등재 목록에 uses의 service 인자가 없다", AC, "    \"code.uses\":           (\"code\",  \"uses\",          (\"name\",),               (\"service\",)),", "    \"code.uses\":           (\"code\",  \"uses\",          (\"name\",),               ()),", [ACT_USES_T, RES_SVC_T]),
 ("번역이 걷기의 후보를 안 쓴다", ND, "    resolved = {h.writer: tuple(h.candidates) for h in (walk.hops if walk is not None else []) if h.writer and h.candidates}", "    resolved = {}", [CLS_T]),
 ("공유 레포 후보에 caveat을 안 단다", ND, "            note = (f\" (레포를 {', '.join(candidates)}가 공유 — 첫 것, 전부 후보)\" if shared", "            note = (\"\" if shared", [CLS_T]),
 ("걷기 노드가 레포 지도를 안 넘긴다", ND, "service_repos=deps.service_repos, declared_by=deps.declared_by", "declared_by=deps.declared_by", [WNODE_T]),
 ("CLI가 없는 레포를 안 거른다", MN, "        if args.repo not in repos:", "        if False:", [CLI_RES_T]),
 ("CLI uses가 없는 서비스를 안 거른다", MN, "            return 1\n        found = [u for u in found if index.symbols[u.sid].repo == known.repo]", "            pass\n        found = [u for u in found if index.symbols[u.sid].repo == known.repo]", [CLI_RES_T]),
 ("palette-empty에 색이 있다", LC, "\"colors\": [] if variant in (\"palette-empty\", \"palette-unread\") else", "\"colors\": [] if variant in (\"palette-unread\",) else", [PAL_T]),
 ("palette-empty가 빈 data가 아니다", LC, "\"source-filtered\", \"palette-empty\", \"chain-empty\"", "\"source-filtered\", \"chain-empty\"", [PAL_T]),
]


# ── 12a-R3-4 ② — 멈춘 자리 = 가장 깊은 나쁜 홉, 다음 칸은 늘, pinned는 사슬만, 걷기 id 한 단계 ──
DEEP_T = "tests/application/test_walk.py::test_멈춘_자리는_가장_깊은_나쁜_홉이고_다음_칸은_늘_있으며_입력이_없으면_입력을_모른다고_적는다"
LADDER2_T = "tests/application/test_briefing.py::test_걷기의_칸들은_사다리가_한_라운드에_한_칸씩_밟고_낸_칸은_건너뛴다"
CASES += [
 ("멈춘 자리가 가장 깊은 나쁜 홉이 아니다", WK, "    stop_hop = max(bad_hops, key=lambda h: h.depth) if bad_hops else hops[0]", "    stop_hop = min(bad_hops, key=lambda h: h.depth) if bad_hops else hops[0]", [DEEP_T, WALK_T]),
 ("입력이 0이어도 정상이라 한다", WK, "                       else f\"입력을 모른다 — {info.writer}(인덱스 자원 0 — 메시지 입력일 수 있다{none})\" if not info.listed", "                       else f\"입력을 모른다 — {info.writer}(인덱스 자원 0 — 메시지 입력일 수 있다{none})\" if False", [DEEP_T]),
 ("입력 0의 칸이 클래스 본문이 아니다", WK, "at=(reader_at(text) if real or missed else None) or class_at(text))", "at=reader_at(text))", [DEEP_T]),
 ("입력 정상의 칸이 읽는 메서드가 아니다", WK, "at=(reader_at(text) if real or missed else None) or class_at(text))", "at=class_at(text))", [WALK_T]),
 ("홉의 부모를 안 적는다", WK, "basis=f\"{writer}{tag}가 읽는 자원 ({res_id})\", parent=came_from))", "basis=f\"{writer}{tag}가 읽는 자원 ({res_id})\"))", [DEEP_T]),
 ("사슬이 부모를 안 따라간다", CS, "            cur = by_id.get(cur.parent) if cur.parent else None", "            cur = None", [DEEP_T]),
 ("사슬 밖의 나쁜 홉도 고정한다", B, "        ids |= set(state.walk.chain_ids())", "        ids |= set(state.walk.bad_ids())", [WBRIEF_T]),
 ("접는 범위가 사슬 밖이 아니다", B, "    return frozenset(ids - set(state.walk.chain_ids()))", "    return frozenset(ids - set(state.walk.bad_ids()))", [WBRIEF_T]),
 ("걷기 읽기에 walked를 안 단다", WK, ".model_copy(update={\"walked\": True})", "", [WALK_T, WNODE_T]),
 ("걷기 id가 다음 빈 번호부터가 아니다", WK, "        task = _task(f\"t-{start + counter - 1}\", goal, action, params)", "        task = _task(f\"t-{counter}\", goal, action, params)", [WALK_T]),
 ("리드가 단 walked를 안 지운다", ND, "\"pinned\": False, \"walked\": False})", "\"pinned\": False})", [PIN_T]),
 ("사다리가 낸 칸을 건너뛰지 않는다", B, "        pending = next((r for r in rungs if not _issued(tasks, r.action, **r.params)), rungs[-1])", "        pending = rungs[0]", [LADDER2_T]),
 ("둘째 칸을 안 고정한다", ND, "        return next(((r.action, dict(r.params)) for r in walk.rungs\n", "        return next(((r.action, dict(r.params)) for r in walk.rungs[:1]\n", [WHOP_T]),
 ("대기 태스크를 낸 칸으로 센다", ND, "        ran = {_query(t) for t in state.plan_tasks if t.action and t.status != \"pending\"}", "        ran = {_query(t) for t in state.plan_tasks if t.action}", [WHOP_T]),
 ("stop_at을 안 적는다", WK, "stopped=stopped, stop_at=ev, rungs=rungs, notes=notes)", "stopped=stopped, rungs=rungs, notes=notes)", [DEEP_T, WALK_T]),
 ("상한 멈춤의 칸을 안 낸다", WK, "    elif nxt is not None:\n        rungs.append(Rung(action=nxt[0], params=nxt[1], evidence=nxt_ev))", "    elif False:\n        rungs.append(Rung(action=nxt[0], params=nxt[1], evidence=nxt_ev))", [CAP_T]),
 ("리터럴 칸이 첫 칸이 아니다", WK, "            rungs = [head] + [r for r in rungs if r != head]", "            rungs = [r for r in rungs if r != head] + [head]", [LIT_NEXT_T]),
 ("walked로 입구 읽기 줄을 안 센다", IM, "    walked = [t for t in state.plan_tasks if t.walked and t.status == \"ok\"]", "    walked = []", [WBRIEF_T]),
 ("칸이 없는데 입구 홉으로 돌아간다", ND, "    if walk is not None and walk.hops and deps.code_index and walk.rungs:", "    if walk is not None and walk.hops and deps.code_index and walk.rungs and False:", [WHOP_T]),
]


# ── 12a-R3-4 ③ — 단일값 검사는 어댑터 안의 조회(범위·짝) ──
LINK_T = "tests/application/test_walk.py::test_이은_컴포넌트_클래스_본문은_멈춘_자리의_뒤_칸이_되고_못_이음은_일부만_검사로_정상이라_하지_않는다"
LC_SRC_T = "tests/test_local_case.py::test_source_missing은_원천_키가_없고_source_blank은_빈_배열이며_둘_다_요약_키의_data는_빈_배열이다"
CASES += [
 ("주석 줄을 센다", DCP, "        if line.lstrip().startswith(\"#\"):\n            continue", "        if False:\n            continue", [FILTER_T]),
 ("뒤집힌 비교식을 안 센다", DCP, "        for m in (*_FORWARD.finditer(line), *_REVERSED.finditer(line)):", "        for m in _FORWARD.finditer(line):", [FILTER_T]),
 ("컴포넌트를 안 잇는다", DCP, "        for comp in literals:", "        for comp in []:", [FILTER_T]),
 ("등록의 클래스 파일을 범위에 안 넣는다", DCP, "                if csym.file not in files:\n                    files.append(csym.file)", "                if False:\n                    files.append(csym.file)", [FILTER_T]),
 ("변수 get을 못 이음으로 안 적는다", DCP, "        unlinked = [f\"변수 {v}\" for v in variables]", "        unlinked = []", [FILTER_T]),
 ("클래스 파일을 안 본다", DCP, "        files = [sym.file]", "        files = []", [FILTER_T]),
 ("스냅샷 없음을 문제로 안 적는다", DCP, "        if snap is None:\n            return FilterScan(problem=f\"{sym.repo}의 스냅샷을 못 받았다\")", "        if snap is None:\n            return FilterScan()", [FILTER_T]),
 ("나쁨에 행 수를 안 적는다", WK, "status, bad, carry = (f\"나쁨 — {len(rows)}행 전부 {shown_fields}", "status, bad, carry = (f\"나쁨 — 모든 행이 {shown_fields}", [LIT_T]),
 ("통과한 필드를 안 적는다", WK, "                status += f\" · 단일값 {passed} — 거르는 줄 없음({scope_note})\"", "                pass", [LIT_T, LINK_T]),
 ("이은 컴포넌트를 뒤 칸으로 안 낸다", WK, "                          for found_at in linked_at.get(ev, [])[:3]]", "                          for found_at in []]", [LINK_T]),
 ("조회 문제를 일부만 검사로 안 적는다", WK, "            elif scan.problem:", "            elif False:", [LINK_T]),
 ("링크를 안 모은다", WK, "            linked_at[came_from] = list((await lookup(writer, fields=dict([watch]) if watch else {}, **branch.params)).linked_at)", "            linked_at[came_from] = []", [LINK_T]),
 ("리터럴 칸의 입력 증거가 그 홉의 읽기가 아니다", WK, "                    literal = (scope, hit.path, hit.line, ev)", "                    literal = (scope, hit.path, hit.line, \"\")", [LIT_T]),
 ("못 이음이 있어도 정상이라 한다", WK, "            elif scan.unlinked:", "            elif False:", [LINK_T]),
 ("걷기 노드가 조회를 안 넘긴다", ND, "lookup=deps.filter_lines, ", "", [WNODE_T]),
 ("측정판의 거르는 줄이 컴포넌트에 없다", LC, "        return [r for r in rows if r.get(\"state\") != \"-\" and r.get(\"wg\") != \"-\"]", "        return [r for r in rows if r.get(\"wg\") != \"-\"]", [LC_SRC_T]),
 ("측정판 batch가 컴포넌트를 안 찾는다", LC, "            rows = self.components.get(\"state_filter\").apply(domain.get(\"record\", []) + json.loads(source or \"[]\"))", "            rows = domain.get(\"record\", []) + json.loads(source or \"[]\")", [LC_SRC_T]),
]


# ── 12a-R3-5 ① — 중첩 함수 이름 가림 핫픽스, 던진 걷기도 보인다 ──
SHADOW_T = "tests/application/test_walk.py::test_단일값_통과_뒤_두_레포_가르기가_같은_걷기에서_이어진다"
THROWN_T = "tests/application/test_nodes.py::test_걷기_노드가_던지면_멈춤_사유가_State에_남고_케이스_블록에_보인다"
DIAG_WALK_T = "tests/application/test_diagnose.py::test_걷기가_있으면_홉_읽기_멈춤_사유를_한_줄로_적고_없으면_안_적는다"
DG = ROOT / "src/application/diagnose.py"
CASES += [
 ("단일값 분기가 중첩 함수 이름을 덮는다", WK, "                status, bad, carry = await judged(rows_of(raw) if raw is not None else [], status, bad, ev)", "                status, bad, params_for = await judged(rows_of(raw) if raw is not None else [], status, bad, ev)", [SHADOW_T]),
 ("홉 0이면 멈춤 사유를 안 보인다", CS, "        if not self.hops and not self.stopped:", "        if not self.hops:", [SHADOW_T, THROWN_T]),
 ("진단에 걷기 줄이 없다", DG, "    if state.walk is not None:\n        # 걷기가 있었으면 한 줄", "    if False:\n        # 걷기가 있었으면 한 줄", [DIAG_WALK_T, THROWN_T]),
]


# ── 12a-R3-5 ② — case walk(측정 도구): 입구 + 걷기만, 그래프와 같은 병합 ──
WO = ROOT / "src/application/walk_only.py"
WO_T = "tests/application/test_walk_only.py::test_walk_only는_입구와_걷기만_돌리고_패치를_State_규칙대로_합친다"
WO_PATCH_T = "tests/application/test_walk_only.py::test_apply_patch는_id_병합과_이어_붙이기와_교체를_그래프와_같게_한다"
CW_ARGS_T = "tests/test_cli_case_walk.py::test_case_walk은_케이스_id나_target_하나만_받고_check는_필수이며_틀리면_점검_목록을_보인다"
CW_RUN_T = "tests/test_cli_case_walk.py::test_case_walk은_target으로_입구를_돌리고_케이스_블록만_찍으며_아무것도_남기지_않는다"
CASES += [
 ("패치를 id로 병합하지 않는다", WO, "            update[key] = merge_by_id(getattr(state, key), value)", "            update[key] = list(value)", [WO_T, WO_PATCH_T]),
 ("패치를 이어 붙이지 않는다", WO, "            update[key] = list(getattr(state, key)) + list(value)", "            update[key] = list(value)", [WO_PATCH_T]),
 ("걷기 노드를 안 부른다", WO, "    for name in (\"intake\", \"walk\"):", "    for name in (\"intake\",):", [WO_T]),
 ("둘 다 줘도 받는다", MN, "    if sum(bool(x) for x in (args.case_id, args.target, args.key)) != 1:\n        print(\"  케이스 id 하나나 --target 하나나 --key 하나 — 셋 중 하나만\")", "    if False:\n        print(\"  케이스 id 하나나 --target 하나나 --key 하나 — 셋 중 하나만\")", [CW_ARGS_T]),
 ("--check 없이도 돈다", MN, "        if not args.check:\n            print(f\"  --check가 필요하다 — 점검: {checks}\")", "        if False:\n            print(f\"  --check가 필요하다 — 점검: {checks}\")", [CW_ARGS_T]),
 ("없는 점검을 안 거른다", MN, "        if args.check not in site.patrol.checks:\n            print(f\"  없는 점검 — {args.check}. 점검: {checks}\")", "        if False:\n            print(f\"  없는 점검 — {args.check}. 점검: {checks}\")", [CW_ARGS_T]),
 ("대상 문자열을 안 넘긴다", MN, "                    check=args.check, target=args.target)", "                    check=args.check, target=\"\")", [CW_RUN_T]),
]


# ── 12a-R3-5 ③ — 단일값 재설계: AST 등록·클래스 파일 범위·상한 없음·N/M·행 0 ──
REG_T = "tests/infrastructure/test_deployed_code.py::test_registrations는_AST로_네_모양의_등록을_읽고_문서_문자열_속_예시는_등록으로_안_센다"
EMPTY_ROWS_T = "tests/application/test_runner_probe.py::test_요약_머리는_빈_배열에도_행_0을_적는다"
CASES += [
 ("문자열 아닌 단일값을 안 센다", WK, "            if len(same_all) > len(same):", "            if False:", [LINK_T]),
 ("일부만 검사가 멈춤을 안 바꾼다", WK, "            unseen = [*info.missed, *([\"일부만 검사\"] if ev in partial else [])]", "            unseen = [*info.missed]", [LINK_T]),
 ("일부만 검사를 미확인으로 안 적는다", WK, "                status = (\"미확인\" + status[len(\"정상\"):]", "                status = (status", [LINK_T]),
 ("점 표기 별칭을 모듈로 안 푼다", DCP, "aliases.get(target.value.id, target.value.id)", "None", [REG_T]),
 ("데코레이터 등록을 안 읽는다", DCP, "        elif isinstance(node, ast.ClassDef):", "        elif False:", [REG_T]),
 ("사이트 인자가 다른 등록도 후보다", DCP, "        ok = [r for r in regs if r.component == component and all(mine.get(k) == v for k, v in r.site.items())]", "        ok = [r for r in regs if r.component == component]", [FILTER_T]),
 ("명시 인자가 많은 등록을 안 고른다", DCP, "        best = max(len(r.site) for r in ok)", "        best = 0", [FILTER_T]),
 ("등록 없는 사전 get을 못 이음으로 센다", DCP, "                if any(r.component == comp for r in regs):\n                    unlinked.append", "                if True:\n                    unlinked.append", [FILTER_T]),
 ("나쁜 필드가 든 클래스를 먼저 두지 않는다", DCP, "            linked_at.sort(key=lambda h: 0 if any(q in snap.get(h.path, \"\") for q in quoted) else 1)", "            pass", [FILTER_T]),
 ("빈 배열 요약에 행 0을 안 적는다", RP, "    if not rows:\n        return \"행 0\"", "    if False:\n        return \"행 0\"", [EMPTY_ROWS_T]),
 ("빈 JSON 목록을 안 푼다", RP, "    return parsed if isinstance(parsed, list) or (isinstance(parsed, dict) and parsed) else None", "    return parsed if isinstance(parsed, (list, dict)) and parsed else None", [EMPTY_ROWS_T]),
]


# ── 12a-R3-5 ④ — 측정판 모양(등록 모음 파일·c-1/c-2 사슬)과 정답 홉 표, 같은 줄 짝·뒤 칸 정렬·case walk의 칸 목록 ──
BOARD_C1_T = "tests/test_board_walk.py::test_c1형_빈_배열_사슬은_메시지_입력_원천_클래스에서_입력을_모른다로_멈추고_다음_칸은_그_클래스와_이은_컴포넌트의_본문이다"
BOARD_C2_T = "tests/test_board_walk.py::test_c2형_단일값_사슬은_비교식_줄을_사실로_적고_원천_클래스의_입력이_다_정상이면_멈추며_뒤_칸은_나쁜_필드가_든_컴포넌트가_먼저다"
SAME_LINE_T = "tests/application/test_walk.py::test_한_줄에_짝이_둘이면_한_줄로_센다"
CHAIN_T = "tests/test_local_case.py::test_chain_empty와_chain_filtered는_정답_홉_표의_사슬_모양이다"
REG_BOARD_T = "tests/test_local_case.py::test_등록은_모음_파일에_호출형_네_모양으로_있고_문서_문자열_속_예시는_등록이_아니다"
CASES += [
 ("링크 조회에 이어 온 단일값을 안 싣는다", WK, "fields=dict([watch]) if watch else {}, **branch.params", "fields={}, **branch.params", [LIT_T, BOARD_C2_T]),
 ("같은 줄의 짝을 따로 센다", WK, "lines_hit = len(dict.fromkeys((h.path, h.line) for h in scan.hits))", "lines_hit = len(scan.hits)", [SAME_LINE_T, BOARD_C2_T]),
 ("case walk가 칸을 안 찍는다", MN, "        for number, rung in enumerate(walk.rungs, 1):", "        for number, rung in enumerate([], 1):", [BOARD_C1_T, BOARD_C2_T]),
 ("측정판이 중첩 함수 가림을 못 잡는다", WK, "                status, bad, carry = await judged(rows_of(raw) if raw is not None else [], status, bad, ev)", "                status, bad, params_for = await judged(rows_of(raw) if raw is not None else [], status, bad, ev)", [BOARD_C1_T]),
 ("측정판에 사이트 인자 등록이 없다", LC, "register(component=\"state_filter\", gbm=\"mx\", factory=\"gumi\")(comp.GumiStateFilter)", "register(component=\"state_filter\")(comp.GumiStateFilter)", [REG_BOARD_T]),
 ("측정판 등록이 여러 줄이 아니다", LC, "register(\n    component=\"row_filler\",\n)(\n    fillers.RowFiller\n)", "register(component=\"row_filler\")(fillers.RowFiller)", [REG_BOARD_T]),
 ("측정판 c-2 행의 나쁜 필드가 일곱 필드 앞이다", LC, "_FILTERED_ROW = {\"unit\": \"ea\",", "_FILTERED_ROW = {\"state\": \"-\", \"unit\": \"ea\",", [CHAIN_T]),
 ("chain-empty의 원천 쓴 쪽이 메시지 입력 클래스가 아니다", LC, "_RAW_WRITER = {\"chain-empty\": \"SourceCollector\", \"chain-objects\": \"RawAppender\", **{v: \"SourceCollector\" for v in _MESSAGE_VARIANTS}}", "_RAW_WRITER = {\"chain-objects\": \"RawAppender\", **{v: \"SourceCollector\" for v in _MESSAGE_VARIANTS}}", [CHAIN_T, BOARD_C1_T]),
 ("측정판 원천 클래스가 다른 레포에 같은 이름으로 없다", LC, "class SourceCollector:\n    def __init__(self, cfg):\n", "class SourceTable:\n    def __init__(self, cfg):\n", [BOARD_C1_T]),
 ("측정판 원천 행에 통과할 단일값이 없다", LC, "\"occ_date\": d[\"occ_date\"], \"unit\": \"ea\",", "\"occ_date\": d[\"occ_date\"],", [BOARD_C1_T]),
]


# ── 12a-R3-5b ① — 입구: 대상 행은 순찰과 같은 함수로 통째로, 쓴 쪽은 후보 읽기의 전체 값에서 ──
DPAT = ROOT / "src/domain/patrol.py"
TARGET_ROW_T = "tests/application/test_intake_map.py::test_대상_행은_identity를_순찰과_같은_함수로_이은_문자열이_케이스_대상과_통째로_같은_행이다"
SLASH_T = "tests/application/test_intake_map.py::test_제목에_슬래시가_든_대상도_고르고_identity는_그_행의_값이다"
WRITER_FULL_T = "tests/application/test_intake_map.py::test_쓴_쪽은_좁힌_다시_읽기가_아니라_후보_읽기의_전체_값에서_찾는다"
CLI_WALK_T = "tests/test_cli_case_walk.py::test_case_walk은_target으로_입구를_돌리고_케이스_블록만_찍으며_아무것도_남기지_않는다"
BOARD_SLASH_T = "tests/test_board_walk.py::test_제목에_슬래시가_든_행도_입구가_고르고_잘린_키의_쓴_쪽은_전체_값에서_찾아_걷는다"
LC_RATE_T = "tests/test_local_case.py::test_제목에_슬래시가_든_배지의_요약_키는_data가_커서_잘리고_쓴_쪽_이름은_data_밖에_있다"
CASES += [
 ("대상 행을 대상 문자열의 조각으로 맞춘다", IM, "target_of(row, fields) == target:", "target_of(row, fields) == \"/\".join(target.split(\"/\")[:len(fields)]):",
  [TARGET_ROW_T, SLASH_T, CLI_WALK_T, BOARD_SLASH_T]),
 ("identity 필드가 없는 행도 대상이 된다", IM, "if fields and all(f in row for f in fields) and target_of(row, fields) == target:",
  "if fields and \"/\".join(str(row.get(f)) for f in fields) == target:", [TARGET_ROW_T]),
 ("대상 문자열의 구분자가 순찰과 다르다", DPAT, "    return \"/\".join(str(row[f]) for f in identity)", "    return \" / \".join(str(row[f]) for f in identity)", [TARGET_ROW_T]),
 ("쓴 쪽을 좁힌 다시 읽기에서 찾는다", IM, "        writer, writer_at, writer_problem = writer_of(value, classes)",
  "        writer, writer_at, writer_problem = writer_of(_unwrap(raw), classes) if raw is not None else (\"\", \"\", \"\")", [WRITER_FULL_T, BOARD_SLASH_T]),
 ("측정판 증감 배지 data가 안 잘린다", LC, "_RATE_ROWS = 150", "_RATE_ROWS = 3", [LC_RATE_T, BOARD_SLASH_T]),
 ("측정판 api가 증감 배지 키를 안 읽는다", LC, "    rate = redis.get(cfg[\"redis_key\"][\"alarm_rate\"][\"key\"].format(line=line))\n", "", [LC_RATE_T, BOARD_SLASH_T]),
]


# ── 12a-R3-5b ② — "비었다"는 한 판정(대상 키 홉·다른 홉), `입력이 다 정상`은 전부 읽고 전부 정상일 때만 ──
STB = ROOT / "src/infrastructure/stubs.py"
EMPTY_ONE_T = "tests/application/test_walk.py::test_빈_목록과_빈_객체는_한_판정이고_행이_든_목록이_하나라도_있으면_정상이다"
HOP0_OBJ_T = "tests/application/test_walk.py::test_대상_키_홉의_빈_객체도_같은_판정으로_나쁘다"
INPUTS_T = "tests/application/test_walk.py::test_입력이_다_정상은_나열된_입력을_전부_읽고_전부_정상일_때만이고_읽은_입력이_없으면_모른다"
SCAN_MISS_T = "tests/application/test_walk.py::test_못_채우는_템플릿을_다_못_읽었거나_scan이_실패하면_다_못_봤다"
SHARED_IN_T = "tests/application/test_walk.py::test_다른_클래스가_이미_읽은_입력은_그_읽기_결과를_따른다"
INTAKE_EMPTY_T = "tests/application/test_intake_map.py::test_대상_키_홉의_빈_자리도_걷기와_같은_한_판정이다"
LC_OBJ_T = "tests/test_local_case.py::test_chain_objects와_palette_unread는_빈_객체_자기_출력뿐인_클래스_읽기_실패의_모양이다"
STUB_ERR_T = "tests/infrastructure/test_stubs.py::test_stub_redis는_오류_신호_시드를_읽기_실패로_돌려준다"
BOARD_OBJ_T = "tests/test_board_walk.py::test_빈_객체_입력과_자기_출력뿐인_원천_클래스는_사실대로_적는다"
BOARD_UNREAD_T = "tests/test_board_walk.py::test_입력_읽기가_실패하면_입력이_다_정상이_아니라_다_못_봤다"
CASES += [
 ("빈 객체를 비었다고 안 본다", IM, "            [place(k) for k, v in obj.items() if isinstance(v, dict) and not v])", "            [])", [EMPTY_ONE_T, INTAKE_EMPTY_T, BOARD_OBJ_T]),
 ("행이 든 목록이 있어도 빈 자리를 적는다", IM, "    if not isinstance(obj, dict) or any(isinstance(v, list) and v and all(isinstance(r, dict) for r in v) for v in obj.values()):", "    if not isinstance(obj, dict):", [EMPTY_ONE_T, INTAKE_EMPTY_T]),
 ("맨 위 빈 객체를 정상으로 본다", WK, "        if not value:\n            return \"빈 객체\", True", "        if False:\n            return \"빈 객체\", True", [EMPTY_ONE_T]),
 ("대상 키 홉이 빈 객체를 안 본다", WK, "bad=bool(target.empty or target.empty_objects),", "bad=bool(target.empty),", [HOP0_OBJ_T]),
 ("입구가 빈 객체 자리를 안 넘긴다", IM, "                                empty=arrays, empty_objects=objects,", "                                empty=arrays,", [INTAKE_EMPTY_T]),
 ("입구 줄이 빈 객체를 안 적는다", IM, "for p in [*target.empty, *target.empty_objects]))", "for p in target.empty))", [INTAKE_EMPTY_T]),
 ("자기 출력도 입력으로 센다", WK, "                    if named != writer:\n                        if result == \"ok\":", "                    if True:\n                        if result == \"ok\":", [INPUTS_T, BOARD_OBJ_T]),
 ("읽기 실패를 못 본 입력으로 안 센다", WK, "                if got.status != \"ok\":\n                    missed.append(f\"읽기 실패 {label}\")", "                if got.status != \"ok\":\n                    pass", [INPUTS_T, BOARD_UNREAD_T]),
 ("scan 일부를 못 본 입력으로 안 센다", WK, "                    elif len(reads) < len(scanned_keys) or not scanned_keys:", "                    elif False:", [SCAN_MISS_T]),
 ("scan 실패를 못 본 입력으로 안 센다", WK, "                        missed.append(f\"읽기 실패 {name}(scan)\")", "                        pass", [SCAN_MISS_T]),
 ("자원이 자기 출력뿐이어도 정상이라 한다", WK, "                       else f\"입력이 다 정상 — {info.writer}\" if info.inputs", "                       else f\"입력이 다 정상 — {info.writer}\" if True", [INPUTS_T, BOARD_OBJ_T]),
 ("이미 읽은 입력을 안 센다", WK, "                        if result == \"ok\":\n                            real += 1", "                        if False:\n                            real += 1", [SHARED_IN_T]),
 ("이미 읽은 입력의 실패를 안 따른다", WK, "                        elif result == \"fail\":\n                            missed.append(f\"읽기 실패 {label}\")", "                        elif False:\n                            missed.append(f\"읽기 실패 {label}\")", [SHARED_IN_T]),
 ("읽기 실패의 칸이 클래스 머리다", WK, "at=(reader_at(text) if real or missed else None) or class_at(text))", "at=(reader_at(text) if real else None) or class_at(text))", [BOARD_UNREAD_T]),
 ("stub이 오류 신호를 값으로 돌려준다", STB, "        if isinstance(found, dict) and set(found) == {\"__error__\"}:", "        if False:", [STUB_ERR_T, BOARD_UNREAD_T]),
 ("측정판 원천 클래스가 자기 출력을 안 읽는다", LC, "        raw = json.loads(self.redis.get(self.cfg[\"redis_key\"][\"alarm_raw\"].format(line=message[\"line\"])) or \"{}\")\n        rows = raw.get(\"rows\", []) + [message]", "        rows = [message]", [LC_OBJ_T, BOARD_OBJ_T]),
 ("palette-unread의 원천 색 키가 읽힌다", LC, "} if variant == \"palette-unread\" else json.dumps(", "} if False else json.dumps(", [LC_OBJ_T, BOARD_UNREAD_T]),
 ("chain-objects의 도메인 record가 빈 객체가 아니다", LC, "\"record\": {} if variant == \"chain-objects\" else", "\"record\": [] if variant == \"chain-objects\" else", [LC_OBJ_T, BOARD_OBJ_T]),
]


# ── 12a-R3-5b ③ — 측정 도구 `case walk --key <키>`: 입구 없이 그 키부터 ──
DECL_FOR_T = "tests/application/test_walk_only.py::test_키의_선언_이름은_흐름_그래프의_선언_키와_자리가_맞는_하나다"
WALK_KEY_T = "tests/application/test_walk_only.py::test_walk_from_key는_입구_없이_그_키를_t_1로_읽어_대상_키로_삼고_걷는다"
BOARD_KEY_T = "tests/test_board_walk.py::test_key로_댄_키부터_입구_없이_걷는다"
CLI_ARGS_T = "tests/test_cli_case_walk.py::test_case_walk은_케이스_id나_target_하나만_받고_check는_필수이며_틀리면_점검_목록을_보인다"
CASES += [
 ("--key 키의 선언 이름을 안 찾는다", WO, "    matched = [t for t in templates if \"{\" in t and re.fullmatch(re.sub(r\"\\\\\\{\\w+\\\\\\}\", \".+?\", re.escape(t)), key)]", "    matched = []", [DECL_FOR_T]),
 ("선언 이름이 여럿이어도 첫 것을 고른다", WO, "    return matched[0] if len(matched) == 1 else key", "    return matched[0] if matched else key", [DECL_FOR_T]),
 ("--key가 쓴 쪽을 안 찾는다", WO, "        writer, writer_at, writer_problem = writer_of(value, deps.classes)", "        writer, writer_at, writer_problem = \"\", \"\", \"\"", [WALK_KEY_T, BOARD_KEY_T]),
 ("--key가 빈 자리를 안 본다", WO, "        arrays, objects = empties(value)", "        arrays, objects = [], []", [WALK_KEY_T, BOARD_KEY_T]),
 ("--key가 키 없음을 대상 키로 삼는다", WO, "    elif raw is None:\n        pick = TargetPick(problem=", "    elif False:\n        pick = TargetPick(problem=", [WALK_KEY_T]),
 ("case walk가 --key를 안 쓴다", MN, "            state = await (walk_from_key(case, args.key, deps) if args.key else walk_only(case, deps))", "            state = await walk_only(case, deps)", [BOARD_KEY_T]),
 ("case walk가 셋 중 둘을 받는다", MN, "    if sum(bool(x) for x in (args.case_id, args.target, args.key)) != 1:", "    if bool(args.case_id) == bool(args.target):", [CLI_ARGS_T]),
]


# ── 12a-R3-5c ① — 쓴 쪽은 문서 목록(행) 밖의 잎에서만, identity는 개수까지 ──
WRITER_ROWS_T = "tests/application/test_intake_map.py::test_쓴_쪽은_문서_목록_밖의_잎에서만_찾는다"
IDENT_COUNT_T = "tests/application/test_intake_map.py::test_identity가_같은_값_둘이면_그_값이_두_번_든_객체만_맞는다"
WALK_ROWS_T = "tests/application/test_walk.py::test_걷기의_입력도_행_안의_클래스_이름을_쓴_쪽으로_안_삼는다"
LC_ALL_T = "tests/test_local_case.py::test_그룹과_제목이_같은_배지와_행_안의_클래스_이름_미끼"
BOARD_ALL_T = "tests/test_board_walk.py::test_그룹과_제목이_같은_배지는_그_값이_한_번_든_키와_안_맞는다"
CASES += [
 ("행 안의 잎도 쓴 쪽으로 본다", IM, "        if value and all(isinstance(r, dict) for r in value):\n            return\n", "", [WRITER_ROWS_T, WALK_ROWS_T, BOARD_SLASH_T]),
 ("identity를 집합으로 비교한다", IM, "        if values and all(leaves[v] >= n for v, n in Counter(values).items()):", "        if values and all(leaves[v] >= 1 for v in values):", [IDENT_COUNT_T, BOARD_ALL_T]),
 ("측정판에 그룹=제목 배지가 없다", LC, "+ [{\"group\": \"ALL\", \"title\": \"ALL\",", "+ [{\"group\": \"ALL\", \"title\": \"NONE\",", [LC_ALL_T, BOARD_ALL_T]),
 ("측정판 미끼 키에 그룹=제목 값이 없다", LC, "\"source\": \"MES\", \"scope\": \"ALL\"}", "\"source\": \"MES\"}", [LC_ALL_T]),
 ("측정판 행 안의 라벨이 클래스 이름이 아니다", LC, "\"v\": i, \"view\": \"Overview\"}", "\"v\": i, \"view\": \"overview\"}", [LC_ALL_T]),
]


# ── 12a-R3-5c ② — 판정 턴: 조각 뒤 멈춤·빈 응답·유휴 상한, 판정 프롬프트의 사슬 중간 홉 접기 ──
ADAPTER_T = "tests/infrastructure/test_llm_adapters.py::test_스트림의_조각_수와_종료_사유가_답에_남는다"
STALL_T = "tests/application/test_lead.py::test_조각이_온_뒤의_멈춤은_본문이_없어도_시간_초과가_아니라_같은_질문으로_한_번_다시_묻는다"
EMPTY_T = "tests/application/test_lead.py::test_빈_응답은_종료_사유와_무관하게_같은_질문으로_한_번_다시_묻는다"
IDLE_T = "tests/config/test_schema_app.py::test_판정_역할의_유휴_상한_기본은_게이트웨이_유휴_끊김_바로_아래다"
FOLD_T = "tests/application/test_briefing.py::test_판정_프롬프트는_사슬_중간_홉의_본문을_접고_대상_키와_멈춘_홉은_남기며_접힌_id는_인용할_수_있다"
CASES += [
 ("성공한 답에 조각 수와 종료 사유를 안 남긴다", LCM, "                        chunks=len(parts), finish_reason=str(metadata.get(\"finish_reason\") or \"\"), **_tokens(usage)), None", "                        ), None", [ADAPTER_T]),
 ("실패한 답에 조각 수를 안 남긴다", LCM, "                              chunks=len(parts), finish_reason=str(metadata.get(\"finish_reason\") or \"\"), **_tokens(usage))", "                              )", [ADAPTER_T]),
 ("조각이 온 뒤의 멈춤도 시간 초과다", LD, "timed_out = _is_timeout(last.error or \"\") and not reply.chunks", "timed_out = _is_timeout(last.error or \"\")", [STALL_T]),
 ("빈 응답을 고칠 글로 다시 묻는다", LD, "                last = Parsed(False, error=f\"빈 응답(조각 {reply.chunks}개, 종료 사유 {reply.finish_reason or '모름'})\")\n                transport = True", "                last = Parsed(False, error=f\"빈 응답(조각 {reply.chunks}개, 종료 사유 {reply.finish_reason or '모름'})\")\n                transport = False", [EMPTY_T]),
 ("빈 응답을 다시 안 묻는다", LD, "                transport = True\n            else:\n                text = reply.text or \"\"\n                parsed = parse_object(text)", "                transport = True\n                timed_out = True\n            else:\n                text = reply.text or \"\"\n                parsed = parse_object(text)", [EMPTY_T]),
 ("빈 응답을 형식 오류로 본다", LD, "            elif not (reply.text or \"\").strip():", "            elif False:", [EMPTY_T]),
 ("판정 역할에 유휴 상한 기본이 없다", SAP, "_ROLE_DEFAULTS: dict[str, dict] = {\"conclude\": {\"response_format\": \"none\", \"stream_idle_s\": 170.0}}", "_ROLE_DEFAULTS: dict[str, dict] = {\"conclude\": {\"response_format\": \"none\"}}", [IDLE_T]),
 ("판정 턴이 사슬 중간 홉을 안 접는다", B, "            \"evidence\": evidence_block(state, budget=evidence_budget, fold_chain=True),", "            \"evidence\": evidence_block(state, budget=evidence_budget),", [FOLD_T]),
 ("멈춘 홉의 본문도 접는다", B, "    keep = {state.walk.stop_at} | {e for t in state.plan_tasks if t.id == \"t-1\" for e in t.result_evidence_ids}", "    keep = {e for t in state.plan_tasks if t.id == \"t-1\" for e in t.result_evidence_ids}", [FOLD_T]),
]


# ── 12a-R3-5c ③ — 인용한 코드 증거의 레포가 component의 레포와 다르면 caveat ──
CAVEAT_T = "tests/application/test_verdict.py::test_component의_근거로_인용한_코드가_다른_레포의_것이면_그_레포의_서비스를_후보로_caveat에_적는다"
CASES += [
 ("인용한 코드의 레포를 안 본다", ND, "            if repo and repo != home:", "            if False:", [CAVEAT_T]),
 ("verify가 코드 레포 caveat를 안 건다", ND, "        verdict = caveat_code_repos(verdict, plan_tasks=state.plan_tasks, service_repos=deps.service_repos)\n", "", [CAVEAT_T]),
 ("코드 증거의 레포 인자를 안 본다", ND, "repo = str(made.params.get(\"repo\") or service_repos.get(str(made.params.get(\"service\", \"\")), \"\"))", "repo = str(service_repos.get(str(made.params.get(\"service\", \"\")), \"\"))", [CAVEAT_T]),
 ("그 레포의 서비스를 후보로 안 적는다", ND, "의 코드다 — 그 레포의 서비스 {others}도 후보\")", "의 코드다\")", [CAVEAT_T]),
]


# ── 12a-R3-5d ①-a — 레포 가르기는 모든 홉에, 값 없는 홉의 멈춤 문구 ──
SPLIT_ALL_T = "tests/application/test_walk.py::test_정상_홉의_쓴_쪽도_키_선언으로_레포를_가르고_못_가르면_둘_다_후보다"
NOVALUE_T = "tests/application/test_walk.py::test_값이_없는_홉에서_멈추면_키가_없다_또는_0행이라_적는다"
CASES += [
 ("정상 홉의 쓴 쪽을 안 가른다", WK, "                    next_branches, (_, shown, cands) = [], split(w, name, walking=False)", "                    next_branches, shown, cands = [], who(w), tuple(x for _, s in homes(w) for x in s)", [SPLIT_ALL_T, BOARD_C1_T]),
 ("정상 홉의 주 줄이 걷는다고 말한다", WK, "{'둘 다 걷는다' if walking else '둘 다 후보'}", "'둘 다 걷는다'", [SPLIT_ALL_T]),
 ("같은 주 줄을 두 번 적는다", WK, "        if note not in notes:\n            notes.append(note)", "        notes.append(note)", [SPLIT_ALL_T]),
 ("키 없음을 쓴 쪽 모름이라 한다", WK, "            stopped = (f\"키가 없다 — {stop_hop.key}\" if stop_hop.status == \"없음\"", "            stopped = (f\"키가 없다 — {stop_hop.key}\" if False", [NOVALUE_T]),
 ("0행을 쓴 쪽 모름이라 한다", WK, "                       else f\"0행 — {stop_hop.key}(값이 비어 쓴 쪽을 알 수 없다)\" if stop_hop.status == \"0행\"", "                       else f\"0행 — {stop_hop.key}(값이 비어 쓴 쪽을 알 수 없다)\" if False", [NOVALUE_T]),
]


# ── 12a-R3-5d ①-b — 번역의 공유/못 가름, 연결 확인에 걷기 키, 걷기와 어긋남, 후보의 토픽 ──
XLATE_REPO_T = "tests/application/test_verdict.py::test_번역은_레포_지도로_한_레포_공유와_두_레포_못_가름을_가른다"
XLATE_NODE_T = "tests/application/test_verdict.py::test_verify_노드가_번역에_레포_지도를_넘긴다"
LINKED_T = "tests/application/test_verdict.py::test_걷기_홉의_키를_인용한_최상위는_연결이_확인된_것이다"
DISAGREE_T = "tests/application/test_verdict.py::test_판정이_걷기가_멈춘_자리와_어긋나면_caveat에_멈춘_홉의_서비스를_후보로_적는다"
TOPIC_ALT_T = "tests/application/test_verdict.py::test_후보의_component가_케이스에_나온_토픽과_같으면_통과하고_그_밖의_서비스_아닌_후보는_재작성_없이_제외한다"
CASES += [
 ("번역이 레포 지도를 안 본다", ND, "            shared = (len({service_repos.get(s, s) for s in candidates}) == 1) if service_repos else link.component in resolved", "            shared = link.component in resolved", [XLATE_REPO_T]),
 ("verify가 번역에 레포 지도를 안 넘긴다", ND, "walk=state.walk, service_repos=deps.service_repos)", "walk=state.walk)", [XLATE_NODE_T, CLS_T]),
 ("걷기 홉의 키를 연결로 안 본다", ND, "    linked = [k for k in [target.key, *(h.key for h in (walk.hops if walk is not None else []))] if k]", "    linked = [k for k in [target.key] if k]", [LINKED_T]),
 ("verify가 연결 확인에 걷기를 안 넘긴다", ND, "evidence=state.evidence, walk=state.walk)", "evidence=state.evidence)", [LINKED_T]),
 ("걷기와 어긋나도 caveat가 없다", ND, "    if stop is None or component not in components or component in stop.candidates:", "    if True:", [DISAGREE_T]),
 ("정상으로 본 홉을 안 적는다", ND, "    if normal is not None:\n        note +=", "    if False:\n        note +=", [DISAGREE_T]),
 ("입력을 안 본 멈춤에서도 어긋났다 한다", ND, "or not walk.stopped.startswith(\"입력\"):", ":", [DISAGREE_T]),
 ("케이스의 토픽을 component로 안 받는다", ND, "        if component in topics:\n            return None", "        if False:\n            return None", [TOPIC_ALT_T]),
 ("서비스 아닌 후보를 되묻는다", ND, "            verdict = drop_foreign_alternates(verdict, component_problem)", "            pass", [TOPIC_ALT_T]),
]


# ── 12a-R3-5d ② — length도 1회 다시, usage, 트레이스 머리의 턴 줄 ──
TD = ROOT / "src/application/trace_digest.py"
USAGE_T = "tests/infrastructure/test_llm_adapters.py::test_게이트웨이가_실은_usage가_답에_남는다"
STATS_T = "tests/application/test_lead.py::test_턴의_조각_수_종료_사유_토큰이_트레이스로_간다"
TRACE_TURN_T = "tests/test_cli.py::test_트레이스가_턴의_조각_수_종료_사유_토큰을_적는다"
DIGEST_TURN_T = "tests/application/test_trace_digest.py::test_요약_머리줄에_턴의_조각_수_종료_사유_토큰이_붙는다"
CASES += [
 ("스트림의 usage를 안 읽는다", LCM, "                    usage = getattr(chunk, \"usage_metadata\", None) or usage\n", "", [USAGE_T]),
 ("비스트림의 usage를 안 읽는다", LCM, "                usage = getattr(message, \"usage_metadata\", None) or {}\n", "                usage = {}\n", [USAGE_T]),
 ("성공한 답에 토큰을 안 싣는다", LCM, "finish_reason=str(metadata.get(\"finish_reason\") or \"\"), **_tokens(usage)), None", "finish_reason=str(metadata.get(\"finish_reason\") or \"\")), None", [USAGE_T]),
 ("턴 통계를 트레이스에 안 넘긴다", LD, "        _tell(on_exchange, asked, text, failure, latency, waited, source, note, stats)", "        _tell(on_exchange, asked, text, failure, latency, waited, source, note, None)", [STATS_T]),
 ("성공한 턴의 통계를 안 만든다", LD, "            stats = {\"chunks\": reply.chunks, \"finish_reason\": reply.finish_reason, \"input_tokens\": reply.input_tokens, \"output_tokens\": reply.output_tokens}", "            stats = {\"chunks\": reply.chunks, \"finish_reason\": reply.finish_reason, \"input_tokens\": reply.input_tokens, \"output_tokens\": reply.output_tokens} if reply.status == \"error\" else None", [STATS_T]),
 ("트레이스 머리에 턴 줄이 없다", MN, "            took += f\"턴: 조각 {stats.get('chunks', 0):,} · 종료 {stats.get('finish_reason') or '모름'} · {tokens}\\n\"", "            pass", [TRACE_TURN_T]),
 ("토큰을 모르는데 0으로 적는다", MN, "                      if stats.get(\"input_tokens\") or stats.get(\"output_tokens\") else \"토큰 모름\")", "                      if True else \"토큰 모름\")", [TRACE_TURN_T]),
 ("요약이 턴 줄을 안 올린다", TD, "           + (f\" · {turn}\" if turn else \"\")\n", "", [DIGEST_TURN_T]),
]


# ── 12a-R3-5d ③ — 상속: 부모 클래스의 자원과 파일 ──
INHERIT_RES_T = "tests/infrastructure/test_deployed_code.py::test_code_resources는_부모_클래스의_멤버_자원까지_세고_어느_부모의_것인지_적는다"
INHERIT_SCOPE_T = "tests/infrastructure/test_deployed_code.py::test_filter_lines의_범위와_컴포넌트_링크에_부모_클래스_파일이_든다"
CASES += [
 ("부모 클래스의 멤버 자원을 안 센다", QYP, "    members = own + reached", "    members = list(own)", [INHERIT_RES_T, BOARD_C1_T]),
 ("부모를 한 단계만 따라간다", QYP, "        stack += [e.dst for e in index.edges_from(base, \"inherits\")]\n", "", [INHERIT_RES_T]),
 ("순환 상속에서 자기 자신으로 돌아온다", QYP, "    seen = {sid}\n    stack = [e.dst for e in index.edges_from(sid, \"inherits\")]", "    seen = set()\n    stack = [e.dst for e in index.edges_from(sid, \"inherits\")]", [INHERIT_RES_T]),
 ("부모 포함 줄이 없다", QYP, "        lines.append(f\"  부모 클래스의 자원 포함: {', '.join(index.symbols[b].qualname for b in bases)}\"\n                     + (f\" · 진입에서 안 닿은 부모 메서드 {skipped}개 뺌\" if skipped else \"\"))\n", "", [INHERIT_RES_T]),
 ("단일값 범위에 부모 파일이 없다", DCP, "        for mid in reached:\n            path = self._index.symbols[mid].file", "        for mid in []:\n            path = self._index.symbols[mid].file", [INHERIT_SCOPE_T]),
 ("부모 파일의 get을 링크로 안 본다", DCP, "        literals, variables = component_names(\"\\n\".join(snap.get(path, \"\") for path in files))", "        literals, variables = component_names(snap[sym.file])", [INHERIT_SCOPE_T]),
]


# ── 12a-R3-5d ④-a — Kafka 포인터 읽기: 포트·stub·실어댑터·등재·증거는 세기만 ──
PTS = ROOT / "src/domain/ports.py"
PORT_MSG_T = "tests/domain/test_ports.py::test_포인터_메시지_한_건을_읽을_수_있다"
PORT_SURF_T = "tests/domain/test_actions.py::test_등재_목록이_포트_표면_안에_있다"
ACT_MSG_T = "tests/domain/test_actions.py::test_포인터_메시지_읽기가_등재돼_있고_본문은_증거에_안_싣는다"
KI_FOUND_T = "tests/infrastructure/test_kafka_inspector.py::test_message_at은_포인터의_그_메시지_한_건만_읽고_그룹에_안_들어간다"
KI_RANGE_T = "tests/infrastructure/test_kafka_inspector.py::test_message_at은_보존_범위_밖이면_없다고_하고_오류가_아니다"
KI_HOLE_T = "tests/infrastructure/test_kafka_inspector.py::test_message_at은_압축으로_빠진_오프셋의_다음_레코드를_그_메시지라_하지_않는다"
KI_STALL_T = "tests/infrastructure/test_kafka_inspector.py::test_message_at은_브로커가_안_주면_빈_응답_셋_뒤_실패로_답한다"
STUB_MSG_T = "tests/infrastructure/test_stubs.py::test_stub_kafka는_포인터의_메시지_한_건을_주고_보존_범위_밖이면_없다고_한다"
RP_MSG_T = "tests/application/test_runner_probe.py::test_포인터_메시지_읽기의_증거는_크기와_행_요약만이고_본문은_안_싣는다"
CAT_MSG_T = "tests/application/test_briefing.py::test_걷기의_포인터_읽기는_리드_목록에_없다"
CASES += [
 ("포트에 포인터 읽기가 없다", PTS, "    async def message_at(self, topic: str, partition: int, offset: int) -> ProbeResult:\n        \"\"\"포인터(토픽·파티션·오프셋)가 가리키는 메시지 **한 건**(R3-5d ④).", "    async def message_at_(self, topic: str, partition: int, offset: int) -> ProbeResult:\n        \"\"\"포인터(토픽·파티션·오프셋)가 가리키는 메시지 **한 건**(R3-5d ④).", [PORT_MSG_T, PORT_SURF_T]),
 ("포인터 읽기가 등재 목록에 없다", AC, "    \"kafka.message_at\":    (\"kafka\", \"message_at\",    (\"topic\", \"partition\", \"offset\"), ()),\n", "", [ACT_MSG_T]),
 ("본문을 안 싣는 읽기 목록이 비었다", AC, "SUMMARY_ONLY = frozenset({\"kafka.message_at\"})", "SUMMARY_ONLY = frozenset()", [ACT_MSG_T, RP_MSG_T]),
 ("포인터 읽기의 본문을 증거에 싣는다", RP, "        if task.action in SUMMARY_ONLY:", "        if False:", [RP_MSG_T]),
 ("요약 줄은 여전히 원본 repr이다", RP, "            summary=aside + head,", "            summary=aside + _summarize(result.data),", [RP_MSG_T]),
 ("범위 밖 오프셋을 읽으러 간다", KI, "            if not begin <= offset < end:", "            if False:", [KI_RANGE_T]),
 ("압축된 오프셋의 다음 레코드를 그 메시지라 한다", KI, "            if record.offset != offset:", "            if False:", [KI_HOLE_T]),
 ("브로커가 안 주면 바로 포기한다", KI, "                    if empties >= EMPTY_POLLS:          # 한 건도 안 오면 tail과 같은 기준으로 그만둔다", "                    if True:          # 한 건도 안 오면 tail과 같은 기준으로 그만둔다", [KI_STALL_T]),
 ("메시지 크기를 안 센다", KI, "\"bytes\": len(record.value or b\"\")}", "\"bytes\": 0}", [KI_FOUND_T]),
 ("스텁이 보존 시작을 무시한다", ST, "        begin = self._begins.get(topic, 0)", "        begin = 0", [STUB_MSG_T]),
 ("스텁이 범위 밖에도 메시지를 지어낸다", ST, "        if not begin <= int(offset) < end:", "        if False:", [STUB_MSG_T]),
 ("리드 목록에 포인터 읽기가 보인다", B, "        if name in _RENDERED_SEPARATELY or name in _WALK_READS or name in hide:", "        if name in _RENDERED_SEPARATELY or name in hide:", [CAT_MSG_T]),
]


# ── 12a-R3-5d ④-b — 걷기의 메시지 포인터 홉·발행 선언·측정판 ──
WALK_MSG_T = "tests/application/test_walk.py::test_포인터가_있으면_그_메시지_한_건을_입력으로_읽고_정상이면_다_정상_비었으면_거기서_멈춘다"
WALK_GONE_T = "tests/application/test_walk.py::test_포인터의_메시지가_보존_범위_밖이면_키를_만든_메시지가_토픽에_없다고_멈추고_다음_칸은_지금_오는_것이_먼저다"
WALK_NONE_T = "tests/application/test_walk.py::test_포인터가_없으면_그_사실만_적고_나쁜_입력의_쓴_쪽으로_내려가면_그_키_값의_포인터를_따라간다"
WALK_SAME_T = "tests/application/test_walk.py::test_메시지의_단일값도_읽는_쪽_코드_줄로_판정하고_그_줄의_함수가_첫_칸이다"
WALK_FAIL_T = "tests/application/test_walk.py::test_포인터_메시지_읽기가_실패하면_다_못_봤다"
INTAKE_PTR_T = "tests/application/test_intake_map.py::test_값_안의_포인터는_topic_partition_offset이_함께_든_객체이고_입구가_대상_키에_적는다"
PRODUCING_T = "tests/knowledge/test_flow.py::test_producing_map은_토픽마다_config로_발행을_선언한_서비스들이다"
LC_MSG_T = "tests/test_local_case.py::test_message_변형_셋은_원천_묶음_키_값에_포인터가_있고_그_메시지가_비었거나_지워졌거나_행이_전부_자리_표시다"
BOARD_MSG_T = "tests/test_board_walk.py::test_메시지_변형_빈_메시지는_메시지_홉에서_멈추고_다음_칸은_받는_클래스_본문_이은_컴포넌트_지금_오는_것이다"
BOARD_GONE_T = "tests/test_board_walk.py::test_메시지_변형_지워진_메시지는_키를_만든_메시지가_토픽에_없다이고_발행_선언은_config에서_온다"
CASES += [
 ("걷기가 포인터 메시지를 안 읽는다", WK, "        if not stopped and pointer:", "        if False:", [WALK_MSG_T]),
 ("범위 밖 메시지를 정상이라 한다", WK, "                elif not data.get(\"found\"):", "                elif False:", [WALK_GONE_T]),
 ("메시지 홉을 읽은 입력으로 안 센다", WK, "                    real += 1                                                          # 메시지도 읽은 입력이다 — 정상이면 \"입력이 다 정상\"에 든다\n", "", [WALK_MSG_T]),
 ("포인터 없음을 안 적는다", WK, "            none = \" · 포인터 없음\" if ev in pointerless else \"\"", "            none = \"\"", [WALK_NONE_T]),
 ("아래 홉으로 포인터를 안 들고 간다", WK, "pointer_of(_unwrap(raw)) if raw is not None else {}) for b in next_branches]", "{}) for b in next_branches]", [WALK_NONE_T]),
 ("메시지의 단일값을 안 본다", WK, "                    status, bad, carry = await judged(rows_of(boxed), status, bad, ev)", "                    pass", [WALK_SAME_T]),
 ("없음의 다음 칸이 지금 오는 것이 아니다", WK, "                rungs += [tail, *code]", "                rungs += [*code, tail]", [WALK_GONE_T]),
 ("메시지만 읽은 클래스의 다음 칸이 없다", WK, "at=(reader_at(text) if real or missed else None) or class_at(text))", "at=reader_at(text) if real or missed else class_at(text))", [WALK_MSG_T]),
 ("메시지 읽기 실패를 못 본 입력으로 안 센다", WK, "                    missed.append(f\"읽기 실패 {label}\")\n                elif not data.get(\"found\"):", "                elif not data.get(\"found\"):", [WALK_FAIL_T]),
 ("입구가 포인터를 안 적는다", IM, "                                empty=arrays, empty_objects=objects, pointer=pointer_of(value),", "                                empty=arrays, empty_objects=objects,", [INTAKE_PTR_T]),
 ("포인터가 행 안의 것도 집는다", IM, "        if value and all(isinstance(r, dict) for r in value):\n            return\n        for i, v in enumerate(value):\n            yield from _objects(v, f\"{path}[{i}]\")", "        for i, v in enumerate(value):\n            yield from _objects(v, f\"{path}[{i}]\")", [INTAKE_PTR_T]),
 ("--key가 포인터를 안 적는다", WO, "empty=arrays, empty_objects=objects, pointer=pointer_of(value), writer=writer,", "empty=arrays, empty_objects=objects, writer=writer,", [WALK_KEY_T]),
 ("발행 선언을 코드 추정에서도 센다", FLW, "        if e.get(\"relation\") == \"produces\" and e.get(\"origin\") == \"config\" and src.get(\"type\") == \"service\" and dst.get(\"label\") in out:", "        if e.get(\"relation\") == \"produces\" and src.get(\"type\") == \"service\" and dst.get(\"label\") in out:", [PRODUCING_T]),
 ("걷기 노드가 발행 선언을 안 넘긴다", ND, "                                  topic_producers=deps.topic_producers, lookup=deps.filter_lines, clock=deps.clock)", "                                  lookup=deps.filter_lines, clock=deps.clock)", [WALK_KEY_T]),
 ("어긋남 caveat가 메시지 홉의 발행 선언을 안 적는다", ND, "    where = f\"쓴 쪽 {stop.writer} [{', '.join(stop.candidates)}]\" if stop.writer else stop.service", "    where = f\"쓴 쪽 {stop.writer} [{', '.join(stop.candidates)}]\"", [DISAGREE_T]),
 ("메시지 홉의 발행 선언이 표에 안 보인다", CS, "        who = (self.writer + (f\" [{self.service}]\" if self.service else \"\")) if self.writer else (self.service or \"—\")", "        who = (self.writer + (f\" [{self.service}]\" if self.service else \"\")) if self.writer else \"—\"", [WALK_MSG_T]),
 ("CLI가 발행 선언을 안 넘긴다", MN, "declared_by=flow.declaring_map(flow_graph), topic_producers=flow.producing_map(flow_graph), clock=clock)", "declared_by=flow.declaring_map(flow_graph), clock=clock)", [BOARD_GONE_T]),
 ("측정판에 포인터가 없다", LC, "\"offset\": _POINTER_OFFSET[l]}} if variant in _MESSAGE_VARIANTS else {})},", "\"offset\": _POINTER_OFFSET[l]}} if False else {})},", [LC_MSG_T, BOARD_MSG_T]),
]


# ── 12a-R3-5d ⑤ — 결함 아님(not_a_defect)은 걷기가 지킨다 ──
ICP = ROOT / "config/prompts/investigate-conclude.md"
VOCAB_T = "tests/domain/test_verdict_model.py::test_degraded는_판정_어휘에_있지만_LLM_어휘에는_없다"
MODEL_NAD_T = "tests/domain/test_verdict_model.py::test_결함_아님은_root_cause_없이_basis로_서고_root_cause가_있으면_거부한다"
LEAD_NAD_T = "tests/application/test_lead.py::test_결함_아님_판정은_root_cause_없이_basis를_요구한다"
VERIFY_NAD_T = "tests/application/test_verdict.py::test_결함_아님은_걷기가_다_정상에서_멈췄고_나쁨이_없고_basis가_코드와_입력_증거를_함께_인용할_때만_지나고_확신_상한은_medium"
CITE_NAD_T = "tests/application/test_verdict.py::test_basis의_태스크_id도_그_태스크의_유일한_증거_id로_고친다"
EXAMPLE_NAD_T = "tests/application/test_verdict.py::test_판정_예시와_프롬프트가_결함_아님과_basis를_말한다"
CASES += [
 ("결함 아님이 판정 어휘에 없다", CS, "                      \"external\", \"inconclusive\", \"degraded\", \"not_a_defect\"]", "                      \"external\", \"inconclusive\", \"degraded\"]", [VOCAB_T, MODEL_NAD_T]),
 ("결함 아님에 root_cause가 있어도 선다", CS, "        if self.verdict_type == \"not_a_defect\" and self.root_cause is not None:", "        if False:", [MODEL_NAD_T]),
 ("리드가 결함 아님에 basis 없이 낸다", LD, "        if self.verdict_type == \"not_a_defect\" and (self.root_cause is not None or not self.basis):", "        if False:", [LEAD_NAD_T]),
 ("결함 아님 조건을 안 본다", ND, "            problems += not_a_defect_problems(verdict, walk=state.walk, plan_tasks=state.plan_tasks)", "            pass", [VERIFY_NAD_T]),
 ("걷기 없는 결함 아님을 지난다", ND, "        return [\"결함 아님은 걷기가 있을 때만 — 걷기가 없다(입구가 키나 쓴 쪽을 못 골랐다)\"]", "        return []", [VERIFY_NAD_T]),
 ("멈춤이 다 정상이 아니어도 결함 아님을 지난다", ND, "    if not walk.stopped.startswith(\"입력이 다 정상\"):", "    if False:", [VERIFY_NAD_T]),
 ("홉 표의 나쁨을 안 본다", ND, "    bad = [h.key for h in walk.hops if h.status.startswith(\"나쁨\")]", "    bad = [h.key for h in walk.hops if False]", [VERIFY_NAD_T]),
 ("코드 증거 없이도 지난다", ND, "    if not cited & set(code_ids):", "    if False:", [VERIFY_NAD_T]),
 ("입력 증거 없이도 지난다", ND, "    if not cited & set(input_ids):", "    if False:", [VERIFY_NAD_T]),
 ("결함 아님의 확신 상한이 없다", ND, "    if verdict.verdict_type != \"not_a_defect\" or verdict.confidence != \"high\":", "    if True:", [VERIFY_NAD_T]),
 ("강등이 결함 아님을 그대로 둔다", ND, "    if verdict.verdict_type == \"not_a_defect\":\n        verdict_type = \"inconclusive\"\n", "", [VERIFY_NAD_T]),
 ("basis의 태스크 id를 안 고친다", ND, "    basis = fix(CauseLink(component=\"basis\", evidence_ids=list(verdict.basis))).evidence_ids if verdict.basis else list(verdict.basis)", "    basis = list(verdict.basis)", [CITE_NAD_T]),
 ("basis의 없는 id를 안 잡는다", ND, "    for evidence_id in verdict.basis:\n        if evidence_id not in citable:", "    for evidence_id in []:\n        if evidence_id not in citable:", [VERIFY_NAD_T]),
 ("예시에 결함 아님이 없다", B, "| external | not_a_defect | inconclusive 중 하나>", "| external | inconclusive 중 하나>", [EXAMPLE_NAD_T]),
 ("프롬프트가 결함 아님을 설명하지 않는다", ICP, "- `not_a_defect`는 걷기가 `입력이 다 정상`에서 멈췄고 홉 표에 `나쁨`이 없을 때만 — 비어 있는 것이 그 클래스 코드의 의도된 결과일 때다. `root_cause`\n  없이 `basis`에 멈춘 클래스의 코드 증거 id와 그 클래스의 비지 않은 입력 증거 id를 **함께** 적어라(코드가 걷기와 대조한다).\n", "", [EXAMPLE_NAD_T]),
]


# ── 12a-R3-5e ① — 사이트가 덮은 같은 값의 둘째 행도 그 관계로 config 엣지 ──
SITE_DUP_T = "tests/knowledge/test_flow.py::test_같은_값을_다른_key_path로_덮은_둘째_행도_그_관계로_config_엣지를_단다"
CASES += [
 ("같은 값의 둘째 행에 엣지를 안 단다", FLW, "        existing = next((n for n in nodes if n[\"id\"] == new_id), None)\n        if existing is None:", "        existing = next((n for n in nodes if n[\"id\"] == new_id), None)\n        if existing is not None:\n            continue\n        if existing is None:", [SITE_DUP_T, BOARD_GONE_T]),
 ("둘째 행의 엣지를 두 번 단다", FLW, "            if any(e[\"source\"] == source and rename.get(e[\"target\"], e[\"target\"]) == new_id and e[\"relation\"] == relation\n                   and e.get(\"origin\") == \"config\" for e in links):\n                continue\n", "", [SITE_DUP_T]),
 ("측정판 사이트 층이 발행 토픽을 안 덮는다", LC, "\"consumer\": {\"group_id\": \"gumi-mx-core\", \"topic\": {\"topic3\": \"gumi.alarm.audit\"}},\n                            \"producer\": {\"topic\": {\"topic2\": \"gumi.alarm.audit\"}}}}},", "\"consumer\": {\"group_id\": \"gumi-mx-core\", \"topic\": {\"topic3\": \"gumi.alarm.audit\"}}}}},", [LC_MSG_T, BOARD_GONE_T]),
]


# ── 12a-R3-5e ② — 진입에서 호출 그래프로 닿는 부모 메서드의 자원만 ──
REACH_T = "tests/infrastructure/test_deployed_code.py::test_code_resources는_진입에서_호출_그래프로_닿는_부모_메서드의_자원만_센다"
CASES += [
 ("호출 그래프로 안 좁힌다", QYP, "    reached = [m for m in parents if m in seen]", "    reached = list(parents)", [REACH_T, INHERIT_SCOPE_T]),
 ("호출 없는 부모 메서드를 진입으로 안 잡는다", QYP, "    roots = own + [m for m in parents if not index.edges_to(m, \"calls\")]", "    roots = list(own)", [INHERIT_RES_T]),
 ("뺀 메서드 수를 안 적는다", QYP, "                     + (f\" · 진입에서 안 닿은 부모 메서드 {skipped}개 뺌\" if skipped else \"\"))", "                     )", [REACH_T]),
 ("단일값 범위가 전 조상 파일이다", DCP, "        _, reached, _ = qy.reachable_members(self._index, found[0])\n        for mid in reached:", "        for mid in [m.id for b in qy.ancestors(self._index, found[0]) for m in self._index.symbols if m.class_id == b]:", [INHERIT_SCOPE_T]),
]

assert len(CASES) >= 945, f"케이스가 {len(CASES)}개뿐이다 — 붙이려던 것이 안 붙었나"

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
    # RED는 "테스트가 실패했다"(1)뿐이다. 4(잘못된 테스트 id)·5(수집 0건)도 0이 아니라서,
    # 이름이 바뀐 테스트를 가리키는 케이스가 조용히 RED로 통과한 적이 있다.
    ok = done.returncode == 1
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
