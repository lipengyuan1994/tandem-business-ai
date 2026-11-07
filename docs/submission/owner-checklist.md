# Owner checklist: from 0.1.11 to submission

Everything below needs the owner (accounts, publishing, recording). The product side is done: version 0.1.11 runs on
`https://45-63-85-83.sslip.io`, all P10 work is released, and the submission documents are current (2026-11-07).

## 1. Rehearse twice on the demo URL (P6-04)

- Get the staff passwords privately from `install-smb-demo/demo-logins.txt` (local, never commit or paste it). Each
  account asks for an authenticator app (TOTP) at first sign-in.
- Follow `docs/demo-script.md`: main flow about 5 minutes, then the failure moments. Steps marked as live AI calls
  can vary; if one gives no suitable proposal, say so and show the manual path.
- Before each run: reset the demo businesses with the operator procedure in the script, and copy a fresh supplier link
  for the procurement step (it changes after every reset).
- Write down anything that needed a manual fix. Two clean runs close P6-04.

## 2. Record the demo video (P7-03)

- Use the rehearsed script; crop the browser address bar during the supplier-link step (the link is a credential).
- Synthetic data only is on screen; the dental banner about real patient data must stay visible on clinic screens.

## 3. Publish the repository (P7-01)

- Checked 2026-11-07: gitleaks finds no secrets in any of the commits; no private files are tracked
  (`install-smb-demo/`, `.env`, `.demo-logins/`, `frontend/e2e/.auth/` are ignored); MIT `LICENSE` present.
- Decide on the commit author e-mail: all commits are authored as `Pengyuan Li <pli396@gatech.edu>`, which becomes
  public with the repository. Changing it means rewriting history, which the project rules forbid without your
  explicit decision.
- Replace the root `README.md` with `docs/submission/README-judges.draft.md` (add the video link first).
- Create the GitHub repository and push (Claude needs your explicit go-ahead in chat for remotes and pushing).

## 4. Slides and submission (P7-04, P7-05)

- Finalise `docs/submission/slides-outline.draft.md` and confirm the business-model prices on slide 7.
- Submit on lablab.ai with the repository, demo URL, video and slides.

## 5. Freeze (P7-06)

- After submission, no feature changes on the demo VM; only fixes for breakage, each recorded in `EVENT_WORK.md`.

## After the event (recommended next engineering work)

- P8-09 host-level egress filtering (raw container egress is still open; deliberately not changed before submission
  to avoid risking the demo).
- Real SMTP for the demo so magic links and reminders are delivered, then compare recorded AI costs with the Vultr bill.
