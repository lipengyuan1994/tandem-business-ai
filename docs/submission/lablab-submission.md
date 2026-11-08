# lablab.ai submission: Tandem Business AI by OpenDL

Vultr "Agent Rush" hackathon, Challenge 2 (Future of Work). Deadline **8 Nov 2026 22:00 UTC**. The owner submits;
every field below is ready to paste. Items marked **OWNER** need an account or a publishing step.

## Project title

Tandem Business AI by OpenDL

## Short description (one line)

An AI front desk and back office for small businesses on Vultr: seven agents answer customers, book, restock and
follow up, while people approve everything that matters.

## Long description

Small businesses juggle customer messages, bookings, stock, suppliers, invoices and follow-ups across too many tools,
and most AI products either do too little or act without asking. Tandem Business AI is a complete, customer-owned
ERP with an AI team built in. Each business installs it on its own Vultr account.

**What judges can try:** the public demo at https://tandemai.open-dl.com (one-click judge login, three synthetic
businesses) and the clinic website at https://tandemaidentaldemo.open-dl.com, which embeds the chat.

**Seven agents, each with a job, a switch, a budget and limits:**
- *Front Desk* answers customers from the business's own knowledge base, with citations. Before any automatic
  reply, a fact check and the owner's own guardrail (written in plain words) must pass; otherwise a person approves.
  Possible emergencies are handed to a person before any AI runs.
- *Customer Care* drafts after-visit check-ins, recall reminders and complaint replies, using only the facts it
  needs, and refuses to promise refunds it cannot verify.
- *Marketing* drafts campaign emails; patients or customers without marketing consent are excluded automatically.
- *Inventory* watches stock against reorder points; *Purchasing* requests and compares supplier quotes (code ranks
  them) and prepares purchase orders; *Finance* drafts overdue-invoice reminders.
- *Operations Manager* writes the morning briefing and routes work; staff can hand a task to any agent with "@".

**People stay in control.** Agents propose; staff approve in one queue with evidence. Nobody approves their own
request (four-eyes rule). Every hand-off chain has hard limits (6 steps, 30 model calls, 10 actions, $0.50), every
action is in the audit log, and AI-sent messages wait in a stop window. Owners set autonomy per action: off, ask,
act and notify, or act, with shadow mode first.

**Customers get service without accounts:** website chat, appointment requests, and self-service rescheduling or
cancelling through an emailed link.

**Built on Vultr:** one Compute VM (Docker Compose, provisioned with Terraform), Vultr Serverless Inference for every
model call (mimo, DeepSeek and Qwen models through a routing gateway, Qwen embeddings for search, Whisper for voice
dictation), Object Storage for encrypted backups with a tested restore, and the Container Registry for versioned
images. The whole public demo has cost under $10 of the $200 credit.

**Measured:** synthetic evaluation suites on live Vultr models: reply guardrail 16/16 (two runs), prompt injection
15/15, grounding 10/10, retrieval 8/8, intake 76/78, Customer Care 18/19. Over 2,400 backend tests at 96% coverage.

**Honest scope:** the non-AI ERP was built before the event and disclosed; all AI was built during the event
(dated logs in the repository). The demo uses synthetic data and simulated email; it is not a compliance
certification for real patient data.

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
