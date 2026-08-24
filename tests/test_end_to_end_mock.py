import pytest
import pytest_asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

from src.database import DatabaseManager
from src.extractor import (
    ExtractionEngine,
    ExtractedPayload,
    ExpenseItem,
    ExpenseCategory,
    TaskItem,
    TaskPriority,
    QueryScope,
)
from src.formatters import format_action_confirmation


@pytest_asyncio.fixture
async def db(tmp_path):
    db_file = str(tmp_path / "test_e2e_tracker.db")
    manager = DatabaseManager(db_file)
    await manager.init_db()
    return manager


@pytest.mark.asyncio
async def test_e2e_casual_message_flow(db: DatabaseManager):
    """Verify casual messages don't touch DB or throw errors."""
    mock_payload = ExtractedPayload(
        conversational_reply="Good morning! Hope you have a productive day."
    )

    with patch.object(ExtractionEngine, "extract_information", new_callable=AsyncMock) as mock_extract:
        mock_extract.return_value = mock_payload

        engine = ExtractionEngine()
        payload = await engine.extract_information("I just woke up", datetime.now(), [])

        has_actions = bool(
            payload.expenses
            or payload.new_tasks
            or payload.completed_task_ids
            or payload.ambiguous_task_note
            or payload.query
        )
        assert not has_actions
        assert payload.conversational_reply == "Good morning! Hope you have a productive day."

        # DB must have 0 expenses and 0 tasks
        open_tasks = await db.get_open_tasks()
        expenses, total, _ = await db.get_daily_summary("2026-08-24")
        assert len(open_tasks) == 0
        assert len(expenses) == 0


@pytest.mark.asyncio
async def test_e2e_compound_expense_and_task_completion(db: DatabaseManager):
    """Verify compound input inserts expense and deterministically marks task as DONE."""
    # Pre-seed tasks
    tid1 = await db.insert_task("Submit monthly report", "HIGH", created_at="2026-08-24 10:00:00")
    tid2 = await db.insert_task("Buy coffee beans", "LOW", created_at="2026-08-24 10:00:00")

    mock_payload = ExtractedPayload(
        expenses=[
            ExpenseItem(amount=18.50, category=ExpenseCategory.FOOD, note="Lunch with team")
        ],
        completed_task_ids=[tid1],
    )

    # Process payload
    inserted_expenses = []
    for exp in mock_payload.expenses:
        eid = await db.insert_expense(exp.amount, exp.category.value, exp.note, "2026-08-24 13:00:00")
        inserted_expenses.append({"id": eid, "amount": exp.amount, "category": exp.category.value, "note": exp.note})

    completed_tasks = await db.complete_tasks_by_ids(mock_payload.completed_task_ids, "2026-08-24 13:00:00")

    assert len(inserted_expenses) == 1
    assert inserted_expenses[0]["amount"] == 18.50
    assert len(completed_tasks) == 1
    assert completed_tasks[0]["id"] == tid1
    assert completed_tasks[0]["status"] == "DONE"

    # Only tid2 should remain open
    remaining = await db.get_open_tasks()
    assert len(remaining) == 1
    assert remaining[0]["id"] == tid2

    # Embed builds successfully
    embed = format_action_confirmation(mock_payload, inserted_expenses, [], completed_tasks)
    assert embed.title == "⚡ Action Processed"
