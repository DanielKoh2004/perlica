import discord
from typing import List, Dict, Any, Optional
from src.extractor import ExtractedPayload, QueryScope


def render_progress_bar(current: float, limit: float, bar_length: int = 10) -> str:
    """Render a visual Unicode progress bar with color-coded alerts."""
    if limit <= 0:
        return f"RM {current:.2f} / Unlimited"
    ratio = min(max(current / limit, 0.0), 1.0)
    filled_length = int(bar_length * ratio)
    bar = "█" * filled_length + "░" * (bar_length - filled_length)
    pct = round((current / limit) * 100, 1)
    status_emoji = " ⚠️" if 80.0 <= pct <= 100.0 else (" 🚨 OVERSPENT" if pct > 100.0 else "")
    return f"`[{bar}]` **RM {current:.2f} / RM {limit:.2f}** ({pct}%){status_emoji}"


def render_sparkline(values: List[float]) -> str:
    """
    Render a fixed-width monospaced sparkline.
    Enclosed in markdown backticks to guarantee identical rendering across Desktop, iOS, and Android.
    """
    if not values or all(v == 0 for v in values):
        return "`[ - - - - - - - ]`"
    bars = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    min_v = min(values)
    max_v = max(values)
    span = max_v - min_v
    if span == 0:
        return "`[" + ("▅" * len(values)) + "]`"
    chars = []
    for v in values:
        idx = int(((v - min_v) / span) * (len(bars) - 1))
        idx = max(0, min(idx, len(bars) - 1))
        chars.append(bars[idx])
    return "`[" + "".join(chars) + "]`"


def format_action_preview(
    payload: ExtractedPayload,
    expenses: List[Dict[str, Any]],
    tasks: List[Dict[str, Any]],
    completed_task_ids: List[int],
) -> discord.Embed:
    """Build a rich preview embed for user review before committing to database."""
    embed = discord.Embed(
        title="📋 Action Ingestion Preview",
        description="Please review what will be recorded. Click **Confirm** to save, **Edit** to modify, or **Reject** to discard.",
        color=discord.Color.gold(),
    )

    # 1. Expenses Preview
    if expenses:
        total = sum(e["amount"] for e in expenses)
        lines = []
        for exp in expenses:
            note_str = f" ({exp['note']})" if exp.get("note") else ""
            lines.append(f"• **RM {exp['amount']:.2f}** — `{exp['category']}`{note_str}")
        embed.add_field(
            name=f"💸 Expenses to Log (Total: RM {total:.2f})",
            value="\n".join(lines),
            inline=False,
        )

    # 2. Tasks Preview (Single or Multi-phase)
    if tasks:
        lines = []
        for t in tasks:
            due_parts = []
            if t.get("due_date"):
                due_parts.append(t["due_date"])
            if t.get("due_time"):
                due_parts.append(t["due_time"])
            due_str = f" _(Due: {' '.join(due_parts)})_" if due_parts else ""

            phases = t.get("phases", [])
            if phases:
                lines.append(f"• 📁 `[{t['priority']}]` **{t['description']}**{due_str}")
                for idx, p in enumerate(phases, start=1):
                    lines.append(f"   └── ⏳ `Phase {idx}`: {p}")
            else:
                lines.append(f"• `[{t['priority']}]` {t['description']}{due_str}")

        embed.add_field(
            name="📝 Tasks to Create",
            value="\n".join(lines),
            inline=False,
        )

    # 3. Recurring Bills Preview
    if payload.add_bill_name and payload.add_bill_amount is not None:
        cat_name = payload.add_bill_category.value if payload.add_bill_category else "Investments & Savings"
        day_str = f"on the {payload.add_bill_day}th" if payload.add_bill_day else "monthly"
        embed.add_field(
            name="🔔 Recurring Bill to Add (Human Reminder Only)",
            value=f"• **{payload.add_bill_name}** — RM {payload.add_bill_amount:.2f} (`{cat_name}`) {day_str}",
            inline=False,
        )

    # 4. Budget Limit Preview
    if payload.set_budget_category and payload.set_budget_amount is not None:
        embed.add_field(
            name="🎯 Monthly Budget to Set",
            value=f"• **{payload.set_budget_category}:** RM {payload.set_budget_amount:.2f} monthly limit",
            inline=False,
        )

    # 5. Completed Tasks Preview
    if completed_task_ids:
        lines = [f"• Task ID `#{tid}` marked `DONE`" for tid in completed_task_ids]
        embed.add_field(
            name="✅ Tasks to Complete",
            value="\n".join(lines),
            inline=False,
        )

    # 6. Ambiguity note if any
    if payload.ambiguous_task_note:
        embed.add_field(
            name="⚠️ Clarification Required",
            value=payload.ambiguous_task_note,
            inline=False,
        )

    return embed


