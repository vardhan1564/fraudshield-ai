"""Claude via the Anthropic SDK (LLM_PROVIDER=anthropic, model from LLM_MODEL)."""

import anthropic
from anthropic import AsyncAnthropic

from app.services.llm.base import ExplainRequest, LLMError
from app.services.llm.prompts import SYSTEM, clean, user_prompt


class ClaudeProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, client: AsyncAnthropic | None = None) -> None:
        self.model = model
        # No SDK retries: the fallback chain is the retry, and the worker has a time budget.
        self.client = client or AsyncAnthropic(api_key=api_key, max_retries=0)

    async def sentence(self, req: ExplainRequest, timeout_s: float) -> str:
        try:
            message = await self.client.with_options(timeout=timeout_s).messages.create(
                model=self.model,
                max_tokens=1024,
                output_config={"effort": "low"},  # one short sentence
                system=SYSTEM,
                messages=[{"role": "user", "content": user_prompt(req)}],
            )
        except anthropic.APIError as exc:  # status, connection and timeout errors
            raise LLMError(f"anthropic: {type(exc).__name__}") from exc
        if message.stop_reason == "refusal":
            raise LLMError("anthropic: refused")
        text = clean("".join(b.text for b in message.content if b.type == "text"))
        if not text:
            raise LLMError("anthropic: empty reply")
        return text
