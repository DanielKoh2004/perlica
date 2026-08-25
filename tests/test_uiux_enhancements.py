import datetime
import pytest
import pytest_asyncio
from src.database import DatabaseManager
from src.formatters import (
    format_milestone_celebration,
    format_category_filtered_view,
    format_voice_transcription_preview,
    format_bill_reminder_embed,
)
from src.bot import BillActionView, GoalsDashboardView


@pytest_asyncio.fixture
async def db(tmp_path):
    db_file = str(tmp_path / "test_uiux.db")
    manager = DatabaseManager(db_file)
    await manager.init_db()
    return manager


@pytest.mark.asyncio
async def test_milestone_spam_loop_neutralized(db: DatabaseManager):
    """
    CRITICAL TEST: Verify that hitting a milestone (e.g. 7-day streak or 50% savings goal)
    awards the badge ONCE, and subsequent checks on the same day/streak return EMPTY lists.
    """
    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")

    # 1. Create a 7-day streak
    for i in range(7):
        d_str = (now - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        await db.insert_expense(10.0, "Food & Dining", "Daily meal", f"{d_str} 12:00:00")

    # First milestone check: MUST unlock 7-Day Discipline Master
    new_milestones_1 = await db.check_new_milestones(today_str, month_str)
    assert len(new_milestones_1) == 1
    assert "7-Day Discipline Master" in new_milestones_1[0]["title"]
    assert new_milestones_1[0]["key"] == "streak_logging_7d_first"

    # Second milestone check (e.g. logging coffee 2 hours later): MUST return 0 new milestones
    new_milestones_2 = await db.check_new_milestones(today_str, month_str)
    assert len(new_milestones_2) == 0

    # Third milestone check: STILL 0 new milestones
    new_milestones_3 = await db.check_new_milestones(today_str, month_str)
    assert len(new_milestones_3) == 0


@pytest.mark.asyncio
async def test_savings_goal_milestones_deduplication(db: DatabaseManager):
    """Verify that savings goals award 50% and 100% badges exactly once."""
    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")

    gid = await db.create_goal(name="Japan Trip", target_amount=1000.0, created_at=f"{today_str} 10:00:00")

    # Deposit RM 600 (60% funded -> triggers 50% milestone)
    await db.deposit_to_goal(gid, 600.0)

    m1 = await db.check_new_milestones(today_str, month_str)
    assert len(m1) == 1
    assert "Halfway to Japan Trip" in m1[0]["title"]

    # Deposit another RM 100 (70% funded) -> should NOT re-award 50% milestone
    await db.deposit_to_goal(gid, 100.0)
    m2 = await db.check_new_milestones(today_str, month_str)
    assert len(m2) == 0

    # Deposit RM 300 (100% funded -> triggers 100% milestone)
    await db.deposit_to_goal(gid, 300.0)
    m3 = await db.check_new_milestones(today_str, month_str)
    assert len(m3) == 1
    assert "Japan Trip Fully Achieved!" in m3[0]["title"]

    # Subsequent check -> should return 0
    m4 = await db.check_new_milestones(today_str, month_str)
    assert len(m4) == 0


@pytest.mark.asyncio
async def test_category_filtered_query_and_embed(db: DatabaseManager):
    """Verify get_expenses_by_category accurately isolates category items and computes subtotal."""
    await db.insert_expense(25.0, "Food & Dining", "Lunch", "2026-08-10 12:00:00")
    await db.insert_expense(15.0, "Food & Dining", "Dinner", "2026-08-11 19:00:00")
    await db.insert_expense(50.0, "Transport", "Petrol", "2026-08-12 08:00:00")

    food_items, food_subtotal = await db.get_expenses_by_category("Food & Dining", "2026-08-01", "2026-08-31")
    assert len(food_items) == 2
    assert food_subtotal == 40.0

    embed = format_category_filtered_view("Food & Dining", food_items, food_subtotal, "2026-08")
    assert "Category Inspector: Food & Dining" in embed.title
    assert "RM 40.00" in embed.description
    assert "Lunch" in embed.fields[0].value
    assert "Dinner" in embed.fields[0].value


def test_stateless_bill_action_view_custom_ids():
    """
    CRITICAL TEST: Verify BillActionView embeds bill_id directly into custom_id
    so buttons survive bot restarts with 0 transient memory dependencies.
    """
    view = BillActionView(bill_id=42, amount=100.0, category="Investments & Savings")
    custom_ids = [child.custom_id for child in view.children]

    assert "perlica:bill:pay:42" in custom_ids
    assert "perlica:bill:custom:42" in custom_ids
    assert "perlica:bill:snooze:42" in custom_ids


def test_voice_transcription_preview_formatting():
    """Verify voice note transcription visual card renders as expected."""
    sample_text = "Bought groceries at 99 Speedmart for RM 45.50"
    embed = format_voice_transcription_preview(sample_text)
    assert "🎙️ Voice Note Transcribed" in embed.title
    assert sample_text in embed.description


def test_milestone_celebration_embed():
    """Verify milestone celebration embeds render with gold theme and badge."""
    milestone = {
        "title": "7-Day Discipline Master",
        "badge": "🔥 7-Day Streak",
        "description": "7 unbroken days of financial clarity!",
    }
    embed = format_milestone_celebration(milestone)
    assert "🎖️ 7-Day Discipline Master" in embed.title
    assert "🔥 7-Day Streak" in embed.description


def test_stateless_copilot_and_sources_views_survive_restarts():
    """
    CRITICAL INVARIANT: Verify CopilotAnswerView and SourcesDashboardView
    have timeout=None and explicit custom_ids so buttons survive infinite bot restarts.
    """
    from src.formatters import CopilotAnswerView, SourcesDashboardView

    # Copilot Answer View
    ans_view = CopilotAnswerView(answer_id=99)
    assert ans_view.timeout is None
    btn = ans_view.children[0]
    assert btn.custom_id == "perlica:copilot:raw:99"

    # Sources Dashboard View
    src_view = SourcesDashboardView()
    assert src_view.timeout is None
    btn_src = src_view.children[0]
    assert btn_src.custom_id == "perlica:sources:refresh"


def test_knowledge_ingestion_hub_embed_and_view():
    """Verify format_ingest_hub_embed produces structured options and KnowledgeIngestSessionView."""
    from src.formatters import format_ingest_hub_embed
    from src.bot import KnowledgeIngestSessionView, WebIngestModal, RepoSyncModal, QuickNoteModal

    emb = format_ingest_hub_embed()
    assert "Knowledge Base Ingestion Hub" in emb.title
    assert len(emb.fields) == 4
    assert any("PDF Document" in f.name for f in emb.fields)
    assert any("Web Page URL" in f.name for f in emb.fields)
    assert any("GitHub Repository" in f.name for f in emb.fields)
    assert any("Quick Knowledge Note" in f.name for f in emb.fields)

    view = KnowledgeIngestSessionView()
    assert len(view.children) == 4
    custom_ids = [c.custom_id for c in view.children]
    assert "perlica:ingest:pdf_session" in custom_ids
    assert "perlica:ingest:web_modal" in custom_ids
    assert "perlica:ingest:repo_modal" in custom_ids
    assert "perlica:ingest:note_modal" in custom_ids
