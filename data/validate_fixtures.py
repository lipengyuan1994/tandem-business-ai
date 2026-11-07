"""Validate the synthetic dataset (data/fixtures.json) against the data contract (data/schema.sql).

Standard library only. Uses an in-memory SQLite database; touches no files and no network.
Run from the repository root:  python3 -I data/validate_fixtures.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROLES = {"owner", "admin", "demo_reviewer", "operator", "inventory_manager", "marketing_manager", "auditor"}
FICTIONAL_PHONE = re.compile(r"^(\+1\d{3}5550[1]\d{2}|\+447700900\d{3})$")  # 555-01xx (US) and Ofcom drama range (UK)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def parse_utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=ZoneInfo("UTC"))


def load(db: sqlite3.Connection, tables: dict[str, list[dict]]) -> None:
    for table, rows in tables.items():
        for row in rows:
            cols = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            try:
                db.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(row.values()))
            except sqlite3.Error as exc:
                fail(f"{table} row {row.get('id', row)}: {exc}")


def check_synthetic(doc: dict, t: dict[str, list[dict]]) -> None:
    if doc.get("synthetic") is not True:
        fail("top-level 'synthetic' must be true")
    for u in t["users"]:
        if not u["email"].endswith("@example.com"):
            fail(f"user email not example.com: {u['id']}")
    for cp in t["contact_points"]:
        if cp["type"] == "email" and not cp["value"].endswith("@example.com"):
            fail(f"contact email not example.com: {cp['id']}")
        if cp["type"] == "phone" and not FICTIONAL_PHONE.match(cp["value"]):
            fail(f"phone not in a fictional range: {cp['id']}")
    for supplier in t.get("suppliers", []):
        if not supplier["email"].endswith("@example.com"):
            fail("supplier email must be example.com")
    for c in t["conversations"]:
        if c.get("visitor_email") and not c["visitor_email"].endswith("@example.com"):
            fail(f"visitor email not example.com: {c['id']}")


def check_dental(t: dict[str, list[dict]]) -> None:
    clinic = next((b for b in t["businesses"] if b["slug"] == "example-dental-clinic"), None)
    if not clinic or not clinic.get("synthetic") or not clinic.get("synthetic_demo"):
        fail("dental business must be explicitly synthetic and synthetic_demo")
    settings = json.loads(clinic["settings_json"])
    if not settings.get("emergency_handling") or not settings.get("emergency_instructions"):
        fail("dental emergency handling and instructions required")
    if not json.loads(clinic["public_profile_json"]).get("avoid_medical_details"):
        fail("dental templates must avoid medical details")
    services = [i for i in t["catalog_items"] if i["business_id"] == clinic["id"] and i["kind"] == "service"]
    if len(services) != 4:
        fail("four dental services required")
    for service in services:
        if json.loads(service["required_resources_json"]) != [{"role": "provider", "kind": "staff"}, {"role": "chair", "kind": "room"}]:
            fail("dental services require provider and chair")
    if next(i for i in services if i["name"] == "Exam and cleaning")["recall_interval_months"] != 6:
        fail("cleaning recall must be six months")
    resources = [r for r in t["resources"] if r["business_id"] == clinic["id"]]
    if sum(r["kind"] == "staff" for r in resources) != 2 or sum(r["kind"] == "room" for r in resources) != 3:
        fail("clinic requires two providers and three chairs")


def check_businesses(t: dict[str, list[dict]]) -> None:
    zones = set()
    for b in t["businesses"]:
        ZoneInfo(b["timezone"])  # raises if invalid
        zones.add(b["timezone"])
    if len(t["businesses"]) < 2 or len(zones) < 2:
        fail("need at least two businesses in different time zones")
    roles_by_business: dict[str, set[str]] = defaultdict(set)
    for g in t["role_grants"]:
        roles_by_business[g["business_id"]].add(g["role"])
    if roles_by_business["biz-cycle"] != ROLES:
        fail(f"demo business must have every role, has {sorted(roles_by_business['biz-cycle'])}")
    multi = defaultdict(set)
    for m in t["memberships"]:
        multi[m["user_id"]].add(m["business_id"])
    if not any(len(v) > 1 for v in multi.values()):
        fail("at least one user must belong to two businesses (business switcher test)")


def check_appointments(t: dict[str, list[dict]]) -> None:
    items = {(i["business_id"], i["id"]): i for i in t["catalog_items"]}
    busy = defaultdict(list)
    for a in t["appointments"]:
        svc = items[(a["business_id"], a["service_id"])]
        start, end, blocked = parse_utc(a["starts_at"]), parse_utc(a["ends_at"]), parse_utc(a["blocked_until"])
        if end - start != timedelta(minutes=svc["duration_minutes"]):
            fail(f"{a['id']}: ends_at does not match service duration")
        if blocked - end != timedelta(minutes=svc["buffer_minutes"]):
            fail(f"{a['id']}: blocked_until does not match service buffer")
        if a["status"] in ("held", "confirmed"):
            for other_start, other_end, other_id in busy[(a["business_id"], a["resource_id"])]:
                if start < other_end and other_start < blocked:
                    fail(f"{a['id']} overlaps {other_id} (the database exclusion constraint would reject it)")
            busy[(a["business_id"], a["resource_id"])].append((start, blocked, a["id"]))


def earliest_slot(t: dict[str, list[dict]], business: str, service: str, local_day: datetime, until_local: str) -> tuple[str, str] | None:
    """Tiny reference version of scheduling.find_slots for one day (no exceptions, granularity 15)."""
    tz = local_day.tzinfo
    svc = next(i for i in t["catalog_items"] if i["business_id"] == business and i["id"] == service)
    need = timedelta(minutes=svc["duration_minutes"] + svc["buffer_minutes"])
    weekday = local_day.weekday()
    hours = [h for h in t["opening_hours"] if h["business_id"] == business and h["weekday"] == weekday]
    candidates = []
    for r in (r for r in t["resources"] if r["business_id"] == business and r["kind"] == svc["resource_kind"]):
        rules = [x for x in t["availability_rules"] if x["resource_id"] == r["id"] and x["weekday"] == weekday]
        busy = [(parse_utc(a["starts_at"]), parse_utc(a["blocked_until"])) for a in t["appointments"]
                if a["resource_id"] == r["id"] and a["status"] in ("held", "confirmed")]
        for h in hours:
            for rule in rules:
                lo = max(h["opens"], rule["start_time"])
                hi = min(h["closes"], rule["end_time"])
                cur = datetime.combine(local_day.date(), datetime.strptime(lo, "%H:%M").time(), tz)
                end = datetime.combine(local_day.date(), datetime.strptime(hi, "%H:%M").time(), tz)
                limit = datetime.combine(local_day.date(), datetime.strptime(until_local, "%H:%M").time(), tz)
                while cur + need <= end and cur < limit:
                    if all(not (cur < b_end and b_start < cur + need) for b_start, b_end in busy):
                        candidates.append((cur, r["id"]))
                        break
                    cur += timedelta(minutes=15)
    if not candidates:
        return None
    first = min(candidates)
    return first[0].strftime("%Y-%m-%dT%H:%M"), first[1]


def stock_levels(t: dict[str, list[dict]]) -> dict[tuple[str, str], tuple[float, float]]:
    on_hand: dict[tuple[str, str], float] = defaultdict(float)
    reserved: dict[tuple[str, str], float] = defaultdict(float)
    for m in t["stock_movements"]:
        on_hand[(m["business_id"], m["item_id"])] += m["quantity"]
    for r in t["stock_reservations"]:
        if r["status"] == "active":
            reserved[(r["business_id"], r["item_id"])] += r["quantity"]
    keys = set(on_hand) | set(reserved)
    return {k: (on_hand[k], reserved[k]) for k in keys}


def check_inventory(doc: dict, t: dict[str, list[dict]]) -> None:
    levels = stock_levels(t)
    for (biz, item), (oh, rs) in levels.items():
        if oh < 0 or oh - rs < 0:
            fail(f"{item}: on hand {oh}, reserved {rs} — negative stock or availability")
    rules = {(r["business_id"], r["item_id"]): r for r in t["reorder_rules"]}
    exp = doc["expected"]
    oh, rs = levels[("biz-cycle", "item-brake-pads")]
    if oh - rs != exp["brake_pads_available_before_ada_booking"]:
        fail(f"brake pads available is {oh - rs}, expected {exp['brake_pads_available_before_ada_booking']}")
    point = rules[("biz-cycle", "item-brake-pads")]["reorder_point"]
    if not (oh - rs >= point and oh - rs - 1 < point):
        fail("brake pads must cross the reorder point after exactly one more reservation")
    oh, rs = levels[("biz-cycle", "item-cable-set")]
    if not (oh - rs < rules[("biz-cycle", "item-cable-set")]["reorder_point"]):
        fail("cable set should be below its reorder point (low-stock demo)")


def check_marketing(doc: dict, t: dict[str, list[dict]]) -> None:
    latest: dict[str, dict] = {}
    for c in sorted(t["consent_records"], key=lambda r: r["recorded_at"]):
        if c["channel"] == "email" and c["purpose"] == "marketing":
            latest[c["customer_id"]] = c
    suppressed = {s["value_normalized"] for s in t["suppressions"] if s["channel"] == "email"}
    emails = {cp["customer_id"]: cp["value"].lower() for cp in t["contact_points"] if cp["type"] == "email" and cp["is_primary"]}
    tagged = {ct["customer_id"] for ct in t["customer_tags"] if ct["tag_id"] == "tag-road"}
    eligible = sorted(c for c in tagged if latest.get(c, {}).get("status") == "granted" and c in emails and emails[c] not in suppressed)
    if eligible != sorted(doc["expected"]["winter_campaign_eligible"]):
        fail(f"campaign eligibility {eligible} differs from expected {doc['expected']['winter_campaign_eligible']}")


def check_proposals(t: dict[str, list[dict]]) -> None:
    for rev in t["proposal_revisions"]:
        payload = json.loads(rev["payload_json"])
        if "business_id" in payload:
            fail("proposal payloads must not carry business_id (the server supplies it)")


def main() -> None:
    doc = json.loads((HERE / "fixtures.json").read_text(encoding="utf-8"))
    t = doc["tables"]
    db = sqlite3.connect(":memory:")
    db.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
    load(db, t)
    problems = db.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        fail(f"foreign key problems: {problems}")
    check_synthetic(doc, t)
    check_dental(t)
    check_businesses(t)
    check_appointments(t)
    slot = earliest_slot(t, "biz-cycle", "svc-brake", datetime(2026, 11, 10, tzinfo=ZoneInfo("America/Denver")), "12:00")
    if slot != ("2026-11-10T11:45", "res-jordan"):
        fail(f"earliest Tuesday-morning brake slot is {slot}, expected 11:45 with res-jordan")
    check_inventory(doc, t)
    check_marketing(doc, t)
    check_proposals(t)
    counts = ", ".join(f"{k}={len(v)}" for k, v in t.items())
    print(f"OK — {sum(len(v) for v in t.values())} rows in {len(t)} tables ({counts})")


if __name__ == "__main__":
    main()
