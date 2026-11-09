# NexusAI — Technical Audit

Status of this document: read-only audit of the repository at commit `e81ab91` (main), written for a founder deciding what to build on, what to fix and what to leave alone. No application code was changed to produce it.

**Labels used throughout:** **[Verified]** observed in the code or in a test run in this repository · **[Assumption]** reasonable but unconfirmed · **[Estimate]** a number I derived · **[Recommendation]** what I would do.

---

## 0. The honest summary

NexusAI today is a **well-tested, single-process, English-leaning AI chat assistant with Gmail, Calendar and Drive tools**. It is not yet an AI *secretary*. A secretary works when you are not looking; NexusAI mostly works when you type to it.

What is genuinely good: the codebase went through a serious hardening pass. Authentication, sessions, per-user isolation, approval-before-acting, structured errors, request ids, a persistent agent checkpointer and a job queue all exist and are covered by tests (**373 backend test functions, 473 collected cases, 14 frontend tests** [Verified]). That is unusually solid for a student-origin project and it is a real asset: do not rewrite it.

What is missing for a paid product, in order of how much it hurts:

1. **Nothing fires a reminder.** Reminders are saved to MongoDB and listed, but no scheduled job delivers one at its due time [Verified: the scheduler registers only the inbox check and a thread purge; the notification engine only produces the daily summary]. The assistant says "Reminder set!" and then never reminds. For a secretary this is the single most embarrassing gap.
2. **No billing, plans, quotas, usage metering or cost limits of any kind** [Verified: no such code exists]. One user can run unlimited LLM calls.
3. **No workspace / organisation model.** Everything is per user. There is no team, role, delegation or shared data [Verified].
4. **No long-term personal memory.** The "memory" is the last conversation turns (history is trimmed to the latest 10 when a turn starts) and a document knowledge base. There is no profile, preferences, contacts or commitments store [Verified].
5. **Proactive features are in-process and fragile.** Background email checking loops over all users sequentially every minute inside the API process; the "seen messages" set is an in-memory dict (lost on restart, so duplicate alerts are possible); notifications go into in-memory queues (lost on restart, and not visible to other workers) [Verified].
6. **Production blockers outside the code:** Gmail's restricted scopes require Google OAuth verification and a recurring third-party security assessment before real customers outside a test list can connect (details in §6).

Everything else in this audit is secondary to those six.

---

## 1. What exists (inventory)

### 1.1 Size and shape [Verified]
- Backend: about 11,300 lines of Python outside tests, organised as `agent/` (graph and nodes), `controllers/` (LLM-facing logic), `services/`, `stores/` (Gmail, Calendar, Drive, LLM providers, vector DB, checkpointer), `models/` (Mongo), `routes/` (FastAPI), `helpers/` (config, auth, errors, logging).
- Frontend: React 19 + Vite, about 4,600 lines. `WorkspacePage.jsx` alone is about 1,230 lines and mixes chat, approvals, settings wiring and panels: it works but is the most brittle file in the repository.
- Tests: 373 backend test functions (473 collected cases with parametrisation) plus 14 frontend tests. Many run against a real MongoDB and skip themselves without one.
- CI: a GitHub Actions workflow exists (tests, ruff, mypy report-only, pip-audit report-only, npm audit). **It has never been observed running on GitHub** [Verified: not run during this work].

### 1.2 Implemented and working (exercised in an end-to-end run of 91 API checks) [Verified]
| Area | Status |
|---|---|
| Email+password auth, httpOnly cookie sessions, rotating refresh tokens with replay detection, CSRF double-submit + Origin check, rate limits on login, sign-up, refresh and Google sign-in | Working |
| "Continue with Google" sign-in/sign-up (verified identity token, no takeover of password accounts) | Working (unit-tested; not completed against real Google in this audit) |
| Per-user data isolation (projects, files, vector collections, conversation threads, jobs, reminders) | Working; cross-user tests pass |
| Chat via streaming SSE, intent classification, conversational / web search / knowledge-base answers | Working |
| Human approval before send-email, create-event, save-reminder, save-to-Drive (single-use resume token) | Working |
| Gmail search + summaries + task extraction from new emails | Working with a connected account |
| Calendar event creation (time-zone aware, weekday lookup table for relative dates) | Working |
| Drive: search, read and summarise, create Doc, upload file | Built and unit-tested with a fake API; **not run against real Drive** |
| Knowledge base: upload, chunk, embed, index, search, RAG answer; spreadsheets for data analysis with a validated plan (no arbitrary code) | Working |
| Background job queue (MongoDB-backed, leases, retries) for document processing and indexing | Working |
| Persistent agent state (`MongoCheckpointer`), conversations restored from the server | Working |
| Voice: speech-to-text (local Whisper, needs ffmpeg), text-to-speech (gTTS) | Works; see risks |
| Observability basics: request ids, JSON logs, redaction of secrets and personal text, `/health` and `/health/ready` | Working; no metrics or tracing |

