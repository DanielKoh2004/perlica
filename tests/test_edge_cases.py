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
    render_sparkline,
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


@pytest.mark.asyncio
async def test_productivity_streak_and_spending_pace(db: DatabaseManager):
    """Test streak calculations and monospaced sparklines."""
    # Day 1: 2026-08-23
    await db.insert_expense(20.0, "Food & Dining", "Lunch", "2026-08-23 12:00:00")
    # Day 2: 2026-08-24
    await db.insert_expense(35.0, "Transport", "Grab", "2026-08-24 15:00:00")
    # Day 3: 2026-08-25
    await db.insert_expense(50.0, "Entertainment", "Movie", "2026-08-25 19:00:00")

    streak = await db.get_productivity_streak("2026-08-25")
    assert streak["streak_days"] == 3

    pace = await db.get_spending_pace("2026-08-25")
    assert pace["today_spend"] == 50.0
    assert len(pace["daily_series"]) == 7

    sparkline = render_sparkline(pace["daily_series"])
    assert sparkline.startswith("`[") and sparkline.endswith("]`")


@pytest.mark.asyncio
async def test_upcoming_bills_3_day_warning(db: DatabaseManager):
    """Test 3-day upcoming recurring bill detection."""
    # Current date: 2026-08-25
    # Bill due on 27th (in 2 days)
    await db.add_recurring_bill("S&P 500", 100.0, "Investments & Savings", 27)
    # Bill due on 28th (in 3 days)
    await db.add_recurring_bill("Internet", 139.0, "Utilities & Bills", 28)
    # Bill due on 1st (far away)
    await db.add_recurring_bill("Rent", 1500.0, "Utilities & Bills", 1)

    upcoming = await db.get_upcoming_recurring_bills(date(2026, 8, 25), days_ahead=3)
    assert len(upcoming) == 2
    assert upcoming[0]["name"] == "S&P 500"
    assert upcoming[0]["due_in_days"] == 2
    assert upcoming[1]["name"] == "Internet"
    assert upcoming[1]["due_in_days"] == 3


@pytest.mark.asyncio
async def test_dropdown_menu_25_option_cap_safeguard():
    """Verify TaskSelectMenu strictly caps to top 25 options when 30 tasks exist."""
    from src.bot import TaskSelectMenu

    tasks_30 = [
        {"id": i, "description": f"Task #{i}", "priority": "HIGH" if i < 5 else "MEDIUM", "due_date": None}
        for i in range(1, 31)
    ]
    menu = TaskSelectMenu(tasks_30)
    assert len(menu.options) == 25
    assert menu.max_values == 25


def test_category_synonym_fuzzy_resolver():
    """Verify resolve_category_from_text handles modal inputs and Malaysian slang."""
    from src.extractor import resolve_category_from_text, ExpenseCategory

    assert resolve_category_from_text("makan") == ExpenseCategory.FOOD
    assert resolve_category_from_text("Dinner with team") == ExpenseCategory.FOOD
    assert resolve_category_from_text("TNG topup") == ExpenseCategory.TRANSPORT
    assert resolve_category_from_text("petrol") == ExpenseCategory.TRANSPORT
    assert resolve_category_from_text("99 speedmart") == ExpenseCategory.GROCERIES
    assert resolve_category_from_text("unifi wifi") == ExpenseCategory.UTILITIES
    assert resolve_category_from_text("steam battlepass") == ExpenseCategory.ENTERTAINMENT
    assert resolve_category_from_text("shopee haul") == ExpenseCategory.SHOPPING
    assert resolve_category_from_text("clinic doctor visit") == ExpenseCategory.HEALTH
    assert resolve_category_from_text("s&p 500 etf") == ExpenseCategory.INVESTMENT
    assert resolve_category_from_text("random unidentified item") == ExpenseCategory.OTHER


