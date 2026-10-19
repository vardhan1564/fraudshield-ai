"""Prompt for the one-sentence explanation (spec 01 F4). Same text for Claude and Ollama."""

from app.models.enums import Decision, Language
from app.services.llm.base import ExplainRequest

LANGUAGE_NAMES = {Language.EN: "English", Language.HI: "Hindi", Language.MR: "Marathi"}

SYSTEM = """\
You write the one-sentence explanation a customer of an Indian payments app sees when a payment \
is checked for fraud.

Rules:
- Write exactly one sentence, at most 30 words, in the language you are asked for.
- Use only the reasons given. Do not invent facts, amounts, names or numbers.
- Plain, calm words a first-time smartphone user understands. No jargon such as "SHAP", "model" \
or "score".
- When the reasons point to a scam (unknown caller, screen sharing, a payee others reported), \
say what the scam usually looks like and that real banks, police or officials never ask for \
payment this way.
- Never ask for an OTP, PIN, password or card number, and never tell the customer to call a \
number.
- Reply with the sentence only: no quotes, no heading, no list."""

WHAT_HAPPENS = {
    Decision.APPROVE: "The payment was approved.",
    Decision.STEP_UP: "The payment needs one more check before it goes through ({action}).",
    Decision.HOLD: "The payment is on hold until a fraud analyst checks it.",
}


def user_prompt(req: ExplainRequest) -> str:
    reasons = "\n".join(f"- {r}" for r in req.reasons) or "- (no specific reason)"
    return (
        f"Decision: {WHAT_HAPPENS[req.decision].format(action=req.action)}\n"
        f"Reasons, most important first:\n{reasons}\n\n"
        f"Write the sentence in {LANGUAGE_NAMES[req.language]}."
    )


def clean(text: str) -> str:
    """One line, no wrapping quotes; empty or overlong output is not usable."""
    out = " ".join(text.split()).strip().strip('"“”').strip()
    return out if 0 < len(out) <= 400 else ""
