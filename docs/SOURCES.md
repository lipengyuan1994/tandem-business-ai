> **Superseded on 2026-11-03 (UTC).** Historical pre-event draft kept for provenance. The current plan is [14-sources.md](roadmap/14-sources.md). Do not implement from this file.

# Source verification — 2026-11-03 UTC

Public pages read without account or credential access. Product documentation supports availability of a capability in general, not quota or provisioning in a particular account. Recheck at kickoff.

| ID | Source | Observed evidence and limits |
|---|---|---|
| E1 | [Vultr hackathon](https://lablab.ai/ai-hackathons/vultr-hackathon) | Event GPUs unavailable. Challenge 1 requires Vultr Serverless Inference; Challenge 2 makes it optional. Both require a Vultr VM backend and web deployment. These are eventual submission requirements, not actions performed now. |
| E2 | [Live dashboard](https://lablab.ai/ai-hackathons/vultr-hackathon/live) | Start shown as November 3, 2026, 15:00 UTC. |
| E3 | [General hackathon guide](https://lablab.ai/guide/ai-hackathons) | FAQ generally permits prior non-AI scaffolding but expects core AI functionality during the event; specific rules govern. No event-specific early-build exception verified. |
| V1 | [VPC-only instances and NAT gateway](https://docs.vultr.com/how-to-deploy-vpc-only-instances-with-nat-gateway-on-vultr) | Documents private instances without public IPs and outbound access through NAT. Does not establish comprehensive privacy or current account eligibility. |
| V2 | [PostgreSQL VPC attachment](https://docs.vultr.com/products/storage/databases/postgresql/management/connection/vpc-networks) | Documents attaching a VPC for private application/database connectivity. |
| V3 | [Managed DB connection troubleshooting](https://docs.vultr.com/support/products/managed-databases/how-to-fix-the-connection-refused-error-when-connecting-to-a-vultr-managed-database) | Distinguishes public/trusted-source and private-hostname paths; private clients must match account, region and VPC. Do not infer public endpoint removal from VPC attachment. |
| V4 | [Cloud GPU provisioning](https://docs.vultr.com/products/compute/instances/cloud-gpu/provisioning) | GPU instance provisioning is a documented product capability, separate from event access. |
| V5 | [Code Llama on Vultr GPU](https://docs.vultr.com/how-to-deploy-code-llama-on-vultr-cloud-gpu) | Example self-hosted model runtime on GPU. Evidence of the pattern, not a selected model or measured SMB workload fit. |

Architecture recommendations beyond these observations are design proposals. No AWS-equivalent IAM, network encryption, hardware dedication, compliance, data residency, backup isolation, speech performance or price guarantee has been verified. No model downloads or documentation commands were executed.
