# 🌟 Perlica — Intelligent Discord Financial, Productivity & Knowledge Copilot

> **Perlica** is an intelligent, zero-friction Discord personal assistant powered by Groq LLMs (Llama 3.3 70B & Llama 3.1 8B fallback), Whisper Large v3 Turbo, Llama 3.2 Vision OCR, and an **Evidence-Grounded Personal Knowledge & Codebase Copilot** running local FastEmbed ONNX vectors and SQLite FTS5.
>
> Designed to live entirely in your Discord DMs, Perlica effortlessly tracks everyday expenses, manages multi-phase tasks, monitors monthly budgets, accumulates dedicated savings goals, and answers technical codebase and document queries with source citations and zero hallucination.

---

## ⚡ Core Highlights & Capabilities

### 🤖 1. Evidence-Grounded Knowledge & Codebase Copilot
* **Multi-Source Ingestion**:
  * 🐙 **GitHub Repositories (`/repo sync`)**: 1-call recursive tree crawler via GitHub REST API with incremental Git SHA reconciliation (skips unchanged files, replaces modified code in an atomic commit, and purges deleted files with zero ghost chunks).
  * 📄 **PDF Documents (`/ingest source_type:pdf`)**: Page-aware extraction via `pypdf` with thread offloading and clause detection (`[contract.pdf > Page 4 > Clause 8.1]`).
  * 🌐 **Web Articles (`/ingest source_type:web`)**: SSRF-hardened HTML extraction via `trafilatura`.
  * 📝 **Instant Notes (`/note`)**: Real-time indexed knowledge snippets saved directly into SQLite.
* **Hybrid Retrieval (FastEmbed + SQLite FTS5)**:
  * Local quantized 384-d `bge-small-en-v1.5` embeddings (~45MB RAM, 8ms latency) + SQLite FTS5 with symbol-aware `_` tokenization.
  * Merged via **Reciprocal Rank Fusion (RRF)** for high-precision technical symbol and document search.
* **The 3-State Abstention Policy**:
  * **State 1 (Zero-Result Abstention)**: Unanswerable queries **never invoke the LLM** and return deterministic notices immediately.
  * **State 2 (Partial Coverage Disclosure)**: Discloses coverage ratios (`5 / 10 files indexed`) when candidate sources are partially indexed without asserting non-existence.
  * **State 3 (Grounded Synthesis)**: Synthesizes responses with clickable citations and preserves verbatim excerpts in `answers` & `answer_evidence` (survives source purges).
* **1-Tap Raw Chunk Inspector**: Click `[📄 View Raw Source]` on any answer to inspect verbatim evidence excerpts in a Discord modal.

---

### 🎙️ 2. Multi-Modal Expense & Task Ingestion
* **Natural Malaysian & Manglish Parsing:** Log entries like `tapau nasi kandar rm 14.50 semalam`, `isi minyak petronas rm 50`, `bayar bil tnb rm 120 kelmarin`, `bought $100 s&p500`, or `Create task 'Launch App' with 3 phases: 1. Design, 2. Backend, 3. Testing`.
* **Voice Notes (Whisper Audio Transcription):** Send voice notes directly from your phone while driving. Perlica transcribes the audio and extracts structured actions.
* **Receipt Image OCR (Groq Vision):** Snap a photo of a physical receipt or invoice for automatic itemization and categorization.
* **200L RON95 Fuel Subsidy Tracker:** Accurately tracks subsidized petrol at RM 1.99/L against the 200L monthly quota while isolating unsubsidized grades (RON97, Diesel) and non-fuel purchases.

---

### 📋 3. Interactive 3-Button Action Gates & Guardrails
* **Zero Accidental Writes:** Every detected entry triggers an interactive 3-button confirmation preview:
  ```text
  [✅ Confirm]  [✏️ Edit]  [❌ Reject]
  ```
* **Double-Tap Duplicate Protection:** Automatically flags identical transactions logged within a 5-minute collision window (`[⚠️ Log Anyway]` / `[🗑️ Discard Duplicate]`).
* **10-Second Quick Undo (`[↩️ Quick Undo (10s)]`):** Ephemeral toast allowing immediate 1-tap database rollback on every confirmed action.

---

