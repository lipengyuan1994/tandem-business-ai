#!/usr/bin/env python3
"""Simulated staff load (task P6-07): N people reading the app's main screens with think time between requests.

  uv run --project backend python deploy/scripts/loadtest.py --base https://localhost:8443 \
      --state frontend/e2e/.auth/morgan.state.json --staff 20 --seconds 300

Read-only (GET) requests only. The session cookie comes from a Playwright storage state (a real login). Reports
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

import httpx


def cookies_from_state(path: Path) -> dict[str, str]:
    state = json.loads(path.read_text(encoding="utf-8"))
    return {c["name"]: c["value"] for c in state["cookies"] if c["name"].startswith("smb_")}


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
    customers = client.get("/api/v1/crm/customers", params={"limit": 5}).json()["items"]
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    window = {"start": day_start.isoformat(), "end": (day_start + timedelta(days=7)).isoformat()}
    items: list[tuple[str, str]] = [
        ("me", "/api/v1/auth/me"),
        ("dashboard.today", "/api/v1/dashboard/today"),
        ("dashboard.counters", "/api/v1/dashboard/counters"),
        ("crm.list", "/api/v1/crm/customers?limit=50"),
        ("catalog.items", "/api/v1/catalog/items?limit=50"),
        ("inventory.levels", "/api/v1/inventory/levels"),
        ("inventory.suggestions", "/api/v1/inventory/reorder-suggestions"),
        ("scheduling.appointments", "/api/v1/scheduling/appointments?" + "&".join(f"{k}={v}" for k, v in window.items()).replace("+", "%2B")),
        ("conversations.list", "/api/v1/conversations?status=waiting_on_staff"),
        ("approvals.list", "/api/v1/approvals/proposals?limit=50"),
        ("marketing.campaigns", "/api/v1/marketing/campaigns"),
        ("admin.system", "/api/v1/admin/system"),
    ]
    for c in customers[:3]:
        items.append(("crm.detail", f"/api/v1/crm/customers/{c['id']}"))
        items.append(("crm.timeline", f"/api/v1/crm/customers/{c['id']}/timeline"))
    return items


def staff_member(base: str, cookies: dict[str, str], endpoints: list[tuple[str, str]], stop_at: float, run: Run, seed: int) -> None:
    rnd = random.Random(seed)
    with httpx.Client(base_url=base, cookies=cookies, verify=False, timeout=30) as client:  # noqa: S501 - local CA
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
            time.sleep(rnd.uniform(0.5, 1.5))  # a person reads the screen before the next click


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="https://localhost:8443")
    parser.add_argument("--state", required=True, type=Path, help="Playwright storage state of a signed-in owner or admin")
    parser.add_argument("--staff", type=int, default=20)
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--p95-ms", type=float, default=300.0)
    parser.add_argument("--out", type=Path, default=Path("backups/loadtest-last.json"))
    args = parser.parse_args()

    cookies = cookies_from_state(args.state)
    with httpx.Client(base_url=args.base, cookies=cookies, verify=False, timeout=30) as probe:  # noqa: S501
        if probe.get("/api/v1/auth/me").status_code != 200:
            raise SystemExit("the session in --state is not signed in")
        endpoints = discover(probe)
    run = Run()
    stop_at = time.monotonic() + args.seconds
    started = datetime.now(UTC)
    threads = [threading.Thread(target=staff_member, args=(args.base, cookies, endpoints, stop_at, run, i)) for i in range(args.staff)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    every = [v for values in run.timings.values() for v in values]
    per_endpoint: dict[str, Any] = {
        name: {"n": len(v), "p50_ms": round(statistics.median(v), 1), "p95_ms": round(percentile(v, 0.95), 1), "p99_ms": round(percentile(v, 0.99), 1), "max_ms": round(max(v), 1)}
        for name, v in sorted(run.timings.items())
    }
    overall = {"requests": len(every), "errors": len(run.errors), "p50_ms": round(statistics.median(every), 1), "p95_ms": round(percentile(every, 0.95), 1),
               "p99_ms": round(percentile(every, 0.99), 1), "requests_per_second": round(len(every) / args.seconds, 1)}
    passed = overall["errors"] == 0 and overall["p95_ms"] < args.p95_ms
    result = {"started": started.isoformat(), "staff": args.staff, "seconds": args.seconds, "target_p95_ms": args.p95_ms, "overall": overall,
              "per_endpoint": per_endpoint, "error_sample": run.errors[:10], "result": "pass" if passed else "FAIL"}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("staff", "seconds", "overall", "result")}, indent=2))
    for name, row in per_endpoint.items():
        print(f"  {name:26} n={row['n']:5} p50={row['p50_ms']:7} p95={row['p95_ms']:7} p99={row['p99_ms']:7} ms")
    for line in run.errors[:5]:
        print("  ERROR", line)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
