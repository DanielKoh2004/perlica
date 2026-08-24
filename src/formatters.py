from typing import List, Dict, Any, Optional
import discord

from src.extractor import ExtractedPayload, QueryScope


def format_action_confirmation(
    payload: ExtractedPayload,
    inserted_expenses: List[Dict[str, Any]],
    inserted_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
) -> discord.Embed:
    """Build a rich confirmation embed for logged actions."""
    embed = discord.Embed(
        title="⚡ Action Processed",
        color=discord.Color.green(),
    )

    # 1. Logged Expenses
    if inserted_expenses:
        total = sum(e["amount"] for e in inserted_expenses)
        lines = []
        for exp in inserted_expenses:
            note_str = f" ({exp['note']})" if exp.get("note") else ""
            lines.append(f"• **RM {exp['amount']:.2f}** — `{exp['category']}`{note_str}")
        embed.add_field(
            name=f"💸 Logged Expenses (Total: RM {total:.2f})",
            value="\n".join(lines),
            inline=False,
        )

    # 2. New Tasks
    if inserted_tasks:
        lines = []
        for t in inserted_tasks:
            due_parts = []
            if t.get("due_date"):
                due_parts.append(t["due_date"])
            if t.get("due_time"):
                due_parts.append(t["due_time"])
            due_str = f" _(Due: {' '.join(due_parts)})_" if due_parts else ""
            lines.append(f"• `[{t['priority']}]` {t['description']}{due_str} `[ID: #{t['id']}]`")
        embed.add_field(
            name="📝 New Tasks Added",
            value="\n".join(lines),
            inline=False,
        )

    # 3. Completed Tasks
    if completed_tasks:
        lines = [f"• ~~`[#{t['id']}]` {t['description']}~~" for t in completed_tasks]
        embed.add_field(
            name="✅ Completed Tasks",
            value="\n".join(lines),
            inline=False,
        )

    # 4. Ambiguity / Clarification
    if payload.ambiguous_task_note:
        embed.add_field(
            name="⚠️ Clarification Required",
            value=payload.ambiguous_task_note,
            inline=False,
        )

    # 5. Conversational Reply fallback (if any additional note)
    if payload.conversational_reply and not inserted_expenses and not inserted_tasks and not completed_tasks and not payload.ambiguous_task_note:
        embed.description = payload.conversational_reply

    return embed


def format_daily_summary(
    expenses: List[Dict[str, Any]],
    total_spent: float,
    open_tasks: List[Dict[str, Any]],
    date_str: str,
) -> discord.Embed:
    """Build a rich embed for the automated daily summary."""
    embed = discord.Embed(
        title=f"📊 Daily Summary — {date_str}",
        color=discord.Color.gold(),
    )

    # Spending Section
    if expenses:
        # Category aggregation
        cat_map: Dict[str, float] = {}
        for exp in expenses:
            cat = exp["category"]
            cat_map[cat] = round(cat_map.get(cat, 0.0) + exp["amount"], 2)
        
        breakdown = [f"• **{cat}:** RM {amt:.2f}" for cat, amt in cat_map.items()]
        embed.add_field(
            name=f"💸 Total Spent: RM {total_spent:.2f}",
            value="\n".join(breakdown),
            inline=False,
        )
    else:
        embed.add_field(
            name="💸 Total Spent: RM 0.00",
            value="No expenses recorded today.",
            inline=False,
        )

    # Tasks Section
    if open_tasks:
        lines = []
        for t in open_tasks:
            due_str = f" _(Due: {t['due_date']})_" if t.get("due_date") else ""
            lines.append(f"• `[#{t['id']} | {t['priority']}]` {t['description']}{due_str}")
        embed.add_field(
            name=f"📋 Remaining Open Tasks ({len(open_tasks)})",
            value="\n".join(lines[:20]) + ("\n...and more" if len(open_tasks) > 20 else ""),
            inline=False,
        )
    else:
        embed.add_field(
            name="📋 Open Tasks",
            value="🎉 All tasks completed! Great job!",
            inline=False,
        )

    return embed


def format_query_results(
    query: QueryScope,
    expenses: Optional[List[Dict[str, Any]]] = None,
    total_spent: Optional[float] = None,
    category_breakdown: Optional[Dict[str, float]] = None,
    tasks: Optional[List[Dict[str, Any]]] = None,
) -> discord.Embed:
    """Format on-demand query results."""
    embed = discord.Embed(
        title=f"🔍 Query Results ({query.query_target} - {query.timeframe})",
        color=discord.Color.blurple(),
    )

    if query.query_target in ("EXPENSES", "SUMMARY") and expenses is not None:
        if expenses:
            cat_lines = [f"• **{k}:** RM {v:.2f}" for k, v in (category_breakdown or {}).items()]
            embed.add_field(
                name=f"💸 Spending Total: RM {(total_spent or 0.0):.2f}",
                value="\n".join(cat_lines) if cat_lines else "No categorized expenses.",
                inline=False,
            )
        else:
            embed.add_field(
                name="💸 Expenses",
                value="No expenses found for this timeframe.",
                inline=False,
            )

    if query.query_target in ("TASKS", "SUMMARY") and tasks is not None:
        if tasks:
            lines = [f"• `[#{t['id']} | {t['priority']}]` {t['description']}" for t in tasks]
            embed.add_field(
                name=f"📋 Active Tasks ({len(tasks)})",
                value="\n".join(lines[:20]),
                inline=False,
            )
        else:
            embed.add_field(
                name="📋 Tasks",
                value="No open tasks found.",
                inline=False,
            )

    return embed
