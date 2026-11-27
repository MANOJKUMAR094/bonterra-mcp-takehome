from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

RoleType = Literal["case_worker", "program_manager", "finance_staff"]
RequestStatusType = Literal["Pending", "Approved", "Rejected", "Paid"]
ErrorCodeType = Literal[
    "UNAUTHORIZED",
    "FORBIDDEN",
    "NOT_FOUND",
    "VALIDATION_ERROR",
    "INSUFFICIENT_FUNDS",
    "INVALID_STATE_TRANSITION",
]


def format_usd(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    abs_cents = abs(cents)
    return f"{sign}${abs_cents // 100:,}.{abs_cents % 100:02d}"


class DomainError(Exception):
    """Structured domain exception mapped to MCP error envelopes."""

    def __init__(self, code: ErrorCodeType, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class StaffContext(BaseModel):
    id: str
    nonprofit_id: str
    name: str
    role: RoleType


class CreateRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant_id: str = Field(..., min_length=1)
    fund_id: str = Field(..., min_length=1)
    amount_cents: int
    purpose: str = Field(..., min_length=1, max_length=500)

    @field_validator("amount_cents", mode="before")
    @classmethod
    def reject_bool_and_float(cls, v: Any) -> int:
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("amount_cents must be a strict integer representing USD cents")
        if v <= 0:
            raise ValueError("amount_cents must be greater than 0")
        return v

    @field_validator("participant_id", "fund_id", "purpose")
    @classmethod
    def strip_non_empty(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field cannot be blank or whitespace only")
        return cleaned


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(..., min_length=1)
    decision_note: str | None = Field(default=None, max_length=500)

    @field_validator("request_id")
    @classmethod
    def clean_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("request_id cannot be blank")
        return cleaned

    @field_validator("decision_note")
    @classmethod
    def clean_note(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned if cleaned else None


class PaymentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(..., min_length=1)
    payment_reference: str | None = Field(default=None, max_length=200)

    @field_validator("request_id")
    @classmethod
    def clean_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("request_id cannot be blank")
        return cleaned

    @field_validator("payment_reference")
    @classmethod
    def clean_ref(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned if cleaned else None


def success_response(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "data": data}


def error_response(err: DomainError) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": err.code,
            "message": err.message,
            "details": err.details,
        },
    }
