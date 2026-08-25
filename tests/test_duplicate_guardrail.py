import datetime
import pytest
import pytest_asyncio
import discord
from src.config import settings
from src.database import DatabaseManager
from src.formatters import format_action_preview
from src.extractor import ExtractedPayload, ExpenseItem, ExpenseCategory
from src.bot import ActionIngestionView


@pytest_asyncio.fixture
async def db(tmp_path):
    db_file = str(tmp_path / "test_duplicate.db")
    manager = DatabaseManager(db_file)
    await manager.init_db()
    return manager


@pytest.mark.asyncio
async def test_duplicate_collision_and_timezone_alignment(db: DatabaseManager):
    """
    CRITICAL TEST: Verify that 5-minute duplicate collision operates with
    exact timezone alignment (Asia/Kuala_Lumpur), eliminating the 8-hour UTC offset bug.
    """
    now_local = datetime.datetime(2026, 8, 25, 13, 0, 0, tzinfo=settings.tz)
    created_at_str = now_local.strftime("%Y-%m-%d %H:%M:%S")

    # 1. Insert original expense at 13:00
    eid = await db.insert_expense(
        amount=15.0,
        category="Food & Dining",
        note="Chicken rice lunch",
        created_at=created_at_str,
    )

    # 2. Check collision 2 minutes later (13:02) -> MUST MATCH!
    query_time_2m = now_local + datetime.timedelta(minutes=2)
    match_2m = await db.find_recent_similar_expense(
        amount=15.0,
        category="Food & Dining",
        window_minutes=5,
        now_local=query_time_2m,
    )
    assert match_2m is not None
    assert match_2m["id"] == eid
    assert match_2m["minutes_ago"] == 2

    # 3. Check collision 6 minutes later (13:06) -> MUST EXPIRE (None)!
    query_time_6m = now_local + datetime.timedelta(minutes=6)
    match_6m = await db.find_recent_similar_expense(
        amount=15.0,
        category="Food & Dining",
        window_minutes=5,
        now_local=query_time_6m,
    )
    assert match_6m is None

    # 4. Check different amount (RM 25.00) -> None
    diff_amt = await db.find_recent_similar_expense(
        amount=25.0,
        category="Food & Dining",
        window_minutes=5,
        now_local=query_time_2m,
    )
    assert diff_amt is None

    # 5. Check different category (Transport) -> None
    diff_cat = await db.find_recent_similar_expense(
        amount=15.0,
        category="Transport",
        window_minutes=5,
        now_local=query_time_2m,
    )
    assert diff_cat is None


def test_action_ingestion_view_duplicate_adaptation():
    """Verify ActionIngestionView adapts button labels when is_duplicate=True."""
    mock_payload = ExtractedPayload(
        expenses=[ExpenseItem(amount=15.0, category=ExpenseCategory.FOOD, note="Lunch")]
    )

    # Standard View
    normal_view = ActionIngestionView(on_confirm=lambda i: None, payload=mock_payload, is_duplicate=False)
    assert normal_view.confirm_button.label == "Confirm"
    assert normal_view.reject_button.label == "Reject"

    # Duplicate Warning View
    dup_view = ActionIngestionView(on_confirm=lambda i: None, payload=mock_payload, is_duplicate=True)
    assert dup_view.confirm_button.label == "Log Anyway"
    assert dup_view.confirm_button.emoji.name == "⚠️"
    assert dup_view.reject_button.label == "Discard Duplicate"
    assert dup_view.reject_button.emoji.name == "🗑️"


def test_format_action_preview_duplicate_warning_embed():
    """Verify format_action_preview renders amber callout when duplicate is passed."""
    mock_payload = ExtractedPayload(
        expenses=[ExpenseItem(amount=15.0, category=ExpenseCategory.FOOD, note="Lunch")]
    )
    dup_info = {
        "id": 42,
        "amount": 15.0,
        "category": "Food & Dining",
        "note": "Chicken rice",
        "minutes_ago": 2,
    }

    embed = format_action_preview(
        payload=mock_payload,
        expenses=[{"amount": 15.0, "category": "Food & Dining", "note": "Lunch"}],
        tasks=[],
        completed_task_ids=[],
        duplicate_warning=dup_info,
    )

    assert isinstance(embed, discord.Embed)
    assert "Duplicate Warning" in embed.title
    assert embed.color == discord.Color.orange()
    assert any("Potential Duplicate Detected" in f.name for f in embed.fields)
    assert "2 min ago" in embed.fields[0].value
    assert "[#42]" in embed.fields[0].value
