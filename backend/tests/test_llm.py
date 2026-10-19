"""LLM providers and the fallback chain. No real model is called: Claude and Ollama are mocked."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import httpx2
import pytest

from app.core.config import Settings
from app.models.enums import Decision, Language
from app.services.llm import ExplainRequest, LLMError, build_chain, explain_sentence
from app.services.llm.claude import ClaudeProvider
from app.services.llm.ollama import OllamaProvider
from app.services.llm.prompts import SYSTEM, clean, user_prompt
from app.services.llm.template import TemplateProvider

REQ = ExplainRequest(
    decision=Decision.STEP_UP,
    action="SCAM_COACH",
    reasons=["You are on a call with an unknown number", "Screen sharing is on"],
    language=Language.HI,
)


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)


def test_chain_follows_llm_provider() -> None:
    names = lambda s: [p.name for p in build_chain(s)]  # noqa: E731
    assert names(settings(llm_provider="anthropic", anthropic_api_key="k")) == [
        "anthropic", "ollama", "template"
    ]  # fmt: skip
    assert names(settings(llm_provider="anthropic")) == ["ollama", "template"]  # no key
    assert names(settings(llm_provider="ollama")) == ["ollama", "template"]
    assert names(settings(llm_provider="template")) == ["template"]


def test_prompt_has_the_reasons_and_language_but_no_pii() -> None:
    text = user_prompt(REQ)
    assert "Hindi" in text and "- Screen sharing is on" in text and "one more check" in text
    assert "OTP" in SYSTEM and "one sentence" in SYSTEM
    assert clean('  "Line one\n line two"  ') == "Line one line two"
    assert clean("x" * 401) == ""


class Fixed:
    def __init__(self, name: str, result: str | Exception, delay: float = 0.0) -> None:
        self.name, self.result, self.delay, self.calls = name, result, delay, 0

    async def sentence(self, req: ExplainRequest, timeout_s: float) -> str:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def test_first_working_provider_wins() -> None:
    first, second = Fixed("a", "From A."), Fixed("b", "From B.")
    assert await explain_sentence(REQ, [first, second, TemplateProvider()], 500) == ("From A.", "a")
    assert second.calls == 0


async def test_failures_and_timeouts_fall_back_to_the_template() -> None:
    broken = Fixed("broken", LLMError("down"))
    slow = Fixed("slow", "Too late.", delay=1.0)
    text, used = await explain_sentence(REQ, [broken, slow, TemplateProvider()], 50)
    assert used == "template" and text.startswith("भुगतान से पहले")
    assert broken.calls == 1 and slow.calls == 1


class FakeMessages:
    def __init__(self, reply: Any) -> None:
        self.reply, self.kwargs, self.timeout = reply, {}, None

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class FakeClaude:
    def __init__(self, reply: Any) -> None:
        self.messages = FakeMessages(reply)

    def with_options(self, timeout: float) -> "FakeClaude":
        self.messages.timeout = timeout
        return self


def reply(text: str, stop: str = "end_turn") -> Any:
    blocks = [
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text=text),
    ]
    return SimpleNamespace(stop_reason=stop, content=blocks)


async def test_claude_provider() -> None:
    fake = FakeClaude(reply(" यह एक घोटाला हो सकता है। "))
    provider = ClaudeProvider("key", "claude-haiku-5-5", client=fake)  # type: ignore[arg-type]
    assert await provider.sentence(REQ, 1.5) == "यह एक घोटाला हो सकता है।"
    sent = fake.messages.kwargs
    assert sent["model"] == "claude-haiku-5-5" and sent["system"] == SYSTEM
    assert sent["output_config"] == {"effort": "low"} and fake.messages.timeout == 1.5
    assert "Hindi" in sent["messages"][0]["content"]

    for bad in (reply("x", stop="refusal"), reply("   ")):
        with pytest.raises(LLMError):
            await ClaudeProvider("k", "m", client=FakeClaude(bad)).sentence(REQ, 1)  # type: ignore[arg-type]


async def test_claude_api_errors_become_llm_errors() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    for error in (
        anthropic.APITimeoutError(request),
        anthropic.APIConnectionError(request=request),
    ):
        with pytest.raises(LLMError, match=type(error).__name__):
            await ClaudeProvider("k", "m", client=FakeClaude(error)).sentence(REQ, 1)  # type: ignore[arg-type]


async def test_ollama_provider() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content), url=str(request.url))
        return httpx.Response(200, json={"message": {"content": "Looks like a scam."}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaProvider("http://ollama:11434/", "llama3.1", client=client)
    assert await provider.sentence(REQ, 1.0) == "Looks like a scam."
    assert seen["url"] == "http://ollama:11434/api/chat" and seen["model"] == "llama3.1"
    assert seen["stream"] is False and seen["messages"][0]["content"] == SYSTEM

    down = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(LLMError):
        await OllamaProvider("http://x", "m", client=down).sentence(REQ, 1.0)
