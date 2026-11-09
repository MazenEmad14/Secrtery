# NexusAI — Product Roadmap

Companion to the vision, business model and technical audit. The roadmap is built so that **each phase can be stopped, pivoted or continued based on evidence**; later phases are deliberately less detailed than earlier ones.

Labels: **[Recommendation]** unless marked. Effort figures are **[Estimate]** for one to two engineers who already know this codebase; they are ranges, not commitments. "Customer value" statements are hypotheses until the metrics prove them.

Guiding rules:
1. Do not build everything before launching. Each phase ends with real users and a decision.
2. Reliability and trust before breadth. A reminder that always fires beats ten features that sometimes work.
3. Reuse what exists (auth, sessions, graph, approvals, queue, tests). Rewrite nothing that works.
4. Every phase has acceptance criteria *a customer could check* and success metrics *the business can measure*.

---

## Phase 0 — Validate and de-risk (weeks 0–4; mostly not code)
Detailed in `NEXUSAI_90_DAY_EXECUTION_PLAN.md`.
- **Business objective:** confirm that owner-managers in the target segment feel the problem and will commit money; start the long-lead platform items.
- **Work:** 25+ customer interviews; a landing page with a waitlist and price test; a concierge pilot (manually assisted ledger and brief for 5–10 businesses using today's product); choose the payment route; start the Google OAuth verification and security-assessment path; a Saudi/Egypt legal check on data transfers.
- **Technical requirements:** none beyond keeping the current app running; fix reminder delivery as a trust baseline.
- **Complexity:** low.
- **Acceptance:** interview synthesis document; at least 10 prospects who accept a *paid* pilot or sign a letter of intent; payment route decided; Google verification submission started.
- **Success metrics / go-no-go:** see §Decision gates (G1).

---

## Phase 1 — Commercial MVP: "Never drop the ball" for one customer type (weeks 4–14)
- **Features**
  1. **Reminders that fire** with delivery on web push/email (and WhatsApp when ready).
  2. **Commitments ledger v1** from Gmail: extract commitments and waiting-on items, store with source links, manual edit and close, per-person view.
  3. **Morning brief** (email and web first): top items needing the user, today's meetings, overdue and stale items.
  4. **Follow-up drafts** for stale waiting-on items with one-tap approval.
  5. **Arabic and English** quality pass: translated classification and extraction prompts, evaluation sets in both languages, RTL fixes.
  6. **Trust baseline:** mandatory token encryption, audit log (view + export), idempotent send/create, per-user cost budget and rate limits, account deletion and export, email verification.
- **Customer value:** the owner sees a trustworthy list of what they owe and are owed, and gets chased items drafted for them.
- **Technical requirements** (from the audit)
  - Due-reminder job and persistent notification store on the existing queue/worker.
  - New collections: `commitments`, `contacts` (lightweight), `actions_log`, `usage`.
  - Move the periodic inbox check onto per-user queue jobs using Gmail incremental sync; persist "seen" state.
  - Mandatory `TOKEN_ENCRYPTION_KEY` with rotation procedure; secrets management.
  - Networked Qdrant (or drop vectors for the ledger and keep embeddings only for the knowledge base).
  - Pricing page + checkout via the chosen payment route; plan and usage tables; metering middleware.
  - Staging environment, backups, error tracking, uptime alerts.
- **Dependencies:** Google OAuth consent screen and verification progress (the product can run with up to 100 test-listed users and weekly re-consent while verification is pending, which is acceptable for a paid pilot but not for launch); payment route; legal review of data handling.
- **Complexity:** medium. Roughly 8–10 engineer-weeks for items 1–5, plus 3–4 for the trust baseline [Estimate].
- **Acceptance criteria** (testable)
  - A reminder set for a time arrives within 60 seconds of due time on at least one channel, in 99% of 200 test reminders.
  - On a labelled set of 200 real (consented, anonymised) emails, commitment extraction reaches **precision ≥ 80%** and **recall ≥ 70%**, in Arabic and English separately; the user can correct entries in two taps.
  - The brief is generated and delivered for every active user before their configured time on 28 consecutive days in staging.
  - No external action executes without an approval record; replaying an approval does nothing; a forced network failure mid-send never produces a double email in 100 fault-injection runs.
  - Deleting an account removes tokens, ledger, messages and files, verified by a script.
  - p95 monthly AI cost per active user stays under $6 in the pilot.
- **Success metrics:** activation (mailbox connected and first ledger viewed within 24 hours) ≥ 60% of invited pilot users; weekly brief opens ≥ 4 of 7 days for ≥ 50% of active users; ≥ 70% of surveyed pilot users say they would be "very disappointed" to lose it (a standard product-fit question, not proof); ≥ 5 of 10 pilot businesses pay after the pilot.

---

## Phase 2 — First paying customers and the WhatsApp channel (weeks 14–26)
- **Features**
  1. **WhatsApp control channel** on the official Business Platform: morning brief out, voice-note capture in ("remind me…", "what's open with Ahmed"), one-tap approvals, opt-in and templates compliant with Meta rules.
  2. **Calendar actions + meeting prep**: conflict detection, prep notes (last interactions, open commitments with attendees).
  3. **Autonomy levels** 0–2 with allow-lists.
  4. **Subscriptions**: plans, annual billing, upgrades, invoices, tax, dunning; usage page; regional pricing.
  5. **Onboarding and analytics**: guided first-run (connect, see first ledger in minutes), event analytics, support tooling, in-app feedback.
  6. **Reliability**: staged rollouts, regression evals for extraction and drafting, on-call basics.
- **Customer value:** the product meets the user where they work and starts to feel like an employee.
- **Technical requirements:** WhatsApp Business Platform integration with a webhook receiver, template management, per-message cost tracking; hosted STT (replace local Whisper) and a production TTS; billing integration; feature flags; metrics and tracing.
- **Dependencies:** WhatsApp business verification and template approval (lead time unknown, start in Phase 0/1); the payment route; Google verification status for scaling beyond 100 users.
- **Complexity:** medium–high. Roughly 12–16 engineer-weeks [Estimate].
- **Acceptance criteria:** a user can approve a follow-up from WhatsApp in under 10 seconds and the audit log shows it; voice notes in Egyptian and Gulf Arabic are transcribed and filed to the ledger with ≥ 90% correct capture on a 100-note test set (target to be calibrated in Phase 1); billing handles upgrade, downgrade, cancel, refund and failed payment correctly in test and live modes; a cost dashboard shows per-user spend.
- **Success metrics:** 50–150 paying customers; paid-conversion from trial ≥ 15% [target to be calibrated on pilot data]; month-3 logo retention ≥ 80%; gross margin ≥ 70%; support tickets per 100 active users < 20 per month.

---

## Phase 3 — The proactive secretary (months 6–10)
- **Features**
  - **Personal Context Engine**: editable profile, VIP contacts, tone samples, standing rules, learned preferences with provenance.
  - **Long-term memory**: episodic notes (decisions, meeting summaries), retrieval by person/project.
  - **Meeting intelligence (careful):** import notes or recordings the user supplies, calendar-attached transcription only with explicit all-attendee consent flow; decisions and action items flow to the ledger.
  - **Proactive monitoring v2**: unanswered important mail, schedule conflicts, at-risk commitments, "next best action".
  - **Outlook / Microsoft 365 connector** (after confirming its review requirements).
  - Autonomy level 3 for proven allow-listed actions.
- **Customer value:** the secretary knows the user and anticipates; moves from "tracks" to "acts".
- **Technical requirements:** memory store with provenance and user-visible editing; evaluation harness for proactive suggestions (precision of "needs attention"); Graph API integration; consent-management for recordings.
- **Dependencies:** Phase 2 traction; decision (from data) on whether meetings are a priority or a distraction.
- **Complexity:** high; 20+ engineer-weeks [Estimate].
- **Acceptance criteria:** proactive suggestions are accepted ≥ 60% of the time in the pilot cohort (below that, tune or switch off per type); user can view, edit and delete every memory item; meeting artifacts always show consent state; Outlook parity for ledger and brief.
- **Success metrics:** ≥ 300 paying customers; month-6 retention ≥ 70%; share of users with ≥ 20 active ledger items ≥ 60%; NPS-style score ≥ 40 [targets to calibrate].

---

## Phase 4 — Business and Team edition (months 9–14)
- **Features:** workspaces and memberships; shared ledger and delegation ("assign to Mona"); roles and permissions; team brief; admin console (seats, connected accounts, audit export, policies); central billing; SSO option; manager dashboard (commitments by owner, overdue by person).
- **Customer value:** the company, not just the founder, stops dropping the ball.
- **Technical requirements:** tenant model across all collections (every query scoped by workspace and user); policy engine (who can see whose mail-derived items; default private); audit extension; SCIM/SSO (later); migration of single-user accounts into workspaces.
- **Dependencies:** evidence that individual customers invite colleagues; legal review of employee-data visibility.
- **Complexity:** high; 20–30 engineer-weeks [Estimate]. The largest data-model change in the plan, so it comes after the product is proven.
- **Acceptance criteria:** cross-tenant isolation tests (adapt the existing cross-user tests to workspaces); no ledger item derived from a private mailbox is visible to teammates unless the owner shares it; admin can export and delete a leaver's data.
- **Success metrics:** 30–50 team accounts; seats per team ≥ 4 on average; net revenue retention ≥ 100%.

---

## Phase 5 — Enterprise and platform expansion (months 14+)
- **Features:** in-region deployment option (Saudi), SSO/SCIM, data residency controls, DPA and security documentation, SOC 2 or ISO 27001 path, public API and webhooks, CRM and ticketing integrations, vertical templates where evidence concentrates (leading hypothesis: real estate brokerages).
- **Technical requirements:** deployment automation for isolated regions; extensible integration framework with per-integration permissions and rate limits; documented API with scopes.
- **Dependencies:** enough revenue and customers asking; a security assessment history.
- **Complexity:** very high; treat as a separate business case once Phase 4 metrics are met.
- **Success metrics:** first enterprise contract above $25,000 per year; integration usage in ≥ 30% of Team accounts.

---

## Decision gates (continue, pivot or stop)
| Gate | When | Continue if | Pivot if | Stop if |
|---|---|---|---|---|
| **G1 Problem and willingness to pay** | End of week 4 | ≥ 10 of 25 interviewees describe dropped follow-ups as costly **and** ≥ 5 businesses agree to a paid pilot or sign a letter of intent; landing page converts ≥ 5% of qualified visitors to a waitlist with a stated price | Pain is real but in another segment (for example agencies or brokerages): re-target before building | Almost nobody will commit money, or pain is only "annoying" |
| **G2 Pilot value** | End of week ~14 | ≥ 5 of 10 pilot businesses pay after the pilot; weekly brief use ≥ 50%; extraction quality targets met | Value appears in a different module (for example meeting follow-ups): re-prioritise | Pilots like it but will not pay, and a price/packaging change does not help in two attempts |
| **G3 Early revenue** | Month 6 | ≥ 50 paying customers; month-3 retention ≥ 80%; gross margin ≥ 70% | Retention fine but acquisition too costly: shift to partnerships or a vertical | Retention < 60% or margin < 50% with no path to fix |
| **G4 Scale decision** | Month 10 | ≥ 300 customers, retention ≥ 70%, evidence of team demand | Team edition demand weak: deepen individual/vertical product | Growth stalls and CAC payback exceeds 12 months |

---

## Cross-cutting engineering backlog (from the audit; do in this order)
1. Reminder delivery and persistent notifications.
2. Mandatory token encryption, audit log, idempotent actions, account deletion/export.
3. Usage metering, budgets, rate limits; plans and billing.
4. Per-user queue jobs for periodic work; Gmail incremental sync; persisted alert state.
5. Hosted STT/TTS; networked vector store.
6. Observability: metrics, tracing, alerts; CI as the merge gate (run it on GitHub, fix mypy findings gradually).
7. Frontend: split the 1,200-line workspace page before adding the Today and Ledger views.
