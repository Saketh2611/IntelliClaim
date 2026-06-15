import json
import time
import asyncio
from datetime import datetime
from google import genai
from core.config import settings
from core.exceptions import ComponentFailureError
from services.policy_loader import get_fraud_thresholds
from db import supabase

MODEL = settings.gemini_model


class FraudDetectorAgent:
    def __init__(self, claim_id: str):
        self.claim_id = claim_id
        self.client = genai.Client(api_key=settings.gemini_api_key)

    async def run(self, claim: dict) -> dict:
        start = time.time()

        try:
            thresholds = get_fraud_thresholds()
            claim_amount = claim.get("amount", 0)
            member_id = claim.get("member_id", "")

            claim_history = self._get_claim_history(member_id)

            prompt = f"""You are an insurance fraud detection agent. Analyze this claim for potential fraud indicators.

Claim details:
- Claim ID: {self.claim_id}
- Member ID: {member_id}
- Category: {claim.get('category')}
- Amount: ₹{claim_amount}
- Treatment date: {claim.get('treatment_date', 'unknown')}
- Provider: {claim.get('provider_name', 'unknown')}

Fraud thresholds:
{json.dumps(thresholds, indent=2)}

Member's recent claim history:
{json.dumps(claim_history, indent=2)}

Analyze for these fraud indicators:
1. High-value claim (above ₹{thresholds.get('high_value_claim_threshold', 25000)})
2. Frequency abuse (>{thresholds.get('same_day_claims_limit', 2)} claims same day, >{thresholds.get('monthly_claims_limit', 6)} claims/month)
3. Unusual patterns (repeated same-amount claims, weekend-heavy claims)
4. Provider red flags (non-network provider for high amounts)

Respond with JSON:
{{
  "fraud_score": <0.0 to 1.0>,
  "risk_level": "LOW" / "MEDIUM" / "HIGH",
  "flags": ["flag1", "flag2"],
  "requires_manual_review": true/false,
  "reasoning": "..."
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
                    result = {"fraud_score": 0.0, "risk_level": "LOW", "flags": [], "requires_manual_review": False, "reasoning": "Unable to parse LLM response"}

            if result.get("fraud_score", 0) >= thresholds.get("fraud_score_manual_review_threshold", 0.8):
                result["requires_manual_review"] = True

            if claim_amount >= thresholds.get("auto_manual_review_above", 25000):
                result["requires_manual_review"] = True
                if "high_value_claim" not in result.get("flags", []):
                    result.setdefault("flags", []).append("high_value_claim")

            duration_ms = int((time.time() - start) * 1000)
            self._write_trace("fraud_detector", "passed", duration_ms, output=result)
            return result

        except (ComponentFailureError,):
            raise
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            self._write_trace("fraud_detector", "failed", duration_ms, error_message=str(e))
            raise ComponentFailureError("fraud_detector", str(e))

    def _get_claim_history(self, member_id: str) -> list:
        try:
            response = (
                supabase.table("claims")
                .select("claim_id, category, amount, status, created_at")
                .eq("member_id", member_id)
                .order("created_at", desc=True)
                .limit(10)
                .execute()
            )
            return response.data or []
        except Exception:
            return []

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
