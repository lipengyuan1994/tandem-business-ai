# SMTP credentials and mail setup

Read [install.md](install.md) and [rotate-secrets.md](rotate-secrets.md) for the installation's other secrets.

The shop chooses and pays for its SMTP provider. Vultr is the application host, not an included mail service.
Use the provider's authenticated submission endpoint, normally **587 with STARTTLS**, or **465 with implicit TLS**.
Vultr may block outgoing **port 25**; do not use it for normal submission.
Certificates and host names are always verified against the system trust store. A provider with an invalid
certificate must fix it; there is no verification bypass.

## Application mail

Edit `/opt/smb/.env` and your saved `install-<name>/.env` in a private editor; keep both mode 600.
Never paste a real URL in shell arguments, tickets, screenshots or version-controlled files.

Synthetic example (replace the provider, credentials and sender):

```dotenv
SMTP_URL=smtp://user:replace-me@mail.example.com:587?starttls=true&from=Shop%20Name%20%3Cno-reply@example.com%3E
SMTP_FROM=
```

Percent-encode username, password and query values (for example `@` → `%40`, `&` → `%26`, `+` → `%2B`,
`#` → `%23`, space → `%20`). `from` is a single mailbox; otherwise set `SMTP_FROM=Shop Name <no-reply@example.com>`.
Use a sender verified by the provider. `smtps://user:replace-me@mail.example.com:465?from=no-reply@example.com`
is the implicit TLS alternative. On 587 STARTTLS is the default. Plain mail is allowed only with explicit
`starttls=false` and literal localhost/127.0.0.1/::1, for local tests. Invalid configuration prevents API/worker startup.
An empty `SMTP_URL` leaves normal notifications simulated; a test e-mail is refused until SMTP is configured.
"Normal notifications" are the marketing campaigns, the supplier quote requests and follow-ups, and the payment
reminders. All of them take one rule (`notifications.email_channel()`) when they are queued: without SMTP they go
to the `simulated` channel, which the worker marks delivered and the screens label *Simulated*, so nothing leaves the
machine and no delivery issue appears (a demo install needs no mail setup). With SMTP configured they use the real
`email` channel. The channel is stored on each message when it is queued: after you add SMTP, messages queued
earlier stay simulated, and after you remove it, messages still waiting as `email` fail with "email delivery is
not configured". **Seeding the demo data (`seed`, `reset-demo`) never sends real mail, even with SMTP configured:**
the seed runs inside `notifications.simulated_delivery()`, so the supplier quote requests it creates (to example.com
addresses) are `simulated` from the start. Mail that people queue afterwards, while using the demo, follows the rule
above, so leave `SMTP_URL` empty on a demo VM unless you want those messages delivered. Staff replies in the Inbox are not e-mailed at all: web chat replies appear in the visitor's
thread and the other conversation channels are recorded as simulated.

Recreate API and worker after editing: `vm$ docker compose up -d api worker`.
System → E-mail shows only the configured host. Owners/admins can choose **Send test e-mail**, enter an
address they control and submit. The result means **queued**, not delivered. The worker performs delivery;
check the inbox and System's delivery issues (failed/unknown). The action is audited without a recipient in
the audit summary and limited to five messages per business per fixed clock hour.
A configured host alone does not establish connectivity or successful delivery.

Notifications have text and escaped HTML, no remote images or tracking pixels. The business's
`public_profile.email`, when set, becomes Reply-To. Marketing carries one-click unsubscribe headers.
SMTP acceptance is not proof of inbox placement. Transient failures retry with outbox backoff; an ambiguous
failure after DATA becomes **unknown** and is never retried automatically. Confirm receipt out of band
before using Delivery issues → Mark delivered or Retry anyway (retry can produce duplicates).

## Keycloak invitations and password resets (manual, install and upgrade)

The application URL does **not** automatically configure Keycloak. `stage.sh` copies
`deploy/compose/realm-smb.prod.json`; `install.sh` uploads it and Keycloak imports it at first startup.
Keycloak skips import for an existing realm on upgrade; `upgrade.sh` updates images, not realm mail settings.
A generated realm with a resolved password would add a secret-bearing staging artifact and still would not
update an existing realm. This release therefore uses the admin console for both installation and rotation.
Do not write credentials into either repository realm JSON file.

1. Open the Keycloak admin console through the SSH tunnel in [access.md](access.md). Select realm **smb**,
   then **Realm settings → Email**.
2. Copy the same *decoded* values used in `SMTP_URL`: **Host** = URL host, **Port** = URL port
   (587 by default for `smtp`, 465 for `smtps`). Do not paste the whole URL into Host.
3. Split the decoded `from` mailbox (or `SMTP_FROM`) into **From** = `no-reply@example.com` and
   **From display name** = `Shop Name`. Optionally set **Reply to** to the business's public e-mail.
4. For 587, enable **StartTLS**, disable **SSL**. For `smtps` or port 465, enable **SSL**, disable **StartTLS**.
   Keep certificate/hostname verification enabled; do not install a permissive trust manager.
5. If URL credentials are present, enable **Authentication** and enter decoded **Username** and **Password**.
   Otherwise disable Authentication. Save. Keycloak stores this configuration in its database, so protect
   database backups as secrets too.
6. Use **Test connection** with an administrator account that has an e-mail address you control; verify receipt.
   Then invite a new synthetic test user from Settings → Staff and follow the received setup link.
   Invitations use Keycloak's mail sender, not the application outbox.
7. In **Realm settings → Login**, check **Forgot password** is enabled (the shipped realm enables password reset).
   Sign out, select **Forgot password?**, request a reset for a test user, and verify receipt and the reset link.
   Remove test memberships/users after testing. The System test button does not verify these Keycloak flows.
8. Repeat or verify these settings after install/upgrade and whenever SMTP credentials or provider settings change.
   Keep application and Keycloak settings aligned before enabling invitations.

## Rotation

Create a replacement SMTP credential at the shop's provider. Update both private `.env` copies and the
Keycloak Email settings, recreate API/worker, then verify the application test, invitation and password-reset
flows above before revoking the old credential. If overlap is unavailable, perform these steps back to back;
mail may pause during rotation. Never print either credential in an operational report. These steps require
an authorized production operator; local protocol tests do not verify a production provider or Keycloak delivery.
