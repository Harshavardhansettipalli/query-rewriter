# Search Query Rewriter Agent

An agent that takes a user's search query, fixes spelling mistakes, checks whether the query has enough context, adds missing context from the user's profile and recent portal activity, and shows the rewritten query to the user to **accept, edit, or reject** before anything is searched.

It uses a **hybrid architecture**: Python handles everything that is deterministic, fast and free (validation, cleaning, spelling detection, database lookups, safety checks), and the AI (OpenAI) handles only what needs language understanding (intent, context sufficiency, ambiguity, rewriting). Neither side is trusted blindly: Python validates every AI response before the user sees it.

---

## Table of contents

1. [What it does](#1-what-it-does)
2. [Architecture](#2-architecture)
3. [Tech stack](#3-tech-stack)
4. [Project structure](#4-project-structure)
5. [Quick start](#5-quick-start)
6. [Where to put your OpenAI API key](#6-where-to-put-your-openai-api-key)
7. [Using the app](#7-using-the-app)
8. [Configuration reference](#8-configuration-reference)
9. [How each pipeline stage works](#9-how-each-pipeline-stage-works)
10. [Data model](#10-data-model)
11. [API reference](#11-api-reference)
12. [Testing](#12-testing)
13. [Common problems this project prevents](#13-common-problems-this-project-prevents)
14. [Security and privacy](#14-security-and-privacy)
15. [Running with Docker](#15-running-with-docker)
16. [Deploying to AWS](#16-deploying-to-aws)
17. [Production checklist](#17-production-checklist)
18. [Troubleshooting](#18-troubleshooting)
19. [Extending the project](#19-extending-the-project)

---

## 1. What it does

| Requirement | How it is met |
|---|---|
| Check for spelling mistakes | Python flags likely misspellings with an offline dictionary. The AI confirms them in context, so technical terms and brand names aren't "corrected". Python applies the final fixes. |
| Check whether the query has enough context | The AI decides whether a search engine could return relevant results without knowing who is asking, and names what is missing (topic, skill level, location, and so on). |
| Add context from profile and recent activity | Only when context is missing, Python loads the user's role, location, interests, preferences and recent portal activity, ranks the activity by relevance, and passes the top items to the AI to rewrite the query. |
| Show the rewritten query for approval | The UI shows a word-by-word diff of the original and the suggestion, explains what changed, and offers Accept, Edit, and Reject. Every decision is saved. |
| Be clear about what was done | Every result includes a step-by-step trace. Each step is tagged **Python**, **AI** or **You** and shows its status (done, skipped, fallback used) and duration. |

**Example**

A data analyst whose profile says `level = intermediate` and who recently viewed an SQL course types:

```
sql coursse
```

The agent returns:

```
intermediate SQL video courses for data analysts
```

It also explains the change: *"Fixed a typo and added your role, level and preferred format."* The user then accepts, edits, or rejects the suggestion.

A specific query such as `python pandas groupby tutorial` passes through unchanged after a single AI call. The profile isn't loaded at all.

---

## 2. Architecture

```
                 ┌──────────────────────────────┐
                 │          User query          │
                 └──────────────┬───────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ 1. PYTHON PREPROCESSING                       (no AI, ~5 ms) │
 │    • input validation (empty, too short, too long, symbols)  │
 │    • normalisation (unicode, invisible chars, spacing,       │
 │      repeated words and punctuation)                         │
 │    • spelling detection (offline dictionary + domain words + │
 │      the user's own profile words)                           │
 │    • duplicate check → reuse a recent identical result       │
 └──────────────────────────────┬──────────────────────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ 2. AI UNDERSTANDING                               (AI call 1)│
 │    • intent detection                                        │
 │    • context sufficiency (is the query enough on its own?)   │
 │    • ambiguity detection (none / low / high)                 │
 │    • confirms which flagged words are real misspellings      │
 └──────────────────────────────┬──────────────────────────────┘
                 context sufficient? ── yes ──► skip to step 5
                                │ no
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ 3. PYTHON CONTEXT RETRIEVAL                       (database) │
 │    • user profile (role, location, interests)               │
 │    • recent portal activity, ranked by relevance + recency   │
 │    • relevant preferences                                    │
 │    • never includes name or email                            │
 └──────────────────────────────┬──────────────────────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ 4. AI QUERY REWRITING                             (AI call 2)│
 │    • add relevant context   • improve clarity                │
 │    • preserve original intent                                │
 │    then PYTHON GUARDRAILS: not empty, not too long, keeps    │
 │    the user's key terms. If a check fails, the spelling-     │
 │    corrected query is used instead.                          │
 └──────────────────────────────┬──────────────────────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ 5. USER APPROVAL          Accept  /  Edit  /  Reject         │
 │    final query saved to history and added to activity        │
 └─────────────────────────────────────────────────────────────┘
```

### Why split the work this way

| Concern | Python | AI |
|---|---|---|
| Cost | Free | Paid per token |
| Speed | Milliseconds | Hundreds of milliseconds per call |
| Determinism | Same input always gives the same output | Can vary between calls |
| Language understanding | Weak | Strong |

So Python does validation, cleaning, lookups and safety checks, and the AI is called only when understanding language is actually required. In practice:

* **Clear queries** cost one AI call.
* **Vague queries** cost two AI calls.
* **Repeated queries** within 5 minutes cost zero AI calls.
* **When the AI is down**, users still get spelling fixes and a clear notice.

---

## 3. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.10+ | Required by the brief, and the strongest ecosystem for AI work |
| Web framework | FastAPI | Fast, typed, automatic API docs at `/docs` |
| Validation | Pydantic v2 | Validates every request and every AI response |
| Database | SQLite locally, PostgreSQL in production | Zero setup locally; the same code runs on Amazon RDS |
| ORM | SQLAlchemy 2.x | Database-independent and mature |
| Spelling | pyspellchecker | Offline, free, no extra API calls |
| AI | OpenAI API (default model `gpt-4o-mini`) | Cheap and fast; JSON mode gives reliable structured output |
| Frontend | Plain HTML, CSS and JavaScript | No build step and nothing extra to install |
| Tests | pytest | 38 tests; no real AI calls needed |
| Packaging | Docker | Same image locally and on AWS |

---

## 4. Project structure

```
query-rewriter-agent/
├── app/
│   ├── main.py                  # App factory, error handlers, health check, static files
│   ├── config.py                # All settings, read from .env / environment variables
│   ├── database.py              # Engine setup (SQLite pragmas, Postgres URL fix), utcnow()
│   ├── models.py                # Tables: users, activities, query_logs
│   ├── schemas.py               # Request/response validation
│   ├── dependencies.py          # DB session, profile lookup, rate limiter
│   ├── routers/
│   │   ├── users.py             # Profiles, activity, history endpoints
│   │   └── queries.py           # /api/rewrite and the accept/edit/reject decision
│   ├── pipeline/
│   │   ├── orchestrator.py      # Runs the 5 stages and records the step trace
│   │   ├── preprocessing.py     # Stage 1: validation, normalisation, spelling detection
│   │   ├── understanding.py     # Stage 2: AI intent / sufficiency / ambiguity
│   │   ├── context.py           # Stage 3: profile + activity retrieval and ranking
│   │   ├── rewriting.py         # Stage 4: AI rewrite + Python guardrails
│   │   ├── prompts.py           # Every AI prompt, in one place
│   │   └── llm_client.py        # OpenAI wrapper with friendly error handling
│   └── data/
│       └── domain_vocabulary.txt  # Words the spellchecker must never "correct"
├── static/
│   ├── index.html               # The UI
│   ├── styles.css               # Light and dark themes, mobile layout
│   └── app.js                   # UI logic (no frameworks)
├── scripts/
│   └── seed.py                  # Loads two demo profiles with activity
├── tests/                       # pytest suite (fake AI, temporary databases)
├── data/                        # Created automatically; holds app.db (git-ignored)
├── .env.example                 # Copy to .env and add your API key
├── requirements.txt             # Runtime dependencies
├── requirements-dev.txt         # Adds pytest and httpx
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## 5. Quick start

### Prerequisites

* **Python 3.10 to 3.13.** Check with `python --version` (on Mac/Linux it may be `python3`).
* **An OpenAI API key.** This is optional; the app runs without one in rule-based mode.

### Step 1: open a terminal in the project folder

```bash
cd query-rewriter-agent
```

### Step 2: create and activate a virtual environment

A virtual environment keeps this project's packages separate from the rest of your system.

**Windows (PowerShell or Command Prompt)**
```bat
python -m venv .venv
.venv\Scripts\activate
```

> If PowerShell says "running scripts is disabled", run this once:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

**macOS / Linux**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

When it is active, your prompt starts with `(.venv)`.

### Step 3: install dependencies

```bash
pip install -r requirements.txt
```

### Step 4: add your API key

```bash
# Windows
copy .env.example .env
# macOS / Linux
cp .env.example .env
```

Open `.env` and set `OPENAI_API_KEY`. See [section 6](#6-where-to-put-your-openai-api-key).

### Step 5: load demo data (optional, recommended)

```bash
python -m scripts.seed
```

This adds two profiles, **Priya Sharma** (data analyst) and **Arjun Mehta** (backend developer), each with recent activity. Running it again is safe: existing demo profiles are skipped. To wipe everything and start fresh:

```bash
python -m scripts.seed --reset
```

### Step 6: start the server

```bash
uvicorn app.main:app --reload
```

`--reload` restarts the server automatically when you edit code. Leave it off in production.

### Step 7: open the app

| URL | What it is |
|---|---|
| http://localhost:8000 | The app |
| http://localhost:8000/docs | Interactive API documentation (disabled when `ENVIRONMENT=production`) |
| http://localhost:8000/api/health | Health check; shows whether the AI is enabled |

To stop the server, press `Ctrl + C` in the terminal.

**Next time** you only need two steps: activate the virtual environment (step 2), then start the server (step 6).

---

## 6. Where to put your OpenAI API key

1. Create a key at https://platform.openai.com/api-keys. The account needs billing enabled, or requests will fail with a quota error.
2. Open the file **`.env`** in the project root. This is the same folder as `requirements.txt`. Create it from `.env.example` if you haven't already.
3. Replace the placeholder:
   ```
   OPENAI_API_KEY=sk-proj-abc123...your real key...
   ```
   Don't use quotes or spaces around the `=`.
4. Restart the server. Settings are read only at startup.
5. Check http://localhost:8000/api/health. It should show `"ai_enabled": true`.

**How the key is protected**

* It is read only by the server (`app/config.py`) and is never sent to the browser.
* `.env` is listed in `.gitignore`, so it won't be committed to Git.
* `.env` is listed in `.dockerignore`, so it won't be baked into Docker images.
* The OpenAI SDK's verbose logging is silenced, so request details don't end up in log files.
* On AWS, the key goes into **Secrets Manager**, not a file (see [section 16](#16-deploying-to-aws)).

**Without a key**, the app still works. It shows a banner saying the AI is off and applies only near-certain dictionary spelling fixes.

**Changing the model:** set `OPENAI_MODEL` in `.env` (for example `gpt-4o`, or a newer model your account has access to). If your chosen model rejects custom temperature values, leave `OPENAI_TEMPERATURE=` empty. The app also retries automatically without a temperature if the model rejects it.

---

## 7. Using the app

### Profile tab

Create or edit a profile. You can set a name, email, role or job title, location, interests (comma-separated), and preferences (one `key = value` per line, for example `level = intermediate`).

Only **role, location, interests, preferences and activity** are used to add context. **Name and email are never sent to the AI.**

### Activity tab

This tab shows what the user has done on the portal: viewed, searched, opened, downloaded, bookmarked, enrolled in, or purchased something. In a real portal these events would be logged automatically by your other pages. Here you can add them manually to test how activity changes the suggestions. Every search you accept, edit or reject is added here automatically.

### Search tab

1. Pick a profile from the top-right menu.
2. Type a query and press **Check query**.
3. Read the suggestion. Removed words are struck through in red and added words are underlined in green. Below it you'll see the explanation, the detected intent, any ambiguity, the spelling fixes and the context that was added.
4. Choose one of:
   * **Accept** to search with the suggestion.
   * **Edit** to change the text, then **Search with my edit**. Enter also submits.
   * **Reject, use my original** to search exactly what you typed.
5. Open **How this query was processed** to see every step, which engine ran it, and how long it took.

If the query needs no changes, the button reads **Search with this query** and Reject is hidden, since there's nothing to reject.

### History tab

Shows past searches, what was originally typed, and the decision for each one.

### Try these with the demo profiles

| Profile | Query | What to expect |
|---|---|---|
| Priya | `courses` | Context added: SQL / Power BI, intermediate, video |
| Priya | `sql coursse` | Spelling fixed plus context added |
| Arjun | `deployment guide` | AWS / microservices context from his recent download |
| Either | `python pandas groupby tutorial` | No change; the profile isn't used |
| Either | the same query twice | The second result is reused, with no AI call |

---

## 8. Configuration reference

Every setting can go in `.env` or be set as an environment variable. Environment variables take precedence over `.env`. Names are case-insensitive.

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | *(empty)* | Your key. Empty means rule-based mode. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model used for both AI stages |
| `OPENAI_TIMEOUT_SECONDS` | `20` | Per-request timeout |
| `OPENAI_MAX_RETRIES` | `2` | Automatic retries on rate limits or 5xx errors, with backoff |
| `OPENAI_TEMPERATURE` | `0.1` | Low means consistent output. Leave empty to not send it. |
| `DATABASE_URL` | `sqlite:///<project>/data/app.db` | Any SQLAlchemy URL; `postgres://` is fixed automatically |
| `MIN_QUERY_LENGTH` | `2` | Shorter queries are rejected |
| `MAX_QUERY_LENGTH` | `300` | Longer queries are rejected with a clear message |
| `MAX_REWRITE_WORDS` | `25` | Guardrail: longer AI rewrites are discarded |
| `DUPLICATE_WINDOW_SECONDS` | `300` | How long an identical query reuses the earlier result |
| `RECENT_ACTIVITY_LIMIT` | `20` | Activities loaded for ranking and the spelling vocabulary |
| `CONTEXT_ACTIVITY_LIMIT` | `5` | Activities actually sent to the AI |
| `RATE_LIMIT_PER_MINUTE` | `30` | Rewrites per user per minute; `0` disables the limit |
| `CORS_ORIGINS` | *(empty)* | Comma-separated origins, only if the UI is on another domain |
| `ENVIRONMENT` | `development` | `production` hides `/docs` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |

---

## 9. How each pipeline stage works

### Stage 1: Python preprocessing (`pipeline/preprocessing.py`)

**Validation.** The query is rejected with a readable message if it is empty, shorter than the minimum, longer than the maximum, or contains no letters or digits. Rejected queries never reach the AI, which saves money and blocks junk input.

**Normalisation:**

* Unicode is normalised, so look-alike characters and full-width forms become standard text.
* Invisible and control characters are removed. These often appear when text is pasted from Word or a PDF.
* Repeated spaces collapse to one, and repeated punctuation collapses too (`???` becomes `?`).
* An accidentally repeated word is removed (`python python jobs` becomes `python jobs`). Repeated numbers are kept.

**Spelling detection.** Each word is checked against an offline English dictionary. Some words are always skipped, because flagging them would cause more harm than good:

* Words under 4 letters, which are usually abbreviations (img, pdf, hrs).
* Anything containing a digit (i7, v2, python3).
* ALL-CAPS acronyms (AWS, SQL).
* Anything with symbols (node.js, c++, emails, URLs, hashtags).
* Non-English words. The AI handles these instead.
* Words in `app/data/domain_vocabulary.txt`.
* Words that appear in **this user's** profile or activity. A data engineer's "pyspark" is never flagged.

Candidates are ranked by:

1. Edit distance, where swapped letters count as a single typo.
2. Whether the first and last letters are kept. People rarely mistype those, so "developr" becomes "developer" rather than "develop".
3. How common the word is.

**Duplicate check.** A case- and punctuation-insensitive key (`Python Jobs!` equals `python jobs`) is compared against the user's recent queries. A match within the window is reused, unless any of these apply:

* The earlier result was rejected.
* The earlier result was produced in degraded mode.
* The profile has changed since.
* New non-search activity has been added since.

### Stage 2: AI understanding (`pipeline/understanding.py`)

The AI receives the cleaned query plus the dictionary's spelling hints, and returns JSON with these fields:

* `intent` and `intent_description`
* `spelling_corrections`, covering only genuine misspellings and never brands or technical terms
* `context_sufficient` (true or false)
* `ambiguity` (none, low or high) and `ambiguity_reason`
* `missing_context`, for example `["topic", "skill level"]`

The response is parsed into a strict Pydantic model:

* Missing fields get safe defaults.
* Wrong types are coerced (`"false"` becomes `false`).
* Unknown values are normalised.
* Malformed items are dropped individually.

If the response is unusable, the pipeline falls back instead of failing.

**Python applies the spelling fixes, not the AI.** Corrections are applied word-for-word to the user's text, keeping their capitalisation, and only for words that actually appear in the query. So this stage can only ever change spelling, never meaning.

### Stage 3: Python context retrieval (`pipeline/context.py`)

This stage runs only when the query lacks context or is highly ambiguous. It collects:

* role, location, interests and preferences from the profile
* recent activities, each scored as `2 × (words shared with the query) + recency`, with the top `CONTEXT_ACTIVITY_LIMIT` kept
* activity times in readable form ("2 days ago")

Name and email are deliberately excluded.

### Stage 4: AI rewriting + Python guardrails (`pipeline/rewriting.py`)

The AI is told to:

* preserve the intent
* keep every key term
* use only context that appears in the provided data, never invented facts
* prefer recent activity and stated preferences over general interests
* return a search query under 20 words

Python then checks the result:

* The rewrite must not be empty after stripping quotes and punctuation.
* It must be no longer than `MAX_REWRITE_WORDS`.
* At least **half of the user's key terms** must still be present. Simple plurals and inflections count.

If any check fails, the spelling-corrected query is shown instead, and the trace says why. This catches intent drift, as well as the AI being steered by text inside the query.

### Stage 5: User approval (`routers/queries.py`)

| Action | Final query | Saved status |
|---|---|---|
| Accept | The suggestion | `accepted` |
| Edit | Your edited text, cleaned and validated | `edited` |
| Reject | Exactly what you typed | `rejected` |

The decision is saved with a conditional update (`WHERE status = 'pending'`). A double click, or two open tabs, can't record two decisions; the second one gets a clear "already recorded" message. The final query is added as a `search` activity, which improves future suggestions.

**Connecting to your real search engine:** the decision endpoint returns `final_query`. In your portal, pass that value to your search backend (Elasticsearch, OpenSearch, Algolia, a SQL search, and so on). This project deliberately stops at producing the approved query.

---

## 10. Data model

**users**

| Column | Type | Notes |
|---|---|---|
| id | int | Primary key |
| name | text(100) | Required; never sent to the AI |
| email | text(255) | Optional, unique; never sent to the AI |
| role | text(100) | e.g. "Data analyst" |
| location | text(100) | |
| interests | JSON list | Up to 20 |
| preferences | JSON object | Up to 20 key/value pairs |
| created_at, updated_at | datetime (UTC) | |

**activities**: id, user_id (cascade delete), event_type (`search`, `view`, `click`, `download`, `bookmark`, `purchase` or `enroll`), content, created_at (indexed).

**query_logs** is the audit trail of every pipeline run. It stores:

* the original query, suggested query and final query
* the decision status, intent, and whether the AI was used
* whether the run was degraded, and which earlier log it was reused from (if any)
* `result`, the full response JSON including the step trace
* `created_at` and `decided_at`

Deleting a user deletes their activity and query logs (`ON DELETE CASCADE`).

All timestamps are stored as UTC and returned with a `Z` suffix, so browsers show the correct local time.

---

## 11. API reference

Interactive documentation with "Try it out" buttons is available at `/docs`. Errors always return `{"detail": "readable message"}`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Server, database and AI status |
| GET | `/api/users` | List profiles |
| POST | `/api/users` | Create a profile |
| GET | `/api/users/{id}` | Get one profile |
| PUT | `/api/users/{id}` | Replace a profile |
| DELETE | `/api/users/{id}` | Delete a profile and all its data |
| GET | `/api/users/{id}/activities?limit=30` | Recent activity |
| POST | `/api/users/{id}/activities` | Add activity |
| DELETE | `/api/users/{id}/activities/{activity_id}` | Remove activity |
| GET | `/api/users/{id}/queries?limit=30` | Search history |
| POST | `/api/rewrite` | Run the pipeline |
| POST | `/api/queries/{query_id}/decision` | Accept, edit or reject |

**Example: rewrite**

```bash
curl -X POST http://localhost:8000/api/rewrite \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "query": "sql coursse"}'
```

```json
{
  "query_id": 12,
  "original_query": "sql coursse",
  "corrected_query": "sql course",
  "suggested_query": "intermediate SQL video courses for data analysts",
  "changed": true,
  "intent": "find courses",
  "intent_description": "Find a SQL course.",
  "context_sufficient": false,
  "ambiguity": "low",
  "ambiguity_reason": "Skill level not given.",
  "spelling_fixes": [{"original": "coursse", "corrected": "course"}],
  "context_used": ["role: data analyst", "level: intermediate"],
  "explanation": "Fixed a typo and added your role and level.",
  "steps": [
    {"key": "preprocess", "title": "Python preprocessing", "engine": "python",
     "status": "done", "summary": "Query is valid (2 words). ...", "duration_ms": 4}
  ],
  "llm_used": true,
  "degraded": false,
  "notice": null,
  "reused_from": null
}
```

**Example: decision**

```bash
curl -X POST http://localhost:8000/api/queries/12/decision \
  -H "Content-Type: application/json" \
  -d '{"action": "edit", "edited_query": "SQL courses for beginners"}'
```

```json
{"query_id": 12, "status": "edited", "final_query": "SQL courses for beginners"}
```

**Status codes**

| Code | Meaning |
|---|---|
| 200 / 201 / 204 | Success |
| 404 | Profile, activity or suggestion not found |
| 409 | Duplicate email, or a decision was already recorded |
| 422 | Invalid input (the message says what to fix) |
| 429 | Rate limit reached (see the `Retry-After` header) |
| 500 | Unexpected server error (details are in the server log, never sent to the client) |

---

## 12. Testing

```bash
pip install -r requirements-dev.txt
pytest -q
```

Tests use a **fake AI**, so they are free, fast and work offline. Each test gets its own temporary database, and your real `.env` is ignored. The 38 tests cover:

* **Validation and normalisation:** every rule in stage 1.
* **Spelling:** detection, ranking, skip rules, the domain vocabulary, per-user vocabulary, and case preservation.
* **AI call counts:** a clear query makes one AI call and never loads the profile; a vague query makes two.
* **Privacy:** activity reaches the AI, but name and email never do.
* **AI spelling handling:** fixes are applied by Python, and invented corrections are ignored.
* **Guardrails:** unsafe rewrites are rejected and the trace explains why.
* **Outages and no key:** an AI outage at either stage falls back gracefully, and the app works with no API key.
* **Duplicates:** repeated queries are reused, and the cache is invalidated when the profile changes.
* **API behaviour:** readable error messages, the rate limit, accept/edit/reject, double-decision protection, and cascade delete.

**Manual check with the real AI:** start the server with your key and try the queries in [section 7](#7-using-the-app).

---

## 13. Common problems this project prevents

These are the issues that usually appear in projects like this one, and how each is handled here.

| # | Problem | Prevention |
|---|---|---|
| 1 | AI returns broken or unexpected JSON | JSON mode plus strict Pydantic parsing with defaults; malformed items dropped; fallback on failure |
| 2 | AI changes what the user meant | Prompt rules plus a Python key-term guardrail, and the user approves every change |
| 3 | AI "corrects" technical or brand words | Domain vocabulary, per-user vocabulary, skip rules, and the AI confirming fixes in context |
| 4 | Prompt injection ("ignore previous instructions…") | Input wrapped in tags and treated as data; output still has to pass the guardrails |
| 5 | Search breaks when OpenAI is down, slow or out of quota | Timeouts, retries, and a friendly fallback with spelling-only results and a notice |
| 6 | Unexpected API bills | Only the needed calls are made, duplicates are cached, length limits apply, rate limiting is on, and a small default model is used |
| 7 | API key leaked to the browser or Git | Server-side only; `.gitignore` and `.dockerignore`; Secrets Manager on AWS |
| 8 | Personal data sent to a third party | Name and email are never included; only the top few relevant activities are sent |
| 9 | Context added to queries that didn't need it | Context is fetched only when the AI says it is insufficient or highly ambiguous |
| 10 | Double submissions and double decisions | UI busy guards plus a conditional database update returning 409 |
| 11 | Timezone bugs ("5 hours ago" for something just now) | Naive UTC stored everywhere, `Z` on output |
| 12 | XSS through a malicious query | The UI only uses `textContent`; tested with `<img onerror=…>` in a real browser |
| 13 | "Database is locked" with SQLite | WAL mode, a busy timeout, and thread-safe connections |
| 14 | `.env` not loaded when started from another folder | The `.env` path is resolved from the project root |
| 15 | An empty `OPENAI_TEMPERATURE=` crashing startup | An empty value means "don't send it" |
| 16 | A model rejecting the temperature setting | Automatic retry without it |
| 17 | `postgres://` URLs rejected by SQLAlchemy | Rewritten automatically to `postgresql+psycopg://` |
| 18 | Placeholder key left in `.env` | Treated as "AI off" with a clear banner, instead of failing on every request |
| 19 | Cryptic validation errors | A single readable sentence that names the field |
| 20 | Stack traces leaking to clients | Generic 500 message to clients; full trace in the server log |

---

## 14. Security and privacy

**Already in place:**

* input validation on every endpoint
* per-user rate limiting
* XSS-safe rendering
* security headers (`nosniff`, `DENY` framing, `same-origin` referrer, `no-store` on API responses)
* a non-root Docker user
* server-only API key
* name and email excluded from AI calls
* cascade delete for full removal of a user's data

**Important limitation: there is no login.** This is a demo of the agent, so the browser chooses which profile to act as by sending `user_id`. **Never deploy it publicly as-is.** In a real portal:

1. Put the app behind your portal's existing authentication (session cookie, JWT, AWS Cognito, or an SSO such as Okta or Azure AD).
2. Read the user id from the verified session or token on the server, and **remove `user_id` from the request body**. The place to change is marked with a `NOTE` comment in `app/routers/queries.py`.
3. Protect the profile endpoints so users can only read and change their own data, and restrict deletion to admins.

**Data sent to OpenAI:** the query, the spelling hints, and, only when needed, role, location, interests, preferences and a few short activity descriptions. If you handle regulated data, review OpenAI's data-usage terms and your company's policy, and consider Azure OpenAI or a private model.

---

## 15. Running with Docker

You need Docker Desktop (Windows/Mac) or Docker Engine (Linux).

```bash
cp .env.example .env            # then add your key
docker compose up --build       # first run
docker compose up -d            # later runs, in the background
docker compose logs -f          # follow logs
docker compose down             # stop
```

The app runs at http://localhost:8000. The `./data` folder is mounted into the container, so the SQLite database survives rebuilds.

To load demo data inside the container:

```bash
docker compose exec app python -m scripts.seed
```

The image is built on `python:3.12-slim`, runs as a non-root user, includes a health check, and never contains `.env`.

---

## 16. Deploying to AWS

### Which option to choose

| Option | Best for | Effort | Notes |
|---|---|---|---|
| **AWS App Runner** | First deployment, small to medium traffic | Low | Container in, HTTPS URL out; autoscaling built in |
| **ECS on Fargate + ALB** | Production at company scale | Medium | Full control of networking, scaling and private subnets |
| **Elastic Beanstalk (Docker)** | Teams already using Beanstalk | Low to medium | |
| **EC2** | Learning, or full control on one server | Medium | You manage the OS, patches and HTTPS |

**Recommendation:** start on App Runner, and move to ECS Fargate when you need private networking or more control.

### Before deploying: switch to PostgreSQL

SQLite stores data in a file inside the container. Containers on App Runner and Fargate are temporary, and there may be several of them, so **you will lose data with SQLite on AWS**. Use **Amazon RDS for PostgreSQL** (or Aurora PostgreSQL):

1. Create an RDS PostgreSQL instance. `db.t4g.micro` is fine to start. Enable automated backups.
2. In `requirements.txt`, uncomment `psycopg[binary]`.
3. Set `DATABASE_URL=postgresql+psycopg://USER:PASSWORD@ENDPOINT:5432/DBNAME`.

No code changes are needed. Tables are created on first start.

### Step-by-step: App Runner

**1. Install the tools.** Install the AWS CLI, then run `aws configure` with an IAM user or role that can use ECR, App Runner, Secrets Manager and RDS.

**2. Push the image to ECR** (Elastic Container Registry). Replace `<ACCOUNT_ID>` and `<REGION>`, for example `ap-south-1` for Mumbai:

```bash
aws ecr create-repository --repository-name query-rewriter
aws ecr get-login-password --region <REGION> | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com
docker build -t query-rewriter .
docker tag query-rewriter:latest <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/query-rewriter:latest
docker push <ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/query-rewriter:latest
```

> **Apple Silicon (M1/M2/M3) Macs:** build with `docker build --platform linux/amd64 -t query-rewriter .`, otherwise the container won't start on AWS.

**3. Store secrets** in AWS Secrets Manager: one secret for `OPENAI_API_KEY`, and one for `DATABASE_URL` (it contains the database password).

```bash
aws secretsmanager create-secret --name query-rewriter/openai-key --secret-string "sk-..."
aws secretsmanager create-secret --name query-rewriter/database-url --secret-string "postgresql+psycopg://..."
```

**4. Create the App Runner service** in the console (App Runner → Create service):

* **Source:** your ECR image, with automatic deployment on push if you want it.
* **Port:** `8000`.
* **Environment variables:**
  * `ENVIRONMENT=production`
  * `LOG_LEVEL=INFO`
  * `OPENAI_API_KEY` → reference the secret
  * `DATABASE_URL` → reference the secret
* **Instance role:** a role allowed to read those two secrets (`secretsmanager:GetSecretValue`).
* **Health check:** HTTP, path `/api/health`.
* **Networking:** add a **VPC connector** so App Runner can reach your RDS database. The RDS security group must allow port 5432 from the connector's security group.

**5. Open the URL** App Runner gives you (HTTPS is included). Add a custom domain from the console if needed.

### Step-by-step: ECS Fargate (production layout)

```
Internet ──► Route 53 ──► ALB (HTTPS, ACM certificate, optional WAF)
                              │
                     private subnets
                              ▼
                  ECS Fargate service (2+ tasks)
                              │
                   ┌──────────┴───────────┐
                   ▼                      ▼
          RDS PostgreSQL          Secrets Manager
          (private, Multi-AZ)     (OpenAI key, DB URL)
                   
   Tasks reach OpenAI through a NAT gateway. Logs go to CloudWatch.
```

1. Push the image to ECR, as above.
2. Create a VPC with public subnets (for the ALB) and private subnets (for tasks and RDS), plus a NAT gateway so tasks can call OpenAI.
3. Create RDS PostgreSQL in the private subnets.
4. Create an ECS cluster, then a **task definition**:
   * Fargate, 0.5 vCPU / 1 GB memory to start
   * container port 8000
   * `secrets` mapped from Secrets Manager
   * log driver `awslogs`, sending to CloudWatch
5. Create an **ALB** with an HTTPS listener using an ACM certificate. The target group should use type IP, port 8000, and health check path `/api/health`.
6. Create the **ECS service**: desired count 2 or more, in the private subnets, attached to the target group. Optionally add target-tracking autoscaling on CPU (for example 60%).
7. Configure security groups:
   * ALB: allow 443 from the internet.
   * Tasks: allow 8000 from the ALB only.
   * RDS: allow 5432 from the tasks only.

### Changes for multiple servers

| Concern | Single server (now) | Multiple servers on AWS |
|---|---|---|
| Rate limiting | In-memory, per process | **AWS WAF** rate-based rule on the ALB, or Redis (ElastiCache) |
| Duplicate cache | Database (already shared) | Works as-is |
| Database | SQLite | **RDS PostgreSQL** |
| Secrets | `.env` file | **Secrets Manager** |
| Logs | Terminal | **CloudWatch Logs** (already prints to stdout) |
| Schema changes | `create_all` on startup | **Alembic** migrations, run as a one-off task before deploying |

### Database migrations with Alembic (needed before your first schema change in production)

`create_all` creates missing tables, but it never alters existing ones. Before you change a model in production:

```bash
pip install alembic
alembic init migrations
# In migrations/env.py: import Base from app.database and set target_metadata = Base.metadata,
# and read the URL from app.config.get_settings().database_url
alembic revision --autogenerate -m "initial"
alembic upgrade head
```

After that, every model change becomes `alembic revision --autogenerate -m "..."` followed by `alembic upgrade head` during deployment.

### CI/CD (optional)

A GitHub Actions workflow can, on every push to `main`:

1. Run `pytest`.
2. Build the image.
3. Push it to ECR.
4. Have App Runner auto-deploy, or update the ECS service.

Use GitHub's OIDC integration with an IAM role instead of storing AWS keys in GitHub.

### Rough monthly cost (small usage)

These are estimates; check AWS pricing for your region.

* **App Runner:** about $5–25
* **RDS `db.t4g.micro`:** about $15–20
* **Secrets Manager:** about $0.40 per secret
* **OpenAI `gpt-4o-mini`:** usually well under a cent per rewrite

For ECS, a NAT gateway (about $32 or more) and an ALB (about $16 or more) add a fixed cost on top.

---

## 17. Production checklist

- [ ] Real authentication; user id taken from the session or token, not the request body ([section 14](#14-security-and-privacy))
- [ ] PostgreSQL (RDS) with automated backups
- [ ] Alembic migrations set up
- [ ] Secrets in Secrets Manager; no `.env` on servers
- [ ] `ENVIRONMENT=production` (hides `/docs`)
- [ ] HTTPS only (App Runner or ALB with ACM)
- [ ] Shared rate limiting (AWS WAF or Redis)
- [ ] CloudWatch alarms on 5xx rate, latency, and the health check
- [ ] An OpenAI usage limit and budget alert set in the OpenAI dashboard
- [ ] `domain_vocabulary.txt` filled with your portal's product and course names
- [ ] Prompts reviewed against real queries from your portal
- [ ] A data-retention policy for `query_logs` and `activities` (for example, delete after 90 days)
- [ ] Privacy review of what is sent to OpenAI

---

## 18. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Banner says "AI is off" | No key, or the placeholder is still there | Set `OPENAI_API_KEY` in `.env` and restart |
| "OpenAI rejected the API key" | Wrong, revoked or mistyped key | Create a new key and check for spaces or quotes |
| "rate limit or quota reached" | No billing, or too many requests | Add billing or credits at platform.openai.com, or wait |
| "Model '…' was not found" | Typo, or no access to that model | Fix `OPENAI_MODEL` |
| "Couldn't reach OpenAI" | No internet, a proxy, or a firewall | Check your connection, VPN or corporate proxy |
| `ModuleNotFoundError: No module named 'app'` | Started from the wrong folder | Run commands from the project root |
| `ModuleNotFoundError` for fastapi or others | Virtual environment not active | Activate `.venv`, then `pip install -r requirements.txt` |
| `'uvicorn' is not recognized` | Same cause | Activate `.venv`, or run `python -m uvicorn app.main:app --reload` |
| "Address already in use" / port 8000 busy | Another server is running | Stop it, or use `--port 8001` |
| PowerShell won't activate the venv | Script policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Fonts look plain | No internet for Google Fonts | Cosmetic only; system fonts are used |
| Changes in `.env` are ignored | Settings are read at startup | Restart the server |
| A word keeps being "corrected" wrongly | Not in the vocabulary | Add it to `app/data/domain_vocabulary.txt` |
| Want a clean database | | `python -m scripts.seed --reset`, or delete `data/app.db` |
| Container exits immediately on AWS | Image built on ARM | Rebuild with `--platform linux/amd64` |
| App Runner can't reach RDS | No VPC connector, or a security group issue | Add a VPC connector and allow 5432 from it |

---

## 19. Extending the project

* **Your real search engine:** send `final_query` from the decision response to Elasticsearch, OpenSearch or Algolia, and show the results.
* **Automatic activity logging:** have your portal's pages call `POST /api/users/{id}/activities` when users view or download items.
* **Better relevance:** replace word-overlap ranking in `context.py` with embeddings (the OpenAI embeddings API plus pgvector in PostgreSQL).
* **Learning from decisions:** `query_logs` records which suggestions were accepted, edited or rejected. Use it to measure the acceptance rate and to tune prompts.
* **Clarifying questions:** when ambiguity is `high` and the profile is empty, ask the user to pick a meaning instead of guessing.
* **Multiple languages:** the AI already handles non-English text; add a language preference to the profile and to the prompt.
* **Analytics dashboard:** track acceptance rate, average latency, AI cost per day, and fallback rate.

---

### Where things live, at a glance

| I want to… | Edit this |
|---|---|
| Change the AI's behaviour | `app/pipeline/prompts.py` |
| Change validation limits | `.env` (see [section 8](#8-configuration-reference)) |
| Stop a word being "corrected" | `app/data/domain_vocabulary.txt` |
| Change which profile data is used | `app/pipeline/context.py` |
| Change guardrail strictness | `MIN_TERM_OVERLAP` in `app/pipeline/rewriting.py` |
| Change the look | `static/styles.css` |
| Add authentication | `app/routers/queries.py` and `app/dependencies.py` |
