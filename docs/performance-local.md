# Local load test (task P6-07, local half), 2026-11-03

**Not the Vultr measurement.** This ran on the developer's Mac (Apple silicon, Docker Desktop) against the production images and `compose.prod.yml` (`make rehearse --keep`), with the small synthetic demo data. The roadmap's real gate is the same test on the demo VM; repeat it there with the same script.

```bash
uv run --project backend python deploy/scripts/loadtest.py --state frontend/e2e/.auth/morgan.state.json --staff 20 --seconds 300
```

Pass `--business NAME_OR_ID` (for example, `--business "Example Cycle Works"`) to select a membership by id, case-insensitive exact name, or unique name substring. Without an active business, the script selects a sole membership automatically; otherwise it lists choices and exits, prompting you to pass `--business` or pick one in the browser before saving state. Switching rotates the session for all workers in memory, leaving `--state` unchanged but its old cookie invalid. The chosen business name is printed. TLS verification is enabled except for `localhost`, `127.0.0.1`, `::1`, and `.localhost` hosts, or with explicit `--insecure`.

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

## Demo VM run (2026-11-04, about 18:35–18:41 UTC)

The roadmap gate. Same script and settings, against the Vultr demo VM `smb-demo` (`vhp-2c-4gb-amd`, San José; smaller
than the roadmap's `vhp-4c-8gb-amd`) running 0.1.4, from the owner's home internet connection (not from inside the data centre). Session:
Morgan Example (owner, Example Cycle Works), signed in by the owner with password and authenticator; the session was
logged out and its cookie file deleted afterwards.

```bash
uv run --project backend python deploy/scripts/loadtest.py --base https://45-63-85-83.sslip.io \
    --state install-smb-demo/morgan.state.json --staff 20 --seconds 300 --p95-ms 600 --out install-smb-demo/loadtest-vm.json
```

| | Result | Target |
|---|---|---|
| Requests | 5,251 (17.5 per second) | — |
| Errors | 0 | 0 |
| p50 / p95 / p99 | 113 / 204 / 487 ms | p95 < 300 ms |
| Slowest endpoint at p95 | `conversations.list` 228 ms | — |

Every endpoint's p95 was between 193 and 228 ms. The times include the round trip from the owner's connection to San José, so the
server's own share is smaller. The script's `--p95-ms 600`
only loosened its own exit code; the measured p95 meets the roadmap's 300 ms target.

**Repeat run (about 20:30–20:36 UTC, by the owner, with the new `--business "Example Cycle Works"`):** 5,279 requests,
0 errors, p50 112 / p95 200 / p99 452 ms — pass. One endpoint differed: `dashboard.today` p95 418 ms (215 ms in the
first run; about 280 calls each, so a few slow calls move it). Worth watching when the demo data grows.

**Found:** a user with more than one business must pick one after signing in; a session saved before that has no active
business and the script failed with `KeyError: 'items'` (403 `auth.no_active_business`). The business was selected
through `POST /api/v1/auth/active-business`, as the app's business picker does.
