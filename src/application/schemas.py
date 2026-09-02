"""LLM 구조화 출력 스키마와 파서 — 스펙 §2.3·§2.4.

LLM 출력은 본문 JSON → pydantic 검증으로 받는다. with_structured_output 대신
이 경로를 쓰는 이유: 스크립트 가짜 LLM으로 전체 결정론 테스트가 되고(§5.5),
게이트웨이 호환성(도구 호출 미지원 모델)도 넓어진다.
"""
import json
import re
from typing import Literal

from pydantic import ValidationError, model_validator

from src.config.schema_app import StrictModel
from src.domain.case import Hypothesis, PlanTask

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class FrameOutput(StrictModel):
    hypotheses: list[Hypothesis]
    tasks: list[PlanTask]


class IntegrateOutput(StrictModel):
    hypotheses: list[Hypothesis] = []
    new_tasks: list[PlanTask] = []
    cancel_task_ids: list[str] = []
    decision: Literal["continue", "ask", "conclude"]
    question: str | None = None

    @model_validator(mode="after")
    def _ask_needs_question(self):
        if self.decision == "ask" and not self.question:
            raise ValueError("decision=ask면 question이 필요하다")
        return self


class SubagentReport(StrictModel):
    status: Literal["ok", "error"]
    summary: str
    evidence_ids: list[str] = []
    error: str | None = None


def parse_structured(text, model_cls):
    """본문에서 JSON을 찾아 model_cls로 검증한다. 실패는 (None, 한국어 원인).

    text는 보통 str이지만 langchain 메시지 content가 블록 리스트일 수 있다
    (예: [{"type": "text", "text": "..."}]) — 그 경우 텍스트 블록만 모아
    문자열화한다. str도 list도 아니면 파싱을 시도하지 않고 오류를 반환한다.
    """
    if isinstance(text, list):
        parts = []
        for block in text:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(block.get("text") or "")
        text = "".join(parts)
    elif not isinstance(text, str):
        return None, "문자열이 아닌 응답"
    match = _FENCE.search(text)
    candidate = match.group(1) if match else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            candidate = text[start:end + 1]
    if candidate is None:
        return None, "응답에서 JSON을 찾지 못했다"
    try:
        return model_cls.model_validate(json.loads(candidate)), None
    except json.JSONDecodeError as exc:
        return None, f"JSON 파싱 실패 — {exc}"
    except ValidationError as exc:
        return None, f"스키마 검증 실패 — {exc}"
