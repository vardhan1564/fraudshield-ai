"""Simulator settings: config.yaml loaded and checked with Pydantic."""

from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ml.simulator.schema import HARD_NEGATIVES, SHARED_SCENARIOS

DEFAULT_CONFIG = Path(__file__).with_name("config.yaml")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _check_range(name: str, pair: tuple[float, float]) -> None:
    if pair[0] > pair[1]:
        raise ValueError(f"{name}: min {pair[0]} is greater than max {pair[1]}")


class RingConfig(_Frozen):
    count: int = Field(ge=0)
    min_size: int = Field(ge=4)  # collector + two forwarding layers need a few accounts
    max_size: int
    victims_per_collector: tuple[int, int]
    forward_share: tuple[float, float]
    new_account_share: float = Field(ge=0, le=1)
    accounts_per_device: int = Field(ge=1)

    @model_validator(mode="after")
    def _ranges(self) -> Self:
        _check_range("rings.size", (self.min_size, self.max_size))
        _check_range("rings.victims_per_collector", self.victims_per_collector)
        _check_range("rings.forward_share", self.forward_share)
        if not 0 < self.forward_share[0] <= self.forward_share[1] <= 1:
            raise ValueError("rings.forward_share must be within (0, 1]")
        return self


class NormalConfig(_Frozen):
    repeat_payee_share: float = Field(ge=0, le=1)
    failed_share: float = Field(ge=0, le=1)
    payees_per_user: tuple[int, int]
    off_hours_share: float = Field(ge=0, le=1)
    channel_mix: dict[Literal["UPI", "CARD", "WALLET"], float]

    @model_validator(mode="after")
    def _ranges(self) -> Self:
        _check_range("normal.payees_per_user", self.payees_per_user)
        if self.payees_per_user[0] < 1:
            raise ValueError("normal.payees_per_user must start at 1 or more")
        return self


class Category(_Frozen):
    weight: float = Field(gt=0)
    amount_mult: float = Field(gt=0)


class City(_Frozen):
    name: str
    tier: Literal[1, 2, 3]
    lat: float
    lon: float
    weight: float = Field(gt=0)


class SimConfig(_Frozen):
    seed: int
    n_transactions: int = Field(gt=0)
    n_users: int = Field(ge=50)
    start: datetime
    days: int = Field(ge=14)
    shares: dict[str, float]
    rings: RingConfig
    normal: NormalConfig
    hard_negatives: dict[str, float]
    merchant_categories: dict[str, Category]
    p2p_amount_mult: float = Field(gt=0)
    cities: list[City] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if set(self.shares) != set(SHARED_SCENARIOS):
            raise ValueError(f"shares must list exactly {SHARED_SCENARIOS}")
        if any(v <= 0 for v in self.shares.values()) or sum(self.shares.values()) >= 0.5:
            raise ValueError("each share must be > 0 and together < 0.5")
        if set(self.hard_negatives) != set(HARD_NEGATIVES):
            raise ValueError(f"hard_negatives must list exactly {HARD_NEGATIVES}")
        if self.start.tzinfo is None:
            raise ValueError("start needs a UTC offset, e.g. +05:30")
        for needed in ("ECOMMERCE", "EDUCATION", "RENT"):  # used by scenarios and hard negatives
            if needed not in self.merchant_categories:
                raise ValueError(f"merchant_categories must include {needed}")
        return self


def load_config(path: Path = DEFAULT_CONFIG, **overrides: Any) -> SimConfig:
    """Read the YAML file; keyword overrides (e.g. n_transactions=5000) win. None means keep."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.update({k: v for k, v in overrides.items() if v is not None})
    return SimConfig.model_validate(data)
