# Demo video storyboard: "A busy week at Example Cycle Works"

Target length about 5 minutes, English voice-over, recorded on the public demo `https://tandemai.open-dl.com`.
All people, e-mail addresses and companies are synthetic. Live AI steps can vary; the narration describes what the
recording actually shows.

## Cast (synthetic)

- **Lena Park** (`lena.park@example.com`), a commuter whose brakes squeal. Customer on the public chat.
- **Example Courier Co.** (Marta Lopez, `fleet@courier.example.com`), a delivery company with six e-bikes. Business
  inquiry through the appointment request form.
- **Morgan Example**, owner of Example Cycle Works (staff app).
- **Riley Example**, admin (second person who approves what Morgan's assistant proposed).
- Example Dental Clinic's public chat for the safety moment.

## Scenes

| # | Scene | Where | What it proves |
|---|---|---|---|
| 0 | Title card | — | Product name, one-line pitch, built on Vultr |
| 1 | Lena asks what a tune-up includes and the holiday hours | Public chat (phone width) | Front Desk answers from the business's own knowledge base, with sources, automatically after the guardrail allows it |
| 2 | Lena asks for a brake service on a weekday morning and for 20 % off | Public chat | Real availability from code; the booking and the discount answer wait for staff (business guardrail) |
| 3 | Example Courier Co. asks for brake service on six e-bikes next week | Public request form | Business inquiry captured as a request, no account needed |
| 4 | Morgan signs in: Today shows what needs attention | Staff app | One place for the day's work |
| 5 | Inbox and Approvals: the auto-sent answer with its sources; the held reply with the guardrail reason; Morgan edits the reply and approves the booking | Inbox, Approvals, Schedule | People stay in control; evidence on every proposal |
| 6 | Morgan asks the assistant whether there are enough brake pads and technician hours for the courier job | Assistant | Tools read stock, reservations and availability; the assistant proposes a quote and supplier quote requests |
| 7 | Supplier answers; the comparison ranks quotes by price, delivery and reliability (code, not AI); purchase-order proposal | Purchasing | AI reads messy supplier text, code decides the numbers |
| 8 | Riley approves the purchase order Morgan's assistant proposed | Approvals (Riley) | Four-eyes approval; audited |
| 9 | AI Team and Autonomy activity: agents, budgets, hand-offs, what was sent automatically and what fell back and why | AI → Team, Autonomy | Transparent agents with limits |
| 10 | Dental clinic chat: "My face is swelling and I have severe pain" | Public chat (clinic) | Emergency hand-over; the AI never answers or books it |
| 11 | Closing card: architecture on Vultr (Compute, Serverless Inference, Object Storage, Container Registry, Terraform), measured eval scores, cost of the run | — | Built on Vultr, measured, cheap |

## Logins needed (owner signs in; Claude never types passwords)

- Morgan: `morgan@example.com` (scenes 4–7, 9).
- Riley: `riley@example.com` (scene 8). Same browser pane: sign out of Morgan first.
- Passwords are in `install-smb-demo/demo-logins.txt` (local, private). Each account asks for an authenticator app at
  first sign-in.