### 🏆 4. Dedicated Wealth & DCA Portfolio (Budget-Immune)
* **Isolated Asset Accumulation:** Dollar-Cost Averaging (DCA) commitments into equities, ETFs, crypto, and savings goals are tracked separately in `expenses(asset_name, asset_class)` and `goals`.
* **Non-Deductible Guarantee:** Investments and savings **never deduct** from your daily living expense allowance!
* **Deterministic Asset Normalization:** Automatically resolves variants (e.g. `voo`, `vanguard 500`, `s&p 500`) to canonical names (`S&P 500`, `Equities`).

---

### 📅 5. Daily Command Center & Interactive Utilities
* **Live Command Center (`/dashboard`):** Pinned overview of spending, safe daily allowance, due bills, open tasks, active goals, and DCA progress.
* **Daily Focus Mode (`/focus`):** Single-task productivity widget with 1-tap completion, skip, and snooze.
* **Paginated Transaction History (`/history`):** Browse past expenses by month with interactive pagination and a 1-tap delete dropdown.
* **Malaysian Public Holidays & Long Weekends (`/holidays`):** Countdown of upcoming Federal and Selangor state holidays.
* **Standalone HTML Executive Reports (`/report`):** Dark-mode standalone HTML report with CSS charts, budget gauges, and asset allocation tables.

---

## 🔒 Safety & Security Architecture

1. **SSRF Protection (`src/security.py`)**: Centralized security client validates URLs before fetching, resolves DNS, blocks private/loopback/link-local IPv4 & IPv6 ranges (`127.0.0.0/8`, `169.254.169.254`, `[::1]`, `10.0.0.0/8`, etc.), and validates per-hop redirects.
2. **Secret Path & Content Denylist**: Blocks secret files (`.env*`, `*.pem`, `*.key`, `id_rsa*`, `credentials.json`) and performs pre-indexing regex inspection for private keys and API tokens.
3. **Prompt-Injection Isolation**: All retrieved evidence is enclosed in `<BEGIN UNTRUSTED EVIDENCE>` blocks with strict system instructions never to execute retrieved text as instructions.
4. **Discord Access Authorization**: Authorization checks against `ALLOWED_DISCORD_USERS` and `ALLOWED_USER_ID` protect private codebase and contract indexes.
5. **Mandatory SQLite Pragmas**: All database connections execute `PRAGMA foreign_keys = ON;`, `PRAGMA recursive_triggers = ON;`, and `PRAGMA journal_mode = WAL;`.

---

## 🛠️ Project Structure

```text
perlica/
├── knowledge/            # Local drop folder for Markdown & PDF documents
├── src/
│   ├── config.py         # Pydantic v2 application configuration & secret denylists
│   ├── security.py       # SSRF-safe HTTP client, IP validator & secret regex scanner
│   ├── database.py       # Async aiosqlite layer (WAL pragmas, FTS5 triggers, vector CRUD, manifest diffing)
│   ├── extractor.py      # Groq LLM extraction engine, Whisper voice transcription & Vision OCR
│   ├── pdf_parser.py     # Page-aware PDF extractor using pypdf with thread pool offloading
│   ├── web_scraper.py    # SSRF-safe HTML article extractor using trafilatura
│   ├── github_sync.py    # GitHub REST API crawler, commit SHA diffing & Python AST chunker
│   ├── rag_engine.py     # FastEmbed ONNX embedder, SQLite FTS5 BM25 search, RRF ranker & grounded LLM
│   ├── formatters.py     # Discord UI embed builders, ASCII sparklines, Copilot QA embeds & HTML report
│   └── bot.py            # Discord.py bot instance, slash commands, views, modals & background workers
├── tests/
│   ├── test_security_and_ssrf.py     # SSRF validation & secret scanning tests
│   ├── test_reconciliation.py        # Manifest reconciliation, FTS cascades & atomic rollback tests
│   ├── test_rag_engine.py            # Python AST chunking, symbol tokenization & abstention tests
│   ├── test_copilot_golden.py        # 10 Golden Retrieval Queries benchmark & telemetry tests
│   ├── test_duplicate_guardrail.py   # Double-tap duplicate collision tests
│   ├── test_malaysian_localization.py# Fuel subsidy boundaries & holiday calendar tests
│   ├── test_wealth_engine.py         # Canonical asset normalization & DCA tests
│   ├── test_database.py              # Core SQLite CRUD & transaction tests
│   ├── test_extractor.py             # LLM extraction prompt & schema validation tests
│   ├── test_formatters.py            # Embed formatting and progress bar tests
│   ├── test_edge_cases.py            # Edge case suite (goals, rollbacks, 25-menu cap, heatmaps)
│   ├── test_advanced_uiux.py         # Pagination, custom IDs & autocomplete cap tests
│   ├── test_uiux_enhancements.py     # Milestone deduplication & stateless views
│   └── test_end_to_end_mock.py       # End-to-end simulated user interaction tests
├── pytest.ini            # Pytest configuration with asyncio mode
├── requirements.txt      # Production dependencies (discord.py, fastembed, pypdf, trafilatura, groq, etc.)
└── README.md             # Project documentation
```

