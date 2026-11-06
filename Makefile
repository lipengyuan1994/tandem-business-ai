# Single entry point for every project command (ADR-016). Run `make help`.
SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE_DEV := docker compose -p smb -f deploy/compose/compose.dev.yml --env-file .env
BACKEND := cd backend &&
FRONTEND := cd frontend &&

.PHONY: help
help: ## List targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.env:
	cp .env.example .env

# ---------------------------------------------------------------- development stack
.PHONY: dev dev-down dev-logs dev-ps test-db test-db-new test-db-drop
dev: .env ## Start the dev stack (postgres, keycloak, api, worker, web, caddy) on https://localhost:8443
	$(COMPOSE_DEV) up -d --build
	@echo "App: https://localhost:8443   Keycloak: https://localhost:8443/auth"

dev-down: ## Stop the dev stack (keeps data volumes)
	$(COMPOSE_DEV) down

dev-logs: ## Follow dev stack logs
	$(COMPOSE_DEV) logs -f --tail=100

dev-ps: ## Show dev stack containers
	$(COMPOSE_DEV) ps

test-db: .env ## Start only the throwaway test database (localhost:5433)
	$(COMPOSE_DEV) up -d --wait db-test

test-db-new: ## Create an isolated test database and print exports: make test-db-new name=w2
	@bash deploy/scripts/test-db.sh new "$(name)"

test-db-drop: ## Drop an isolated test database: make test-db-drop name=w2
	@bash deploy/scripts/test-db.sh drop "$(name)"

# ---------------------------------------------------------------- database
.PHONY: migrate migration
migrate: ## Apply migrations to the dev database
	$(COMPOSE_DEV) exec api alembic upgrade head

migration: ## Create an empty migration: make migration name=crm_customers
	@test -n "$(name)" || (echo "usage: make migration name=<module>_<what>" && exit 1)
	$(BACKEND) uv run alembic revision -m "$(name)" --rev-id "$$(date -u +%Y%m%d_%H%M)_$(name)"

# ---------------------------------------------------------------- quality
.PHONY: check test lint fmt typecheck backend-test frontend-test licenses secrets
check: fixtures lint typecheck test deploy-check licenses secrets ## Everything that must pass before a task is done
	@echo "make check: ALL PASSED"

.PHONY: fixtures
fixtures: ## Validate portable synthetic fixtures (stdlib only, no network)
	python3 -I data/validate_fixtures.py

lint: ## Lint and format-check backend and frontend
	$(BACKEND) uv run ruff check . && uv run ruff format --check .
	$(FRONTEND) pnpm lint

fmt: ## Auto-format backend
	$(BACKEND) uv run ruff format . && uv run ruff check --fix .

typecheck: ## Type-check backend (mypy strict) and frontend (tsc)
	$(BACKEND) uv run mypy
	$(FRONTEND) pnpm typecheck

test: backend-test frontend-test ## Backend and frontend tests

backend-test: test-db ## Backend tests against the real Postgres test database
	$(BACKEND) uv run pytest --cov --cov-report=term-missing:skip-covered -q

frontend-test: ## Frontend component tests
	$(FRONTEND) pnpm test

licenses: ## Fail on GPL/AGPL dependencies
	$(BACKEND) uv run pip-licenses --partial-match --fail-on="GNU General Public License;GNU Affero;AGPL;GPLv" --format=plain >/dev/null && echo "backend licences OK"
	$(FRONTEND) pnpm licenses list --prod --json | python3 -I -c "import json,sys; d=json.load(sys.stdin); bad=[k for k in d if 'GPL' in k.upper() and 'LGPL' not in k.upper()]; print('frontend licences OK' if not bad else 'BAD: '+str(bad)); sys.exit(1 if bad else 0)"

secrets: ## Scan the working tree for secrets (gitleaks, if installed)
	@if command -v gitleaks >/dev/null; then gitleaks dir . --no-banner --redact -c .gitleaks.toml; else echo "gitleaks not installed: skipped"; fi

