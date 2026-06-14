from pydantic import BaseModel
from typing import Optional
from enum import Enum


class DecisionOutcome(str, Enum):
    APPROVED      = "APPROVED"
    PARTIAL       = "PARTIAL"
    REJECTED      = "REJECTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class LineItemDecision(BaseModel):
    description: str
    amount:      float
    status:      str       # APPROVED / REJECTED
    reason:      str


class DecisionResponse(BaseModel):
    decision_id:             str
    outcome:                 DecisionOutcome
    approved_amount:         float
    reason:                  str
    confidence:              float
    breakdown:               Optional[list[LineItemDecision]] = None
    network_discount_applied: Optional[float] = None
    copay_deducted:          Optional[float] = None
    decided_at:              str