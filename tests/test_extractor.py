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
    assert "Touch 'n Go" in prompt
    assert "99 Speedmart" in prompt
    assert "Mamak" in prompt


def test_extracted_payload_budget_and_csv():
    payload = ExtractedPayload(
        set_budget_category="Food & Dining",
        set_budget_amount=800.0,
        export_csv=True,
    )
    assert payload.set_budget_category == "Food & Dining"
    assert payload.set_budget_amount == 800.0
    assert payload.export_csv is True


def test_extracted_payload_recurring_bill():
    payload = ExtractedPayload(
        add_bill_name="Unifi",
        add_bill_amount=139.0,
        add_bill_category=ExpenseCategory.UTILITIES,
        add_bill_day=1,
    )
    assert payload.add_bill_name == "Unifi"
    assert payload.add_bill_amount == 139.0
    assert payload.add_bill_day == 1


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
