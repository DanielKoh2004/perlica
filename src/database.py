import os
import io
import re
import csv
import json
from datetime import date, datetime, timedelta
import aiosqlite
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Tuple, Any, AsyncGenerator
from src.config import settings


def normalize_canonical_asset(raw_name: Optional[str]) -> Tuple[str, str]:
    """
    Deterministically normalize raw asset text or tickers to a canonical asset name and asset class.
    Guarantees SQL GROUP BY aggregation integrity across diverse user inputs.
    Returns: (Canonical Name, Asset Class)
    """
    if not raw_name or not raw_name.strip():
        return ("General Investment", "Unassigned")

    cleaned = raw_name.strip().lower()
    cleaned_compact = cleaned.replace(" ", "").replace("&", "").replace("-", "").replace(".", "").replace("/", "")
    tokens = set(re.findall(r"[a-z0-9]+", cleaned))

    # 1. Malaysian Wealth Platforms & Funds (Evaluated first to avoid substring collisions)
    if "asb" in cleaned_compact or "amanahsaham" in cleaned_compact:
        return ("ASB (Amanah Saham)", "Fixed Yield")

    if "epf" in cleaned_compact or "kwsp" in cleaned_compact:
        return ("EPF / KWSP", "Retirement")

    if "tabunghaji" in cleaned_compact or cleaned_compact == "th":
        return ("Tabung Haji", "Fixed Yield")

    if "versa" in cleaned_compact:
        return ("Versa Cash", "Money Market")

    if "stashaway" in cleaned_compact:
        return ("StashAway", "Robo-Advisor")

    if "wahed" in cleaned_compact:
        return ("Wahed Invest", "Robo-Advisor")

    if "kdi" in cleaned_compact or "kenanga" in cleaned_compact:
        return ("KDI (Kenanga Digital)", "Robo-Advisor")

    # 2. US & Global Equities
    sp500_exact = {"voo", "spy", "ivv", "sp500", "snp500", "snp", "spx", "standardandpoors", "sandp500", "sandp"}
    if cleaned_compact in sp500_exact or tokens.intersection(sp500_exact) or "s&p" in cleaned or "snp" in cleaned or "sp 500" in cleaned or "sp500" in cleaned_compact:
        return ("S&P 500", "US Equities")

    nasdaq_exact = {"qqq", "nasdaq", "nasdaq100", "ndx", "qqqm"}
    if cleaned_compact in nasdaq_exact or tokens.intersection(nasdaq_exact) or "nasdaq" in cleaned:
        return ("Nasdaq 100", "US Equities")

    all_world_exact = {"vwra", "vt", "allworld", "msciworld", "iwda", "urth"}
    if cleaned_compact in all_world_exact or tokens.intersection(all_world_exact) or "world etf" in cleaned:
        return ("All-World ETF", "Global Equities")

    # 3. Crypto & Digital Assets
    btc_exact = {"btc", "bitcoin", "sats", "satoshi", "satoshis"}
    if cleaned_compact in btc_exact or tokens.intersection(btc_exact) or "bitcoin" in cleaned or "satoshi" in cleaned:
        return ("Bitcoin (BTC)", "Crypto")

    eth_exact = {"eth", "ethereum", "ether"}
    if cleaned_compact in eth_exact or tokens.intersection(eth_exact) or "ethereum" in cleaned:
        return ("Ethereum (ETH)", "Crypto")

    sol_exact = {"sol", "solana"}
    if cleaned_compact in sol_exact or tokens.intersection(sol_exact) or "solana" in cleaned:
        return ("Solana (SOL)", "Crypto")

    if "luno" in cleaned_compact or "crypto" in cleaned or "usdt" in cleaned or "usdc" in cleaned:
        return ("Crypto Portfolio", "Crypto")

    # 4. Commodities
    if "gold" in cleaned or "emas" in cleaned or "xau" in cleaned_compact:
        return ("Gold", "Commodities")

    if "silver" in cleaned or "perak" in cleaned or "xag" in cleaned_compact:
        return ("Silver", "Commodities")

    # 5. Malaysian Equities / Stocks
    bursa_keywords = {"bursa", "maybank", "tenaga", "public bank", "cimb", "pbbank", "topglove", "hartalega", "ihh", "yinson", "genting"}
    if any(k in cleaned for k in bursa_keywords):
        return (raw_name.strip().title(), "Malaysian Equities")

    # Fallback to clean title
    return (raw_name.strip().title(), "Equities & Assets")