@pytest.mark.asyncio
async def test_safe_daily_allowance_zero_division_and_overspend_guards(db: DatabaseManager):
    """
    Test safe-to-spend allowance:
    1. Middle of month (15 days left)
    2. Last day of month (August 31 -> days_remaining must be 1, NEVER 0)
    3. Overspend condition (budget exceeded -> safe allowance must be 0.0 with overspent alert)
    """
    await db.set_budget("Food & Dining", 600.0)

    # 1. Mid-month (Aug 15 -> 17 days left including today: 31 - 15 + 1 = 17)
    await db.insert_expense(260.0, "Food & Dining", "Meals", "2026-08-15 12:00:00")
    mid_month_dt = datetime(2026, 8, 15, 8, 30, 0)
    allowance_mid = await db.get_safe_daily_allowance(mid_month_dt)
    assert allowance_mid["has_budget"] is True
    assert allowance_mid["days_remaining"] == 17
    assert allowance_mid["remaining_budget"] == 340.0
    assert allowance_mid["safe_daily_allowance"] == round(340.0 / 17, 2)
    assert allowance_mid["is_overspent"] is False

    # 2. Last day of month (Aug 31 -> days_remaining must be 1, NO ZeroDivisionError!)
    last_day_dt = datetime(2026, 8, 31, 8, 30, 0)
    allowance_last = await db.get_safe_daily_allowance(last_day_dt)
    assert allowance_last["days_remaining"] == 1
    assert allowance_last["safe_daily_allowance"] == 340.0

    # 3. Overspend condition (Spend additional 400 -> total 660 on 600 budget)
    await db.insert_expense(400.0, "Food & Dining", "Fancy Dinner", "2026-08-31 20:00:00")
    allowance_over = await db.get_safe_daily_allowance(last_day_dt)
    assert allowance_over["is_overspent"] is True
    assert allowance_over["overspent_by"] == 60.0
    assert allowance_over["safe_daily_allowance"] == 0.0


@pytest.mark.asyncio
async def test_task_snooze_and_reschedule(db: DatabaseManager):
    """Test snoozing task forward by 1 day and custom days across month boundaries."""
    tid = await db.insert_task("Submit Tax Return", priority="HIGH", due_date="2026-08-31", created_at="2026-08-25 10:00:00")
    
    # Snooze +1 day -> moves from 2026-08-31 to 2026-09-01 (handles month rollover)
    snoozed_1 = await db.snooze_task(tid, days_to_add=1)
    assert snoozed_1 is not None
    assert snoozed_1["due_date"] == "2026-09-01"

    # Snooze +5 days -> moves to 2026-09-06
    snoozed_5 = await db.snooze_task(tid, days_to_add=5)
    assert snoozed_5["due_date"] == "2026-09-06"


@pytest.mark.asyncio
async def test_category_proportion_ascii_heatmap(db: DatabaseManager):
    """Test category spending proportions and ASCII proportion heatmap rendering."""
    from src.formatters import render_category_heatmap

    await db.insert_expense(50.0, "Food & Dining", "Lunch", "2026-08-25 12:00:00")
    await db.insert_expense(30.0, "Transport", "Grab", "2026-08-25 14:00:00")
    await db.insert_expense(20.0, "Entertainment", "Steam Game", "2026-08-25 16:00:00")

    proportions = await db.get_category_proportions("2026-08-25", "2026-08-25")
    assert len(proportions) == 3
    assert proportions[0]["category"] == "Food & Dining"
    assert proportions[0]["percentage"] == 50.0
    assert proportions[1]["category"] == "Transport"
    assert proportions[1]["percentage"] == 30.0
    assert proportions[2]["category"] == "Entertainment"
    assert proportions[2]["percentage"] == 20.0

    heatmap = render_category_heatmap(proportions)
    assert "Food & Dining" in heatmap
    assert "50.0%" in heatmap
    assert "`[" in heatmap and "]`" in heatmap


@pytest.mark.asyncio
async def test_savings_goals_crud_and_isolation_from_expenses(db: DatabaseManager):
    """
    Verify Savings Goals:
    1. Creation of target goal (e.g. Japan Trip - RM 6,000)
    2. Savings deposits increase balance
    3. Normal expenses DO NOT deduct from goal balance!
    4. Goal completion flag triggers when target is reached
    """
    # 1. Create Goal
    gid = await db.create_goal(name="Japan Trip", target_amount=6000.0, target_date="2027-04-01", created_at="2026-08-25 10:00:00")
    assert gid > 0

    goals = await db.get_active_goals()
    assert len(goals) == 1
    assert goals[0]["name"] == "Japan Trip"
    assert goals[0]["target_amount"] == 6000.0
    assert goals[0]["current_amount"] == 0.0
    assert goals[0]["remaining"] == 6000.0

    # 2. Deposit RM 500 to Goal
    updated = await db.deposit_to_goal(gid, 500.0)
    assert updated["current_amount"] == 500.0
    assert updated["remaining"] == 5500.0
    assert updated["percentage"] == round(500.0 / 6000.0 * 100, 1)

    # 3. Log an everyday expense (RM 50 Food & Dining)
    await db.insert_expense(50.0, "Food & Dining", "Sushi Lunch", "2026-08-25 12:00:00")

    # Goal balance must remain strictly intact at RM 500.00!
    goal_check = await db.get_goal_by_id(gid)
    assert goal_check["current_amount"] == 500.0

    # 4. Deposit remaining balance to reach target
    final_deposit = await db.deposit_to_goal(gid, 5500.0)
    assert final_deposit["current_amount"] == 6000.0
    assert final_deposit["is_completed"] == 1

    # Active goals query should now return empty since it's completed
    active = await db.get_active_goals()
    assert len(active) == 0


