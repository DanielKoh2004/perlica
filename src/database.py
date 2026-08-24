import os
import io
import csv
from datetime import date, datetime
import aiosqlite
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Tuple, Any, AsyncGenerator


class DatabaseManager:
    def __init__(self, db_path: str = "tracker.db"):
        self.db_path = db_path

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """Async context manager yielding a connection configured with Row factory."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
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

            # Auto-migration for tasks table columns
            async with conn.execute("PRAGMA table_info(tasks);") as cursor:
                columns = [row["name"] for row in await cursor.fetchall()]
                if "parent_id" not in columns:
                    await conn.execute("ALTER TABLE tasks ADD COLUMN parent_id INTEGER;")
                if "phase_name" not in columns:
                    await conn.execute("ALTER TABLE tasks ADD COLUMN phase_name TEXT;")
                if "phase_order" not in columns:
                    await conn.execute("ALTER TABLE tasks ADD COLUMN phase_order INTEGER DEFAULT 1;")

            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_expenses_created ON expenses(created_at);"
            )
            await conn.commit()

    # --- EXPENSES ---

    async def insert_expense(
        self,
        amount: float,
        category: str,
        note: Optional[str],
        created_at: str,
    ) -> int:
        """Insert an expense record and return its primary key ID."""
        async with self.get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO expenses (amount, category, note, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (round(amount, 2), category, note, created_at),
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
        Safeguards:
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
        total_budget = sum(budgets.values())

        async with self.get_connection() as conn:
            async with conn.execute(
                """
                SELECT SUM(amount) as total_spent FROM expenses
                WHERE substr(created_at, 1, 7) = ?
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
