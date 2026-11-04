# Regulated data profile

This profile makes the software ready but does not make the installation compliant.
Signed agreements, the clinic's own policies and a legal review are needed. This is
infrastructure preparation for a possible later clinic edition, not clinical software
or a healthcare compliance claim. Do not put real clinic data into the event demo.

## Agreements and switching profiles

An operator with shell access changes the installation's protected `.env` file in
`/opt/smb`. The default is `DATA_PROFILE=standard`. To enable the profile, set:

```dotenv
DATA_PROFILE=regulated
REGULATED_HOST_BAA="Example host, BAA signed 2027-01-10"
REGULATED_BACKUP_BAA="Example backup provider, BAA signed 2027-01-10"
```

These are plain text attestations, each non-empty after trimming and at most 200
characters. They are displayed to owners/admins on System; never put credentials
in them. The operator must obtain the actual signed agreements and verify their
product coverage first. The software cannot verify an agreement. A provider name,
API key, or attestation alone does not establish coverage. Host and backup storage
can be different providers; Vultr inference or S3 compatibility implies no BAA.

After applying the migration in the normal upgrade procedure, recreate **both** API
and worker so they use the same policy. From `/opt/smb`:

```sh
docker compose --env-file .env -f compose.yml up -d --no-deps --force-recreate api worker
```

This is an operator procedure, not something P8-23 deploys. Missing attestations
prevent startup. Check System for the profile and both attestations, then verify a
staff record read in Audit. Do not dump the environment or resolved Compose config
into logs. To return to standard, set `DATA_PROFILE=standard` and recreate both
processes again. Existing restrictions remain restricted, contact suppressions stay,
and retained history is not erased by a profile switch. Downgrading the migration
is refused while restricted rows exist or would discard authentication origins;
never bypass those constraints.

## What changes

- Staff reads of customer detail, notes, timeline and consent history produce
  `crm.customer.viewed`; conversation detail produces `conversations.conversation.viewed`;
  appointment detail produces `scheduling.appointment.viewed`. The actor and target ID
  identify who viewed whom; summaries contain no personal data. Successful reads only,
  at most once per staff user, business and target per 10 minutes, including concurrent
  reads. Standard performs no read-audit database work. Exports remain audited in all
  profiles with `admin_ops.export.created`.
- The Audit page already filters by action prefix and actor kind. Enter the action
  names above to find view activity and open an event for its target ID; no extra tab is added. Lists, search results, failed reads,
  public visitor reads and other endpoints are outside this task's read-audit coverage.
- The owner's typed-name erasure confirmation instead restricts contact after the
  same checks as erasure: open approval proposals referring to the customer or any
  of their linked records, and upcoming held/confirmed appointments, block it with
  409 `admin_ops.erasure_blocked` and the same reasons. Decide/cancel those proposals
  and cancel/complete those appointments before retrying. A blocked request changes nothing. Master identity, personal notes, timeline, consent evidence,
  conversation text, appointment notes, approvals and audit history are kept.
  Contact points are blanked and inactive; every stored e-mail address receives an
  `erased` suppression, including inactive/secondary addresses. Matching visitor
  conversations are linked to the restricted customer, their visitor contact fields
  cleared and visitor tokens revoked. The customer is hidden from lists/search,
  excluded from marketing and cannot be booked or sent replies. Regulated booking
  and hold paths lock and recheck the customer with `FOR SHARE`, so they serialize
  with restriction. A booking/hold that commits first blocks restriction; a restriction
  that commits first makes the waiting booking/hold ineligible. Existing appointments
  and history remain. Each matching outbox message gets a durable restriction timestamp independent of
  its delivery status. Claims and retries honor it, including after a profile switch
  or a late provider result. An already in-flight attempt cannot be recalled; its
  actual outcome is retained, but it cannot schedule another attempt. Conversations
  also keep a restriction marker and cannot be relinked away from the restricted
  customer or replied to. Cached idempotency responses containing the customer's ID
  are deleted: these are a cache, not retained history. Reusing such a creation key
  becomes a new mutation rather than replaying the old active record.
  `erasure_requests.status=restricted` and `admin_ops.erasure.restricted` record the
  result. Repeating the request returns the same result. **Lifting a restriction is
  out of scope, including for owners.** Standard retains its existing erasure rules.
- Appointment confirmation/reminder text and HTML contain only business name,
  date/time and a reply instruction for reaching the business; no person, service,
  resource or notes. Selection happens in one shared renderer at enqueue and delivery.
  There are no automatic appointment notification callers/reminder jobs yet; adding
  those is separate work. The business must ensure replies reach a monitored contact
  (configure its public e-mail/Reply-To as described in [email.md](email.md)).
- Public chat displays: “Please do not send medical details here. We will contact
  you to discuss them.” The public profile exposes only a notice boolean, not
  attestations or the installation profile name. This is a notice, not a content filter.
- Staff sessions expire after 15 idle minutes or 8 absolute hours. The absolute cap
  also applies to sessions created before switching profiles. Authentication time
  persists across business switches/session rotation; only a new authentication starts
  a new absolute lifetime. Cookie lifetime is
  8 hours; public visitor sessions are separate. Standard session settings are unchanged.
  Existing session origins are backfilled from their stored creation times; pre-upgrade
  rotation chains cannot be reconstructed. An installation already using regulated
  sessions should require fresh staff authentication when applying this upgrade.
- Settings → Policies supports eight terminology overrides in every profile:
  customer(s), technician(s), service(s), appointment(s). Each value is 1–40 characters
  of single-line plain text. Blank inputs remove the override. Navigation, titles,
  main buttons and short labels use i18next interpolation scoped to the active business.
  Record names, API fields and free-text history are not rewritten.

## What this does not do

AI stays off until P5-01 implements BAA-aware routing (`baa` and the forced regulated
policy) after kickoff. P8-23 adds no AI module, route file or inference call. Never
attach an unrestricted inference integration to a regulated installation.

There is no automatic retention enforcement or legal hold expiry/release workflow.
Backups retain data until their own expiry; this profile does not delete or alter
backups. This setting does not configure compliant hosting, storage encryption,
provider agreements, SMTP coverage, access policies, clinical records, treatment
plans, insurance, or the clinic's legal retention schedule. Export contains retained
personal records and needs the operator's own secure handling policy. Before any
real-data pilot, obtain the required legal and security review and verify the
installation, including its mail, logs, backups and restore procedure.
