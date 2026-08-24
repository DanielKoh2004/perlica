import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, patch
from pydantic import ValidationError

from src.extractor import (
    build_system_prompt,
    ExtractedPayload,
    ExpenseItem,
    TaskItem,
    ExpenseCategory,
    TaskPriority,
    QueryScope,
    ExtractionEngine,
)


def test_build_system_prompt_temporal_anchors():
    tz = ZoneInfo("Asia/Kuala_Lumpur")
    # Monday 10:00 PM (22:00)
    now_local = datetime(2026, 8, 24, 22, 0, 0, tzinfo=tz)

    open_tasks = [
        {"id": 1, "description": "Call client A", "priority": "HIGH"},
        {"id": 2, "description": "Call client B", "priority": "MEDIUM"},
    ]

    prompt = build_system_prompt(now_local, open_tasks)

    # Verify temporal anchors resolve to exact local dates
    assert "2026-08-24 22:00:00" in prompt
    assert "TODAY is: 2026-08-24 (Monday)" in prompt
    assert "TOMORROW is: 2026-08-25 (Tuesday)" in prompt
    assert "YESTERDAY is: 2026-08-23 (Sunday)" in prompt
    assert "[ID: 1] Call client A (Priority: HIGH)" in prompt
    assert "[ID: 2] Call client B (Priority: MEDIUM)" in prompt


def test_extracted_payload_casual_defaults():
    # Verify that an empty payload initializes safely without validation errors
    payload = ExtractedPayload()
    assert payload.expenses == []
    assert payload.new_tasks == []
    assert payload.completed_task_ids == []
    assert payload.ambiguous_task_note is None
    assert payload.query is None
    assert payload.conversational_reply is None


def test_extracted_payload_compound_valid():
    payload = ExtractedPayload(
        expenses=[
            ExpenseItem(amount=15.0, category=ExpenseCategory.FOOD, note="Lunch chicken rice")
        ],
        new_tasks=[
            TaskItem(description="Submit invoice", priority=TaskPriority.HIGH, due_date="2026-08-25")
        ],
        completed_task_ids=[1],
    )
    assert len(payload.expenses) == 1
    assert payload.expenses[0].amount == 15.0
    assert payload.expenses[0].category == ExpenseCategory.FOOD
    assert len(payload.new_tasks) == 1
    assert payload.new_tasks[0].due_date == "2026-08-25"
    assert payload.completed_task_ids == [1]


def test_extracted_payload_ambiguity_note():
    payload = ExtractedPayload(
        ambiguous_task_note="You have 2 call tasks: #1 Call client A and #2 Call client B. Which one did you complete?"
    )
    assert payload.ambiguous_task_note is not None
    assert len(payload.completed_task_ids) == 0


def test_extracted_payload_query_scope():
    payload = ExtractedPayload(
        query=QueryScope(query_target="EXPENSES", timeframe="TODAY")
    )
    assert payload.query is not None
    assert payload.query.query_target == "EXPENSES"
    assert payload.query.timeframe == "TODAY"


@pytest.mark.asyncio
async def test_extractor_error_resilience():
    """Verify that ExtractionEngine catches API/Validation errors and returns safe fallback."""
    engine = ExtractionEngine(api_key="fake-key")

    mock_client = AsyncMock()
    mock_client.chat.completions.create.side_effect = Exception("Groq API Timeout")

    with patch.object(engine, "_get_client", return_value=mock_client):
        payload = await engine.extract_information("Some text", datetime.now(), [])
        assert isinstance(payload, ExtractedPayload)
        assert payload.conversational_reply is not None
        assert "trouble" in payload.conversational_reply.lower()
        assert len(payload.expenses) == 0
        assert len(payload.new_tasks) == 0
