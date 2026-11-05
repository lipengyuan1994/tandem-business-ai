"""Host watchdog. Never log command output, environment values, or webhook URLs."""

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shlex
import socket
import ssl
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

JOBS_SQL = """BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';
SELECT json_build_object(
 'dead', count(*) FILTER (WHERE status = 'dead'),
 'old_pending', count(*) FILTER (WHERE status IN ('queued', 'failed')
   AND run_at < now() - interval '15 minutes'),
 'expired_running', count(*) FILTER (WHERE status = 'running'
   AND (locked_until IS NULL OR locked_until < now() - interval '5 minutes')))
FROM jobs;
ROLLBACK;"""


def command(args, input=None):
    return subprocess.run(  # noqa: S603 - fixed command arguments, never a shell
        args,
        input=input,
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
    ).stdout.strip()


def read_settings(path):
    # Do not source .env as shell code. Only these two host settings are consumed.
    settings = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in ("PUBLIC_URL", "ALERT_WEBHOOK_URL"):
            parts = shlex.split(value, comments=True)
            settings[key.strip()] = "".join(parts)
    return settings


def age(timestamp):
    return time.time() - dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()


def backup_issues(status):
    issues = []
    if status.get("result") != "ok":
        issues.append("backup: last attempt failed or never ran")
    if not status.get("last_success") or not 0 <= age(status["last_success"]) <= 7 * 3600:
        issues.append("backup: no success within 7 hours")
    for kind in ("prune", "check"):
        detail = status.get("maintenance", {}).get(kind, {})
        if detail.get("result") != "ok":
            issues.append(f"backup {kind}: failed or never ran")
        if not detail.get("last_success") or not 0 <= age(detail["last_success"]) <= 8 * 86400:
            issues.append(f"backup {kind}: no success within 8 days")
    return issues


