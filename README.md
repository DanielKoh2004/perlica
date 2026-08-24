# 🌟 Perlica — Intelligent Discord Financial & Productivity Assistant

> **Perlica** is an intelligent, zero-friction Discord personal companion powered by Groq LLMs (Llama 3.3 70B & Llama 3.1 8B fallback), Whisper Large v3 Turbo, and Llama 3.2 Vision. Designed to live entirely in your Discord DMs, Perlica tracks everyday expenses, manages multi-phase tasks, monitors monthly budgets, accumulates dedicated savings goals, and provides proactive financial runway advice without friction.

---

## ⚡ Core Highlights & Capabilities

### 🎙️ 1. Multi-Modal Ingestion
* **Natural Language Text:** Log complex Malaysian or international entries like `RM 15.50 chicken rice for lunch`, `TNG reload RM 50`, `recurring buy $100 s&p500 on 27th`, or `Create task 'Launch App' with 3 phases: 1. Design, 2. Backend, 3. Testing`.
* **Voice Notes (Whisper Audio Transcription):** Directly record and send Discord voice notes (`.ogg` Opus / mobile voice messages) while driving or walking. Perlica transcribes the audio and extracts structured data.
* **Receipt Image OCR (Groq Vision):** Snap a photo or screenshot of a physical receipt or invoice. Groq's Vision models extract merchant names, line-item totals, and auto-categorize the purchase.

---

### 📋 2. Interactive 3-Button Ingestion Previews & Popup Modals
* **Zero Accidental Writes:** Every detected entry triggers an interactive 3-button confirmation preview:
  ```text
  [✅ Confirm]  [✏️ Edit]  [❌ Reject]
  ```
* **Native Discord Modals with Select Dropdowns:** Clicking `[✏️ Edit]` opens a popup form with pre-filled inputs and native `discord.ui.Select` dropdown menus for:
  * 💸 **Expense Categories** (*Food & Dining, Transport, Groceries, Utilities & Bills, Entertainment, Shopping, Health & Personal, Investments & Savings, Other*)
  * 📝 **Task Priorities** (*HIGH Priority 🔴, MEDIUM Priority 🟡, LOW Priority 🟢*)
  * 🔔 **Recurring Bill Day & Category**

---

### 🏆 3. Dedicated Savings Goals Engine
* **Isolated Asset Accumulation:** Stored in a separate `goals` SQLite table.
* **Non-Deductible Guarantee:** Normal everyday spending (*Food*, *Transport*, *Shopping*) **never deducts** from your savings goals!
* **Exact Integer Primary Key Matching:** Active goals are injected into system prompts (`[Goal ID: 1] Japan Trip (Target: RM 6,000.00 | Saved: RM 500.00)`), eliminating fuzzy substring errors.
* **Progress Gauges:** Visual progress bars with percentage completion and remaining balance.

---

### ⏱️ 4. 10-Second Quick-Undo Ephemeral Toast (`[↩️ Quick Undo (10s)]`)
* Immediately after confirming an action, Perlica attaches an ephemeral **`[↩️ Quick Undo (10s)]`** button.
* **Deterministic Primary Key Rollback:** Stores the exact created primary keys (`created_expense_ids`, `created_task_ids`, `goal_deposit_delta`).
* Tapping `[Quick Undo]` executes exact rollback queries (`WHERE id IN (...)` and decrements goal balances by the deposited amount)—never guessing via `ORDER BY id DESC LIMIT 1`.
* Automatically disables after 10 seconds.

---

### 📅 5. 7-Day Interactive Calendar Day Strip (`CalendarStripView`)
* **Zero HTTP 400 Crash Guarantee:** Split cleanly across Row 0 (5 weekday buttons: `[Mon]`, `[Tue]`, `[Wed]`, `[Thu]`, `[Fri]`) and Row 1 (2 weekend buttons: `[Sat]`, `[Sun]`).
* Tapping any day button inspects all expenses and tasks due on that date.
* **Command:** `/calendar` or `calendar`.

---

### 📊 6. Monospaced ASCII Heatmaps & Sparklines
* **Cross-Platform Visual Alignment:** Sparklines (`[ ▂▃▅█▂ ]`) and category proportion heatmaps (`[████░░░░░░] 45.0% Food & Dining`) are enclosed in markdown backticks to guarantee identical rendering across iOS, Android, and Desktop Discord apps.

---

### 💡 7. Safe-to-Spend Daily Runway Gauge
* Calculates your exact daily spending allowance:
  $$\text{Safe Daily Allowance} = \frac{\text{Remaining Monthly Budget}}{\text{Days Remaining in Month}}$$
* **Month-End Safeguard:** Days remaining includes today: `(total_days - current_day) + 1`, preventing `ZeroDivisionError` on the last day of the month.
* **Overspend Guard:** If overspent, allowance safely resets to `RM 0.00 / day` with an explicit alert showing the overspent amount.

---

### 🎖️ 8. Gamified Productivity Ranks & Streaks
* Badges and ranks awarded based on active logging streaks and weekly tasks completed:
  * 🌱 **Level 0: Apprentice**
  * 🥉 **Level 1: Active Tracker** *(1–3 Day Streak)*
  * 🥈 **Level 2: Budget Strategist** *(4–7 Day Streak)*
  * 🥇 **Level 3: Productivity Commander** *(8–14 Day Streak)*
  * 💎 **Level 4: Financial Master** *(15–29 Day Streak)*
  * 👑 **Level 5: Perlica Legend** *(30+ Day Streak)*

