# SMB AI operations suite Vultr architecture privacy and deployment

Design draft for founder and implementation team | 24 October 2026 UTC | Nothing provisioned or deployed

## Architecture decision

Use a modular monolith for the first application, PostgreSQL for authoritative state and a durable job/outbox mechanism for asynchronous work. Separate the inference interface from the application so the same workflow can run with a small self-hosted model or a permitted managed endpoint. Start with explicit structured tools, not autonomous infrastructure agents.

Keep two deployment profiles. The hackathon profile proves the workflow on Vultr using fictional data. The customer-owned profile aims to keep database, inference, speech and operational logs under each customer's control. Shared application interfaces do not make their privacy properties equivalent.

Framework, model, image versions and machine sizes remain implementation decisions. No quota, account eligibility, current price, GPU stock, credit redemption, deployment success or performance has been verified. This document is not an infrastructure reservation or spending authorisation.

## Verified platform facts and practical limits

Vultr Cloud Compute supports VPC attachment, including documented Console, API, CLI and Terraform paths. A VPC provides an isolated private network interface between attached instances; it is not proof of encryption, physical dedication or isolation from the cloud provider. [Cloud Compute VPC documentation](https://docs.vultr.com/products/compute/instances/cloud-compute/networking/vpc)

Vultr documents VPC-only instances and NAT Gateways for private instances needing internet access. Availability and compatibility with the eventual selected instance type and region must still be verified. Do not assume an AWS-style VPC endpoint, IAM role, security group or managed secret service exists merely because a similarly named concept exists elsewhere. [VPC-only deployment developer guide](https://docs.vultr.com/how-to-deploy-vpc-only-instances-with-nat-gateway-on-vultr)

The specific Vultr outbound-traffic support article states that Vultr Firewall filters ingress, not outgoing instance traffic. Design outbound restrictions in the host firewall and application egress layer, then test them; do not rely on an assumed provider egress firewall. [Outbound traffic support article](https://docs.vultr.com/support/products/network/does-vultr-firewall-filter-outgoing-traffic-from-my-instance)

Vultr documents attaching Cloud GPU instances to VPC networks. This supports a future private-network model server design, but does not establish a current GPU allocation, required model fit or event access. [GPU VPC documentation](https://docs.vultr.com/products/compute/instances/cloud-gpu/networking/vpc)

Vultr Serverless Inference is a managed service with a subscription setup and charges acknowledgement. It is not the same as a customer-operated model server inside the customer's VPC. Do not infer retention, training-use, data residency or private-endpoint guarantees from “OpenAI-compatible.” [Provisioning documentation](https://docs.vultr.com/products/compute/serverless-inference/provisioning)

## Hackathon profile

Target Challenge 2, Future of Work. The event requires a Vultr VM backend and Vultr web deployment. Serverless Inference is optional; event GPUs are unavailable. Challenge 1 instead requires Serverless Inference and isolated execution, so do not accidentally describe this design as satisfying that track. [Event requirements](https://lablab.ai/ai-hackathons/vultr-hackathon)

Smallest proposed topology: public HTTPS reverse proxy, web/API process, background worker and self-hosted PostgreSQL on a single Vultr Compute VM, with private container/service networking. Only HTTPS is public. Database and model endpoints are not exposed publicly. This is a low-complexity demo arrangement with a single failure domain, not a highly available customer deployment.

Inference choices, to be selected only after access and budget checks:

1. CPU-only self-hosted small model on permitted Compute resources. Benchmark actual structured-output quality, memory and latency. Reduce context and concurrency; do not assume real-time voice performance.
2. Vultr Serverless Inference, explicitly optional for this track. Send fictional demo content only. Describe the hosted-inference boundary accurately and confirm current model availability, authentication, terms and charges before use.
3. Another inference provider only if explicitly permitted by the final event rules and authorised by the user. Do not assume that optional Serverless Inference means every other provider is allowed.

A deterministic fixture mode may support tests and an offline demonstration, but must be labelled as simulated and must not be represented as live AI. If inference remains unverified, mark the AI milestone blocked rather than fabricating a working demo.

## Customer-owned target profile

Deploy each customer into its own Vultr account/project arrangement as supported and verified at deployment time. Keep configuration, databases, models, backups and operational ownership customer-specific. Avoid a shared vendor control plane that silently receives business data. Account/project terminology and access controls must be checked against actual Vultr capabilities before implementation.

Proposed service boundaries within one selected region:

- Edge: HTTPS reverse proxy, web assets, request limits and authentication entry point. Public exposure is limited to the intended application endpoints.
- Application: API and worker, authorisation, workflow state machine, validation, audit and connector dispatch.
- Data: customer-operated PostgreSQL and backup process. Database listens on private interfaces only and requires authenticated encrypted connections.
- Inference: customer-operated model runtime with no public endpoint, no direct database credentials and only the minimum scoped request context.
- Speech: customer-operated STT and TTS services when audio privacy is required. Separate capacity limits protect the rest of the application.
- Administration: customer-controlled VPN or equivalent restricted administrative path, with named operators and audited access. Not a publicly exposed database administration panel.

Prefer VPC-only internal nodes where the selected products support them. Otherwise explicitly block public ingress, bind internal services to private addresses and test from outside the VPC. Attaching a VPC does not automatically remove an existing public interface. Enforce host-level inbound and outbound rules on every node, including IPv6.

A NAT Gateway is a possible controlled path for updates or approved connectors, not evidence that egress is safe by itself. Customer private mode has no silent external inference fallback. Package/model downloads occur through a documented maintenance path; normal inference should not require arbitrary internet access.

Begin without Kubernetes unless actual operational needs justify its overhead. Containers are packaging/process isolation, not a complete sandbox for executing untrusted code. This product does not need model-generated code execution. Managed databases may be a future alternative for customers who choose managed operations, but the requested self-hosted database remains the default target.

## Data flow and trust boundaries

Browser text travels over TLS to the application's authenticated session. Server-side retrieval selects authorised records, constructs minimal context and calls the configured inference service. Model output is parsed against an action schema, then sent to the proposal service. Only the executor can perform allowlisted mutations, after permission and approval checks. The model never receives cloud management credentials or unrestricted SQL access.

Browser voice sends microphone audio to the customer-owned application, then to private STT. The resulting text follows the same proposal flow. Private TTS returns audio to the browser. Do not silently use browser speech recognition, operating-system cloud dictation or hosted speech APIs and call the result fully private. Text entry is the default fallback when a private speech path is unavailable.

Streaming HTTPS/WebSocket transport is a plausible first browser-voice implementation. If WebRTC is chosen later, document and test STUN/TURN routing, relay location, authentication and media encryption; a third-party relay is a separate privacy dependency. Browser permission, device settings and compromised endpoints remain outside the VPC's protection.

CRM imports, emails, calendar content, web pages and transcripts are untrusted input. They can supply facts but cannot change policy or approve actions. External connectors inherently transmit the approved data to their destination; explain that exception instead of claiming nothing ever leaves the network.

## Privacy and security requirements

Customer-owned means control over deployment and operations, not immunity from provider administrators, compromised hosts, lawful access, backups or user devices. A VPC is logical network isolation. Dedicated hardware would be a separately evaluated product choice; it does not follow from VPC attachment or a dedicated CPU plan. No absolute privacy or compliance claim is justified here.

- Minimise retained conversation data and personal attributes. Make raw audio retention opt-in and time-bounded; document transcript retention separately.
- Apply business scope and role checks to API reads, writes, exports, jobs and retrieval. Test cross-business access even if initial deployments are single-customer.
- Encrypt transport, including private service connections. Select storage and backup encryption with explicit key custody before a real customer pilot; do not assume the provider's encryption meets every customer requirement.
- Keep credentials outside source code, prompts, browser bundles and logs. Use separately scoped connector credentials and documented rotation. Credential creation/configuration is a later approved operational action.
- Disable raw prompt/audio logging by default. Record redacted operational metadata and request IDs. Logs, traces, crash dumps and backups are all part of the data inventory.
- Approval records bind payload, targets, versions and expiry. Reject stale approval after policy or content changes. Audit records identify who proposed, approved and executed an action.
- Use append-only audit permissions for application identities. Database administrators can still alter records; hash chaining alone is not tamper-proof. Stronger independent retention is a later customer decision.
- Define deletion across primary records, derivatives and expiring backups. A retained backup is not immediate erasure; explain retention honestly.
- Restrict connector destinations and outbound network paths. Prevent server-side requests to loopback, private metadata services and unapproved URLs. Do not let model text choose arbitrary network destinations.
- Require consent and a visible indicator for microphone use. Review applicable recording, marketing and sector requirements before real deployment; this design does not establish legal compliance.

## Reliability and operations

Database transactions protect local appointment and stock updates. External actions use outbox jobs, bounded retries and reconciliation states. A provider timeout is not permission to duplicate a message or booking. Connector outages cannot bypass staff approval or force external inference in private mode.

Define customer-specific recovery objectives before pilot. A provisional design target, not a promise, is encrypted daily full backups plus more frequent database recovery data where implemented, with an off-instance copy and a tested restore. Confirm storage location, retention, restore credentials and deletion behaviour. VM snapshots alone are not a validated database recovery plan.

Monitor service health, queue age, failed approvals/executions, inference errors, resource utilisation, backup age and certificate expiry without collecting unnecessary customer content. Establish who receives alerts and who can repair a deployment. Customer ownership without an operations owner creates an unmanaged risk.

Use pinned dependency and container versions, vulnerability review, reversible database migrations and an explicit rollback plan. Test the deployed version, not just local development. Maintain model license and provenance records and check commercial-use restrictions before choosing weights.

## Deployment sequence for later authorisation

1. Confirm final event rules or customer requirements, region, data residency needs, account access, spend cap, instance availability and inference mode.
2. Inventory public/private interfaces, ports, host firewall policy, egress destinations and administrative access. Verify VPC-only compatibility; no assumed AWS networking parity.
3. Provision only after approval. Configure secrets through an approved secure path, not this design workspace.
4. Deploy pinned application services and database migrations. Keep ingress closed until authentication and service bindings are checked.
5. Run isolation, permission, concurrency, injection, timeout, retry, audio-retention and backup-restore tests. Validate IPv4 and IPv6 exposure from an external vantage point.
6. Exercise the exact end-to-end demo or pilot workflow, recording version, topology, inference mode and actual test results.
7. Open the approved application endpoint, monitor, and document rollback and teardown. Verify resource deletion and continuing charges when the environment is retired.

## Acceptance gates and open questions

Hackathon gate: public browser app backed by Vultr; fictional data; real, identified inference path where claimed; approval and audit functioning; no database/model public exposure; clean documented setup; known gaps visible. Repository publication and deployment are future actions, not covered by writing these documents.

Private pilot gate: no unapproved outbound traffic; server-side database/LLM/STT/TTS processing stays within the declared customer deployment; encrypted browser input/output necessarily crosses the network; external connectors are separately declared; credential rotation and backup restoration tested; permissions and duplicate handling verified; latency and load measured on the selected hardware. Security review and operational ownership are required before calling the system production-ready.

Open decisions: target region and products, CPU/GPU sizing, model licenses and quality, STT/TTS fit, encrypted backup/key arrangements, administrative access, service recovery objectives, monitoring ownership, domain/certificate approach, connector providers and actual costs. None is silently resolved by the phrase “run it in a VPC.”

## Review additions: identity, isolation and operational gates

Authentication is not specified sufficiently for implementation yet. Select a maintained authentication component and decide local identity versus an explicitly declared identity provider. Require named staff accounts, administrative MFA, secure HttpOnly/SameSite cookies, CSRF protection for mutations, session rotation and revocation, rate-limited login and a documented recovery process. No ad hoc password system. Public enquiry access is scoped to submission and separately verified ownership; it cannot enumerate CRM records. An external identity provider is an additional privacy dependency.

Scope jobs, caches, exports, search indexes and backups as well as SQL rows. Use composite business/resource references and consider PostgreSQL row-level security as defense in depth, never as a substitute for service authorization. Service accounts receive minimum privileges; inference has neither database credentials nor execution authority. On a single demo VM, process/container networking is not a strong boundary against a compromised host or privileged container. Test host and container forwarding rules rather than assuming a host firewall covers published container ports.

For strict-private speech, prohibit external model, transcription, synthesis, telemetry and crash-report fallback. Capture only after consent; verify stop/disconnect handling and deletion of temporary audio, buffers and logs. STT/TTS, voice quality, languages, licenses and CPU capacity remain unselected and untested. Text-only is the safe scope fallback. Do not claim the browser or user device is inside the VPC.

Before spending, set an explicit total budget and owner, estimate compute-hours, persistent storage, backups, NAT, bandwidth and inference usage separately, and record prices and assumptions when authorized. Credits are not a cost cap. Apply application request/token/concurrency limits and an operational stop plan; provider alerts are not assumed hard limits. Document teardown verification and residual resources. No amounts or account-specific billing controls are verified here.

Set numerical RPO/RTO and retention with the owner before pilot. Restore an encrypted off-instance backup into an isolated environment, verify data and audit/outbox consistency, measure recovery time, and prevent replay of already-delivered effects. Keep key recovery and operator succession documented. Daily backups alone cannot promise a shorter recovery-point objective.
