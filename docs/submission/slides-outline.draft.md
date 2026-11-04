<!-- DRAFT for P7-04 (owner finalises). 8 slides, about 4 minutes. [AFTER KICKOFF] marks facts that need measurement. Updated 2026-11-03: sales, purchasing and procurement added (slides 1 to 6 and 8). The assistant does not exist before kickoff (3 Nov 2026 15:00 UTC): every assistant step below is planned, and the deck is final only when each one is checked against the working demo. -->

# Slides outline

1. **Problem.** A 5-person shop answers messages, books jobs, orders parts, chases suppliers and chases customers for payment by hand, across 4 tools. Enterprise ERPs are too heavy, and SaaS means handing customer data to a vendor.
2. **Product.** One app for customers, schedule, stock, inbox, marketing, sales, purchasing and approvals. It installs on the shop's own Vultr account. Screenshot: Today (replies, approvals, low stock, a late purchase order).
3. **Live flows** (the demo; five minutes at most, script in `docs/demo-script.md`):
   - Flow 1, a message becomes a booking:
     - A customer's message comes in.
     - The assistant finds a real slot and the part, and drafts a reply (planned).
     - A person approves.
     - The timeline and stock update.
   - Flow 2, procurement (a focus area of the challenge):
     - Today shows a late purchase order.
     - Three suppliers were asked for a quote. Two have answered; the third answers on a phone.
     - The comparison ranks them with a score computed in code. The cheapest supplier does not win: it is slow and unreliable.
     - The assistant reads a pasted free-text reply, checks every number against the supplier's text and explains the ranking (planned).
     - "Create purchase order" makes a proposal, and a different person approves it.
   - [AFTER KICKOFF: timing] [UPDATE after kickoff: which assistant steps shipped]
4. **Architecture on Vultr.**
   - One Compute VM runs Compose.
   - Serverless Inference behind a provider-agnostic gateway (planned).
   - Object Storage for backups, Container Registry for images, Terraform for installs.
   - Nothing runs on Vultr yet. [AFTER KICKOFF: replace with what runs on the demo URL]
5. **Safety by construction.**
   - The agent only proposes (planned).
   - Approvals with separation of duties and an audit trail.
   - Row-level security per business.
   - Consent is never inferred.
   - Injection attempts are treated as data, in customer messages and in supplier replies (planned). Show the failure moments from the demo.
   - Numbers come from code. Prices, totals and the quote ranking are computed, not generated. A number read from a supplier's reply must appear in the supplier's own text (planned).
6. **Evals and cost.** The thresholds, the models compared, the chosen route and the cost per intake run. A procurement suite too: reading replies, explanations, injection inside supplier text. [AFTER KICKOFF]
7. **Business model.** Each customer owns the install: a one-time setup plus a support subscription. The shop pays Vultr directly, from about $24 a month for the VM plus inference by usage. [owner to confirm prices]
8. **Roadmap and honesty.**
   - Built before kickoff, disclosed and logged with UTC times: the ERP, including sales, purchasing and the procurement quote flow, with no AI code, plus the written design of the AI layer (documents only). Built at the event: the assistant, the evals and the first Vultr deployment.
   - Not built yet: automatic retention clean-up, point-in-time recovery, file attachments, inbound e-mail, direct accounting integrations.
   - Next: those items, a real e-mail provider in use, and a pilot with real shops in January 2027.
