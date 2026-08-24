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

    expenses_today, total_today, _ = await db.get_daily_summary("2026-08-24")
    assert len(expenses_today) == 2
    assert total_today == 45.50

    all_expenses, total_all, breakdown = await db.get_expenses_summary()
    assert len(all_expenses) == 3
    assert total_all == 95.50
    assert breakdown["Food & Dining"] == 15.50
    assert breakdown["Transport"] == 30.00
    assert breakdown["Groceries"] == 50.00


@pytest.mark.asyncio
async def test_budgets_and_status(db: DatabaseManager):
    await db.set_budget("Food & Dining", 500.0)
    await db.set_budget("Transport", 200.0)

    budgets = await db.get_budgets()
    assert budgets["Food & Dining"] == 500.0
    assert budgets["Transport"] == 200.0

    # Insert expenses in 2026-08
    await db.insert_expense(450.0, "Food & Dining", "Meals", "2026-08-10 12:00:00")
    await db.insert_expense(50.0, "Transport", "Petrol", "2026-08-12 12:00:00")

    status = await db.get_budget_status("2026-08")
    assert len(status) == 2
    food_stat = next(s for s in status if s["category"] == "Food & Dining")
    assert food_stat["spent"] == 450.0
    assert food_stat["percentage"] == 90.0
    assert food_stat["is_warning"] is True
    assert food_stat["is_overspent"] is False


@pytest.mark.asyncio
async def test_recurring_bills(db: DatabaseManager):
    bid1 = await db.add_recurring_bill("Unifi", 139.0, "Utilities & Bills", 1)
    bid2 = await db.add_recurring_bill("Netflix", 55.0, "Entertainment", 15)

    assert bid1 == 1
    assert bid2 == 2

    due_on_1st = await db.get_due_recurring_bills(1)
    assert len(due_on_1st) == 1
    assert due_on_1st[0]["name"] == "Unifi"

    all_bills = await db.list_recurring_bills()
    assert len(all_bills) == 2


@pytest.mark.asyncio
async def test_csv_export(db: DatabaseManager):
    await db.insert_expense(25.0, "Food & Dining", "Lunch", "2026-08-24 12:00:00")
    csv_out = await db.generate_csv_data("2026-08-01")
    assert "Expense ID,Date,Category,Amount (MYR),Note" in csv_out
    assert "Food & Dining,25.00,Lunch" in csv_out


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

    res = await db.complete_task_by_id(tid1, "2026-08-25 10:00:00")
    assert res is not None
    assert res["status"] == "DONE"

    batch_res = await db.complete_tasks_by_ids([tid2, tid3], "2026-08-25 11:00:00")
    assert len(batch_res) == 2

    remaining = await db.get_open_tasks()
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_multi_phase_tasks(db: DatabaseManager):
    parent_id, subtasks = await db.insert_task_with_phases(
        description="Launch Mobile App",
        priority="HIGH",
        phases=["Wireframes", "Frontend UI", "Backend API"],
        due_date="2026-08-30",
        due_time=None,
        created_at="2026-08-24 10:00:00",
    )

    assert parent_id == 1
    assert len(subtasks) == 3

    open_tasks = await db.get_open_tasks()
    assert len(open_tasks) == 4

    phase1_id = subtasks[0]["id"]
    res1 = await db.complete_task_by_id(phase1_id, "2026-08-25 10:00:00")
    assert res1["status"] == "DONE"

    open_tasks_after = await db.get_open_tasks()
    assert len(open_tasks_after) == 3

    await db.complete_task_by_id(parent_id, "2026-08-26 10:00:00")
    remaining = await db.get_open_tasks()
    assert len(remaining) == 0