def format_action_confirmation(
    payload: ExtractedPayload,
    inserted_expenses: List[Dict[str, Any]],
    inserted_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
    budget_alerts: Optional[List[str]] = None,
    streak_info: Optional[Dict[str, Any]] = None,
) -> discord.Embed:
    """Build a rich confirmation embed for logged actions with hierarchy, budget alerts, and streak badge."""
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

    # 2. Budget Alerts
    if budget_alerts:
        embed.add_field(
            name="⚠️ Budget Alert",
            value="\n".join(budget_alerts),
            inline=False,
        )

    # 3. New Tasks (Parent & Phases)
    if inserted_tasks:
        lines = []
        for t in inserted_tasks:
            due_parts = []
            if t.get("due_date"):
                due_parts.append(t["due_date"])
            if t.get("due_time"):
                due_parts.append(t["due_time"])
            due_str = f" _(Due: {' '.join(due_parts)})_" if due_parts else ""

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

    # 4. Completed Tasks
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

    # 5. Streak Footer
    if streak_info and streak_info.get("streak_days", 0) > 0:
        embed.set_footer(
            text=f"🔥 {streak_info['streak_days']}-Day Logging Streak | {streak_info.get('completed_this_week', 0)} tasks crushed this week!"
        )

    # 6. Conversational Reply fallback
    if payload.conversational_reply and not inserted_expenses and not inserted_tasks and not completed_tasks and not payload.ambiguous_task_note:
        embed.description = payload.conversational_reply

    return embed


