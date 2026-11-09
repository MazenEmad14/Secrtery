# NexusAI — Product Vision

Companion documents: `NEXUSAI_MARKET_RESEARCH.md` (evidence), `NEXUSAI_TECHNICAL_AUDIT.md` (what exists), `NEXUSAI_BUSINESS_MODEL.md`, `NEXUSAI_PRODUCT_ROADMAP.md`, `NEXUSAI_90_DAY_EXECUTION_PLAN.md`.

Labels: **[Verified]** from the repository or vendor pages · **[Assumption]** to be validated · **[Estimate]** · **[Recommendation]**. Nothing here is a claim of demand: demand is something the validation plan must *prove*.

---

## 1. The one-sentence product

> **NexusAI is the Arabic-and-English chief of staff for owner-managers: it keeps a ledger of everything you promised and everything people promised you, chases what is slipping, and tells you each morning — on WhatsApp — the five things that need you today. It never sends or books anything without your approval unless you tell it to.**

That sentence is the filter for every feature. If a feature does not make the ledger more complete, the follow-up more reliable or the morning brief more useful, it waits.

### Why this and not "an AI assistant"
The technical audit's verdict is blunt: today's NexusAI is a good chat assistant with tools. Chat assistants are a commodity; suites bundle them. Competitors that sell "AI secretaries" are priced at $37–$99 per user per month (Fyxer) and are praised for routine triage but criticised for drafts that need rewriting and for unpredictable credit billing (Lindy). The market-research document finds **no product I could identify that targets Arabic-speaking owner-managers in Egypt and the Gulf, lives on WhatsApp, and centres on commitments rather than the inbox** — a hypothesis from absence that must be validated, not assumed.

---

## 2. Three directions (plus one) compared

### Direction 1 — Personal AI Secretary
- **Vision:** manages an individual's professional admin: inbox, calendar, reminders, notes.
- **Customers:** freelancers, consultants, employees.
- **Problems:** inbox overflow, forgotten follow-ups, scheduling.
- **Value:** time back; fewer misses.
- **Core features:** triage, drafts, calendar, reminders, brief.
- **Differentiation:** weak on features (Fyxer, Superhuman, Motion cover them); only language/channel can differentiate.
- **Competitors:** Fyxer, Motion, Reclaim, Superhuman, Shortwave, Copilot, Gemini.
- **Pricing potential:** $10–$30/month [Estimate from competitor anchors]. Price-sensitive; bundled alternatives.
- **Development complexity:** low–medium (mostly present).
- **Acquisition difficulty:** easy to reach, hard to convert; no pain sharp enough to force payment.
- **Retention:** low unless memory lock-in forms.
- **Expansion:** upsell into the executive tier.

### Direction 2 — AI Executive Secretary (for owner-managers and their small teams)
- **Vision:** a secretary for the person whose time is the company's bottleneck.
- **Customers:** founders, owners, GMs, partners of 5–50-person services businesses; later their managers.
- **Problems:** promises made in email, meetings and WhatsApp that nobody tracks; follow-ups that decide revenue; no assistant on payroll.
- **Value:** "nothing falls through the cracks", a daily brief, fewer dropped balls with clients.
- **Core features:** commitments ledger, morning brief, follow-up chasing with approval, meeting prep, calendar actions, WhatsApp control channel.
- **Differentiation:** ledger-first design, Arabic/English, WhatsApp, fixed price, approvals and audit.
- **Competitors:** Fyxer (closest), Lindy (DIY), suites.
- **Pricing potential:** $29–$49/month per executive [Estimate]; anchored by Fyxer at $37–$49 and by human VAs at roughly $800–$2,500/month [Secondary].
- **Development complexity:** medium; builds directly on the existing workflow, approval gate, job queue and Google integrations.
- **Acquisition difficulty:** medium; reachable through founder communities and direct outreach; short sales cycle with the decision-maker.
- **Retention:** high *if* the ledger becomes the system the user relies on.
- **Expansion:** team edition (delegation, shared ledger), then vertical packs.

### Direction 3 — Autonomous AI Chief of Staff
- **Vision:** coordinates workflows across a team, monitors projects, flags problems, drives decisions to execution.
- **Customers:** management teams of 20–200 people; later enterprises.
- **Problems:** execution drift, status chasing, decision follow-through.
- **Value:** operational visibility.
- **Core features:** cross-tool monitoring (CRM, tickets, docs), project health, decision logs.
- **Differentiation:** potentially large, but depends on integrations NexusAI does not have.
- **Competitors:** project tools with AI (Notion, Asana, ClickUp), Lindy-style agents, consultants.
- **Pricing potential:** $1,500+/month per team [Assumption].
- **Development complexity:** high (many integrations, permissions, evaluation).
- **Acquisition difficulty:** high (enterprise-like trust and security demands).
- **Retention:** very high once embedded.
- **Expansion:** the destination, not the starting point.

