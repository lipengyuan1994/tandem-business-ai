# smb-web: the built single-page app plus Caddy (TLS, security headers, routing to api and keycloak).
# Build from the repository root:  docker build -f deploy/docker/web.Dockerfile -t smb-web:0.1.0 .
# The static build is architecture independent, so it always runs on the build host's platform.
FROM --platform=$BUILDPLATFORM node:24-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS build
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 CI=1
RUN corepack enable pnpm
WORKDIR /app
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build && find dist -name '*.map' -delete

FROM caddy:2.11.7-alpine@sha256:eb4d35b7452d1ab6171fbdbe8d9275b2301dad6dad92115d91e19fb9b24d510f AS runtime
# Pick up security fixes after the pinned base was published. Health checks use BusyBox wget.
# Caddy runs as a fixed unprivileged user, uid 10003 (smb-api is 10001, smb-backup 10002). A new named volume takes its
# owner from the image directory it is mounted on, so a fresh install gets a writable /data (certificates, ACME account)
# and /config. A volume made by the former root container keeps root as owner: docs/runbooks/upgrade.md.
RUN apk upgrade --no-cache \
 && if apk info -e curl >/dev/null; then apk del --no-cache curl; fi \
 && addgroup -S -g 10003 caddy \
 && adduser -S -u 10003 -G caddy -H -h /nonexistent -s /sbin/nologin caddy \
 && chown -R caddy:caddy /data /config
COPY --from=build /app/dist /srv
COPY deploy/compose/Caddyfile /etc/caddy/Caddyfile
USER caddy
# Fail the build on a broken Caddyfile (DOMAIN is only a placeholder here).
RUN DOMAIN=example.com caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8081/healthz"]
