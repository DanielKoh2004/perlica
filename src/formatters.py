from typing import List, Dict, Any, Optional
import discord

from src.extractor import ExtractedPayload, QueryScope


def format_action_confirmation(
    payload: ExtractedPayload,
    inserted_expenses: List[Dict[str, Any]],
    inserted_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
) -> discord.Embed:
    """Build a rich confirmation embed for logged actions with hierarchy for multi-phase tasks."""
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

    # 2. New Tasks (Parent & Phases)
    if inserted_tasks:
        lines = []
        for t in inserted_tasks:
            due_parts = []
            if t.get("due_date"):
                due_parts.append(t["due_date"])
            if t.get("due_time"):
                due_parts.append(t["due_time"])
            due_str = f" _(Due: {' '.join(due_parts)})_" if due_parts else ""

            # Check if this task has subphases
            subphases = t.get("subphases", [])
            if subphases:
                lines.append(f"• 📁 `[{t['priority']}]` **{t['description']}**{due_str} `[ID: #{t['id']}]`")
                for s in subphases:
                    lines.append(f"   └── ⏳ `{s.get('phase_name', 'Phase')}`: {s['description']} `[ID: #{s['id']}]`")
            else:
                prefix = f"   └── ⏳ `{t.get('phase_name')}`: " if t.get("parent_id") else "• "
                lines.append(f"{prefix}`[{t['priority']}]` {t['description']}{due_str} `[ID: #{t['id']}]`")

        embed.add_field(
            name="📝 New Tasks Added",
            value="\n".join(lines),
            inline=False,
        )

    # 3. Completed Tasks
    if completed_tasks:
        lines = []
        for t in completed_tasks:
            phase_str = f" `{t.get('phase_name')}` " if t.get("phase_name") else " "
            lines.append(f"• ~~`[#{t['id']}]`{phase_str}{t['description']}~~")
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

    # 5. Conversational Reply fallback
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
            prefix = "   └── ⏳ " if t.get("parent_id") else "• "
            phase_info = f"`{t['phase_name']}`: " if t.get("phase_name") else ""
            lines.append(f"{prefix}`[#{t['id']} | {t['priority']}]` {phase_info}{t['description']}{due_str}")
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


def format_full_snapshot_summary(
    snapshot: Dict[str, Any],
    timeframe_title: str,
    ai_digest: Optional[str] = None,
) -> discord.Embed:
    """Build a comprehensive on-demand summary embed with financial and productivity stats."""
    embed = discord.Embed(
        title=f"📊 Executive Summary ({timeframe_title})",
        color=discord.Color.blue(),
    )

    if ai_digest:
        embed.description = f"💡 **AI Digest:**\n{ai_digest}\n"

    # 1. Finances
    total_spent = snapshot.get("total_spent", 0.0)
    cat_breakdown = snapshot.get("category_breakdown", {})
    if cat_breakdown:
        breakdown_lines = [f"• **{k}:** RM {v:.2f}" for k, v in cat_breakdown.items()]
        embed.add_field(
            name=f"💸 Total Expenditure (RM {total_spent:.2f})",
            value="\n".join(breakdown_lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="💸 Expenditure",
            value="RM 0.00 recorded for this period.",
            inline=False,
        )

    # 2. Accomplished Tasks
    completed = snapshot.get("completed_tasks", [])
    if completed:
        lines = [f"• ~~`[#{t['id']}]` {t['description']}~~" for t in completed]
        embed.add_field(
            name=f"✅ Completed Items ({len(completed)})",
            value="\n".join(lines[:15]),
            inline=False,
        )

    # 3. Pending Open Tasks
    open_tasks = snapshot.get("open_tasks", [])
    if open_tasks:
        lines = []
        for t in open_tasks:
            due_str = f" _(Due: {t['due_date']})_" if t.get("due_date") else ""
            prefix = "   └── ⏳ " if t.get("parent_id") else "• "
            phase_info = f"`{t['phase_name']}`: " if t.get("phase_name") else ""
            lines.append(f"{prefix}`[#{t['id']} | {t['priority']}]` {phase_info}{t['description']}{due_str}")
        embed.add_field(
            name=f"📋 Active Open Tasks ({len(open_tasks)})",
            value="\n".join(lines[:15]),
            inline=False,
        )
    else:
        embed.add_field(
            name="📋 Active Tasks",
            value="🎉 No open tasks! You are all caught up.",
            inline=False,
        )

    return embed


def format_query_results(
    query: QueryScope,
    expenses: Optional[List[Dict[str, Any]]] = None,
    total_spent: Optional[float] = None,
    category_breakdown: Optional[Dict[str, float]] = None,
    tasks: Optional[List[Dict[str, Any]]] = None,
    ai_answer: Optional[str] = None,
) -> discord.Embed:
    """Format on-demand query results."""
    embed = discord.Embed(
        title=f"🔍 Status Report ({query.query_target} - {query.timeframe})",
        color=discord.Color.blurple(),
    )

    if ai_answer:
        embed.description = ai_answer

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
            lines = []
            for t in tasks:
                prefix = "   └── ⏳ " if t.get("parent_id") else "• "
                lines.append(f"{prefix}`[#{t['id']} | {t['priority']}]` {t['description']}")
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


def format_help_guide() -> discord.Embed:
    """Build a comprehensive guide embed showing how to interact with the bot."""
    embed = discord.Embed(
        title="📖 Perlica Personal Agent — Quick Start Guide",
        description="Just send natural sentences directly in this DM! Here are some examples of what you can say:",
        color=discord.Color.teal(),
    )

    embed.add_field(
        name="💸 Logging Expenses",
        value=(
            "• `RM 15.50 chicken rice for lunch`\n"
            "• `Spent 45 on petrol and 12 on toll`\n"
            "• `Yesterday paid RM 80 for electricity bill`"
        ),
        inline=False,
    )

    embed.add_field(
        name="📝 Creating Single & Multi-Phase Tasks",
        value=(
            "• `Remind me to submit client invoice tomorrow 5pm`\n"
            "• `Create task 'App Redesign' with 3 phases: 1. Wireframes, 2. UI Design, 3. Testing`\n"
            "• `Prepare presentation slides next Monday`"
        ),
        inline=False,
    )

    embed.add_field(
        name="⚡ Compound Multi-Actions",
        value="• `Spent RM 25 on Grab and finished Phase 1 of App Redesign`\n*(Logs expense + marks sub-phase as DONE in 1 text!)*",
        inline=False,
    )

    embed.add_field(
        name="📊 Immediate Summaries & Insights",
        value=(
            "• `Summarize today` or `Recap my day`\n"
            "• `How much did I spend this week?`\n"
            "• `What are my open tasks?`\n"
            "• `Give me advice on my budget`"
        ),
        inline=False,
    )

    embed.add_field(
        name="🛡️ Zero-Assumption Policy",
        value="• If you say `Spent RM 50`, the bot will ask what it was for rather than guessing.",
        inline=False,
    )

    return embed
