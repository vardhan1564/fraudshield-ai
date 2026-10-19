"""What every LLM provider takes and returns (spec 01 F4)."""

from dataclasses import dataclass
from typing import Protocol

from app.models.enums import Decision, Language


@dataclass(frozen=True)
class ExplainRequest:
    """Only what the sentence needs: no names, phone numbers, VPAs or account numbers."""

    decision: Decision
    action: str
    reasons: list[str]  # reason_map phrases, most important first
    language: Language


class LLMError(Exception):
    """The provider could not give a usable sentence; the caller falls back to the next one."""


class Provider(Protocol):
    name: str

    async def sentence(self, req: ExplainRequest, timeout_s: float) -> str: ...