### Direction 4 — Vertical secretary (real estate brokerages in the Gulf/Egypt as the leading hypothesis)
- **Vision:** the Executive Secretary pre-shaped for one industry's follow-up cycle (leads, viewings, offers).
- **Why consider it:** sharper pain and clearer ROI (a missed follow-up costs a deal).
- **Why not first:** the work lives in WhatsApp and CRMs, not email; integration depth is high; the evidence I found does not support it yet. Keep as a validated *expansion* once Direction 2 has 30–50 paying owner-managers and shows which industry clusters.

### Comparison and recommendation
| Criterion | D1 Personal | D2 Executive | D3 Chief of Staff | D4 Vertical |
|---|---|---|---|---|
| Willingness to pay | Low–medium | **Medium–high** | High | High |
| Differentiation potential | Low | **Medium–high** | High | High |
| Build effort from today's code | Low | **Medium** | High | High |
| Time to first paid customer | Short | **Short** | Long | Medium |
| Retention potential | Low | **High** | Very high | High |
| Competitive crowding | Heavy | **Moderate** | Moderate | Light |
| Risk of being copied by a suite | High | **Medium** (language/channel/trust) | Medium | Low |

**[Recommendation] Start with Direction 2.** Sell it to individuals through the same product (Direction 1 as the low-priced tier), grow into Direction 3 through the team edition, and let evidence choose a vertical.

---

## 3. What a secretary must do (module design, prioritised)

Principle: **fewer capabilities, each reliable.** Priority is by value × frequency ÷ risk, not by what is exciting.

