import pytest
import os
import json
from datetime import datetime, timedelta
from src.database import DatabaseManager
from src.goal_wizard import (
    GoalWizardState,
    normalize_iso_date,
    build_wizard_system_prompt,
    process_wizard_turn,
)
from src.formatters import (
    format_goals_overview,
    format_rich_goal_detail_embed,
    format_goal_wizard_preview_embed,
    format_goal_disambiguation_embed,
)
from src.config import settings


@pytest.fixture
async def test_db(tmp_path):
    db_file = str(tmp_path / "test_goals.db")
    manager = DatabaseManager(db_path=db_file)
    await manager.init_db()
    yield manager


@pytest.mark.asyncio
async def test_wizard_session_persistence_and_15min_expiry(test_db):
    """Verify Goal Wizard interview state is persisted in SQLite and expires after 15m."""
    user_id = 987654321
    state = {
        "user_id": user_id,
        "step": 1,
        "goal_name": "Japan Trip 2027",
        "goal_category": "Travel",
        "target_amount": 6000.0,
        "target_date": "2027-01-31",
        "milestones": [{"title": "Book flights", "estimated_cost": 1800.0}],
    }

    # 1. Save session
    await test_db.save_wizard_session(user_id, state)

    # 2. Retrieve immediately -> matches
    retrieved = await test_db.get_wizard_session(user_id, max_age_seconds=900)
    assert retrieved is not None
    assert retrieved["goal_name"] == "Japan Trip 2027"
    assert retrieved["target_amount"] == 6000.0

    # 3. Simulate expiration (max_age_seconds = 0)
    expired = await test_db.get_wizard_session(user_id, max_age_seconds=0)
    assert expired is None

    # Verify session was automatically cleaned up
    async with test_db.get_connection() as conn:
        async with conn.execute("SELECT COUNT(*) as c FROM goal_wizard_sessions WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            assert row["c"] == 0


@pytest.mark.asyncio
async def test_allocation_vs_expenditure_ledger_isolation(test_db):
    """
    Verify that goal deposits are isolated balance allocations (zero OPEX effect)
    while milestone expenditures record an expense row and link the foreign key.
    """
    now_str = "2026-08-25 12:00:00"
    # Create goal with milestone
    goal_id = await test_db.create_goal_with_milestones(
        name="Japan Trip",
        category="Travel",
        target_amount=6000.0,
        current_amount=0.0,
        target_date="2027-01-31",
        milestones=[{"title": "Book return flights", "estimated_cost": 1800.0}],
        created_at=now_str,
    )

    # 1. Sinking Fund Deposit (Asset Transfer)
    updated = await test_db.deposit_to_goal(goal_id, 500.0)
    assert updated["current_amount"] == 500.0
    assert updated["remaining"] == 5500.0

    # Verify ZERO expenses were logged
    expenses, total_spent, _ = await test_db.get_expenses_summary("2026-08-01")
    assert total_spent == 0.0
    assert len(expenses) == 0

    # 2. Milestone Cash Outflow (Real OPEX Expense)
    expense_id = await test_db.insert_expense(
        amount=1800.0,
        category="Travel",
        note="Bought Japan flights",
        created_at=now_str,
    )
    assert expense_id > 0

    # Link milestone to expense
    goal_data = await test_db.get_goal_with_milestones(goal_id)
    milestone_id = goal_data["milestones"][0]["id"]
    completed_m = await test_db.complete_milestone_with_expense(milestone_id, expense_id, completed_at=now_str)

    assert completed_m["is_completed"] == 1
    assert completed_m["expense_id"] == expense_id

    # Verify expense summary now correctly reflects the OPEX outflow
    expenses, total_spent, _ = await test_db.get_expenses_summary("2026-08-01")
    assert total_spent == 1800.0
    assert len(expenses) == 1


@pytest.mark.asyncio
async def test_deterministic_goal_resolution_and_disambiguation(test_db):
    """Verify deterministic goal matching resolves exact, single, and ambiguous collisions."""
    now_str = "2026-08-25 12:00:00"
    g1 = await test_db.create_goal(name="Japan Trip 2027", target_amount=6000.0, created_at=now_str)
    g2 = await test_db.create_goal(name="Tokyo Marathon 2027", target_amount=3000.0, created_at=now_str)
    g3 = await test_db.create_goal(name="MacBook Pro M4", target_amount=10000.0, created_at=now_str)

    # 1. Exact Match
    res_type, matched = await test_db.resolve_goal_by_name_or_query("MacBook Pro M4")
    assert res_type == "EXACT"
    assert matched["id"] == g3

    # 2. Single Confident Match
    res_type, matched = await test_db.resolve_goal_by_name_or_query("macbook")
    assert res_type == "SINGLE"
    assert matched["id"] == g3

    # 3. Ambiguous Collision (e.g. "Japan" vs "Tokyo" if query matches multiple)
    res_type, matched = await test_db.resolve_goal_by_name_or_query("2027")
    assert res_type == "AMBIGUOUS"
    assert len(matched) == 2

    # 4. No Match
    res_type, matched = await test_db.resolve_goal_by_name_or_query("Nonexistent Goal")
    assert res_type == "NONE"
    assert matched is None


def test_iso_date_normalization_and_safe_countdown():
    """Verify ISO date normalization and safe countdown formatting against ValueError."""
    # YYYY-MM-DD
    iso1, human1 = normalize_iso_date("2027-01-31", "January 2027")
    assert iso1 == "2027-01-31"

    # YYYY-MM
    iso2, human2 = normalize_iso_date("2027-01", "January 2027")
    assert iso2 == "2027-01-31"

    # Invalid / Open-ended text
    iso3, human3 = normalize_iso_date("sometime next year", "Sometime next year")
    assert iso3 is None
    assert human3 == "Sometime next year"

    # Verify formatters handle valid and invalid dates safely
    dummy_goal = {
        "id": 1,
        "name": "Japan Trip",
        "category": "Travel",
        "current_amount": 1000.0,
        "target_amount": 6000.0,
        "target_date": "2027-01-31",
        "notes": "Looking for discounts",
        "milestones": [{"sort_order": 1, "title": "Flights", "estimated_cost": 1800.0, "is_completed": 0, "expense_id": None}],
    }
    embed = format_rich_goal_detail_embed(dummy_goal)
    assert embed.title is not None
    assert "days remaining" in embed.description

    # Overdue or arbitrary string
    dummy_goal["target_date"] = "Flexible 2027"
    embed2 = format_rich_goal_detail_embed(dummy_goal)
    assert "Flexible 2027" in embed2.description


@pytest.mark.asyncio
async def test_milestone_crud_and_cascade_delete(test_db):
    """Verify creating, toggling, editing, and cascade deleting milestones."""
    now_str = "2026-08-25 12:00:00"
    goal_id = await test_db.create_goal_with_milestones(
        name="MacBook Pro",
        category="Purchase",
        target_amount=10000.0,
        current_amount=0.0,
        notes="Waiting for 12.12 sale",
        milestones=[
            {"title": "Monitor 12.12 prices", "estimated_cost": 0.0},
            {"title": "Save RM 10,000", "estimated_cost": 10000.0},
        ],
        created_at=now_str,
    )

    goal_with_m = await test_db.get_goal_with_milestones(goal_id)
    assert len(goal_with_m["milestones"]) == 2
    assert goal_with_m["milestones_progress_ratio"] == "0/2"

    # Add a custom subtask
    m3_id = await test_db.add_goal_milestone(goal_id, title="Buy AppleCare+", estimated_cost=899.0)
    assert m3_id > 0

    # Toggle subtask 1 complete
    m1_id = goal_with_m["milestones"][0]["id"]
    toggled = await test_db.toggle_goal_milestone(m1_id, is_completed=1)
    assert toggled["is_completed"] == 1
    assert toggled["completed_at"] is not None

    goal_updated = await test_db.get_goal_with_milestones(goal_id)
    assert goal_updated["milestones_progress_ratio"] == "1/3"

    # Delete subtask 3
    deleted_m = await test_db.delete_goal_milestone(m3_id)
    assert deleted_m is True

    # Cascade delete goal
    deleted_goal = await test_db.delete_goal(goal_id)
    assert deleted_goal is not None

    # Verify milestones are cascaded
    async with test_db.get_connection() as conn:
        async with conn.execute("SELECT COUNT(*) as c FROM goal_milestones WHERE goal_id = ?", (goal_id,)) as cur:
            row = await cur.fetchone()
            assert row["c"] == 0
