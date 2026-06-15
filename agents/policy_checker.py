import json
import time
import asyncio
from datetime import date, datetime, timedelta
from google import genai
from core.config import settings
from core.exceptions import ComponentFailureError
from services.policy_loader import (
    get_coverage_for_category,
    get_waiting_periods,
    get_exclusions,
    get_network_hospitals,
    load_policy,
)
from db import supabase

MODEL = settings.gemini_model

# keyword → condition key for fallback matching
DIAGNOSIS_CONDITION_MAP = {
    "diabetes":          ["diabetes", "t2dm", "type 2 diabetes", "diabetic", "dm2"],
    "hypertension":      ["hypertension", "htn", "high blood pressure"],
    "thyroid_disorders": ["thyroid", "hypothyroidism", "hyperthyroidism"],
    "joint_replacement": ["joint replacement", "knee replacement", "hip replacement", "arthroplasty"],
    "maternity":         ["maternity", "pregnancy", "antenatal", "prenatal", "delivery"],
    "mental_health":     ["mental health", "depression", "anxiety", "psychiatric"],
    "obesity_treatment": ["obesity", "bariatric", "weight loss", "morbid obesity"],
    "hernia":            ["hernia"],
    "cataract":          ["cataract"],
}

# procedures requiring pre-auth → minimum amount threshold (0 = always)
PRE_AUTH_TRIGGERS = {
    "MRI":      10000,
    "CT SCAN":  10000,
    "CT":       10000,
    "PET SCAN": 0,
    "PET":      0,
}