---

## 🚀 Getting Started

### 1. Prerequisites
* Python 3.11+
* A [Discord Bot Token](https://discord.com/developers/applications) with Message Content and Server Members intents enabled.
* A [Groq API Key](https://console.groq.com/).
* *(Optional)* A [GitHub Personal Access Token](https://github.com/settings/tokens) for indexing private repositories.

### 2. Installation & Setup
```bash
git clone https://github.com/DanielKoh2004/perlica.git
cd perlica
pip install -r requirements.txt
```

### 3. Environment Variables
Create a `.env` file in the root directory:

```env
DISCORD_TOKEN=your_discord_bot_token_here
ALLOWED_USER_ID=your_discord_user_id_here
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile
GITHUB_TOKEN=your_github_pat_token_here
TIMEZONE=Asia/Kuala_Lumpur
DATABASE_PATH=tracker.db
DAILY_SUMMARY_TIME=22:00
MORNING_BRIEFING_TIME=08:30
WEEKLY_REVIEW_TIME=20:00
```

---

## 🧪 Running the Test Suite

Perlica comes with **75 comprehensive automated unit, integration, and benchmark tests**:

```bash
python -m pytest -v
```

Expected output:
```text
============================= 75 passed in 7.69s =============================
```

---

## 💬 Command Reference & Cheat Sheet

| Feature | Slash Command | DM Chat Trigger | Description |
| :--- | :--- | :--- | :--- |
| **Ask Copilot** | `/ask <query> [in_source]` | `ask: <query>` or `? <query>` | Evidence-grounded technical Q&A with deep citations & raw inspector |
| **Manage Repos** | `/repo sync\|purge\|info` | — | Sync GitHub repo with incremental Git SHA reconciliation in background |
| **Ingest URL / PDF**| `/ingest web\|pdf <target>` | — | SSRF-safe webpage or local PDF extraction |
| **Instant Note** | `/note <content> [title]` | — | Save and immediately index an instant knowledge snippet |
| **Sources Dashboard**| `/sources` | `sources` or `kb` | Live dashboard of all indexed sources, coverage ratios & status |
| **Command Center** | `/dashboard` | `dashboard` | Pinned live dashboard with 1-click in-place refresh |
| **Daily Focus** | `/focus` | `focus` | Daily single-task focus widget with skip & snooze |
| **Transaction History**| `/history` | — | Paginated transaction explorer with 1-tap delete dropdown |
| **Public Holidays** | `/holidays [days]` | `holidays` or `cuti` | Upcoming Selangor & Federal public holidays & long weekends |
| **Wealth & DCA** | `/investments` | `investments` or `dca` | Dedicated Wealth portfolio, asset allocation & DCA tracking |
| **Savings Goals** | `/goals` | `goals` | View active savings goals and progress meters |
| **Open Tasks** | `/tasks` | `tasks` | Batch task completion dropdown |
| **Budget Health** | `/budgets` | `budgets` | View and adjust monthly category budget limits |
| **HTML Report** | `/report` | `report` | Generate & download standalone dark-mode HTML executive report |
| **Category Inspector**| `/category` | — | Itemized category inspector with autocomplete |
| **Force Sync** | — | `!sync` | Force sync Discord application slash commands |

---

## 📄 License
This project is open-source and available under the [MIT License](LICENSE).
