# Demo video storyboard (v2): "A week at Example Dental Clinic"

Target about 8 minutes, English voice-over (local Kokoro voice), recorded on the public demo
`https://tandemai.open-dl.com` and the clinic website `https://tandemaidentaldemo.open-dl.com`. All people, e-mail
addresses and companies are synthetic. Live AI steps can vary; the narration describes what the recording shows.

v1 (2026-11-07, 5:35) followed Example Cycle Works. v2 follows the dental clinic, whose website is the customer-facing
page judges open, shows all seven AI agents at work, and keeps the Cycle Works scenes as a second business that uses
automatic replies and two-person purchasing.

## Cast (synthetic)

- **Sam Rivera** and **Priya Shah**, patients writing on the clinic website.
- **Ellery Example**, owner of Example Dental Clinic (staff app; first sign-in with authenticator setup).
- Cycle Works: **Lena Park**, **Marta Lopez** (Example Courier Co.), **Morgan** (owner) and **Riley** (admin).

## Scenes

| # | Scene | Where | What it proves |
|---|---|---|---|
| 0 | Title | — | Product, pitch, built on Vultr |
| 1 | Clinic website: a patient browses, opens "Chat with us", asks about whitening, then reports swelling and severe pain | Clinic website (desktop) | Embeddable chat on a business's own site; emergencies handed to a person at once with the phone number; no AI answer |
| 2 | Judges' sign-in (prefilled) and Ellery's first sign-in with authenticator setup (QR blurred) | Sign-in | Easy judge access; staff accounts use a second factor |
| 3 | Today: the Operations Manager's briefing, low stock for gloves and prophy paste | Today | One page for the day; numbers from the clinic's own records |
| 4 | Inbox: Sam's emergency is with a person; the AI's earlier whitening draft was withdrawn automatically | Inbox | AI stops when a person must handle it |
| 5 | Priya asks about Saturdays and a first exam; the clinic is closed; Ellery asks for a summary; the drafted reply cannot be approved by the person who asked for it; Ellery answers herself | Inbox, Approvals | Summaries, four-eyes rule (Settings → Policies), people stay in control |
| 6 | Ellery speaks a question to the assistant (voice dictation, Whisper on Vultr) | Assistant | Voice input; answers from tools over live records |
| 7 | Agents at work: Inventory (reorder draft), Purchasing (quote requests and comparison), Customer Care (after-visit check-in and recall), Marketing (campaign draft), Finance (overdue-invoice reminder), Operations Manager (hand-off chain with cost) | Inventory, Purchasing, Schedule, Approvals, Marketing, Sales, AI → Team, Activity | Seven agents, each with a job, switch, budget and visible results |
| 8 | Patients, Schedule, Knowledge, Audit log, Settings (website, guardrail, policies), System health | Staff app | A complete back office |
| 9 | A patient reschedules their own booking through the emailed link (simulated email) | Clinic website → bookings | Customer self-service with the clinic's own rules |
| 10 | Cycle Works: automatic answers after the owner's guardrail, fleet request, held draft, second approver for purchasing | Existing v1 footage | The same platform at a different business, with automatic replies |
| 11 | Closing: architecture on Vultr, eval scores, cost | — | Built on Vultr, measured, cheap |

## Recording log

- 2026-11-08: clinic website scene recorded (`raw/clinic.webm`); staff tour as Ellery recorded (`raw/staff-tour.webm`,
  marks in `raw/staff-tour.events.json`). Rendered `tandem-demo-v2.mp4` (9:38) and `tandem-demo-short.mp4` (6:09) in
  `frontend/output/video/` (not committed). The authenticator QR code and the emailed sign-in and supplier links are
  pixelated. Finance is shown at Cycle Works (the clinic has no invoices). The clinic's front desk hands every
  question to a person, so the "AI answers automatically" moment uses the Cycle Works scene from v1.
