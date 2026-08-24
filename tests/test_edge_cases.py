import pytest
import pytest_asyncio
from datetime import datetime, date
from zoneinfo import ZoneInfo

from src.database import DatabaseManager
from src.extractor import (
    build_system_prompt,
    ExtractedPayload,
    ExpenseItem,
    TaskItem,
    ExpenseCategory,
    TaskPriority,
)
from src.formatters import (
    render_progress_bar,
    format_action_preview,
    format_action_confirmation,
    format_morning_briefing,
    format_daily_summary,
    format_full_snapshot_summary,
    format_budget_overview,
)


@pytest_asyncio.fixture
async def db(tmp_path):
    db_file = str(tmp_path / "test_edge_cases.db")
    manager = DatabaseManager(db_file)
    await manager.init_db()
    return manager


@pytest.mark.asyncio
async def test_recurring_bill_crud_edge_cases(db: DatabaseManager):
    """Test creating, updating, listing, and deleting recurring bills with edge days."""
    # 1. Add recurring bills
    b1 = await db.add_recurring_bill("S&P 500 DCA", 100.0, "Investments & Savings", 27)
    b2 = await db.add_recurring_bill("Rent", 1500.0, "Utilities & Bills", 31)
    b3 = await db.add_recurring_bill("Gym", 120.0, "Health & Personal", 30)

    assert b1 == 1
    assert b2 == 2
    assert b3 == 3

    # 2. Test month-end clipping on Feb 28
    feb_bills = await db.get_due_recurring_bills(date(2026, 2, 28))
    feb_names = [b["name"] for b in feb_bills]
    assert "Rent" in feb_names
    assert "Gym" in feb_names
    assert "S&P 500 DCA" not in feb_names

    # 3. Update S&P 500 bill amount to 400
    updated_b1 = await db.update_recurring_bill(b1, amount=400.0)
    assert updated_b1 is not None
    assert updated_b1["amount"] == 400.0
    assert updated_b1["name"] == "S&P 500 DCA"

    # 4. Delete Rent bill
    deleted_b2 = await db.delete_recurring_bill(b2)
    assert deleted_b2 is not None
    assert deleted_b2["id"] == b2

    # 5. List active bills
    active_bills = await db.list_recurring_bills()
    assert len(active_bills) == 2
    assert active_bills[0]["name"] == "S&P 500 DCA"
    assert active_bills[1]["name"] == "Gym"


@pytest.mark.asyncio
async def test_compound_preview_formatting():
    """Verify preview embed properly formats compound actions with expenses, tasks, budgets, and bills."""
    payload = ExtractedPayload(
        add_bill_name="S&P500",
        add_bill_amount=100.0,
        add_bill_category=ExpenseCategory.INVESTMENT,
        add_bill_day=27,
        set_budget_category="Investments & Savings",
        set_budget_amount=500.0,
    )
    expenses = [{"amount": 15.50, "category": "Food & Dining", "note": "Chicken rice"}]
    tasks = [{"description": "Launch App", "priority": "HIGH", "due_date": "2026-08-30", "due_time": "17:00", "phases": ["Phase 1", "Phase 2"]}]
    completed = [5]

    embed = format_action_preview(payload, expenses, tasks, completed)
    assert embed.title == "📋 Action Ingestion Preview"
    
    field_names = [f.name for f in embed.fields]
    assert any("Expenses to Log" in name for name in field_names)
    assert any("Tasks to Create" in name for name in field_names)
    assert any("Recurring Bill to Add" in name for name in field_names)
    assert any("Monthly Budget to Set" in name for name in field_names)
    assert any("Tasks to Complete" in name for name in field_names)


@pytest.mark.asyncio
async def test_budget_overspend_edge_cases(db: DatabaseManager):
    """Test 0%, 50%, 80% (warning), 100%, and 120% (overspent) thresholds."""
    await db.set_budget("Food & Dining", 100.0)

    # 0% spent
    status_0 = await db.get_budget_status("2026-08")
    assert status_0[0]["spent"] == 0.0
    assert status_0[0]["percentage"] == 0.0
    assert status_0[0]["is_warning"] is False
    assert status_0[0]["is_overspent"] is False

    # 85% spent (warning)
    await db.insert_expense(85.0, "Food & Dining", "Buffet", "2026-08-15 12:00:00")
    status_85 = await db.get_budget_status("2026-08")
    assert status_85[0]["percentage"] == 85.0
    assert status_85[0]["is_warning"] is True
    assert status_85[0]["is_overspent"] is False

    # 120% spent (overspent)
    await db.insert_expense(35.0, "Food & Dining", "Dinner", "2026-08-16 19:00:00")
    status_120 = await db.get_budget_status("2026-08")
    assert status_120[0]["percentage"] == 120.0
    assert status_120[0]["is_warning"] is False
    assert status_120[0]["is_overspent"] is True


@pytest.mark.asyncio
async def test_multi_phase_hierarchy_completions(db: DatabaseManager):
    """Test parent completion auto-resolves child phases, and single sub-phase completion."""
    parent_id, subtasks = await db.insert_task_with_phases(
        description="Write Research Paper",
        priority="HIGH",
        phases=["Literature Review", "Methodology", "Experiments", "Drafting"],
        due_date="2026-09-15",
        due_time=None,
        created_at="2026-08-25 10:00:00",
    )

    assert parent_id == 1
    assert len(subtasks) == 4

    # 1. Complete subtask 1
    await db.complete_task_by_id(subtasks[0]["id"], "2026-08-25 11:00:00")
    open_tasks = await db.get_open_tasks()
    assert len(open_tasks) == 4  # Parent + 3 remaining phases

    # 2. Complete remaining subtasks one by one -> Parent auto-completes when last child completes!
    await db.complete_task_by_id(subtasks[1]["id"], "2026-08-25 12:00:00")
    await db.complete_task_by_id(subtasks[2]["id"], "2026-08-25 13:00:00")
    await db.complete_task_by_id(subtasks[3]["id"], "2026-08-25 14:00:00")

    open_after_all_phases = await db.get_open_tasks()
    assert len(open_after_all_phases) == 0  # Parent automatically marked DONE
