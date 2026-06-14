from pydantic import BaseModel
from typing import Optional


class TraceStep(BaseModel):
    step_name:       str
    status:          str        # passed / failed / skipped / degraded
    input_snapshot:  Optional[dict] = None
    output_snapshot: Optional[dict] = None
    error_message:   Optional[str]  = None
    duration_ms:     Optional[int]  = None
    created_at:      str


class ClaimTraceResponse(BaseModel):
    claim_id:    str
    steps:       list[TraceStep]
    total_steps: int
    failed_steps: int