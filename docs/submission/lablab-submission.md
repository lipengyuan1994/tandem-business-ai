# lablab.ai submission: Tandem Business AI by OpenDL

Vultr "Agent Rush" hackathon, Challenge 2 (Future of Work). Deadline **8 Nov 2026 22:00 UTC**. The owner submits;
every field below is ready to paste. Items marked **OWNER** need an account or a publishing step.

## Project title

Tandem Business AI by OpenDL

## Short description (as submitted, updated 2026-11-08)

Not a prototype: a production-ready AI front desk and back office for small businesses. Seven AI agents work under human approval, and one script deploys the whole system to a customer's own Vultr account with Docker and Terraform.

## Long description (as submitted, updated 2026-11-08; lablab limit 2,000 characters)

Tandem Business AI is a production-ready product, not a proof of concept. The public demo runs the same release images any customer gets. One install script deploys the complete system (React front end, FastAPI back end, PostgreSQL, worker, Keycloak sign-in, Caddy with automatic HTTPS) to a business's own Vultr account in about 30 minutes: Terraform provisions the VM and its firewall, versioned Docker images come from the Vultr Container Registry, and backups go to the customer's Object Storage. Upgrades run a backup and a smoke test and roll back automatically on failure. Encrypted backups have a tested restore. Staff sign in with an authenticator app; each business's data is isolated in the database. Over 2,400 backend tests (96% coverage), a load test (20 staff, 204 ms p95, 0 errors) and a security review back it.

The product is a complete ERP for small businesses (customers, schedule, stock, inbox, sales, purchasing, marketing, knowledge base) with seven AI agents, each with a job, a switch, a budget and limits: Front Desk answers customers from the business's own knowledge after a fact check and the owner's plain-language guardrail; Customer Care drafts check-ins, recalls and complaint replies; Marketing drafts campaigns and respects consent; Inventory suggests reorders; Purchasing requests and compares supplier quotes; Finance drafts overdue-invoice reminders; Operations Manager writes the daily briefing and routes work. Staff hand tasks to any agent with "@", by text or voice.

People stay in control: agents propose, staff approve with evidence, nobody approves their own request, every chain has hard limits and everything is audited. Emergencies go to a person before any AI runs.

Every model call uses Vultr Serverless Inference. The non-AI ERP was built before the event and disclosed; all AI was built during it. The demo uses synthetic data and simulated email; a customer install sends real email once its mail server is configured.

## Technologies / tags

Vultr, Vultr Serverless Inference, Vultr Compute, Vultr Object Storage, Vultr Container Registry, Terraform, AI agents,
multi-agent, human-in-the-loop, FastAPI, Python, React, TypeScript, PostgreSQL, Keycloak, Caddy, Docker, Whisper,
DeepSeek, Qwen, RAG, small business, ERP

## Links

| Field | Value |
|---|---|
| Demo / application URL | https://tandemai.open-dl.com |
| Customer-facing page | https://tandemaidentaldemo.open-dl.com |
| Judge login | prefilled on the sign-in page (`judge@example.com`), one click, no authenticator code |
| Repository | **OWNER**: `https://github.com/<your-account>/tandem-business-ai` after pushing (public, MIT) |
| Video | **OWNER**: upload `frontend/output/video/tandem-demo-v2.mp4` (or the short cut) and paste the link |
| Slides | `docs/submission/tandem-slides.pdf` |
| Cover image | `docs/submission/cover.png` (1920 × 1080) |

## Owner steps before 22:00 UTC

1. Push the repository and make it public (the command is in the owner checklist); paste its URL above and in the
   form.
2. Upload the video (YouTube or as lablab allows) and paste the link; add it to the README **Demo** line.
3. Submit on lablab.ai with the title, descriptions, tags, cover, slides, video, repository and demo URL.
4. After submitting: no feature changes on the demo VM (freeze); keep it running through judging.
