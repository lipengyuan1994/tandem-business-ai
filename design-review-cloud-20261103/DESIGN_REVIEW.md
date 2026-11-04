# Design review

Reviewed at 2026-11-03T23:20:04.403361+00:00. Both design documents read end to end. Verdict: coherent general-SMB direction, suitable for further design preparation, not yet an implementation-ready or production-validated specification.

## Findings and decisions

| Priority | Finding | Required decision or next step |
|---|---|---|
| Build gate | No event-specific preparation exception verified | Confirm organizer boundary and disclose earlier SQL/fixtures and these designs |
| Build gate | Model and CPU feasibility unknown; no event GPU | Choose fictional-data inference mode; benchmark only during authorized build; text-first |
| Before implementation | Authentication, account recovery and approval authority unresolved | Select maintained auth component; define roles, revocation and second-review policy |
| Before implementation | Scope may exceed event window | Appointment/CRM first; inventory, marketing and voice optional |
| Before pilot | Privacy requires controls beyond a VPC | Confirm residency, egress, private speech, temporary files, logs and external identity dependencies |
| Before spending | Budget and operating owner absent | Set total cap, resource estimate, request limits, monitoring/stop responsibility |
| Before pilot | Restore, keys and audit durability unproven | Set RPO/RTO; test restoration and unknown-outcome reconciliation |

Approval payload binding and no-blind-retry semantics were sound. Added role-revocation checks, atomic execution claims and transactional audit/outbox requirements. Corrected reservation cancellation so it does not fabricate a physical stock movement. Clarified browser transit versus private server processing and the single-host isolation limitation. No operational capability was demonstrated.

## Official-source checks

- [Cloud Compute VPC](https://docs.vultr.com/products/compute/instances/cloud-compute/networking/vpc) and [GPU VPC](https://docs.vultr.com/products/compute/instances/cloud-gpu/networking/vpc): private network attachment documented; no hardware dedication or encryption guarantee inferred.
- [VPC-only developer guide](https://docs.vultr.com/how-to-deploy-vpc-only-instances-with-nat-gateway-on-vultr): private-instance/NAT pattern documented; selected-product compatibility untested.
- [Firewall support](https://docs.vultr.com/support/products/network/does-vultr-firewall-filter-outgoing-traffic-from-my-instance): ingress filtering; outbound controls belong on instances. Removed an unsupported assertion about conflicting documentation.
- [Serverless provisioning](https://docs.vultr.com/products/compute/serverless-inference/provisioning): subscription and charges acknowledgement confirmed; no customer-VPC privacy guarantee inferred.
- [Event page](https://lablab.ai/ai-hackathons/vultr-hackathon): Challenge 2 optional serverless inference, required Vultr backend/web deployment, no event GPUs. General event wording stresses sandboxing; reconfirm track-specific expectations rather than infer eligibility from this review.

## Proposed acceptance tests — none executed

| Test | Pass evidence during later implementation |
|---|---|
| Business isolation | Cross-business IDs denied for API, queued job, export and retrieval; no leaked rows or cache entries |
| Authentication | Logged-out, revoked-session and CSRF requests fail; role removal blocks queued execution |
| Approval integrity | Edited/expired/revoked proposal cannot execute; two worker claims yield one local mutation |
| Concurrent booking | Two requests for one resource/time yield one booking and one clear conflict |
| Audit failure | Forced audit-write failure leaves no local booking, reservation or outbox mutation |
| Unknown external outcome | Timeout after provider acceptance produces reconciliation state, no blind duplicate retry |
| Stock cancellation | Available quantity restored while physical on-hand remains unchanged |
| Private PII/speech | Synthetic canary data absent from undeclared egress/logs; speech outage fails to text, no cloud fallback |
| Network exposure | Outside IPv4/IPv6 probes cannot reach DB/model/speech/admin; denied egress verified from containers too |
| Recovery | Restore into isolated environment within chosen RTO and RPO; no replay of delivered outbox effects |
| Cost/quality | Enforced application request limit; measured latency, errors and resource usage on named hardware |

Prepare a role/action matrix, deployment data-flow inventory, acceptance-test specification and budget worksheet for review next. Do not implement the tests or application yet. Numeric latency/quality thresholds, RPO/RTO and budget need owner agreement.

## Verification limits and provenance

Public documentation only: no account, quota, prices, capacity, deployment, backup, network or AI-runtime tests. This is a design review, not a penetration test or compliance/eligibility determination. No application code, install, Git initialization, commit, remote, cloud mutation or credential access. Requested Astra/medium routing could not be established from available runtime evidence; no model switch is claimed.

Original ZIP and Library metadata remain unchanged. Local Markdown revisions are recorded in REVIEW_CHANGES.json; they have not been uploaded as a new Library version. Original cloud provenance covers its original creation task, while the appended revision records local changes.
