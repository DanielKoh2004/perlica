import os
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
        """Initialize SQLite database tables, indexes, and apply column migrations."""
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        async with self.get_connection() as conn:
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

            # Auto-migration for existing tasks table missing phase columns
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

    async def complete_task_by_id(
        self, task_id: int, completed_at: str
    ) -> Optional[Dict[str, Any]]:
        """
        Deterministically mark a task as DONE by its exact integer ID.
        If it has sub-phases, completing the parent completes all remaining subphases.
        If a subphase is completed, check if all sibling phases are completed to auto-complete parent.
        """
        async with self.get_connection() as conn:
            async with conn.execute(
                "SELECT * FROM tasks WHERE id = ? AND status = 'OPEN'", (task_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                task_dict = dict(row)

            # Mark this task as DONE
            await conn.execute(
                "UPDATE tasks SET status = 'DONE', completed_at = ? WHERE id = ?",
                (completed_at, task_id),
            )

            # If this was a parent task, mark all open child phases as DONE
            await conn.execute(
                "UPDATE tasks SET status = 'DONE', completed_at = ? WHERE parent_id = ? AND status = 'OPEN'",
                (completed_at, task_id),
            )

            # If this was a child phase, check if all sibling phases of the parent are done
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
        """
        Return active OPEN tasks ordered by priority and hierarchy (parents first, with subphases).
        """
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
