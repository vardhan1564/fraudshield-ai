"""One-sentence explanations with a fallback chain (spec 01 F4, spec 02 §7 "LLM down").

LLM_PROVIDER picks where the chain starts: anthropic -> ollama -> template, ollama -> template,
or template alone. Claude is skipped without ANTHROPIC_API_KEY. Each provider gets
LLM_TIMEOUT_MS; any failure moves on to the next, and the template never fails. Called by the
worker (T3.6), never in the scoring request.
"""

import asyncio
import logging

from app.core.config import Settings
from app.services.llm.base import ExplainRequest, LLMError, Provider
from app.services.llm.claude import ClaudeProvider
from app.services.llm.ollama import OllamaProvider
from app.services.llm.template import TemplateProvider

logger = logging.getLogger(__name__)

__all__ = ["ExplainRequest", "LLMError", "Provider", "build_chain", "explain_sentence"]


def build_chain(settings: Settings) -> list[Provider]:
    chain: list[Provider] = []
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key is not None:
        chain.append(
            ClaudeProvider(settings.anthropic_api_key.get_secret_value(), settings.llm_model)
        )
    if settings.llm_provider in ("anthropic", "ollama"):
        chain.append(OllamaProvider(settings.ollama_url, settings.ollama_model))
    chain.append(TemplateProvider())
    return chain


async def explain_sentence(
    req: ExplainRequest, chain: list[Provider], timeout_ms: int
) -> tuple[str, str]:
    """(sentence, provider name). Never raises: the last provider is the template."""
    timeout_s = timeout_ms / 1000
    for provider in chain[:-1]:
        try:
            return await asyncio.wait_for(
                provider.sentence(req, timeout_s), timeout_s
            ), provider.name
        except (LLMError, TimeoutError) as exc:
            logger.warning(
                "llm provider failed", extra={"provider": provider.name, "error": str(exc)}
            )
    last = chain[-1]
    return await last.sentence(req, timeout_s), last.name
