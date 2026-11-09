# NexusAI — Business Model

Read with `NEXUSAI_PRODUCT_VISION.md` (the product) and `NEXUSAI_MARKET_RESEARCH.md` (the evidence and sources for competitor prices).

**Rules for every number here.** Competitor prices are **[Verified]** (read from vendor pages) or **[Secondary]** (reported by third-party sites). Everything else — usage per user, mix of plans, conversion, regional split, fixed costs — is an **[Assumption]** or **[Estimate]** that I chose to make the arithmetic concrete. **None of this is a forecast, a promise or a guarantee.** The numbers show *what would have to be true*, so you know what to test.

---

## 1. Model summary

- **Subscription SaaS, priced per seat per month, flat and predictable.** No credit roulette. Heavy usage is handled by fair-use limits and an "extra automation pack", not by surprise bills. (Lindy's credit burn and Motion's/Fyxer's billing complaints are the reason; see the market research.)
- **Three self-serve tiers plus custom Business/Enterprise.** Annual plans give two months free.
- **Regional parity pricing** for Egypt; Gulf prices at list (billed in USD, with SAR/AED local display — conversion at current rates; I do not state exchange rates here).
- **Margins are high at the software level and the real cost is people and compliance**, not tokens (§5).

---

## 2. Price anchors from the market

| Reference | Price (per user per month) | Status |
|---|---|---|
| Fyxer Pro / Team | $49 / $99 monthly; $37 / $64 annual | Verified |
| Motion Pro AI / Business AI | $19 / $29 | Verified |
| Reclaim Starter / Business / Enterprise | $12 / $18 monthly ($10 / $15 / $22 yearly) | Verified |
| Lindy Team | from $49.99 for 5,000 credits (team) | Verified |
| Shortwave Business / Premier / Max | $30 / $45 / $120 | Verified |
| Otter Pro / Business | list $16.99 / $30 (discounted on page) | Verified (page inconsistent) |
| Superhuman Starter / Business | about $30 / $40 monthly ($25 / $33 annual) | Secondary |
| Fireflies Pro / Business | about $18 / $29 monthly ($10 / $19 annual) | Secondary |
| Microsoft 365 Copilot Business / enterprise | $21 / $30 list (sources conflict on promos) | Secondary |
| Human executive VA (Philippines / Egypt / South Africa / LatAm) | about $800–$2,500 per month (one agency); $8–$14 per hour via agencies | Secondary |

---

## 3. Pricing tiers

All prices USD, list, per month. **[Recommendation]**

| | **Personal** | **Professional** | **Team** | **Business / Enterprise** |
|---|---|---|---|---|
| Target | Freelancers, consultants, anyone testing | Founders, owners, GMs (the core customer) | Management teams, 3 to 25 seats | 25+ seats, regulated or in-region needs |
| Monthly price | **$12** | **$29** | **$39 per seat** (min 3) | Custom, from about $1,500 per month |
| Annual (2 months free) | **$120** ($10/mo) | **$290** ($24.17/mo) | **$390 per seat** ($32.50/mo) | Annual contract |
| Egypt parity price | **$7** | **$17** | **$23 per seat** | Not applicable |
| Connected accounts | 1 mailbox + 1 calendar | 2 mailboxes + 2 calendars | 3 mailboxes per seat | Unlimited by contract |
| Commitments ledger | Yes, 200 active items | Unlimited | Unlimited + shared | Unlimited + shared |
| Morning brief | Email + web | **WhatsApp + email + web** | WhatsApp + email + web | Same + custom schedule |
| Follow-up chasing | Drafts for approval, 30/month | 150/month | 250/month per seat | Contract |
| Autonomy levels | 0–1 | 0–3 with allow-lists | 0–3 + team policy | Policy engine |
| Meeting prep | Basic | Full | Full | Full |
| WhatsApp control channel | 200 messages/month | 1,500 messages/month | 2,000 per seat | Contract |
| AI usage (fair use, actions per month) | 1,500 | 5,000 | 8,000 per seat | Contract |
| Documents and memory | 1 GB, 500 memory items | 5 GB, unlimited items | 10 GB per seat | Contract |
| Audit log | 30-day view | 12-month view + export | 12-month + admin view | Retention by contract |
| Support | Email, 2 business days | Email + chat, 1 business day | Priority, onboarding call | Dedicated, SLA |
| Upgrade incentive | Ledger cap and no WhatsApp brief | Team sharing, delegation | SSO, in-region hosting, policy | — |

Add-ons **[Recommendation]**: extra automation pack (+2,500 actions for $9 per month), extra mailbox $5 per month, in-region hosting for Business.

Why these prices: Professional at $29 sits below Fyxer Pro ($37–$49) and at Motion Business ($29) while offering a different job; the human-VA comparison makes $29 a rounding error for an owner-manager. Personal at $12 matches Reclaim Starter and undercuts Superhuman. Egypt parity prices reflect lower purchasing power **[Assumption]**; test them rather than trust them.

**Test before committing:** run price-point tests on the landing page and in pilot conversations ($19 / $29 / $49 for Professional) and record willingness to commit, not stated opinions.

---

## 4. Cost model (per active user per month)

**All unit costs are [Estimate] built from [Secondary] public prices.** Reference prices used: Groq `gpt-oss-120b` about $0.15 per million input tokens and $0.60 per million output; `gpt-oss-20b` about $0.075 / $0.30 (October 2026 secondary sources; verify on groq.com/pricing); Groq Whisper turbo about $0.04 per hour of audio; WhatsApp utility messages about $0.0107 (Saudi), $0.0157 (UAE), $0.0036 (Egypt) per delivered message (third-party rate cards; verify on Meta's page).

### Derivation for a Professional-tier user
| Component | Assumption | Calculation | Cost |
|---|---|---|---|
| Inbox triage and commitment extraction | 80 emails/day, 800 input + 150 output tokens each, small model | 1.9M in + 0.36M out per month | ~$0.25 |
| Draft replies and nudges | 10/day, 3,000 in + 400 out, larger model | 0.9M in + 0.12M out | ~$0.21 |
| Assistant chat and approvals | 10 interactions/day, 6,000 in + 600 out, larger model | 1.8M in + 0.18M out | ~$0.38 |
| Briefs, ledger reconciliation, meeting prep | 1 brief/day plus reconciliation | ~ | ~$0.30 |
| Speech-to-text | 10 minutes/day | ~5 audio hours at $0.04 | ~$0.20 |
| Text-to-speech (replace gTTS with a paid API) | allowance | — | ~$0.30 |
| WhatsApp messages | ~60 utility messages/month (blended, mostly Gulf) | 60 × ~$0.011 | ~$0.65 |
| Storage and vector search | 5 GB, ~50k chunks | — | ~$0.20 |
| **Subtotal** | | | **~$2.5** |
| Contingency +70% (retries, longer threads, **reasoning-model tokens count as output**, spikes) | | | **~$4.2 used in the model** |

Personal tier: lighter usage, no WhatsApp brief: **$1.8**. Team seat: more automations and storage: **$5.0**.

### Why token cost is not the main risk
At these assumptions LLM spend is about one dollar per heavy user per month. The bigger variable costs are **WhatsApp per-message fees** (at Gulf rates they can match the LLM bill), **voice** if usage is heavy, and **runaway usage** from loops or abuse, which is why per-user budgets and limits are mandatory. A switch from small open models to frontier models for drafts could raise the LLM line several times over; that is a quality decision to price consciously, not an accident.

### Controlling AI cost without hurting experience
1. **Route by task:** a small model for triage/extraction, a larger one only for drafts and chat; cache embeddings and extractions; never re-read an email already processed (history ids, content hashes).
2. **Deterministic first:** ranking, scheduling and overdue detection are rules, not LLM calls.
3. **Per-user monthly budgets in cost units**, shown to the user as a plain "usage" bar; graceful degradation (briefs and reminders always work; heavy chat slows) instead of hard stops; add-on pack to extend.
4. **Batch and schedule** non-urgent work (nightly reconciliation) at batch-API discounts where available (reported 50% on Groq batch).
5. **Prompt hygiene:** truncate quoted history, strip signatures, send diffs not whole threads.
6. **Evals before model swaps:** measure draft acceptance and extraction accuracy per model per language, then choose the cheapest model that meets the bar.
7. **Alert on cost per user** (p95, max) daily; cut off anomalies automatically.

---

## 5. Fixed and semi-fixed costs (assumptions)

| Item | Assumption |
|---|---|
| Hosting (API, workers, MongoDB, networked Qdrant, monitoring) | $250/month up to 500 users; $600 up to 2,000; $1,500 up to 5,000; $3,000 up to 10,000 |
| Tools (error tracking, email sending, analytics, support desk) | $300/month up to 1,000 users; $600 up to 5,000; $1,200 beyond |
| Google OAuth verification and annual security assessment | Up to about $4,500 per year, amortised ($375/month) as a conservative figure; secondary sources range from roughly $500 to $4,500 and older sources cite much more. **Verify before budgeting.** |
| Support and customer success | $0 up to 100 subscribers (founder-led); $1,500/month up to 1,000; $3,000 up to 5,000; $6,000 beyond (Egypt-based hires assumed) |
| Payment processing | 3.5% of revenue (assumption; depends on processor and card mix) |
| **Excluded from operating profit below** | founders' pay and salaried engineers/designers/sales (shown separately), legal and incorporation, taxes, marketing spend, refunds and chargebacks, FX losses |

---

## 6. Illustrative scenarios

**Blended assumptions [Assumption]:** plan mix 45% Personal / 45% Professional / 10% Team seats; list ARPU = $22.35; 40% of subscribers pay annually at a 1/6 discount; 30% of subscribers are in Egypt at a 40% parity discount. **Effective ARPU = $18.36 per month.** Variable cost per subscriber = $3.20 (mix of the tier costs above); payment fees 3.5% ($0.64). These are the same inputs for every row, so rows differ only by scale and fixed costs.

| Subscribers | MRR | ARR (MRR × 12) | Variable AI/usage cost | Payment fees | Assumed fixed + support | **Gross margin** (revenue − variable − fees) | **Operating profit** before founders'/developers' pay | Margin |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | $1,836 | $22,028 | $320 | $64 | $925 | 79.1% | $526 | 28.7% |
| 500 | $9,178 | $110,141 | $1,600 | $321 | $2,425 | 79.1% | $4,832 | 52.6% |
| 1,000 | $18,357 | $220,282 | $3,200 | $642 | $2,775 | 79.1% | $11,739 | 64.0% |
| 5,000 | $91,784 | $1,101,408 | $16,000 | $3,212 | $5,475 | 79.1% | $67,097 | 73.1% |
| 10,000 | $183,568 | $2,202,816 | $32,000 | $6,425 | $10,575 | 79.1% | $134,568 | 73.3% |

**Gross revenue** is the MRR line. **Gross margin** subtracts only costs that scale with usage and payments. **Net profit** is what remains after *everything*, including salaries, taxes and marketing; the right-hand "operating profit" is *before* those, so it overstates net profit.

**With a small salaried team** (three people at an assumed loaded $2,000 per month each, i.e. $6,000): operating profit is about **−$1,168 at 500 subscribers, +$5,739 at 1,000, +$61,097 at 5,000**. **Break-even is about 581 subscribers** with that team and about 168 subscribers for the lean cost base only (before any pay). These break-even points assume the *effective* ARPU of $18.36 holds, nobody churns and acquisition costs nothing — all false in reality. Real net outcomes will be lower; the model's use is to show that the business works at under a thousand customers *if* the price and usage assumptions hold.

### 100 business (Team) customers
Assumption: average 8 seats, $39 per seat, 60% annual at a 1/6 discount, no Egypt discount (Gulf and international buyers): **800 seats, ARPU $35.10, MRR $28,080, ARR $336,960**, variable cost $4,000 (at $5.00 per seat), payment fees about $983, **gross margin about 82%**, assumed fixed and support costs about $8,575 per month. Business customers need more support, security reviews, onboarding and sometimes in-region hosting; their *sales cost* is not modelled and will be significant.

### What would break the model
- Usage 3× higher than assumed (long threads, voice, many WhatsApp messages) raises variable cost per subscriber from $3.20 to $9.60 and cuts gross margin from 79% to about 44% at the blended price [recomputed: (18.36 − 9.60 − 0.64) ÷ 18.36]. This is why per-user budgets are in the plan.
- Egypt at 60% of subscribers instead of 30% lowers effective ARPU to about $15.8 [recomputed from the same formula].
- Churn: at 5% monthly churn, a subscriber lives about 20 months on average; at 10%, about 10 months [simple arithmetic]. Acquisition cost must stay well below gross profit per customer over that life.
- Google verification delay or failure caps growth at 100 users.
- WhatsApp rule or fee changes (the per-message model changed in 2025–2026 according to the sources).

---

## 7. Billing, markets and operations
- **Collecting money [Unresolved]:** I could not confirm Stripe availability for Egyptian or Saudi-registered businesses (sources conflict for Saudi; nothing confirmed for Egypt). Options to check directly: Stripe via a UAE or US entity, Paddle or Lemon Squeezy as merchant of record, regional processors (PayTabs, MyFatoorah, Paymob). A merchant of record simplifies tax handling at a higher fee. Decide this in the first two weeks of the execution plan; it can block revenue.
- **Entity and tax:** choose the incorporation jurisdiction with advice from a lawyer and accountant; not covered here.
- **Currency display:** price in USD, display SAR/AED/EGP; avoid currency risk by billing in USD.
- **Data and compliance in the Gulf:** Saudi PDPL transfer rules and in-region hosting demand shape the Business tier; do not promise localisation before the legal review.
- **Refunds and dark patterns:** because billing and cancellation complaints are the loudest in this market, make cancellation one click, show the next renewal date, and send a pre-renewal email for annual plans. This is cheap and a differentiator.

---

## 8. Metrics to run the business by
Activation (connected mailbox and first ledger within 24 hours), weekly active use of the brief, commitments captured and closed per user, follow-ups approved, paid conversion from trial, gross margin per user, cost per user (p50/p95), logo and revenue churn, support load per 100 users. Targets are in the roadmap.
