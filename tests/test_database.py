import pytest
import pytest_asyncio
from src.database import DatabaseManager


@pytest_asyncio.fixture
async def db(tmp_path):
    db_file = str(tmp_path / "test_tracker.db")
    manager = DatabaseManager(db_file)
    await manager.init_db()
    return manager


@pytest.mark.asyncio
async def test_insert_and_get_expenses(db: DatabaseManager):
    eid1 = await db.insert_expense(15.50, "Food & Dining", "Chicken rice", "2026-08-24 12:30:00")
    eid2 = await db.insert_expense(30.00, "Transport", "Grab ride", "2026-08-24 18:00:00")
    eid3 = await db.insert_expense(50.00, "Groceries", "Supermarket", "2026-08-23 10:00:00")

    assert eid1 == 1
    assert eid2 == 2
    assert eid3 == 3

    # Daily summary for 2026-08-24
    expenses_today, total_today, _ = await db.get_daily_summary("2026-08-24")
    assert len(expenses_today) == 2
    assert total_today == 45.50

    # Summary with category breakdown for all
    all_expenses, total_all, breakdown = await db.get_expenses_summary()
    assert len(all_expenses) == 3
    assert total_all == 95.50
    assert breakdown["Food & Dining"] == 15.50
    assert breakdown["Transport"] == 30.00
    assert breakdown["Groceries"] == 50.00


@pytest.mark.asyncio
async def test_insert_and_complete_tasks(db: DatabaseManager):
    tid1 = await db.insert_task("Call client A", "HIGH", "2026-08-25", "17:00", "2026-08-24 22:00:00")
    tid2 = await db.insert_task("Call client B", "MEDIUM", None, None, "2026-08-24 22:01:00")
    tid3 = await db.insert_task("Buy coffee beans", "LOW", None, None, "2026-08-24 22:02:00")

    assert tid1 == 1
    assert tid2 == 2
    assert tid3 == 3

    open_tasks = await db.get_open_tasks()
    assert len(open_tasks) == 3
    # Order should be HIGH -> MEDIUM -> LOW
    assert open_tasks[0]["id"] == tid1
    assert open_tasks[1]["id"] == tid2
    assert open_tasks[2]["id"] == tid3

    # Deterministic completion by ID
    res = await db.complete_task_by_id(tid1, "2026-08-25 10:00:00")
    assert res is not None
    assert res["status"] == "DONE"
    assert res["completed_at"] == "2026-08-25 10:00:00"

    # Completing already completed task returns None
    res_repeat = await db.complete_task_by_id(tid1, "2026-08-25 10:01:00")
    assert res_repeat is None

    # Completing non-existent task returns None
    res_none = await db.complete_task_by_id(999, "2026-08-25 10:00:00")
    assert res_none is None

    # Batch completion
    batch_res = await db.complete_tasks_by_ids([tid2, tid3, 999], "2026-08-25 11:00:00")
    assert len(batch_res) == 2

    # Verify no open tasks remain
    remaining = await db.get_open_tasks()
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_full_snapshot(db: DatabaseManager):
    await db.insert_expense(20.00, "Food & Dining", "Nasi Lemak", "2026-08-24 09:00:00")
    tid = await db.insert_task("Read paper", "LOW", created_at="2026-08-24 09:00:00")
    await db.complete_task_by_id(tid, "2026-08-24 11:00:00")
    await db.insert_task("Prepare demo", "HIGH", created_at="2026-08-24 12:00:00")

    snapshot = await db.get_full_snapshot("2026-08-24", "2026-08-24")
    assert snapshot["total_spent"] == 20.00
    assert len(snapshot["completed_tasks"]) == 1
    assert len(snapshot["open_tasks"]) == 1
    assert snapshot["open_tasks"][0]["description"] == "Prepare demo"
