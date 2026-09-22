"""파일 턴 어댑터 — **바깥의 무언가가 답을 써 넣는** LLM 자리.

사내 모델은 이 리포 밖에서만 돈다. 이 어댑터가 있어야 같은 배선을 여기서 끝까지
돌리고 리드 자리에만 약한 모델 대역을 세울 수 있다.
"""
import asyncio
from datetime import datetime

import pytest

from src.config.schema_llm import LlmConfig
from src.infrastructure.llm_factory import build_llm
from src.infrastructure.llm_fakes import FileTurnAdapter

T0 = datetime(2026, 9, 14, 9, 0)


async def test_프롬프트를_파일로_내고_답_파일을_읽는다(tmp_path):
    llm = FileTurnAdapter(tmp_path, clock=lambda: T0, poll_s=0.01)

    async def answer():
        while not (tmp_path / "001-ask.md").exists():
            await asyncio.sleep(0.01)
        assert (tmp_path / "001-ask.md").read_text(encoding="utf-8") == "질문"
        (tmp_path / "001-reply.md").write_text("답", encoding="utf-8")

    got, _ = await asyncio.gather(llm.ask("질문"), answer())
    assert got.status == "ok" and got.text == "답"


async def test_답이_안_오면_오류로_흡수한다(tmp_path):
    """던지지 않는다(규율 1). 리드는 `stopped_by=llm_error`로 정직하게 끝난다."""
    llm = FileTurnAdapter(tmp_path, clock=lambda: T0, timeout_s=0.03, poll_s=0.01)
    got = await llm.ask("질문")
    assert got.status == "error" and "안 왔다" in (got.error or "")


async def test_지난_실행의_답_파일을_새_답으로_읽지_않는다(tmp_path):
    (tmp_path / "001-reply.md").write_text("지난 답", encoding="utf-8")
    llm = FileTurnAdapter(tmp_path, clock=lambda: T0, timeout_s=0.03, poll_s=0.01)
    got = await llm.ask("질문")
    assert got.status == "error", "남아 있던 답을 새 답으로 읽었다"


def test_config로_조립된다(tmp_path):
    cfg = LlmConfig(adapter="file", model="standin", turn_dir=str(tmp_path))
    assert isinstance(build_llm(cfg, clock=lambda: T0), FileTurnAdapter)


def test_file_어댑터는_turn_dir이_필요하다():
    with pytest.raises(ValueError, match="turn_dir"):
        LlmConfig(adapter="file", model="standin")
