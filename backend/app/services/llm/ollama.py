"""Local model via Ollama's chat API (fallback when Claude is unavailable)."""

import httpx

from app.services.llm.base import ExplainRequest, LLMError
from app.services.llm.prompts import SYSTEM, clean, user_prompt


class OllamaProvider:
    name = "ollama"

    def __init__(self, url: str, model: str, client: httpx.AsyncClient | None = None) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.client = client or httpx.AsyncClient()

    async def sentence(self, req: ExplainRequest, timeout_s: float) -> str:
        body = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_prompt(req)},
            ],
        }
        try:
            resp = await self.client.post(f"{self.url}/api/chat", json=body, timeout=timeout_s)
            resp.raise_for_status()
            text = clean(str(resp.json()["message"]["content"]))
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise LLMError(f"ollama: {type(exc).__name__}") from exc
        if not text:
            raise LLMError("ollama: empty reply")
        return text