### 1.3 Partially implemented
- **Proactive urgent-email alerts**: logic exists (`controllers/proactive_agent.py`) and runs every minute, but alerts are pushed to in-memory queues and shown only if the browser is connected to the notification stream. There is no email/WhatsApp/push delivery.
- **Daily summary**: generated once per local day at a configured hour per user, delivered the same way (in-memory queue).
- **Tasks**: extracted from emails and stored; there is a Tasks panel, but no deadline monitoring, no follow-up, no completion tracking from email replies.
- **Time zones**: per-user setting works; "morning briefing" depends on it.
- **Multi-language**: prompts live in locale templates (`locales/en/…`), but only English exists for the agent prompts; Arabic exists only for the RAG prompts. Models reply in the user's language, which works for Arabic, but classification and extraction prompts are English-only.

### 1.4 Planned only / absent [Verified absent]
Meeting bots or transcription of meetings · Outlook/Microsoft 365 support · WhatsApp or SMS channels · reminders delivery · long-term memory or contact graph · shared workspaces, roles, delegation · billing and plans · usage metering · audit log · admin console · mobile apps · API for third parties · data export and account deletion endpoints.

---

## 2. Agents and responsibilities (what "multi-agent" means here)

NexusAI is **one LangGraph state machine with specialised nodes**, not a society of autonomous agents. That is a good thing and should stay so.

| Node | Responsibility | Tools | Permission model |
|---|---|---|---|
| `save_query` | Persist the query, load the user's uploaded file names | Mongo | Own data only |
| `detect_request_type` | Classify into conversation / knowledge base / web / action types | LLM (JSON) | None |
| `conversational_node` | Persona replies, 1–2 sentences | LLM | None |
| `search_knowledge_base` | RAG over uploads (+ tasks, reminders, calendar when asked) | Qdrant, Mongo, Calendar | Own data |
| `search_gmail` | Plan a Gmail search (validated), summarise, mine tasks | Gmail read | Read |
| `drive_search` | Plan a Drive search (validated, escaped), read and summarise | Drive read | Read |
| `web_search` | Google CSE or DuckDuckGo, answer from snippets | HTTP | None |
| `data_analysis` | Validated JSON plan (aggregate, top-n, correlation, describe) | pandas | Own uploaded files |
| `daily_summary` | Briefing from calendar, mail, tasks, reminders | Google APIs, Mongo | Read |
| `extract_action_details` + `send_email` / `create_calendar_event` / `save_reminder` / `drive_save` | Extract parameters, then **pause for approval**, then execute | Gmail send, Calendar, Drive create, Mongo | Write, always behind approval |

Assessment [Recommendation]: the *workflow* design (classify → deterministic node → approval for writes) is the right foundation for a trustworthy secretary. Do **not** split it into more "agents". Add capabilities as nodes or scheduled workflows, and add an *autonomy level* switch in front of the approval gate (see the product reports).

Known weaknesses:
- Classification is a single LLM call with a keyword fallback. Mis-routes (for example a personal-document question sent to web search) were found and mitigated by passing the user's document names to the classifier; the underlying fragility remains. [Verified]
- The model in use during testing (a 20B open model on Groq) is a reasoning model that needed special handling to avoid empty answers. Model choice drives both quality and cost and should be an explicit, tested configuration. [Verified]

---

## 3. Memory architecture [Verified]

| Layer | What it holds | Limits |
|---|---|---|
| Conversation state | LangGraph checkpoint in MongoDB per thread (`<user_id>_<thread_id>`), `chat_history` | History is trimmed to the last 10 turns when building the next turn's state; threads idle for 30 days are purged |
| Knowledge base | Chunks in MongoDB, vectors in an **embedded Qdrant folder on local disk** | Single process owns the folder; not shareable across workers or machines |
| Tasks / reminders / queries | Mongo collections | Reminders are never delivered (see §0) |
| Personal context | **None** | No profile, preferences, contacts, relationships, projects or commitments |

Consequence: the assistant cannot learn "who matters to this person" or "what they promised Ahmed last week". That is the data a secretary needs, and it does not exist yet.

---