| # | Capability | Business value | Complexity | Priority | Notes |
|---|---|---|---|---|---|
| 1 | **Commitments ledger** (promises by you / to you, owner, due date, status, source link) | The core reason to subscribe; creates lock-in | Medium | **P0** | New collection; extraction from email first (the code already extracts tasks from emails) |
| 2 | **Morning brief** (top items needing you, today's meetings with prep, overdue commitments) delivered on WhatsApp and email | Daily habit, retention driver | Low–medium | **P0** | Daily-summary job exists; needs real delivery and the ledger |
| 3 | **Reminders that actually fire** | Table stakes; currently broken | Low | **P0** | Due-time job + delivery |
| 4 | **Follow-up chasing** ("Ahmed hasn't replied in 3 days; here is a draft nudge — send?") | Converts the ledger into outcomes | Medium | **P0/P1** | Approval-gated drafts; later rule-based auto-send for trusted contacts |
| 5 | **Triage and priority detection** (what needs a reply from me) | Feeds the ledger and the brief | Medium | P1 | Do not build a new email client; label, don't hide |
| 6 | **Calendar actions + meeting prep** (who, last interactions, open commitments with them) | High perceived value | Medium | P1 | Meeting prep is a ledger query, cheap to build once the ledger exists |
| 7 | **WhatsApp control channel** (voice notes in, approvals in one tap, brief out) | Where the target users live; strongest differentiation | Medium–high | P1 | Official WhatsApp Business Platform only; it controls *your* assistant, it does not read your private chats |
| 8 | **Personal context** (preferences, VIP contacts, tone, working hours, standing rules) | Quality and personalisation | Medium | P1 | Starts as an editable profile, not magic |
| 9 | **Meeting intelligence** (transcribe, decisions, action items → ledger) | Strong, but crowded and legally sensitive | High | P2 | Prefer *importing* notes/recordings the user provides (or a calendar-attached notetaker) over a bot that joins uninvited; consent rules differ (about a dozen US states are all-party) |
| 10 | **Document and knowledge base** (already built) | Supports meeting prep and Q&A | Done | Keep | Do not expand |
| 11 | **Research and reports** | Nice to have | Low | P3 | Commodity |
| 12 | **Autonomous multi-step workflows** | Powerful, risky | High | P3 | Only after approvals, audit and evals are strong |

What to **cut or freeze** from today's product: the generic "chat with everything" framing, Drive as a headline feature (restricted scope, uncertain value), the data-analysis chart feature as a marketing point, the landing page claims that outrun the product.

---

## 4. The autonomous secretary: architecture

The aim is a reactive-to-proactive transition **without** a sprawl of agents. The existing LangGraph pipeline stays the "interactive path". A second, event-driven path does the proactive work.

### 4.1 Five engines (as modules, not as microservices)

1. **Personal Context Engine** — a per-user profile: name, role, language, time zone, working hours, VIP contacts, tone samples, standing rules ("never accept meetings on Friday", "always cc my partner on client replies"), projects. Stored in MongoDB, editable by the user, with every learned item showing where it came from. *Reuses:* user model, settings screen.
2. **Memory Engine** — three stores: (a) the **ledger** (structured commitments), (b) **episodic notes** (decisions, meeting summaries, with embeddings for retrieval), (c) the existing **document knowledge base**. Writes are explicit and reviewable; reads are retrieval with the user's ids. *Reuses:* vector store (move Qdrant to a networked deployment), Mongo.
3. **Executive Intelligence Engine** — scoring and ranking: what is urgent, important, overdue or at risk, using simple transparent rules first (deadline distance, VIP sender, unanswered duration, meeting proximity) and the LLM only to *extract* and *draft*. *Why rules first:* predictability is the product.
4. **Proactive Monitoring Engine** — jobs on the existing queue: incremental inbox sync (Gmail history), calendar watch, due reminders, overdue/at-risk scans, brief generation. Idempotent, per-user, rate-limited, with persisted alert state.
5. **Workflow Execution Engine** — the current approval-gated action nodes, upgraded with idempotency keys, an action log, autonomy levels and allow-lists.

### 4.2 Do we need many agents? **No.**
For this product, separate "agents" add latency, cost and failure modes. I recommend:
- **One orchestrator (the existing graph)** that routes to **deterministic, tool-specific workflows** (inbox, calendar, ledger, brief, follow-up, documents).
- **LLM calls are narrow functions** ("extract commitments from this email as JSON", "draft a polite nudge in this tone"), each with a schema, tests and a cost budget.
- Keep "agent" as a marketing word at most. A new workflow is added only when a recorded customer problem needs it.

| Component | Responsibility | Tools | Permissions | Failure handling | Measured by |
|---|---|---|---|---|---|
| Inbox sync | Pull new mail, extract commitments and priority | Gmail read | Read | Retry with backoff; per-user circuit breaker; surface "inbox not syncing" | % mail processed < 5 min; extraction precision/recall on a labelled set |
| Ledger | Create/update/close commitments, link sources | Mongo | Own data | Never delete; soft-close with reason | Commitments captured per user per week; % later confirmed correct by the user |
| Follow-up | Detect stale items, draft nudges | Gmail draft/send | Send behind approval (or allow-list) | Idempotency key; record message id before reporting success | Follow-ups sent; reply rate; user edit rate |
| Calendar | Create/modify events, conflicts, prep | Calendar | Write behind approval | Conflict check before create; idempotent create | Events created without edits; conflicts caught |
| Brief | Compile and deliver the morning brief | Ledger, calendar, mail | Read | If a source fails, say which section is missing | Open rate; "useful" thumbs; time-to-first-action |
| Reminders | Deliver at due time through the user's channel | Notifications | None | Retry across channels; never silent failure | Delivered-on-time rate |
| Documents/KB | Answer from uploads | Qdrant | Own files | Say "not found" instead of guessing | Answer groundedness checks |

### 4.3 Trust and safety model
- **Autonomy levels, per action type and per contact** (default is level 0):
  - **0 Ask first** — every external action shows an approval card (today's behaviour).
  - **1 Draft only** — prepares drafts and events, never sends.
  - **2 Auto for allow-list** — sends or books automatically only for named contacts/domains and templates the user approved (e.g. confirming a meeting with an existing client).
  - **3 Auto with review window** — executes after a short undo window and reports it in the brief. Offered only after a clean track record.
- **Approval cards always show everything that will happen** (full recipients, full text, exact time and time zone).
- **Audit log:** an append-only record of every proposed and executed action (who, what, parameters hash, approver, timestamp, result id), exportable by the user and by admins.
- **Idempotency:** each action carries a key; the Gmail/Calendar result id is recorded *before* success is reported so a retry cannot double-send or double-book.
- **Prompt-injection posture:** external text is delimited and labelled as data; extracted fields are validated; recipients on auto levels must come from the allow-list, not from model output; the model never chooses which tool credentials to use; actions requiring a new recipient always escalate to approval. Test with a growing red-team corpus (emails that try to redirect sends or exfiltrate).
- **Least-privilege scopes and revocation:** request the smallest scope that works; show connected accounts and one-click disconnect; revoke tokens on deletion.
- **Data rights:** export and delete everything for a user; document retention.

---

## 5. The killer feature

**The Commitments Ledger with proactive follow-through** — branded in the product as **"Never drop the ball."**

What the user experiences:
1. They connect Gmail and Calendar (and later WhatsApp). Within minutes the assistant shows a first ledger: "Things you promised" (draft the proposal for Sara by Thursday), "Things you're waiting on" (Ahmed owes you the signed contract, asked 6 days ago), "Meetings this week and what's open with each person".
2. Every morning, on WhatsApp: "Good morning. Five things need you today. Two are overdue. Ahmed hasn't answered the contract question for 6 days: want me to nudge him? Reply 1 to approve." (An approval is a single tap or a voice note.)
3. After meetings, it proposes new ledger entries from notes or summaries the user provides, and asks "add these 3?".
4. At any time: "What did I promise the Al-Mansour team?" gets an answer with links.

**Why customers would pay specifically for this [Assumption — to be proven]:**
- It targets the *expensive* failure mode: the dropped follow-up, the forgotten promise, the unanswered client. For owner-managers of services firms those are revenue and reputation events, not annoyances.
- Competitors' complaints point at it: Fyxer already sells "continuously updated follow-ups" in its Pro plan (validating demand while showing it is a feature, not a platform); Lindy users complain about unpredictability; suites do not reconcile promises across email and meetings.
- It compounds: the longer it runs, the more valuable the ledger and the more costly it is to leave (retention at month 12).
- It demonstrates measurable ROI: commitments closed on time, follow-ups sent, overdue items per week.

**Why this beats the other candidates:** "never miss an email" is what every triage tool claims and is easy to copy; "prepares your workday" is a feature of the brief; "remembers everything" is vague and invites privacy fear. The ledger is specific, checkable by the user (they can see every entry and its source) and builds the proprietary dataset the company is defensible on.

---

## 6. Experience design (coherent, not a pile of modules)
Four surfaces only:
1. **Today** — the morning brief view: needs-you list, today's meetings with prep, overdue items. This is the home screen.
2. **Ledger** — promises by me / to me / waiting on others, filter by person and project.
3. **Chat and voice** — natural-language access to everything else (the current assistant).
4. **Settings** — accounts, autonomy levels, allow-list, brief time, language, data export/delete.

Channels: web app first; **WhatsApp as the control and brief channel** (official Business Platform; the assistant has its own number the user messages; it does not read the user's personal chats); email digest as a fallback.

Language: Arabic (Modern Standard and Egyptian/Gulf dialect understanding) and English as first-class; prompts and evaluations in both; right-to-left UI.

---

## 7. The ten questions

1. **What exact product would I build?** The Arabic/English chief of staff for owner-managers: a commitments ledger, morning brief and proactive follow-through on web and WhatsApp, on top of Gmail and Google Calendar, approval-first.
2. **Who are the first paying customers?** Owners, GMs and partners of 5–50-person services businesses (agencies, consultancies, brokerages, clinics' management) in Egypt, Saudi Arabia and the UAE who use Google Workspace and run their day on email and WhatsApp. [Hypothesis; validate first.]
3. **What expensive problem?** Promises and follow-ups that slip. Each dropped follow-up risks a client, a deal or a deadline.
4. **What makes it different?** Ledger-first (not inbox-first), Arabic and WhatsApp-native, fixed predictable pricing, approval-and-audit trust model, and no bots joining meetings uninvited.
5. **The killer feature?** "Never drop the ball": the commitments ledger with proactive, approved follow-up and the WhatsApp morning brief.
6. **First paid version?** Gmail + Calendar connection, ledger from email, morning brief on WhatsApp and email, reminders that fire, follow-up drafts with one-tap approval, Arabic/English, audit log, usage metering. See the roadmap.
7. **Pricing?** Personal $12, Professional $29, Team $39 per seat, regional parity pricing for Egypt (see the business model).
8. **First 100 customers?** Founder-led direct outreach and paid pilots with 10 businesses, then referrals and founder communities; target 100 by month 9–12 only if pilot retention and willingness-to-pay criteria are met (see the 90-day plan and roadmap).
9. **How to become much larger?** Team edition (shared ledger, delegation, roles), a second mail provider (Outlook), vertical packs where evidence concentrates, an API/integrations layer for CRMs, and in-region deployments for Saudi and enterprise buyers.
10. **What makes it defensible?** Accumulated per-user context (ledger, contacts, preferences, decision history), Arabic and regional workflow quality, trust track record (audit log, no incidents, verified Google app and security assessment), deep WhatsApp integration, and customer data portability that is honest but practically sticky. Suites will bundle commodity features; they are unlikely to serve this segment with this depth soon, but "unlikely" is an assumption, not a moat. The honest moat is *execution on a niche plus the data flywheel*.
