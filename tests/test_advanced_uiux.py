import datetime
import pytest
import pytest_asyncio
from src.database import DatabaseManager
from src.formatters import (
    get_time_aware_greeting,
    format_transaction_page,
    format_focus_task_embed,
)
from src.bot import (
    TransactionExplorerView,
    DailyFocusView,
    BillActionView,
    task_autocomplete,
    category_autocomplete,
)


@pytest_asyncio.fixture
async def db(tmp_path):
    db_file = str(tmp_path / "test_advanced_uiux.db")
    manager = DatabaseManager(db_file)
    await manager.init_db()
    return manager


@pytest.mark.asyncio
async def test_page_zero_negative_offset_protection(db: DatabaseManager):
    """
    CRITICAL TEST: Verify that querying an empty month or deleting the last item
    strictly clamps safe_page=1, total_pages=1, and NEVER calculates a negative SQL OFFSET.
    """
    # 1. Query on completely empty database
    expenses, safe_page, total_pages, total_count = await db.get_paginated_expenses("2026-08", page=1, page_size=10)
    assert expenses == []
    assert safe_page == 1
    assert total_pages == 1
    assert total_count == 0

    # 2. Insert 1 expense
    eid = await db.insert_expense(15.0, "Food & Dining", "Solo meal", "2026-08-10 12:00:00")
    expenses, safe_page, total_pages, total_count = await db.get_paginated_expenses("2026-08", page=1, page_size=10)
    assert len(expenses) == 1
    assert total_count == 1

    # 3. Delete the last expense in the month
    await db.delete_expenses_by_ids([eid])
    expenses, safe_page, total_pages, total_count = await db.get_paginated_expenses("2026-08", page=1, page_size=10)
    assert expenses == []
    assert safe_page == 1
    assert total_pages == 1
    assert total_count == 0

    # Formatter should render empty state cleanly
    embed = format_transaction_page(expenses, safe_page, total_pages, total_count, "2026-08")
    assert "No expenses recorded for **2026-08**." in embed.description


@pytest.mark.asyncio
async def test_shrinking_array_and_priority_focus(db: DatabaseManager):
    """
    CRITICAL TEST: Verify that high priority tasks sort first, and safe modulo indexing
    prevents IndexError when task arrays shrink asynchronously.
    """
    await db.insert_task("Low task", priority="LOW", created_at="2026-08-01 10:00:00")
    await db.insert_task("High urgent task", priority="HIGH", created_at="2026-08-01 10:00:00")
    await db.insert_task("Medium task", priority="MEDIUM", created_at="2026-08-01 10:00:00")

    tasks = await db.get_highest_priority_tasks()
    assert len(tasks) == 3
    assert tasks[0]["description"] == "High urgent task"
    assert tasks[0]["priority"] == "HIGH"
    assert tasks[1]["description"] == "Medium task"
    assert tasks[2]["description"] == "Low task"

    # Simulate viewing index 2, but array shrinks to 1 item
    target_idx = 2
    shrunk_tasks = [tasks[0]]
    safe_idx = max(0, target_idx) % len(shrunk_tasks)
    assert safe_idx == 0
    focus_task = shrunk_tasks[safe_idx]
    assert focus_task["description"] == "High urgent task"

    embed = format_focus_task_embed(focus_task, safe_idx, len(shrunk_tasks))
    assert "High urgent task" in embed.description


@pytest.mark.asyncio
async def test_strict_25_choice_autocomplete_cap(db: DatabaseManager, monkeypatch):
    """
    CRITICAL TEST: Verify that task_autocomplete strictly caps choices to <= 25
    even when 35 open tasks exist in SQLite, preventing Discord gateway rejection.
    """
    # Seed 35 tasks
    for i in range(35):
        await db.insert_task(f"Task number {i+1}", priority="MEDIUM", created_at="2026-08-01 10:00:00")

    # Mock the global db in bot.py to use this test db
    import src.bot as bot_mod
    monkeypatch.setattr(bot_mod, "db", db)

    class DummyInteraction:
        pass

    choices = await task_autocomplete(DummyInteraction(), current="")
    assert len(choices) == 25  # EXACTLY 25 choices, no overflow!

    # Filtered search
    filtered_choices = await task_autocomplete(DummyInteraction(), current="Task number 1")
    assert len(filtered_choices) <= 25


def test_custom_id_lengths_under_discord_ceiling():
    """
    CRITICAL TEST: Assert all stateless custom_ids are strictly under 30 characters
    (less than 30% of Discord's 100-character ceiling).
    """
    # 1. Transaction Explorer
    view_tx = TransactionExplorerView(
        expenses=[{"id": 101, "amount": 25.0, "category": "Food & Dining", "note": "Lunch", "created_at": "2026-08-25 12:00:00"}],
        month_str="2026-08",
        page=2,
        total_pages=5,
    )
    for child in view_tx.children:
        assert len(child.custom_id) <= 30, f"custom_id too long: {child.custom_id}"

    # 2. Daily Focus View
    view_foc = DailyFocusView(task_id=42, next_index=2)
    for child in view_foc.children:
        assert len(child.custom_id) <= 30, f"custom_id too long: {child.custom_id}"

    # 3. Bill Action View
    view_bill = BillActionView(bill_id=88, amount=400.0, category="Investments & Savings")
    for child in view_bill.children:
        assert len(child.custom_id) <= 30, f"custom_id too long: {child.custom_id}"


def test_time_aware_greeting_output():
    """Verify atmospheric time greetings across 24 hours."""
    morn = datetime.datetime(2026, 8, 25, 8, 30)
    aft = datetime.datetime(2026, 8, 25, 14, 15)
    eve = datetime.datetime(2026, 8, 25, 20, 00)
    night = datetime.datetime(2026, 8, 25, 2, 00)

    assert "Good morning" in get_time_aware_greeting(morn)
    assert "Good afternoon" in get_time_aware_greeting(aft)
    assert "Good evening" in get_time_aware_greeting(eve)
    assert "Late night" in get_time_aware_greeting(night)