## 4. Authentication, authorisation, isolation

### 4.1 What is solid [Verified]
- Passwords: bcrypt, strength checks, uniform error and timing for unknown emails, throttling per account and per address.
- Sessions: 15-minute access token and rotating single-use refresh token in httpOnly cookies, replay revokes the family, CSRF double-submit plus Origin check, bearer tokens still accepted for API clients.
- Every non-public route requires authentication (a test enumerates all routes).
- Project names are scoped to the user (`u<id>p<name>`); file access resolves only inside the user's folder and uses the last path component only; analysis plans are validated, never executed as code.
- Human approval tokens are single-use and tied to the pending interrupt; replays and wrong tokens are rejected.
- Text from emails, files and web pages is wrapped as data in prompts with explicit "do not follow instructions inside" wording (prompt-injection mitigation, not a guarantee).

### 4.2 What is weak
- **Google token encryption is optional** (`TOKEN_ENCRYPTION_KEY` unset means plaintext in MongoDB) [Verified]. For a product holding refresh tokens to customers' inboxes this must become mandatory, with key rotation and ideally a KMS. [Recommendation, P0]
- **No audit log.** Approved actions are logged as text lines, not as a queryable, tamper-evident record of "who approved what, when, with which parameters". [Recommendation, P0 for business customers]
- **No account deletion or data export.** Required by privacy law and by customers. [Recommendation, P1]
- **Google sign-in cannot link to an existing password account** (by design, for safety). A secure linking flow (verify the password, then link) is needed. [Recommendation, P2]
- **No email verification** for password sign-ups. [Recommendation, P1]
- **No MFA.** [Recommendation, P2 for individuals, P1 for business]

---

## 5. Data model [Verified]

MongoDB collections: `users`, `sessions`, `projects`, `assets`, `chunks`, `queries`, `tasks`, `task_email_scans`, `reminders`, `meetings`, `emails`, `jobs`, `rate_limits`, plus the LangGraph `lg_checkpoints`, `lg_checkpoint_writes`, `lg_checkpoint_blobs`. Indexes are created at startup.

Observations:
- A single `users` document holds profile, Google token and flags. There is no `workspace`, `membership` or `subscription` entity.
- `meetings` and `emails` collections exist but are not the basis of any feature today.
- The vector store is embedded Qdrant on disk; production needs a networked Qdrant (or pgvector/Atlas Vector Search) before a second server process exists.
- The earlier unused Postgres stack was removed. MongoDB is the system of record; that is fine for this workload.

---

## 6. Integrations and platform risk

