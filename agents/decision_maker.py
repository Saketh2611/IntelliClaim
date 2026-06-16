import asyncio
import json
import time
import uuid
from datetime import datetime

from google import genai

from core.config import settings
from core.exceptions import ComponentFailureError
from core.gemini_retry import call_gemini_with_retry
from db import supabase

MODEL = settings.gemini_model


class DecisionMakerAgent:
    """
    Final decision agent.
    Deterministic rules produce the authoritative outcome; the LLM is used only
    to improve wording when it is available.
    """

    def __init__(self, claim_id: str):
        self.claim_id = claim_id
        self.client = genai.Client(api_key=settings.gemini_api_key)

    async def run(
        self,
        claim: dict,
        documents: list[dict],
        policy_result: dict,
        fraud_result: dict,
        confidence: float,
        degraded: bool,
    ) -> dict:
        start = time.time()

        try:
            base_decision = self._deterministic_decision(
                claim=claim,
                policy_result=policy_result or {},
                fraud_result=fraud_result or {},
                confidence=confidence,
                degraded=degraded,
            )

            try:
                llm_result = await self._llm_reasoning(
                    claim=claim,
                    documents=documents,
                    policy_result=policy_result,
                    fraud_result=fraud_result,
                    confidence=confidence,
                    degraded=degraded,
                    base_decision=base_decision,
                )
                reason = llm_result.get("reason") or base_decision["reason"]
                trace_status = "passed"
                error_message = None
            except Exception as e:
                reason = base_decision["reason"]
                trace_status = "degraded"
                error_message = f"LLM decision wording unavailable: {str(e)}"

            decision = {
                "decision_id": str(uuid.uuid4()),
                "claim_id": self.claim_id,
                "outcome": base_decision["outcome"],
                "approved_amount": base_decision["approved_amount"],
                "reason": reason,
                "confidence": confidence,
                "breakdown": base_decision.get("breakdown"),
                "network_discount_applied": base_decision.get("network_discount_applied"),
                "copay_deducted": base_decision.get("copay_deducted"),
                "decided_at": datetime.utcnow().isoformat(),
            }

            self._save_decision(decision)
            duration_ms = int((time.time() - start) * 1000)
            self._write_trace(
                status=trace_status,
                input_snapshot={
                    "claim_category": claim.get("claim_category"),
                    "claimed_amount": claim.get("claimed_amount"),
                    "policy_available": bool(policy_result),
                    "fraud_available": bool(fraud_result),
                    "confidence": confidence,
                    "degraded": degraded,
                },
                output_snapshot=decision,
                error_message=error_message,
                duration_ms=duration_ms,
            )
            return decision

        except ComponentFailureError:
            raise
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            self._write_trace(
                status="failed",
                input_snapshot={
                    "claim_category": claim.get("claim_category"),
                    "claimed_amount": claim.get("claimed_amount"),
                    "confidence": confidence,
                },
                output_snapshot=None,
                error_message=str(e),
                duration_ms=duration_ms,
            )
            raise ComponentFailureError("decision_maker", str(e))

    def _deterministic_decision(
        self,
        claim: dict,
        policy_result: dict,
        fraud_result: dict,
        confidence: float,
        degraded: bool,
    ) -> dict:
        claimed_amount = float(claim.get("claimed_amount", 0) or 0)
        approved_amount = float(policy_result.get("approved_amount", 0) or 0)
        breakdown = policy_result.get("line_item_decisions") or None
        network_discount = policy_result.get("network_discount_applied")
        copay = policy_result.get("copay_deducted")

        if confidence < 0.5:
            return self._manual_review(
                approved_amount=0,
                reason=f"Pipeline confidence too low ({confidence}).",
                breakdown=breakdown,
                network_discount=network_discount,
                copay=copay,
            )

        if degraded or confidence < 0.7:
            return self._manual_review(
                approved_amount=0,
                reason="Pipeline degraded; manual review required before final approval.",
                breakdown=breakdown,
                network_discount=network_discount,
                copay=copay,
            )

        if (
            float(fraud_result.get("fraud_score", 0) or 0) >= 0.8
            or fraud_result.get("requires_manual_review")
        ):
            return self._manual_review(
                approved_amount=0,
                reason="Fraud risk requires manual review.",
                breakdown=breakdown,
                network_discount=network_discount,
                copay=copay,
            )

        if policy_result and not policy_result.get("eligible", True):
            reasons = policy_result.get("rejection_reasons") or []
            notes = policy_result.get("notes") or []
            return {
                "outcome": "REJECTED",
                "approved_amount": 0,
                "reason": self._join_reason(reasons, notes, "Policy check marked claim ineligible."),
                "breakdown": breakdown,
                "network_discount_applied": network_discount,
                "copay_deducted": copay,
            }

        if policy_result and 0 < approved_amount < claimed_amount:
            return {
                "outcome": "PARTIAL",
                "approved_amount": round(approved_amount, 2),
                "reason": self._join_reason(
                    policy_result.get("flags") or [],
                    policy_result.get("notes") or [],
                    "Part of the claim is eligible under policy limits.",
                ),
                "breakdown": breakdown,
                "network_discount_applied": network_discount,
                "copay_deducted": copay,
            }

        if policy_result:
            return {
                "outcome": "APPROVED",
                "approved_amount": round(approved_amount or claimed_amount, 2),
                "reason": self._join_reason(
                    policy_result.get("flags") or [],
                    policy_result.get("notes") or [],
                    "Claim passed policy and fraud checks.",
                ),
                "breakdown": breakdown,
                "network_discount_applied": network_discount,
                "copay_deducted": copay,
            }

        return self._manual_review(
            approved_amount=0,
            reason="Policy result unavailable; manual review required.",
            breakdown=breakdown,
            network_discount=network_discount,
            copay=copay,
        )

    async def _llm_reasoning(
        self,
        claim: dict,
        documents: list[dict],
        policy_result: dict,
        fraud_result: dict,
        confidence: float,
        degraded: bool,
        base_decision: dict,
    ) -> dict:
        prompt = f"""You are an insurance claim decision explainer.
The final outcome has already been determined by deterministic rules.
Write a concise user-facing reason without changing the outcome or amount.

Claim details:
- Claim ID: {self.claim_id}
- Member ID: {claim.get("member_id")}
- Category: {claim.get("claim_category")}
- Claimed amount: INR {claim.get("claimed_amount", 0)}
- Treatment date: {claim.get("treatment_date", "unknown")}
- Hospital/provider: {claim.get("hospital_name") or "unknown"}

Pipeline confidence: {confidence}
Pipeline degraded: {degraded}

Deterministic decision:
{json.dumps(base_decision, indent=2)}

Policy check result:
{json.dumps(policy_result, indent=2) if policy_result else "NOT AVAILABLE"}

Fraud detection result:
{json.dumps(fraud_result, indent=2) if fraud_result else "NOT AVAILABLE"}

Number of documents: {len(documents)}
Document types: {[d.get("document_type") for d in documents]}

Respond ONLY with JSON:
{{
  "reason": "concise explanation"
}}"""

        response = await call_gemini_with_retry(self.client, MODEL, prompt)
        text = getattr(response, "text", None) or str(response)
        return self._parse_json(text)

    def _manual_review(
        self,
        approved_amount: float,
        reason: str,
        breakdown: list[dict] | None,
        network_discount: float | None,
        copay: float | None,
    ) -> dict:
        return {
            "outcome": "MANUAL_REVIEW",
            "approved_amount": approved_amount,
            "reason": reason,
            "breakdown": breakdown,
            "network_discount_applied": network_discount,
            "copay_deducted": copay,
        }

    def _join_reason(
        self,
        primary: list,
        notes: list,
        default: str,
    ) -> str:
        details = [str(item) for item in primary + notes if item]
        if not details:
            return default
        return "; ".join(details[:3])

    def _parse_json(self, text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            text = text.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end > start:
                return json.loads(text[start:end])
            raise

    def _save_decision(self, decision: dict):
        supabase.table("decisions").upsert(
            {
                "decision_id": decision["decision_id"],
                "claim_id": self.claim_id,
                "outcome": decision["outcome"],
                "approved_amount": decision["approved_amount"],
                "reason": decision["reason"],
                "breakdown": decision.get("breakdown") or [],
                "confidence": decision["confidence"],
                "decided_at": decision["decided_at"],
            },
            on_conflict="claim_id",
        ).execute()

    def _write_trace(
        self,
        status: str,
        input_snapshot: dict,
        output_snapshot: dict | None,
        duration_ms: int,
        error_message: str | None = None,
    ):
        supabase.table("trace_steps").insert({
            "claim_id": self.claim_id,
            "step_name": "decision_maker",
            "status": status,
            "input_snapshot": input_snapshot,
            "output_snapshot": output_snapshot,
            "error_message": error_message,
            "duration_ms": duration_ms,
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
