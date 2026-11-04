# smb-api: backend app, Alembic migrations and CLI (docs/roadmap/10-vultr-infrastructure.md#container-images).
# Build from the repository root:  docker build -f deploy/docker/api.Dockerfile -t smb-api:0.1.0 .
FROM ghcr.io/astral-sh/uv:0.12@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a AS uv

FROM python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /build
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
# Bytecode is compiled now because the runtime root filesystem is read-only.
RUN /opt/venv/bin/python -m compileall -q app

FROM python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962 AS runtime
ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/srv PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app \
 # Hardening: the runtime never installs packages, so remove pip (and unused standard-library parts) from the final filesystem.
 && rm -rf /usr/local/lib/python3.13/site-packages/pip* /usr/local/lib/python3.13/ensurepip \
           /usr/local/lib/python3.13/idlelib /usr/local/lib/python3.13/test /usr/local/lib/python3.13/turtledemo \
           /usr/local/bin/pip* /usr/local/bin/idle*
COPY --from=build /opt/venv /opt/venv
COPY --from=build /build/app /srv/app
COPY backend/alembic.ini /srv/alembic.ini
COPY backend/migrations /srv/migrations
# Synthetic demo dataset for `python -m app.cli seed` (demo installs only; no real data is ever baked in).
COPY data/fixtures.json /data/fixtures.json
WORKDIR /srv
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readyz', timeout=4).status == 200 else 1)"]
# Raw access logs include OAuth codes and unsubscribe tokens in query strings, outside application redaction.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips=*", "--no-access-log"]
