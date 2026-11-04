# smb-backup: pg_dump + restic + supercronic (ADR-010). Build from the repository root.
FROM postgres:17-alpine@sha256:eee6c6e1b0894fa4fe0075eb054af9fd1cf39a35321c2d4110626cfe506411fb
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.51
# Pinned checksums for the release binaries (computed 2026-10-25 from the GitHub release assets).
ARG SUPERCRONIC_SHA256_AMD64=cc232108374e8e2b537045f2d5eaa1229f619db314c3ba6a14d38b35dc0aa123
ARG SUPERCRONIC_SHA256_ARM64=4ae22576a78223d8ccdf5f3b3660a5cbb5288416bf0ca96afc4a36d195932b94
RUN apk add --no-cache restic bash curl ca-certificates jq \
 && case "${TARGETARCH:-amd64}" in \
      amd64) sum="$SUPERCRONIC_SHA256_AMD64" ;; \
      arm64) sum="$SUPERCRONIC_SHA256_ARM64" ;; \
      *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH:-amd64}" \
 && echo "${sum}  /usr/local/bin/supercronic" | sha256sum -c - \
 && chmod 0755 /usr/local/bin/supercronic \
 # The base image's entrypoint helper (gosu) is unused here (we start as a non-root user) and carries old Go libraries.
 && rm -f /usr/local/bin/gosu \
 && addgroup -S -g 10002 backup && adduser -S -u 10002 -G backup -H -s /sbin/nologin backup \
 && mkdir -p /state && chown backup:backup /state
COPY deploy/scripts/backup.sh deploy/scripts/lib/restore-db.sh /usr/local/bin/
COPY deploy/scripts/backup.crontab /etc/backup.crontab
RUN chmod 0755 /usr/local/bin/backup.sh /usr/local/bin/restore-db.sh
ENV RESTIC_CACHE_DIR=/state/cache BACKUP_STATE_DIR=/state
USER backup
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD ["/usr/local/bin/backup.sh", "--healthcheck"]
ENTRYPOINT []
CMD ["supercronic", "/etc/backup.crontab"]