def format_morning_briefing(
    open_tasks: List[Dict[str, Any]],
    due_bills: List[Dict[str, Any]],
    budget_status: List[Dict[str, Any]],
    date_str: str,
    upcoming_bills: Optional[List[Dict[str, Any]]] = None,
) -> discord.Embed:
    """Build a sharp morning kickoff embed at 08:30."""
    embed = discord.Embed(
        title=f"☀️ Morning Briefing — {date_str}",
        description="Here is your focus and financial outlook for today. Have a productive day!",
        color=discord.Color.orange(),
    )

    # 1. High Priority & Open Tasks
    if open_tasks:
        lines = []
        high_prio = [t for t in open_tasks if t.get("priority") == "HIGH"]
        other_tasks = [t for t in open_tasks if t.get("priority") != "HIGH"]

        if high_prio:
            lines.append("**🔥 High Priority Focus:**")
            for t in high_prio:
                due = f" _(Due: {t['due_date']})_" if t.get("due_date") else ""
                lines.append(f"• `[#{t['id']}]` {t['description']}{due}")
            lines.append("")

        if other_tasks:
            lines.append("**📋 Other Active Tasks:**")
            for t in other_tasks[:8]:
                lines.append(f"• `[#{t['id']} | {t['priority']}]` {t['description']}")

        embed.add_field(
            name=f"🎯 Tasks to Tackle ({len(open_tasks)})",
            value="\n".join(lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="🎯 Tasks",
            value="🎉 No open tasks! You're completely clear.",
            inline=False,
        )

    # 2. Due Recurring Bills (Human-in-the-loop reminder)
    if due_bills:
        bill_lines = []
        for b in due_bills:
            bill_lines.append(f"• **{b['name']}:** RM {b['amount']:.2f} (`{b['category']}`)")
        bill_lines.append("\n💡 _Reply `Logged <Bill>` or `Spent RM <amount>` when paid._")
        embed.add_field(
            name=f"🔔 Recurring Bills Due Today ({len(due_bills)})",
            value="\n".join(bill_lines),
            inline=False,
        )

    # 3. 3-Day Upcoming Bill Warnings (Cash Flow Heads Up)
    if upcoming_bills:
        up_lines = []
        for b in upcoming_bills:
            up_lines.append(f"• **{b['name']}:** RM {b['amount']:.2f} _(in {b['due_in_days']} days — {b['due_date_str']})_")
        embed.add_field(
            name=f"⏳ Upcoming Bills (Next 3 Days)",
            value="\n".join(up_lines),
            inline=False,
        )

    # 4. Monthly Budget Health
    if budget_status:
        b_lines = []
        for b in budget_status[:5]:
            bar = render_progress_bar(b["spent"], b["limit"])
            b_lines.append(f"• **{b['category']}:**\n  {bar}")
        embed.add_field(
            name="📊 Monthly Budget Overview",
            value="\n".join(b_lines),
            inline=False,
        )

    return embed


def format_daily_summary(
    expenses: List[Dict[str, Any]],
    total_spent: float,
    open_tasks: List[Dict[str, Any]],
    date_str: str,
    spending_pace: Optional[Dict[str, Any]] = None,
    streak_info: Optional[Dict[str, Any]] = None,
) -> discord.Embed:
    """Build a rich embed for the automated daily summary at 22:00."""
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

    # 7-Day Pace & Monospaced Sparkline
    if spending_pace:
        sparkline = render_sparkline(spending_pace.get("daily_series", []))
        diff_pct = spending_pace.get("diff_pct", 0.0)
        sign = "+" if diff_pct > 0 else ""
        indicator = "🔴" if diff_pct > 15.0 else ("🟢" if diff_pct < -15.0 else "🟡")
        avg = spending_pace.get("seven_day_avg", 0.0)
        pace_text = f"**RM {total_spent:.2f}** today vs **RM {avg:.2f}** 7-day avg ({sign}{diff_pct}% {indicator})\n7-Day Trend: {sparkline}"
        embed.add_field(
            name="📈 Spending Pace & Trend",
            value=pace_text,
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

    # Streak Footer
    if streak_info and streak_info.get("streak_days", 0) > 0:
        embed.set_footer(
            text=f"🔥 {streak_info['streak_days']}-Day Logging Streak | {streak_info.get('completed_this_week', 0)} tasks finished this week!"
        )

    return embed


def format_weekly_executive_review(
    review_data: Dict[str, Any],
    ai_strategic_kickoff: Optional[str] = None,
) -> discord.Embed:
    """Build a rich Sunday 8:00 PM Weekly Executive Review embed."""
    embed = discord.Embed(
        title=f"🏆 Sunday Executive Review ({review_data['start_date']} to {review_data['end_date']})",
        color=discord.Color.dark_purple(),
    )

    if ai_strategic_kickoff:
        embed.description = f"🎯 **Weekly Strategic Kickoff:**\n{ai_strategic_kickoff}\n"

    # 1. Weekly Finances
    total_spent = review_data.get("total_spent", 0.0)
    cat_breakdown = review_data.get("category_breakdown", {})
    if cat_breakdown:
        sorted_cats = sorted(cat_breakdown.items(), key=lambda x: x[1], reverse=True)
        cat_lines = [f"• **{cat}:** RM {amt:.2f}" for cat, amt in sorted_cats[:5]]
        embed.add_field(
            name=f"💸 Total Spent This Week: RM {total_spent:.2f}",
            value="\n".join(cat_lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="💸 Total Spent This Week",
            value="RM 0.00 logged this week.",
            inline=False,
        )

    # 2. Tasks Performance Ratio
    completed_count = review_data.get("completed_tasks_count", 0)
    open_count = review_data.get("open_tasks_count", 0)
    total_tasks = completed_count + open_count
    ratio_pct = round((completed_count / total_tasks * 100), 1) if total_tasks > 0 else 100.0
    embed.add_field(
        name="📊 Task Execution Rate",
        value=f"• **{completed_count}** tasks completed this week ({ratio_pct}% completion rate)\n• **{open_count}** open tasks carrying into next week",
        inline=False,
    )

    # 3. Monthly Budget Health
    budget_status = review_data.get("budget_status", [])
    if budget_status:
        b_lines = []
        for b in budget_status[:4]:
            bar = render_progress_bar(b["spent"], b["limit"])
            b_lines.append(f"• **{b['category']}:** {bar}")
        embed.add_field(
            name="💳 Monthly Budget Runway",
            value="\n".join(b_lines),
            inline=False,
        )

    embed.set_footer(text="Have a powerful and focused week ahead!")
    return embed


def format_task_selector_embed(open_tasks: List[Dict[str, Any]]) -> discord.Embed:
    """Build task selection guide embed for native dropdown completion."""
    embed = discord.Embed(
        title="📋 Active Open Tasks",
        color=discord.Color.blurple(),
    )
    if not open_tasks:
        embed.description = "🎉 No open tasks! You're completely caught up."
        return embed

    total_count = len(open_tasks)
    showing_count = min(total_count, 25)
    note_extra = f"\n_(Showing top 25 of {total_count} open tasks in dropdown below)_" if total_count > 25 else ""
    embed.description = f"Select tasks from the dropdown menu below to mark them **DONE** in one tap!{note_extra}\n"

    lines = []
    for t in open_tasks[:25]:
        due_str = f" _(Due: {t['due_date']})_" if t.get("due_date") else ""
        lines.append(f"• `[#{t['id']} | {t['priority']}]` {t['description']}{due_str}")

    embed.add_field(
        name=f"Tasks List ({showing_count})",
        value="\n".join(lines),
        inline=False,
    )
    return embed


def format_full_snapshot_summary(
    snapshot: Dict[str, Any],
    timeframe_title: str,
    ai_digest: Optional[str] = None,
    budget_status: Optional[List[Dict[str, Any]]] = None,
) -> discord.Embed:
    """Build a comprehensive on-demand summary embed with financial, task, and budget stats."""
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

    # 2. Budget Utilization
    if budget_status:
        b_lines = []
        for b in budget_status:
            bar = render_progress_bar(b["spent"], b["limit"])
            b_lines.append(f"• **{b['category']}:**\n  {bar}")
        embed.add_field(
            name="📈 Budget Health",
            value="\n".join(b_lines),
            inline=False,
        )

    # 3. Accomplished Tasks
    completed = snapshot.get("completed_tasks", [])
    if completed:
        lines = [f"• ~~`[#{t['id']}]` {t['description']}~~" for t in completed]
        embed.add_field(
            name=f"✅ Completed Items ({len(completed)})",
            value="\n".join(lines[:15]),
            inline=False,
        )

    # 4. Pending Open Tasks
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


def format_budget_overview(budget_status: List[Dict[str, Any]]) -> discord.Embed:
    """Build a dedicated budget status embed."""
    embed = discord.Embed(
        title="📊 Monthly Budget Overview",
        color=discord.Color.teal(),
    )
    if not budget_status:
        embed.description = "No category budgets configured yet. Set one anytime by saying e.g. *'Set monthly food budget to RM 800'*!"
        return embed

    lines = []
    for b in budget_status:
        bar = render_progress_bar(b["spent"], b["limit"])
        rem_str = f"RM {b['remaining']:.2f} left" if b['remaining'] >= 0 else f"RM {abs(b['remaining']):.2f} OVER"
        lines.append(f"• **{b['category']}** ({rem_str}):\n  {bar}\n")

    embed.description = "\n".join(lines)
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
        description="Just send natural sentences, voice notes, or receipt photos directly in this DM!",
        color=discord.Color.teal(),
    )

    embed.add_field(
        name="💸 Logging Expenses & Receipts",
        value=(
            "• `RM 15.50 chicken rice for lunch`\n"
            "• `Reload TNG RM 50` / `99 Speedmart RM 28`\n"
            "• `recurring buy $100 s&p500 on 27th`\n"
            "• 🎙️ *Send a voice note while driving!*\n"
            "• 📸 *Send a photo of a receipt!*"
        ),
        inline=False,
    )

    embed.add_field(
        name="📊 Budget Limits & Progress Bars",
        value=(
            "• `Set monthly food budget to RM 800`\n"
            "• `Set monthly entertainment budget to RM 200`\n"
            "• `Check my budget status`"
        ),
        inline=False,
    )

    embed.add_field(
        name="🔔 Recurring Bills & Briefings",
        value=(
            "• `Add recurring bill: Unifi RM 139 on the 1st`\n"
            "• `edit the recurring buy to 400`\n"
            "• ☀️ *Morning Briefing at 8:30 AM (with 3-day heads-up)*\n"
            "• 🏆 *Sunday Executive Review at 8:00 PM*"
        ),
        inline=False,
    )

    embed.add_field(
        name="📝 Multi-Phase Tasks & Dropdowns",
        value=(
            "• `Create task 'App Launch' with 3 phases: 1. Wireframes, 2. Design, 3. Testing`\n"
            "• `What are my open tasks?` *(Native 1-tap select dropdown)*\n"
            "• `Done task #1`"
        ),
        inline=False,
    )

    embed.add_field(
        name="📄 CSV Export & Undo",
        value=(
            "• `Export this month's expenses` *(Instant .csv download)*\n"
            "• `undo` *(Reverse the last entry)*"
        ),
        inline=False,
    )

    return embed
