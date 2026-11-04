# 14 · Sources and verified facts

Checked 2026-11-03/25 UTC from public pages and unauthenticated public API endpoints. No account, credential or paid resource was used. **Re-verify anything marked "re-check" when it is first relied on.**

## Event

| Fact | Source | Re-check |
|---|---|---|
| Vultr "Agent Rush" hackathon on lablab.ai; online build 3–8 Nov 2026; on-site final 8 Nov in Salt Lake City | [Event page](https://lablab.ai/ai-hackathons/vultr-hackathon) | Yes; the page currently says the schedule is "to be announced" |
| Kickoff 3 Nov 2026 15:00 UTC, submission 8 Nov 2026 22:00 UTC | [Live page](https://lablab.ai/ai-hackathons/vultr-hackathon/live), as recorded on 2026-11-03 in `design-review-cloud-20261103/PRE_EVENT_PROVENANCE.md` | Yes |
| Challenge 2 "Powering the Future of Work": enterprise multi-step workflow platforms; **Vultr VM backend and public web deployment required; Serverless Inference optional; no GPUs** | Event page | Yes |
| $200 Vultr credits per participant, gift code shared **at kickoff**, needs an email not previously linked to Vultr; covers Compute, Managed Databases, Kubernetes; payment method needed to keep running afterwards | Event page | Yes |
| Submissions must be original and MIT-compliant; public GitHub repo, demo URL, video, slides, architecture explanation required | Event page | Yes |
| Judging: application of technology, presentation, business value, originality | Event page | — |
| General guidance: core AI functionality should be built during the event; prior non-AI scaffolding generally allowed; event rules govern | [lablab general guide](https://lablab.ai/guide/ai-hackathons), [rule book](https://lablab.ai/hackathon-rules) | Ask organisers (Q1) |

## Vultr platform

| Fact | Source |
|---|---|
| Cloud Compute plans and monthly/hourly prices (e.g. `vhp-2c-4gb-amd` $24, `vhp-4c-8gb-amd` $48, `vhp-8c-16gb-amd` $96, `vc2-4c-8gb` $40) and 33 regions including `sjc` | Public API `GET https://api.vultr.com/v2/plans` and `/v2/regions` (fetched 2026-11-03) |
| Object Storage clusters include `sjc1.vultrobjects.com`, `sea1`, `ewr1/2`, `ams1/2`, `lhr1`, … | Public API `GET /v2/object-storage/clusters` |
| Object Storage tiers: Standard $18/month incl. 1,000 GB storage + 1,000 GB transfer; Premium $36; Performance $50; Accelerated $100 | [How is Object Storage billed](https://docs.vultr.com/support/platform/billing/how-is-object-storage-billed) (re-check) |
| Container Registry plans: Start Up free (10 GB), Business $5, Premium $10, Enterprise $20 | [Container Registry plans](https://docs.vultr.com/support/products/container-registry/what-are-the-available-plans-for-vultr-container-registry) (re-check) |
| Kubernetes Engine versions offered: v1.35–v1.37 | Public API `GET /v2/kubernetes/versions` |
| Managed PostgreSQL: price **not verified**; a third-party comparison claims an entry tier around $90/month | [InfraTally comparison](https://infratally.com/articles/vultr-vs-digitalocean-managed-databases-2026/) (unverified) |
| Serverless Inference: token-billed, docs quote $0.55 per million input and $2.75 per million output tokens as the general rate | [Usage and cost doc](https://docs.vultr.com/support/products/serverless/how-do-i-monitor-the-usage-and-cost-of-my-vultr-serverless-inference-subscription) |
| Serverless Inference chat endpoint `https://api.vultrinference.com/v1/chat/completions` (OpenAI-compatible); list models at `/v1/models` | [Chat endpoint doc](https://docs.vultr.com/products/serverless/inference/management/usage/chat) |
| Live model catalog (2026-11-03) includes text models with `tools` + `response_format` (`qwen3.8-flash-next`, `glm-5.3-flash`, `glm-5.3`, `deepseek-v4.1-flash`, `mimo-v2.6-flash-rl`, `mimo-v2.6-pro-rl`, `minimax-m3`, `qwen3.8-27b`, `muse-glimmer-30b`, `nemotron-3-nano-omni-30b-a3b-reasoning`), `nemotron-3.5-content-safety`, `qwen3-embedding-4b`, rerankers, `whisper-large-v3-turbo`, TTS (`chatterbox`, `qwen3-tts-12hz-1.7b`, `auk` evaluation-only), image/video models; data centres listed `sea`, `atl`, `sjc` | Public `GET https://api.vultrinference.com/v1/models` |
| A Vultr guide says tool calling is supported only on `kimi-k2-instruct` (which is no longer in the catalog). Treat it as outdated; our design does not depend on native tool calling | [Tool calling guide](https://docs.vultr.com/how-to-use-tool-calling-with-vultr-serverless-inference) |
| Serverless Inference provisioning includes acknowledging a charges note; the amount is not stated on the page | [Provisioning doc](https://docs.vultr.com/products/serverless/inference/provisioning) (re-check in console) |
| VPC for Cloud Compute; VPC-only instances with NAT gateway; Vultr Firewall filters ingress only | Links in `design-review-cloud-20261103/DESIGN_REVIEW.md` |
| Terraform: official provider `vultr/vultr`; reference docs for resources such as `vultr_vpc`, firewall groups and rules, object storage tiers | [Terraform VPC resource](https://docs.vultr.com/reference/terraform/resources/vpc.md), [Firewall rules](https://docs.vultr.com/products/network/firewall-groups/management/rules.md) |

## External models (owner's shortlist)

| Fact | Source | Re-check |
|---|---|---|
| "GPT Luna" is GPT-5.6 Luna, the smallest and lowest-cost tier of OpenAI's GPT-5.6 family (third-party listings; prices vary by listing) | [OpenRouter listing](https://openrouter.ai/models/openai/gpt-5.6-luna-20260709), [Vercel AI Gateway](https://vercel.com/ai-gateway/models/gpt-5.6-luna) | Yes: confirm the ID and price on OpenAI's site |
| Claude Haiku 5.5 model ID `claude-haiku-5-5` | Anthropic model list available to this tool environment | Confirm price |
| Gemini Flash tier: current model ID not verified here | — | Yes |

## Earlier verified sources (still valid)

See [docs/SOURCES.md](../SOURCES.md) (superseded but kept) and `design-review-cloud-20261103/DESIGN_REVIEW.md` for the VPC, NAT, firewall and managed-database links checked on 2026-11-03.