---

### 📄 9. Standalone HTML Executive Reports & CSV Exports
* **HTML Report Card (`/report`):** Generates a standalone, responsive, dark-mode `.html` report with CSS summary cards, category tables, budget meters, and goals.
* **CSV Data Export (`/export`):** Generates RFC-4180 compliant `.csv` spreadsheets of monthly expenses.

---

### ⏰ 10. Automated Proactive Schedules
* **☀️ 08:30 Morning Briefing:** Daily Safe Daily Allowance runway, high-priority tasks, bills due today, and 3-day upcoming recurring bill warnings.
* **🌙 22:00 Daily Summary:** Daily total spending, category heatmap, 7-day spending pace vs average, open tasks carrying forward, and streak footer.
* **🏆 Sunday 20:00 Weekly Executive Review:** Total weekly expenditure by category, task execution completion ratio, monthly budget health, and AI strategic kickoff.

---

## 🛠️ Project Structure

```text
perlica/
├── src/
│   ├── config.py         # Pydantic v2 application settings & timezone configuration
│   ├── database.py       # Async aiosqlite database layer (CRUD, atomic rollbacks, goals, metrics)
│   ├── extractor.py      # Groq LLM extraction engine, Whisper voice transcription & Vision OCR
│   ├── formatters.py     # Discord embed builders, ASCII heatmaps, sparklines & HTML report generator
│   └── bot.py            # Discord.py bot instance, views, modals, slash commands & scheduled loops
├── tests/
│   ├── test_database.py       # Database unit tests (expenses, tasks, budgets, recurring bills)
│   ├── test_extractor.py      # LLM extraction prompt & schema validation tests
│   ├── test_formatters.py     # Embed formatting and progress bar tests
│   ├── test_edge_cases.py     # Edge case suite (goals, rollbacks, 25-menu cap, zero-division, heatmaps)
│   └── test_end_to_end_mock.py# End-to-end simulated user interaction tests
├── pytest.ini            # Pytest configuration with asyncio mode
├── requirements.txt      # Production dependencies
└── README.md             # Project documentation
```

---

## 🚀 Getting Started

### 1. Prerequisites
* Python 3.11+
* A [Discord Bot Token](https://discord.com/developers/applications) with Message Content Intent enabled.
* A [Groq API Key](https://console.groq.com/).

### 2. Installation & Setup
Clone the repository and install dependencies:

```bash
git clone https://github.com/DanielKoh2004/perlica.git
cd perlica
pip install -r requirements.txt
```

### 3. Environment Variables
Create a `.env` file in the root directory:

```env
DISCORD_TOKEN=your_discord_bot_token_here
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile
ALLOWED_USER_ID=your_discord_user_id_here
TIMEZONE=Asia/Kuala_Lumpur
DATABASE_PATH=perlica.db
DAILY_SUMMARY_TIME=22:00
MORNING_BRIEFING_TIME=08:30
WEEKLY_REVIEW_TIME=20:00
```

---

## 🧪 Running the Test Suite

Perlica comes with an automated test suite covering all edge cases, database transactions, and model fallbacks:

```bash
python -m pytest -v tests/
```

Expected output:
```text
============================= 34 passed in 1.95s ==============================
```

---

## 🌐 Running & Deploying

### Local Run
```bash
python -m src.bot
```

### Deploying to Railway
1. Fork or push this repository to GitHub.
2. Link the repository in [Railway.app](https://railway.app/).
3. Set the environment variables in Railway's Settings tab (`DISCORD_TOKEN`, `GROQ_API_KEY`, `ALLOWED_USER_ID`, `TIMEZONE`, etc.).
4. Railway automatically detects `requirements.txt` and starts `python -m src.bot`.

---

## 💬 Command Reference & Cheat Sheet

| Feature | Slash Command | Zero-Wait DM Text Alias | Description |
| :--- | :--- | :--- | :--- |
| **Command Center** | `/dashboard` | `dashboard` | Pinned live dashboard with 1-click in-place refresh |
| **Savings Goals** | `/goals` | `goals` | View active savings goals and progress meters |
| **Open Tasks** | `/tasks` | `tasks` | View active open tasks with 1-tap select dropdown |
| **Budget Health** | `/budgets` | `budgets` | View monthly category budget progress bars |
| **Calendar Strip** | `/calendar` | `calendar` | 7-day interactive day inspector |
| **HTML Report** | `/report` | `report` | Generate & download standalone HTML executive report |
| **CSV Export** | `/export` | `export` | Download monthly expense spreadsheet (.csv) |
| **Quick Presets** | `/presets` | `presets` | 1-tap buttons for common everyday expenses |
| **Search & Filter** | `/search <text>` | `find <text>` | Search historical expenses and tasks |
| **Task Snooze** | — | `snooze <id>` | Postpone a task +1 day or to weekend |
| **Manual Force Sync** | — | `!sync` | Manually sync slash commands to Discord |

---

## 📄 License
This project is open-source and available under the [MIT License](LICENSE).
