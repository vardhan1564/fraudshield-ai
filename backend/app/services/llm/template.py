"""The final fallback: a fixed sentence, instant and always available."""

from app.services.explain import template_sentence
from app.services.llm.base import ExplainRequest


class TemplateProvider:
    name = "template"

    async def sentence(self, req: ExplainRequest, timeout_s: float) -> str:
        return template_sentence(req.decision, req.action, req.reasons, req.language)