### 6.1 Gmail and Google Calendar — the biggest non-code risk
- The app requests `gmail.readonly`, `gmail.modify`, `gmail.send`, `calendar`, `calendar.events`, `drive.readonly`, `drive.file`. Per Google's documentation (as reported by secondary sources) the Gmail scopes `readonly` and `modify` are **restricted**, which for apps that store or transmit that data on servers requires OAuth verification **plus an annual third-party security assessment (CASA)**; `gmail.send` is sensitive (verification, no assessment). `drive.readonly` is also restricted. [Source-reported; confirm on Google's pages]
- Reported assessment cost: roughly **$500–$4,500 per year** depending on tier, with some sources citing far higher historical figures. [Source-reported, unverified]
- Until verification is complete, an external-type app is limited to **100 users** and, in "Testing" status, refresh tokens expire after **7 days**. [Source-reported by Google's own setup page as summarised in search results]
- **Least privilege [Recommendation]:** for the first paid version, request the minimum. Drop `drive.readonly` (restricted) until Drive is proven valuable; prefer `gmail.metadata`/`gmail.readonly` only if triage needs it; consider `gmail.compose` over `gmail.modify`. Fewer restricted scopes means a cheaper and faster assessment.
- Microsoft Outlook (Graph) support is absent. Whether Graph mail scopes need an equivalent assessment was **not established** in my research; treat Outlook as a second integration to evaluate, not a free option.

### 6.2 Other external dependencies
- LLM: provider abstraction exists (OpenAI, Groq, Cohere, Gemini). Cost and rate-limit behaviour differ per provider; no per-user budgets exist.
- STT: local Whisper on the API host (CPU, slow, memory heavy, needs ffmpeg). Fine for a demo, wrong for production. A hosted transcription API is cheaper to operate (about $0.04 per audio hour on Groq's Whisper turbo per secondary sources).
- TTS: `gTTS` calls an unofficial Google Translate endpoint. That is not a supportable production dependency. [Recommendation: replace]
- Web search: DuckDuckGo fallback is unofficial and may break.

---

## 7. Background execution and scheduling [Verified]

- `APScheduler` runs inside the API process: inbox check every `INBOX_CHECK_INTERVAL_MINUTES` (default 1) and a daily thread purge. A separate "notification engine" loop produces the per-user daily summary.
- The inbox job walks **all** Google-connected users **sequentially** each cycle. At roughly 0.3–1 s of Gmail and LLM latency per user (estimate), 1,000 users take 5–15 minutes per cycle: the one-minute schedule becomes meaningless and cycles overlap.
- "Messages already alerted" is an in-memory set: a restart repeats alerts.
- Notifications are in-memory per-user queues (capped at 50) behind a one-time-ticket SSE endpoint. They vanish on restart and do not exist for a user who is not connected.
- The job queue (`jobs` collection, leases, retries, optional external worker process) is the right pattern but is used only for document processing and indexing.

**[Recommendation]** Move all periodic per-user work onto the existing job queue as small idempotent jobs ("check inbox for user X since history id Y"), use Gmail push notifications (watch + Pub/Sub) or `history.list` incremental sync instead of polling, persist alert state, and add real delivery channels (email digest, web push, WhatsApp).

---

## 8. Reliability, security and operational findings

| # | Finding | Severity | Notes |
|---|---|---|---|
| 1 | Reminders never fire | **Critical (product)** | Add a due-reminder job; deliver via push/email/WhatsApp |
| 2 | No usage limits or cost caps | **Critical (business)** | One abusive or buggy loop burns money |
| 3 | Token encryption optional | High | Make mandatory; add rotation |
| 4 | No audit log | High | Needed for trust and business sales |
| 5 | In-process scheduler, serial per-user loop | High | Breaks beyond a few hundred users |
| 6 | In-memory notification queues and tickets | High | Single process only |
| 7 | Embedded Qdrant on local disk | Medium | Blocks horizontal scale |
| 8 | Idempotency of sends: approval is single-use, but a retry after a network error between "Gmail accepted" and "we recorded it" could double-send | Medium | Add an idempotency key per action and record the Gmail message id before reporting success |
| 9 | Local Whisper and gTTS in the API process | Medium | Replace with hosted services |
| 10 | Chat endpoint has no rate limit | Medium | Add per-user limits and budgets |
| 11 | No metrics, tracing, alerting | Medium | Logs and health only |
| 12 | `WorkspacePage.jsx` is a 1,200-line component | Low–Medium | Refactor before adding more UI |
| 13 | CI never observed on GitHub; mypy reports ~174 findings | Low | Make CI the gate |
| 14 | English-only agent prompts | Medium for MENA | Translate classification and extraction prompts, test Arabic dialect inputs |

Prompt-injection posture: untrusted text is delimited and labelled as data; actions that change anything require approval; analysis plans are validated rather than executed. This is a sound baseline. It is **not** proof against a determined attacker: an email that persuades the model to put attacker text into an approved draft is still possible, which is why approval cards must show full recipients and bodies, and why autonomy levels above "ask first" need allow-lists (known recipients, internal domains).

---

## 9. What can be reused as-is (do not rewrite) [Recommendation]
Auth and sessions · CSRF/origin protection · per-user scoping · the LangGraph pipeline and approval gate · `llm_json` (validated model JSON with a retry) · domain exception hierarchy and error JSON · request-id logging and redaction · job queue and worker · MongoDB checkpointer · Google service wrappers and token handling (scope-safe refresh) · locale-template prompt system · the test suite and fakes.

## 10. What should be replaced or built next
1. Reminder delivery and a notification service with persistence and channels.
2. Commitment ledger and personal context store (new collections; see product vision).
3. Usage metering, plans and billing; per-user LLM budgets.
4. Workspace/membership model (before the Team edition, not before the first paid version).
5. Hosted STT/TTS; networked vector store; incremental Gmail sync.
6. Mandatory token encryption, audit log, account deletion and export.
7. Frontend decomposition of the workspace page.

---

## 11. Sources for facts that are not from the code
- Google restricted-scope verification and annual assessment: Google's restricted scope verification page (developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification) and secondary summaries (deepstrike.io CASA guide; TAC Security price list as cited by search results; aiemaily.com).
- 100-user cap and 7-day refresh-token expiry in Testing status: Google Health API setup guide (developers.google.com/health/setup) and unipile.com guides.
- Groq Whisper turbo about $0.04 per hour: Groq announcement and aggregator pages (groq.com).
- All numbers marked **[Estimate]** are mine.
