#!/usr/bin/env python3
"""Simulated staff load (task P6-07): N people reading the app's main screens with think time between requests.

  uv run --project backend python deploy/scripts/loadtest.py --base https://localhost:8443 \
      --state frontend/e2e/.auth/morgan.state.json --staff 20 --seconds 300

The load uses read-only (GET) requests; setup may POST to select a business. The session cookie comes from a
Playwright storage state (a real login); session rotation is kept in memory only. Reports
p50/p95/p99 per endpoint and overall, and exits 1 when the overall p95 is above --p95-ms or any request fails.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx


def cookies_from_state(path: Path) -> dict[str, str]:
    state = json.loads(path.read_text(encoding="utf-8"))
    return {
        c["name"]: c["value"] for c in state["cookies"] if c["name"].startswith("smb_")
    }


def verify_tls(base: str, insecure: bool = False) -> bool:
    host = urlsplit(base).hostname or ""
    return not (
        insecure
        or host in {"localhost", "127.0.0.1", "::1"}
        or host.endswith(".localhost")
    )


def business_names(memberships: list[dict[str, Any]]) -> str:
    return ", ".join(m["business_name"] for m in memberships) or "(none)"


def match_business(memberships: list[dict[str, Any]], choice: str) -> dict[str, Any]:
    for membership in memberships:
        if membership["business_id"].casefold() == choice.casefold():
            return membership
    exact = [
        m for m in memberships if m["business_name"].casefold() == choice.casefold()
    ]
    matches = exact or [
        m for m in memberships if choice.casefold() in m["business_name"].casefold()
    ]
    if len(matches) == 1:
        return matches[0]
    reason = (
        "Business selection is ambiguous"
        if matches
        else "No membership matches --business"
    )
    raise SystemExit(f"{reason}. Available businesses: {business_names(memberships)}")


def require_ok(response: httpx.Response, endpoint: str) -> None:
    if response.status_code == 200:
        return
    try:
        body = response.json()
    except ValueError:
        body = None
    code = body.get("code") if isinstance(body, dict) else None
    detail = f" ({code})" if code else ""
    raise SystemExit(f"{endpoint}: HTTP {response.status_code}{detail}")


def prepare_session(client: httpx.Client, business: str | None) -> None:
    response = client.get("/api/v1/auth/me")
    if response.status_code == 401:
        raise SystemExit("the session in --state is not signed in or has expired")
    require_ok(response, "/api/v1/auth/me")
    me = response.json()
    memberships = me["memberships"]
    active = me["active_business_id"]
    if business is not None:
        selected = match_business(memberships, business)
    elif active is not None:
        selected = next((m for m in memberships if m["business_id"] == active), None)
        if selected is None:
            raise SystemExit(
                "The active business is not in memberships; pass --business. "
                f"Available businesses: {business_names(memberships)}"
            )
    elif len(memberships) == 1:
        selected = memberships[0]
        print(
            f"Automatically selecting the only membership: {selected['business_name']}"
        )
    else:
        raise SystemExit(
            "The session has no active business. "
            f"Available businesses: {business_names(memberships)}. "
            "Pass --business NAME_OR_ID or pick a business in the browser before saving the state."
        )
    if selected["business_id"] != active:
        response = client.post(
            "/api/v1/auth/active-business",
            json={"business_id": selected["business_id"]},
            headers={"X-CSRF-Token": me["csrf_token"]},
        )
        require_ok(response, "/api/v1/auth/active-business")
        rotated = response.cookies.get("smb_session")
        if not rotated:
            raise SystemExit(
                "/api/v1/auth/active-business did not return a new session cookie"
            )
        # State cookies were loaded without domains. HTTPX adds the response cookie with a domain,
        # so remove both variants before setting the new value for this single-host probe.
        client.cookies.delete("smb_session")
        client.cookies.set("smb_session", rotated)
    print(f"Business: {selected['business_name']}")


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))] if ordered else 0.0


class Run:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.timings: dict[str, list[float]] = {}
        self.errors: list[str] = []

    def record(self, name: str, ms: float, ok: bool, detail: str = "") -> None:
        with self.lock:
            self.timings.setdefault(name, []).append(ms)
            if not ok:
                self.errors.append(f"{name}: {detail}")


def discover(client: httpx.Client) -> list[tuple[str, str]]:
    """The endpoints a staff member's screens call, with ids taken from the signed-in business's own data."""
    response = client.get("/api/v1/crm/customers", params={"limit": 5})
    require_ok(response, "/api/v1/crm/customers")
    customers = response.json()["items"]
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    window = {
        "start": day_start.isoformat(),
        "end": (day_start + timedelta(days=7)).isoformat(),
    }
    items: list[tuple[str, str]] = [
        ("me", "/api/v1/auth/me"),
        ("dashboard.today", "/api/v1/dashboard/today"),
        ("dashboard.counters", "/api/v1/dashboard/counters"),
        ("crm.list", "/api/v1/crm/customers?limit=50"),
        ("catalog.items", "/api/v1/catalog/items?limit=50"),
        ("inventory.levels", "/api/v1/inventory/levels"),
        ("inventory.suggestions", "/api/v1/inventory/reorder-suggestions"),
        (
            "scheduling.appointments",
            "/api/v1/scheduling/appointments?"
            + "&".join(f"{k}={v}" for k, v in window.items()).replace("+", "%2B"),
        ),
        ("conversations.list", "/api/v1/conversations?status=waiting_on_staff"),
        ("approvals.list", "/api/v1/approvals/proposals?limit=50"),
        ("marketing.campaigns", "/api/v1/marketing/campaigns"),
        ("admin.system", "/api/v1/admin/system"),
    ]
    for c in customers[:3]:
        items.append(("crm.detail", f"/api/v1/crm/customers/{c['id']}"))
        items.append(("crm.timeline", f"/api/v1/crm/customers/{c['id']}/timeline"))
    return items


def staff_member(
    base: str,
    cookies: dict[str, str],
    endpoints: list[tuple[str, str]],
    stop_at: float,
    run: Run,
    seed: int,
    verify: bool = True,
) -> None:
    rnd = random.Random(seed)
    with httpx.Client(
        base_url=base, cookies=cookies, verify=verify, timeout=30
    ) as client:
        while time.monotonic() < stop_at:
            name, path = rnd.choice(endpoints)
            started = time.perf_counter()
            try:
                response = client.get(path)
                ok = response.status_code == 200
                detail = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                ok, detail = False, type(exc).__name__
            run.record(name, (time.perf_counter() - started) * 1000, ok, detail)
            time.sleep(
                rnd.uniform(0.5, 1.5)
            )  # a person reads the screen before the next click


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="https://localhost:8443")
    parser.add_argument(
        "--state",
        required=True,
        type=Path,
        help="Playwright storage state of a signed-in owner or admin",
    )
    parser.add_argument(
        "--business",
        metavar="NAME_OR_ID",
        help="Membership business id or name (unique substring allowed)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification (automatic for localhost)",
    )
    parser.add_argument("--staff", type=int, default=20)
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--p95-ms", type=float, default=300.0)
    parser.add_argument("--out", type=Path, default=Path("backups/loadtest-last.json"))
    args = parser.parse_args()

    cookies = cookies_from_state(args.state)
    verify = verify_tls(args.base, args.insecure)
    with httpx.Client(
        base_url=args.base, cookies=cookies, verify=verify, timeout=30
    ) as probe:
        prepare_session(probe, args.business)
        endpoints = discover(probe)
        cookies = dict(probe.cookies)
    run = Run()
    stop_at = time.monotonic() + args.seconds
    started = datetime.now(UTC)
    threads = [
        threading.Thread(
            target=staff_member,
            args=(args.base, cookies, endpoints, stop_at, run, i, verify),
        )
        for i in range(args.staff)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    every = [v for values in run.timings.values() for v in values]
    per_endpoint: dict[str, Any] = {
        name: {
            "n": len(v),
            "p50_ms": round(statistics.median(v), 1),
            "p95_ms": round(percentile(v, 0.95), 1),
            "p99_ms": round(percentile(v, 0.99), 1),
            "max_ms": round(max(v), 1),
        }
        for name, v in sorted(run.timings.items())
    }
    overall = {
        "requests": len(every),
        "errors": len(run.errors),
        "p50_ms": round(statistics.median(every), 1),
        "p95_ms": round(percentile(every, 0.95), 1),
        "p99_ms": round(percentile(every, 0.99), 1),
        "requests_per_second": round(len(every) / args.seconds, 1),
    }
    passed = overall["errors"] == 0 and overall["p95_ms"] < args.p95_ms
    result = {
        "started": started.isoformat(),
        "staff": args.staff,
        "seconds": args.seconds,
        "target_p95_ms": args.p95_ms,
        "overall": overall,
        "per_endpoint": per_endpoint,
        "error_sample": run.errors[:10],
        "result": "pass" if passed else "FAIL",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: result[k] for k in ("staff", "seconds", "overall", "result")}, indent=2
        )
    )
    for name, row in per_endpoint.items():
        print(
            f"  {name:26} n={row['n']:5} p50={row['p50_ms']:7} p95={row['p95_ms']:7} p99={row['p99_ms']:7} ms"
        )
    for line in run.errors[:5]:
        print("  ERROR", line)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