class DatabaseManager:
    def __init__(self, db_path: str = "tracker.db"):
        self.db_path = db_path

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """Async context manager yielding a connection configured with Row factory and mandatory WAL pragmas."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys = ON;")
            await conn.execute("PRAGMA recursive_triggers = ON;")
            await conn.execute("PRAGMA journal_mode = WAL;")
            await conn.execute("PRAGMA busy_timeout = 5000;")
            await conn.execute("PRAGMA synchronous = NORMAL;")
            yield conn

    async def init_db(self) -> None:
        """Initialize SQLite database tables, indexes, and apply migrations."""
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        async with self.get_connection() as conn:
            # 1. Expenses Table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    amount REAL NOT NULL,
                    category TEXT NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            # 2. Tasks Table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_id INTEGER,
                    phase_order INTEGER DEFAULT 1,
                    phase_name TEXT,
                    description TEXT NOT NULL,
                    status TEXT CHECK(status IN ('OPEN', 'DONE')) DEFAULT 'OPEN',
                    priority TEXT CHECK(priority IN ('LOW', 'MEDIUM', 'HIGH')) DEFAULT 'MEDIUM',
                    due_date TEXT,
                    due_time TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                """
            )
            # 3. Monthly Budgets Table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS budgets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT UNIQUE NOT NULL,
                    monthly_limit REAL NOT NULL
                );
                """
            )
            # 4. Recurring Bills Table (Human-in-the-loop reminders only)
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recurring_bills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    amount REAL NOT NULL,
                    category TEXT NOT NULL,
                    day_of_month INTEGER NOT NULL,
                    is_active INTEGER DEFAULT 1
                );
                """
            )
            # 5. Dedicated Savings Goals Table (Asset accumulation, NOT deductible by expenses)
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    target_amount REAL NOT NULL,
                    current_amount REAL DEFAULT 0.0,
                    target_date TEXT,
                    is_completed INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                """
            )
            # 6. User Milestones Ledger (Atomic milestone tracking & anti-spam deduplication)
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_milestones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    milestone_key TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    awarded_at TEXT NOT NULL
                );
                """
            )

            # Auto-migration for tasks table columns
            async with conn.execute("PRAGMA table_info(tasks);") as cursor:
                columns = [row["name"] for row in await cursor.fetchall()]
                if "parent_id" not in columns:
                    await conn.execute("ALTER TABLE tasks ADD COLUMN parent_id INTEGER;")
                if "phase_name" not in columns:
                    await conn.execute("ALTER TABLE tasks ADD COLUMN phase_name TEXT;")
                if "phase_order" not in columns:
                    await conn.execute("ALTER TABLE tasks ADD COLUMN phase_order INTEGER DEFAULT 1;")

            # Auto-migration for expenses table columns (Canonical Wealth & Investments)
            async with conn.execute("PRAGMA table_info(expenses);") as cursor:
                exp_cols = [row["name"] for row in await cursor.fetchall()]
                if "asset_name" not in exp_cols:
                    await conn.execute("ALTER TABLE expenses ADD COLUMN asset_name TEXT;")
                if "asset_class" not in exp_cols:
                    await conn.execute("ALTER TABLE expenses ADD COLUMN asset_class TEXT;")
                if "recurring_bill_id" not in exp_cols:
                    await conn.execute("ALTER TABLE expenses ADD COLUMN recurring_bill_id INTEGER;")

            await conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_expenses_created ON expenses(created_at);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_expenses_asset ON expenses(asset_name);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_milestone_key ON user_milestones(milestone_key);")

            # --- KNOWLEDGE & CODEBASE COPILOT SCHEMA ---

            # 7. Knowledge Sources Table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL UNIQUE,
                    eligible_count INTEGER DEFAULT 0,
                    indexed_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'COMPLETE',
                    last_sync_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )

            # 8. Source Files Manifest Table
            # Status enum: 'INDEXED', 'EXCLUDED_SIZE', 'EXCLUDED_BINARY', 'EXCLUDED_SECRET', 'EXCLUDED_CAP', 'FAILED_FETCH', 'FAILED_PARSE', 'FAILED_EMBED'
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS source_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    path TEXT NOT NULL,
                    blob_sha TEXT,
                    last_seen_sync_id INTEGER,
                    status TEXT DEFAULT 'INDEXED',
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_id, path)
                );
                """
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_source_files_source ON source_files(source_id);")

            # 9. Knowledge Chunks Table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    source_file_id INTEGER REFERENCES source_files(id) ON DELETE CASCADE,
                    section_title TEXT,
                    permalink_url TEXT,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source ON knowledge_chunks(source_id);")

            # 10. FTS5 Virtual Table on Knowledge Chunks
            await conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
                    content,
                    section_title,
                    content='knowledge_chunks',
                    content_rowid='id',
                    tokenize="unicode61 remove_diacritics 2 tokenchars '_'"
                );
                """
            )

            # 11. FTS5 Synchronization Triggers
            await conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS knowledge_chunks_ai AFTER INSERT ON knowledge_chunks BEGIN
                    INSERT INTO knowledge_chunks_fts(rowid, content, section_title)
                    VALUES (new.id, new.content, new.section_title);
                END;
                """
            )
            await conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS knowledge_chunks_ad AFTER DELETE ON knowledge_chunks BEGIN
                    INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rowid, content, section_title)
                    VALUES ('delete', old.id, old.content, old.section_title);
                END;
                """
            )
            await conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS knowledge_chunks_au AFTER UPDATE ON knowledge_chunks BEGIN
                    INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts, rowid, content, section_title)
                    VALUES ('delete', old.id, old.content, old.section_title);
                    INSERT INTO knowledge_chunks_fts(rowid, content, section_title)
                    VALUES (new.id, new.content, new.section_title);
                END;
                """
            )

            # 12. Chunk Embeddings Table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunk_embeddings (
                    chunk_id INTEGER PRIMARY KEY REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
                    model_id TEXT NOT NULL,
                    embedding_blob BLOB NOT NULL
                );
                """
            )

            # 13. Ingestion Job Queue Table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ingestion_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type TEXT NOT NULL,
                    target_ref TEXT NOT NULL,
                    status TEXT DEFAULT 'PENDING',
                    progress_text TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

            # 14. Answer Snapshots Table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS answers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    response TEXT NOT NULL,
                    user_id TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )

            # 15. Historical Answer Evidence Table (Decoupled from cascades)
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS answer_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    answer_id INTEGER NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
                    source_id INTEGER,
                    source_file_id INTEGER,
                    citation TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    metadata_json TEXT
                );
                """
            )

            # 16. Retrieval Telemetry Logs Table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS retrieval_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    top_cosine_json TEXT,
                    bm25_hit_count INTEGER,
                    rrf_results_json TEXT,
                    selected_sources_json TEXT,
                    context_token_count INTEGER,
                    user_feedback TEXT,
                    answer_id INTEGER,
                    created_at TEXT NOT NULL
                );
                """
            )

            # Explicit Bootstrap Action: Rebuild FTS5 on initial setup/migration
            try:
                await conn.execute("INSERT INTO knowledge_chunks_fts(knowledge_chunks_fts) VALUES ('rebuild');")
            except Exception:
                pass

            await conn.commit()

    # --- EXPENSES & WEALTH LOGGING ---

    async def insert_expense(
        self,
        amount: float,
        category: str,
        note: Optional[str],
        created_at: str,
        asset_name: Optional[str] = None,
        asset_class: Optional[str] = None,
        recurring_bill_id: Optional[int] = None,
    ) -> int:
        """
        Insert an expense or wealth investment record and return its primary key ID.
        Automatically normalizes canonical asset names for Investments & Savings.
        """
        if category == "Investments & Savings" or asset_name:
            c_name, c_class = normalize_canonical_asset(asset_name or note)
            asset_name = c_name
            asset_class = asset_class or c_class

        async with self.get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO expenses (amount, category, note, created_at, asset_name, asset_class, recurring_bill_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (round(amount, 2), category, note, created_at, asset_name, asset_class, recurring_bill_id),
            )
            await conn.commit()
            return cursor.lastrowid

    async def get_expense_by_id(self, expense_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a specific expense by ID."""
        async with self.get_connection() as conn:
            async with conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_last_expense(self) -> Optional[Dict[str, Any]]:
        """Fetch the most recently created expense."""
        async with self.get_connection() as conn:
            async with conn.execute("SELECT * FROM expenses ORDER BY id DESC LIMIT 1") as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def delete_expense(self, expense_id: int) -> Optional[Dict[str, Any]]:
        """Delete an expense by ID and return its data."""
        exp = await self.get_expense_by_id(expense_id)
        if not exp:
            return None
        async with self.get_connection() as conn:
            await conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
            await conn.commit()
        return exp

    async def update_expense(
        self,
        expense_id: int,
        amount: Optional[float] = None,
        category: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update an existing expense."""
        exp = await self.get_expense_by_id(expense_id)
        if not exp:
            return None
        new_amount = round(amount, 2) if amount is not None else exp["amount"]
        new_cat = category or exp["category"]
        new_note = note if note is not None else exp["note"]

        async with self.get_connection() as conn:
            await conn.execute(
                "UPDATE expenses SET amount = ?, category = ?, note = ? WHERE id = ?",
                (new_amount, new_cat, new_note, expense_id),
            )
            await conn.commit()
        return await self.get_expense_by_id(expense_id)

    # --- TASKS ---

    async def insert_task(
        self,
        description: str,
        priority: str = "MEDIUM",
        due_date: Optional[str] = None,
        due_time: Optional[str] = None,
        created_at: str = "",
        parent_id: Optional[int] = None,
        phase_name: Optional[str] = None,
        phase_order: int = 1,
    ) -> int:
        """Insert a task record and return its primary key ID."""
        priority_normalized = priority.upper() if priority.upper() in ("LOW", "MEDIUM", "HIGH") else "MEDIUM"
        async with self.get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO tasks (parent_id, phase_order, phase_name, description, priority, due_date, due_time, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (parent_id, phase_order, phase_name, description, priority_normalized, due_date, due_time, created_at),
            )
            await conn.commit()
            return cursor.lastrowid

    async def insert_task_with_phases(
        self,
        description: str,
        priority: str,
        phases: List[str],
        due_date: Optional[str],
        due_time: Optional[str],
        created_at: str,
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """Insert a parent task and its structured sub-phases."""
        parent_id = await self.insert_task(
            description=description,
            priority=priority,
            due_date=due_date,
            due_time=due_time,
            created_at=created_at,
        )

        subtasks = []
        for idx, phase_desc in enumerate(phases, start=1):
            phase_id = await self.insert_task(
                description=phase_desc,
                priority=priority,
                due_date=due_date,
                due_time=due_time,
                created_at=created_at,
                parent_id=parent_id,
                phase_name=f"Phase {idx}",
                phase_order=idx,
            )
            subtasks.append(
                {
                    "id": phase_id,
                    "parent_id": parent_id,
                    "phase_name": f"Phase {idx}",
                    "phase_order": idx,
                    "description": phase_desc,
                    "status": "OPEN",
                    "priority": priority,
                }
            )

        return parent_id, subtasks

    async def get_task_by_id(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a specific task by ID."""
        async with self.get_connection() as conn:
            async with conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_last_task(self) -> Optional[Dict[str, Any]]:
        """Fetch the most recently created task."""
        async with self.get_connection() as conn:
            async with conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 1") as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def delete_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Delete a task (and all its subphases if parent) by ID."""
        task = await self.get_task_by_id(task_id)
        if not task:
            return None
        async with self.get_connection() as conn:
            await conn.execute("DELETE FROM tasks WHERE id = ? OR parent_id = ?", (task_id, task_id))
            await conn.commit()
        return task

    async def update_task(
        self,
        task_id: int,
        description: Optional[str] = None,
        priority: Optional[str] = None,
        due_date: Optional[str] = None,
        due_time: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update an existing task."""
        t = await self.get_task_by_id(task_id)
        if not t:
            return None
        new_desc = description or t["description"]
        new_prio = priority.upper() if priority and priority.upper() in ("LOW", "MEDIUM", "HIGH") else t["priority"]
        new_due_date = due_date if due_date is not None else t["due_date"]
        new_due_time = due_time if due_time is not None else t["due_time"]
        new_status = status if status in ("OPEN", "DONE") else t["status"]

        async with self.get_connection() as conn:
            await conn.execute(
                """
                UPDATE tasks 
                SET description = ?, priority = ?, due_date = ?, due_time = ?, status = ?
                WHERE id = ?
                """,
                (new_desc, new_prio, new_due_date, new_due_time, new_status, task_id),
            )
            await conn.commit()
        return await self.get_task_by_id(task_id)

    async def complete_task_by_id(
        self, task_id: int, completed_at: str
    ) -> Optional[Dict[str, Any]]:
        """
        Deterministically mark a task as DONE by its exact integer ID.
        Completing a parent completes all child phases.
        """
        async with self.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM tasks WHERE id = ? AND status = 'OPEN'", (task_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                task_dict = dict(row)

            await conn.execute(
                "UPDATE tasks SET status = 'DONE', completed_at = ? WHERE id = ?",
                (completed_at, task_id),
            )

            await conn.execute(
                "UPDATE tasks SET status = 'DONE', completed_at = ? WHERE parent_id = ? AND status = 'OPEN'",
                (completed_at, task_id),
            )

            if task_dict.get("parent_id"):
                parent_id = task_dict["parent_id"]
                async with conn.execute(
                    "SELECT COUNT(*) as remaining FROM tasks WHERE parent_id = ? AND status = 'OPEN'",
                    (parent_id,),
                ) as cur:
                    remaining_row = await cur.fetchone()
                    if remaining_row and remaining_row["remaining"] == 0:
                        await conn.execute(
                            "UPDATE tasks SET status = 'DONE', completed_at = ? WHERE id = ? AND status = 'OPEN'",
                            (completed_at, parent_id),
                        )

            await conn.commit()
            task_dict["status"] = "DONE"
            task_dict["completed_at"] = completed_at
            return task_dict

    async def complete_tasks_by_ids(
        self, task_ids: List[int], completed_at: str
    ) -> List[Dict[str, Any]]:
        """Deterministically complete multiple tasks by their exact integer IDs."""
        completed = []
        for tid in task_ids:
            res = await self.complete_task_by_id(tid, completed_at)
            if res:
                completed.append(res)
        return completed

    async def get_open_tasks(self) -> List[Dict[str, Any]]:
        """Return active OPEN tasks ordered by priority and hierarchy."""
        async with self.get_connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'OPEN'
                ORDER BY 
                    COALESCE(parent_id, id) ASC,
                    CASE WHEN parent_id IS NULL THEN 0 ELSE 1 END,
                    phase_order ASC,
                    CASE priority
                        WHEN 'HIGH' THEN 1
                        WHEN 'MEDIUM' THEN 2
                        WHEN 'LOW' THEN 3
                        ELSE 4
                    END,
                    id ASC
                """
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_completed_tasks(
        self, target_date_str: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Fetch completed tasks, optionally filtered by completion date."""
        async with self.get_connection() as conn:
            if target_date_str:
                query = """
                    SELECT * FROM tasks
                    WHERE status = 'DONE' AND substr(completed_at, 1, 10) = ?
                    ORDER BY completed_at DESC
                """
                params = (target_date_str,)
            else:
                query = "SELECT * FROM tasks WHERE status = 'DONE' ORDER BY completed_at DESC"
                params = ()

            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    # --- BUDGETS & OVERSPEND ---

    async def set_budget(self, category: str, monthly_limit: float) -> Dict[str, Any]:
        """Insert or update a monthly budget limit for a category."""
        limit_rounded = round(monthly_limit, 2)
        async with self.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO budgets (category, monthly_limit)
                VALUES (?, ?)
                ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit
                """,
                (category, limit_rounded),
            )
            await conn.commit()
        return {"category": category, "monthly_limit": limit_rounded}

    async def get_budgets(self) -> Dict[str, float]:
        """Fetch all configured monthly budget limits."""
        async with self.get_connection() as conn:
            async with conn.execute("SELECT category, monthly_limit FROM budgets") as cur:
                rows = await cur.fetchall()
                return {row["category"]: row["monthly_limit"] for row in rows}

    async def get_budget_status(self, year_month_str: str) -> List[Dict[str, Any]]:
        """
        Calculate budget utilization for the month (YYYY-MM).
        Returns list of dicts with category, spent, limit, percentage, and remaining.
        """
        budgets = await self.get_budgets()
        if not budgets:
            return []

        # Get spending in this month
        async with self.get_connection() as conn:
            async with conn.execute(
                """
                SELECT category, SUM(amount) as total_spent
                FROM expenses
                WHERE substr(created_at, 1, 7) = ?
                GROUP BY category
                """,
                (year_month_str,),
            ) as cur:
                rows = await cur.fetchall()
                spent_map = {row["category"]: round(row["total_spent"], 2) for row in rows}

        results = []
        for cat, limit in budgets.items():
            spent = spent_map.get(cat, 0.0)
            pct = round((spent / limit) * 100, 1) if limit > 0 else 0.0
            remaining = round(limit - spent, 2)
            results.append(
                {
                    "category": cat,
                    "spent": spent,
                    "limit": limit,
                    "percentage": pct,
                    "remaining": remaining,
                    "is_overspent": spent > limit,
                    "is_warning": 80.0 <= pct <= 100.0,
                }
            )
        return sorted(results, key=lambda x: x["percentage"], reverse=True)

    # --- RECURRING BILLS (HUMAN IN THE LOOP) ---

    async def add_recurring_bill(
        self, name: str, amount: float, category: str, day_of_month: int
    ) -> int:
        """Add a recurring bill reminder definition (No auto-deductions)."""
        async with self.get_connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO recurring_bills (name, amount, category, day_of_month, is_active)
                VALUES (?, ?, ?, ?, 1)
                """,
                (name, round(amount, 2), category, day_of_month),
            )
            await conn.commit()
            return cur.lastrowid

    async def get_due_recurring_bills(self, current_date_or_day: Any) -> List[Dict[str, Any]]:
        """
        Fetch active recurring bills due on current_date.
        Handles month-end boundary clipping (e.g. Feb 28 catches bills set for 28th, 29th, 30th, 31st).
        """
        import calendar
        from datetime import date, datetime

        if isinstance(current_date_or_day, (date, datetime)):
            day = current_date_or_day.day
            _, last_day = calendar.monthrange(current_date_or_day.year, current_date_or_day.month)
            is_last_day = (day == last_day)
        else:
            day = int(current_date_or_day)
            is_last_day = False

        async with self.get_connection() as conn:
            if is_last_day:
                query = "SELECT * FROM recurring_bills WHERE day_of_month >= ? AND is_active = 1"
                params = (day,)
            else:
                query = "SELECT * FROM recurring_bills WHERE day_of_month = ? AND is_active = 1"
                params = (day,)

            async with conn.execute(query, params) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_recurring_bill_by_id(self, bill_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a specific recurring bill by ID."""
        async with self.get_connection() as conn:
            async with conn.execute("SELECT * FROM recurring_bills WHERE id = ?", (bill_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def update_recurring_bill(
        self,
        bill_id: int,
        name: Optional[str] = None,
        amount: Optional[float] = None,
        category: Optional[str] = None,
        day_of_month: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update an existing recurring bill."""
        bill = await self.get_recurring_bill_by_id(bill_id)
        if not bill:
            return None
        new_name = name or bill["name"]
        new_amount = round(amount, 2) if amount is not None else bill["amount"]
        new_cat = category or bill["category"]
        new_day = day_of_month if day_of_month is not None else bill["day_of_month"]

        async with self.get_connection() as conn:
            await conn.execute(
                """
                UPDATE recurring_bills
                SET name = ?, amount = ?, category = ?, day_of_month = ?
                WHERE id = ?
                """,
                (new_name, new_amount, new_cat, new_day, bill_id),
            )
            await conn.commit()
        return await self.get_recurring_bill_by_id(bill_id)

    async def delete_recurring_bill(self, bill_id: int) -> Optional[Dict[str, Any]]:
        """Delete a recurring bill by ID."""
        bill = await self.get_recurring_bill_by_id(bill_id)
        if not bill:
            return None
        async with self.get_connection() as conn:
            await conn.execute("DELETE FROM recurring_bills WHERE id = ?", (bill_id,))
            await conn.commit()
        return bill

    async def list_recurring_bills(self) -> List[Dict[str, Any]]:
        """List all active recurring bills."""
        async with self.get_connection() as conn:
            async with conn.execute("SELECT * FROM recurring_bills WHERE is_active = 1 ORDER BY day_of_month ASC") as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    # --- SUMMARIES & DATA EXPORT ---

    async def get_daily_summary(
        self, target_date_str: str
    ) -> Tuple[List[Dict[str, Any]], float, List[Dict[str, Any]]]:
        """Fetch expenses on target_date_str (YYYY-MM-DD), total spending, and all active open tasks."""
        async with self.get_connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM expenses
                WHERE substr(created_at, 1, 10) = ?
                ORDER BY id ASC
                """,
                (target_date_str,),
            ) as cursor:
                expense_rows = await cursor.fetchall()
                expenses = [dict(r) for r in expense_rows]

            total_spent = sum(e["amount"] for e in expenses)

        open_tasks = await self.get_open_tasks()
        return expenses, round(total_spent, 2), open_tasks

    async def get_expenses_summary(
        self,
        start_date_str: Optional[str] = None,
        end_date_str: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], float, Dict[str, float]]:
        """Fetch itemized expenses within date range, total sum, and per-category breakdown."""
        async with self.get_connection() as conn:
            if start_date_str and end_date_str:
                query = """
                    SELECT * FROM expenses
                    WHERE substr(created_at, 1, 10) BETWEEN ? AND ?
                    ORDER BY created_at ASC
                """
                params = (start_date_str, end_date_str)
            elif start_date_str:
                query = """
                    SELECT * FROM expenses
                    WHERE substr(created_at, 1, 10) >= ?
                    ORDER BY created_at ASC
                """
                params = (start_date_str,)
            else:
                query = "SELECT * FROM expenses ORDER BY created_at ASC"
                params = ()

            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                expenses = [dict(r) for r in rows]

        total_spent = round(sum(e["amount"] for e in expenses), 2)
        category_breakdown: Dict[str, float] = {}
        for exp in expenses:
            cat = exp["category"]
            category_breakdown[cat] = round(
                category_breakdown.get(cat, 0.0) + exp["amount"], 2
            )

        return expenses, total_spent, category_breakdown

    async def get_full_snapshot(
        self,
        start_date_str: Optional[str] = None,
        end_date_str: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch a complete financial and task snapshot for a given date or range."""
        expenses, total_spent, breakdown = await self.get_expenses_summary(
            start_date_str, end_date_str
        )
        open_tasks = await self.get_open_tasks()
        completed_tasks = await self.get_completed_tasks(start_date_str if start_date_str == end_date_str else None)

        return {
            "expenses": expenses,
            "total_spent": total_spent,
            "category_breakdown": breakdown,
            "open_tasks": open_tasks,
            "completed_tasks": completed_tasks,
        }

    async def generate_csv_data(
        self,
        start_date_str: Optional[str] = None,
        end_date_str: Optional[str] = None,
    ) -> str:
        """Generate a clean RFC-4180 CSV string of expenses."""
        expenses, _, _ = await self.get_expenses_summary(start_date_str, end_date_str)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Expense ID", "Date", "Category", "Amount (MYR)", "Note"])
        for e in expenses:
            writer.writerow(
                [
                    e["id"],
                    e["created_at"],
                    e["category"],
                    f"{e['amount']:.2f}",
                    e.get("note") or "",
                ]
            )
        return output.getvalue()

    # --- ADVANCED UI/UX AGGREGATES & METRICS ---

    async def get_productivity_streak(self, today_str: str) -> Dict[str, Any]:
        """
        Calculate consecutive active logging days (days with logged expenses or completed tasks).
        Also counts tasks completed in the current calendar week.
        """
        from datetime import datetime, timedelta

        # Get all distinct active dates
        async with self.get_connection() as conn:
            async with conn.execute(
                """
                SELECT DISTINCT substr(created_at, 1, 10) as log_date FROM expenses
                UNION
                SELECT DISTINCT substr(completed_at, 1, 10) as log_date FROM tasks WHERE status = 'DONE' AND completed_at IS NOT NULL
                ORDER BY log_date DESC
                """
            ) as cur:
                rows = await cur.fetchall()
                active_dates = {r["log_date"] for r in rows}

        today_dt = datetime.strptime(today_str, "%Y-%m-%d").date()
        
        # Calculate consecutive streak leading up to today or yesterday
        streak = 0
        check_dt = today_dt
        if check_dt.strftime("%Y-%m-%d") not in active_dates:
            check_dt = today_dt - timedelta(days=1)

        while check_dt.strftime("%Y-%m-%d") in active_dates:
            streak += 1
            check_dt -= timedelta(days=1)

        # Count tasks completed this week (Monday to Sunday)
        start_of_week = (today_dt - timedelta(days=today_dt.weekday())).strftime("%Y-%m-%d")
        async with self.get_connection() as conn:
            async with conn.execute(
                """
                SELECT COUNT(*) as completed_count FROM tasks
                WHERE status = 'DONE' AND substr(completed_at, 1, 10) >= ?
                """,
                (start_of_week,),
            ) as cur:
                row = await cur.fetchone()
                completed_this_week = row["completed_count"] if row else 0

        return {
            "streak_days": streak,
            "completed_this_week": completed_this_week,
        }

    async def get_spending_pace(self, today_str: str) -> Dict[str, Any]:
        """
        Calculate 7-day spending pace and daily spending history for monospaced sparkline rendering.
        """
        from datetime import datetime, timedelta

        today_dt = datetime.strptime(today_str, "%Y-%m-%d").date()
        seven_days_ago = (today_dt - timedelta(days=6)).strftime("%Y-%m-%d")

        async with self.get_connection() as conn:
            async with conn.execute(
                """
                SELECT substr(created_at, 1, 10) as day_date, SUM(amount) as daily_total
                FROM expenses
                WHERE substr(created_at, 1, 10) BETWEEN ? AND ?
                GROUP BY day_date
                """,
                (seven_days_ago, today_str),
            ) as cur:
                rows = await cur.fetchall()
                day_map = {r["day_date"]: round(r["daily_total"], 2) for r in rows}

        daily_series = []
        for i in range(6, -1, -1):
            d_str = (today_dt - timedelta(days=i)).strftime("%Y-%m-%d")
            daily_series.append(day_map.get(d_str, 0.0))

        today_spend = daily_series[-1]
        seven_day_avg = round(sum(daily_series) / 7.0, 2)

        if seven_day_avg > 0:
            diff_pct = round(((today_spend - seven_day_avg) / seven_day_avg) * 100, 1)
        else:
            diff_pct = 0.0

        return {
            "daily_series": daily_series,
            "today_spend": today_spend,
            "seven_day_avg": seven_day_avg,
            "diff_pct": diff_pct,
        }

    async def get_upcoming_recurring_bills(self, current_date: date, days_ahead: int = 3) -> List[Dict[str, Any]]:
        """
        Fetch active recurring bills due within the next N days (1 to days_ahead),
        excluding bills due today.
        """
        import calendar
        from datetime import timedelta

        upcoming = []
        seen_ids = set()

        for offset in range(1, days_ahead + 1):
            future_dt = current_date + timedelta(days=offset)
            f_day = future_dt.day
            _, last_day = calendar.monthrange(future_dt.year, future_dt.month)
            is_last = (f_day == last_day)

            async with self.get_connection() as conn:
                if is_last:
                    query = "SELECT * FROM recurring_bills WHERE day_of_month >= ? AND is_active = 1"
                    params = (f_day,)
                else:
                    query = "SELECT * FROM recurring_bills WHERE day_of_month = ? AND is_active = 1"
                    params = (f_day,)

                async with conn.execute(query, params) as cur:
                    rows = await cur.fetchall()
                    for r in rows:
                        b = dict(r)
                        if b["id"] not in seen_ids:
                            seen_ids.add(b["id"])
                            b["due_in_days"] = offset
                            b["due_date_str"] = future_dt.strftime("%Y-%m-%d")
                            upcoming.append(b)

        return sorted(upcoming, key=lambda x: x["due_in_days"])

    async def get_weekly_review_data(self, start_date_str: str, end_date_str: str) -> Dict[str, Any]:
        """Fetch weekly expenditure breakdown, task completion ratio, and budget health."""
        expenses, total_spent, category_breakdown = await self.get_expenses_summary(start_date_str, end_date_str)
        
        async with self.get_connection() as conn:
            async with conn.execute(
                """
                SELECT COUNT(*) as completed_count FROM tasks
                WHERE status = 'DONE' AND substr(completed_at, 1, 10) BETWEEN ? AND ?
                """,
                (start_date_str, end_date_str),
            ) as cur:
                row = await cur.fetchone()
                completed_tasks_count = row["completed_count"] if row else 0

        open_tasks = await self.get_open_tasks()
        month_str = end_date_str[:7]
        budget_status = await self.get_budget_status(month_str)

        return {
            "start_date": start_date_str,
            "end_date": end_date_str,
            "total_spent": total_spent,
            "category_breakdown": category_breakdown,
            "expenses_count": len(expenses),
            "completed_tasks_count": completed_tasks_count,
            "open_tasks_count": len(open_tasks),
            "budget_status": budget_status,
        }

    async def get_safe_daily_allowance(self, now_dt: datetime) -> Dict[str, Any]:
        """
        Calculate remaining days in current month and compute Safe-to-Spend daily allowance.
        Guarantees:
        - Excludes 'Investments & Savings' from consumptive spend so capital deployments never pollute runway.
        - Days left includes today: (last_day - current_day) + 1, so days_remaining is never 0.
        - Negative remaining budget returns allowance 0.0 with explicit overspent_by amount.
        """
        import calendar

        year = now_dt.year
        month = now_dt.month
        current_day = now_dt.day
        month_str = now_dt.strftime("%Y-%m")
        _, total_days = calendar.monthrange(year, month)
        days_remaining = max((total_days - current_day) + 1, 1)

        budgets = await self.get_budgets()
        total_budget = sum(limit for cat, limit in budgets.items() if cat != "Investments & Savings")

        async with self.get_connection() as conn:
            async with conn.execute(
                """
                SELECT SUM(amount) as total_spent FROM expenses
                WHERE substr(created_at, 1, 7) = ? AND category != 'Investments & Savings'
                """,
                (month_str,),
            ) as cur:
                row = await cur.fetchone()
                total_spent = row["total_spent"] if row and row["total_spent"] else 0.0

        if total_budget <= 0:
            return {
                "has_budget": False,
                "days_remaining": days_remaining,
                "total_budget": 0.0,
                "total_spent": total_spent,
                "remaining_budget": 0.0,
                "safe_daily_allowance": 0.0,
                "is_overspent": False,
            }

        remaining_budget = round(total_budget - total_spent, 2)
        if remaining_budget <= 0:
            return {
                "has_budget": True,
                "days_remaining": days_remaining,
                "total_budget": total_budget,
                "total_spent": total_spent,
                "remaining_budget": remaining_budget,
                "overspent_by": abs(remaining_budget),
                "safe_daily_allowance": 0.0,
                "is_overspent": True,
            }

        safe_daily_allowance = round(remaining_budget / days_remaining, 2)
        return {
            "has_budget": True,
            "days_remaining": days_remaining,
            "total_budget": total_budget,
            "total_spent": total_spent,
            "remaining_budget": remaining_budget,
            "safe_daily_allowance": safe_daily_allowance,
            "is_overspent": False,
        }

    # --- WEALTH & INVESTMENT TRACKING ENGINE ---

    async def get_investments_summary(
        self,
        start_date_str: Optional[str] = None,
        end_date_str: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fetch itemized investments, total capital deployed, and canonical asset grouping.
        """
        async with self.get_connection() as conn:
            if start_date_str and end_date_str:
                query = """
                    SELECT * FROM expenses
                    WHERE category = 'Investments & Savings' AND substr(created_at, 1, 10) BETWEEN ? AND ?
                    ORDER BY created_at DESC
                """
                params = (start_date_str, end_date_str)
            elif start_date_str:
                query = """
                    SELECT * FROM expenses
                    WHERE category = 'Investments & Savings' AND substr(created_at, 1, 10) >= ?
                    ORDER BY created_at DESC
                """
                params = (start_date_str,)
            else:
                query = """
                    SELECT * FROM expenses
                    WHERE category = 'Investments & Savings'
                    ORDER BY created_at DESC
                """
                params = ()

            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                investments = [dict(r) for r in rows]

        total_invested = round(sum(inv["amount"] for inv in investments), 2)

        asset_map: Dict[str, Dict[str, Any]] = {}
        class_map: Dict[str, float] = {}

        for inv in investments:
            raw_asset = inv.get("asset_name") or inv.get("note")
            canonical_name, canonical_class = normalize_canonical_asset(raw_asset)
            
            if canonical_name not in asset_map:
                asset_map[canonical_name] = {
                    "asset_name": canonical_name,
                    "asset_class": canonical_class,
                    "total_amount": 0.0,
                    "count": 0,
                }
            asset_map[canonical_name]["total_amount"] = round(asset_map[canonical_name]["total_amount"] + inv["amount"], 2)
            asset_map[canonical_name]["count"] += 1

            class_map[canonical_class] = round(class_map.get(canonical_class, 0.0) + inv["amount"], 2)

        asset_breakdown = []
        for item in sorted(asset_map.values(), key=lambda x: x["total_amount"], reverse=True):
            pct = round((item["total_amount"] / total_invested * 100), 1) if total_invested > 0 else 0.0
            item["percentage"] = pct
            asset_breakdown.append(item)

        class_breakdown = []
        for c_name, c_amt in sorted(class_map.items(), key=lambda x: x[1], reverse=True):
            c_pct = round((c_amt / total_invested * 100), 1) if total_invested > 0 else 0.0
            class_breakdown.append({"asset_class": c_name, "total_amount": c_amt, "percentage": c_pct})

        return {
            "total_invested": total_invested,
            "asset_breakdown": asset_breakdown,
            "class_breakdown": class_breakdown,
            "investments": investments,
            "count": len(investments),
        }

    async def get_dca_progress(self, year_month_str: str) -> List[Dict[str, Any]]:
        """
        Compare active recurring investment commitments against actual logged investments in that calendar month.
        Deterministically matches via foreign key recurring_bill_id or canonical asset name.
        """
        async with self.get_connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM recurring_bills
                WHERE is_active = 1 AND category = 'Investments & Savings'
                ORDER BY day_of_month ASC
                """
            ) as cur:
                bills = [dict(r) for r in await cur.fetchall()]

            if not bills:
                return []

            async with conn.execute(
                """
                SELECT * FROM expenses
                WHERE category = 'Investments & Savings' AND substr(created_at, 1, 7) = ?
                """,
                (year_month_str,),
            ) as cur:
                month_investments = [dict(r) for r in await cur.fetchall()]

        progress_list = []
        for bill in bills:
            c_name, _ = normalize_canonical_asset(bill["name"])
            target = bill["amount"]
            due_day = bill["day_of_month"]

            matched_investments = [
                inv for inv in month_investments
                if inv.get("recurring_bill_id") == bill["id"] or (normalize_canonical_asset(inv.get("asset_name") or inv.get("note"))[0] == c_name)
            ]
            invested_amount = round(sum(inv["amount"] for inv in matched_investments), 2)
            pct = round((invested_amount / target) * 100, 1) if target > 0 else 0.0
            is_fulfilled = (invested_amount >= target)

            streak = await self.get_bill_dca_streak(bill["id"], c_name, year_month_str)

            progress_list.append(
                {
                    "bill_id": bill["id"],
                    "name": bill["name"],
                    "canonical_name": c_name,
                    "target_amount": target,
                    "invested_amount": invested_amount,
                    "percentage": pct,
                    "is_fulfilled": is_fulfilled,
                    "due_day": due_day,
                    "streak_months": streak,
                    "logs_count": len(matched_investments),
                }
            )

        return progress_list

    async def get_bill_dca_streak(self, bill_id: int, canonical_asset_name: str, current_month_str: str) -> int:
        """
        Calculate consecutive months streak of meeting the DCA investment target leading up to current or previous month.
        """
        from datetime import datetime, timedelta

        async with self.get_connection() as conn:
            bill = await self.get_recurring_bill_by_id(bill_id)
            if not bill:
                return 0
            target = bill["amount"]

            async with conn.execute(
                """
                SELECT substr(created_at, 1, 7) as ym, SUM(amount) as month_total
                FROM expenses
                WHERE category = 'Investments & Savings' 
                  AND (recurring_bill_id = ? OR asset_name = ? OR note LIKE ?)
                GROUP BY ym
                ORDER BY ym DESC
                """,
                (bill_id, canonical_asset_name, f"%{canonical_asset_name}%"),
            ) as cur:
                rows = await cur.fetchall()
                month_totals = {r["ym"]: r["month_total"] for r in rows}

        cur_dt = datetime.strptime(f"{current_month_str}-01", "%Y-%m-%d").date()
        
        streak = 0
        check_dt = cur_dt
        cur_ym = check_dt.strftime("%Y-%m")
        if month_totals.get(cur_ym, 0.0) >= target:
            streak += 1
            check_dt = (check_dt.replace(day=1) - timedelta(days=1)).replace(day=1)
        else:
            check_dt = (check_dt.replace(day=1) - timedelta(days=1)).replace(day=1)

        while True:
            ym = check_dt.strftime("%Y-%m")
            if month_totals.get(ym, 0.0) >= target:
                streak += 1
                check_dt = (check_dt.replace(day=1) - timedelta(days=1)).replace(day=1)
            else:
                break

        return streak

    async def get_cumulative_investments_by_asset(self) -> Dict[str, Any]:
        """
        Calculate all-time cumulative capital deployed per asset, portfolio total, and asset classes.
        """
        return await self.get_investments_summary()

    async def get_category_proportions(
        self, start_date_str: Optional[str] = None, end_date_str: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Fetch category spending totals and percentage shares sorted descending."""
        _, total_spent, category_breakdown = await self.get_expenses_summary(start_date_str, end_date_str)
        proportions = []
        if total_spent <= 0:
            return []

        for cat, amt in category_breakdown.items():
            pct = round((amt / total_spent) * 100, 1)
            proportions.append({"category": cat, "amount": amt, "percentage": pct})

        return sorted(proportions, key=lambda x: x["amount"], reverse=True)

    async def snooze_task(self, task_id: int, days_to_add: int = 1) -> Optional[Dict[str, Any]]:
        """Postpone a task's due date by N days."""
        from datetime import datetime, timedelta

        task = await self.get_task_by_id(task_id)
        if not task:
            return None

        current_due = task.get("due_date")
        if current_due:
            try:
                base_dt = datetime.strptime(current_due, "%Y-%m-%d").date()
            except ValueError:
                base_dt = datetime.now().date()
        else:
            base_dt = datetime.now().date()

        new_due_str = (base_dt + timedelta(days=days_to_add)).strftime("%Y-%m-%d")
        return await self.update_task(task_id, due_date=new_due_str)

    # --- SAVINGS GOALS (ISOLATED ASSET ACCUMULATION) ---

    async def create_goal(
        self,
        name: str,
        target_amount: float,
        current_amount: float = 0.0,
        target_date: Optional[str] = None,
        created_at: str = "",
    ) -> int:
        """Create a dedicated savings goal."""
        async with self.get_connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO goals (name, target_amount, current_amount, target_date, is_completed, created_at)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (name, round(target_amount, 2), round(current_amount, 2), target_date, created_at),
            )
            await conn.commit()
            return cur.lastrowid

    async def get_goal_by_id(self, goal_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a savings goal by its exact integer ID."""
        async with self.get_connection() as conn:
            async with conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_active_goals(self) -> List[Dict[str, Any]]:
        """List active savings goals with progress calculations."""
        async with self.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM goals WHERE is_completed = 0 ORDER BY id ASC"
            ) as cur:
                rows = await cur.fetchall()
                goals = []
                for r in rows:
                    g = dict(r)
                    target = g["target_amount"]
                    curr = g["current_amount"]
                    pct = round((curr / target) * 100, 1) if target > 0 else 0.0
                    rem = max(round(target - curr, 2), 0.0)
                    g["percentage"] = min(pct, 100.0)
                    g["remaining"] = rem
                    goals.append(g)
                return goals

    async def deposit_to_goal(self, goal_id: int, amount: float) -> Optional[Dict[str, Any]]:
        """
        Deterministically deposit savings into a goal by its exact integer ID.
        Standard expenses do NOT deduct from this balance.
        """
        goal = await self.get_goal_by_id(goal_id)
        if not goal:
            return None

        new_amt = round(goal["current_amount"] + amount, 2)
        is_done = 1 if new_amt >= goal["target_amount"] else 0

        async with self.get_connection() as conn:
            await conn.execute(
                "UPDATE goals SET current_amount = ?, is_completed = ? WHERE id = ?",
                (new_amt, is_done, goal_id),
            )
            await conn.commit()

        updated = await self.get_goal_by_id(goal_id)
        if updated:
            updated["percentage"] = round((updated["current_amount"] / updated["target_amount"]) * 100, 1)
            updated["remaining"] = max(round(updated["target_amount"] - updated["current_amount"], 2), 0.0)
        return updated

    async def revert_goal_deposit(self, goal_id: int, amount: float) -> Optional[Dict[str, Any]]:
        """Revert / rollback a specific savings deposit."""
        goal = await self.get_goal_by_id(goal_id)
        if not goal:
            return None
        new_amt = max(0.0, round(goal["current_amount"] - amount, 2))
        is_done = 1 if new_amt >= goal["target_amount"] else 0
        async with self.get_connection() as conn:
            await conn.execute(
                "UPDATE goals SET current_amount = ?, is_completed = ? WHERE id = ?",
                (new_amt, is_done, goal_id),
            )
            await conn.commit()
        return await self.get_goal_by_id(goal_id)

    async def delete_goal(self, goal_id: int) -> Optional[Dict[str, Any]]:
        """Delete a savings goal."""
        g = await self.get_goal_by_id(goal_id)
        if not g:
            return None
        async with self.get_connection() as conn:
            await conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
            await conn.commit()
        return g

    # --- ATOMIC ROLLBACK / QUICK UNDO HELPERS ---

    async def delete_expenses_by_ids(self, expense_ids: List[int]) -> int:
        """Deterministically delete specific expenses by primary keys."""
        if not expense_ids:
            return 0
        placeholders = ",".join("?" * len(expense_ids))
        async with self.get_connection() as conn:
            cur = await conn.execute(
                f"DELETE FROM expenses WHERE id IN ({placeholders})",
                tuple(expense_ids),
            )
            await conn.commit()
            return cur.rowcount

    async def delete_tasks_by_ids(self, task_ids: List[int]) -> int:
        """Deterministically delete specific tasks by primary keys."""
        if not task_ids:
            return 0
        placeholders = ",".join("?" * len(task_ids))
        async with self.get_connection() as conn:
            cur = await conn.execute(
                f"DELETE FROM tasks WHERE id IN ({placeholders}) OR parent_id IN ({placeholders})",
                tuple(task_ids) + tuple(task_ids),
            )
            await conn.commit()
            return cur.rowcount

    # --- GAMIFIED PRODUCTIVITY RANKS ---

    async def get_productivity_rank(self, today_str: str) -> Dict[str, Any]:
        """Compute user's gamified rank and badge based on streak and task completion momentum."""
        streak_data = await self.get_productivity_streak(today_str)
        streak = streak_data.get("streak_days", 0)
        tasks_week = streak_data.get("completed_this_week", 0)

        # Ranks: Novice (1-3) -> Strategist (4-7) -> Commander (8-14) -> Master (15-29) -> Legend (30+)
        if streak >= 30:
            level = 5
            title = "Perlica Legend 👑"
            next_goal = "You've attained the highest mastery tier! Keep dominating."
        elif streak >= 15:
            level = 4
            title = "Financial Master 💎"
            next_goal = f"{30 - streak} more active days to reach Legend tier."
        elif streak >= 8 or tasks_week >= 15:
            level = 3
            title = "Productivity Commander 🥇"
            next_goal = f"{15 - streak} more active days to reach Financial Master."
        elif streak >= 4 or tasks_week >= 7:
            level = 2
            title = "Budget Strategist 🥈"
            next_goal = f"{8 - streak} more active days to reach Commander."
        elif streak >= 1:
            level = 1
            title = "Active Tracker 🥉"
            next_goal = f"{4 - streak} more active days to reach Strategist."
        else:
            level = 0
            title = "Apprentice 🌱"
            next_goal = "Log an expense or finish a task today to begin your streak!"

        return {
            "level": level,
            "title": title,
            "streak_days": streak,
            "completed_this_week": tasks_week,
            "next_milestone": next_goal,
        }

    # --- KEYWORD SEARCH & FILTER ---

    async def search_records(self, keyword: str) -> Dict[str, Any]:
        """Search expenses and tasks matching keyword across notes, categories, and descriptions."""
        pattern = f"%{keyword.strip()}%"
        async with self.get_connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM expenses
                WHERE note LIKE ? OR category LIKE ?
                ORDER BY created_at DESC LIMIT 15
                """,
                (pattern, pattern),
            ) as cur:
                exp_rows = await cur.fetchall()
                expenses = [dict(r) for r in exp_rows]

            async with conn.execute(
                """
                SELECT * FROM tasks
                WHERE description LIKE ?
                ORDER BY created_at DESC LIMIT 15
                """,
                (pattern,),
            ) as cur:
                task_rows = await cur.fetchall()
                tasks = [dict(r) for r in task_rows]

        total_spent = sum(e["amount"] for e in expenses)
        return {
            "keyword": keyword,
            "expenses": expenses,
            "tasks": tasks,
            "total_spent_on_matches": round(total_spent, 2),
        }

    async def get_expenses_by_category(
        self,
        category: str,
        start_date_str: Optional[str] = None,
        end_date_str: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], float]:
        """
        Fetch itemized expenses under a specific category within a timeframe, plus subtotal.
        """
        async with self.get_connection() as conn:
            if start_date_str and end_date_str:
                query = """
                    SELECT * FROM expenses
                    WHERE category = ? AND substr(created_at, 1, 10) BETWEEN ? AND ?
                    ORDER BY created_at DESC
                """
                params = (category, start_date_str, end_date_str)
            elif start_date_str:
                query = """
                    SELECT * FROM expenses
                    WHERE category = ? AND substr(created_at, 1, 10) >= ?
                    ORDER BY created_at DESC
                """
                params = (category, start_date_str)
            else:
                query = """
                    SELECT * FROM expenses
                    WHERE category = ?
                    ORDER BY created_at DESC
                """
                params = (category,)

            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                expenses = [dict(r) for r in rows]

        subtotal = round(sum(e["amount"] for e in expenses), 2)
        return expenses, subtotal

    # --- MILESTONES & GAMIFICATION LEDGER ---

    async def try_award_milestone(
        self, milestone_key: str, title: str, description: str, awarded_at: str
    ) -> bool:
        """
        Atomically attempt to award a milestone.
        Returns True ONLY if this milestone was never awarded before.
        Returns False if already awarded (guarantees anti-spam deduplication).
        """
        async with self.get_connection() as conn:
            try:
                cursor = await conn.execute(
                    """
                    INSERT INTO user_milestones (milestone_key, title, description, awarded_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (milestone_key, title, description, awarded_at),
                )
                await conn.commit()
                return cursor.rowcount > 0
            except aiosqlite.IntegrityError:
                return False

    async def check_new_milestones(self, today_str: str, month_str: str) -> List[Dict[str, Any]]:
        """
        Evaluate streaks, DCA commitments, and savings goals against the milestone ledger.
        Returns ONLY newly unlocked milestone dicts.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        unlocked: List[Dict[str, Any]] = []

        # 1. Logging Streaks
        streak_info = await self.get_productivity_streak(today_str)
        s_days = streak_info.get("streak_days", 0)

        if s_days >= 7:
            key = "streak_logging_7d_first"
            if await self.try_award_milestone(
                milestone_key=key,
                title="7-Day Discipline Master",
                description="Logged expenses/tasks consistently for 7 consecutive days!",
                awarded_at=now_str,
            ):
                unlocked.append({
                    "key": key,
                    "title": "7-Day Discipline Master",
                    "badge": "🔥 7-Day Streak",
                    "description": "You've logged your financial and focus activity for 7 consecutive days! Compounding momentum is building.",
                })

        if s_days >= 30:
            key = "streak_logging_30d_first"
            if await self.try_award_milestone(
                milestone_key=key,
                title="30-Day Financial Titan",
                description="Logged consistently for 30 consecutive days!",
                awarded_at=now_str,
            ):
                unlocked.append({
                    "key": key,
                    "title": "30-Day Financial Titan",
                    "badge": "🌟 30-Day Streak",
                    "description": "One full month of unbroken financial clarity. You are in the top tier of disciplined builders.",
                })

        # 2. Savings Goals Milestones (50% and 100%)
        async with self.get_connection() as conn:
            async with conn.execute("SELECT * FROM goals") as cur:
                goals = [dict(r) for r in await cur.fetchall()]
        for g in goals:
            target = g["target_amount"]
            current = g["current_amount"]
            if target > 0:
                pct = (current / target) * 100.0
                if pct >= 50.0:
                    key = f"goal_50pct_{g['id']}"
                    if await self.try_award_milestone(
                        milestone_key=key,
                        title=f"Halfway to {g['name']}",
                        description=f"Saved over 50% (RM {current:.2f} of RM {target:.2f}) toward {g['name']}!",
                        awarded_at=now_str,
                    ):
                        unlocked.append({
                            "key": key,
                            "title": f"Halfway to {g['name']}",
                            "badge": "⚡ 50% Goal Reached",
                            "description": f"You've crossed 50% of your target for **{g['name']}** (RM {current:.2f} / RM {target:.2f})!",
                        })
                if pct >= 100.0:
                    key = f"goal_100pct_{g['id']}"
                    if await self.try_award_milestone(
                        milestone_key=key,
                        title=f"{g['name']} Achieved!",
                        description=f"Fully funded {g['name']} with RM {current:.2f}!",
                        awarded_at=now_str,
                    ):
                        unlocked.append({
                            "key": key,
                            "title": f"{g['name']} Fully Achieved!",
                            "badge": "🏆 100% Fully Funded",
                            "description": f"Congratulations! You have reached your target for **{g['name']}** (RM {current:.2f})!",
                        })

        # 3. DCA Consistency Milestones (3-Month Streak per Recurring Bill)
        dca_progress = await self.get_dca_progress(month_str)
        for d in dca_progress:
            if d.get("streak_months", 0) >= 3:
                key = f"dca_streak_3mo_{d['bill_id']}_{month_str}"
                if await self.try_award_milestone(
                    milestone_key=key,
                    title=f"DCA Compounder: {d['name']}",
                    description=f"Met your monthly investment target for {d['name']} for 3 consecutive months!",
                    awarded_at=now_str,
                ):
                    unlocked.append({
                        "key": key,
                        "title": f"3-Month DCA Compounder: {d['name']}",
                        "badge": "🔥 3-Month DCA Streak",
                        "description": f"You've fulfilled your monthly DCA commitment for **{d['name']}** 3 months in a row. Dollar-cost averaging mastery!",
                    })

        return unlocked

    # --- PAGINATED EXPENSES & PRIORITY FOCUS ---

    async def get_paginated_expenses(
        self, month_str: str, page: int = 1, page_size: int = 10
    ) -> Tuple[List[Dict[str, Any]], int, int, int]:
        """
        Fetch paginated expenses for a month with strict non-negative offset bounds.
        Returns: (expenses_slice, safe_page, total_pages, total_count)
        """
        async with self.get_connection() as conn:
            # 1. Total count
            async with conn.execute(
                "SELECT COUNT(*) as count FROM expenses WHERE substr(created_at, 1, 7) = ?",
                (month_str,),
            ) as cur:
                row = await cur.fetchone()
                total_count = row["count"] if row else 0

            # 2. Total pages is strictly >= 1
            total_pages = max(1, (total_count + page_size - 1) // page_size)

            # 3. Safe page is strictly clamped
            safe_page = max(1, min(page, total_pages))

            # 4. Offset is strictly non-negative (>= 0)
            offset = (safe_page - 1) * page_size

            # 5. Fetch slice
            if total_count > 0:
                async with conn.execute(
                    """
                    SELECT * FROM expenses
                    WHERE substr(created_at, 1, 7) = ?
                    ORDER BY created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (month_str, page_size, offset),
                ) as cur:
                    rows = await cur.fetchall()
                    expenses = [dict(r) for r in rows]
            else:
                expenses = []

        return expenses, safe_page, total_pages, total_count

    async def get_highest_priority_tasks(self) -> List[Dict[str, Any]]:
        """
        Fetch active open tasks sorted by priority (HIGH -> MEDIUM -> LOW), due date, and ID.
        """
        async with self.get_connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'OPEN'
                ORDER BY
                    CASE priority
                        WHEN 'HIGH' THEN 1
                        WHEN 'MEDIUM' THEN 2
                        WHEN 'LOW' THEN 3
                        ELSE 4
                    END ASC,
                    CASE WHEN due_date IS NULL OR due_date = '' THEN '9999-99-99' ELSE due_date END ASC,
                    id ASC
                """
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def get_monthly_ron95_liters(self, month_str: str) -> float:
        """
        Calculate total RON95 liters consumed in a given calendar month.
        Strictly excludes RON97, Diesel, cooking oil, and restaurants.
        """
        async with self.get_connection() as conn:
            async with conn.execute(
                """
                SELECT amount, category, note FROM expenses
                WHERE substr(created_at, 1, 7) = ? AND category = 'Transport'
                ORDER BY created_at ASC
                """,
                (month_str,),
            ) as cur:
                rows = await cur.fetchall()

        cumulative_liters = 0.0
        for r in rows:
            f_info = classify_fuel_expense(r["category"], r["note"])
            if f_info and f_info["consumes_subsidy"]:
                details = calculate_fuel_details(r["amount"], f_info, cumulative_liters)
                cumulative_liters = details["new_total_ron95_liters"]

        return cumulative_liters

    async def find_recent_similar_expense(
        self,
        amount: float,
        category: str,
        window_minutes: int = 5,
        now_local: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Check if an identical expense (same amount and category) was recorded within the last N minutes.
        Uses timezone-aligned comparison to prevent UTC-to-MYR offset drift.
        Returns: { ...expense_row, 'minutes_ago': int } or None
        """
        if now_local is None:
            now_local = datetime.now(settings.tz)
        elif now_local.tzinfo is None:
            now_local = now_local.replace(tzinfo=settings.tz)

        threshold_dt = now_local - timedelta(minutes=window_minutes)
        threshold_str = threshold_dt.strftime("%Y-%m-%d %H:%M:%S")

        async with self.get_connection() as conn:
            async with conn.execute(
                """
                SELECT * FROM expenses
                WHERE amount = ? AND category = ? AND created_at >= ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (amount, category, threshold_str),
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    return None

                res = dict(row)
                try:
                    created_dt = datetime.strptime(res["created_at"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=settings.tz)
                    diff_mins = max(0, int((now_local - created_dt).total_seconds() / 60))
                except Exception:
                    diff_mins = 0
                res["minutes_ago"] = diff_mins
                return res

    # --- KNOWLEDGE & CODEBASE COPILOT METHODS ---

    async def get_or_create_source(
        self,
        name: str,
        source_type: str,
        source_ref: str,
        eligible_count: int = 0,
    ) -> int:
        """Fetch or create a knowledge source record."""
        now_str = datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M:%S")
        async with self.get_connection() as conn:
            async with conn.execute(
                "SELECT id FROM sources WHERE source_ref = ?", (source_ref,)
            ) as cur:
                row = await cur.fetchone()
                if row:
                    return row["id"]

            cursor = await conn.execute(
                """
                INSERT INTO sources (name, source_type, source_ref, eligible_count, indexed_count, status, created_at)
                VALUES (?, ?, ?, ?, 0, 'COMPLETE', ?)
                """,
                (name, source_type, source_ref, eligible_count, now_str),
            )
            await conn.commit()
            return cursor.lastrowid

    async def update_source_status(
        self,
        source_id: int,
        eligible_count: int,
        indexed_count: int,
        status: str,
        last_error: Optional[str] = None,
    ) -> None:
        """Update source metrics, status ('COMPLETE', 'PARTIAL', 'FAILED'), and timestamp."""
        now_str = datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M:%S")
        async with self.get_connection() as conn:
            await conn.execute(
                """
                UPDATE sources
                SET eligible_count = ?, indexed_count = ?, status = ?, last_error = ?, last_sync_at = ?
                WHERE id = ?
                """,
                (eligible_count, indexed_count, status, last_error, now_str, source_id),
            )
            await conn.commit()

    async def get_source_by_ref(self, source_ref: str) -> Optional[Dict[str, Any]]:
        """Fetch source record by its unique reference string."""
        async with self.get_connection() as conn:
            async with conn.execute("SELECT * FROM sources WHERE source_ref = ?", (source_ref,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_source_by_id(self, source_id: int) -> Optional[Dict[str, Any]]:
        """Fetch source record by primary key."""
        async with self.get_connection() as conn:
            async with conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_source_files_manifest(self, source_id: int) -> Dict[str, Dict[str, Any]]:
        """Get mapping of path -> row dict for all known source files in a source."""
        async with self.get_connection() as conn:
            async with conn.execute("SELECT * FROM source_files WHERE source_id = ?", (source_id,)) as cur:
                rows = await cur.fetchall()
                return {r["path"]: dict(r) for r in rows}

    async def create_ingestion_job(self, source_type: str, target_ref: str) -> int:
        """Create a new background ingestion job record."""
        now_str = datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M:%S")
        async with self.get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO ingestion_jobs (source_type, target_ref, status, progress_text, created_at, updated_at)
                VALUES (?, ?, 'PENDING', 'Queued for processing...', ?, ?)
                """,
                (source_type, target_ref, now_str, now_str),
            )
            await conn.commit()
            return cursor.lastrowid

    async def update_ingestion_job(
        self,
        job_id: int,
        status: str,
        progress_text: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Update background ingestion job progress and status."""
        now_str = datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M:%S")
        async with self.get_connection() as conn:
            await conn.execute(
                """
                UPDATE ingestion_jobs
                SET status = ?, progress_text = COALESCE(?, progress_text), error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, progress_text, error_message, now_str, job_id),
            )
            await conn.commit()

    async def get_ingestion_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        """Fetch ingestion job status."""
        async with self.get_connection() as conn:
            async with conn.execute("SELECT * FROM ingestion_jobs WHERE id = ?", (job_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def commit_file_reconciliation(
        self,
        source_id: int,
        file_path: str,
        blob_sha: Optional[str],
        sync_id: int,
        chunks: List[Dict[str, Any]],
        embeddings: List[Tuple[str, bytes]],
    ) -> int:
        """
        Atomically commit reconciled chunks and embeddings for a specific file.
        Deletes old chunks and replaces them within a short single-transaction window.
        """
        now_str = datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M:%S")
        async with self.get_connection() as conn:
            # 1. Upsert source_files row
            cursor = await conn.execute(
                """
                INSERT INTO source_files (source_id, path, blob_sha, last_seen_sync_id, status, updated_at)
                VALUES (?, ?, ?, ?, 'INDEXED', ?)
                ON CONFLICT(source_id, path) DO UPDATE SET
                    blob_sha = excluded.blob_sha,
                    last_seen_sync_id = excluded.last_seen_sync_id,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                RETURNING id;
                """,
                (source_id, file_path, blob_sha, sync_id, now_str),
            )
            row = await cursor.fetchone()
            file_id = row["id"] if row else None
            if not file_id:
                async with conn.execute("SELECT id FROM source_files WHERE source_id = ? AND path = ?", (source_id, file_path)) as c2:
                    r2 = await c2.fetchone()
                    file_id = r2["id"]

            # 2. Delete old chunks for this file
            await conn.execute("DELETE FROM knowledge_chunks WHERE source_file_id = ?", (file_id,))

            # 3. Insert new chunks and embeddings
            for i, c in enumerate(chunks):
                cur_chunk = await conn.execute(
                    """
                    INSERT INTO knowledge_chunks (
                        source_id, source_file_id, section_title, permalink_url,
                        content, content_hash, metadata_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        file_id,
                        c.get("section_title"),
                        c.get("permalink_url"),
                        c["content"],
                        c.get("content_hash", ""),
                        json.dumps(c.get("metadata", {})),
                        now_str,
                    ),
                )
                chunk_id = cur_chunk.lastrowid
                if i < len(embeddings):
                    model_id, emb_blob = embeddings[i]
                    await conn.execute(
                        """
                        INSERT INTO chunk_embeddings (chunk_id, model_id, embedding_blob)
                        VALUES (?, ?, ?)
                        """,
                        (chunk_id, model_id, emb_blob),
                    )

            await conn.commit()
            return file_id

    async def mark_source_files_excluded_cap(
        self,
        source_id: int,
        capped_files: List[Tuple[str, str]],
        sync_id: int,
    ) -> None:
        """
        Record remote eligible files that were seen in the remote manifest but not indexed due to 250 cap.
        Updates last_seen_sync_id = sync_id and status = 'EXCLUDED_CAP', ensuring they are not mistakenly purged.
        """
        now_str = datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M:%S")
        async with self.get_connection() as conn:
            for path, blob_sha in capped_files:
                cursor = await conn.execute(
                    """
                    INSERT INTO source_files (source_id, path, blob_sha, last_seen_sync_id, status, updated_at)
                    VALUES (?, ?, ?, ?, 'EXCLUDED_CAP', ?)
                    ON CONFLICT(source_id, path) DO UPDATE SET
                        blob_sha = excluded.blob_sha,
                        last_seen_sync_id = excluded.last_seen_sync_id,
                        status = excluded.status,
                        updated_at = excluded.updated_at
                    RETURNING id;
                    """,
                    (source_id, path, blob_sha, sync_id, now_str),
                )
                row = await cursor.fetchone()
                file_id = row["id"] if row else None
                if not file_id:
                    async with conn.execute("SELECT id FROM source_files WHERE source_id = ? AND path = ?", (source_id, path)) as c2:
                        r2 = await c2.fetchone()
                        file_id = r2["id"] if r2 else None

                if file_id:
                    await conn.execute("DELETE FROM knowledge_chunks WHERE source_file_id = ?", (file_id,))
            await conn.commit()

    async def mark_source_file_failed(
        self,
        source_id: int,
        file_path: str,
        blob_sha: Optional[str],
        sync_id: int,
        status: str = "FAILED_FETCH",
    ) -> None:
        """
        Record a remote file that failed to fetch, parse, or embed during sync.
        Updates last_seen_sync_id = sync_id and status = status.
        Crucial Invariant: Preserves existing knowledge_chunks so transient network failures do not cause data loss.
        """
        now_str = datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M:%S")
        async with self.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO source_files (source_id, path, blob_sha, last_seen_sync_id, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, path) DO UPDATE SET
                    blob_sha = COALESCE(excluded.blob_sha, source_files.blob_sha),
                    last_seen_sync_id = excluded.last_seen_sync_id,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (source_id, file_path, blob_sha, sync_id, status, now_str),
            )
            await conn.commit()

    async def mark_source_file_secret_excluded(
        self,
        source_id: int,
        file_path: str,
        blob_sha: Optional[str],
        sync_id: int,
    ) -> None:
        """
        Record a remote file that was excluded because it contains secrets or private keys.
        Updates last_seen_sync_id = sync_id and status = 'EXCLUDED_SECRET'.
        Purges any previously stored chunks for this file to prevent secret leakage.
        """
        now_str = datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M:%S")
        async with self.get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO source_files (source_id, path, blob_sha, last_seen_sync_id, status, updated_at)
                VALUES (?, ?, ?, ?, 'EXCLUDED_SECRET', ?)
                ON CONFLICT(source_id, path) DO UPDATE SET
                    blob_sha = excluded.blob_sha,
                    last_seen_sync_id = excluded.last_seen_sync_id,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                RETURNING id;
                """,
                (source_id, file_path, blob_sha, sync_id, now_str),
            )
            row = await cursor.fetchone()
            file_id = row["id"] if row else None
            if not file_id:
                async with conn.execute("SELECT id FROM source_files WHERE source_id = ? AND path = ?", (source_id, file_path)) as c2:
                    r2 = await c2.fetchone()
                    file_id = r2["id"] if r2 else None

            if file_id:
                await conn.execute("DELETE FROM knowledge_chunks WHERE source_file_id = ?", (file_id,))
            await conn.commit()

    async def purge_unseen_source_files(self, source_id: int, current_sync_id: int) -> int:
        """
        Purge source files (and cascading chunks/FTS) that were not seen in the current sync run.
        """
        async with self.get_connection() as conn:
            cursor = await conn.execute(
                """
                DELETE FROM source_files
                WHERE source_id = ? AND (last_seen_sync_id != ? OR last_seen_sync_id IS NULL)
                """,
                (source_id, current_sync_id),
            )
            purged_count = cursor.rowcount
            await conn.commit()
            return purged_count

    async def fts_search_knowledge(
        self,
        query_text: str,
        source_id: Optional[int] = None,
        limit: int = 15,
    ) -> List[Dict[str, Any]]:
        """
        Execute BM25 lexical search using SQLite FTS5 index.
        Prioritizes conjunctive AND search, falling back to disjunctive OR search.
        """
        clean_q = re.sub(r'[^\w\s_-]', ' ', query_text).strip()
        if not clean_q:
            return []

        tokens = clean_q.split()
        if not tokens:
            return []

        and_query = " ".join([f'"{t}"*' for t in tokens])
        or_query = " OR ".join([f'"{t}"*' for t in tokens if len(t) > 1] or [f'"{tokens[0]}"*'])

        async def _execute_fts(conn, fts_q: str):
            if source_id is not None:
                sql = """
                    SELECT kc.*, bm25(knowledge_chunks_fts) as bm25_rank, s.name as source_name, s.source_type, s.source_ref, s.last_sync_at as source_last_sync_at, sf.path as file_path
                    FROM knowledge_chunks_fts fts
                    JOIN knowledge_chunks kc ON fts.rowid = kc.id
                    JOIN sources s ON kc.source_id = s.id
                    JOIN source_files sf ON kc.source_file_id = sf.id
                    WHERE knowledge_chunks_fts MATCH ? AND kc.source_id = ?
                    AND sf.status = 'INDEXED'
                    ORDER BY bm25_rank ASC
                    LIMIT ?
                """
                params = (fts_q, source_id, limit)
            else:
                sql = """
                    SELECT kc.*, bm25(knowledge_chunks_fts) as bm25_rank, s.name as source_name, s.source_type, s.source_ref, s.last_sync_at as source_last_sync_at, sf.path as file_path
                    FROM knowledge_chunks_fts fts
                    JOIN knowledge_chunks kc ON fts.rowid = kc.id
                    JOIN sources s ON kc.source_id = s.id
                    JOIN source_files sf ON kc.source_file_id = sf.id
                    WHERE knowledge_chunks_fts MATCH ?
                    AND sf.status = 'INDEXED'
                    ORDER BY bm25_rank ASC
                    LIMIT ?
                """
                params = (fts_q, limit)

            async with conn.execute(sql, params) as cur:
                rows = await cur.fetchall()
                results = []
                for r in rows:
                    item = dict(r)
                    if item.get("metadata_json"):
                        try:
                            item["metadata"] = json.loads(item["metadata_json"])
                        except Exception:
                            item["metadata"] = {}
                    else:
                        item["metadata"] = {}
                    results.append(item)
                return results

        async with self.get_connection() as conn:
            try:
                return await _execute_fts(conn, and_query)
            except Exception:
                # Fallback to OR query only if AND query had syntax error
                try:
                    return await _execute_fts(conn, or_query)
                except Exception:
                    return []

    async def get_all_chunks_with_embeddings(
        self,
        source_id: Optional[int] = None,
        model_id: str = "bge-small-en-v1.5",
    ) -> List[Dict[str, Any]]:
        """
        Fetch all chunks with their pre-computed binary embedding vectors for NumPy cosine search.
        Filters out files that failed to index or were excluded (only INDEXED files participate).
        """
        async with self.get_connection() as conn:
            if source_id is not None:
                sql = """
                    SELECT kc.*, ce.model_id, ce.embedding_blob, s.name as source_name, s.source_type, s.source_ref, s.last_sync_at as source_last_sync_at, sf.path as file_path
                    FROM knowledge_chunks kc
                    JOIN chunk_embeddings ce ON kc.id = ce.chunk_id
                    JOIN sources s ON kc.source_id = s.id
                    JOIN source_files sf ON kc.source_file_id = sf.id
                    WHERE ce.model_id = ? AND kc.source_id = ?
                    AND sf.status = 'INDEXED'
                """
                params = (model_id, source_id)
            else:
                sql = """
                    SELECT kc.*, ce.model_id, ce.embedding_blob, s.name as source_name, s.source_type, s.source_ref, s.last_sync_at as source_last_sync_at, sf.path as file_path
                    FROM knowledge_chunks kc
                    JOIN chunk_embeddings ce ON kc.id = ce.chunk_id
                    JOIN sources s ON kc.source_id = s.id
                    JOIN source_files sf ON kc.source_file_id = sf.id
                    WHERE ce.model_id = ?
                    AND sf.status = 'INDEXED'
                """
                params = (model_id,)

            async with conn.execute(sql, params) as cur:
                rows = await cur.fetchall()
                results = []
                for r in rows:
                    item = dict(r)
                    if item.get("metadata_json"):
                        try:
                            item["metadata"] = json.loads(item["metadata_json"])
                        except Exception:
                            item["metadata"] = {}
                    else:
                        item["metadata"] = {}
                    results.append(item)
                return results

    async def record_answer_with_evidence(
        self,
        query: str,
        response: str,
        user_id: Optional[str],
        evidence_list: List[Dict[str, Any]],
    ) -> int:
        """
        Record a generated answer and persist historical answer evidence snapshots.
        Evidence snapshots are decoupled from cascades so they survive source/file purges.
        """
        now_str = datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M:%S")
        async with self.get_connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO answers (query, response, user_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (query, response, str(user_id) if user_id else None, now_str),
            )
            answer_id = cur.lastrowid

            for ev in evidence_list:
                await conn.execute(
                    """
                    INSERT INTO answer_evidence (answer_id, source_id, source_file_id, citation, raw_text, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        answer_id,
                        ev.get("source_id"),
                        ev.get("source_file_id"),
                        ev.get("citation", "Source"),
                        ev.get("content", ""),
                        json.dumps(ev.get("metadata", {})),
                    ),
                )

            await conn.commit()
            return answer_id

    async def get_answer_evidence_snapshots(self, answer_id: int) -> List[Dict[str, Any]]:
        """Fetch immutable historical evidence snapshots for a given answer ID."""
        async with self.get_connection() as conn:
            async with conn.execute("SELECT * FROM answer_evidence WHERE answer_id = ?", (answer_id,)) as cur:
                rows = await cur.fetchall()
                results = []
                for r in rows:
                    item = dict(r)
                    if item.get("metadata_json"):
                        try:
                            item["metadata"] = json.loads(item["metadata_json"])
                        except Exception:
                            item["metadata"] = {}
                    else:
                        item["metadata"] = {}
                    results.append(item)
                return results

    async def log_retrieval_telemetry(
        self,
        query: str,
        top_cosine: List[Dict[str, Any]],
        bm25_count: int,
        rrf_results: List[Dict[str, Any]],
        selected_sources: List[str],
        context_tokens: int,
        answer_id: Optional[int] = None,
        user_feedback: Optional[str] = None,
    ) -> int:
        """Persist retrieval telemetry for Phase 2 calibration and evaluation."""
        now_str = datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M:%S")
        async with self.get_connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO retrieval_logs (
                    query, top_cosine_json, bm25_hit_count, rrf_results_json,
                    selected_sources_json, context_token_count, answer_id, user_feedback, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    query,
                    json.dumps(top_cosine[:5]),
                    bm25_count,
                    json.dumps(rrf_results[:5]),
                    json.dumps(selected_sources),
                    context_tokens,
                    answer_id,
                    user_feedback,
                    now_str,
                ),
            )
            await conn.commit()
            return cur.lastrowid

    async def get_knowledge_sources_summary(self) -> List[Dict[str, Any]]:
        """Fetch dashboard summary of all knowledge sources with chunk and file counts."""
        async with self.get_connection() as conn:
            async with conn.execute(
                """
                SELECT 
                    s.*,
                    COUNT(DISTINCT sf.id) as actual_files_count,
                    COUNT(DISTINCT kc.id) as total_chunks_count
                FROM sources s
                LEFT JOIN source_files sf ON s.id = sf.source_id
                LEFT JOIN knowledge_chunks kc ON s.id = kc.source_id
                GROUP BY s.id
                ORDER BY s.created_at DESC
                """
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]

    async def delete_knowledge_source(self, source_ref: str) -> bool:
        """
        Delete a knowledge source by reference. Cascades to source_files, knowledge_chunks,
        FTS5 entries (via triggers), and embeddings, while preserving historical answer_evidence.
        """
        async with self.get_connection() as conn:
            cur = await conn.execute("DELETE FROM sources WHERE source_ref = ?", (source_ref,))
            await conn.commit()
            return cur.rowcount > 0

    async def store_quick_note(self, content: str, section_title: str = "Quick Note") -> int:
        """Store a quick note directly into SQLite NOTES source and link to an INDEXED source_files row."""
        source_id = await self.get_or_create_source("Quick Notes", "NOTES", "local:notes")
        content_hash = re.sub(r'\s+', ' ', content).strip()
        now_str = datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M:%S")

        async with self.get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO source_files (source_id, path, blob_sha, last_seen_sync_id, status, updated_at)
                VALUES (?, 'quick_notes.md', 'local_note', 1, 'INDEXED', ?)
                ON CONFLICT(source_id, path) DO UPDATE SET
                    status = 'INDEXED',
                    updated_at = excluded.updated_at
                RETURNING id;
                """,
                (source_id, now_str),
            )
            row = await cursor.fetchone()
            file_id = row["id"] if row else None
            if not file_id:
                async with conn.execute("SELECT id FROM source_files WHERE source_id = ? AND path = 'quick_notes.md'", (source_id,)) as c2:
                    r2 = await c2.fetchone()
                    file_id = r2["id"] if r2 else None

            cur = await conn.execute(
                """
                INSERT INTO knowledge_chunks (source_id, source_file_id, section_title, content, content_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source_id, file_id, section_title, content, content_hash, now_str),
            )
            chunk_id = cur.lastrowid
            await conn.commit()
            return chunk_id


def classify_fuel_expense(category: str, note: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Classify vehicle fuel transactions into distinct grades.
    Excludes cooking oil (minyak masak) and restaurants (Shell Out).
    Returns: {grade: 'RON95'|'RON97'|'Diesel', price_per_liter: float, consumes_subsidy: bool} or None
    """
    if category != "Transport" or not note:
        return None
    cleaned = note.lower()

    # Explicit negative exclusions (cooking oil, groceries, restaurants)
    negative_exclusions = {
        "minyak masak", "cooking oil", "shell out", "shellout", "speedmart",
        "groceries", "food", "kedai runcit", "pasar", "ikan", "ayam"
    }
    if any(neg in cleaned for neg in negative_exclusions):
        return None

    # 1. RON97 Check (Unsubsidized Floating ~RM 3.47/L)
    if "ron97" in cleaned or "v-power racing" in cleaned or "vpower" in cleaned:
        return {"grade": "RON97", "price_per_liter": 3.47, "consumes_subsidy": False}

    # 2. Diesel Check (Euro 5 Diesel ~RM 3.35/L)
    if "diesel" in cleaned or "euro 5" in cleaned or "euro5" in cleaned:
        return {"grade": "Diesel", "price_per_liter": 3.35, "consumes_subsidy": False}

    # 3. RON95 Subsidized Tier (Default for petrol, petronas, shell, caltex, bhp, isi minyak)
    positive_fuel_tokens = {
        "ron95", "petrol", "isi minyak", "minyak kereta", "petronas", "caltex",
        "bhp", "petron", "shell", "pump minyak", "fuel", "minyak"
    }
    if any(pos in cleaned for pos in positive_fuel_tokens):
        return {"grade": "RON95", "price_per_liter": 1.99, "consumes_subsidy": True}

    return None


def calculate_fuel_details(
    amount: float, fuel_info: Dict[str, Any], prior_ron95_liters: float = 0.0
) -> Dict[str, Any]:
    """
    Calculate liters pumped and 200L RON95 subsidy quota impact.
    RON95: First 200L @ RM 1.99/L, subsequent liters @ RM 2.60/L.
    RON97 / Diesel: Exact market pricing with 0 subsidy consumption.
    """
    grade = fuel_info["grade"]
    consumes_subsidy = fuel_info["consumes_subsidy"]

    if not consumes_subsidy:
        price = fuel_info["price_per_liter"]
        liters = round(amount / price, 2)
        return {
            "grade": grade,
            "liters_added": liters,
            "tier_label": f"Market Rate (@ RM {price:.2f}/L)",
            "consumes_subsidy": False,
            "prior_ron95_liters": prior_ron95_liters,
            "new_total_ron95_liters": prior_ron95_liters,
            "ron95_quota_remaining": max(0.0, round(200.0 - prior_ron95_liters, 2)),
        }

    # RON95 Two-Tier Subsidy Math
    rem_sub_liters = max(0.0, 200.0 - prior_ron95_liters)
    cost_to_fill_sub = rem_sub_liters * 1.99

    if amount <= cost_to_fill_sub or rem_sub_liters >= (amount / 1.99):
        # Fully Subsidized
        sub_liters = round(amount / 1.99, 2)
        total_liters = sub_liters
        new_ron95_total = round(prior_ron95_liters + total_liters, 2)
        tier_label = "Subsidized Tier (@ RM 1.99/L)"
    elif rem_sub_liters > 0:
        # Split Boundary (Part subsidized @ 1.99, part unsubsidized @ 2.60)
        sub_liters = round(rem_sub_liters, 2)
        rem_amount = amount - cost_to_fill_sub
        unsub_liters = round(rem_amount / 2.60, 2)
        total_liters = round(sub_liters + unsub_liters, 2)
        new_ron95_total = round(prior_ron95_liters + total_liters, 2)
        tier_label = f"Split: {sub_liters}L @ RM 1.99 + {unsub_liters}L @ RM 2.60"
    else:
        # Fully Unsubsidized
        unsub_liters = round(amount / 2.60, 2)
        total_liters = unsub_liters
        new_ron95_total = round(prior_ron95_liters + total_liters, 2)
        tier_label = "Unsubsidized Tier (@ RM 2.60/L)"

    quota_left = max(0.0, round(200.0 - new_ron95_total, 2))
    return {
        "grade": "RON95",
        "liters_added": total_liters,
        "tier_label": tier_label,
        "consumes_subsidy": True,
        "prior_ron95_liters": prior_ron95_liters,
        "new_total_ron95_liters": new_ron95_total,
        "ron95_quota_remaining": quota_left,
    }