class PolicyCheckerAgent:
    """
    Fully deterministic policy checking.
    LLM only used for diagnosis → condition mapping (fuzzy text).
    All financial calculations done in code.
    """

    def __init__(self, claim_id: str):
        self.claim_id = claim_id
        self.client   = genai.Client(api_key=settings.gemini_api_key)

    async def run(self, claim: dict, documents: list[dict]) -> dict:
        start = time.time()

        try:
            # ── Fix 1: correct key names ──────────────────────────────────
            category       = claim.get("claim_category", "")
            claimed_amount = float(claim.get("claimed_amount", 0))
            treatment_date = date.fromisoformat(str(claim.get("treatment_date")))
            member_id      = claim.get("member_id")
            hospital_name  = claim.get("hospital_name") or ""

            # ── Fix 2: fetch join_date from DB, not claim dict ────────────
            member_row = (
                supabase.table("members")
                .select("join_date")
                .eq("member_id", member_id)
                .single()
                .execute()
            )
            if not member_row.data:
                raise ComponentFailureError("policy_checker", f"Member {member_id} not found")

            join_date = date.fromisoformat(member_row.data["join_date"])

            # ── Load all policy config ────────────────────────────────────
            policy            = load_policy()
            coverage          = get_coverage_for_category(category)
            waiting_periods   = get_waiting_periods()
            exclusions        = get_exclusions()
            network_hospitals = get_network_hospitals()
            submission_rules  = policy.get("submission_rules", {})
            fraud_thresholds  = policy.get("fraud_thresholds", {})
            per_claim_limit   = policy.get("coverage", {}).get("per_claim_limit", 5000)

            # ── Collect from extracted docs ───────────────────────────────
            all_extracted = [
                d.get("extracted_data", {})
                for d in documents
                if d.get("extracted_data")
            ]
            diagnoses  = self._collect_diagnoses(all_extracted)
            line_items = self._collect_line_items(all_extracted)

            # ── Build result object ───────────────────────────────────────
            result = {
                "eligible":                  True,
                "rejection_reasons":         [],
                "flags":                     [],
                "claim_category":            category,
                "claimed_amount":            claimed_amount,
                "approved_amount":           claimed_amount,
                "copay_percent":             coverage.get("copay_percent", 0),
                "copay_deducted":            0.0,
                "network_discount_applied":  0.0,
                "sub_limit":                 coverage.get("sub_limit", 0),
                "per_claim_limit":           per_claim_limit,
                "waiting_period_satisfied":  True,
                "exclusion_hit":             None,
                "requires_pre_auth":         False,
                "pre_auth_missing":          False,
                "line_item_decisions":       [],
                "notes":                     [],
            }

            # ── Check 1: Minimum claim amount ─────────────────────────────
            min_amount = submission_rules.get("minimum_claim_amount", 500)
            if claimed_amount < min_amount:
                result["eligible"] = False
                result["rejection_reasons"].append("BELOW_MINIMUM_AMOUNT")
                result["notes"].append(
                    f"Claimed ₹{claimed_amount} is below minimum ₹{min_amount}"
                )

            # ── Check 2: Initial waiting period (30 days) ─────────────────
            initial_days   = waiting_periods.get("initial_waiting_period_days", 30)
            days_since_join = (treatment_date - join_date).days

            if days_since_join < initial_days:
                eligible_from = join_date + timedelta(days=initial_days)
                result["eligible"] = False
                result["waiting_period_satisfied"] = False
                result["rejection_reasons"].append("WAITING_PERIOD")
                result["notes"].append(
                    f"Initial {initial_days}-day waiting period not satisfied. "
                    f"Member joined {join_date}. "
                    f"Eligible from {eligible_from}."
                )

            # ── Check 3: Condition-specific waiting periods ────────────────
            if diagnoses and result["waiting_period_satisfied"]:
                condition_msg = await self._check_condition_waiting_period(
                    diagnoses, waiting_periods, join_date, treatment_date
                )
                if condition_msg:
                    result["eligible"] = False
                    result["waiting_period_satisfied"] = False
                    result["rejection_reasons"].append("WAITING_PERIOD")
                    result["notes"].append(condition_msg)

            # ── Check 4: Exclusions ───────────────────────────────────────
            exclusion_hit = self._check_exclusions(
                diagnoses, line_items, exclusions, category
            )
            if exclusion_hit:
                result["eligible"] = False
                result["exclusion_hit"] = exclusion_hit
                result["rejection_reasons"].append("EXCLUDED_CONDITION")
                result["notes"].append(f"Excluded treatment: {exclusion_hit}")

            # ── Check 5: Pre-authorization (TC007) ───────────────────────
            pre_auth = self._check_pre_authorization(line_items, claimed_amount)
            if pre_auth["required"]:
                result["requires_pre_auth"] = True
                result["pre_auth_missing"]  = True
                result["eligible"]          = False
                result["rejection_reasons"].append("PRE_AUTH_MISSING")
                result["notes"].append(pre_auth["message"])

            # ── Check 6: Per-claim limit (TC008) ──────────────────────────
            if claimed_amount > per_claim_limit:
                result["eligible"] = False
                result["rejection_reasons"].append("PER_CLAIM_EXCEEDED")
                result["notes"].append(
                    f"Claimed ₹{claimed_amount} exceeds per-claim limit of ₹{per_claim_limit}"
                )

            # ── Check 7: Category sub-limit ───────────────────────────────
            sub_limit = coverage.get("sub_limit", float("inf"))
            if result["approved_amount"] > sub_limit:
                result["approved_amount"] = sub_limit
                result["flags"].append(f"SUB_LIMIT_APPLIED: capped at ₹{sub_limit}")
                result["notes"].append(f"Category sub-limit ₹{sub_limit} applied")

            # ── Check 8: Line-item decisions (TC006 dental) ───────────────
            if line_items:
                result["line_item_decisions"] = self._evaluate_line_items(
                    line_items, category, coverage, exclusions
                )
                approved_items = [
                    li for li in result["line_item_decisions"]
                    if li["status"] == "APPROVED"
                ]
                rejected_items = [
                    li for li in result["line_item_decisions"]
                    if li["status"] == "REJECTED"
                ]
                if approved_items:
                    result["approved_amount"] = sum(li["amount"] for li in approved_items)
                if rejected_items:
                    result["flags"].append("PARTIAL_LINE_ITEMS")
                    result["notes"].append(
                        f"Rejected line items: "
                        f"{[li['description'] for li in rejected_items]}"
                    )

            # ── Check 9: Network discount BEFORE co-pay (TC010) ───────────
            if result["eligible"]:
                is_network = any(
                    nh.lower() in hospital_name.lower()
                    for nh in network_hospitals
                ) if hospital_name else False

                if is_network:
                    discount_pct = coverage.get("network_discount_percent", 0)
                    if discount_pct:
                        discount                         = result["approved_amount"] * discount_pct / 100
                        result["approved_amount"]        -= discount
                        result["network_discount_applied"] = round(discount, 2)
                        result["notes"].append(
                            f"Network hospital discount {discount_pct}%: -₹{discount:.2f}"
                        )

            # ── Check 10: Co-pay AFTER network discount (TC010) ───────────
            if result["eligible"]:
                copay_pct = coverage.get("copay_percent", 0)
                if copay_pct:
                    copay                        = result["approved_amount"] * copay_pct / 100
                    result["approved_amount"]    -= copay
                    result["copay_deducted"]      = round(copay, 2)
                    result["copay_percent"]       = copay_pct
                    result["notes"].append(
                        f"Co-pay {copay_pct}%: -₹{copay:.2f}"
                    )

            result["approved_amount"] = round(result["approved_amount"], 2)

            # ── Check 11: High value flag ──────────────────────────────────
            auto_review_above = fraud_thresholds.get("auto_manual_review_above", 25000)
            if claimed_amount > auto_review_above:
                result["flags"].append(
                    f"HIGH_VALUE_CLAIM: ₹{claimed_amount} > ₹{auto_review_above}"
                )

            duration_ms = int((time.time() - start) * 1000)
            self._write_trace(
                status          = "passed",
                input_snapshot  = {
                    "claim_category":  category,
                    "claimed_amount":  claimed_amount,
                    "treatment_date":  str(treatment_date),
                    "join_date":       str(join_date),
                    "days_since_join": days_since_join,
                    "hospital_name":   hospital_name,
                    "diagnoses":       diagnoses,
                },
                output_snapshot = result,
                duration_ms     = duration_ms,
            )
            return result

        except ComponentFailureError:
            raise
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            self._write_trace(
                status          = "degraded",
                input_snapshot  = {"claim_category": claim.get("claim_category")},
                output_snapshot = None,
                error_message   = str(e),
                duration_ms     = duration_ms,
            )
            raise ComponentFailureError("policy_checker", str(e))

    # ── Helpers ───────────────────────────────────────────────────────────

    def _collect_diagnoses(self, extracted_list: list) -> list[str]:
        diagnoses = []
        for data in extracted_list:
            d = (
                data.get("diagnosis")
                or data.get("findings")
                or data.get("impression")
                or data.get("treatment")
            )
            if d:
                diagnoses.append(str(d).lower())
            for proc in data.get("procedures", []) or []:
                if isinstance(proc, dict):
                    name = proc.get("name", "")
                    if name:
                        diagnoses.append(name.lower())
        return diagnoses

    def _collect_line_items(self, extracted_list: list) -> list[dict]:
        items = []
        for data in extracted_list:
            for item in (
                data.get("items", [])
                or data.get("procedures", [])
                or []
            ):
                if isinstance(item, dict):
                    items.append(item)
        return items

    async def _check_condition_waiting_period(
        self,
        diagnoses:       list[str],
        waiting_periods: dict,
        join_date:       date,
        treatment_date:  date,
    ) -> str | None:
        specific     = waiting_periods.get("specific_conditions", {})
        days_elapsed = (treatment_date - join_date).days

        # LLM maps diagnosis text → condition keys
        prompt = f"""Map these diagnoses to insurance waiting period condition keys.

Diagnoses: {json.dumps(diagnoses)}

Available condition keys:
{json.dumps(list(specific.keys()))}

Return ONLY a JSON array of matched keys. Empty array if no match.
Do NOT guess. Only include clear matches.
Example: ["diabetes", "hypertension"]"""

        try:
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model    = MODEL,
                contents = prompt,
            )
            text = (getattr(response, "text", None) or str(response)).strip()
            text = text.replace("```json", "").replace("```", "").strip()
            matched = json.loads(text)
        except Exception:
            # LLM failed — fall back to keyword matching
            matched = self._keyword_condition_match(diagnoses)

        for condition in matched:
            required_days = specific.get(condition)
            if required_days and days_elapsed < required_days:
                eligible_from = join_date + timedelta(days=required_days)
                return (
                    f"Waiting period for {condition.replace('_', ' ').title()} "
                    f"is {required_days} days. "
                    f"Member joined {join_date}, treatment on {treatment_date} "
                    f"({days_elapsed} days elapsed). "
                    f"Eligible from {eligible_from}."
                )

        return None

    def _keyword_condition_match(self, diagnoses: list[str]) -> list[str]:
        full_text = " ".join(diagnoses)
        return [
            condition
            for condition, keywords in DIAGNOSIS_CONDITION_MAP.items()
            if any(kw in full_text for kw in keywords)
        ]

    def _check_exclusions(
        self,
        diagnoses:  list[str],
        line_items: list[dict],
        exclusions: dict,
        category:   str,
    ) -> str | None:
        all_text  = " ".join(diagnoses).lower()
        item_text = " ".join(
            item.get("description", "") for item in line_items
        ).lower()

        for excl in exclusions.get("conditions", []):
            if excl.lower() in all_text or excl.lower() in item_text:
                return excl

        if category == "DENTAL":
            for excl in exclusions.get("dental_exclusions", []):
                if excl.lower() in item_text:
                    return excl

        if category == "VISION":
            for excl in exclusions.get("vision_exclusions", []):
                if excl.lower() in all_text or excl.lower() in item_text:
                    return excl

        return None

    def _check_pre_authorization(
        self,
        line_items:     list[dict],
        claimed_amount: float,
    ) -> dict:
        for item in line_items:
            desc   = item.get("description", "").upper()
            amount = float(item.get("amount", claimed_amount))
            for trigger, threshold in PRE_AUTH_TRIGGERS.items():
                if trigger in desc and amount > threshold:
                    return {
                        "required": True,
                        "message": (
                            f"Pre-authorization required for {item.get('description')} "
                            f"(₹{amount:.0f} exceeds ₹{threshold} threshold). "
                            f"Please obtain pre-authorization before treatment and resubmit."
                        ),
                    }
        return {"required": False}

    def _evaluate_line_items(
        self,
        line_items: list[dict],
        category:   str,
        coverage:   dict,
        exclusions: dict,
    ) -> list[dict]:
        dental_covered  = [p.lower() for p in coverage.get("covered_procedures", [])]
        dental_excluded = [e.lower() for e in exclusions.get("dental_exclusions", [])]
        vision_excluded = [e.lower() for e in exclusions.get("vision_exclusions", [])]

        decisions = []
        for item in line_items:
            desc      = item.get("description", "")
            amount    = float(item.get("amount", 0))
            desc_low  = desc.lower()
            status    = "APPROVED"
            reason    = "Covered procedure"

            if category == "DENTAL":
                if any(excl in desc_low for excl in dental_excluded):
                    status = "REJECTED"
                    reason = "Cosmetic/excluded dental procedure"
                elif dental_covered and not any(proc in desc_low for proc in dental_covered):
                    status = "REJECTED"
                    reason = "Procedure not in covered dental procedures list"

            elif category == "VISION":
                if any(excl in desc_low for excl in vision_excluded):
                    status = "REJECTED"
                    reason = "Excluded vision procedure"

            decisions.append({
                "description": desc,
                "amount":      amount,
                "status":      status,
                "reason":      reason,
            })

        return decisions

    def _write_trace(self, status: str, input_snapshot: dict,
                     output_snapshot: dict, duration_ms: int,
                     error_message: str = None):
        supabase.table("trace_steps").insert({
            "claim_id":        self.claim_id,
            "step_name":       "policy_checker",
            "status":          status,
            "input_snapshot":  input_snapshot,
            "output_snapshot": output_snapshot,
            "error_message":   error_message,
            "duration_ms":     duration_ms,
            "created_at":      datetime.utcnow().isoformat(),
        }).execute()