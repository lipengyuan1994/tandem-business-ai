# Developing on a Claude cloud machine

How to build, run and test Tandem Business AI on one of Claude's cloud development machines (a root Linux container with
Docker installed but not running, about 4 CPUs and 15 GB of memory, no GPU). It also works on any root Linux box with
Docker, `uv`, `pnpm` and Python 3.13.

## One command

```bash
make cloud-dev                     # same as deploy/scripts/cloud-dev-setup.sh
make cloud-dev args=--deps-only    # only dependencies and the test database (enough for make test)
make cloud-dev args=--no-seed      # full stack without the synthetic businesses
```

It is safe to run again. It:

1. starts the Docker daemon if it is down, with `mirror.gcr.io` as registry mirror;
2. pulls the stack's images, taking Keycloak and `uv` from Docker Hub and retagging them (see below);
3. creates `.env` from `.env.example` if missing (dummy values only);
4. installs backend (`uv sync --frozen`) and frontend (`pnpm install --frozen-lockfile`) dependencies on the host;
5. starts the full dev stack (`postgres`, `keycloak`, `api`, `worker`, `web`, `caddy`, `db-test`) and waits for
   `/api/v1/readyz`;
6. loads the three synthetic businesses (`app.cli seed`), skipping this if they are already there.

Then: app at `https://localhost:8443` (self-signed certificate), `make demo-logins` for staff passwords,
`make backend-test`, `make frontend-test`, `make check`, `make dev-logs`, `make dev-down`.

AI features use the scripted `fake` provider from `deploy/config/ai-routes.dev.yaml`, so no API keys are needed.

## What is different on the cloud machine

| Problem | Workaround |
|---|---|
| The default `node` is 22; the frontend targets Node 24, and 24 dictation tests fail on 22 | Run `export PATH=/opt/node24/bin:$PATH` in your shell before frontend commands. The script does this for its own steps and reminds you. |
| Docker isn't running | The script starts `dockerd` (needs root). Its log is `/var/log/dockerd.log`. |
| Docker Hub answers 429 to anonymous pulls | `/etc/docker/daemon.json` gets `"registry-mirrors": ["https://mirror.gcr.io"]`. |
| `quay.io` and `ghcr.io` downloads are blocked | `keycloak/keycloak:26.8.1` and `astral/uv:0.12` come from Docker Hub and are tagged as `quay.io/keycloak/keycloak:26.8.1` and `ghcr.io/astral-sh/uv:0.12`, so the Compose and Dockerfile references stay unchanged. |
| Outbound TLS is re-signed by the machine's proxy CA, so `uv sync` in the image build and `pnpm install` in the `web` container fail with "unknown issuer" | `deploy/compose/compose.cloud.yml` layers on `compose.dev.yml`: the backend image is built from `deploy/compose/backend.cloud.Dockerfile`, which trusts the CA bundle from `CLOUD_CA_DIR` (default `/root/.ccr`), and the `web` container gets `NODE_EXTRA_CA_CERTS`. The Makefile adds this override only when `$CLOUD_CA_DIR/ca-bundle.crt` exists, so laptops, CI and the demo VM are unaffected. |

## Tests

- **Frontend** (`make frontend-test`): about 2.5 minutes.
- **Backend** (`make backend-test`): about 2,470 tests run one at a time against the `db-test` Postgres, so it is slow
  here. For a quicker loop run only the module you are changing, for example
  `cd backend && uv run pytest app/platform/tests/test_permissions.py`. Separate test databases for parallel runs:
  `make test-db-new name=w2` prints the exports to use.
- **Smoke test** (`deploy/scripts/smoke.sh https://localhost:8443`): written for production installs. On the dev stack
  the checks for security headers, a 404 for unknown assets and the hidden API docs fail by design, because the dev
  Caddyfile and Vite serve those differently.
- **End-to-end** (`make e2e`): needs Playwright browsers, which this setup does not install.

## Not covered

Real AI providers, SMTP, object storage, backups and Vultr deployment need the operator's own keys; leave the dummy
values in `.env` for development.