@pytest.mark.asyncio
async def test_atomic_rollback_and_quick_undo(db: DatabaseManager):
    """Verify deterministic rollback of expenses, tasks, and goal deposits by primary keys."""
    # Insert 2 expenses
    e1 = await db.insert_expense(25.0, "Transport", "Grab", "2026-08-25 10:00:00")
    e2 = await db.insert_expense(15.0, "Food & Dining", "Kopi", "2026-08-25 10:05:00")

    # Insert 1 parent task with subphases
    pid, subtasks = await db.insert_task_with_phases(
        description="Project Alpha",
        priority="HIGH",
        phases=["P1", "P2"],
        due_date="2026-08-30",
        due_time=None,
        created_at="2026-08-25 10:00:00",
    )

    # Deposit to goal
    gid = await db.create_goal("MacBook", 8000.0, created_at="2026-08-25 10:00:00")
    await db.deposit_to_goal(gid, 1000.0)

    # Execute atomic rollback
    del_exp_count = await db.delete_expenses_by_ids([e1, e2])
    assert del_exp_count == 2
    assert await db.get_expense_by_id(e1) is None
    assert await db.get_expense_by_id(e2) is None

    del_task_count = await db.delete_tasks_by_ids([pid])
    assert del_task_count >= 1
    assert await db.get_task_by_id(pid) is None

    reverted_goal = await db.revert_goal_deposit(gid, 1000.0)
    assert reverted_goal["current_amount"] == 0.0


@pytest.mark.asyncio
async def test_productivity_rank_tiers(db: DatabaseManager):
    """Verify progression through productivity rank tiers."""
    # Rank with 0 streak -> Apprentice
    rank0 = await db.get_productivity_rank("2026-08-25")
    assert rank0["level"] == 0
    assert "Apprentice" in rank0["title"]

    # 5-day streak -> Budget Strategist
    for i in range(5):
        d_str = f"2026-08-{21+i:02d}"
        await db.insert_expense(10.0, "Food & Dining", "Meal", f"{d_str} 12:00:00")

    rank5 = await db.get_productivity_rank("2026-08-25")
    assert rank5["streak_days"] == 5
    assert rank5["level"] >= 2
    assert "Strategist" in rank5["title"]


@pytest.mark.asyncio
async def test_keyword_search_across_expenses_and_tasks(db: DatabaseManager):
    """Verify search_records searches across expense notes, categories, and task descriptions."""
    await db.insert_expense(45.0, "Transport", "Grab ride to KL Sentral", "2026-08-25 12:00:00")
    await db.insert_expense(12.0, "Food & Dining", "GrabFood delivery", "2026-08-25 13:00:00")
    await db.insert_task("Book Grab for airport transfer", created_at="2026-08-25 10:00:00")

    results = await db.search_records("Grab")
    assert results["keyword"] == "Grab"
    assert len(results["expenses"]) == 2
    assert results["total_spent_on_matches"] == 57.0
    assert len(results["tasks"]) == 1
    assert "airport transfer" in results["tasks"][0]["description"]


def test_html_report_generator():
    """Verify generate_html_report outputs valid, styled HTML content."""
    from src.formatters import generate_html_report

    html = generate_html_report(
        month_str="2026-08",
        expenses=[{"id": 1, "created_at": "2026-08-25 12:00:00", "category": "Food & Dining", "amount": 25.0, "note": "Lunch"}],
        total_spent=25.0,
        proportions=[{"category": "Food & Dining", "amount": 25.0, "percentage": 100.0}],
        budget_status=[{"category": "Food & Dining", "spent": 25.0, "limit": 800.0, "percentage": 3.1}],
        open_tasks=[],
        completed_tasks=[{"id": 1, "description": "Finished proposal"}],
        goals=[{"name": "Japan Trip", "current_amount": 500.0, "target_amount": 6000.0, "percentage": 8.3}],
        streak_info={"streak_days": 7, "completed_this_week": 5},
        rank_info={"level": 2, "title": "Budget Strategist 🥈"},
    )

    assert "<!DOCTYPE html>" in html
    assert "Perlica Executive Report" in html
    assert "2026-08" in html
    assert "Budget Strategist" in html
    assert "Japan Trip" in html
    assert "RM 25.00" in html



