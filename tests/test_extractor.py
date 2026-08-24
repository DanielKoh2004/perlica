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
    now_local = datetime(2026, 8, 24, 22, 0, 0, tzinfo=tz)

    open_tasks = [
        {"id": 1, "description": "Call client A", "priority": "HIGH"},
        {"id": 2, "description": "Call client B", "priority": "MEDIUM"},
    ]

    prompt = build_system_prompt(now_local, open_tasks)

    assert "2026-08-24 22:00:00" in prompt
    assert "TODAY is: 2026-08-24 (Monday)" in prompt
    assert "TOMORROW is: 2026-08-25 (Tuesday)" in prompt
    assert "YESTERDAY is: 2026-08-23 (Sunday)" in prompt
    assert "[ID: 1] Call client A (Priority: HIGH)" in prompt
    assert "[ID: 2] Call client B (Priority: MEDIUM)" in prompt


def test_extracted_payload_casual_defaults():
    payload = ExtractedPayload()
    assert payload.expenses == []
    assert payload.new_tasks == []
    assert payload.completed_task_ids == []
    assert payload.needs_clarification is False
    assert payload.clarification_prompt is None
    assert payload.ambiguous_task_note is None
    assert payload.query is None
    assert payload.conversational_reply is None


def test_extracted_payload_multi_phase_task():
    payload = ExtractedPayload(
        new_tasks=[
            TaskItem(
                description="App Launch",
                priority=TaskPriority.HIGH,
                phases=["Wireframes", "Frontend", "Backend"],
            )
        ]
    )
    assert len(payload.new_tasks) == 1
    assert len(payload.new_tasks[0].phases) == 3
    assert payload.new_tasks[0].phases[0] == "Wireframes"


def test_extracted_payload_clarification():
    payload = ExtractedPayload(
        needs_clarification=True,
        clarification_prompt="What did you spend RM 50 on? (e.g. Food, Groceries, Transport)",
    )
    assert payload.needs_clarification is True
    assert payload.clarification_prompt is not None


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
