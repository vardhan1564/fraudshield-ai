"""Rules CRUD (spec 04 endpoint 23; condition format in spec 05)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import RuleAction
from ml.features.schema import DTYPES, MERCHANT_CATEGORIES

OPS = (">", ">=", "<", "<=", "==", "!=")
MAX_CLAUSES = 20


def check_condition(condition: dict[str, Any]) -> dict[str, Any]:
    """`{"all"|"any": [{"field", "op", "value"}, ...]}` with real feature names (spec 03 §2),
    a known operator and a value of the feature's type. Raises ValueError with what is wrong."""
    if set(condition) not in ({"all"}, {"any"}):
        raise ValueError('use exactly one of "all" or "any"')
    clauses = next(iter(condition.values()))
    if not isinstance(clauses, list) or not 1 <= len(clauses) <= MAX_CLAUSES:
        raise ValueError(f"needs 1 to {MAX_CLAUSES} clauses")
    for i, clause in enumerate(clauses):
        where = f"clause {i + 1}"
        if not isinstance(clause, dict) or set(clause) != {"field", "op", "value"}:
            raise ValueError(f'{where}: needs exactly "field", "op" and "value"')
        name, op, value = clause["field"], clause["op"], clause["value"]
        if name not in DTYPES:
            raise ValueError(f"{where}: unknown feature {name!r}")
        if op not in OPS:
            raise ValueError(f"{where}: op must be one of {', '.join(OPS)}")
        dtype = DTYPES[name]
        if dtype == "bool":
            if not isinstance(value, bool) or op not in ("==", "!="):
                raise ValueError(f"{where}: {name} is true/false: use == or != with true/false")
        elif dtype == "category":
            if value not in MERCHANT_CATEGORIES or op not in ("==", "!="):
                raise ValueError(f"{where}: {name} needs == or != and one of "
                                 f"{', '.join(MERCHANT_CATEGORIES)}")  # fmt: skip
        elif isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{where}: {name} needs a number")
    return condition


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    condition_json: dict[str, Any]
    action: RuleAction
    priority: int = Field(default=100, ge=0, le=10_000)  # lower runs first
    is_active: bool = True

    @field_validator("condition_json")
    @classmethod
    def _valid(cls, value: dict[str, Any]) -> dict[str, Any]:
        return check_condition(value)


class RuleOut(BaseModel):
    id: uuid.UUID
    name: str
    condition_json: dict[str, Any]
    action: RuleAction
    priority: int
    is_active: bool
    updated_by: uuid.UUID | None
    created_at: datetime
