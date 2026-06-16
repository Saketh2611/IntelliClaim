import asyncio
import json
import time
from datetime import date, datetime

from google import genai

from core.config import settings
from core.exceptions import ComponentFailureError
from core.gemini_retry import call_gemini_with_retry
from db import supabase
from services.policy_loader import get_fraud_thresholds, get_network_hospitals

MODEL = settings.gemini_model


class FraudDetectorAgent:
    """
    Fraud detection with deterministic guardrails first.
    The LLM can add softer pattern analysis, but a bad LLM response should not
    break claim processing.
    """

    def __init__(self, claim_id: str):
        self.claim_id = claim_id
        self.client = genai.Client(api_key=settings.gemini_api_key)

    async def run(self, claim: dict) -> dict:
        start = time.time()

        try:
            thresholds = get_fraud_thresholds()
            network_hospitals = get_network_hospitals()
            category = claim.get("claim_category", "")
            claimed_amount = float(claim.get("claimed_amount", 0) or 0)
            member_id = claim.get("member_id", "")
            treatment_date = str(claim.get("treatment_date", ""))
            hospital_name = claim.get("hospital_name") or ""

            claim_history = self._get_claim_history(member_id)
            deterministic = self._deterministic_result(
                thresholds=thresholds,
                network_hospitals=network_hospitals,
                claim_amount=claimed_amount,
                treatment_date=treatment_date,
                hospital_name=hospital_name,
                claim_history=claim_history,
            )

            try:
                llm_result = await self._llm_analysis(
                    claim_id=self.claim_id,
                    member_id=member_id,
                    category=category,
                    claimed_amount=claimed_amount,
                    treatment_date=treatment_date,
                    hospital_name=hospital_name,
                    thresholds=thresholds,
                    claim_history=claim_history,
                    deterministic_flags=deterministic["flags"],
                )
                result = self._merge_results(deterministic, llm_result, thresholds)
                trace_status = "passed"
                error_message = None
            except Exception as e:
                result = deterministic
                result["reasoning"] = (
                    result.get("reasoning", "")
                    + f" LLM fraud analysis unavailable: {str(e)}"
                ).strip()
                trace_status = "degraded"
                error_message = str(e)

            duration_ms = int((time.time() - start) * 1000)
            self._write_trace(
                status=trace_status,
                input_snapshot={
                    "claim_category": category,
                    "claimed_amount": claimed_amount,
                    "member_id": member_id,
                    "treatment_date": treatment_date,
                    "hospital_name": hospital_name,
                    "history_count": len(claim_history),
                },
                output_snapshot=result,
                error_message=error_message,
                duration_ms=duration_ms,
            )
            return result

        except ComponentFailureError:
            raise
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            self._write_trace(
                status="failed",
                input_snapshot={
                    "claim_category": claim.get("claim_category"),
                    "claimed_amount": claim.get("claimed_amount"),
                    "member_id": claim.get("member_id"),
                },
                output_snapshot=None,
                error_message=str(e),
                duration_ms=duration_ms,
            )
            raise ComponentFailureError("fraud_detector", str(e))

    def _deterministic_result(
        self,
        thresholds: dict,
        network_hospitals: list[str],
        claim_amount: float,
        treatment_date: str,
        hospital_name: str,
        claim_history: list[dict],
    ) -> dict:
        flags = []
        score = 0.0

        high_value_threshold = thresholds.get("high_value_claim_threshold", 25000)
        auto_review_above = thresholds.get("auto_manual_review_above", 25000)
        same_day_limit = thresholds.get("same_day_claims_limit", 2)
        monthly_limit = thresholds.get("monthly_claims_limit", 6)

        if claim_amount >= high_value_threshold:
            flags.append("high_value_claim")
            score = max(score, 0.65)

        same_day_count = self._same_day_count(claim_history, treatment_date)
        if same_day_count > same_day_limit:
            flags.append("same_day_claim_frequency")
            score = max(score, 0.75)

        monthly_count = self._monthly_count(claim_history, treatment_date)
        if monthly_count > monthly_limit:
            flags.append("monthly_claim_frequency")
            score = max(score, 0.7)

        repeated_amount_count = sum(
            1
            for item in claim_history
            if float(item.get("claimed_amount") or 0) == claim_amount
        )
        if repeated_amount_count >= 3:
            flags.append("repeated_same_amount_claims")
            score = max(score, 0.55)

        is_network = any(
            hospital.lower() in hospital_name.lower()
            for hospital in network_hospitals
        ) if hospital_name else False
        if claim_amount >= auto_review_above and hospital_name and not is_network:
            flags.append("high_value_non_network_provider")
            score = max(score, 0.8)

        if claim_amount >= auto_review_above:
            score = max(score, 0.8)

        score = min(round(score, 2), 1.0)
        return {
            "fraud_score": score,
            "risk_level": self._risk_level(score),
            "flags": flags,
            "requires_manual_review": score >= thresholds.get(
                "fraud_score_manual_review_threshold",
                0.8,
            ),
            "reasoning": self._reasoning(flags),
        }

    async def _llm_analysis(
        self,
        claim_id: str,
        member_id: str,
        category: str,
        claimed_amount: float,
        treatment_date: str,
        hospital_name: str,
        thresholds: dict,
        claim_history: list[dict],
        deterministic_flags: list[str],
    ) -> dict:
        prompt = f"""You are an insurance fraud detection agent.
Analyze this claim for fraud indicators, but do not override deterministic flags.

Claim details:
- Claim ID: {claim_id}
- Member ID: {member_id}
- Category: {category}
- Claimed amount: INR {claimed_amount}
- Treatment date: {treatment_date}
- Hospital/provider: {hospital_name or "unknown"}

Fraud thresholds:
{json.dumps(thresholds, indent=2)}

Deterministic flags already found:
{json.dumps(deterministic_flags, indent=2)}

Member claim history:
{json.dumps(claim_history, indent=2)}

Respond ONLY with JSON:
{{
  "fraud_score": 0.0,
  "risk_level": "LOW",
  "flags": [],
  "requires_manual_review": false,
  "reasoning": "short explanation"
}}"""

        response = await call_gemini_with_retry(self.client, MODEL, prompt)
        text = getattr(response, "text", None) or str(response)
        return self._parse_json(text)

    def _merge_results(
        self,
        deterministic: dict,
        llm_result: dict,
        thresholds: dict,
    ) -> dict:
        fraud_score = max(
            float(deterministic.get("fraud_score", 0) or 0),
            float(llm_result.get("fraud_score", 0) or 0),
        )
        flags = list(dict.fromkeys(
            (deterministic.get("flags") or []) + (llm_result.get("flags") or [])
        ))
        requires_manual_review = (
            bool(deterministic.get("requires_manual_review"))
            or bool(llm_result.get("requires_manual_review"))
            or fraud_score >= thresholds.get("fraud_score_manual_review_threshold", 0.8)
        )

        return {
            "fraud_score": round(min(fraud_score, 1.0), 2),
            "risk_level": self._risk_level(fraud_score),
            "flags": flags,
            "requires_manual_review": requires_manual_review,
            "reasoning": llm_result.get("reasoning")
            or deterministic.get("reasoning")
            or self._reasoning(flags),
        }

    def _get_claim_history(self, member_id: str) -> list[dict]:
        try:
            response = (
                supabase.table("claims")
                .select(
                    "claim_id, claim_category, claimed_amount, status, "
                    "treatment_date, hospital_name, submitted_at"
                )
                .eq("member_id", member_id)
                .order("submitted_at", desc=True)
                .limit(10)
                .execute()
            )
            return [
                item
                for item in (response.data or [])
                if str(item.get("claim_id")) != str(self.claim_id)
            ]
        except Exception:
            return []

    def _same_day_count(self, claim_history: list[dict], treatment_date: str) -> int:
        return 1 + sum(
            1
            for item in claim_history
            if str(item.get("treatment_date")) == treatment_date
        )

    def _monthly_count(self, claim_history: list[dict], treatment_date: str) -> int:
        current_month = self._month_key(treatment_date)
        if not current_month:
            return 1
        return 1 + sum(
            1
            for item in claim_history
            if self._month_key(str(item.get("treatment_date"))) == current_month
        )

    def _month_key(self, value: str) -> str | None:
        try:
            return date.fromisoformat(value).strftime("%Y-%m")
        except (TypeError, ValueError):
            return None

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

    def _risk_level(self, score: float) -> str:
        if score >= 0.8:
            return "HIGH"
        if score >= 0.5:
            return "MEDIUM"
        return "LOW"

    def _reasoning(self, flags: list[str]) -> str:
        if not flags:
            return "No deterministic fraud indicators found."
        return "Deterministic fraud indicators found: " + ", ".join(flags)

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
            "step_name": "fraud_detector",
            "status": status,
            "input_snapshot": input_snapshot,
            "output_snapshot": output_snapshot,
            "error_message": error_message,
            "duration_ms": duration_ms,
            "created_at": datetime.utcnow().isoformat(),
        }).execute()
