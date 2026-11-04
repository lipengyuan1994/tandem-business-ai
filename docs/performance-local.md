# Local load test (task P6-07, local half), 2026-11-03

**Not the Vultr measurement.** This ran on the developer's Mac (Apple silicon, Docker Desktop) against the production images and `compose.prod.yml` (`make rehearse --keep`), with the small synthetic demo data. The roadmap's real gate is the same test on the demo VM; repeat it there with the same script.

```bash
uv run --project backend python deploy/scripts/loadtest.py --state frontend/e2e/.auth/morgan.state.json --staff 20 --seconds 300
```

20 simulated staff, each reading one of the main screens' endpoints every 0.5–1.5 s for 5 minutes (read-only requests, a real signed-in owner session, the API behind Caddy over HTTPS).

| | Result | Target |
|---|---|---|
| Requests | 5853 (19.5 per second) | |
| Errors | 0 | 0 |
| p50 / p95 / p99 | 13.5 / 26.0 / 210.3 ms | p95 < 300 ms |
| Verdict | **pass** | |

| Endpoint | Requests | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|
| `admin.system` | 316 | 13.9 | 23.7 | 37.8 |
| `approvals.list` | 320 | 12.1 | 27.5 | 91.9 |
| `catalog.items` | 348 | 12.1 | 23.0 | 31.6 |
| `conversations.list` | 295 | 13.9 | 25.2 | 285.3 |
| `crm.detail` | 1016 | 13.9 | 26.2 | 217.7 |
| `crm.list` | 310 | 14.1 | 25.2 | 45.4 |
| `crm.timeline` | 959 | 12.3 | 24.2 | 193.1 |
| `dashboard.counters` | 347 | 12.7 | 22.4 | 42.6 |
| `dashboard.today` | 317 | 20.7 | 39.3 | 306.4 |
| `inventory.levels` | 313 | 14.2 | 25.6 | 180.4 |
| `inventory.suggestions` | 346 | 15.0 | 26.6 | 188.0 |
| `marketing.campaigns` | 311 | 12.2 | 25.2 | 78.8 |
| `me` | 342 | 13.0 | 23.3 | 423.0 |
| `scheduling.appointments` | 313 | 13.0 | 24.9 | 167.4 |

**Limits of this result:** tiny data (a few dozen rows per table), no writes, no AI calls, no large lists, Docker Desktop's network. The slowest requests (up to about 0.4 s) were not investigated. Expect the VM numbers to differ; the target is the one in the roadmap (`vhp-4c-8gb-amd`).
