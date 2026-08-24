import discord
from src.extractor import ExtractedPayload, QueryScope
from src.formatters import (
    format_action_preview,
    format_action_confirmation,
    format_daily_summary,
    format_full_snapshot_summary,
    format_query_results,
    format_help_guide,
)


def test_format_action_preview():
    payload = ExtractedPayload()
    expenses = [{"amount": 15.50, "category": "Food & Dining", "note": "Chicken rice"}]
    tasks = [{"description": "Launch App", "priority": "HIGH", "due_date": "2026-08-30", "due_time": "17:00", "phases": ["Phase 1", "Phase 2"]}]
    completed = [1]

    embed = format_action_preview(payload, expenses, tasks, completed)
    assert isinstance(embed, discord.Embed)
    assert "Action Ingestion Preview" in embed.title
    field_names = [f.name for f in embed.fields]
    assert any("Expenses to Log" in name for name in field_names)
    assert any("Tasks to Create" in name for name in field_names)
    assert any("Tasks to Complete" in name for name in field_names)


def test_format_action_confirmation():
    payload = ExtractedPayload(
        ambiguous_task_note="Ambiguity note test"
    )
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
    )

    assert isinstance(embed, discord.Embed)
    assert embed.title == "⚡ Action Processed"
    field_names = [f.name for f in embed.fields]
    assert any("Logged Expenses" in name for name in field_names)
    assert any("New Tasks Added" in name for name in field_names)
    assert any("Completed Tasks" in name for name in field_names)
    assert any("Clarification Required" in name for name in field_names)


def test_format_daily_summary():
    expenses = [
        {"amount": 20.0, "category": "Food & Dining"},
        {"amount": 10.0, "category": "Transport"},
    ]
    total_spent = 30.0
    open_tasks = [
        {"id": 1, "description": "Review PR", "priority": "HIGH", "due_date": "2026-08-25"}
    ]

    embed = format_daily_summary(expenses, total_spent, open_tasks, "2026-08-24")
    assert isinstance(embed, discord.Embed)
    assert "2026-08-24" in embed.title
    field_names = [f.name for f in embed.fields]
    assert any("Total Spent: RM 30.00" in name for name in field_names)
    assert any("Remaining Open Tasks" in name for name in field_names)


def test_format_full_snapshot_summary():
    snapshot = {
        "total_spent": 50.0,
        "category_breakdown": {"Food & Dining": 30.0, "Transport": 20.0},
        "completed_tasks": [{"id": 1, "description": "Write report"}],
        "open_tasks": [{"id": 2, "description": "Send email", "priority": "HIGH", "due_date": "2026-08-25"}],
    }
    embed = format_full_snapshot_summary(snapshot, "Today — 2026-08-24", "You had a great day!")
    assert isinstance(embed, discord.Embed)
    assert "Executive Summary" in embed.title
    assert "AI Digest" in embed.description


def test_format_query_results():
    query = QueryScope(query_target="EXPENSES", timeframe="TODAY")
    expenses = [{"amount": 25.0, "category": "Food & Dining"}]
    breakdown = {"Food & Dining": 25.0}

    embed = format_query_results(
        query=query,
        expenses=expenses,
        total_spent=25.0,
        category_breakdown=breakdown,
    )
    assert isinstance(embed, discord.Embed)
    assert "EXPENSES - TODAY" in embed.title


def test_format_help_guide():
    embed = format_help_guide()
    assert isinstance(embed, discord.Embed)
    assert "Quick Start Guide" in embed.title
