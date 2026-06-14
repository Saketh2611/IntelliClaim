import json
from functools import lru_cache
from core.config import settings
from core.exceptions import PolicyLoadError


@lru_cache(maxsize=1)  # load once, cache forever
def load_policy() -> dict:
    try:
        with open(settings.policy_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        raise PolicyLoadError(f"policy_terms.json not found at {settings.policy_path}")
    except json.JSONDecodeError as e:
        raise PolicyLoadError(f"Invalid JSON in policy file: {e}")


def get_document_requirements(claim_category: str) -> dict:
    policy = load_policy()
    return policy["document_requirements"].get(claim_category, {})


def get_coverage_for_category(claim_category: str) -> dict:
    policy = load_policy()
    category_map = {
        "CONSULTATION":         "consultation",
        "PHARMACY":             "pharmacy",
        "DENTAL":               "dental",
        "DIAGNOSTIC":           "diagnostic",
        "VISION":               "vision",
        "ALTERNATIVE_MEDICINE": "alternative_medicine",
    }
    key = category_map.get(claim_category)
    return policy["opd_categories"].get(key, {})


def get_waiting_periods() -> dict:
    return load_policy()["waiting_periods"]


def get_exclusions() -> dict:
    return load_policy()["exclusions"]


def get_network_hospitals() -> list:
    return load_policy()["network_hospitals"]


def get_fraud_thresholds() -> dict:
    return load_policy()["fraud_thresholds"]