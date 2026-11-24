# Cloud dev variant of backend/Dockerfile.dev (scripts/cloud-dev-setup.sh, docs/CLOUD_DEV.md).
# Claude's cloud machines re-sign outbound TLS with their own CA, so `uv sync` must trust it. The CA comes in through
# the `cloudca` build context; nothing secret is baked in (it is a public CA certificate bundle).
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
COPY --from=cloudca ca-bundle.crt /etc/ssl/certs/cloud-ca-bundle.crt
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH \
    SSL_CERT_FILE=/etc/ssl/certs/cloud-ca-bundle.crt
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project
COPY . .
