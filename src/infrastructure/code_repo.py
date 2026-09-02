"""코드 레포 리더 — git subprocess, 읽기 명령만 노출한다 (스펙 §4.3).

hash 지정 읽기가 기본이다: 사이트 조사는 워크트리가 아니라 deployment.yaml의
커밋으로 읽는다(§2.5-2). 레포 변경 명령(pull, checkout 등)은 존재하지 않는다.
"""
import subprocess
from pathlib import Path

from src.domain.ports import CodeRepoReaderPort


class CodeRepoError(Exception):
    pass


class CodeRepoReader(CodeRepoReaderPort):
    def __init__(self, repos: dict[str, Path]):
        self._repos = {name: Path(p) for name, p in repos.items()}

    def _run(self, repo: str, *args: str) -> subprocess.CompletedProcess:
        if repo not in self._repos:
            raise CodeRepoError(f"레포 {repo!r}는 config에 등록돼 있지 않다")
        return subprocess.run(["git", "-C", str(self._repos[repo]), *args],
                              capture_output=True, text=True)

    def hash_exists(self, repo, commit):
        return self._run(repo, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0

    def show(self, repo, commit, path):
        proc = self._run(repo, "show", f"{commit}:{path}")
        if proc.returncode != 0:
            raise CodeRepoError(f"{repo}@{commit[:7]}:{path} 읽기 실패 — {proc.stderr.strip()}")
        return proc.stdout

    def head(self, repo):
        proc = self._run(repo, "rev-parse", "HEAD")
        if proc.returncode != 0:
            raise CodeRepoError(f"{repo}의 HEAD 조회 실패 — {proc.stderr.strip()}")
        return proc.stdout.strip()

    def grep(self, repo, commit, pattern):
        proc = self._run(repo, "grep", "-n", pattern, commit)
        if proc.returncode > 1:                      # 1 = 매치 없음(정상), >1 = 오류
            raise CodeRepoError(f"{repo}@{commit[:7]} grep 실패 — {proc.stderr.strip()}")
        return [line.split(":", 1)[1] if ":" in line else line
                for line in proc.stdout.splitlines()]
