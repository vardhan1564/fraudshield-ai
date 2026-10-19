"""POST /transactions/score request and response (spec 04 §2.1)."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, IPvAnyAddress, field_validator

from app.models.enums import Channel, Decision, Language
from app.services.decision import Action
from ml.features.schema import MERCHANT_CATEGORIES


class Geo(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class PaymentContext(BaseModel):
    """Call / screen-share signals (spec 01 F2). Missing means 'not happening'."""

    on_call: bool = False
    caller_in_contacts: bool = False
    call_duration_s: int = Field(default=0, ge=0)
    screen_share_active: bool = False
    remote_access_app: str | None = Field(default=None, max_length=64)
    is_vpn: bool = False  # from the bank's network checks (not in the original spec example)


class Behaviour(BaseModel):
    typing_speed_z: float = 0.0
    session_len_s: float = Field(default=0.0, ge=0)
    login_to_pay_s: float = Field(default=0.0, ge=0)


class ScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    txn_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64, description="Customer id, e.g. U1029")
    amount: float = Field(gt=0, le=10_000_000)
    currency: Literal["INR"] = "INR"
    channel: Channel
    payee_vpa: str = Field(min_length=3, max_length=255)
    merchant_category: str = Field(default="P2P", max_length=32)
    device_id: str | None = Field(default=None, max_length=128)
    ip: IPvAnyAddress | None = None
    geo: Geo | None = None
    timestamp: AwareDatetime
    context: PaymentContext = PaymentContext()
    behaviour: Behaviour = Behaviour()

    @field_validator("merchant_category")
    @classmethod
    def _known_category(cls, value: str) -> str:
        value = value.upper()
        if value not in MERCHANT_CATEGORIES:
            raise ValueError(f"must be one of {', '.join(MERCHANT_CATEGORIES)}")
        return value


class ModelScores(BaseModel):
    transaction: float
    anomaly: float
    graph: float
    social_eng: float


class Reason(BaseModel):
    feature: str
    value: float | int | bool | str
    impact: float
    text: str


class CoachQuestion(BaseModel):
    key: str
    text: str


class ScoreResponse(BaseModel):
    txn_id: str
    risk_score: float
    decision: Decision
    action: Action
    model_scores: ModelScores
    top_reasons: list[Reason]
    explanation: str
    explanation_status: Literal["ready", "pending"]
    coach_questions: list[CoachQuestion]
    model_version: str
    degraded: list[str]
    latency_ms: int


class ExplainResponse(BaseModel):
    """GET /transactions/{id}/explain (spec 04 §2.3): the stored top 10 SHAP values."""

    txn_id: str
    shap: list[Reason]
    explanation: str
    language: Language