# ---------------------------------------------------------------- API contract
# --- Production images and deployment (docs/roadmap/10-vultr-infrastructure.md) ---
VERSION ?= 0.1.0
REGISTRY ?= smb
# Empty = build for this machine (fast local rehearsal). Images for a Vultr VM must be linux/amd64.
PLATFORM ?=
DOCKER_BUILD = docker build $(if $(PLATFORM),--platform $(PLATFORM)) --build-arg BUILDKIT_INLINE_CACHE=0

.PHONY: images
images: ## Build smb-api, smb-web and smb-backup images: make images VERSION=0.1.0 [REGISTRY=host/path] [PLATFORM=linux/amd64]
	$(DOCKER_BUILD) -f deploy/docker/api.Dockerfile -t $(REGISTRY)/smb-api:$(VERSION) .
	$(DOCKER_BUILD) -f deploy/docker/web.Dockerfile -t $(REGISTRY)/smb-web:$(VERSION) .
	$(DOCKER_BUILD) -f deploy/docker/backup.Dockerfile -t $(REGISTRY)/smb-backup:$(VERSION) .
	@docker image ls --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep -E '^$(REGISTRY)/smb-(api|web|backup):$(VERSION) '

.PHONY: deploy-check
deploy-check: ## Static checks of deploy/: shellcheck, Compose, Caddyfile, realm, Terraform validate, cloud-init schema, installer dry run
	deploy/scripts/check-deploy.sh

.PHONY: rehearse
rehearse: ## Local production rehearsal: production images, bootstrap, smoke, e2e, upgrade, rollback, restore (stop `make dev` first)
	deploy/scripts/rehearse.sh $(REHEARSE_ARGS)

.PHONY: gen-api
gen-api: ## Regenerate backend/openapi.json and frontend/src/api/schema.d.ts
	$(BACKEND) uv run python -m app.cli openapi > openapi.json
	$(FRONTEND) pnpm exec openapi-typescript ../backend/openapi.json -o src/api/schema.d.ts

# ---------------------------------------------------------------- later phases
.PHONY: seed demo-logins e2e eval images backup-test
seed: ## Load data/fixtures.json into the dev stack through the services; `make seed reset=1` recreates the demo businesses
	$(COMPOSE_DEV) exec -T api python -m app.cli seed $(if $(reset),--reset)

# The command is the one a production VM uses (docs/runbooks/install.md); the folder is mounted the same way, and the
# container runs as you so that the file is yours with mode 600.
demo-logins: ## Keycloak logins for the demo staff on the dev stack; passwords in .demo-logins/logins.txt. Options: make demo-logins args="--email riley@example.com --reset-passwords"
	@mkdir -p -m 700 .demo-logins
	$(COMPOSE_DEV) run --rm --no-deps -T --user "$$(id -u):$$(id -g)" -v "$(CURDIR)/.demo-logins:/out" api \
		python -m app.cli demo-logins --out /out/logins.txt $(args)
	@echo "Passwords: .demo-logins/logins.txt (mode 600, never printed). Delete it when you are done."

e2e: ## Playwright end-to-end tests against the running dev stack (`make dev` first); resets the demo data
	$(FRONTEND) pnpm e2e

eval: ## Synthetic AI eval: AI_LIVE=1 make eval suite=intake [model_class=agent provider=vultr model=...]
	@test "$$AI_LIVE" = "1" || (echo "Refusing live evaluation: explicitly set AI_LIVE=1." >&2; exit 2)
	$(BACKEND) .venv/bin/python -m evals.runner --suite "$(suite)" $(if $(model_class),--model-class "$(model_class)") $(if $(provider),--provider "$(provider)") $(if $(model),--model "$(model)") $(if $(baseline),--baseline "$(abspath $(baseline))")

backup-test: ## Backup and restore round trip on a throwaway stack with a local restic repository (needs `make images`)
	VERSION=$(VERSION) REGISTRY=$(REGISTRY) deploy/scripts/backup-test.sh
