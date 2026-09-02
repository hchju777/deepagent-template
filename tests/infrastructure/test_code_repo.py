import subprocess

import pytest
from src.infrastructure.code_repo import CodeRepoError, CodeRepoReader


@pytest.fixture()
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "svc.py").write_text("OEE = output / planned_time\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    return tmp_path


def test_head와_hash_exists와_show(repo):
    reader = CodeRepoReader({"twin-services": repo})
    head = reader.head("twin-services")
    assert reader.hash_exists("twin-services", head)
    assert not reader.hash_exists("twin-services", "0" * 40)
    assert "planned_time" in reader.show("twin-services", head, "svc.py")


def test_grep과_미등록_repo(repo):
    reader = CodeRepoReader({"twin-services": repo})
    head = reader.head("twin-services")
    hits = reader.grep("twin-services", head, "planned_time")
    assert hits and "svc.py" in hits[0]
    with pytest.raises(CodeRepoError, match="등록"):
        reader.head("ghost-repo")
