import time
from datetime import datetime
from db import supabase
from core.exceptions import (
    DocumentValidationError,
    UnreadableDocumentError,
    PatientMismatchError,
    ComponentFailureError,
)
from agents.document_validator  import DocumentValidatorAgent
from agents.extractor           import ExtractionAgent
from agents.cross_doc_validator import CrossDocValidatorAgent
from agents.policy_checker      import PolicyCheckerAgent
from agents.fraud_detector      import FraudDetectorAgent
from agents.decision_maker      import DecisionMakerAgent


class ClaimPipeline:
    """
    Orchestrates all agents in sequence.
    Each agent writes its own trace step.
    Gracefully degrades if a non-critical agent fails.
    """

    def __init__(self, claim_id: str, simulate_failure: bool = False):
        self.claim_id         = claim_id
        self.simulate_failure = simulate_failure
        self.confidence       = 1.0
        self.failed_components = []

    async def run(self, claim: dict, documents: list[dict]) -> dict:
        self._update_claim_status("processing")

        # ── Stage 1: Document validation (CRITICAL — stop if fails)
        try:
            await DocumentValidatorAgent(self.claim_id).run(claim, documents)
        except (DocumentValidationError, UnreadableDocumentError, PatientMismatchError):
            self._update_claim_status("failed")
            raise   # bubble up — these need specific user-facing messages

        # ── Stage 2: Extraction (CRITICAL — need data to proceed)
        try:
            documents = await ExtractionAgent(self.claim_id).run(documents)
        except ComponentFailureError as e:
            self._handle_degradation(e.component, e.reason, penalty=0.3)

        # ── Stage 3: Cross-document validation (CRITICAL)
        try:
            await CrossDocValidatorAgent(self.claim_id).run(claim, documents)
        except PatientMismatchError:
            self._update_claim_status("failed")
            raise

        # ── Stage 4: Policy check (NON-CRITICAL — degrade if fails)
        policy_result = {}
        try:
            if self.simulate_failure:
                raise ComponentFailureError("policy_checker", "Simulated failure (TC011)")
            policy_result = await PolicyCheckerAgent(self.claim_id).run(claim, documents)
        except ComponentFailureError as e:
            self._handle_degradation(e.component, e.reason, penalty=0.25)

        # ── Stage 5: Fraud detection (NON-CRITICAL — degrade if fails)
        fraud_result = {}
        try:
            fraud_result = await FraudDetectorAgent(self.claim_id).run(claim)
        except ComponentFailureError as e:
            self._handle_degradation(e.component, e.reason, penalty=0.1)

        # ── Stage 6: Decision (always runs — works with whatever it has)
        decision = await DecisionMakerAgent(self.claim_id).run(
            claim         = claim,
            documents     = documents,
            policy_result = policy_result,
            fraud_result  = fraud_result,
            confidence    = self.confidence,
            degraded      = len(self.failed_components) > 0,
        )

        self._update_claim_status("completed")
        return decision

    def _handle_degradation(self, component: str, reason: str, penalty: float):
        """Log the failure, reduce confidence, continue pipeline."""
        self.failed_components.append(component)
        self.confidence = max(0.0, self.confidence - penalty)
        self._write_trace(
            step_name     = component,
            status        = "degraded",
            error_message = reason,
        )

    def _update_claim_status(self, status: str):
        supabase.table("claims").update({"status": status}).eq("claim_id", self.claim_id).execute()

    def _write_trace(self, step_name: str, status: str, error_message: str = None):
        supabase.table("trace_steps").insert({
            "claim_id":      self.claim_id,
            "step_name":     step_name,
            "status":        status,
            "error_message": error_message,
            "created_at":    datetime.utcnow().isoformat(),
        }).execute()