def collect(directory, public_url, previous):
    compose = [
        "docker",
        "compose",
        "--project-directory",
        str(directory),
        "-f",
        str(directory / "compose.yml"),
        "--env-file",
        str(directory / ".env"),
    ]
    issues, observations = [], {}

    def probe(name, action):
        try:
            action()
        except Exception:
            # Output and exceptions can contain credentials; expose only the check name.
            issues.append(f"{name}: check unavailable")

    def ready():
        if not public_url.startswith("https://"):
            raise ValueError("HTTPS required")
        with urllib.request.urlopen(  # noqa: S310 - HTTPS scheme checked immediately above
            public_url.rstrip("/") + "/api/v1/readyz", timeout=10
        ) as response:
            if json.load(response).get("status") != "ok":
                issues.append("https readyz: not ready")

    def containers():
        services = command([*compose, "config", "--services"]).splitlines()
        if not services:
            raise ValueError("no services")
        restarts = {}
        for service in services:
            ids = command([*compose, "ps", "--all", "--quiet", service]).splitlines()
            if not ids:
                issues.append(f"container {service}: missing")
            for container in ids:
                state = json.loads(command(["docker", "inspect", "--format", "{{json .State}}", container]))
                if service == "migrate":
                    if state.get("Status") != "exited" or state.get("ExitCode") != 0:
                        issues.append("container migrate: not completed successfully")
                    continue
                if state.get("Status") != "running" or state.get("Health", {}).get("Status", "healthy") != "healthy":
                    issues.append(f"container {service}: not running/healthy")
                count = int(command(["docker", "inspect", "--format", "{{.RestartCount}}", container]))
                restarts[container] = count
                old = previous.get("observations", {}).get("restarts", {}).get(container, 0)
                if count > old:
                    issues.append(f"container {service}: restarted")
                if state.get("OOMKilled"):
                    issues.append(f"container {service}: OOM killed")
        observations["restarts"] = restarts

    def oom():
        try:
            log = command(["journalctl", "-k", "--since", "10 minutes ago", "--no-pager", "-o", "cat"])
        except (subprocess.SubprocessError, OSError):
            log = command(["dmesg", "--since", "10 minutes ago"])
        if re.search(r"out of memory|oom-kill|killed process", log, re.I):
            issues.append("host: recent OOM kill")

    def disk():
        disks = {}
        for path in (directory, Path("/var/lib/docker")):
            stat = os.statvfs(path)
            free = stat.f_bavail * stat.f_frsize
            disks[str(path)] = {"free_bytes": free, "free_inodes": stat.f_favail}
            if free < 5 * 1024**3:
                issues.append(f"disk {path}: less than 5 GiB free")
            if stat.f_favail < 10000 or stat.f_favail / max(stat.f_files, 1) < 0.05:
                issues.append(f"disk {path}: low free inodes")
        observations["disk"] = disks

    def backup():
        issues.extend(backup_issues(json.loads(command([*compose, "exec", "-T", "backup", "backup.sh", "--status"]))))

    def jobs():
        counts = json.loads(
            command(
                [
                    *compose,
                    "exec",
                    "-T",
                    "postgres",
                    "psql",
                    "-X",
                    "-qAt",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    "postgres",
                    "-d",
                    "smb",
                ],
                JOBS_SQL,
            )
        )
        observations["jobs"] = counts
        for key in ("dead", "old_pending", "expired_running"):
            if counts[key] > 0:
                issues.append(f"jobs: {key}")

    def certificate():
        url = urllib.parse.urlparse(public_url)
        with socket.create_connection((url.hostname, url.port or 443), timeout=10) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=url.hostname) as tls:
                expires = ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
        observations["certificate_expires_epoch"] = expires
        if expires - time.time() < 14 * 86400:
            issues.append("certificate: expires within 14 days")

    def clock():
        if command(["timedatectl", "show", "--property=NTPSynchronized", "--value"]) != "yes":
            issues.append("time sync: not synchronized")

    for name, action in [
        ("https readyz", ready),
        ("containers", containers),
        ("OOM journal", oom),
        ("disk", disk),
        ("backup", backup),
        ("jobs", jobs),
        ("certificate", certificate),
        ("time sync", clock),
    ]:
        probe(name, action)
    return sorted(set(issues)), observations


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_alert(url, payload):
    if not url:
        return
    if not url.startswith(("https://", "http://")):
        raise ValueError("webhook must use HTTP(S)")
    request = urllib.request.Request(  # noqa: S310 - HTTP(S) schemes validated above
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
        if not 200 <= response.status < 300:
            raise ValueError("webhook refused alert")


def update_state(path, issues, observations, webhook):
    previous = json.loads(path.read_text()) if path.exists() else {}
    signature = hashlib.sha256(json.dumps(issues, sort_keys=True).encode()).hexdigest()
    state = {
        "checked_at": dt.datetime.now(dt.UTC).isoformat(),
        "issues": issues,
        "observations": observations,
        "signature": signature,
        "notified_signature": previous.get("notified_signature"),
    }
    if signature != state["notified_signature"]:
        text = "SMB watchdog: " + ("; ".join(issues) if issues else "all checks healthy")
        print(text, flush=True)
        try:
            send_alert(webhook, {"text": text, "status": "alert" if issues else "ok", "issues": issues})
            state["notified_signature"] = signature
            state["delivery"] = "sent" if webhook else "logging_only"
        except Exception:
            state["delivery"] = "failed"
            print("SMB watchdog: webhook delivery failed; will retry next run", flush=True)
    else:
        state["delivery"] = previous.get("delivery", "logging_only")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(path)
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose-dir", type=Path, default=Path("/opt/smb"))
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/smb-watchdog"))
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    settings = read_settings(args.compose_dir / ".env")
    webhook = settings.get("ALERT_WEBHOOK_URL", "")
    if args.test:
        print("SMB watchdog: test alert" + ("" if webhook else " (logging only; no URL set)"))
        send_alert(webhook, {"text": "SMB watchdog: test alert", "status": "test", "issues": []})
        return
    args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (args.state_dir / "watchdog.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = args.state_dir / "status.json"
        try:
            previous = json.loads(path.read_text())
        except (OSError, ValueError):
            previous = {}
        issues, observations = collect(args.compose_dir, settings.get("PUBLIC_URL", ""), previous)
        # A corrupt status file is recoverable; replace it atomically after checking.
        if path.exists() and not previous:
            path.unlink()
        update_state(path, issues, observations, webhook)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("SMB watchdog: execution failed (check configuration and local permissions)", flush=True)
        raise SystemExit(1) from None
