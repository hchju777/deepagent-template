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
"""
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
R, C, M, S = (ROOT/"src/infrastructure/git_reader.py", ROOT/"src/knowledge/checkout.py",
              ROOT/"src/__main__.py", ROOT/"tests/support.py")
K = "tests/knowledge/test_checkout.py"; G = "tests/infrastructure/test_git_reader.py"
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
]
bad = []
for label, path, old, new, tests in CASES:
    src = path.read_text(encoding="utf-8")
    if old not in src:
        bad.append(f"{label}: 자리 못 찾음"); print(f"???  {label}"); continue
    path.write_text(src.replace(old, new, 1), encoding="utf-8")
    try:
        done = subprocess.run([".venv/bin/python", "-m", "pytest", "-q", *tests],
                              cwd=ROOT, capture_output=True, text=True)
    finally:
        path.write_text(src, encoding="utf-8")
    tail = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else "(출력 없음)"
    ok = done.returncode != 0
    print(f"{'RED ' if ok else '초록!'} {label} — {tail}")
    if not ok: bad.append(f"{label}: 지웠는데 통과")
print()
print(f"총 {len(CASES)}가지 — " + ("모두 RED" if not bad else "문제:\n  " + "\n  ".join(bad)))

# **되돌리기가 실제로 됐는지 확인한다.** 스윕이 소스를 건드리므로, 여기서
# 안 보면 망가진 채로 커밋될 수 있다 — 그건 스윕이 막으려던 것보다 나쁘다.
dirty = subprocess.run(["git", "status", "--porcelain", "src", "tests"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace").stdout.strip()
if dirty:
    print("\n⚠ 작업 트리가 깨끗하지 않다 — 스윕이 되돌리지 못했거나 원래 수정이 있었다:")
    print("  " + dirty.replace("\n", "\n  "))
sys.exit(1 if bad else 0)
