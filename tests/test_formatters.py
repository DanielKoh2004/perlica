import discord
from src.extractor import ExtractedPayload, QueryScope
from src.formatters import (
    render_progress_bar,
    format_action_preview,
    format_action_confirmation,
    format_daily_summary,
    format_morning_briefing,
    format_full_snapshot_summary,
    format_budget_overview,
    format_query_results,
    format_help_guide,
)


def test_render_progress_bar():
    bar1 = render_progress_bar(50.0, 100.0)
    assert "50.0%" in bar1
    assert "RM 50.00 / RM 100.00" in bar1

    bar_warn = render_progress_bar(85.0, 100.0)
    assert "⚠️" in bar_warn

    bar_over = render_progress_bar(120.0, 100.0)
    assert "OVERSPENT" in bar_over


def test_format_morning_briefing():
    open_tasks = [
        {"id": 1, "description": "High priority task", "priority": "HIGH", "due_date": "2026-08-25"},
        {"id": 2, "description": "Regular task", "priority": "MEDIUM"},
    ]
    due_bills = [
        {"name": "Unifi", "amount": 139.0, "category": "Utilities & Bills"}
    ]
    budget_status = [
        {"category": "Food & Dining", "spent": 400.0, "limit": 500.0, "percentage": 80.0, "remaining": 100.0}
    ]

    embed = format_morning_briefing(open_tasks, due_bills, budget_status, "2026-08-25")
    assert isinstance(embed, discord.Embed)
    assert "Morning Briefing" in embed.title
    field_names = [f.name for f in embed.fields]
    assert any("Tasks to Tackle" in name for name in field_names)
    assert any("Recurring Bills Due" in name for name in field_names)
    assert any("Monthly Budget Overview" in name for name in field_names)


def test_format_budget_overview():
    budget_status = [
        {"category": "Food & Dining", "spent": 400.0, "limit": 500.0, "percentage": 80.0, "remaining": 100.0}
    ]
    embed = format_budget_overview(budget_status)
    assert isinstance(embed, discord.Embed)
    assert "Monthly Budget Overview" in embed.title
    assert "Food & Dining" in embed.description


def test_format_action_preview():
    payload = ExtractedPayload()
    expenses = [{"amount": 15.50, "category": "Food & Dining", "note": "Chicken rice"}]
    tasks = [{"description": "Launch App", "priority": "HIGH", "due_date": "2026-08-30", "due_time": "17:00", "phases": ["Phase 1", "Phase 2"]}]
    completed = [1]

    embed = format_action_preview(payload, expenses, tasks, completed)
    assert isinstance(embed, discord.Embed)
    assert "Action Ingestion Preview" in embed.title


def test_format_action_confirmation():
    payload = ExtractedPayload(ambiguous_task_note="Ambiguity note test")
    inserted_expenses = [
        {"id": 1, "amount": 15.50, "category": "Food & Dining", "note": "Chicken rice"}
    ]
    inserted_tasks = [
        {"id": 1, "description": "Call client", "priority": "HIGH", "due_date": "2026-08-25", "due_time": "17:00"}
    ]
    completed_tasks = [
        {"id": 2, "description": "Buy groceries"}
    ]

    embed = format_action_confirmation(
        payload=payload,
        inserted_expenses=inserted_expenses,
        inserted_tasks=inserted_tasks,
        completed_tasks=completed_tasks,
        budget_alerts=["⚠️ Food is at 85% of limit!"],
    )
    assert isinstance(embed, discord.Embed)
    assert embed.title == "⚡ Action Processed"
    field_names = [f.name for f in embed.fields]
    assert any("Budget Alert" in name for name in field_names)


def test_sanitize_discord_response_markdown_and_copilot_embed():
    from src.formatters import sanitize_discord_response_markdown, format_copilot_answer_embed

    raw_table_and_html = (
        "Here is the breakdown:<br><br>"
        "| Step | Action | Details |<br>"
        "|---|---|---|<br>"
        "| 1. Ingestion | Tree crawler | Fetches Git blobs |<br>"
        "| 2. Vectorization | FastEmbed | Local ONNX vectors |<br>"
        "<b>Summary complete.</b>"
    )
    cleaned = sanitize_discord_response_markdown(raw_table_and_html)
    assert "<br>" not in cleaned
    assert "<b>" not in cleaned
    assert "• **1. Ingestion**: Tree crawler — Fetches Git blobs" in cleaned
    assert "• **2. Vectorization**: FastEmbed — Local ONNX vectors" in cleaned

    answer_data = {
        "status": "SUCCESS",
        "query": "How does ingestion work?",
        "response": raw_table_and_html,
        "citations": [{"citation": "src/github_sync.py:L10-L20", "permalink": "https://github.com/..."}],
    }
    embed = format_copilot_answer_embed(answer_data)
    assert isinstance(embed, discord.Embed)
    assert "<br>" not in embed.description
    assert len(embed.fields) == 1
    assert "Grounded Source Citations" in embed.fields[0].name
