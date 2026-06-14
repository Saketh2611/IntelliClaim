import json
import os
import sys

from .client import supabase


def load_policy_terms(path: str = "policy_terms.json") -> dict:
    if not os.path.exists(path):
        print(f"ERROR: {path} not found")
        sys.exit(1)
    with open(path, "r") as f:
        return json.load(f)


def map_member(raw: dict, policy_id: str, policy_start: str) -> dict:
    """
    Map a raw member from policy_terms.json → members table row.
    Dependents don't have join_date in the JSON,
    so we fall back to policy start date.
    """
    return {
        "member_id":         raw.get("member_id"),
        "name":              raw.get("name"),
        "date_of_birth":     raw.get("date_of_birth"),
        "gender":            raw.get("gender"),
        "relationship":      raw.get("relationship"),
        "join_date":         raw.get("join_date") or policy_start,  # dependents fallback
        "primary_member_id": raw.get("primary_member_id"),           # null for SELF
        "policy_id":         policy_id,
        "dependents":        raw.get("dependents") or [],
    }


def validate_member(member: dict, index: int) -> bool:
    required = [
        "member_id",
        "name",
        "date_of_birth",
        "gender",
        "relationship",
        "join_date",
        "policy_id",
    ]
    missing = [k for k in required if not member.get(k)]
    if missing:
        print(f"  WARNING: member[{index}] ({member.get('member_id', '?')}) skipped — missing: {missing}")
        return False
    return True


def seed_members(data: dict):
    raw_members = data.get("members") or []

    if not raw_members:
        print("ERROR: No 'members' array found in policy_terms.json")
        print(f"       Found top-level keys: {list(data.keys())}")
        sys.exit(1)

    # pull top-level policy context
    policy_id    = data.get("policy_id", "PLUM_GHI_2024")
    policy_start = data.get("policy_holder", {}).get("policy_start_date", "2024-04-01")

    print(f"Policy ID    : {policy_id}")
    print(f"Policy start : {policy_start}")
    print(f"Members found: {len(raw_members)}\n")

    mapped, skipped = [], 0
    for i, raw in enumerate(raw_members):
        member = map_member(raw, policy_id, policy_start)
        if validate_member(member, i):
            mapped.append(member)
        else:
            skipped += 1

    if not mapped:
        print("ERROR: No valid members to insert")
        sys.exit(1)

    print(f"Inserting {len(mapped)} members ({skipped} skipped)...")

    response = (
        supabase.table("members")
        .upsert(mapped, on_conflict="member_id")
        .execute()
    )

    print("Seeded successfully.\n")


def verify_seed():
    response = supabase.table("members").select("member_id, name, relationship, join_date").execute()
    rows  = response.data or []
    count = len(rows)

    print(f"Verification: {count} members in DB\n")

    # show SELF members first, then dependents
    self_members = [r for r in rows if r["relationship"] == "SELF"]
    dependents   = [r for r in rows if r["relationship"] != "SELF"]

    print(f"  Employees ({len(self_members)}):")
    for r in self_members:
        print(f"    {r['member_id']} — {r['name']} (joined {r['join_date']})")

    print(f"\n  Dependents ({len(dependents)}):")
    for r in dependents:
        print(f"    {r['member_id']} — {r['name']} ({r['relationship']})")


def run(path: str = "policy_terms.json"):
    print("=== Plum Claims — DB Seed ===\n")
    data = load_policy_terms(path)
    seed_members(data)
    verify_seed()
    print("\nDone.")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "policy_terms.json"
    run(path)