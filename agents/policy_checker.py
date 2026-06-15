import json
import time
import asyncio
from datetime import datetime
from google import genai
from core.config import settings
from core.exceptions import ComponentFailureError
from services.policy_loader import (
    get_coverage_for_category,
    get_waiting_periods,
    get_exclusions,
)
from db import supabase

MODEL = settings.gemini_model


class PolicyCheckerAgent:
    def __init__(self, claim_id: str):
        self.claim_id = claim_id
        self.client = genai.Client(api_key=settings.gemini_api_key)

    async def run(self, claim: dict, documents: list[dict]) -> dict:
        start = time.time()

        try:
            category = claim.get("category", "")
            coverage = get_coverage_for_category(category)
            waiting_periods = get_waiting_periods()
            exclusions = get_exclusions()

            claim_amount = claim.get("amount", 0)
            treatment_date = claim.get("treatment_date", "")
            member_join_date = claim.get("join_date", "2024-04-01")

            prompt = f"""You are an insurance policy checker. Determine if this claim is covered under the policy.

Claim details:
- Category: {category}
- Amount: ₹{claim_amount}
- Treatment date: {treatment_date}
- Member join date: {member_join_date}

Coverage rules for {category}:
{json.dumps(coverage, indent=2)}

Waiting periods:
{json.dumps(waiting_periods, indent=2)}

Exclusions:
{json.dumps(exclusions, indent=2)}

Extracted data from documents:
{json.dumps([d.get('extracted_data', {}) for d in documents], indent=2)}

Determine:
1. Is the claim within the sub-limit for this category?
2. Has the waiting period been satisfied?
3. Is the treatment/procedure excluded?
4. What copay percentage applies?
5. What is the approved amount after applying limits and copay?

Respond with JSON:
{{
  "eligible": true/false,
  "sub_limit": <number>,
  "claim_amount": <number>,
  "approved_amount": <number>,
  "copay_percent": <number>,
  "copay_deducted": <number>,
  "waiting_period_satisfied": true/false,
  "exclusion_hit": null or "reason",
  "requires_pre_auth": true/false,
  "notes": "..."
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
                    raise ValueError("Failed to parse policy check response")

            duration_ms = int((time.time() - start) * 1000)
            self._write_trace("policy_checker", "passed", duration_ms, output=result)
            return result

        except (ComponentFailureError,):
            raise
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            self._write_trace("policy_checker", "failed", duration_ms, error_message=str(e))
            raise ComponentFailureError("policy_checker", str(e))

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
