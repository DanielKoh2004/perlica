<div align="center">

<a href="https://github.com/DanielKoh2004/perlica">
  <img src="https://capsule-render.vercel.app/api?type=rect&color=0B1020&height=230&section=header&text=PERLICA&fontSize=74&fontColor=FFFFFF&animation=fadeIn&fontAlignY=43&desc=Your%20life%20OS%2C%20inside%20Discord.&descAlignY=67&descSize=18" width="100%"/>
</a>

<br>

### 🧠 **Personal Intelligence · Built for Discord**

**Capture what matters. Remember what happened. Surface what comes next.**

<br>

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/Discord.py-2.3%2B-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
[![Groq](https://img.shields.io/badge/LLM-Groq-F55036)](https://groq.com/)
[![Pydantic](https://img.shields.io/badge/Validation-Pydantic-E92063)](https://docs.pydantic.dev/)
[![SQLite](https://img.shields.io/badge/Storage-SQLite-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![FastEmbed](https://img.shields.io/badge/RAG-FastEmbed-7C3AED)](https://github.com/qdrant/fastembed)

<br><br>

> **Perlica turns Discord into a personal command center.**
>
> Expenses, tasks, goals, bills, investments, reminders, summaries and even your GitHub codebase — captured through natural language and organized into one persistent assistant.

<br>

</div>

---

## 🌌 The Idea

Most productivity tools make you **enter information into forms**.

Perlica tries to make the interaction disappear.

You say:

```text
"Spent RM18.50 on makan at the mamak."
```

Perlica can understand the intent, normalize the category, and turn the message into structured data.

Or:

```text
"Remind me to finish the FYP methodology tomorrow at 9pm."
```

Or:

```text
"How much did I spend on transport this month?"
```

Or even:

```text
"Read my repo and explain how the RAG pipeline works."
```

The interaction stays conversational.

The data underneath stays structured.

---

# ⚡ What Perlica Does

<table>
<tr>
<td width="33%" align="center">

### 💸 MONEY

Expenses<br>
Budgets<br>
Bills<br>
Investments

</td>
<td width="33%" align="center">

### ✅ EXECUTION

Tasks<br>
Projects<br>
Goals<br>
Milestones

</td>
<td width="33%" align="center">

### 🧠 INTELLIGENCE

RAG Copilot<br>
GitHub Knowledge<br>
Web Research<br>
Daily Briefings

</td>
</tr>
</table>

Perlica is not just a chatbot sitting on top of an LLM.

It is a **stateful personal agent** with a database, deterministic application logic, structured extraction, retrieval, scheduling and interactive Discord interfaces.

---

# 🧩 Core Capabilities

## 💰 Personal Finance

Talk to Perlica about money naturally instead of maintaining spreadsheets manually.

| Capability | Example |
|---|---|
| **Expense capture** | `"RM25 lunch at Mamak"` |
| **Smart categorisation** | `makan` → Food & Dining |
| **Budget tracking** | `"Set my food budget to RM800"` |
| **Recurring bills** | Track subscriptions and commitments |
| **Investment tracking** | Stocks, ETFs, crypto, gold and savings |
| **Fuel tracking** | Extract fuel details from receipts |
| **Spending analysis** | Ask for weekly or monthly breakdowns |

The extractor normalizes free-form input into typed expense and task objects instead of storing raw chat messages as the source of truth. fileciteturn10file0

---

## ✅ Tasks & Projects

Perlica treats a task as more than a line of text.

A task can carry:

```text
Description
Priority
Due date
Due time
Project phases / milestones
Status
```

That means a message like:

```text
"Finish the hackathon prototype by Friday. Break it into
UI, API integration and testing. High priority."
```

can become an actionable project rather than a vague reminder.

Tasks can also be edited, deleted, reopened, snoozed and completed through the Discord workflow.

---

## 🎯 Goals That Become Actions

Large goals are difficult to execute when they stay abstract.

Perlica's goal flow is designed to turn them into **phases and concrete next actions**.

```text
BIG GOAL
   │
   ├── Phase 1
   │     ├── Task A
   │     └── Task B
   │
   ├── Phase 2
   │     ├── Task C
   │     └── Task D
   │
   └── Milestone → ✅
```

The result is a system that can remember not only **what you want**, but also **where you are in getting there**.

---

# 🤖 The Intelligence Layer

## Natural-Language Extraction

Perlica uses an LLM-backed extraction layer with structured Pydantic schemas.

The model doesn't simply answer the user.

It first determines the **intent and structured data** hidden inside the message.

```text
Discord Message
      │
      ▼
┌────────────────────┐
│ Intent + Extraction │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ Structured Payload │
├────────────────────┤
│ expenses[]         │
│ tasks[]            │
│ edits / deletes    │
│ budgets            │
│ goals              │
│ queries             │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ Application Logic  │
└─────────┬──────────┘
          │
          ▼
      SQLite DB
```

This architecture keeps **LLM interpretation** separate from **database mutation**.

---

# 🔎 Perlica Copilot

The more unusual part of Perlica is its ability to become a **knowledge-aware coding and research assistant**.

You can point it at knowledge sources and ask questions from Discord rather than opening every file manually.

### Knowledge sources

```text
📁 Local Markdown / PDF knowledge
🐙 GitHub repositories
🌐 Public web pages
```

The codebase-aware pipeline can structurally chunk Python using the AST, preserve file paths and line ranges, and create GitHub permalinks for retrieved code evidence. fileciteturn7file0

---

## 🧠 Hybrid RAG

Perlica combines two retrieval signals:

```text
                 USER QUESTION
                       │
              ┌────────┴────────┐
              ▼                 ▼
        SQLite FTS5          Dense Search
          BM25              FastEmbed
              │                 │
              └────────┬────────┘
                       ▼
              Reciprocal Rank Fusion
                       │
                       ▼
               Relevant Evidence
                       │
                       ▼
                LLM Synthesis
                       │
                       ▼
             Answer + Citations
```

The retrieval engine uses **SQLite FTS5/BM25 + FastEmbed dense embeddings + Reciprocal Rank Fusion (RRF)** rather than relying on a single search mechanism. It also records coverage and retrieval telemetry as part of the structured answer model. fileciteturn5file0

---

# 🔗 Answers With Evidence

Perlica's Copilot response model is intentionally structured around provenance.

Each answer can carry:

```text
Answer
Query
Citations
Evidence IDs
Evidence payloads
Coverage status
Answer status
Retrieval telemetry
```

A citation can retain:

```text
Source name
Source type
Location
Chunk ID
File path
Permalink
```

So instead of:

> "I think this function does X."

you can get:

```text
Answer
   │
   ├── Source: src/rag_engine.py
   │      └── Lines 120–170
   │
   ├── Source: knowledge/architecture.md
   │      └── Section: Retrieval
   │
   └── Coverage: COMPLETE
```

That distinction matters when the assistant is explaining a real codebase.

---

# 🛡️ Security By Design

A personal assistant should not accidentally become a data exfiltration assistant.

Perlica therefore places security checks around the knowledge pipeline.

### Repository filtering

The GitHub sync layer can exclude:

```text
.env files
Private keys
Certificates
Credential files
Binary files
Lockfiles
Large files
Ignored directories
```

The repository scanner also checks content for secrets before indexing it. fileciteturn7file0

### Web safety

Web ingestion uses a safe URL fetcher with redirect and timeout controls, extracts readable content, and rejects extracted content when secret-like material is detected. fileciteturn11file0

### Access control

Copilot access can be restricted through configured Discord user IDs, keeping the knowledge interface intentionally scoped. fileciteturn6file0

---

# ⏰ Your Assistant Should Know When to Speak

Perlica isn't designed to wait for `/ask` all day.

Scheduled workflows include configurable:

```text
🌅 Morning Briefing
🌙 Daily Summary
📅 Weekly Executive Review
🔄 Repository Auto-Sync
```

The default configuration uses **Asia/Kuala_Lumpur** as the timezone and supports configurable dispatch times. fileciteturn6file0

Think of it as:

```text
             YOU
              │
       ┌──────┴──────┐
       ▼             ▼
   "Ask Perlica"   "Perlica tells me"
       │             │
       └──────┬──────┘
              ▼
      Persistent Context
```

---

# 📅 The Daily Loop

A typical day can look like this:

```text
08:30 ── 🌅 Morning Briefing
          ↓
          What matters today?

10:15 ── 💬 Capture expense / task
          ↓
          No spreadsheet. No form.

14:00 ── 🧠 Ask Copilot
          ↓
          Search personal knowledge / code

18:30 ── ✅ Complete tasks
          ↓
          Progress updates automatically

22:00 ── 🌙 Daily Summary
          ↓
          What happened today?
          What changed?
          What's next?
```

The result is a lightweight feedback loop between **capture → context → action → reflection**.

---

# 🏗️ Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                         DISCORD                             │
│                                                             │
│ Messages · DMs · Buttons · Modals · Selectors · Voice      │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      PERLICA AGENT                          │
│                                                             │
│  ┌──────────────┐   ┌────────────────┐   ┌──────────────┐ │
│  │ Extraction   │   │ Action / Query │   │ Scheduler    │ │
│  │ Engine       │   │ Router         │   │ & Briefings  │ │
│  └──────┬───────┘   └───────┬────────┘   └──────┬───────┘ │
│         │                    │                   │         │
│         └────────────────────┼───────────────────┘         │
│                              ▼                             │
│                    ┌──────────────────┐                   │
│                    │ Database Manager │                   │
│                    └────────┬─────────┘                   │
│                             │                             │
│               ┌─────────────┴─────────────┐              │
│               ▼                           ▼              │
│         Personal Data                Knowledge Base       │
│                                                  │         │
│        expenses · tasks · goals                 ▼         │
│        bills · budgets · etc.          ┌────────────────┐ │
│                                        │ Hybrid RAG     │ │
│                                        │ BM25 + Dense   │ │
│                                        │ + RRF          │ │
│                                        └───────┬────────┘ │
│                                                ▼          │
│                                           LLM Synthesis   │
└─────────────────────────────────────────────────────────────┘
```

---

# 🧱 Project Structure

```text
perlica/
│
├── src/
│   ├── bot.py              # Discord interface + interaction workflows
│   ├── config.py           # Environment-driven configuration
│   ├── database.py         # SQLite persistence + domain queries
│   ├── extractor.py        # LLM extraction + structured domain models
│   ├── formatters.py       # Discord embeds, reports and UI formatting
│   ├── github_sync.py      # Repository ingestion + code chunking
│   ├── goal_wizard.py      # Guided goal creation workflow
│   ├── pdf_parser.py       # PDF knowledge ingestion
│   ├── rag_engine.py       # Embeddings, retrieval and synthesis
│   ├── security.py         # Authorization + secret scanning
│   └── web_scraper.py      # Safe webpage ingestion
│
├── knowledge/              # Local knowledge archive
├── tests/                  # Automated tests
├── run_local_test.py       # Local integration / test runner
├── Dockerfile              # Container deployment
├── fly.toml                # Fly.io deployment configuration
├── requirements.txt        # Python dependencies
└── .env.example            # Environment configuration template
```

The repository is intentionally centered around small domain modules while keeping the Discord experience cohesive. fileciteturn4file0

---

# 🧰 Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Interface | **Discord.py** | Conversational UI, components and interactions |
| LLM | **Groq** | Fast structured language-model inference |
| Structured AI | **Instructor + Pydantic** | Typed extraction and validation |
| Database | **SQLite / aiosqlite** | Persistent personal state |
| Embeddings | **FastEmbed** | Local semantic embeddings |
| Retrieval | **SQLite FTS5 + Dense + RRF** | Hybrid knowledge retrieval |
| Web ingestion | **Trafilatura + httpx** | Safe article extraction |
| Documents | **pypdf** | PDF ingestion |
| Deployment | **Docker + Fly.io** | Containerized deployment |

The dependency set in the repository includes the above core components. fileciteturn3file0

---

# 🚀 Getting Started

## 1. Clone

```bash
git clone https://github.com/DanielKoh2004/perlica.git
cd perlica
```

## 2. Create a virtual environment

```bash
python -m venv .venv
```

### Windows

```powershell
.venv\Scripts\activate
```

### macOS / Linux

```bash
source .venv/bin/activate
```

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

## 4. Configure environment variables

```bash
cp .env.example .env
```

Then configure at minimum:

```env
DISCORD_TOKEN=your_discord_bot_token
ALLOWED_USER_ID=your_discord_user_id
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=your_supported_groq_model
```

Optional Copilot configuration:

```env
GITHUB_TOKEN=your_github_token
```

The supplied configuration template also exposes timezone, briefing schedules and persistence settings. fileciteturn15file0

---

# ▶️ Run

Start the bot using the repository's application entrypoint.

```bash
python -m src.bot
```

For local validation, the repository also provides:

```bash
python run_local_test.py
```

---

# 🧪 Testing

The project includes `pytest` and `pytest-asyncio` for automated testing.

```bash
pytest
```

For targeted development:

```bash
pytest -q
```

---

# 🎛️ Configuration

Important runtime settings include:

| Setting | Default |
|---|---:|
| `DATABASE_PATH` | `tracker.db` |
| `TIMEZONE` | `Asia/Kuala_Lumpur` |
| `MORNING_BRIEFING_TIME` | `08:30` |
| `DAILY_SUMMARY_TIME` | `22:00` |
| `WEEKLY_REVIEW_TIME` | `20:00` |
| `REPO_AUTO_SYNC_ENABLED` | `true` |
| `REPO_AUTO_SYNC_TIME` | `04:00` |
| `MAX_REPO_FILES` | `250` |
| `MAX_SOURCE_FILE_BYTES` | `1 MB` |

These values are defined in the application's settings model and can be overridden through environment variables. fileciteturn6file0

---

# 🎨 Discord UX

Perlica uses native Discord interaction patterns instead of forcing everything through plain text.

Examples include:

```text
┌───────────────────────────────┐
│ ✏️  Edit Expense              │
│                               │
│ Amount      [ 15.50       ]   │
│ Category    [ Food & Dining ] │
│ Note        [ Lunch       ]   │
│                               │
│        [ Save ] [ Cancel ]    │
└───────────────────────────────┘
```

Alongside text responses, the bot includes formatted views for summaries, budgets, goals, investments, calendars, live dashboards, Copilot answers and knowledge-source management. fileciteturn9file0

---

# 🔥 Design Principles

### **1. Natural First**

The user should describe what happened instead of learning a command language.

### **2. State Over Chat History**

Important information becomes structured application state instead of disappearing into an old message.

### **3. AI With Guardrails**

LLM output is constrained through typed schemas, deterministic application logic and security filters.

### **4. Evidence Over Vibes**

When the assistant answers questions about a knowledge source or codebase, the goal is to return evidence and citations — not confident guesses.

### **5. One Interface**

Personal finance, execution, planning and technical knowledge should not require four separate applications.

---

# 🗺️ Roadmap

```text
                 NOW
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
  Personal OS          Knowledge Copilot
        │                   │
        ├─ Finance         ├─ GitHub sync
        ├─ Tasks           ├─ Hybrid RAG
        ├─ Goals           ├─ Citations
        ├─ Bills           └─ Web / PDF ingest
        └─ Briefings
                  │
                  ▼
             NEXT LAYER
                  │
        ┌─────────┼─────────┐
        ▼         ▼         ▼
   Better Voice  Smarter   Cross-source
   Interaction   Planning  Intelligence
```

Potential future directions include deeper voice interaction, richer cross-source reasoning, stronger analytics, and more proactive recommendations.

---

# 📸 Product Showcase

> Add real screenshots from your Discord workspace here once you have them. Repository-local images are preferable because they remain stable even if an external image host changes.

```markdown
<p align="center">
  <img src="docs/images/perlica-dashboard.png" width="92%" alt="Perlica dashboard">
</p>

<p align="center">
  <img src="docs/images/perlica-copilot.png" width="46%" alt="Perlica Copilot">
  <img src="docs/images/perlica-briefing.png" width="46%" alt="Perlica briefing">
</p>
```

For the README itself, the banner above is generated from a lightweight remote asset, so the repository does not need to ship a binary hero image.

---

# 🧠 Example Prompts

Try talking to Perlica like a person:

```text
"I spent RM12.50 on lunch and RM8.20 on LRT today."
```

```text
"Set my entertainment budget to RM200 this month."
```

```text
"Create a high priority task to finish the FYP literature review by Friday."
```

```text
"What did I spend on transport this month?"
```

```text
"Give me my weekly executive review."
```

```text
"Search my knowledge base for the authentication flow."
```

```text
"Explain how the GitHub sync pipeline chunks Python files."
```

```text
"What should I focus on today?"
```

---

# 🌱 Why Discord?

Because your productivity system should live where you already communicate.

Discord gives Perlica a useful combination of:

```text
Conversation
     +
Rich interactive components
     +
DMs / channels
     +
Notifications
     +
Low-friction input
```

Instead of opening another dashboard just to record a RM15 lunch or capture a new task, Perlica meets you where the message is already being typed.

---

# 🔐 Security Notes

Never commit:

```text
.env
Discord tokens
API keys
GitHub tokens
Private keys
Credential files
```

The application already provides environment-based configuration and repository secret filtering; treat production secrets as credentials, not configuration examples. fileciteturn6file0 fileciteturn7file0

---

# 📄 License

Add your preferred license here before publishing the repository as an open-source project.

---

<div align="center">

<br>

### **Perlica**

**Remember more. Decide faster. Do the work.**

<br>

*An AI-powered personal operating system living inside Discord.*

<br><br>

<a href="https://github.com/DanielKoh2004/perlica">GitHub</a>

<br><br>

<img src="https://capsule-render.vercel.app/api?type=waving&color=0B1020&height=110&section=footer" width="100%"/>

</div>
