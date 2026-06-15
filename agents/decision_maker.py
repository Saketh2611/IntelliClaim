import json
import time
import uuid
import asyncio
from datetime import datetime
from google import genai
from core.config import settings
from db import supabase

MODEL = settings.gemini_model


class DecisionMakerAgent:
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

        prompt = f"""You are an insurance claim decision maker. Based on all available information, make a final decision on this claim.

Claim details:
- Claim ID: {self.claim_id}
- Member ID: {claim.get('member_id')}
- Category: {claim.get('category')}
- Amount requested: ₹{claim.get('amount', 0)}
- Treatment date: {claim.get('treatment_date', 'unknown')}
- Provider: {claim.get('provider_name', 'unknown')}

Pipeline confidence: {confidence}
Pipeline degraded (some agents failed): {degraded}

Policy check result:
{json.dumps(policy_result, indent=2) if policy_result else "NOT AVAILABLE (agent failed)"}

Fraud detection result:
{json.dumps(fraud_result, indent=2) if fraud_result else "NOT AVAILABLE (agent failed)"}

Number of documents: {len(documents)}
Document types: {[d.get('document_type') for d in documents]}

Decision rules:
1. If fraud_score >= 0.8 OR requires_manual_review → MANUAL_REVIEW
2. If policy says not eligible (exclusion or waiting period) → REJECTED
3. If approved_amount < claim_amount but > 0 → PARTIAL (approve the eligible portion)
4. If all checks pass and amount within limits → APPROVED
5. If pipeline is degraded (confidence < 0.7) → MANUAL_REVIEW
6. If confidence < 0.5 → MANUAL_REVIEW regardless

Respond with JSON:
{{
  "outcome": "APPROVED" / "PARTIAL" / "REJECTED" / "MANUAL_REVIEW",
  "approved_amount": <number>,
  "reason": "concise explanation",
  "confidence": <0.0 to 1.0>,
  "breakdown": [
    {{"description": "item", "amount": <number>, "status": "APPROVED/REJECTED", "reason": "..."}}
  ],
  "network_discount_applied": <number or null>,
  "copay_deducted": <number or null>
}}"""

        response = await asyncio.to_thread(self.client.models.generate_content, model=MODEL, contents=prompt)
        result_text = getattr(response, "text", None) or getattr(response, "response", None) or str(response)
        try:
            result = json.loads(result_text)
        except json.JSONDecodeError:
            start_idx = result_text.find("{")
            end_idx = result_text.rfind("}") + 1
            if start_idx != -1 and end_idx > start_idx:
                result = json.loads(result_text[start_idx:end_idx])
            else:
                result = {
                    "outcome": "MANUAL_REVIEW",
                    "approved_amount": 0,
                    "reason": "Unable to parse decision model response",
                    "confidence": 0.0,
                }

        if confidence < 0.5:
            result["outcome"] = "MANUAL_REVIEW"
            result["reason"] = f"Pipeline confidence too low ({confidence}). " + result.get("reason", "")

        decision = {
            "decision_id": str(uuid.uuid4()),
            "outcome": result.get("outcome", "MANUAL_REVIEW"),
            "approved_amount": result.get("approved_amount", 0),
            "reason": result.get("reason", ""),
            "confidence": confidence,
            "breakdown": result.get("breakdown"),
            "network_discount_applied": result.get("network_discount_applied"),
            "copay_deducted": result.get("copay_deducted"),
            "decided_at": datetime.utcnow().isoformat(),
        }

        duration_ms = int((time.time() - start) * 1000)
        self._write_trace("decision_maker", "passed", duration_ms, output=decision)
        self._save_decision(decision)

        return decision

    def _save_decision(self, decision: dict):
        supabase.table("claims").update({
            "decision_outcome": decision["outcome"],
            "approved_amount": decision["approved_amount"],
            "decision_reason": decision["reason"],
            "decided_at": decision["decided_at"],
        }).eq("claim_id", self.claim_id).execute()

    def _write_trace(self, step_name: str, status: str, duration_ms: int, output: dict = None, error_message: str = None):
        supabase.table("trace_steps").insert({
            "claim_id": self.claim_id,
            "step_name": step_name,
            "status": status,
            "duration_ms": duration_ms,
            "output_snapshot": output,
            "error_message": error_message,
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
